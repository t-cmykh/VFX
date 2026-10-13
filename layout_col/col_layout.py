"""
Layout 3D "col de montagne / épingle de route avec spectateurs" (bpy)
Basé sur la photo reference.jpg (virage en épingle, foule, camping-car).

Usage (Blender 3.6+ / 4.x / 5.x) :
    blender -b -P col_layout.py -- --render out.png --save col_layout.blend
    blender -P col_layout.py                      # ouvre le layout dans l'UI

Options (après le "--") :
    --render PATH       rend une image (EEVEE)
    --save PATH         sauvegarde un .blend
    --seed N            graine aléatoire (défaut 7)
    --crowd-scale F     multiplicateur du nombre de spectateurs (défaut 1.0)
    --reference PATH    photo de référence affichée en fond de caméra
    --frames N          durée du travelling/pan en images (défaut 120, 0 = fixe)
    --anim              avec --render : rend toute l'animation (PNG numérotés)
    --res W H           résolution (défaut 1320 866 = format de la photo)

Repère : 1 unité = 1 m. Route en épingle ouverte vers +X, sommet de la
courbe vers -X, caméra sur le talus (côté -Y) qui regarde vers le virage.
"""
import argparse
import math
import os
import random
import sys

import bpy  # noqa: F401  (doit être importé avant bmesh en mode module)
import bmesh
from mathutils import Matrix, Vector, noise

# ----------------------------------------------------------------- paramètres
R = 12.0            # rayon de l'axe de l'épingle
ROAD_W = 6.5        # largeur de chaussée
ROAD_Z = 0.04       # la route flotte juste au-dessus du terrain (anti z-fight)
ROAD_LEN = 110.0    # longueur des deux branches droites (vers +X)

PALETTE_SHIRTS = [
    (0.80, 0.08, 0.06), (0.08, 0.22, 0.70), (0.90, 0.90, 0.85),
    (0.95, 0.75, 0.10), (0.08, 0.08, 0.10), (0.90, 0.38, 0.52),
    (0.12, 0.52, 0.24), (0.92, 0.45, 0.08),
]
PALETTE_PANTS = [(0.10, 0.10, 0.14), (0.20, 0.30, 0.50), (0.45, 0.40, 0.32)]
PALETTE_SKIN = [(0.85, 0.62, 0.50), (0.70, 0.48, 0.36), (0.93, 0.74, 0.62)]
PALETTE_HATS = [None, (0.92, 0.92, 0.90), (0.78, 0.68, 0.50), (0.90, 0.50, 0.60)]


def smoothstep(a, b, x):
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return a + (b - a) * t


# ------------------------------------------------------------ géométrie route
def road_dist(x, y):
    """Distance à l'axe de la route (analytique : 2 droites + demi-cercle)."""
    if x >= 0:
        return abs(abs(y) - R)
    return abs(math.hypot(x, y) - R)


def nearest_center(x, y):
    if x >= 0:
        return x, (R if y >= 0 else -R)
    r = max(math.hypot(x, y), 1e-6)
    return R * x / r, R * y / r


def centerline(step=1.0):
    """Points de l'axe, de la branche basse (x=+L) vers la branche haute."""
    pts = []
    n = int(ROAD_LEN / step)
    for i in range(n + 1):
        pts.append(Vector((ROAD_LEN - i * step, -R, 0)))
    arc = int(math.pi * R / step)
    for i in range(1, arc):
        th = -math.pi / 2 - math.pi * i / arc
        pts.append(Vector((R * math.cos(th), R * math.sin(th), 0)))
    for i in range(n + 1):
        pts.append(Vector((i * step, R, 0)))
    return pts


