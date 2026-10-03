"""Helpers bmesh / scene pour la grue portuaire. Unites : metres. X = droite image, Y = profondeur, Z = haut."""
import bpy, bmesh, math
from mathutils import Vector, Matrix

S = 0.055            # m par pixel de reference.jpg (550x550)
OX, GZ = 220, 530    # pixel de l'axe de pivot / pixel du sol
Y = Vector((0, 1, 0))

def P(px, py, y=0.0):
    """pixel de la photo -> point 3D (plan XZ, profondeur y)"""
    return Vector(((px - OX) * S, y, (GZ - py) * S))

# ------------------------------------------------------------------ scene
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for c in list(bpy.data.collections): bpy.data.collections.remove(c)
    sc = bpy.context.scene
    sc.unit_settings.system = 'METRIC'
    return sc

def collection(name, parent=None):
    c = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(c)
    return c

_mats = {}
def material(name, rgb, rough=0.6, metal=0.0):
    if name in _mats: return _mats[name]
    m = bpy.data.materials.new(name); m.diffuse_color = (*rgb, 1)
    m.roughness = rough; m.metallic = metal
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1); b.inputs["Roughness"].default_value = rough; b.inputs["Metallic"].default_value = metal
    _mats[name] = m; return m

def make_obj(name, bm, mat, coll, parent=None, loc=None):
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free(); me.update()
    ob = bpy.data.objects.new(name, me); coll.objects.link(ob)
    if mat: me.materials.append(mat)
    if parent: ob.parent = parent
    if loc is not None: ob.location = loc
    return ob

def empty(name, coll, loc=(0, 0, 0), parent=None):
    e = bpy.data.objects.new(name, None); e.empty_display_type = 'PLAIN_AXES'; e.empty_display_size = 1.5
    coll.objects.link(e); e.location = loc
    if parent: e.parent = parent
    return e

# ------------------------------------------------------------------ primitives (ajoutees a un bmesh)
def add_box(bm, c, size, rot=None):
    M = Matrix.Translation(Vector(c)) @ (rot.to_4x4() if rot else Matrix.Identity(4)) @ Matrix.Diagonal(Vector((*size, 1)))
    bmesh.ops.create_cube(bm, size=1.0, matrix=M)

def axis_rot(axis):
    return {'Z': Matrix.Identity(3), 'X': Matrix.Rotation(math.radians(90), 3, 'Y'), 'Y': Matrix.Rotation(math.radians(-90), 3, 'X')}[axis]

def add_cyl(bm, c, r, depth, axis='Z', segs=32, r2=None):
    M = Matrix.Translation(Vector(c)) @ axis_rot(axis).to_4x4()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r, radius2=r if r2 is None else r2, depth=depth, matrix=M)

def frame(t):
    t = t.normalized(); n = t.cross(Y).normalized(); return t, n

def ring(p, t, w, h):
    t, n = frame(t); hy, hn = Y * (w / 2), n * (h / 2)
    return [p - hy - hn, p + hy - hn, p + hy + hn, p - hy + hn]

