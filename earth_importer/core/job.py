"""Une operation complete : test du protocole -> parcours de l'octree -> telechargement -> OBJ -> ABC/USD."""
import glob
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

HERE = Path(__file__).resolve().parent.parent
BLENDER_SCRIPT = HERE / "export" / "blender_export.py"


@dataclass
class Params:
    lat: float
    lon: float
    radius: float = 200.0
    level: int = 18
    content: str = "geo_tex"   # "geo_tex" | "geo"
    fmt: str = "obj"           # "obj" | "abc" | "usd"
    out_dir: str = "."
    name: str = ""
    crop: bool = True

    def validate(self):
        if not -90 <= self.lat <= 90 or not -180 <= self.lon <= 180:
            raise ValueError("coordonnees hors limites")
        if not 1 <= self.radius <= 5000:
            raise ValueError("rayon entre 1 et 5000 m")
        if not 1 <= self.level <= 22:
            raise ValueError("niveau de detail entre 1 et 22")
        if self.content not in ("geo_tex", "geo") or self.fmt not in ("obj", "abc", "usd"):
            raise ValueError("contenu ou format inconnu")
        if not self.name:
            self.name = f"earth_{self.lat:.5f}_{self.lon:.5f}_r{self.radius:.0f}_l{self.level}"
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
        cache = Path(params.out_dir).expanduser() / ".earth_cache"
        self.fetcher = Fetcher(cfg, cache_dir=cache, cancel=self.cancel, log=self.log)
        self.frame = LocalFrame(params.lat, params.lon)
        self.with_tex = params.content == "geo_tex"

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
            self.log("INFO", f"Zone {self.p.lat:.6f}, {self.p.lon:.6f} - rayon {self.p.radius:.0f} m - niveau {self.p.level}")
            _, root = self.health_check()
            candidates = self.collect(root)
            excl = self.exclusions(candidates)
            todo = {p: m for p, m in candidates.items() if len(excl[p]) < 8}
            if not todo:
                raise RuntimeError("Aucune donnee 3D dans cette zone a ce niveau (essaie un niveau plus bas ou une autre zone).")
            levels = sorted({len(p) for p in todo})
            mpt = min(m.meters_per_texel for m in todo.values() if m.meters_per_texel) if todo else 0
            self.log("INFO", f"{len(todo)} tuiles a telecharger (niveaux {levels[0]}-{levels[-1]}, ~{mpt:.2f} m/texel au plus fin)")
            if self.estimate_only:
                self.emit("estimate", {"tiles": len(todo), "levels": levels})
                self.emit("state", {"state": "finished"})
                return
            self.out.mkdir(parents=True, exist_ok=True)
            obj_path = self.write_obj(todo, excl)
            final = obj_path
            if self.p.fmt != "obj":
                final = self.blender_export(obj_path)
            size = final.stat().st_size / 1e6
            self.log("OK", f"Export termine : {final} ({size:.1f} Mo, {time.time() - t0:.0f} s)")
            self.emit("state", {"state": "finished", "path": str(final)})
        except Cancelled:
            self.log("WARN", "Operation annulee (les tuiles deja telechargees restent en cache)")
            self.emit("state", {"state": "cancelled"})
        except Exception as e:  # tout doit finir dans le panneau de log
            self.log("ERROR", str(e) or e.__class__.__name__)
            self.emit("state", {"state": "error"})

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

    def write_mesh(self, obj, mtl, tex_dir, name, node, mesh, exclude, flip_v, counts):
        tris = mesh.triangles
        if exclude and len(tris):
            tris = tris[~np.isin(mesh.octants[tris[:, 0]], list(exclude))]
        if not len(tris):
            return
        enu = self.frame.to_enu(rt.mesh_to_ecef(node, mesh))
        if self.p.crop:
            outside = np.hypot(enu[:, 0], enu[:, 1]) > self.p.radius
            tris = tris[~outside[tris].all(axis=1)]
            if not len(tris):
                return
        used, remap = np.unique(tris, return_inverse=True)
        faces = remap.reshape(-1, 3) + 1
        v = enu[used]
        obj.write(f"o tile_{name}\n")
        np.savetxt(obj, np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1), fmt="v %.4f %.4f %.4f")
        has_uv = self.with_tex and mesh.uv is not None
        if has_uv:
            uv = mesh.uv[used].copy()
            if flip_v:
                uv[:, 1] = 1.0 - uv[:, 1]
            np.savetxt(obj, uv, fmt="vt %.6f %.6f")
            mat = f"m_{name}"
            mtl.write(f"newmtl {mat}\nKd 1 1 1\nKs 0 0 0\nd 1\nillum 1\n")
            if mesh.texture and mesh.texture_format == rt.TEX_JPG:
                (tex_dir / f"{name}.jpg").write_bytes(mesh.texture)
                mtl.write(f"map_Kd textures/{name}.jpg\n")
            elif mesh.texture:
                counts["crn"] += 1
            mtl.write("\n")
            obj.write(f"usemtl {mat}\n")
            f = np.repeat(faces, 2, axis=1) + np.tile([counts["v"], counts["vt"]], 3)
            np.savetxt(obj, f, fmt="f %d/%d %d/%d %d/%d")
            counts["vt"] += len(used)
        else:
            np.savetxt(obj, faces + counts["v"], fmt="f %d %d %d")
        counts["v"] += len(used)
        counts["tris"] += len(faces)

    # ------------------------------------------------------------ ABC / USD
    def blender_export(self, obj_path):
        blender = find_blender(self.cfg.get("blender_path", ""))
        if not blender:
            raise RuntimeError(f"Blender introuvable : l'OBJ est pret ({obj_path}). "
                               "Renseigne blender_path dans config.yaml pour l'export ABC/USD.")
        ext = {"abc": ".abc", "usd": ".usdc"}[self.p.fmt]
        target = self.out / f"{self.p.name}{ext}"
        self.progress("Export Blender", 0, 0)
        self.log("INFO", f"Export {self.p.fmt.upper()} via Blender ({blender})")
        cmd = [blender, "-b", "--factory-startup", "--python", str(BLENDER_SCRIPT), "--",
               str(obj_path), str(target), self.p.fmt, "1" if self.with_tex else "0"]
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
        if self.p.fmt == "usd" or not self.with_tex:
            obj_path.unlink(missing_ok=True)
            (obj_path.with_suffix(".mtl")).unlink(missing_ok=True)
        return target
