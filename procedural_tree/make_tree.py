"""Arbre conifère procédural avec bpy.

Méthode :
  1. Curves : tronc -> big branches -> medium branches -> small branches
  2. Curves -> géométrie (bevel avec radius par point, puis conversion en mesh)
  3. Une feuille unique, à plat, au centre du monde (0,0,0)
  4. Geometry Nodes : la feuille est instanciée le long des small branches
  5. Rendu "grey shader" sur sol plat avec un mannequin humain de 1.75 m (échelle)

Usage :  python make_tree.py [--out tree.png] [--res 1500 2000] [--samples 64] [--blend tree.blend]
"""
import sys
import math
import random
import argparse

import bpy
import bmesh
from mathutils import Vector, Matrix

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
ap = argparse.ArgumentParser()
ap.add_argument("--out", default="tree_grey.png")
ap.add_argument("--blend", default="")
ap.add_argument("--res", type=int, nargs=2, default=[1500, 2000])
ap.add_argument("--samples", type=int, default=64)
ap.add_argument("--seed", type=int, default=7)
args = ap.parse_args(argv)

rng = random.Random(args.seed)

# ----------------------------------------------------------------------------
# Paramètres de l'arbre (mètres)
# ----------------------------------------------------------------------------
H = 12.0              # hauteur totale
CROWN_BASE = 2.4      # début du feuillage (le tronc est nu en dessous)
R_MAX = 3.0           # rayon max de la couronne
TRUNK_R0 = 0.115       # rayon du tronc à la base
Z_UP = Vector((0, 0, 1))


# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------
def smoothstep(a, b, x):
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


class Branch:
    """Polyligne + radius par point ; permet d'échantillonner pos/tangente/radius."""

    def __init__(self, pts, rad):
        self.pts, self.rad = pts, rad
        self.length = sum((pts[i + 1] - pts[i]).length for i in range(len(pts) - 1))

    def at(self, t):
        n = len(self.pts) - 1
        f = max(0.0, min(0.9999, t)) * n
        i = int(f)
        k = f - i
        p = self.pts[i].lerp(self.pts[i + 1], k)
        tan = (self.pts[i + 1] - self.pts[i]).normalized()
        r = self.rad[i] * (1 - k) + self.rad[i + 1] * k
        return p, tan, r


def grow(origin, direction, length, r0, r1, nseg, droop=0.0, up=0.0, wobble=0.04):
    """Fait pousser une branche : gravité au début, redressement vers le bout."""
    d = direction.normalized()
    p = origin.copy()
    ds = length / nseg
    pts, rad = [p.copy()], [r0]
    for i in range(1, nseg + 1):
        t = i / nseg
        d = d + Vector((0, 0, -droop * (1 - t) + up * t)) * 0.3
        d += Vector((rng.gauss(0, wobble), rng.gauss(0, wobble), rng.gauss(0, wobble)))
        d.normalize()
        p = p + d * ds
        pts.append(p.copy())
        rad.append(r1 + (r0 - r1) * (1 - t) ** 0.9)
    return Branch(pts, rad)


def child_dir(tan, side, angle_deg, roll_deg):
    """Direction d'un enfant : écart `angle` autour de la tangente, côté `side` (+1/-1)."""
    s = tan.cross(Z_UP)
    if s.length < 1e-3:
        s = Vector((1, 0, 0))
    s.normalize()
    u = tan.cross(s).normalized()
    a, r = math.radians(angle_deg), math.radians(roll_deg)
    lateral = (s * math.cos(r) + u * math.sin(r)) * side
    return (tan * math.cos(a) + lateral * math.sin(a)).normalized()


def make_curve_object(name, branches, bevel_res, poly=False, bevel=True):
    """Toutes les branches d'un niveau dans UN seul objet curve (1 spline / branche)."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.resolution_u = 2
    if bevel:
        cu.bevel_depth = 1.0          # le radius des points pilote l'épaisseur
        cu.bevel_resolution = bevel_res
        cu.use_fill_caps = True
    for br in branches:
        sp = cu.splines.new("POLY" if poly else "NURBS")
        sp.points.add(len(br.pts) - 1)
        for i, (p, r) in enumerate(zip(br.pts, br.rad)):
            sp.points[i].co = (p.x, p.y, p.z, 1.0)
            sp.points[i].radius = r
        if not poly:
            sp.order_u = 4
            sp.use_endpoint_u = True
    ob = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def curve_to_mesh_object(src, name, mat):
    """Curve biseautée -> vrai mesh (géométrie)."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(src.evaluated_get(dg))
    me.name = name
    ob = bpy.data.objects.new(name, me)
    ob.data.materials.append(mat)
    bpy.context.scene.collection.objects.link(ob)
    for p in me.polygons:
        p.use_smooth = True
    return ob


