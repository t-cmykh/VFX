"""
Layout 3D "col de montagne / épingle de route avec spectateurs" (bpy)
Basé sur la photo reference.jpg (virage en épingle, foule de spectateurs, peloton).

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
    --riders N          taille du peloton (défaut 62, 0 = aucun), de gauche à droite
    --group2 N          taille du 2e groupe en retard (défaut 16, 0 = aucun)
    --radius R          rayon de l'épingle ; --camera-fit F.json : caméra calée sur une vidéo
    --transparent       fond transparent + shadow catcher (incrustation) ; --no-hero : sans humain central
    --hero              décor + un humain riggé simple au centre du monde (0, 0)
    --human             foule d'humains riggés en liesse (remplace --stick), + humain central
    --stick             foule en bonhommes bâtons en liesse (applaudissent, sautent, crient), sans coureurs
    --set-only          décor seul (sans foule, coureurs ni accessoires)
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
FIT_GROUND = False  # sol simplifié (pente douce) quand la caméra est calée sur une vidéo
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
    if FIT_GROUND:      # sol bas et lisse : sert juste de réceptacle d'ombres, sans masquer la foule
        h = (0.18 * dout if k < 0 else -0.15 * dout) if dout > 0 else 0.0
        return h * smoothstep(ROAD_W / 2 + 0.5, ROAD_W / 2 + 6.0, road_dist(x, y))
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
    return res["verts"]


def add_box(bm, size, loc, mi, rot=None):
    m = Matrix.Translation(loc)
    if rot is not None:
        m = m @ rot.to_4x4()
    m = m @ Matrix.Diagonal(Vector((size[0], size[1], size[2], 1.0)))
    res = bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    tag(res["verts"], mi)
    return res["verts"]


def add_sphere(bm, r, loc, mi, scale=(1, 1, 1)):
    m = Matrix.Translation(loc) @ Matrix.Diagonal(Vector((*scale, 1.0)))
    res = bmesh.ops.create_uvsphere(bm, u_segments=8, v_segments=6, radius=r, matrix=m)
    tag(res["verts"], mi)
    return res["verts"]


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


def arm_mesh(shirt_i):
    """Bras qui pend le long de -Z, pivot à l'épaule (manche + main)."""
    bm = bmesh.new()
    mats = [mat(f"Shirt{shirt_i}", PALETTE_SHIRTS[shirt_i], 0.85),
            mat("Skin0", PALETTE_SKIN[0], 0.7)]
    add_cyl(bm, 0.045, 0.055, 0.40, (0, 0, -0.20), 0, seg=6)
    add_cyl(bm, 0.035, 0.04, 0.20, (0, 0, -0.50), 1, seg=6)
    return finish(f"Arm_{shirt_i}", bm, mats)


def stick_mesh(seated):
    """Bonhomme bâton : tête, tronc et jambes en traits noirs (bras à part)."""
    bm = bmesh.new()
    mats = [mat("Stick", (0.02, 0.02, 0.02), 0.9)]
    r = 0.045
    if not seated:
        add_sphere(bm, 0.13, (0, 0, 1.62), 0)
        add_limb(bm, (0, 0, 0.92), (0, 0, 1.45), r, r, 0, seg=6)            # tronc
        add_limb(bm, (0, 0, 0.92), (-0.14, 0, 0.0), r, r, 0, seg=6)         # jambes
        add_limb(bm, (0, 0, 0.92), (0.14, 0, 0.0), r, r, 0, seg=6)
    else:
        add_sphere(bm, 0.13, (0, -0.02, 0.9), 0)
        add_limb(bm, (0, 0, 0.12), (0, -0.02, 0.68), r, r, 0, seg=6)
        add_limb(bm, (0, 0, 0.12), (-0.12, 0.6, 0.12), r, r, 0, seg=6)      # jambes tendues
        add_limb(bm, (0, 0, 0.12), (0.12, 0.6, 0.12), r, r, 0, seg=6)
    return finish(f"Stick_{int(seated)}", bm, mats)


def stick_arm_mesh():
    bm = bmesh.new()
    mats = [mat("Stick", (0.02, 0.02, 0.02), 0.9)]
    add_limb(bm, (0, 0, 0), (0, 0, -0.62), 0.04, 0.04, 0, seg=6)
    add_sphere(bm, 0.06, (0, 0, -0.64), 0)                                   # main
    return finish("StickArm", bm, mats)