def sweep(bm, pts, sizes, cap=True):
    """Poutre a section rectangulaire (w=Y, h=dans le plan) le long d'une polyligne, joints en onglet."""
    pts = [Vector(p) for p in pts]; rings = []
    for i, p in enumerate(pts):
        if i == 0: t = pts[1] - pts[0]; k = 1
        elif i == len(pts) - 1: t = pts[-1] - pts[-2]; k = 1
        else:
            t0, t1 = (pts[i] - pts[i - 1]).normalized(), (pts[i + 1] - pts[i]).normalized()
            t = t0 + t1; k = 1 / max(t0.dot(t.normalized()), 0.3)
        w, h = sizes[i]
        rings.append(ring(p, t, w, h * k))
    vs = [[bm.verts.new(v) for v in r] for r in rings]
    for a, b in zip(vs, vs[1:]):
        for i in range(4): bm.faces.new((a[i], a[(i + 1) % 4], b[(i + 1) % 4], b[i]))
    if cap:
        bm.faces.new(vs[0][::-1]); bm.faces.new(vs[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

def beam(bm, a, b, s0, s1=None):
    sweep(bm, [a, b], [s0, s1 or s0])

def tube(bm, a, b, r, segs=8):
    a, b = Vector(a), Vector(b); d = b - a
    if d.length < 1e-6: return
    q = d.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r, radius2=r, depth=d.length,
                          matrix=Matrix.Translation((a + b) / 2) @ q)

# ------------------------------------------------------------------ harden / soften
def harden_soften(ob, angle=30, bevel=0.0, segments=2, weighted=True):
    """Mise en forme 'hard surface' : aretes dures marquees sharp au-dela de `angle`, surfaces douces,
    bevel (limite par l'angle) + Weighted Normal pour des plans propres sans artefact d'ombrage."""
    me = ob.data; bm = bmesh.new(); bm.from_mesh(me)
    lim = math.radians(angle)
    for f in bm.faces: f.smooth = True
    for e in bm.edges:
        e.smooth = not (e.is_boundary or (len(e.link_faces) == 2 and e.calc_face_angle(0) > lim))
    bm.to_mesh(me); bm.free()
    if bevel > 0:
        m = ob.modifiers.new("Bevel", 'BEVEL'); m.width = bevel; m.segments = segments; m.limit_method = 'ANGLE'
        m.angle_limit = lim; m.harden_normals = False; m.use_clamp_overlap = True; m.miter_outer = 'MITER_ARC' if False else 'MITER_SHARP'
    if weighted:
        w = ob.modifiers.new("WeightedNormal", 'WEIGHTED_NORMAL'); w.keep_sharp = True; w.weight = 50; w.mode = 'FACE_AREA'
    return ob

# ------------------------------------------------------------------ playblast (Workbench)
def setup_playblast(sc, res=640):
    sc.render.engine = 'BLENDER_WORKBENCH'
    sc.render.resolution_x = sc.render.resolution_y = res; sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    sh = sc.display.shading
    sh.light = 'STUDIO'; sh.color_type = 'MATERIAL'; sh.show_cavity = True; sh.cavity_type = 'BOTH'
    sh.cavity_ridge_factor = 0.8; sh.cavity_valley_factor = 1.0
    sh.show_object_outline = True; sh.object_outline_color = (0, 0, 0)
    sh.show_specular_highlight = True; sh.background_type = 'WORLD'
    sc.world = bpy.data.worlds.new("W"); sc.world.color = (0.16, 0.17, 0.19)
    sc.display.render_aa = '8'
    sc.view_settings.view_transform = 'Standard'

def make_cam(name, loc, target, lens=50, ortho=None):
    cam = bpy.data.cameras.new(name); ob = bpy.data.objects.new(name, cam); bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    ob.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
    if ortho: cam.type = 'ORTHO'; cam.ortho_scale = ortho
    else: cam.lens = lens
    cam.clip_start, cam.clip_end = 0.5, 1000
    return ob

def render_to(sc, cam, path):
    sc.camera = cam; sc.render.filepath = path; bpy.ops.render.render(write_still=True)

# ------------------------------------------------------------------ profils extrudes / treillis
def prism(bm, pts, y0, y1, M=None):
    """Profil (x,z) extrude entre y0 et y1 : plaques, poutres coupees, goussets. M = transform optionnelle."""
    M = M or Matrix.Identity(4)
    f = [bm.verts.new(M @ Vector((x, y0, z))) for x, z in pts]
    b = [bm.verts.new(M @ Vector((x, y1, z))) for x, z in pts]
    n = len(pts)
    bm.faces.new(f[::-1]); bm.faces.new(b)
    for i in range(n): bm.faces.new((f[i], f[(i + 1) % n], b[(i + 1) % n], b[i]))

def path_len(pts): return sum((pts[i + 1] - pts[i]).length for i in range(len(pts) - 1))

def path_at(pts, s):
    """point + tangente a l'abscisse curviligne s (tangente lissee entre segments)"""
    acc = 0
    for i in range(len(pts) - 1):
        seg = (pts[i + 1] - pts[i]); L = seg.length
        if s <= acc + L or i == len(pts) - 2:
            u = min(max((s - acc) / L, 0), 1); p = pts[i] + seg * u
            t = seg.normalized()
            if u < .5 and i > 0: t = (t + (pts[i] - pts[i - 1]).normalized()).normalized()
            if u >= .5 and i < len(pts) - 2: t = (t + (pts[i + 2] - pts[i + 1]).normalized()).normalized()
            return p, t
        acc += L

def stations(pts, spacing):
    pts = [Vector(p) for p in pts]; L = path_len(pts); n = max(2, round(L / spacing)); return [path_at(pts, L * i / n) for i in range(n + 1)]

def truss(bm, st, h, w, r_chord=0.12, r_diag=0.065, diag=True, top_solid=None):
    """Treillis spatial 4 membrures le long de stations (p,t) ; h = hauteur dans le plan, w = largeur en Y.
    top_solid=(w,h) : la membrure haute est un caisson plein au lieu d'un tube."""
    T = [[], []]; B = [[], []]
    for p, t in st:
        t, n = frame(t)
        for k, sy in enumerate((-1, 1)):
            T[k].append(p + n * h / 2 + Y * sy * w / 2); B[k].append(p - n * h / 2 + Y * sy * w / 2)
    for k in range(2):
        for i in range(len(st) - 1):
            if top_solid is None: tube(bm, T[k][i], T[k][i + 1], r_chord)
            tube(bm, B[k][i], B[k][i + 1], r_chord)
        for i in range(len(st)):
            tube(bm, T[k][i], B[k][i], r_diag)
            if diag and i < len(st) - 1:
                if i % 2 == 0: tube(bm, B[k][i], T[k][i + 1], r_diag)
                else: tube(bm, T[k][i], B[k][i + 1], r_diag)
    for i in range(len(st)):                       # cadres transversaux
        tube(bm, T[0][i], T[1][i], r_diag); tube(bm, B[0][i], B[1][i], r_diag)
        if diag: tube(bm, T[0][i], B[1][i], r_diag)
    return T, B

# ------------------------------------------------------------------ details (etape 3)
def bool_cut(ob, cutters):
    """Coupe booleenne (exact) puis application : fenetres, rainures de panneaux. `cutters` = liste de bmesh fermes."""
    big = bmesh.new()
    for c in cutters:
        me = bpy.data.meshes.new("_c"); c.to_mesh(me); c.free(); big.from_mesh(me); bpy.data.meshes.remove(me)
    bmesh.ops.recalc_face_normals(big, faces=big.faces)
    cu = make_obj("_cutter", big, None, bpy.context.scene.collection)
    m = ob.modifiers.new("cut", 'BOOLEAN'); m.operation = 'DIFFERENCE'; m.object = cu; m.solver = 'EXACT'; m.use_self = True
    with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
        bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(cu)

def circle_pts(r, z, n=48, a0=0, a1=360, c=(0, 0)):
    return [Vector((c[0] + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), c[1] + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)), z)) for i in range(n + 1)]