# -------------------------------------------------------------------- terrain
def terrain_h(x, y):
    r = math.hypot(x, y)
    if x >= 0:
        dout = abs(y) - R
        k = 1.0 if y > 0 else -1.0
    else:
        dout = r - R
        k = y / math.hypot(y, 3.0)
    wnoise = smoothstep(-R, R, dout) * 0.7 + 0.3
    small = (noise.noise(Vector((x * 0.07, y * 0.07, 0))) * 0.45
             + noise.noise(Vector((x * 0.25, y * 0.25, 3))) * 0.12) * wnoise
    if dout < 0:                              # îlot herbeux entre les 2 branches
        h = small
    elif k < 0:                               # talus montant côté caméra
        h = 0.34 * dout + 0.0045 * dout * dout
        h = 30.0 * math.tanh(h / 30.0)
        h += small
    else:                                     # versant qui plonge dans la vallée
        slope = lerp(0.12, 0.6, smoothstep(-0.2, 0.8, k))
        h = -55.0 * math.tanh(slope * dout / 55.0)
        h += small
    # montagnes lointaines tout autour
    dist = math.hypot(x - 20, y + 10)
    n = noise.fractal(Vector((x * 0.006, y * 0.006, 0)), 1.0, 2.0, 5)
    m = max(0.0, min(1.0, 0.55 + 0.7 * n))
    ridge = 120.0 * smoothstep(90, 340, dist) * (0.3 + 0.7 * m)
    h += ridge
    # on aplatit pour la chaussée
    d = road_dist(x, y)
    w = smoothstep(ROAD_W / 2 + 0.5, ROAD_W / 2 + 6.0, d)
    return h * w


def terrain_color(x, y, h):
    dist = math.hypot(x - 20, y + 10)
    n = noise.noise(Vector((x * 0.09, y * 0.09, 7))) * 0.5 + 0.5
    grass = Vector((0.20 + 0.10 * n, 0.36 + 0.10 * n, 0.08 + 0.04 * n))
    forest = Vector((0.07, 0.17 + 0.05 * n, 0.07))
    rock = Vector((0.36 + 0.08 * n, 0.33 + 0.06 * n, 0.29))
    c = grass.lerp(forest, smoothstep(45, 200, dist))
    c = c.lerp(rock, smoothstep(40, 100, h) * 0.9)
    return (c.x, c.y, c.z, 1.0)


def build_terrain(coll):
    nx, ny = 240, 200
    cx, cy, lx, ly = 40.0, 30.0, 400.0, 260.0

    def warp(t):  # maillage dense au centre, large aux bords
        return 0.35 * t + 0.65 * t ** 3

    xs = [cx + lx * warp(-1 + 2 * i / nx) for i in range(nx + 1)]
    ys = [cy + ly * warp(-1 + 2 * j / ny) for j in range(ny + 1)]
    verts, cols = [], []
    for y in ys:
        for x in xs:
            h = terrain_h(x, y)
            verts.append((x, y, h))
            cols.append(terrain_color(x, y, h))
    faces = []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            faces.append((a, a + 1, a + nx + 2, a + nx + 1))
    me = bpy.data.meshes.new("Terrain")
    me.from_pydata(verts, [], faces)
    me.update()
    for p in me.polygons:
        p.use_smooth = True
    ca = me.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="POINT")
    for i, c in enumerate(cols):
        ca.data[i].color = c
    mat = bpy.data.materials.new("Terrain_Mat")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    vc = nt.nodes.new("ShaderNodeVertexColor")
    vc.layer_name = "Col"
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.95
    me.materials.append(mat)
    ob = bpy.data.objects.new("Terrain", me)
    coll.objects.link(ob)
    return ob


# ----------------------------------------------------------------- matériaux
_mats = {}


def mat(name, color, rough=0.8, metal=0.0):
    if name in _mats:
        return _mats[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color[:3], 1.0)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    m.diffuse_color = (*color[:3], 1.0)
    _mats[name] = m
    return m


# ----------------------------------------------------------------- bmesh utils
def tag(bm_verts, mi):
    for f in {f for v in bm_verts for f in v.link_faces}:
        f.material_index = mi


def add_cyl(bm, r1, r2, depth, loc, mi, rot=None, seg=8):
    m = Matrix.Translation(loc)
    if rot is not None:
        m = m @ rot.to_4x4()
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=r1,
                                radius2=r2, depth=depth, matrix=m)
    tag(res["verts"], mi)


def add_box(bm, size, loc, mi, rot=None):
    m = Matrix.Translation(loc)
    if rot is not None:
        m = m @ rot.to_4x4()
    m = m @ Matrix.Diagonal(Vector((size[0], size[1], size[2], 1.0)))
    res = bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    tag(res["verts"], mi)