class Crowd:
    def __init__(self, coll, rng, stick=False, human=False):
        self.coll, self.rng = coll, rng
        self.stick = stick
        self.human = human
        self.protos = {}
        self.arm_protos = {}
        self.people = []          # (objet, bras G/D, assis, x, y)
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
        if math.hypot(x, y) < 1.4:                      # centre réservé au humain de référence
            return False
        key = (int(x / 0.55), int(y / 0.55))
        if key in self.taken:
            return False
        self.taken.add(key)
        seated = rng.random() < sit_p
        hat = rng.randint(1, 3) if rng.random() < hat_p else 0
        shirt = rng.randrange(len(PALETTE_SHIRTS))
        z = terrain_h(x, y) - 0.02
        if face_road:
            cx_, cy_ = nearest_center(x, y)
            yaw = math.atan2(-(cx_ - x), (cy_ - y)) + rng.uniform(-0.7, 0.7)
        else:
            yaw = rng.uniform(0, 2 * math.pi)
        if self.human:
            o = human_instance(f"Human{self.count:04d}", self.coll, shirt, (x, y, z), yaw,
                               rng.uniform(0.92, 1.08))
            self.people.append((o, [], seated, x, y))
            self.count += 1
            return True
        if self.stick:
            me = self.protos.setdefault(("stick", seated), stick_mesh(seated))
        else:
            me = self.proto(shirt, seated, hat)
        o = new_obj(f"Person{self.count:04d}", me, self.coll, (x, y, z), yaw)
        s = rng.uniform(0.9, 1.08)
        o.scale = (s, s, s)
        akey = "stick" if self.stick else shirt
        if akey not in self.arm_protos:
            self.arm_protos[akey] = stick_arm_mesh() if self.stick else arm_mesh(shirt)
        arms = []
        for side in (1, -1):
            arm = bpy.data.objects.new(f"{o.name}_arm{'R' if side > 0 else 'L'}",
                                       self.arm_protos[akey])
            self.coll.objects.link(arm)
            arm.parent = o
            arm.location = (side * 0.24, 0, 0.68 if seated else 1.42)
            arm.rotation_euler = (0, -side * 0.12, 0)       # bras le long du corps
            arms.append(arm)
        self.people.append((o, arms, seated, x, y))
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


def build_crowd(coll, rng, scale, stick=False, human=False):
    c = Crowd(coll, rng, stick, human)
    u = rng.uniform
    edge = ROAD_W / 2 + 0.35
    N = lambda n: max(1, int(n * scale))

    # 1) foule dense en bordure extérieure de la branche haute (ligne de crête)
    c.fill(N(190), lambda: (u(4, 105), R + edge + u(0, 3.8) ** 1.0), sit_p=0.12)
    # 2) arc autour du sommet de l'épingle
    def arc():
        th = math.radians(u(100, 235))
        r = R + edge + u(0, 3.5)
        return r * math.cos(th), r * math.sin(th)
    c.fill(N(60), arc, sit_p=0.25)
    # 3) bord côté caméra de la branche basse (spectateurs assis sur l'herbe)
    c.fill(N(55 if LOWER_X1 < 60 else 95), lambda: (u(4, LOWER_X1), -(R + edge + u(0, 3.5))), sit_p=0.65)
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
    return c


def build_crowd_fit(coll, rng, scale, stick=False, human=False):
    """Foule concentrée dans la fenêtre visible d'une caméra calée sur une vidéo
    (téléobjectif : on ne voit que l'apex et ~35 m de route de chaque côté)."""
    c = Crowd(coll, rng, stick, human)
    u = rng.uniform
    edge = ROAD_W / 2 + 0.35
    N = lambda n: max(1, int(n * scale))
    # bord extérieur de la branche haute (au fond)
    c.fill(N(70), lambda: (u(-2, 26), R + edge + u(0, 3.6)), sit_p=0.12)
    # arc autour du sommet de l'épingle
    def arc():
        th = math.radians(u(95, 240))
        r = R + edge + u(0, 3.6)
        return r * math.cos(th), r * math.sin(th)
    c.fill(N(85), arc, sit_p=0.25)
    # bord bas (côté caméra), assis et debout sur le talus
    c.fill(N(120), lambda: (u(0, 40), -(R + edge + u(0, 4.0))), sit_p=0.5)
    # îlot herbeux entre les deux branches
    for gx, gy, rad, n, sp in ((6, -0.5, 3.8, 34, 0.4), (17, 1.0, 3.5, 28, 0.4),
                               (28, -1.5, 3.2, 20, 0.5)):
        def disc(gx=gx, gy=gy, rad=rad):
            a = u(0, 2 * math.pi)
            r = rad * math.sqrt(rng.random())
            return gx + r * math.cos(a), gy + r * math.sin(a)
        c.fill(N(n), disc, sit_p=sp)
    return c


# ---------------------------------------------------------------------- props
def yaw_xy(tx, ty):
    return math.atan2(-tx, ty)


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



# -------------------------------------------------------------------- peloton
PELOTON_JERSEYS = [
    (0.95, 0.80, 0.10), (0.80, 0.08, 0.08), (0.10, 0.25, 0.75), (0.95, 0.95, 0.95),
    (0.10, 0.55, 0.25), (0.92, 0.42, 0.08), (0.10, 0.10, 0.13), (0.80, 0.30, 0.60),
]
Q = math.pi * R / 2          # longueur d'un quart d'arc
LOWER_X1 = 55.0              # extension x du bord bas de la route (foule)
PELO_S0 = 24.0               # abscisse de la tête du peloton à l'image 1
PELO_SPEED = 11.5            # m/s (~41 km/h)
PELO_HEAD = -18.0            # coureur le plus avancé (échappé), en 'a'


