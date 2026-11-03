"""Une operation complete : test du protocole -> parcours de l'octree -> telechargement -> OBJ -> ABC/USD."""
import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import rocktree as rt
from .fetch import Cancelled, Fetcher, FetchError
from .geo import LocalFrame
from . import align
from .bake import TiledTopView, split, tex_name
from .lidar import Lidar

HERE = Path(__file__).resolve().parent.parent
BLENDER_SCRIPT = HERE / "export" / "blender_export.py"


@dataclass
class Params:
    lat: float
    lon: float
    radius: float = 200.0
    level: int = 22            # toujours la meilleure definition (le parcours s'arrete avec les donnees)
    content: str = "geo_tex"   # "geo_tex" | "geo" | "none" (pas de 3D Google)
    fmt: str = "obj"           # "obj" | "abc" | "usd"
    out_dir: str = "."
    name: str = ""
    crop: bool = True
    lidar: str = "none"        # LiDAR HD IGN maille : "none" | "mns" | "mnt" | "both"
    lidar_res: float = 0.5     # pas de la grille LiDAR (m) : resolution native
    laz: bool = False          # dalles brutes du nuage de points LiDAR HD
    align: bool = True         # cale le LiDAR sur la 3D Google (decalage Est/Nord/haut)
    lidar_tex: bool = True     # texture du LiDAR = vue de dessus de la 3D Google, resolution d'origine

    def validate(self):
        if not -90 <= self.lat <= 90 or not -180 <= self.lon <= 180:
            raise ValueError("coordonnees hors limites")
        if not 1 <= self.radius <= 5000:
            raise ValueError("rayon entre 1 et 5000 m")
        if not 1 <= self.level <= 22:
            raise ValueError("niveau de detail entre 1 et 22")
        if self.content not in ("geo_tex", "geo", "none") or self.fmt not in ("obj", "abc", "usd"):
            raise ValueError("contenu ou format inconnu")
        if self.lidar not in ("none", "mns", "mnt", "both") or self.lidar_res not in (0.5, 1, 2, 5):
            raise ValueError("option LiDAR inconnue")
        if self.content == "none" and self.lidar == "none" and not self.laz:
            raise ValueError("rien a telecharger : choisis la 3D Google et/ou le LiDAR")
        if not self.name:
            self.name = f"earth_{self.lat:.5f}_{self.lon:.5f}_r{self.radius:.0f}"
        self.name = re.sub(r"[^\w.\-]+", "_", self.name)


