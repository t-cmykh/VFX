"""Calage du LiDAR IGN sur la 3D Google (meme algorithme que web/src/align.js).

Les deux maillages sont ramenes a des grilles de hauteur (max par cellule) dans le repere local,
puis on cherche le decalage horizontal qui maximise leur correlation (NCC) en pyramide
(cellules de 4 m sur +/- 40 m, puis 1 m, puis 0,5 m). Le decalage vertical est la mediane des ecarts.
Points : (Est, Nord, haut).
"""
import numpy as np

PASSES = [(4.0, 40.0), (1.0, 4.0), (0.5, 1.0)]   # (cellule, portee) en metres
MAX_HALF = 150.0       # on ne compare que le centre de la zone (300 m x 300 m au plus)
MIN_SCORE = 0.3        # en dessous, calage non fiable : on ne deplace rien
MIN_OVERLAP = 400


def samples(v, faces, half, step=1.0):
    """Sommets + points a l'interieur des grands triangles (un tous les ~step m). v : (n, 3) Est, Nord, haut."""
    lim = half + 20
    keep = (np.abs(v[:, 0]) <= lim) & (np.abs(v[:, 1]) <= lim)
    out = [v[keep]]
    if len(faces):
        a, b, c = v[faces[:, 0]], v[faces[:, 1]], v[faces[:, 2]]
        edge = np.max([np.hypot(*(a - b)[:, :2].T), np.hypot(*(b - c)[:, :2].T), np.hypot(*(c - a)[:, :2].T)], axis=0)
        big = (edge > 1.5 * step) & (np.abs(a[:, 0]) <= lim + edge) & (np.abs(a[:, 1]) <= lim + edge)
        ks = np.minimum(np.ceil(edge / step), 40).astype(int)
        for k in np.unique(ks[big]):
            sel = big & (ks == k)
            w = np.array([(u / k, t / k) for u in range(k + 1) for t in range(k + 1 - u)
                          if u != k and t != k and u + t != 0])
            if not len(w):
                continue
            wa, wb = w[:, 0:1], w[:, 1:2]
            pts = wa[None] * a[sel][:, None] + wb[None] * b[sel][:, None] + (1 - wa - wb)[None] * c[sel][:, None]
            out.append(pts.reshape(-1, 3))
    return np.concatenate(out)


def grid(pts, half, cell, dE=0.0, dN=0.0):
    n = int(np.ceil(2 * half / cell))
    x = np.floor((pts[:, 0] + dE + half) / cell).astype(np.int64)
    y = np.floor((pts[:, 1] + dN + half) / cell).astype(np.int64)
    ok = (x >= 0) & (y >= 0) & (x < n) & (y < n)
    g = np.full(n * n, -np.inf)
    np.maximum.at(g, y[ok] * n + x[ok], pts[ok, 2])
    g[np.isinf(g)] = np.nan
    return g.reshape(n, n)


def search(A, B, r):
    """Meilleur decalage (cellules) de B pour ressembler a A, dans +/- r cellules : (score, sx, sy)."""
    n = A.shape[0]
    best = (-2.0, 0, 0)
    for sy in range(-r, r + 1):
        for sx in range(-r, r + 1):
            ya, yb = slice(max(0, sy), min(n, n + sy)), slice(max(0, -sy), min(n, n - sy))
            xa, xb = slice(max(0, sx), min(n, n + sx)), slice(max(0, -sx), min(n, n - sx))
            a, b = A[ya, xa], B[yb, xb]
            m = ~(np.isnan(a) | np.isnan(b))
            cnt = m.sum()
            if cnt < MIN_OVERLAP:
                continue
            a, b = a[m] - a[m].mean(), b[m] - b[m].mean()
            den = np.sqrt((a * a).sum() * (b * b).sum())
            if den <= 0:
                continue
            score = float((a * b).sum() / den)
            if score > best[0]:
                best = (score, sx, sy)
    return best


def solve(ref, lidar, half):
    """Decalage (dE, dN, dH) a ajouter au LiDAR pour le poser sur Google, score final et score grossier."""
    half = min(half, MAX_HALF)
    dE = dN = 0.0
    score = coarse = -2.0
    for k, (cell, rng) in enumerate(PASSES):
        A, B = grid(ref, half, cell), grid(lidar, half, cell, dE, dN)
        s, sx, sy = search(A, B, int(round(rng / cell)))
        if s <= -2:
            break
        dE += sx * cell
        dN += sy * cell
        score = s
        if k == 0:
            coarse = s
    cell = PASSES[-1][0]
    A, B = grid(ref, half, cell), grid(lidar, half, cell, dE, dN)
    d = (A - B)[~(np.isnan(A) | np.isnan(B))]
    dH = float(np.sort(d)[len(d) // 2]) if len(d) else 0.0
    return {"dE": dE, "dN": dN, "dH": dH, "score": score, "coarse": coarse}
