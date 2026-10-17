"""WGS84 : lat/lon -> ECEF, et ECEF -> repere local ENU (Est, Nord, Haut), en float64."""
import math

import numpy as np

WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3


def lla_to_ecef(lat, lon, alt=0.0):
    la, lo = math.radians(lat), math.radians(lon)
    n = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(la) ** 2)
    return np.array([
        (n + alt) * math.cos(la) * math.cos(lo),
        (n + alt) * math.cos(la) * math.sin(lo),
        (n * (1 - WGS84_E2) + alt) * math.sin(la),
    ])


def enu_rotation(lat, lon):
    """Matrice 3x3 dont les lignes sont les axes Est, Nord, Haut exprimes en ECEF."""
    la, lo = math.radians(lat), math.radians(lon)
    sl, cl, so, co = math.sin(la), math.cos(la), math.sin(lo), math.cos(lo)
    return np.array([
        [-so, co, 0.0],
        [-sl * co, -sl * so, cl],
        [cl * co, cl * so, sl],
    ])


class LocalFrame:
    """Origine locale : tout point ECEF est ramene en metres autour de (lat, lon)."""

    def __init__(self, lat, lon):
        self.origin = lla_to_ecef(lat, lon)
        self.rot = enu_rotation(lat, lon)

    def to_enu(self, ecef):
        return (np.asarray(ecef, dtype=np.float64) - self.origin) @ self.rot.T

    def horizontal_distance(self, ecef):
        e = self.to_enu(ecef)
        return math.hypot(e[0], e[1])
