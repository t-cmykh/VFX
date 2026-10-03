"""Conversions colorimetriques sRGB <-> ACEScg, calculees a partir des primaires."""
import numpy as np

# primaires CIE xy
REC709 = dict(r=(0.640, 0.330), g=(0.300, 0.600), b=(0.150, 0.060), w=(0.3127, 0.3290))   # D65
AP1 = dict(r=(0.713, 0.293), g=(0.165, 0.830), b=(0.128, 0.044), w=(0.32168, 0.33767))     # D60

_BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                      [-0.7502, 1.7135, 0.0367],
                      [0.0389, -0.0685, 1.0296]])


def _xy_to_xyz(xy):
    x, y = xy
    return np.array([x / y, 1.0, (1 - x - y) / y])


def rgb_to_xyz_matrix(p):
    prim = np.array([_xy_to_xyz(p[c]) for c in "rgb"]).T          # colonnes = primaires
    scale = np.linalg.solve(prim, _xy_to_xyz(p["w"]))
    return prim * scale


def adaptation(src_white, dst_white):
    """Bradford : XYZ(src_white) -> XYZ(dst_white)."""
    s = _BRADFORD @ _xy_to_xyz(src_white)
    d = _BRADFORD @ _xy_to_xyz(dst_white)
    return np.linalg.inv(_BRADFORD) @ np.diag(d / s) @ _BRADFORD


def conversion_matrix(src, dst):
    m = (np.linalg.inv(rgb_to_xyz_matrix(dst))
         @ adaptation(src["w"], dst["w"]) @ rgb_to_xyz_matrix(src))
    return m


SRGB_TO_ACESCG = conversion_matrix(REC709, AP1)
ACESCG_TO_SRGB = np.linalg.inv(SRGB_TO_ACESCG)


def srgb_decode(v):
    """Courbe sRGB (EOTF inverse) -> lineaire."""
    v = np.asarray(v, np.float32)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4).astype(np.float32)


def srgb_to_acescg(lin):
    """lin : HxWx3 lineaire Rec.709 (valeurs > 1 permises) -> lineaire ACEScg."""
    return (lin @ SRGB_TO_ACESCG.T.astype(np.float32)).astype(np.float32)


def acescg_to_srgb(lin):
    return (lin @ ACESCG_TO_SRGB.T.astype(np.float32)).astype(np.float32)
