"""Texture du LiDAR IGN prise sur la 3D Google (equivalent de web/src/bake.js, sans carte graphique).

La 3D Google texturee est "rendue" vue de dessus dans une image carree couvrant la zone
(haut de l'image = Nord, droite = Est) : chaque triangle est echantillonne plus finement que le pixel
et, pour chaque pixel, on garde la couleur du point le plus haut (tampon de profondeur).
Les maillages LiDAR recoivent ensuite des UV de projection verticale (position Est/Nord du sommet).
"""
from io import BytesIO

import numpy as np
from PIL import Image

BACKGROUND = 128            # gris la ou Google n'a pas de donnees
DENSITY = 1.1               # echantillons par pixel le long d'une arete (trous bouches ensuite)
CHUNK = 4_000_000           # echantillons traites a la fois (memoire)
_LATTICES = {}


def lattice(k):
    """Poids barycentriques (m, 3) d'une grille triangulaire de pas 1/k (sommets compris)."""
    if k not in _LATTICES:
        i, j = np.triu_indices(k + 1)
        a, b = (k - j).astype(np.float32), (j - i).astype(np.float32)   # a + b <= k
        w = np.stack([a, b, k - a - b], axis=1) / k
        _LATTICES[k] = w.astype(np.float32)
    return _LATTICES[k]