def add_sphere(bm, r, loc, mi, scale=(1, 1, 1)):
    m = Matrix.Translation(loc) @ Matrix.Diagonal(Vector((*scale, 1.0)))
    res = bmesh.ops.create_uvsphere(bm, u_segments=8, v_segments=6, radius=r, matrix=m)
    tag(res["verts"], mi)


def add_torus(bm, major, minor, loc, mi, rot=None, n=20, m_=6):
    vs = []
    xf = Matrix.Translation(loc)
    if rot is not None:
        xf = xf @ rot.to_4x4()
    for i in range(n):
        a = 2 * math.pi * i / n
        for j in range(m_):
            b = 2 * math.pi * j / m_
            rr = major + minor * math.cos(b)
            vs.append(bm.verts.new(xf @ Vector((rr * math.cos(a), rr * math.sin(a),
                                                minor * math.sin(b)))))
    for i in range(n):
        for j in range(m_):
            a = vs[i * m_ + j]
            b = vs[i * m_ + (j + 1) % m_]
            c = vs[((i + 1) % n) * m_ + (j + 1) % m_]
            d = vs[((i + 1) % n) * m_ + j]
            f = bm.faces.new((a, b, c, d))
            f.material_index = mi


def finish(name, bm, mats):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    for p in me.polygons:
        p.use_smooth = False
    return me


def new_obj(name, me, coll, loc=(0, 0, 0), yaw=0.0):
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    ob.rotation_euler = (0, 0, yaw)
    coll.objects.link(ob)
    return ob


# ---------------------------------------------------------------------- route
def ribbon(name, pts, lo, hi, z, material, coll):
    """Ruban de largeur [lo, hi] (décalage latéral par rapport à l'axe)."""
    bm = bmesh.new()
    left, right = [], []
    for i, p in enumerate(pts):
        a = pts[max(i - 1, 0)]
        b = pts[min(i + 1, len(pts) - 1)]
        t = (b - a).normalized()
        n = Vector((-t.y, t.x, 0))
        left.append(bm.verts.new(p + n * hi + Vector((0, 0, z))))
        right.append(bm.verts.new(p + n * lo + Vector((0, 0, z))))
    for i in range(len(pts) - 1):
        bm.faces.new((right[i], right[i + 1], left[i + 1], left[i]))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(material)
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def build_road(coll):
    pts = centerline(1.0)
    asphalt = mat("Asphalt", (0.17, 0.17, 0.18), 0.9)
    white = mat("RoadPaint", (0.9, 0.9, 0.88), 0.7)
    ribbon("Road", pts, -ROAD_W / 2, ROAD_W / 2, ROAD_Z, asphalt, coll)
    # lignes de rive continues
    for s, nm in ((-1, "EdgeL"), (1, "EdgeR")):
        o = s * (ROAD_W / 2 - 0.35)
        ribbon(nm, pts, o - 0.06, o + 0.06, ROAD_Z + 0.01, white, coll)
    # ligne centrale en tirets : 3 m peints / 5 m vides
    bm = bmesh.new()
    i = 0
    while i < len(pts) - 4:
        seg = pts[i:i + 4]
        verts_l, verts_r = [], []
        for k, p in enumerate(seg):
            a = seg[max(k - 1, 0)]
            b = seg[min(k + 1, len(seg) - 1)]
            t = (b - a).normalized()
            n = Vector((-t.y, t.x, 0))
            verts_l.append(bm.verts.new(p + n * 0.07 + Vector((0, 0, ROAD_Z + 0.01))))
            verts_r.append(bm.verts.new(p - n * 0.07 + Vector((0, 0, ROAD_Z + 0.01))))
        for k in range(len(seg) - 1):
            bm.faces.new((verts_r[k], verts_r[k + 1], verts_l[k + 1], verts_l[k]))
        i += 8
    me = bpy.data.meshes.new("CenterDashes")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(white)
    coll.objects.link(bpy.data.objects.new("CenterDashes", me))