def path_pos(s):
    """Abscisse curviligne s (0 = sommet de l'épingle). Sens de course : la route
    monte la branche basse, tourne au sommet, puis file vers +X sur la branche haute."""
    if s <= -Q:
        return Vector((-s - Q, -R, 0)), Vector((-1, 0, 0))
    if s >= Q:
        return Vector((s - Q, R, 0)), Vector((1, 0, 0))
    th = -math.pi - s / R
    return (Vector((R * math.cos(th), R * math.sin(th), 0)),
            Vector((math.sin(th), -math.cos(th), 0)))


def add_limb(bm, p0, p1, r0, r1, mi, seg=6):
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    rot = d.to_track_quat("Z", "X").to_matrix()
    return add_cyl(bm, r0, r1, d.length, (p0 + p1) / 2, mi, rot=rot, seg=seg)


def rider_mesh(team, white_helmet):
    """Cycliste + vélo, origine au sol, l'avant est +Y."""
    bm = bmesh.new()
    jersey = PELOTON_JERSEYS[team]
    mats = [mat(f"Jersey{team}", jersey, 0.7),
            mat("RiderSkin", (0.80, 0.58, 0.46), 0.7),
            mat("Shorts", (0.05, 0.05, 0.07), 0.8),
            mat("HelmetW" if white_helmet else f"HelmetT{team}",
                (0.95, 0.95, 0.95) if white_helmet else jersey, 0.4),
            mat("BikeFrameR", (0.08, 0.08, 0.10), 0.4, 0.6),
            mat("BikeTire", (0.04, 0.04, 0.04), 0.9)]
    rw = Matrix.Rotation(math.pi / 2, 3, "Y")
    for y in (-0.52, 0.52):                                   # roues
        add_torus(bm, 0.33, 0.022, (0, y, 0.35), 5, rot=rw, n=16, m_=4)
    add_limb(bm, (0, -0.52, 0.35), (0, -0.12, 0.88), 0.013, 0.013, 4)   # base arrière
    add_limb(bm, (0, -0.12, 0.88), (0, 0.38, 0.80), 0.014, 0.014, 4)    # tube horizontal
    add_limb(bm, (0, 0.38, 0.80), (0, 0.52, 0.35), 0.013, 0.013, 4)     # fourche
    add_limb(bm, (0, -0.12, 0.88), (0, 0.10, 0.30), 0.016, 0.016, 4)    # tube diagonal
    add_limb(bm, (0, 0.38, 0.80), (0, 0.36, 0.95), 0.012, 0.012, 4)     # potence
    add_box(bm, (0.40, 0.03, 0.03), (0, 0.38, 0.95), 4)                 # guidon
    hip = Vector((0, -0.12, 0.98))
    sh = Vector((0, 0.40, 1.22))
    add_limb(bm, hip, sh, 0.15, 0.13, 0, seg=8)                         # buste penché
    add_sphere(bm, 0.075, sh + Vector((0, 0.14, 0.00)), 1)              # visage
    add_sphere(bm, 0.115, sh + Vector((0, 0.12, 0.07)), 3, (1, 1.3, 0.8))  # casque
    for sx in (-1, 1):
        add_limb(bm, sh + Vector((sx * 0.17, 0, -0.04)), (sx * 0.20, 0.40, 0.97), 0.05, 0.04, 0)
        up = sx < 0                                                     # une jambe haute, une basse
        knee = (sx * 0.11, 0.26, 0.84) if up else (sx * 0.11, 0.06, 0.62)
        foot = (sx * 0.11, 0.10, 0.50) if up else (sx * 0.11, 0.00, 0.28)
        add_limb(bm, (sx * 0.10, hip.y, hip.z), knee, 0.075, 0.06, 2)   # cuisse (cuissard)
        add_limb(bm, knee, foot, 0.05, 0.04, 1)                         # mollet
    return finish(f"Rider_{team}_{int(white_helmet)}", bm, mats)


def blob(rng, n, length, a0=0.0, half_max=None):
    """Nuage de coureurs en forme de 'larme' (étroit devant, large au milieu,
    effilé derrière), sans chevauchement. (a, b) = distance derrière la tête
    de course, décalage latéral."""
    half_max = ROAD_W / 2 - 0.75 if half_max is None else half_max
    pts = []
    tries = 0
    while len(pts) < n and tries < 20000:
        tries += 1
        a = length * rng.random() ** 1.2
        w = 0.9 + (half_max - 0.9) * math.sin(math.pi * min(1.0, (a / length) ** 0.75))
        b = rng.uniform(-w, w)
        if all(((a - pa) / 1.75) ** 2 + ((b - pb) / 0.85) ** 2 > 1.0 for pa, pb in pts):
            pts.append((a, b))
    return sorted((a0 + a, b) for a, b in pts)


