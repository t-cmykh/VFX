"""LiDAR HD de l'IGN (France) : MNS / MNT en maillage, et dalles brutes du nuage de points (LAZ).

- MNS (surface : sol + batiments + vegetation) et MNT (sol nu) : grilles d'altitude lues sur le WMS
  raster de la Geoplateforme (data.geopf.fr/wms-r) en float32 (image/x-bil;bits=32), en Lambert-93,
  puis maillees en grille reguliere et placees dans le MEME repere local que la 3D Google
  (sphere de Google, origine au centre de la zone, Y en haut dans l'OBJ).
- Nuage de points : les dalles LAZ/COPC de 1 km x 1 km qui touchent la zone (index WFS), telechargees
  telles quelles (500 Mo a 2 Go chacune), en Lambert-93 / altitudes IGN69. Un fichier texte donne
  l'origine du repere local en Lambert-93 pour les recaler.

Tout ce qui depend des services IGN (URLs, noms de couches, sens des lignes du BIL) est dans
config.yaml, section "lidar".
"""
import json
import math
import re
import time
from pathlib import Path

import numpy as np
import requests

from .bake import planar_uv
from .fetch import Cancelled, Fetcher, FetchError

# ---------------------------------------------------------------- Lambert-93 (RGF93 / GRS80, EPSG:2154)
# Constantes de la projection conique conforme (IGN, notice NTG_71 / ALG0019).
L93_E = 0.0818191910428158        # excentricite GRS80
L93_N = 0.7256077650532670
L93_C = 11754255.426096
L93_XS = 700000.0
L93_YS = 12655612.049876
L93_LON0 = math.radians(3.0)


def lambert93_to_lonlat(x, y):
    """Lambert-93 (m) -> longitude, latitude (degres, RGF93 ~ WGS84). Accepte des tableaux."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    dx, dy = x - L93_XS, L93_YS - y
    r = np.hypot(dx, dy)
    gamma = np.arctan2(dx, dy)
    lon = L93_LON0 + gamma / L93_N
    iso = -np.log(r / L93_C) / L93_N
    phi = 2 * np.arctan(np.exp(iso)) - math.pi / 2
    for _ in range(12):
        es = L93_E * np.sin(phi)
        phi = 2 * np.arctan(((1 + es) / (1 - es)) ** (L93_E / 2) * np.exp(iso)) - math.pi / 2
    return np.degrees(lon), np.degrees(phi)


def lonlat_to_lambert93(lon, lat):
    lon, lat = np.radians(np.asarray(lon, dtype=np.float64)), np.radians(np.asarray(lat, dtype=np.float64))
    es = L93_E * np.sin(lat)
    iso = np.log(np.tan(math.pi / 4 + lat / 2) * ((1 - es) / (1 + es)) ** (L93_E / 2))
    r = L93_C * np.exp(-L93_N * iso)
    t = L93_N * (lon - L93_LON0)
    return L93_XS + r * np.sin(t), L93_YS - r * np.cos(t)


# ---------------------------------------------------------------- maillage d'une grille
def grid_mesh(z, valid):
    """Triangles d'une grille (h, w) : 2 par cellule dont les 4 coins sont valides. Indices = j * w + i."""
    h, w = z.shape
    j, i = np.mgrid[0:h - 1, 0:w - 1]
    a = (j * w + i).ravel()
    b, c, d = a + 1, a + w, a + w + 1
    ok = (valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1] & valid[1:, 1:]).ravel()
    a, b, c, d = a[ok], b[ok], c[ok], d[ok]
    # lignes du nord vers le sud : (a, c, b) et (b, c, d) sont dans le sens trigo vus du dessus
    return np.concatenate([np.stack([a, c, b], 1), np.stack([b, c, d], 1)])