class TopViewBaker:
    """Les maillages sont mis en attente puis rendus par paquets (beaucoup de triangles a la fois) :
    les textures du paquet sont mises bout a bout dans un seul tableau, indexe par maillage."""

    FLUSH_TRIS = 150_000

    def __init__(self, half, size, x0=None, y_top=None, width=None):
        """Image size x size couvrant le carre [x0, x0 + width] (Est) x [y_top - width, y_top] (Nord) ;
        par defaut toute la zone [-half, half]."""
        self.half, self.size = float(half), int(size)
        self.x0 = -self.half if x0 is None else float(x0)
        self.y_top = self.half if y_top is None else float(y_top)
        self.width = 2 * self.half if width is None else float(width)
        self.scale = self.size / self.width
        self.z = np.full(self.size * self.size, -np.inf, dtype=np.float32)
        self.rgb = np.full((self.size * self.size, 3), BACKGROUND, dtype=np.uint8)
        self.meshes = 0
        self._tris, self._tex, self._ntris = [], [], 0

    def add(self, v, faces, uv, jpeg):
        """v : sommets (n, 3) Y en haut (X = Est, -Z = Nord) ; uv : convention OpenGL (OBJ) ; jpeg : octets."""
        S = self.size
        P = np.stack([(v[:, 0] - self.x0) * self.scale,        # colonne (Est)
                      (v[:, 2] + self.y_top) * self.scale,     # ligne depuis le Nord (-Z = Nord)
                      v[:, 1],                                 # hauteur
                      uv[:, 0], uv[:, 1]], axis=1).astype(np.float32)
        T = P[faces]                                           # (t, 3 sommets, 5)
        lo, hi = T[:, :, :2].min(axis=1), T[:, :, :2].max(axis=1)
        T = T[(hi[:, 0] >= 0) & (hi[:, 1] >= 0) & (lo[:, 0] < S) & (lo[:, 1] < S)]
        if not len(T):
            return
        self.meshes += 1
        tex = np.asarray(Image.open(BytesIO(jpeg)).convert("RGB"))
        tid = np.full((len(T), 3, 1), len(self._tex), np.float32)
        self._tris.append(np.concatenate([T, tid], axis=2))  # 6e colonne : maillage (texture)
        self._tex.append(tex)
        self._ntris += len(T)
        if self._ntris >= self.FLUSH_TRIS:
            self.flush()

    def flush(self):
        if not self._tris:
            return
        T = np.concatenate(self._tris)
        sizes = np.array([t.shape[:2] for t in self._tex], dtype=np.int64)     # (h, w)
        off = np.concatenate([[0], np.cumsum(sizes[:, 0] * sizes[:, 1])[:-1]])
        flat = np.concatenate([t.reshape(-1, 3) for t in self._tex])
        self._tris, self._tex, self._ntris = [], [], 0
        e = np.max([np.hypot(*(T[:, a, :2] - T[:, b, :2]).T) for a, b in ((0, 1), (1, 2), (2, 0))], axis=0)
        ks = np.clip(np.ceil(e * DENSITY), 1, 4000).astype(np.int32)
        order = np.argsort(ks, kind="stable")
        T, ks = T[order], ks[order]
        bounds = np.flatnonzero(np.diff(ks)) + 1
        for grp in np.split(np.arange(len(ks)), bounds):
            k = int(ks[grp[0]])
            W = lattice(k)
            per = max(1, CHUNK // len(W))
            for s in range(0, len(grp), per):
                pts = np.matmul(W, T[grp[s:s + per]]).reshape(-1, 6)   # (t, m, 6) : BLAS
                self._splat(pts, flat, sizes, off)

    def _splat(self, pts, flat, sizes, off):
        S = self.size
        x, y = pts[:, 0], pts[:, 1]
        ok = (x >= 0) & (y >= 0) & (x < S) & (y < S)
        if not ok.any():
            return
        pts = pts[ok]
        pix = pts[:, 1].astype(np.int32) * np.int32(S) + pts[:, 0].astype(np.int32)
        # tampon de profondeur : on garde le plus haut ; les gagnants ecrivent leur couleur
        z = pts[:, 2]
        np.maximum.at(self.z, pix, z)
        win = z >= self.z[pix]
        if not win.any():
            return
        pts, pix = pts[win], pix[win]
        tid = np.rint(pts[:, 5]).astype(np.int32)
        th, tw = sizes[tid, 0], sizes[tid, 1]
        tx = np.clip((pts[:, 3] * tw).astype(np.int32), 0, tw - 1)
        ty = np.clip(((1 - pts[:, 4]) * th).astype(np.int32), 0, th - 1)
        self.rgb[pix] = flat[off[tid] + ty * tw + tx]

    def jpeg(self, quality=90):
        """Image finale (JPEG), petits trous bouches avec les voisins. None si rien n'a ete rendu."""
        self.flush()
        if not self.meshes or not np.isfinite(self.z).any():
            return None
        S = self.size
        filled = np.isfinite(self.z).reshape(S, S)
        rgb = self.rgb.reshape(S, S, 3)
        for _ in range(2):  # trous d'un ou deux pixels entre echantillons
            holes = ~filled
            acc = np.zeros((S, S, 3), np.int32)
            cnt = np.zeros((S, S), np.int32)
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                f = np.roll(filled, (dy, dx), axis=(0, 1))
                c = np.roll(rgb, (dy, dx), axis=(0, 1))
                acc += c * f[..., None]
                cnt += f
            fix = holes & (cnt >= 2)
            if not fix.any():
                break
            rgb[fix] = (acc[fix] // cnt[fix][:, None]).astype(np.uint8)
            filled = filled | fix
        buf = BytesIO()
        Image.fromarray(rgb).save(buf, "JPEG", quality=quality)
        return buf.getvalue()


TILE_MAX = 4096     # cote max d'une dalle de texture (comme la version web)
MAX_TILES = 8       # au plus 8 x 8 dalles
PAD = 2.0           # marge (m) autour de chaque dalle : les triangles a cheval restent dans leur texture


class TiledTopView:
    """Vue de dessus de la 3D Google a la resolution d'origine de ses textures, en dalles de TILE_MAX px.
    Les maillages sont gardes pendant le telechargement (sommets + JPEG), puis rendus dalle par dalle."""

    def __init__(self, half):
        self.half = float(half)
        self.meshes = []

    def add(self, v, faces, uv, jpeg):
        self.meshes.append((np.asarray(v, np.float32), np.asarray(faces), np.asarray(uv, np.float32), jpeg))

    def plan(self, mpt, preview=False):
        need = int(np.ceil(2 * self.half / (mpt if mpt and mpt > 0 else 0.1)))
        n = 1 if preview else max(1, -(-need // TILE_MAX))
        capped = n > MAX_TILES
        n = min(n, MAX_TILES)
        pad = PAD if n > 1 else 0.0
        cell = 2 * self.half / n
        size = min(TILE_MAX, int(np.ceil(need * (cell + 2 * pad) / (2 * self.half) / 4)) * 4)
        return {"n": n, "size": max(size, 256), "cell": cell, "pad": pad, "half": self.half,
                "need": need, "capped": capped}

    def bake(self, plan, progress=lambda done, total: None):
        """Remplit plan["images"][ligne][colonne] (JPEG ; ligne 0 = Nord, colonne 0 = Ouest). False si rien."""
        n, cell, pad, size = plan["n"], plan["cell"], plan["pad"], plan["size"]
        images, any_data = [], False
        for r in range(n):
            images.append([])
            for c in range(n):
                progress(r * n + c, n * n)
                b = TopViewBaker(self.half, size, x0=-self.half + c * cell - pad,
                                 y_top=self.half - r * cell + pad, width=cell + 2 * pad)
                for m in self.meshes:
                    b.add(*m)
                jpeg = b.jpeg()
                any_data |= jpeg is not None
                images[r].append(jpeg or blank_jpeg(size))
        plan["images"] = images
        return any_data


def blank_jpeg(size):
    buf = BytesIO()
    Image.new("RGB", (size, size), (BACKGROUND,) * 3).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def split(v_enu, faces, plan):
    """Decoupe un maillage LiDAR (sommets Est, Nord, haut) selon les dalles du plan (centre des triangles).
    Renvoie [(ligne, colonne, sommets, triangles, uv OpenGL)]."""
    n, cell, pad, half = plan["n"], plan["cell"], plan["pad"], plan["half"]
    w = cell + 2 * pad
    cen = v_enu[faces].mean(axis=1)
    col = np.clip(np.floor((cen[:, 0] + half) / cell), 0, n - 1).astype(int)
    row = np.clip(np.floor((half - cen[:, 1]) / cell), 0, n - 1).astype(int)
    out = []
    for r in range(n):
        for c in range(n):
            f = faces[(row == r) & (col == c)]
            if not len(f):
                continue
            used, remap = np.unique(f, return_inverse=True)
            v = v_enu[used]
            x0, y0 = -half + c * cell - pad, half - (r + 1) * cell - pad
            uv = np.stack([(v[:, 0] - x0) / w, (v[:, 1] - y0) / w], axis=1)
            out.append((r, c, v, remap.reshape(-1, 3), uv))
    return out


def tex_name(plan, r, c):
    return f"lidar_r{r}c{c}" if plan["n"] > 1 else "lidar_texture"
