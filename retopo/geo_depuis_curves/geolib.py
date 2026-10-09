import bpy, bmesh, math
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ZF = -17.0   # en dessous : plancher (z = -18)


class Sections:
    """Carte (y, w) de la coque : w = z sur le bordage et le bouchain,
    w = -18 - (x - x_debut_plancher) sur le plancher plat."""
    def __init__(self, bm):
        self.bvh = BVHTree.FromBMesh(bm)
        self.cache = {}

    def _xf(self, y):
        k = int(round(y / 5.0))
        if k in self.cache:
            return self.cache[k]
        val = None
        for dk in [0] + [d for i in range(1, 40) for d in (i, -i)]:
            yy = (k + dk) * 5.0
            x = -2300.0
            while x < -1450.0:
                h = self.bvh.ray_cast(Vector((x, yy, -500.0)), Vector((0, 0, 1)))
                if h[0] is not None and h[0].z < ZF:
                    val = h[0].x
                    break
                x += 6.0
            if val is not None:
                break
        self.cache[k] = val
        return val

    def s_of(self, p):
        if p.z >= ZF:
            return p.z, 0.0
        xf = self._xf(p.y)
        if xf is None:
            return p.z, 0.0
        return -18.0 - (p.x - xf), 0.0

    def p_of(self, y, w):
        if w >= ZF:
            h = self.bvh.ray_cast(Vector((-3500.0, y, w)), Vector((1, 0, 0)))
            if h[0] is not None:
                return h[0]
            return self.bvh.find_nearest(Vector((-2100.0, y, w)))[0]
        xf = self._xf(y)
        if xf is None:
            return self.bvh.find_nearest(Vector((-2100.0, y, w)))[0]
        x = xf + (-18.0 - w)
        h = self.bvh.ray_cast(Vector((x, y, -500.0)), Vector((0, 0, 1)))
        if h[0] is not None:
            return h[0]
        return self.bvh.find_nearest(Vector((x, y, -18.0)))[0]