class Lidar:
    """Une passe LiDAR pour la zone d'un Job (cercle de rayon r ou carre de cote 2r)."""

    def __init__(self, cfg, params, frame, cache_dir, cancel, log, progress, square=False):
        self.cfg = cfg.get("lidar", {})
        self.full_cfg = cfg
        self.p = params
        self.frame = frame
        self.cancel = cancel
        self.log = log
        self.progress = progress
        self.square = square
        # meme Fetcher que Google (cache, nouvelles tentatives) mais avec des URLs completes
        self.fetcher = Fetcher(dict(cfg, base_url="", min_interval=self.cfg.get("min_interval", 0.2)),
                               cache_dir=cache_dir, cancel=cancel, log=log)
        self.half = params.radius
        self.res = float(params.lidar_res)
        self.shift = (0.0, 0.0, 0.0)   # calage sur la 3D Google (Est, Nord, haut), voir core/align.py

    # ------------------------------------------------------------ zone en Lambert-93
    def bbox(self, res):
        """Rectangle Lambert-93 (cale sur la grille de pas res) qui couvre la zone, avec une marge."""
        lat0, lon0 = self.p.lat, self.p.lon
        r = self.frame_radius()
        h = self.half * 1.02 + 2 * res
        dlat = math.degrees(h / r)
        dlon = math.degrees(h / (r * math.cos(math.radians(lat0))))
        lons = [lon0 - dlon, lon0 + dlon, lon0 - dlon, lon0 + dlon, lon0, lon0]
        lats = [lat0 - dlat, lat0 - dlat, lat0 + dlat, lat0 + dlat, lat0 - dlat, lat0 + dlat]
        x, y = lonlat_to_lambert93(lons, lats)
        x0, x1 = math.floor(x.min() / res) * res, math.ceil(x.max() / res) * res
        y0, y1 = math.floor(y.min() / res) * res, math.ceil(y.max() / res) * res
        return x0, y0, x1, y1

    def frame_radius(self):
        return float(np.linalg.norm(self.frame.origin))

    def grid_shape(self, res=None):
        res = res or self.res
        x0, y0, x1, y1 = self.bbox(res)
        return round((y1 - y0) / res), round((x1 - x0) / res)

    # ------------------------------------------------------------ WMS
    def wms_url(self, layer, x0, y0, x1, y1, w, h):
        return (f"{self.cfg['wms']}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS={layer}&STYLES="
                f"&CRS=EPSG:2154&BBOX={x0:.3f},{y0:.3f},{x1:.3f},{y1:.3f}&WIDTH={w}&HEIGHT={h}"
                f"&FORMAT=image/x-bil%3Bbits%3D32")

    def fetch_grid(self, layer, res, label):
        """Grille d'altitude (lignes du nord vers le sud), NaN hors donnees, par blocs de max_px."""
        x0, y0, x1, y1 = self.bbox(res)
        H, W = round((y1 - y0) / res), round((x1 - x0) / res)
        step = int(self.cfg.get("max_px", 2048))
        grid = np.full((H, W), np.nan, dtype=np.float32)
        blocks = [(r, c) for r in range(0, H, step) for c in range(0, W, step)]
        south_first = self.cfg.get("bil_south_first", True)
        lo, hi = self.cfg.get("valid_range", [-500, 5000])
        for k, (r, c) in enumerate(blocks):
            if self.cancel.is_set():
                raise Cancelled()
            h, w = min(step, H - r), min(step, W - c)
            bx0, by1 = x0 + c * res, y1 - r * res
            url = self.wms_url(layer, bx0, by1 - h * res, bx0 + w * res, by1, w, h)
            data = self.fetcher.get(url)
            if len(data) != w * h * 4:
                text = data[:300].decode("utf-8", "replace")
                raise RuntimeError(f"Reponse IGN inattendue pour {layer} ({len(data)} octets au lieu de "
                                   f"{w * h * 4}) : {re.sub(r'<[^>]+>', ' ', text).strip()[:200]}")
            block = np.frombuffer(data, dtype="<f4").reshape(h, w)
            if south_first:
                block = block[::-1]
            block = np.where((block >= lo) & (block <= hi), block, np.nan)
            grid[r:r + h, c:c + w] = block
            self.progress(f"LiDAR {label}", k + 1, len(blocks))
        return grid, (x0, y1)

    # ------------------------------------------------------------ produits
    def products(self):
        return {"mns": ["mns"], "mnt": ["mnt"], "both": ["mns", "mnt"]}.get(self.p.lidar, [])

    def check_size(self):
        h, w = self.grid_shape()
        limit = int(self.cfg.get("max_vertices", 25_000_000))
        if h * w > limit:
            raise RuntimeError(f"Zone trop grande pour le LiDAR a {self.res:g} m ({w} x {h} points) : "
                               f"reduis la zone")
        return h, w

    def estimate(self):
        lines = []
        if self.products():
            h, w = self.check_size()
            for prod in self.products():
                tris = f"{2 * h * w:,}".replace(",", " ")
                lines.append(f"LiDAR {prod.upper()} : grille {w} x {h} au pas de {self.res:g} m, ~{tris} triangles")
        if self.p.laz:
            tiles = self.laz_tiles()
            sizes = [self.remote_size(u) for _, u in tiles]
            total = sum(s for s in sizes if s)
            known = "" if all(sizes) else " (taille inconnue pour certaines)"
            lines.append(f"LiDAR nuage de points : {len(tiles)} dalle(s) LAZ, {total / 1e9:.1f} Go{known}")
        for line in lines:
            self.log("INFO", line)
        return lines

    def mesh(self, prod):
        """Maillage (sommets ENU float64, triangles) d'un produit, ou None s'il n'y a pas de donnees."""
        layer = self.cfg["layers"][prod]
        grid, (x0, y1) = self.fetch_grid(layer, self.res, prod.upper())
        res = self.res
        if not np.isfinite(grid).any() and prod == "mnt" and self.cfg.get("mnt_fallback"):
            fres = max(res, 1.0)
            self.log("WARN", f"Pas de MNT LiDAR HD ici : repli sur le RGE ALTI ({fres:g} m)")
            grid, (x0, y1) = self.fetch_grid(self.cfg["mnt_fallback"], fres, "RGE ALTI")
            res = fres
        if not np.isfinite(grid).any():
            self.log("WARN", f"Aucune donnee {prod.upper()} LiDAR HD sur cette zone "
                             "(hors France ou zone pas encore couverte)")
            return None
        h, w = grid.shape
        xs = x0 + (np.arange(w) + 0.5) * res
        ys = y1 - (np.arange(h) + 0.5) * res
        X, Y = np.meshgrid(xs, ys)
        lon, lat = lambert93_to_lonlat(X.ravel(), Y.ravel())
        alt = np.nan_to_num(grid.ravel().astype(np.float64)) + float(self.cfg.get("z_offset", 0.0))
        enu = self.frame.to_enu(sphere_ecef(lat, lon, alt, self.frame_radius())) + np.array(self.shift)
        valid = np.isfinite(grid)
        tris = grid_mesh(grid, valid)
        # decoupe comme pour Google : on retire les triangles entierement hors de la zone
        if self.square:
            out = np.maximum(np.abs(enu[:, 0]), np.abs(enu[:, 1])) > self.half
        else:
            out = np.hypot(enu[:, 0], enu[:, 1]) > self.half
        if self.p.crop:
            tris = tris[~out[tris].all(axis=1)]
        if not len(tris):
            return None
        used, remap = np.unique(tris, return_inverse=True)
        holes = (~valid).mean()
        if holes > 0.01:
            self.log("INFO", f"{prod.upper()} : {100 * holes:.0f} % de la grille sans donnee (trous)")
        return enu[used], remap.reshape(-1, 3)

    def write_obj(self, out_dir, name, prod, texture=None):
        """OBJ du produit ; texture = JPEG de la vue de dessus Google (core/bake.py), projetee verticalement."""
        res = self.mesh(prod)
        if res is None:
            return None
        v, faces = res
        path = Path(out_dir) / f"{name}_lidar_{prod}.obj"
        with open(path, "w") as f:
            f.write(f"# Earth Importer - LiDAR HD IGN {prod.upper()} - Y en haut, X = Est, -Z = Nord, metres\n")
            f.write(f"# origine : {self.p.lat:.7f}, {self.p.lon:.7f} (meme repere que la 3D Google)\n")
            if texture:
                mtl = path.with_suffix(".mtl")
                (Path(out_dir) / "textures").mkdir(exist_ok=True)
                (Path(out_dir) / "textures" / f"lidar_{prod}.jpg").write_bytes(texture)
                mtl.write_text(f"newmtl m_lidar_{prod}\nKd 1 1 1\nKs 0 0 0\nd 1\nillum 1\n"
                               f"map_Kd textures/lidar_{prod}.jpg\n")
                f.write(f"mtllib {mtl.name}\n")
            f.write(f"o lidar_{prod}\n")
            np.savetxt(f, np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1), fmt="v %.3f %.3f %.3f")
            if texture:
                np.savetxt(f, planar_uv(v, self.half), fmt="vt %.6f %.6f")
                f.write(f"usemtl m_lidar_{prod}\n")
                np.savetxt(f, np.repeat(faces + 1, 2, axis=1), fmt="f %d/%d %d/%d %d/%d")
            else:
                np.savetxt(f, faces + 1, fmt="f %d %d %d")
        self.log("INFO", f"LiDAR {prod.upper()} : {len(faces):,} triangles -> {path.name}".replace(",", " "))
        return path

    # ------------------------------------------------------------ nuage de points (dalles LAZ)
    def laz_tiles(self):
        """[(nom, url)] des dalles du nuage de points qui touchent la zone (index WFS de l'IGN)."""
        x0, y0, x1, y1 = self.bbox(1.0)
        url = (f"{self.cfg['wfs']}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
               f"&TYPENAMES={self.cfg['laz_typename']}&OUTPUTFORMAT=application/json&COUNT=1000"
               f"&SRSNAME=EPSG:2154&BBOX={x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f},urn:ogc:def:crs:EPSG::2154")
        try:
            data = json.loads(self.fetcher.get(url))
        except FetchError as e:
            raise RuntimeError(f"Index des dalles LiDAR HD injoignable ({e})")
        except ValueError:
            raise RuntimeError("Index des dalles LiDAR HD illisible : le service WFS de l'IGN a peut-etre change "
                               "(laz_typename dans config.yaml)")
        tiles = {}
        for feat in data.get("features", []):
            props = feat.get("properties") or {}
            link = next((v for v in props.values() if isinstance(v, str) and re.search(r"\.(laz|las)(\?|$)", v, re.I)),
                        None)
            if link:
                tiles[link.rsplit("/", 1)[-1].split("?")[0]] = link
        if not tiles:
            self.log("WARN", "Aucune dalle LiDAR HD sur cette zone (hors France ou pas encore diffusee)")
        return sorted(tiles.items())

    def remote_size(self, url):
        try:
            r = self.fetcher.session.head(url, timeout=20, allow_redirects=True)
            return int(r.headers.get("Content-Length", 0)) if r.ok else 0
        except requests.RequestException:
            return 0

    def download_laz(self, out_dir):
        tiles = self.laz_tiles()
        if not tiles:
            return []
        dest = Path(out_dir) / "lidar_laz"
        dest.mkdir(parents=True, exist_ok=True)
        self.write_origin(dest)
        self.log("INFO", f"{len(tiles)} dalle(s) LAZ a telecharger (chacune 0,5 a 2 Go)")
        paths = []
        for k, (fname, url) in enumerate(tiles):
            target = dest / fname
            size = self.remote_size(url)
            if target.exists() and size and target.stat().st_size == size:
                self.log("INFO", f"{fname} deja present")
                paths.append(target)
                continue
            self.stream(url, target, f"Dalle LAZ {k + 1}/{len(tiles)}", size)
            paths.append(target)
        return paths

    def stream(self, url, target, label, size):
        tmp = target.with_suffix(target.suffix + ".part")
        for attempt in range(1, 4):
            try:
                with self.fetcher.session.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    total = size or int(r.headers.get("Content-Length", 0))
                    done = 0
                    last = 0.0
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            if self.cancel.is_set():
                                raise Cancelled()
                            f.write(chunk)
                            done += len(chunk)
                            if time.monotonic() - last > 0.3:
                                last = time.monotonic()
                                self.progress(f"{label} (Mo)", done >> 20, total >> 20)
                tmp.replace(target)
                self.log("INFO", f"{target.name} : {done / 1e6:.0f} Mo")
                return
            except Cancelled:
                tmp.unlink(missing_ok=True)
                raise
            except requests.RequestException as e:
                if attempt == 3:
                    raise RuntimeError(f"Echec du telechargement de {target.name} : {e}")
                self.log("WARN", f"{target.name} : {e.__class__.__name__}, nouvel essai")
                time.sleep(2 * attempt)

    def grid_rotation_deg(self):
        """Angle (sens trigo vu du dessus) de l'axe +Y Lambert-93 dans le repere local Est/Nord."""
        x, y = lonlat_to_lambert93(self.p.lon, self.p.lat)
        lon, lat = lambert93_to_lonlat([x, x], [y, y + 100.0])
        e = self.frame.to_enu(sphere_ecef(lat, lon, np.zeros(2), self.frame_radius()))
        d = e[1] - e[0]
        return math.degrees(math.atan2(-d[0], d[1]))

    def write_origin(self, dest):
        x, y = lonlat_to_lambert93(self.p.lon, self.p.lat)
        rot = self.grid_rotation_deg()
        (dest / "origine_repere_local.txt").write_text(
            "Dalles LiDAR HD IGN : Lambert-93 (EPSG:2154), altitudes IGN69 (NGF), metres.\n"
            f"Origine du repere local de l'export (centre de la zone) : {self.p.lat:.7f}, {self.p.lon:.7f}\n"
            f"  en Lambert-93 : X = {float(x):.3f}  Y = {float(y):.3f}\n"
            "Pour recaler le nuage sur les maillages exportes (Z en haut, X = Est, Y = Nord) :\n"
            f"  1. translater de ({-float(x):.3f}, {-float(y):.3f}, 0)\n"
            f"  2. tourner de {rot:.4f} deg autour de Z (sens trigo vu du dessus, autour de l'origine)\n"
            "  (ecart residuel de quelques cm a 1 km : courbure et echelle de la projection)\n",
            encoding="utf-8")


def sphere_ecef(lat, lon, alt, radius):
    la, lo = np.radians(lat), np.radians(lon)
    r = radius + alt
    return np.stack([r * np.cos(la) * np.cos(lo), r * np.cos(la) * np.sin(lo), r * np.sin(la)], axis=1)