# -------------------------------------------------------------------- humains
def person_mesh(shirt_i, seated, hat_i):
    bm = bmesh.new()
    skin_i = (shirt_i + hat_i + int(seated)) % len(PALETTE_SKIN)
    mats = [
        mat(f"Shirt{shirt_i}", PALETTE_SHIRTS[shirt_i], 0.85),
        mat(f"Skin{skin_i}", PALETTE_SKIN[skin_i], 0.7),
        mat(f"Pants{shirt_i % len(PALETTE_PANTS)}", PALETTE_PANTS[shirt_i % len(PALETTE_PANTS)], 0.9),
    ]
    hat = PALETTE_HATS[hat_i]
    if hat:
        mats.append(mat(f"Hat{hat_i}", hat, 0.8))
    if not seated:
        add_cyl(bm, 0.12, 0.10, 0.85, (0, 0, 0.42), 2)                  # jambes
        add_cyl(bm, 0.21, 0.17, 0.62, (0, 0, 1.15), 0)                  # buste
        add_sphere(bm, 0.115, (0, 0, 1.58), 1, (1, 1, 1.15))            # tête
        if hat:
            add_cyl(bm, 0.24, 0.24, 0.025, (0, 0, 1.66), 3, seg=10)     # bord
            add_cyl(bm, 0.12, 0.11, 0.10, (0, 0, 1.72), 3, seg=10)      # calotte
    else:
        add_box(bm, (0.34, 0.62, 0.15), (0, 0.32, 0.1), 2)             # jambes tendues
        add_cyl(bm, 0.21, 0.17, 0.58, (0, -0.05, 0.42), 0)              # buste
        add_sphere(bm, 0.115, (0, -0.02, 0.85), 1, (1, 1, 1.15))
        if hat:
            add_cyl(bm, 0.24, 0.24, 0.025, (0, -0.02, 0.93), 3, seg=10)
            add_cyl(bm, 0.12, 0.11, 0.10, (0, -0.02, 0.99), 3, seg=10)
    return finish(f"P_{shirt_i}_{int(seated)}_{hat_i}", bm, mats)


class Crowd:
    def __init__(self, coll, rng):
        self.coll, self.rng = coll, rng
        self.protos = {}
        self.taken = set()
        self.count = 0

    def proto(self, shirt, seated, hat):
        k = (shirt, seated, hat)
        if k not in self.protos:
            self.protos[k] = person_mesh(*k)
        return self.protos[k]

    def spawn(self, x, y, sit_p=0.2, hat_p=0.45, on_road=False, face_road=True):
        rng = self.rng
        if not on_road and road_dist(x, y) < ROAD_W / 2 + 0.1:
            return False
        key = (int(x / 0.55), int(y / 0.55))
        if key in self.taken:
            return False
        self.taken.add(key)
        seated = rng.random() < sit_p
        hat = rng.randint(1, 3) if rng.random() < hat_p else 0
        me = self.proto(rng.randrange(len(PALETTE_SHIRTS)), seated, hat)
        z = terrain_h(x, y) - 0.02
        if face_road:
            cx_, cy_ = nearest_center(x, y)
            yaw = math.atan2(-(cx_ - x), (cy_ - y)) + rng.uniform(-0.7, 0.7)
        else:
            yaw = rng.uniform(0, 2 * math.pi)
        o = new_obj(f"Person{self.count:04d}", me, self.coll, (x, y, z), yaw)
        s = rng.uniform(0.9, 1.08)
        o.scale = (s, s, s)
        self.count += 1
        return True

    def fill(self, n, sampler, **kw):
        tries = 0
        done = 0
        while done < n and tries < n * 12:
            tries += 1
            x, y = sampler()
            if self.spawn(x, y, **kw):
                done += 1