# ----------------------------------------------------------------------------
# Scène vierge + matériaux
# ----------------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def grey_material(name, value):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (value, value, value, 1)
    bsdf.inputs["Roughness"].default_value = 1.0
    return m


mat_grey = grey_material("Grey", 0.55)
mat_ground = grey_material("GreyGround", 0.35)

# ----------------------------------------------------------------------------
# 1. CURVES : tronc / big / medium / small
# ----------------------------------------------------------------------------
# Tronc : légère oscillation, conicité marquée, petit évasement à la base
trunk_pts, trunk_rad = [], []
NT = 24
sway_x, sway_y = rng.uniform(-1, 1), rng.uniform(-1, 1)
for i in range(NT + 1):
    t = i / NT
    z = H * t
    off = 0.12 * math.sin(t * 3.0 + 0.5)
    trunk_pts.append(Vector((off * sway_x * t, off * sway_y * t, z)))
    flare = 1 + 0.18 * math.exp(-z / 0.3)
    trunk_rad.append(TRUNK_R0 * flare * (1 - t) ** 0.95 + 0.008)
trunk = Branch(trunk_pts, trunk_rad)

# Big branches : verticilles espacés régulièrement, longueur suivant un profil conique
big = []
h = CROWN_BASE
golden = math.radians(137.5)
phase = 0.0
while h < H - 0.5:
    u = (h - CROWN_BASE) / (H - CROWN_BASE)
    # profil : plus large vers 25 % de la couronne puis cône régulier jusqu'à la pointe
    prof = (1 - u) ** 1.1 * (0.6 + 0.4 * smoothstep(0.0, 0.2, u))
    n_w = 5 if u < 0.7 else 4
    phase += golden
    for k in range(n_w):
        ang = phase + k * 2 * math.pi / n_w + rng.uniform(-0.25, 0.25)
        L = R_MAX * prof * rng.uniform(0.85, 1.1) + 0.12
        z = h + rng.uniform(-0.12, 0.12)
        tp, tt, tr = trunk.at(z / H)
        elev = math.radians(-14 + 52 * u + rng.uniform(-6, 6))     # bas : retombant, haut : relevé
        d = Vector((math.cos(ang) * math.cos(elev), math.sin(ang) * math.cos(elev), math.sin(elev)))
        r0 = min(tr * 0.55, 0.012 + 0.016 * L)
        b = grow(tp, d, L, r0, 0.004, 9, droop=0.55 * (1 - u) + 0.1, up=0.35, wobble=0.03)
        big.append(b)
    h += 0.42 - 0.16 * u

# Medium branches : le long des big, alternées gauche/droite
medium = []
for b in big:
    t = 0.16
    side = rng.choice([-1, 1])
    while t < 0.97:
        p, tan, r = b.at(t)
        L = b.length * (0.5 * (1 - t) ** 0.9 + 0.06) * rng.uniform(0.75, 1.15)
        if L > 0.18:
            d = child_dir(tan, side, rng.uniform(48, 68), rng.uniform(-25, 25))
            medium.append(grow(p, d, L, max(r * 0.5, 0.004), 0.0025, 5,
                               droop=0.25, up=0.15, wobble=0.03))
        side = -side
        t += rng.uniform(0.07, 0.10) * (3.0 / max(b.length, 1.0)) ** 0.5

# Small branches : portent les feuilles
small = []
for b in medium:
    t = 0.08
    side = rng.choice([-1, 1])
    while t < 0.98:
        p, tan, r = b.at(t)
        L = rng.uniform(0.22, 0.36) * (1 - 0.4 * t) * (0.45 + 0.55 * min(1.0, b.length / 1.2))
        d = child_dir(tan, side, rng.uniform(50, 75), rng.uniform(-35, 35))
        small.append(grow(p, d, L, max(r * 0.55, 0.0025), 0.0012, 3,
                          droop=0.15, up=0.1, wobble=0.04))
        side = -side
        t += rng.uniform(0.12, 0.16) / max(0.45, b.length) * 0.55 + 0.06

print(f"trunk 1 | big {len(big)} | medium {len(medium)} | small {len(small)}")

# ----------------------------------------------------------------------------
# 2. CURVES -> GÉOMÉTRIE (bevel piloté par les radius)
# ----------------------------------------------------------------------------
c_trunk = make_curve_object("c_Trunk", [trunk], 4)
c_big = make_curve_object("c_BigBranches", big, 2)
c_med = make_curve_object("c_MediumBranches", medium, 1)
c_small = make_curve_object("c_SmallBranches", small, 1, poly=True)
bpy.context.view_layer.update()