def find_blender(configured=""):
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("blender")
    if found:
        return found
    patterns = [
        r"C:\Program Files\Blender Foundation\Blender*\blender.exe",
        "/Applications/Blender.app/Contents/MacOS/Blender",
        "/snap/bin/blender",
        "/usr/local/bin/blender",
        os.path.expanduser("~/blender*/blender"),
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


class Job:
    def __init__(self, cfg, params, emit, estimate_only=False):
        self.cfg = cfg
        self.p = params
        self.emit = emit
        self.estimate_only = estimate_only
        self.cancel = threading.Event()
        self.out = Path(params.out_dir).expanduser() / params.name
        self.cache_dir = Path(params.out_dir).expanduser() / ".earth_cache"
        self.fetcher = Fetcher(cfg, cache_dir=self.cache_dir, cancel=self.cancel, log=self.log)
        self.frame = LocalFrame(params.lat, params.lon)  # rayon reel lu au test de sante
        self.with_tex = params.content == "geo_tex"
        self.preview = None         # apercu 3D empaquete (bouton Tester)
        self.preview_tiles = []
        self.ref_pts = []           # points de la 3D Google (Est, Nord, haut) pour caler le LiDAR
        self.baker = None           # vue de dessus de la 3D Google, pour texturer le LiDAR

    # ------------------------------------------------------------ evenements
    def log(self, level, msg):
        self.emit("log", {"level": level, "msg": msg})

    def progress(self, phase, done, total):
        self.emit("progress", {"phase": phase, "done": done, "total": total})

    # ------------------------------------------------------------ requetes
    def get_planetoid(self):
        return rt.parse_planetoid(self.fetcher.get(self.cfg["planetoid"]))

    def get_bulk(self, path, epoch):
        return rt.parse_bulk(self.fetcher.get(self.cfg["bulk"].format(path=path, epoch=epoch)), path)

    def node_resource(self, path, meta):
        fmt = rt.choose_texture_format(meta.texture_formats, tuple(self.cfg.get("texture_formats", [1, 6])))
        imagery = ""
        if meta.imagery_epoch is not None:
            imagery = self.cfg["node_imagery"].format(imagery_epoch=meta.imagery_epoch)
        return self.cfg["node"].format(path=path, epoch=meta.epoch, texture_format=fmt, imagery=imagery)

    # ------------------------------------------------------------ etapes
    def health_check(self):
        """Verifie que le protocole n'a pas change avant de lancer quoi que ce soit."""
        try:
            planetoid = self.get_planetoid()
            root = self.get_bulk("", planetoid["root_epoch"])
        except FetchError as e:
            raise RuntimeError(f"Serveur injoignable ou acces refuse ({e}). "
                               "Verifie ta connexion ; si elle est bonne, l'URL a peut-etre change (config.yaml).")
        except (ValueError, IndexError, KeyError) as e:
            raise RuntimeError(f"Protocole Google modifie : reponse illisible ({e}). Decodeur a mettre a jour.")
        if not root.nodes or not any(n.obb is not None for n in root.nodes.values()):
            raise RuntimeError("Protocole Google modifie : metadonnees racine vides. Decodeur a mettre a jour.")
        self.log("INFO", f"Protocole OK (epoch racine {planetoid['root_epoch']})")
        return planetoid, root

    def in_area(self, obb):
        return self.frame.horizontal_distance(obb.center) <= self.p.radius + obb.radius

    def collect(self, root):
        """Parcourt l'octree et renvoie {chemin: NodeMeta} des noeuds a telecharger."""
        max_level = self.p.level
        candidates = {}
        pending = [root]
        n_bulks = 1
        workers = self.cfg.get("workers", 6)
        with ThreadPoolExecutor(workers) as pool:
            while pending:
                children = []
                for bulk in pending:
                    accepted = {""}
                    for rel in sorted(bulk.nodes, key=len):
                        meta = bulk.nodes[rel]
                        full = bulk.path + rel
                        if len(full) > max_level or rel[:-1] not in accepted:
                            continue
                        if meta.obb is None or not self.in_area(meta.obb):
                            continue
                        accepted.add(rel)
                        if meta.has_data:
                            candidates[full] = meta
                        if meta.has_bulk and len(full) < max_level:
                            children.append((full, meta.bulk_epoch))
                if self.cancel.is_set():
                    raise Cancelled()
                futures = [pool.submit(self.get_bulk, path, epoch) for path, epoch in children]
                pending = []
                for f in as_completed(futures):
                    try:
                        pending.append(f.result())
                    except FetchError as e:
                        self.log("WARN", f"Metadonnees ignorees : {e}")
                n_bulks += len(children)
                self.progress("Analyse de l'octree", n_bulks, 0)
        self.log("INFO", f"{n_bulks} blocs de metadonnees lus")
        return candidates

    @staticmethod
    def exclusions(paths):
        """Octants d'un noeud deja couverts par un enfant plus detaille."""
        excl = {p: set() for p in paths}
        for p in paths:
            if len(p) > 1 and p[:-1] in excl:
                excl[p[:-1]].add(int(p[-1]))
        return excl

    def download_node(self, path, meta):
        return path, rt.parse_node(self.fetcher.get(self.node_resource(path, meta)), self.with_tex)

    # ------------------------------------------------------------ main
    def run(self):
        t0 = time.time()
        try:
            self.p.validate()
            self.log("INFO", f"Zone {self.p.lat:.6f}, {self.p.lon:.6f} - rayon {self.p.radius:.0f} m - definition maximale")
            outputs = []
            if self.p.content != "none":
                final = self.run_google()
                if final:
                    outputs.append(final)
            if self.p.lidar != "none" or self.p.laz:
                outputs += self.run_lidar()
            if self.estimate_only:
                if not self.preview_tiles:
                    raise RuntimeError("Rien a previsualiser.")
                self.progress("Aperçu 3D", 0, 0)
                tris = self.publish_preview(self.preview_tiles)
                what = f"{self.estimated} tuiles a telecharger" if self.p.content != "none" else "LiDAR"
                self.log("OK", f"Apercu : {tris:,} triangles affiches - {what}".replace(",", " "))
                self.emit("state", {"state": "finished", "preview": True})
                return
            if not outputs:
                raise RuntimeError("Rien n'a ete exporte.")
            for final in outputs:
                size = final.stat().st_size / 1e6
                self.log("OK", f"Export termine : {final} ({size:.1f} Mo)")
            self.log("OK", f"Termine en {time.time() - t0:.0f} s")
            self.emit("state", {"state": "finished", "path": str(outputs[0])})
        except Cancelled:
            self.log("WARN", "Operation annulee (ce qui a deja ete telecharge reste en cache)")
            self.emit("state", {"state": "cancelled"})
        except Exception as e:  # tout doit finir dans le panneau de log
            self.log("ERROR", str(e) or e.__class__.__name__)
            self.emit("state", {"state": "error"})

    def run_google(self):
        planetoid, root = self.health_check()
        self.frame = LocalFrame(self.p.lat, self.p.lon, planetoid["radius"])
        candidates = self.collect(root)
        if self.p.lidar != "none" and self.p.lidar_tex:
            if self.with_tex:
                self.baker = TiledTopView(self.p.radius)
            else:
                self.log("WARN", "Texture LiDAR : pas de texture Google (choisir Contenu = Geometrie + textures)")
        excl = self.exclusions(candidates)
        todo = {p: m for p, m in candidates.items() if len(excl[p]) < 8}
        if not todo:
            raise RuntimeError("Aucune donnee 3D dans cette zone a ce niveau (essaie un niveau plus bas ou une autre zone).")
        levels = sorted({len(p) for p in todo})
        mpt = min((m.meters_per_texel for m in todo.values() if m.meters_per_texel), default=0)
        self.log("INFO", f"{len(todo)} tuiles a telecharger (niveaux {levels[0]}-{levels[-1]}, ~{mpt:.2f} m/texel au plus fin)")
        self.tex_mpt = mpt
        if self.estimate_only:
            self.estimated = len(todo)
            self.emit("estimate", {"tiles": len(todo), "levels": levels})
            sub, sub_excl, level = self.preview_subset(candidates, todo, excl, self.cfg.get("preview_max_tiles", 400))
            if level:
                self.log("INFO", f"Apercu allege : {len(sub)} tuiles (niveau {level} au plus) ; "
                                 "le telechargement prendra la meilleure definition")
            self.preview_tiles += self.preview_google(sub, sub_excl)
            return None
        self.out.mkdir(parents=True, exist_ok=True)
        obj_path = self.write_obj(todo, excl)
        if self.p.fmt != "obj":
            return self.blender_export(obj_path, self.with_tex)
        return obj_path

    def keep_ref(self, v, faces, uv=None, tex=None):
        """Garde des points de la 3D Google (Y en haut -> Est, Nord, haut) pour le calage du LiDAR,
        et la rend dans la vue de dessus qui texture le LiDAR."""
        if self.baker is not None and uv is not None and tex:
            self.baker.add(v, faces, uv, tex)
        if self.p.align and self.p.lidar != "none":
            enu = np.stack([v[:, 0], -v[:, 2], v[:, 1]], axis=1)
            self.ref_pts.append(align.samples(enu, faces, min(self.p.radius, align.MAX_HALF)))

    def align_lidar(self, lid):
        """Cherche le decalage qui pose le LiDAR sur la 3D Google, et verifie le sens Nord/Sud des grilles IGN."""
        self.progress("Calage LiDAR / Google", 0, 0)
        ref = np.concatenate(self.ref_pts)
        half = self.p.radius

        def mns_points():
            res0 = lid.res
            lid.res = max(res0, 1.0)
            try:
                m = lid.mesh("mns")
            finally:
                lid.res = res0
            return None if m is None else align.samples(m[0], m[1], min(half, align.MAX_HALF))

        pts = mns_points()
        if pts is None or not len(ref):
            self.log("WARN", "Calage LiDAR impossible : pas de MNS ou pas de 3D Google au centre de la zone")
            return
        sol = align.solve(ref, pts, half)
        # le sens des lignes du BIL n'a pas pu etre verifie sur le vrai service : on essaie l'autre
        orig = lid.cfg.get("bil_south_first", True)
        lid.cfg["bil_south_first"] = not orig
        flipped = mns_points()
        sol_f = align.solve(ref, flipped, half) if flipped is not None else None
        if sol_f and sol_f["coarse"] > sol["coarse"] + 0.05:
            self.log("WARN", f"Grilles IGN inversees Nord/Sud (correlation {sol['coarse']:.2f} -> "
                             f"{sol_f['coarse']:.2f}) : corrige pour la suite")
            sol = sol_f
        else:
            lid.cfg["bil_south_first"] = orig
        if sol["score"] < align.MIN_SCORE:
            self.log("WARN", f"Calage LiDAR non fiable (correlation {sol['score']:.2f}) : "
                             "LiDAR laisse a sa position geographique")
            return
        lid.shift = (sol["dE"], sol["dN"], sol["dH"])
        self.log("INFO", f"LiDAR cale sur Google : Est {sol['dE']:+.1f} m, Nord {sol['dN']:+.1f} m, "
                         f"hauteur {sol['dH']:+.1f} m (correlation {sol['score']:.2f})")

    def bake_texture(self):
        """Vue de dessus Google en dalles (plan avec les JPEG) pour texturer le LiDAR, ou None."""
        if self.baker is None:
            return None
        t0 = time.time()
        plan = self.baker.plan(self.tex_mpt, preview=self.estimate_only)
        ok = self.baker.bake(plan, lambda d, t: self.progress("Texture LiDAR : vue de dessus Google", d, t))
        if not ok:
            self.log("WARN", "Texture LiDAR : aucune texture Google dans la zone")
            return None
        n, size = plan["n"], plan["size"]
        tiling = f"{n} x {n} dalles de {size} px" if n > 1 else f"{size} x {size} px"
        cm = 100 * (plan["cell"] + 2 * plan["pad"]) / size
        mo = sum(len(j) for row in plan["images"] for j in row) / 1e6
        self.log("INFO", f"Texture LiDAR : vue de dessus Google, {tiling} (~{cm:.1f} cm/px), {mo:.1f} Mo, "
                         f"{time.time() - t0:.0f} s")
        if self.estimate_only:
            self.log("INFO", "Apercu : texture LiDAR allegee ; le telechargement prendra la resolution d'origine de Google")
        elif plan["capped"]:
            self.log("WARN", f"Zone tres grande : texture LiDAR limitee a {n} x {n} dalles (resolution reduite)")
        return plan

    def run_lidar(self):
        """LiDAR HD IGN : MNS/MNT mailles dans le meme repere que Google, et/ou dalles LAZ brutes."""
        lid = Lidar(self.cfg, self.p, self.frame, self.cache_dir, self.cancel, self.log, self.progress)
        if self.p.align and self.ref_pts and lid.products():
            self.align_lidar(lid)
        plan = self.bake_texture() if lid.products() else None
        if self.estimate_only:
            lid.estimate()
            if lid.products():
                lid.check_size()
            for prod in lid.products():
                res = lid.mesh(prod)
                if not res:
                    continue
                v, faces = res
                if not plan:
                    self.preview_tiles.append({"name": f"lidar_{prod}", "faces": faces,
                                               "pos": np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1)})
                    continue
                for r, c, pv, pf, uv in split(v, faces, plan):
                    self.preview_tiles.append({"name": f"lidar_{prod}_r{r}c{c}", "faces": pf,
                                               "pos": np.stack([pv[:, 0], pv[:, 2], -pv[:, 1]], axis=1),
                                               "uv": uv, "texture": plan["images"][r][c]})
            return []
        outputs = []
        self.out.mkdir(parents=True, exist_ok=True)
        if lid.products():
            lid.check_size()
        for prod in lid.products():
            obj_path = lid.write_obj(self.out, self.p.name, prod, plan)
            if obj_path is None:
                continue
            outputs.append(self.blender_export(obj_path, bool(plan)) if self.p.fmt != "obj" else obj_path)
        if self.p.laz:
            laz = lid.download_laz(self.out)
            if laz:
                self.log("INFO", f"Dalles LAZ dans {laz[0].parent} (Lambert-93 : voir origine_repere_local.txt)")
                outputs += laz
        return outputs

    # ------------------------------------------------------------ OBJ
    def write_obj(self, todo, excl):
        tex_dir = self.out / "textures"
        if self.with_tex:
            tex_dir.mkdir(exist_ok=True)
        obj_path = self.out / f"{self.p.name}.obj"
        mtl_path = self.out / f"{self.p.name}.mtl"
        flip_v = self.cfg.get("flip_v", True)
        counts = {"v": 0, "vt": 0, "tris": 0, "errors": 0, "crn": 0}
        total = len(todo)
        done = 0
        self.progress("Telechargement", 0, total)
        with open(obj_path, "w") as obj, (open(mtl_path, "w") if self.with_tex else open(os.devnull, "w")) as mtl, \
                ThreadPoolExecutor(self.cfg.get("workers", 6)) as pool:
            obj.write("# Earth Importer - Y en haut, X = Est, -Z = Nord, metres\n")
            obj.write(f"# origine : {self.p.lat:.7f}, {self.p.lon:.7f}\n")
            if self.with_tex:
                obj.write(f"mtllib {mtl_path.name}\n")
            futures = [pool.submit(self.download_node, p, m) for p, m in sorted(todo.items())]
            for f in as_completed(futures):
                if self.cancel.is_set():
                    for x in futures:
                        x.cancel()
                    raise Cancelled()
                try:
                    path, node = f.result()
                except Cancelled:
                    raise
                except (FetchError, ValueError, IndexError) as e:
                    counts["errors"] += 1
                    self.log("WARN", f"Tuile ignoree : {e}")
                else:
                    for i, mesh in enumerate(node.meshes):
                        self.write_mesh(obj, mtl, tex_dir, f"{path}_{i}", node, mesh, excl[path], flip_v, counts)
                done += 1
                self.progress("Telechargement", done, total)
        if counts["errors"]:
            self.log("WARN", f"{counts['errors']} tuile(s) en echec sur {total}")
        if counts["crn"]:
            self.log("WARN", f"{counts['crn']} texture(s) CRN non decodees : maillages concernes sans texture")
        if counts["tris"] == 0:
            raise RuntimeError("Aucun triangle dans la zone apres decoupe.")
        self.log("INFO", f"{counts['tris']:,} triangles, {counts['v']:,} sommets, "
                         f"{self.fetcher.downloaded_bytes / 1e6:.1f} Mo telecharges".replace(",", " "))
        return obj_path

    def mesh_arrays(self, node, mesh, exclude, flip_v):
        """Sommets (Y en haut, X = Est, -Z = Nord), triangles 0-based, UV (OpenGL) et JPEG d'un maillage,
        ou None s'il ne reste rien apres exclusion des octants et decoupe."""
        tris = mesh.triangles
        if exclude and len(tris):
            tris = tris[~np.isin(mesh.octants[tris[:, 0]], list(exclude))]
        if not len(tris):
            return None
        enu = self.frame.to_enu(rt.mesh_to_ecef(node, mesh))
        if self.p.crop:
            outside = np.hypot(enu[:, 0], enu[:, 1]) > self.p.radius
            tris = tris[~outside[tris].all(axis=1)]
            if not len(tris):
                return None
        used, remap = np.unique(tris, return_inverse=True)
        faces = remap.reshape(-1, 3)
        v = enu[used]
        v = np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1)
        uv = tex = None
        if self.with_tex and mesh.uv is not None:
            uv = mesh.uv[used].astype(np.float64)
            if flip_v:
                uv[:, 1] = 1.0 - uv[:, 1]
            if mesh.texture and mesh.texture_format == rt.TEX_JPG:
                tex = mesh.texture
            elif mesh.texture:
                tex = False  # CRN : non decode
        return v, faces, uv, tex

    def write_mesh(self, obj, mtl, tex_dir, name, node, mesh, exclude, flip_v, counts):
        res = self.mesh_arrays(node, mesh, exclude, flip_v)
        if res is None:
            return
        v, faces, uv, tex = res
        self.keep_ref(v, faces, uv, tex)
        faces = faces + 1
        obj.write(f"o tile_{name}\n")
        np.savetxt(obj, v, fmt="v %.4f %.4f %.4f")
        if uv is not None:
            np.savetxt(obj, uv, fmt="vt %.6f %.6f")
            mat = f"m_{name}"
            mtl.write(f"newmtl {mat}\nKd 1 1 1\nKs 0 0 0\nd 1\nillum 1\n")
            if tex:
                (tex_dir / f"{name}.jpg").write_bytes(tex)
                mtl.write(f"map_Kd textures/{name}.jpg\n")
            elif tex is False:
                counts["crn"] += 1
            mtl.write("\n")
            obj.write(f"usemtl {mat}\n")
            f = np.repeat(faces, 2, axis=1) + np.tile([counts["v"], counts["vt"]], 3)
            np.savetxt(obj, f, fmt="f %d/%d %d/%d %d/%d")
            counts["vt"] += len(v)
        else:
            np.savetxt(obj, faces + counts["v"], fmt="f %d %d %d")
        counts["v"] += len(v)
        counts["tris"] += len(faces)

    # ------------------------------------------------------------ apercu 3D (bouton Tester)
    @staticmethod
    def preview_subset(candidates, todo, excl, max_tiles):
        """Tuiles d'un apercu limite a max_tiles : on retire les niveaux les plus fins tant qu'il y en a trop."""
        level = max(len(k) for k in todo)
        while len(todo) > max_tiles and level > 1:
            level -= 1
            sub = {k: m for k, m in candidates.items() if len(k) <= level}
            e = Job.exclusions(sub)
            t = {k: m for k, m in sub.items() if len(e[k]) < 8}
            if not t:
                break
            todo, excl = t, e
            if len(todo) <= max_tiles:
                return todo, excl, level
        return todo, excl, 0

    def preview_google(self, todo, excl):
        """Telecharge (cache disque) et prepare les tuiles de l'apercu."""
        tiles = []
        flip_v = self.cfg.get("flip_v", True)
        done = 0
        self.progress("Aperçu", 0, len(todo))
        with ThreadPoolExecutor(self.cfg.get("workers", 6)) as pool:
            futures = [pool.submit(self.download_node, p, m) for p, m in sorted(todo.items())]
            for f in as_completed(futures):
                if self.cancel.is_set():
                    for x in futures:
                        x.cancel()
                    raise Cancelled()
                try:
                    path, node = f.result()
                except Cancelled:
                    raise
                except (FetchError, ValueError, IndexError) as e:
                    self.log("WARN", f"Tuile ignoree : {e}")
                else:
                    for i, mesh in enumerate(node.meshes):
                        res = self.mesh_arrays(node, mesh, excl[path], flip_v)
                        if res:
                            v, faces, uv, tex = res
                            self.keep_ref(v, faces, uv, tex)
                            tiles.append({"name": f"tile_{path}_{i}", "pos": v, "faces": faces,
                                          "uv": uv, "texture": tex or None})
                done += 1
                self.progress("Aperçu", done, len(todo))
        return tiles

    def publish_preview(self, tiles):
        """Empaquette les tuiles pour l'interface : [u32 taille JSON][JSON][donnees alignees sur 4 octets]."""
        head, chunks = [], []
        for t in tiles:
            pos = np.ascontiguousarray(t["pos"], dtype="<f4")
            faces = np.ascontiguousarray(t["faces"], dtype="<u4")
            uv = None if t.get("uv") is None else np.ascontiguousarray(t["uv"], dtype="<f4")
            tex = t.get("texture") or b""
            head.append({"name": t["name"], "nv": len(pos), "nf": len(faces), "uv": uv is not None,
                         "tex": len(tex), "color": t.get("color")})
            chunks += [pos.tobytes(), faces.tobytes()] + ([uv.tobytes()] if uv is not None else [])
            chunks.append(tex + b"\0" * (-len(tex) % 4))
        meta = json.dumps({"tiles": head}).encode()
        meta += b" " * (-len(meta) % 4)
        self.preview = len(meta).to_bytes(4, "little") + meta + b"".join(chunks)
        tris = sum(h["nf"] for h in head)
        self.emit("preview", {"tiles": len(head), "triangles": tris, "bytes": len(self.preview)})
        return tris

    # ------------------------------------------------------------ ABC / USD
    def blender_export(self, obj_path, with_tex):
        blender = find_blender(self.cfg.get("blender_path", ""))
        if not blender:
            raise RuntimeError(f"Blender introuvable : l'OBJ est pret ({obj_path}). "
                               "Renseigne blender_path dans config.yaml pour l'export ABC/USD.")
        ext = {"abc": ".abc", "usd": ".usdc"}[self.p.fmt]
        target = obj_path.with_suffix(ext)
        self.progress("Export Blender", 0, 0)
        self.log("INFO", f"Export {self.p.fmt.upper()} via Blender ({blender})")
        cmd = [blender, "-b", "--factory-startup", "--python", str(BLENDER_SCRIPT), "--",
               str(obj_path), str(target), self.p.fmt, "1" if with_tex else "0"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace")
        tail = []
        for line in proc.stdout:
            line = line.rstrip()
            tail = (tail + [line])[-15:]
            if line.startswith("[earth] "):
                self.log("INFO", line[8:])
            if self.cancel.is_set():
                proc.kill()
                raise Cancelled()
        if proc.wait() != 0 or not target.exists():
            raise RuntimeError("Echec de l'export Blender :\n" + "\n".join(tail))
        if self.p.fmt == "usd" or not with_tex:
            obj_path.unlink(missing_ok=True)
            (obj_path.with_suffix(".mtl")).unlink(missing_ok=True)
        return target