def build_crowd(coll, rng, scale):
    c = Crowd(coll, rng)
    u = rng.uniform
    edge = ROAD_W / 2 + 0.35
    N = lambda n: max(1, int(n * scale))

    # 1) foule dense en bordure extérieure de la branche haute (ligne de crête)
    c.fill(N(190), lambda: (u(4, 105), R + edge + u(0, 3.8) ** 1.0), sit_p=0.12)
    # 2) arc autour du sommet de l'épingle (derrière le camping-car)
    def arc():
        th = math.radians(u(100, 235))
        r = R + edge + u(0, 3.5)
        return r * math.cos(th), r * math.sin(th)
    c.fill(N(60), arc, sit_p=0.25)
    # 3) bord côté caméra de la branche basse (spectateurs assis sur l'herbe)
    c.fill(N(55), lambda: (u(4, 55), -(R + edge + u(0, 3.5))), sit_p=0.65)
    # 4) groupes sur l'îlot herbeux entre les deux branches
    for (gx, gy, rad, n, sp) in ((9, -3.0, 5.0, 55, 0.5), (21, 2.0, 4.5, 40, 0.45),
                                 (36, -4.0, 4.0, 24, 0.7), (60, 1.0, 5.0, 22, 0.4),
                                 (-1, 0, 4, 10, 0.3)):
        def disc(gx=gx, gy=gy, rad=rad):
            a = u(0, 2 * math.pi)
            r = rad * math.sqrt(rng.random())
            return gx + r * math.cos(a), gy + r * math.sin(a)
        c.fill(N(n), disc, sit_p=sp)
    # 5) groupe d'avant-plan assis sur le talus, près de la caméra
    c.fill(N(14), lambda: (u(14, 27), u(-27, -19)), sit_p=0.85, hat_p=0.8, face_road=True)
    # 6) quelques personnes sur la chaussée
    c.fill(N(14), lambda: (u(25, 60), -R + u(-ROAD_W / 2 + 0.6, ROAD_W / 2 - 0.6)),
           sit_p=0.0, on_road=True)
    c.fill(N(8), lambda: (u(40, 95), R + u(-ROAD_W / 2 + 0.6, 0.5)), sit_p=0.0, on_road=True)
    return c.count


# ---------------------------------------------------------------------- props
def yaw_xy(tx, ty):
    return math.atan2(-tx, ty)


def build_van(coll):
    bm = bmesh.new()
    mats = [mat("VanWhite", (0.92, 0.92, 0.90), 0.5), mat("VanGlass", (0.05, 0.07, 0.10), 0.2),
            mat("Tire", (0.03, 0.03, 0.03), 0.9), mat("VanStripe", (0.55, 0.55, 0.58), 0.6)]
    add_box(bm, (2.15, 4.6, 2.35), (0, -0.55, 1.55), 0)                  # cellule
    add_box(bm, (2.1, 1.9, 1.55), (0, 2.45, 1.05), 0)                    # capot-cabine
    add_box(bm, (1.8, 0.05, 0.7), (0, 3.38, 1.55), 1)                    # pare-brise
    add_box(bm, (0.04, 1.1, 0.55), (1.08, 2.4, 1.55), 1)                 # vitre cabine D
    add_box(bm, (0.04, 1.1, 0.55), (-1.08, 2.4, 1.55), 1)
    add_box(bm, (0.04, 1.2, 0.6), (1.09, -0.6, 1.9), 1)                  # fenêtre cellule
    add_box(bm, (2.17, 0.02, 0.12), (0, -0.55, 1.1), 3)
    for sx in (-1, 1):
        for y in (-1.7, 2.3):
            add_cyl(bm, 0.38, 0.38, 0.25, (sx * 1.0, y, 0.38), 2,
                    rot=Matrix.Rotation(math.pi / 2, 3, "Y"), seg=12)
    me = finish("Van", bm, mats)
    th = math.radians(-150)
    r = R + ROAD_W / 2 - 1.5
    x, y = r * math.cos(th), r * math.sin(th)
    t = (-math.sin(th), math.cos(th))
    new_obj("CamperVan", me, coll, (x, y, 0.0), yaw_xy(*t))