def railing(bm, pts, h=1.05, post_every=1.4, r=0.025, closed=False, rails=(0.5, 1.0), up=Vector((0, 0, 1))):
    """Garde-corps le long d'une polyligne 3D : poteaux + lisses."""
    pts = [Vector(p) for p in pts]
    acc, nxt = 0.0, 0.0
    for i in range(len(pts)):
        if i > 0: acc += (pts[i] - pts[i - 1]).length
        if acc >= nxt or i in (0, len(pts) - 1):
            tube(bm, pts[i], pts[i] + up * h, r, 6); nxt = acc + post_every
    for k in rails:
        for i in range(len(pts) - 1): tube(bm, pts[i] + up * h * k, pts[i + 1] + up * h * k, r * 0.9, 6)

def ladder(bm, a, b, wd, y, rung=0.32, r=0.03):
    """Echelle plane dans le plan XZ (profondeur y), montants espaces de wd dans le plan."""
    a, b = Vector(a), Vector(b); a.y = b.y = y
    t, n = frame(b - a); L = (b - a).length
    for s in (-1, 1): tube(bm, a + n * s * wd / 2, b + n * s * wd / 2, r * 1.3, 6)
    for i in range(int(L / rung)):
        p = a + t * (rung * (i + 0.5)); tube(bm, p - n * wd / 2, p + n * wd / 2, r, 6)

def stair(bm, a, b, width, steps, y):
    a, b = Vector(a), Vector(b); a.y = b.y = y
    for i in range(steps):
        p = a + (b - a) * ((i + 0.5) / steps)
        add_box(bm, p, ((b.x - a.x) / steps * 1.02, width, 0.05))
    prism(bm, [(a.x, a.z - 0.15), (b.x, b.z - 0.15), (b.x, b.z - 0.02), (a.x, a.z - 0.02)], y - width / 2 - 0.04, y - width / 2 + 0.02)
    prism(bm, [(a.x, a.z - 0.15), (b.x, b.z - 0.15), (b.x, b.z - 0.02), (a.x, a.z - 0.02)], y + width / 2 - 0.02, y + width / 2 + 0.04)

def clip_x(poly, x0, x1):
    out = poly
    for edge, keep in ((x0, lambda p: p[0] >= x0), (x1, lambda p: p[0] <= x1)):
        inp, out = out, []
        for i in range(len(inp)):
            p, q = inp[i], inp[(i + 1) % len(inp)]
            if keep(p):
                out.append(p)
            if keep(p) != keep(q):
                u = (edge - p[0]) / (q[0] - p[0]); out.append((edge, p[1] + u * (q[1] - p[1])))
        if not out: return []
    return out
