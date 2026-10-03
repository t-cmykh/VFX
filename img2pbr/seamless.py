"""Rendre une image tileable (cross-fade par decalage d'un demi-carre)."""
import numpy as np


def _weights(n, band):
    """0 sur les bords, 1 au centre ; `band` = largeur de la transition (fraction de n, <= 0.5)."""
    u = (np.arange(n) + 0.5) / n
    e = np.minimum(u, 1 - u)
    t = np.clip(e / band, 0, 1)
    return (t * t * (3 - 2 * t)).astype(np.float32)


def make_seamless(img, band=0.35):
    """Passe en X puis en Y : out = w*I + (1-w)*roll(I, n/2).

    Les bords de roll(I) sont le milieu de I, donc continus par wrap ; la couture de
    roll(I) tombe au centre, ou w = 1 (on y garde I). Les deux passes sont separables.
    """
    band = float(np.clip(band, 0.05, 0.5))
    out = img.astype(np.float32)
    for axis in (1, 0):
        n = out.shape[axis]
        shape = [1] * out.ndim
        shape[axis] = n
        w = _weights(n, band).reshape(shape)
        out = w * out + (1 - w) * np.roll(out, n // 2, axis=axis)
    return out


def seam_ratio(img):
    """Saut moyen a travers la couture de wrap / 95e percentile des sauts entre lignes voisines.

    <= 1 : la couture n'est pas pire que les 5 % de lignes les plus contrastees de l'image
    (invisible) ; >> 1 : couture visible. Retourne le pire des deux axes.
    """
    img = img.astype(np.float32)
    r = []
    for axis in (1, 0):
        wrap = np.abs(np.take(img, 0, axis=axis) - np.take(img, -1, axis=axis)).mean()
        other = tuple(i for i in range(img.ndim) if i != axis)
        inner = np.abs(np.diff(img, axis=axis)).mean(axis=other)
        r.append(wrap / max(float(np.percentile(inner, 95)), 1e-6))
    return float(max(r))