def build_umbrellas(coll):
    items = [((6.5, R + edge_o(0.8)), (0.65, 0.85, 0.12), 1.0),   # vert-jaune (fond)
             ((12.0, R + edge_o(1.6)), (0.80, 0.72, 0.45), 0.8),  # beige
             ((24.0, -4.0), (0.95, 0.80, 0.15), 1.0),             # jaune îlot
             ((60.0, 2.0), (0.85, 0.20, 0.15), 0.9)]
    for i, ((x, y), col, s) in enumerate(items):
        bm = bmesh.new()
        mats = [mat("UmbPole", (0.15, 0.15, 0.15), 0.5), mat(f"Umb{i}", col, 0.7)]
        add_cyl(bm, 0.02, 0.02, 2.3, (0, 0, 1.15), 0, seg=6)
        add_cyl(bm, 0.95, 0.04, 0.38, (0, 0, 2.3), 1, seg=12)
        me = finish(f"Umbrella{i}", bm, mats)
        o = new_obj(f"Umbrella{i}", me, coll, (x, y, terrain_h(x, y)))
        o.scale = (s, s, s)


def edge_o(d):
    return ROAD_W / 2 + 0.35 + d


def build_bikes(coll):
    bm = bmesh.new()
    mats = [mat("BikeTire", (0.04, 0.04, 0.04), 0.9), mat("BikeFrame", (0.15, 0.3, 0.7), 0.4, 0.5)]
    rx = Matrix.Rotation(math.pi / 2, 3, "Y")
    for y in (-0.55, 0.55):
        add_torus(bm, 0.33, 0.02, (0, y, 0.35), 0, rot=rx)
    add_box(bm, (0.03, 1.0, 0.03), (0, 0, 0.55), 1)
    add_box(bm, (0.03, 0.5, 0.03), (0, -0.2, 0.45), 1, rot=Matrix.Rotation(0.8, 3, "X"))
    add_box(bm, (0.03, 0.03, 0.4), (0, 0.5, 0.6), 1)
    add_box(bm, (0.45, 0.03, 0.03), (0, 0.5, 0.82), 1)
    me = finish("Bike", bm, mats)
    for i, (x, y, yaw) in enumerate(((30.0, -2.5, 1.2), (31.0, -1.4, 1.4), (29.5, 0.0, 1.0),
                                    (32.0, -3.6, 1.3))):
        o = new_obj(f"Bike{i}", me, coll, (x, y, terrain_h(x, y)), yaw)
        o.rotation_euler.y = 0.22                      # appuyés


def build_posts(coll):
    bm = bmesh.new()
    mats = [mat("Wood", (0.35, 0.22, 0.12), 0.9)]
    add_cyl(bm, 0.05, 0.05, 1.1, (0, 0, 0.55), 0, seg=6)
    me = finish("Post", bm, mats)
    rng = random.Random(3)
    for i in range(9):
        x = 6 + i * 2.3 + rng.uniform(-0.3, 0.3)
        y = -(R + ROAD_W / 2 + 0.7 + rng.uniform(0, 0.4))
        o = new_obj(f"Post{i}", me, coll, (x, y, terrain_h(x, y)))
        o.rotation_euler = (rng.uniform(-0.12, 0.12), rng.uniform(-0.12, 0.12), 0)


def build_flag(coll):
    bm = bmesh.new()
    mats = [mat("Pole", (0.7, 0.7, 0.7), 0.4, 0.8), mat("FlagBlue", (0.1, 0.25, 0.7), 0.8),
            mat("FlagWhite", (0.95, 0.95, 0.95), 0.8)]
    add_cyl(bm, 0.02, 0.02, 3.2, (0, 0, 1.6), 0, seg=6)
    for k in range(4):
        add_box(bm, (0.02, 0.9, 0.09), (0, 0.46, 3.05 - k * 0.09 * 2), 1 if k % 2 == 0 else 2)
    me = finish("Flag", bm, mats)
    x, y = -4.0, 18.0
    new_obj("Flag", me, coll, (x, y, terrain_h(x, y)), 0.3)


# --------------------------------------------------------------- scène globale
def setup_world(sun_coll):
    w = bpy.data.worlds.new("World")
    bpy.context.scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    bg = nt.nodes["Background"]
    try:
        sky = nt.nodes.new("ShaderNodeTexSky")
        sky.sky_type = "NISHITA"
        sky.sun_elevation = math.radians(52)
        sky.sun_rotation = math.radians(210)
        nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 1.0
    except Exception:
        bg.inputs["Color"].default_value = (0.35, 0.55, 0.9, 1)
    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy = 4.0
    sun.angle = math.radians(1.5)
    ob = bpy.data.objects.new("Sun", sun)
    ob.rotation_euler = (math.radians(52), 0, math.radians(30))
    sun_coll.objects.link(ob)