def peloton_formation(rng, n, n2=16):
    """Peloton principal + échappés devant + attardés + 2e groupe en retard."""
    main = blob(rng, n, 28.0)
    breakaway = [(-11.0, 0.3), (-14.5, -0.5), (-18.0, 0.2)]          # 3 échappés devant
    stragglers = [(34.0, 0.4), (37.5, -0.6), (41.0, 0.1), (45.0, -0.3)]
    group2 = blob(rng, n2, 16.0, a0=52.0, half_max=2.0) if n2 > 0 else []
    return breakaway + main + stragglers + group2


def build_peloton(coll, rng, frames, fps=24, n=62, n2=16, speed=PELO_SPEED):
    protos = {}
    form = peloton_formation(rng, n, n2)
    s_front0 = PELO_S0
    pref = bpy.context.preferences.edit
    old_interp = pref.keyframe_new_interpolation_type
    pref.keyframe_new_interpolation_type = "LINEAR"     # vitesse constante
    team_of = {}
    last = max(frames, 1)
    for i, (a, b) in enumerate(form):
        grp = int(a // 6)                  # les équipes roulent par blocs
        if grp not in team_of:
            team_of[grp] = rng.sample(range(len(PELOTON_JERSEYS)), 3)
        team = rng.choice(team_of[grp]) if rng.random() < 0.8 else rng.randrange(len(PELOTON_JERSEYS))
        wh = rng.random() < 0.5
        if (team, wh) not in protos:
            protos[(team, wh)] = rider_mesh(team, wh)
        o = new_obj(f"Rider{i:02d}", protos[(team, wh)], coll)
        phase = rng.uniform(0, 6.28)
        steps = list(range(1, last + 1, 2))
        if steps[-1] != last:
            steps.append(last)
        for f in steps:
            s = s_front0 + speed * (f - 1) / fps - a
            c, t = path_pos(s)
            nrm = Vector((-t.y, t.x, 0))
            sway = 0.10 * math.sin(phase + (f - 1) / fps * 2.2)
            p = c + nrm * (b + sway)
            o.location = (p.x, p.y, ROAD_Z)
            o.rotation_euler = (0, 0, yaw_xy(t.x, t.y) + 0.04 * math.sin(phase + f * 0.1))
            if frames > 0:
                o.keyframe_insert("location", frame=f)
                o.keyframe_insert("rotation_euler", frame=f)
            else:
                break
    pref.keyframe_new_interpolation_type = old_interp
    return len(form), max(a for a, _ in form)

def path_s(x, y):
    """Abscisse curviligne (cf. path_pos) du point de la route le plus proche."""
    if x >= 0:
        return Q + x if y >= 0 else -Q - x
    th = math.atan2(y, x)
    return R * (math.pi - th) if y >= 0 else -R * (th + math.pi)


def animate_crowd(crowd, frames, fps, tail_a, seed):
    """Les spectateurs sautent de joie, bras levés, quand les coureurs passent
    devant eux (de l'arrivée de l'échappé jusqu'au départ du dernier groupe)."""
    rng = random.Random(seed + 101)
    pref = bpy.context.preferences.edit
    old = pref.keyframe_new_interpolation_type
    pref.keyframe_new_interpolation_type = "LINEAR"
    head_lead = -PELO_HEAD
    n_anim = 0
    for o, arms, seated, x, y in crowd.people:
        sp = path_s(x, y)
        # fenêtre de joie en images : tête à 10 m avant, dernier coureur 8 m après
        f0 = 1 + fps * (sp - 10 - PELO_S0 - head_lead) / PELO_SPEED + rng.uniform(0, 4)
        f1 = 1 + fps * (sp + 8 - PELO_S0 + tail_a) / PELO_SPEED + rng.uniform(-3, 6)
        f0, f1 = max(1, int(f0)), min(frames, int(f1))
        if f1 - f0 < 4:
            continue
        n_anim += 1
        amp = rng.uniform(0.02, 0.07) if seated else rng.uniform(0.12, 0.34)
        period = rng.uniform(9, 15)                    # images par saut
        ph = rng.uniform(0, math.pi)
        up = rng.uniform(2.45, 2.95)                   # amplitude bras levés (rad)
        z0 = o.location.z
        frames_k = list(range(f0, f1 + 1, 2))
        if frames_k[-1] != f1:
            frames_k.append(f1)
        for f in frames_k:
            env = min(1.0, (f - f0) / 6.0, (f1 - f) / 8.0 + 0.0)
            env = max(0.0, env)
            jump = abs(math.sin(math.pi * (f - f0) / period + ph)) * amp * env
            o.location.z = z0 + jump
            o.keyframe_insert("location", index=2, frame=f)
            for k, arm in enumerate(arms):
                side = 1 if k == 0 else -1
                wave = 0.35 * math.sin(2 * math.pi * (f - f0) / period + ph + k * 1.7)
                arm.rotation_euler.y = -side * (0.12 + env * (up - 0.12 + wave))
                arm.keyframe_insert("rotation_euler", index=1, frame=f)
        o.location.z = z0
        for k, arm in enumerate(arms):                 # retour à la pose repos
            arm.rotation_euler.y = -(1 if k == 0 else -1) * 0.12
    pref.keyframe_new_interpolation_type = old
    return n_anim


def animate_crowd_stick(crowd, frames, fps, seed):
    """Foule de bonhommes bâtons en liesse sur toute la durée : chacun applaudit,
    saute bras levés ou crie (mains en porte-voix, buste qui se cambre)."""
    rng = random.Random(seed + 202)
    pref = bpy.context.preferences.edit
    old = pref.keyframe_new_interpolation_type
    pref.keyframe_new_interpolation_type = "LINEAR"
    kinds = {"clap": 0, "cheer": 0, "shout": 0}
    for o, arms, seated, x, y in crowd.people:
        r = rng.random()
        kind = "clap" if r < 0.34 else ("cheer" if r < 0.80 else "shout")
        kinds[kind] += 1
        ph = rng.uniform(0, 6.28)
        fr = rng.uniform(1.6, 2.3)                    # sauts / s
        amp = rng.uniform(0.02, 0.05) if seated else rng.uniform(0.15, 0.38)
        if kind == "clap":
            amp *= 0.35
        z0 = o.location.z
        for f in range(1, frames + 1):
            t = (f - 1) / fps
            jump = abs(math.sin(math.pi * fr * t + ph)) * amp
            o.location.z = z0 + jump
            o.keyframe_insert("location", index=2, frame=f)
            if kind == "shout":
                lean = -0.22 * (0.5 + 0.5 * math.sin(2 * math.pi * 1.3 * t + ph))
                o.rotation_euler.x = lean
                o.keyframe_insert("rotation_euler", index=0, frame=f)
            for k, arm in enumerate(arms):
                side = 1 if k == 0 else -1            # +1 = bras droit (x > 0)
                if kind == "cheer":                   # bras en V qui s'agitent
                    wv = 0.4 * math.sin(2 * math.pi * 2.0 * t + ph + k * 1.9)
                    arm.rotation_euler = (0, -side * (2.55 + wv), 0)
                elif kind == "clap":                  # mains devant, qui se rejoignent
                    clap = 0.5 + 0.5 * math.sin(2 * math.pi * 2.7 * t + ph)
                    arm.rotation_euler = (1.35, 0, side * (0.08 + 0.30 * clap))
                else:                                 # porte-voix devant la bouche
                    wv = 0.08 * math.sin(2 * math.pi * 1.3 * t + ph)
                    arm.rotation_euler = (2.45 + wv, 0, side * 0.42)
                arm.keyframe_insert("rotation_euler", frame=f)
        o.location.z = z0
        o.rotation_euler.x = 0
    pref.keyframe_new_interpolation_type = old
    return kinds



# ------------------------------------------------------- humain simple + rig
# Squelette minimal (rotations en coordonnées armature, le corps regarde +Y).
BONES = [  # (nom, parent, tête, queue)
    ("hips", None, (0, 0, 0.95), (0, 0, 1.05)),
    ("spine", "hips", (0, 0, 1.05), (0, 0, 1.25)),
    ("chest", "spine", (0, 0, 1.25), (0, 0, 1.45)),
    ("neck", "chest", (0, 0, 1.45), (0, 0, 1.55)),
    ("head", "neck", (0, 0, 1.55), (0, 0, 1.76)),
] + [b for sd, sx in (("L", -1), ("R", 1)) for b in (
    (f"upper_arm.{sd}", "chest", (sx * 0.21, 0, 1.42), (sx * 0.21, 0, 1.15)),
    (f"forearm.{sd}", f"upper_arm.{sd}", (sx * 0.21, 0, 1.15), (sx * 0.21, 0, 0.90)),
    (f"hand.{sd}", f"forearm.{sd}", (sx * 0.21, 0, 0.90), (sx * 0.21, 0, 0.78)),
    (f"thigh.{sd}", "hips", (sx * 0.10, 0, 0.95), (sx * 0.10, 0, 0.52)),
    (f"shin.{sd}", f"thigh.{sd}", (sx * 0.10, 0, 0.52), (sx * 0.10, 0, 0.10)),
    (f"foot.{sd}", f"shin.{sd}", (sx * 0.10, 0, 0.10), (sx * 0.10, 0.16, 0.04)),
)]
BONE_IDX = {b[0]: i for i, b in enumerate(BONES)}
_human = {}                      # cache : armature, maillons par chemise, actions


def human_armature():
    if "arm" in _human:
        return _human["arm"]
    ad = bpy.data.armatures.new("HumanRig")
    tmp = bpy.data.objects.new("HumanRig_template", ad)
    bpy.context.scene.collection.objects.link(tmp)
    bpy.context.view_layer.objects.active = tmp
    bpy.ops.object.mode_set(mode="EDIT")
    for name, parent, head, tail in BONES:
        b = ad.edit_bones.new(name)
        b.head, b.tail = head, tail
        if parent:
            b.parent = ad.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    tmp.hide_render = tmp.hide_viewport = True
    _human["arm"] = ad
    _human["tmp"] = tmp
    return ad


def human_mesh(shirt_i):
    """Corps low-poly en pièces rigides pondérées à 100 % sur un os."""
    key = ("mesh", shirt_i)
    if key in _human:
        return _human[key]
    bm = bmesh.new()
    dl = bm.verts.layers.deform.verify()
    mats = [mat(f"HShirt{shirt_i}", PALETTE_SHIRTS[shirt_i], 0.85),
            mat("HSkin", (0.80, 0.60, 0.48), 0.7),
            mat("HPants", (0.12, 0.14, 0.22), 0.9),
            mat("HShoes", (0.08, 0.08, 0.08), 0.8)]

    def w(verts, bone):
        for v in verts:
            v[dl][BONE_IDX[bone]] = 1.0

    w(add_box(bm, (0.32, 0.20, 0.17), (0, 0, 1.00), 2), "hips")
    w(add_box(bm, (0.32, 0.19, 0.22), (0, 0, 1.16), 0), "spine")
    w(add_box(bm, (0.38, 0.21, 0.26), (0, 0, 1.37), 0), "chest")
    w(add_cyl(bm, 0.055, 0.055, 0.12, (0, 0, 1.52), 1, seg=8), "neck")
    w(add_sphere(bm, 0.115, (0, 0.01, 1.66), 1, (1, 1.05, 1.18)), "head")
    for sd, sx in (("L", -1), ("R", 1)):
        w(add_sphere(bm, 0.065, (sx * 0.21, 0, 1.42), 0), f"upper_arm.{sd}")
        w(add_limb(bm, (sx * 0.21, 0, 1.42), (sx * 0.21, 0, 1.15), 0.055, 0.047, 0), f"upper_arm.{sd}")
        w(add_sphere(bm, 0.05, (sx * 0.21, 0, 1.15), 1), f"forearm.{sd}")
        w(add_limb(bm, (sx * 0.21, 0, 1.15), (sx * 0.21, 0, 0.90), 0.046, 0.038, 1), f"forearm.{sd}")
        w(add_sphere(bm, 0.05, (sx * 0.21, 0, 0.84), 1, (1, 1, 1.2)), f"hand.{sd}")
        w(add_sphere(bm, 0.085, (sx * 0.10, 0, 0.95), 2), f"thigh.{sd}")
        w(add_limb(bm, (sx * 0.10, 0, 0.95), (sx * 0.10, 0, 0.52), 0.082, 0.062, 2), f"thigh.{sd}")
        w(add_sphere(bm, 0.062, (sx * 0.10, 0, 0.52), 2), f"shin.{sd}")
        w(add_limb(bm, (sx * 0.10, 0, 0.52), (sx * 0.10, 0, 0.10), 0.06, 0.045, 2), f"shin.{sd}")
        w(add_box(bm, (0.10, 0.25, 0.07), (sx * 0.10, 0.06, 0.045), 3), f"foot.{sd}")
    me = finish(f"HumanBody_{shirt_i}", bm, mats)
    _human[key] = me
    return me


def human_instance(name, coll, shirt_i, loc, yaw, scale=1.0):
    """Armature + corps (liés à la même donnée), prêts à recevoir une action."""
    ad = human_armature()
    me = human_mesh(shirt_i)
    arm = bpy.data.objects.new(name, ad)
    coll.objects.link(arm)
    arm.location, arm.rotation_euler, arm.scale = loc, (0, 0, yaw), (scale,) * 3
    body = bpy.data.objects.new(name + "_body", me)
    coll.objects.link(body)
    body.parent = arm
    for b in BONES:
        body.vertex_groups.new(name=b[0])
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    return arm


def _rx(a): return Matrix.Rotation(a, 3, "X")
def _ry(a): return Matrix.Rotation(a, 3, "Y")
def _rz(a): return Matrix.Rotation(a, 3, "Z")


def _pose(tmp, bone, rot=None, loc=None, frame=1):
    """Applique une rotation / translation exprimée dans l'espace armature."""
    pb = tmp.pose.bones[bone]
    pb.rotation_mode = "QUATERNION"
    r = pb.bone.matrix_local.to_3x3()
    pb.rotation_quaternion = (r.transposed() @ (rot or Matrix.Identity(3)) @ r).to_quaternion()
    pb.keyframe_insert("rotation_quaternion", frame=frame)
    if loc is not None:
        pb.location = r.transposed() @ Vector(loc)
        pb.keyframe_insert("location", frame=frame)


def _new_action(name):
    return bpy.data.actions.new(name)


def human_action(kind, seated, frames, fps, rng):
    """Cycle d'animation de joie : clap / cheer / shout (+ pose assise)."""
    tmp = human_armature() and _human["tmp"]
    act = _new_action(f"H_{kind}_{'sit' if seated else 'stand'}")
    ad = tmp.animation_data_create()
    ad.action = act
    ph = rng.uniform(0, 6.28)
    fr = rng.uniform(1.6, 2.3)                       # sauts / s
    amp = rng.uniform(0.02, 0.05) if seated else rng.uniform(0.12, 0.30)
    if kind == "clap":
        amp *= 0.4
    for f in range(1, frames + 1):
        t = (f - 1) / fps
        j = abs(math.sin(math.pi * fr * t + ph))
        jump = j * amp
        crouch = (1 - j) if not seated else 0.0
        hips_z = -0.85 if seated else (jump - 0.07 * crouch)
        _pose(tmp, "hips", loc=(0, 0, hips_z), frame=f)
        if seated:
            for sd in ("L", "R"):
                _pose(tmp, f"thigh.{sd}", _rx(1.45), frame=f)
                _pose(tmp, f"foot.{sd}", _rx(-0.3), frame=f)
        else:
            for sd in ("L", "R"):
                _pose(tmp, f"thigh.{sd}", _rx(0.30 * crouch), frame=f)
                _pose(tmp, f"shin.{sd}", _rx(-0.6 * crouch), frame=f)
        lean = 0.5 + 0.5 * math.sin(2 * math.pi * 1.3 * t + ph)
        if kind == "shout":
            _pose(tmp, "chest", _rx(0.20 * lean), frame=f)
            _pose(tmp, "head", _rx(0.28 * lean), frame=f)
        for sd, sx in (("L", -1), ("R", 1)):
            k = 0 if sx > 0 else 1
            if kind == "cheer":                      # bras en V qui s'agitent
                wv = 0.40 * math.sin(2 * math.pi * 2.0 * t + ph + k * 1.9)
                _pose(tmp, f"upper_arm.{sd}", _ry(-sx * (2.45 + wv)), frame=f)
                _pose(tmp, f"forearm.{sd}", _rx(0.25 + 0.25 * math.sin(6.0 * t + ph)), frame=f)
            elif kind == "clap":                     # mains devant qui se rejoignent
                clap = 0.5 + 0.5 * math.sin(2 * math.pi * 2.7 * t + ph)
                _pose(tmp, f"upper_arm.{sd}", _rz(sx * (0.10 + 0.22 * clap)) @ _rx(0.95), frame=f)
                _pose(tmp, f"forearm.{sd}", _rx(0.75), frame=f)
            else:                                    # porte-voix devant la bouche
                wv = 0.08 * math.sin(2 * math.pi * 1.3 * t + ph)
                _pose(tmp, f"upper_arm.{sd}", _rz(sx * 0.30) @ _rx(0.80 + wv), frame=f)
                _pose(tmp, f"forearm.{sd}", _rx(1.95), frame=f)
    ad.action = None
    return act


def assign_action(obj, act):
    ad = obj.animation_data_create()
    ad.action = act
    slots = getattr(act, "slots", None)
    if slots and hasattr(ad, "action_slot"):         # Blender 4.4+ : actions à slots
        try:
            ad.action_slot = slots[0]
        except Exception:
            pass


def animate_crowd_human(crowd, frames, fps, seed, variants=6):
    """Remplace l'animation bâton : même répartition clap / cheer / shout, avec
    un petit nombre de cycles partagés (6 variantes) entre tous les figurants."""
    rng = random.Random(seed + 303)
    pool = {}
    for kind, seated in (("clap", False), ("cheer", False), ("shout", False),
                         ("clap", True), ("cheer", True)):
        pool[(kind, seated)] = [human_action(kind, seated, frames, fps, rng)
                                for _ in range(variants)]
    counts = {"clap": 0, "cheer": 0, "shout": 0}
    for o, _arms, seated, x, y in crowd.people:
        r = rng.random()
        kind = "clap" if r < 0.34 else ("cheer" if r < 0.80 else "shout")
        if seated and kind == "shout":
            kind = "cheer"
        counts[kind] += 1
        assign_action(o, rng.choice(pool[(kind, seated)]))
    return counts


def apply_camera_fit(cam, fit, frames):
    """Caméra calée sur une vidéo : pose de la 1re et de la dernière image
    (x, y, z, cap, inclinaison) + focale, interpolées linéairement."""
    cam.data.lens = fit["f"]
    cam.data.sensor_fit = "HORIZONTAL"
    pref = bpy.context.preferences.edit
    old = pref.keyframe_new_interpolation_type
    pref.keyframe_new_interpolation_type = "LINEAR"
    if "cams" in fit:                        # calage image par image (x, y, z, cap, inclinaison, focale)
        cam.data.sensor_fit = "HORIZONTAL"
        for i, (x, y, z, yaw, pit, f) in enumerate(fit["cams"]):
            fwd = Vector((math.cos(pit) * math.cos(yaw), math.cos(pit) * math.sin(yaw), -math.sin(pit)))
            cam.location = (x, y, z)
            cam.rotation_euler = fwd.to_track_quat("-Z", "Y").to_euler()
            cam.data.lens = f
            cam.keyframe_insert("location", frame=i + 1)
            cam.keyframe_insert("rotation_euler", frame=i + 1)
            cam.data.keyframe_insert("lens", frame=i + 1)
        pref.keyframe_new_interpolation_type = old
        return
    for frame, c in ((1, fit["cam1"]), (max(frames, 2), fit["camN"])):
        x, y, z, yaw, pit = c
        fwd = Vector((math.cos(pit) * math.cos(yaw), math.cos(pit) * math.sin(yaw), -math.sin(pit)))
        cam.location = (x, y, z)
        cam.rotation_euler = fwd.to_track_quat("-Z", "Y").to_euler()
        cam.keyframe_insert("location", frame=frame)
        cam.keyframe_insert("rotation_euler", frame=frame)
    pref.keyframe_new_interpolation_type = old


def build_hero(coll, frames, fps, rng):
    """Un humain riggé au centre du monde (0, 0)."""
    z = terrain_h(0.0, 0.0)
    hero = human_instance("Human_Center", coll, 3, (0.0, 0.0, z), math.radians(-20))
    if frames > 0:
        assign_action(hero, human_action("cheer", False, frames, fps, rng))
    return hero

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
    ap.add_argument("--riders", type=int, default=62,
                    help="taille du peloton principal (0 = aucun)")
    ap.add_argument("--group2", type=int, default=16,
                    help="taille du 2e groupe, en retard derrière le peloton (0 = aucun)")
    ap.add_argument("--radius", type=float, help="rayon de l'épingle (m)")
    ap.add_argument("--camera-fit", help="JSON de calage caméra sur une vidéo (cam1, camN, f)")
    ap.add_argument("--no-hero", action="store_true", help="pas d'humain central")
    ap.add_argument("--transparent", action="store_true",
                    help="fond transparent + sol en shadow catcher (pour incruster la foule sur une vidéo)")
    ap.add_argument("--hero", action="store_true",
                    help="décor + un seul humain riggé au centre du monde (0, 0)")
    ap.add_argument("--human", action="store_true",
                    help="foule d'humains riggés (remplace les bonhommes bâtons) qui applaudissent, sautent, crient")
    ap.add_argument("--stick", action="store_true",
                    help="foule en bonhommes bâtons qui applaudissent, sautent et crient (sans les coureurs)")
    ap.add_argument("--set-only", action="store_true",
                    help="décor seul : terrain, route, ciel, caméra (sans foule, coureurs ni accessoires)")
    ap.add_argument("--anim", action="store_true", help="rend toute l'animation")
    ap.add_argument("--res", type=int, nargs=2, default=(1320, 866))
    a = ap.parse_args(argv)

    global R, Q, LOWER_X1, FIT_GROUND
    if a.radius:
        R = a.radius
        Q = math.pi * R / 2
    fit = None
    if a.camera_fit:
        import json
        with open(a.camera_fit) as fh:
            fit = json.load(fh)
        R = fit.get("R", R)
        Q = math.pi * R / 2
        LOWER_X1 = 100.0
        FIT_GROUND = True
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene

    def coll(name):
        c = bpy.data.collections.new(name)
        sc.collection.children.link(c)
        return c
    c_terrain, c_road, c_crowd, c_props, c_peloton, c_light, c_cam = (
        coll("Terrain"), coll("Road"), coll("Crowd"), coll("Props"), coll("Peloton"),
        coll("Lighting"), coll("Camera"))

    rng = random.Random(a.seed)
    noise.seed_set(a.seed)
    build_terrain(c_terrain)
    build_road(c_road)
    crowd, n = None, 0
    if a.hero:
        a.set_only = True
    if not a.set_only:
        crowd = (build_crowd_fit if fit else build_crowd)(
            c_crowd, rng, a.crowd_scale, stick=a.stick, human=a.human)
        n = crowd.count
        if not fit:                              # incrustation : pas d'accessoires
            build_umbrellas(c_props)
            build_bikes(c_props)
            build_posts(c_props)
            build_flag(c_props)
    setup_world(c_light)
    setup_camera(c_cam, a.reference)
    setup_render(*a.res)
    if fit:
        apply_camera_fit(sc.camera, fit, a.frames)
    elif a.frames > 0:
        animate_camera(a.frames)
    if a.transparent:
        sc.render.film_transparent = True
        sc.render.image_settings.color_mode = "RGBA"
        for ob in list(c_terrain.objects) + list(c_road.objects):
            ob.is_shadow_catcher = True
    if (a.hero or a.human) and not a.no_hero:
        build_hero(c_props, a.frames, sc.render.fps, random.Random(a.seed + 7))
    if a.human and not a.set_only and a.frames > 0:
        print('Foule humains :', animate_crowd_human(crowd, a.frames, sc.render.fps, a.seed))
    elif a.stick and not a.set_only and a.frames > 0:
        print('Foule bâtons :', animate_crowd_stick(crowd, a.frames, sc.render.fps, a.seed))
    elif a.riders > 0 and not a.set_only:
        nr, tail_a = build_peloton(c_peloton, rng, a.frames, n=a.riders, n2=a.group2)
        print(f"Peloton : {nr} coureurs")
        if a.frames > 0:
            na = animate_crowd(crowd, a.frames, sc.render.fps, tail_a, a.seed)
            print(f"Spectateurs animés : {na}")
    print(f"Layout prêt : {n} spectateurs")

    if a.save:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.save))
    if a.render:
        sc.render.filepath = os.path.abspath(a.render)
        bpy.ops.render.render(animation=a.anim, write_still=not a.anim)


if __name__ == "__main__":
    main()
