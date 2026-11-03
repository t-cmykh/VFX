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

    def __init__(self, half, size):
        self.half, self.size = float(half), int(size)
        self.scale = self.size / (2 * self.half)
        self.z = np.full(self.size * self.size, -np.inf, dtype=np.float32)
        self.rgb = np.full((self.size * self.size, 3), BACKGROUND, dtype=np.uint8)
        self.meshes = 0
        self._tris, self._tex, self._ntris = [], [], 0

    def add(self, v, faces, uv, jpeg):
        """v : sommets (n, 3) Y en haut (X = Est, -Z = Nord) ; uv : convention OpenGL (OBJ) ; jpeg : octets."""
        S = self.size
        P = np.stack([(v[:, 0] + self.half) * self.scale,      # colonne (Est)
                      (v[:, 2] + self.half) * self.scale,      # ligne depuis le Nord (-Z = Nord)
                      v[:, 1],                                 # hauteur
                      uv[:, 0], uv[:, 1]], axis=1).astype(np.float32)
        T = P[faces]                                           # (t, 3 sommets, 5)
        lo, hi = T[:, :, :2].min(axis=1), T[:, :, :2].max(axis=1)
        T = T[(hi[:, 0] >= 0) & (hi[:, 1] >= 0) & (lo[:, 0] < S) & (lo[:, 1] < S)]
        self.meshes += 1
        if not len(T):
            return
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


def planar_uv(v_enu, half):
    """UV (convention OpenGL / OBJ) de projection verticale : sommets (n, 3) Est, Nord, haut."""
    side = 2 * half
    return np.stack([(v_enu[:, 0] + half) / side, (v_enu[:, 1] + half) / side], axis=1)