g_trunk = curve_to_mesh_object(c_trunk, "Trunk", mat_grey)
g_big = curve_to_mesh_object(c_big, "BigBranches", mat_grey)
g_med = curve_to_mesh_object(c_med, "MediumBranches", mat_grey)
g_small = curve_to_mesh_object(c_small, "SmallBranches", mat_grey)

# Les curves d'origine servent de guides : on retire leur épaisseur (rien à rendre)
# mais elles restent disponibles pour le Geometry Nodes.
for c in (c_trunk, c_big, c_med):
    bpy.data.objects.remove(c)
c_small.data.bevel_depth = 0.0

# ----------------------------------------------------------------------------
# 3. FEUILLE UNIQUE, À PLAT, AU CENTRE DU MONDE
#    Rameau d'aiguilles : pointe vers +X, posé dans le plan XY, légèrement dentelé
# ----------------------------------------------------------------------------
LEAF_L, LEAF_W = 0.17, 0.045
LEAF_SCALE_MIN, LEAF_SCALE_MAX = 0.45, 1.7     # random de taille par feuille
stations = 10
verts, faces = [], []
for i in range(stations + 1):
    x = LEAF_L * i / stations
    w = LEAF_W * 0.5 * math.sin(math.pi * (i / stations) ** 0.8) ** 0.75
    if i % 2:
        w *= 1.25                                    # dentelure
    if i == stations:
        w = 0.0
    verts += [(x, w, 0.0), (x, -w, 0.0)]
for i in range(stations):
    a = 2 * i
    faces.append((a, a + 2, a + 3, a + 1))
leaf_me = bpy.data.meshes.new("Leaf")
leaf_me.from_pydata(verts, [], faces)
leaf_me.update()
leaf_me.materials.append(mat_grey)
for p in leaf_me.polygons:
    p.use_smooth = False
leaf = bpy.data.objects.new("Leaf", leaf_me)
leaf.location = (0, 0, 0)
bpy.context.scene.collection.objects.link(leaf)

# ----------------------------------------------------------------------------
# 4. GEOMETRY NODES : instancier la feuille le long des small branches
# ----------------------------------------------------------------------------
ng = bpy.data.node_groups.new("LeafInstancer", "GeometryNodeTree")
ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
nodes, links = ng.nodes, ng.links


def node(type_, x, **props):
    n = nodes.new(type_)
    n.location = (x, 0)
    for k, v in props.items():
        setattr(n, k, v)
    return n


def first_enabled(n, name):
    return next(s for s in n.inputs if s.name == name and s.enabled)


n_out = node("NodeGroupOutput", 1400)
n_curve = node("GeometryNodeObjectInfo", 0, transform_space="ORIGINAL")
n_curve.inputs["Object"].default_value = c_small
n_leaf = node("GeometryNodeObjectInfo", 0, transform_space="ORIGINAL")
n_leaf.inputs["Object"].default_value = leaf
n_pts = node("GeometryNodeCurveToPoints", 250, mode="LENGTH")
n_pts.inputs["Length"].default_value = 0.03

# rotations / échelles aléatoires par instance
n_rrot = node("FunctionNodeRandomValue", 250, data_type="FLOAT_VECTOR")
first_enabled(n_rrot, "Min").default_value = (-math.pi, -0.35, -1.0)
first_enabled(n_rrot, "Max").default_value = (math.pi, 0.35, 1.0)
first_enabled(n_rrot, "Seed").default_value = 3
n_rscl = node("FunctionNodeRandomValue", 250, data_type="FLOAT")
first_enabled(n_rscl, "Min").default_value = LEAF_SCALE_MIN
first_enabled(n_rscl, "Max").default_value = LEAF_SCALE_MAX
first_enabled(n_rscl, "Seed").default_value = 11

n_inst = node("GeometryNodeInstanceOnPoints", 600)
n_rot = node("GeometryNodeRotateInstances", 900)
n_rot.inputs["Local Space"].default_value = True

links.new(n_curve.outputs["Geometry"], n_pts.inputs["Curve"])
links.new(n_pts.outputs["Points"], n_inst.inputs["Points"])
links.new(n_leaf.outputs["Geometry"], n_inst.inputs["Instance"])
links.new(n_pts.outputs["Rotation"], n_inst.inputs["Rotation"])
links.new(n_rscl.outputs[1] if False else next(s for s in n_rscl.outputs if s.name == "Value" and s.enabled),
          n_inst.inputs["Scale"])
