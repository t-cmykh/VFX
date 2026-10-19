"""Repere de Google Earth : une SPHERE (rayon donne par PlanetoidMetadata, 6 371 010 m), pas l'ellipsoide WGS84.

Preuve : la camera "nyc" du client de reference (earth-reverse-engineering, client/main.cpp),
(1329866, -4643494, 4154677), tombe sur Lower Manhattan a 169 m d'altitude lue sur cette sphere,
et sur le Bronx a 2,1 km d'altitude lue en WGS84. La latitude saisie (WGS84, celle des cartes)
est utilisee directement comme latitude sur la sphere.

ECEF -> repere local ENU (Est, Nord, Haut) autour du centre de la zone, en float64.
"""
import math

import numpy as np

GOOGLE_RADIUS = 6371010.0


def lla_to_ecef(lat, lon, alt=0.0, radius=GOOGLE_RADIUS):
    la, lo = math.radians(lat), math.radians(lon)
    r = radius + alt
    return np.array([r * math.cos(la) * math.cos(lo), r * math.cos(la) * math.sin(lo), r * math.sin(la)])


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
    """Origine locale : tout point ECEF est ramene en metres autour de (lat, lon), au niveau de la sphere."""

    def __init__(self, lat, lon, radius=GOOGLE_RADIUS):
        self.origin = lla_to_ecef(lat, lon, 0.0, radius or GOOGLE_RADIUS)
        self.rot = enu_rotation(lat, lon)

    def to_enu(self, ecef):
        return (np.asarray(ecef, dtype=np.float64) - self.origin) @ self.rot.T

    def horizontal_distance(self, ecef):
        e = self.to_enu(ecef)
        return math.hypot(e[0], e[1])