def setup_camera(coll, reference):
    cam_xy = Vector((31.0, -34.0))
    target = Vector((0.0, 4.0, 0.0))
    pos = Vector((cam_xy.x, cam_xy.y, terrain_h(*cam_xy) + 5.0))
    cd = bpy.data.cameras.new("Cam")
    cd.lens = 32
    cd.clip_end = 3000
    ob = bpy.data.objects.new("Camera", cd)
    ob.location = pos
    d = target - pos
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    coll.objects.link(ob)
    bpy.context.scene.camera = ob
    if reference and os.path.exists(reference):
        try:
            img = bpy.data.images.load(reference)
            bg = cd.background_images.new()
            bg.image = img
            bg.alpha = 0.5
            cd.show_background_images = True
        except Exception as e:                       # API variable selon versions
            print("Image de référence ignorée :", e)


def animate_camera(frames, fps=24, pan_deg=9.0, dolly=7.0):
    """Léger pan vers la droite + léger travelling arrière (ease in/out)."""
    sc = bpy.context.scene
    sc.render.fps = fps
    sc.frame_start, sc.frame_end = 1, frames
    cam = sc.camera
    fwd = cam.rotation_euler.to_quaternion() @ Vector((0, 0, -1))
    fwd.z = 0
    fwd.normalize()
    p0 = cam.location.copy()
    e0 = cam.rotation_euler.copy()
    p1 = p0 - fwd * dolly
    p1.z = terrain_h(p1.x, p1.y) + 5.0          # reste au-dessus du talus
    e1 = e0.copy()
    e1.z -= math.radians(pan_deg)               # z négatif = pan à droite
    cam.location, cam.rotation_euler = p0, e0
    cam.keyframe_insert("location", frame=1)
    cam.keyframe_insert("rotation_euler", frame=1)
    cam.location, cam.rotation_euler = p1, e1
    cam.keyframe_insert("location", frame=frames)
    cam.keyframe_insert("rotation_euler", frame=frames)
    cam.location, cam.rotation_euler = p0, e0


def setup_render(w, h):
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.resolution_percentage = 100
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            sc.render.engine = eng
            break
        except TypeError:
            continue
    try:
        sc.view_settings.view_transform = "Filmic" if "Filmic" in \
            sc.view_settings.bl_rna.properties["view_transform"].enum_items else "AgX"
    except Exception:
        pass


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--render")
    ap.add_argument("--save")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--crowd-scale", type=float, default=1.0)
    ap.add_argument("--reference", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ".",
        "reference.jpg"))
    ap.add_argument("--frames", type=int, default=120,
                    help="durée du mouvement de caméra en images (0 = caméra fixe)")
    ap.add_argument("--anim", action="store_true", help="rend toute l'animation")
    ap.add_argument("--res", type=int, nargs=2, default=(1320, 866))
    a = ap.parse_args(argv)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene

    def coll(name):
        c = bpy.data.collections.new(name)
        sc.collection.children.link(c)
        return c
    c_terrain, c_road, c_crowd, c_props, c_light, c_cam = (
        coll("Terrain"), coll("Road"), coll("Crowd"), coll("Props"),
        coll("Lighting"), coll("Camera"))

    rng = random.Random(a.seed)
    noise.seed_set(a.seed)
    build_terrain(c_terrain)
    build_road(c_road)
    n = build_crowd(c_crowd, rng, a.crowd_scale)
    build_van(c_props)
    build_umbrellas(c_props)
    build_bikes(c_props)
    build_posts(c_props)
    build_flag(c_props)
    setup_world(c_light)
    setup_camera(c_cam, a.reference)
    setup_render(*a.res)
    if a.frames > 0:
        animate_camera(a.frames)
    print(f"Layout prêt : {n} spectateurs")

    if a.save:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.save))
    if a.render:
        sc.render.filepath = os.path.abspath(a.render)
        bpy.ops.render.render(animation=a.anim, write_still=not a.anim)


if __name__ == "__main__":
    main()