links.new(n_inst.outputs["Instances"], n_rot.inputs["Instances"])
links.new(next(s for s in n_rrot.outputs if s.name == "Value" and s.enabled), n_rot.inputs["Rotation"])
links.new(n_rot.outputs["Instances"], n_out.inputs["Geometry"])

leaves = bpy.data.objects.new("Leaves", bpy.data.meshes.new("LeavesHost"))
bpy.context.scene.collection.objects.link(leaves)
mod = leaves.modifiers.new("LeafInstancer", "NODES")
mod.node_group = ng
leaf.hide_render = True          # la feuille "source" ne s'affiche pas, seules les instances

# ----------------------------------------------------------------------------
# 5. Sol, mannequin d'échelle, lumière, caméra
# ----------------------------------------------------------------------------
bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, 0))
ground = bpy.context.object
ground.name = "Ground"
ground.data.materials.append(mat_ground)


def cone_between(p0, p1, r0, r1, mat, name):
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    bpy.ops.mesh.primitive_cone_add(vertices=24, radius1=r0, radius2=r1, depth=d.length,
                                    location=(p0 + p1) / 2)
    o = bpy.context.object
    o.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    o.name = name
    return o


def sphere(loc, scale, name):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=loc)
    o = bpy.context.object
    o.scale = scale
    o.name = name
    return o


HX, HY = -1.9, -2.2            # position du mannequin
parts = []
for s in (-1, 1):
    x = s * 0.095
    parts.append(cone_between((x, 0, 0.92), (x * 1.05, 0, 0.50), 0.088, 0.062, mat_grey, "thigh"))
    parts.append(cone_between((x * 1.05, 0, 0.50), (x * 1.1, 0, 0.09), 0.058, 0.040, mat_grey, "calf"))
    parts.append(sphere((x * 1.05, 0, 0.50), (0.062, 0.062, 0.062), "knee"))
    parts.append(sphere((x * 1.1, -0.045, 0.045), (0.05, 0.13, 0.045), "foot"))
    ax = s * 0.215
    parts.append(cone_between((ax, 0, 1.43), (ax * 1.2, 0, 1.15), 0.050, 0.040, mat_grey, "upper_arm"))
    parts.append(cone_between((ax * 1.2, 0, 1.15), (ax * 1.28, 0, 0.90), 0.040, 0.030, mat_grey, "forearm"))
    parts.append(sphere((ax * 1.28, 0, 0.84), (0.035, 0.025, 0.07), "hand"))
    parts.append(sphere((ax, 0, 1.43), (0.055, 0.055, 0.055), "shoulder"))
parts.append(sphere((0, 0, 0.97), (0.17, 0.105, 0.13), "pelvis"))
parts.append(sphere((0, 0, 1.22), (0.19, 0.11, 0.29), "torso"))
parts.append(cone_between((0, 0, 1.50), (0, 0, 1.58), 0.05, 0.045, mat_grey, "neck"))
parts.append(sphere((0, 0, 1.66), (0.085, 0.10, 0.11), "head"))   # sommet de la tête = 1.77 m
for p in bpy.context.scene.objects:
    p.select_set(False)
for p in parts:
    p.select_set(True)
    p.data.materials.clear()
    p.data.materials.append(mat_grey)
bpy.context.view_layer.objects.active = parts[0]
bpy.ops.object.join()
human = bpy.context.object
human.name = "Human_1.75m"
human.data.transform(human.matrix_world)      # pieds à z=0, origine au monde
human.matrix_world = Matrix.Identity(4)
bpy.ops.object.shade_smooth()

human.location = (HX, HY, 0)
human.rotation_euler = (0, 0, math.radians(20))

# Éclairage : ciel gris uniforme + soleil doux
world = bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.75, 0.75, 0.75, 1)
bg.inputs["Strength"].default_value = 0.9

sun_d = bpy.data.lights.new("Sun", "SUN")
sun_d.energy = 3.2
sun_d.angle = math.radians(4)
sun = bpy.data.objects.new("Sun", sun_d)
sun.rotation_euler = (math.radians(52), math.radians(8), math.radians(-38))
scene.collection.objects.link(sun)

cam_d = bpy.data.cameras.new("Cam")
cam_d.lens = 90
cam = bpy.data.objects.new("Cam", cam_d)
scene.collection.objects.link(cam)
cam.location = (0, -36, 6.1)
cam.rotation_euler = (math.radians(90), 0, 0)
scene.camera = cam

# Rendu
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = args.samples
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 4
scene.render.resolution_x, scene.render.resolution_y = args.res
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "Standard"
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = args.out
scene.render.threads_mode = "AUTO"

if args.blend:
    bpy.ops.wm.save_as_mainfile(filepath=args.blend)
bpy.ops.render.render(write_still=True)
print("rendered ->", args.out)
