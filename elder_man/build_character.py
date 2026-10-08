"""
Vieil homme (casquette plate, chemise à carreaux, pantalon beige, mocassins)
modélisé procéduralement avec bpy à partir du character sheet.

    python build_character.py            -> elder_man.blend + rendus dans ./renders

Le personnage est centré à l'origine du monde (pieds à z=0), regarde vers -Y,
et est rendu en argile grise sur un plan qui sert de sol.
"""
import bpy, bmesh, math, os, sys
import numpy as np
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.abspath(__file__))
V = Vector

# ----------------------------------------------------------------------------
# utilitaires
# ----------------------------------------------------------------------------

def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def make_obj(name, bm, mat=None):
    me = bpy.data.meshes.new(name)
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    if mat:
        me.materials.append(mat)
    return ob


def activate(ob):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)


def modifier(ob, kind, **kw):
    activate(ob)
    m = ob.modifiers.new(kind.title(), kind)
    for k, v in kw.items():
        setattr(m, k, v)
    bpy.ops.object.modifier_apply(modifier=m.name)


def smooth(ob):
    me = ob.data
    me.polygons.foreach_set('use_smooth', [True] * len(me.polygons))
    me.update()


def get_co(ob):
    n = len(ob.data.vertices)
    a = np.empty(n * 3)
    ob.data.vertices.foreach_get('co', a)
    return a.reshape(n, 3)


def set_co(ob, a):
    ob.data.vertices.foreach_set('co', np.ascontiguousarray(a).ravel())
    ob.data.update()


def get_vn(ob):
    ob.data.calc_normals() if hasattr(ob.data, 'calc_normals') else None
    n = len(ob.data.vertices)
    a = np.empty(n * 3)
    ob.data.vertex_normals.foreach_get('vector', a)
    return a.reshape(n, 3)


def join(objs, name):
    activate(objs[0])
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    objs[0].name = name
    return objs[0]


# --- bruit de valeur vectorisé -----------------------------------------------

def _hash(ix, iy, iz, seed):
    n = (ix * 374761393 + iy * 668265263 + iz * 2147483629 + seed * 1442695041)
    n = (n ^ (n >> 13)) * 1274126177
    n = n ^ (n >> 16)
    return ((n & 0xFFFFFF) / float(0xFFFFFF)) * 2.0 - 1.0


def vnoise(P, seed=0):
    P = np.asarray(P, dtype=np.float64)
    i = np.floor(P).astype(np.int64)
    f = P - i
    f = f * f * (3 - 2 * f)
    ix, iy, iz = i[:, 0], i[:, 1], i[:, 2]
    fx, fy, fz = f[:, 0], f[:, 1], f[:, 2]
    c = lambda a, b, d: _hash(ix + a, iy + b, iz + d, seed)
    x00 = c(0, 0, 0) * (1 - fx) + c(1, 0, 0) * fx
    x10 = c(0, 1, 0) * (1 - fx) + c(1, 1, 0) * fx
    x01 = c(0, 0, 1) * (1 - fx) + c(1, 0, 1) * fx
    x11 = c(0, 1, 1) * (1 - fx) + c(1, 1, 1) * fx
    y0 = x00 * (1 - fy) + x10 * fy
    y1 = x01 * (1 - fy) + x11 * fy
    return y0 * (1 - fz) + y1 * fz


def fbm(P, octaves=4, lac=2.0, gain=0.5, seed=0):
    amp, tot, out = 1.0, 0.0, 0.0
    for o in range(octaves):
        out = out + amp * vnoise(P * (lac ** o), seed + o * 17)
        tot += amp
        amp *= gain
    return out / tot


def displace_noise(ob, amp, freq, octaves=4, seed=0, aniso=(1, 1, 1), ridged=False):
    P = get_co(ob)
    N = get_vn(ob)
    q = P * freq * np.array(aniso)
    d = fbm(q, octaves, seed=seed)
    if ridged:
        d = 1.0 - 2.0 * np.abs(d)
    set_co(ob, P + N * (d * amp)[:, None])


# --- lofts ------------------------------------------------------------------

def catmull(rings, n):
    """Rééchantillonne une liste de tuples numériques en n éléments (Catmull-Rom)."""
    R = np.array(rings, dtype=float)
    m = len(R)
    out = []
    for s in np.linspace(0, m - 1, n):
        i = min(int(math.floor(s)), m - 2)
        t = s - i
        p0 = R[max(i - 1, 0)]
        p1 = R[i]
        p2 = R[i + 1]
        p3 = R[min(i + 2, m - 1)]
        out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t +
                          (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    return out


def loft(name, rings, mat, nseg=32, p=2.0, ref=(1, 0, 0), cap0=True, cap1=True,
         mirror=1, shape=None, resample=None):
    """rings: (cx, cy, cz, rx, ry). Les anneaux sont orientés selon la tangente du chemin.
    u = ref projeté (rx), v = t x u (ry)."""
    if resample:
        rings = catmull(rings, resample)
    n = len(rings)
    bm = bmesh.new()
    rows = []
    pts = [V((r[0] * mirror, r[1], r[2])) for r in rings]
    for i, r in enumerate(rings):
        c = pts[i]
        t = (pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized()
        rv = V(ref)
        u = rv - t * rv.dot(t)
        u.normalize()
        v = t.cross(u)
        row = []
        for k in range(nseg):
            ang = 2 * math.pi * k / nseg
            cs, sn = math.cos(ang), math.sin(ang)
            e = 2.0 / p
            x = math.copysign(abs(cs) ** e, cs)
            y = math.copysign(abs(sn) ** e, sn)
            m = shape(ang, i / max(n - 1, 1)) if shape else 1.0
            row.append(bm.verts.new(c + u * (r[3] * x * m) + v * (r[4] * y * m)))
        rows.append(row)
    for i in range(n - 1):
        for k in range(nseg):
            k2 = (k + 1) % nseg
            bm.faces.new((rows[i][k], rows[i][k2], rows[i + 1][k2], rows[i + 1][k]))
    if cap0:
        bm.faces.new(rows[0][::-1])
    if cap1:
        bm.faces.new(rows[-1])
    return make_obj(name, bm, mat)


def finish(ob, sub=2, solid=0.0, offset=-1.0):
    if sub:
        modifier(ob, 'SUBSURF', levels=sub, render_levels=sub)
    if solid:
        modifier(ob, 'SOLIDIFY', thickness=solid, offset=offset, use_even_offset=True)
    smooth(ob)
    return ob


def gauss_disp(P, N, c, s, amp, dirv=None, mirror=False):
    """Ajoute une bosse gaussienne anisotrope. c, s: centre / sigma (m). dirv None -> normale."""
    cs = [np.array(c, dtype=float)]
    if mirror:
        cs.append(np.array([-c[0], c[1], c[2]], dtype=float))
    s = np.array(s, dtype=float)
    for cc in cs:
        w = np.exp(-0.5 * np.sum(((P - cc) / s) ** 2, axis=1))
        if dirv is None:
            P = P + N * (amp * w)[:, None]
        else:
            P = P + np.array(dirv)[None, :] * (amp * w)[:, None]
    return P


# ----------------------------------------------------------------------------
# matériaux (argile grise)
# ----------------------------------------------------------------------------

def mk_mat(name, gray=0.5, rough=0.62, sss=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (gray, gray, gray, 1)
    b.inputs['Roughness'].default_value = rough
    for k, v in (('Subsurface Weight', sss), ('Subsurface Scale', 0.01)):
        if k in b.inputs:
            b.inputs[k].default_value = v
    if 'Specular IOR Level' in b.inputs:
        b.inputs['Specular IOR Level'].default_value = 0.3
    return m


def bump_noise(m, scale, strength, dist=0.001, detail=8.0, stretch=None):
    nt = m.node_tree
    n = nt.nodes
    tc = n.new('ShaderNodeTexCoord')
    nz = n.new('ShaderNodeTexNoise')
    nz.inputs['Scale'].default_value = scale
    nz.inputs['Detail'].default_value = detail
    bm_ = n.new('ShaderNodeBump')
    bm_.inputs['Strength'].default_value = strength
    bm_.inputs['Distance'].default_value = dist
    src = tc.outputs['Object']
    if stretch:
        mp = n.new('ShaderNodeMapping')
        mp.inputs['Scale'].default_value = stretch
        nt.links.new(src, mp.inputs['Vector'])
        src = mp.outputs['Vector']
    nt.links.new(src, nz.inputs['Vector'])
    nt.links.new(nz.outputs['Fac'], bm_.inputs['Height'])
    nt.links.new(bm_.outputs['Normal'], n['Principled BSDF'].inputs['Normal'])


def gingham(m, period=0.0085, gray=0.5):
    """Vichy en niveaux de gris : bandes dans deux directions + bump."""
    nt = m.node_tree
    n = nt.nodes
    L = nt.links
    b = n['Principled BSDF']
    tc = n.new('ShaderNodeTexCoord')
    sep = n.new('ShaderNodeSeparateXYZ')
    L.new(tc.outputs['Object'], sep.inputs['Vector'])

    def stripe(axis):
        mul = n.new('ShaderNodeMath')
        mul.operation = 'MULTIPLY'
        mul.inputs[1].default_value = 2 * math.pi / period
        L.new(sep.outputs[axis], mul.inputs[0])
        sn = n.new('ShaderNodeMath')
        sn.operation = 'SINE'
        L.new(mul.outputs[0], sn.inputs[0])
        gt = n.new('ShaderNodeMath')
        gt.operation = 'GREATER_THAN'
        gt.inputs[1].default_value = 0.0
        L.new(sn.outputs[0], gt.inputs[0])
        return gt.outputs[0]

    # chaîne : abscisse curviligne autour de l'axe vertical (rayon moyen 0.17) ; trame : hauteur z
    at = n.new('ShaderNodeMath')
    at.operation = 'ARCTAN2'
    L.new(sep.outputs['Y'], at.inputs[0])
    L.new(sep.outputs['X'], at.inputs[1])
    arc = n.new('ShaderNodeMath')
    arc.operation = 'MULTIPLY'
    arc.inputs[1].default_value = 0.17
    L.new(at.outputs[0], arc.inputs[0])
    sep2 = n.new('ShaderNodeCombineXYZ')
    L.new(arc.outputs[0], sep2.inputs['X'])
    L.new(sep.outputs['Z'], sep2.inputs['Z'])
    sep = n.new('ShaderNodeSeparateXYZ')
    L.new(sep2.outputs['Vector'], sep.inputs['Vector'])
    sx = stripe('X')
    sz = stripe('Z')
    sy = sz
    # fac = (sx + max(sz, sy)) -> 0,1,2 : 3 niveaux comme un vrai vichy
    mx = n.new('ShaderNodeMath')
    mx.operation = 'MAXIMUM'
    L.new(sz, mx.inputs[0])
    L.new(sy, mx.inputs[1])
    add = n.new('ShaderNodeMath')
    add.operation = 'ADD'
    L.new(sx, add.inputs[0])
    L.new(mx.outputs[0], add.inputs[1])
    ramp = n.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (gray * 1.12, gray * 1.12, gray * 1.12, 1)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (gray * 0.74, gray * 0.74, gray * 0.74, 1)
    div = n.new('ShaderNodeMath')
    div.operation = 'DIVIDE'
    div.inputs[1].default_value = 2.0
    L.new(add.outputs[0], div.inputs[0])
    L.new(div.outputs[0], ramp.inputs['Fac'])
    L.new(ramp.outputs['Color'], b.inputs['Base Color'])
    bp = n.new('ShaderNodeBump')
    bp.inputs['Strength'].default_value = 0.35
    bp.inputs['Distance'].default_value = 0.0006
    L.new(div.outputs[0], bp.inputs['Height'])
    L.new(bp.outputs['Normal'], b.inputs['Normal'])


# ----------------------------------------------------------------------------
# dimensions principales (mètres)
# ----------------------------------------------------------------------------
HEAD_C = np.array([0.0, -0.010, 1.624])
M_DIAL = M_SKIN = M_SHIRT = M_PANTS = M_CAP = M_HAIR = M_SHOE = M_METAL = M_EYE = M_GLASS = M_BELT = None


def build_materials():
    global M_DIAL, M_SKIN, M_SHIRT, M_PANTS, M_CAP, M_HAIR, M_SHOE, M_METAL, M_EYE, M_GLASS, M_BELT
    M_SKIN = mk_mat('clay_skin', 0.40, 0.55, sss=0.12)
    bump_noise(M_SKIN, 900, 0.25, 0.0006)
    M_SHIRT = mk_mat('clay_shirt', 0.40, 0.8)
    gingham(M_SHIRT, 0.0085, 0.40)
    M_PANTS = mk_mat('clay_pants', 0.38, 0.85)
    bump_noise(M_PANTS, 1400, 0.25, 0.0004, stretch=(1, 1, 1))
    M_CAP = mk_mat('clay_cap', 0.36, 0.9)
    bump_noise(M_CAP, 1600, 0.6, 0.0006)
    M_HAIR = mk_mat('clay_hair', 0.50, 0.6)
    M_SHOE = mk_mat('clay_shoe', 0.30, 0.4)
    bump_noise(M_SHOE, 500, 0.12, 0.0003)
    M_BELT = mk_mat('clay_belt', 0.27, 0.5)
    M_METAL = mk_mat('clay_metal', 0.6, 0.25)
    M_METAL.node_tree.nodes['Principled BSDF'].inputs['Metallic'].default_value = 0.8
    M_DIAL = mk_mat('clay_dial', 0.78, 0.35)
    M_EYE = mk_mat('clay_eye', 0.55, 0.15)
    # iris / pupille en coordonnées objet (sphère rayon 0.0145 centrée à l'origine objet)
    nt = M_EYE.node_tree
    n = nt.nodes
    L = nt.links
    tc = n.new('ShaderNodeTexCoord')
    sep = n.new('ShaderNodeSeparateXYZ')
    L.new(tc.outputs['Object'], sep.inputs['Vector'])
    xx = n.new('ShaderNodeMath'); xx.operation = 'MULTIPLY'
    L.new(sep.outputs['X'], xx.inputs[0]); L.new(sep.outputs['X'], xx.inputs[1])
    zz = n.new('ShaderNodeMath'); zz.operation = 'MULTIPLY'
    L.new(sep.outputs['Z'], zz.inputs[0]); L.new(sep.outputs['Z'], zz.inputs[1])
    ad = n.new('ShaderNodeMath'); ad.operation = 'ADD'
    L.new(xx.outputs[0], ad.inputs[0]); L.new(zz.outputs[0], ad.inputs[1])
    sq = n.new('ShaderNodeMath'); sq.operation = 'SQRT'
    L.new(ad.outputs[0], sq.inputs[0])
    ramp = n.new('ShaderNodeValToRGB')
    ramp.color_ramp.interpolation = 'CONSTANT'
    e = ramp.color_ramp.elements
    e[0].position = 0.0; e[0].color = (0.01, 0.01, 0.01, 1)
    e[1].position = 0.0020; e[1].color = (0.26, 0.26, 0.26, 1)
    e.new(0.0050).color = (0.14, 0.14, 0.14, 1)
    e.new(0.0060).color = (0.62, 0.62, 0.62, 1)
    # seulement vers l'avant (y<0)
    L.new(sq.outputs[0], ramp.inputs['Fac'])
    L.new(ramp.outputs['Color'], n['Principled BSDF'].inputs['Base Color'])
    M_GLASS = bpy.data.materials.new('clay_glass')
    M_GLASS.use_nodes = True
    nt = M_GLASS.node_tree
    for n_ in list(nt.nodes):
        nt.nodes.remove(n_)
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    tr = nt.nodes.new('ShaderNodeBsdfTransparent')
    gl = nt.nodes.new('ShaderNodeBsdfGlossy')
    gl.inputs['Roughness'].default_value = 0.02
    mx = nt.nodes.new('ShaderNodeMixShader')
    mx.inputs['Fac'].default_value = 0.07
    nt.links.new(tr.outputs[0], mx.inputs[1])
    nt.links.new(gl.outputs[0], mx.inputs[2])
    nt.links.new(mx.outputs[0], out.inputs['Surface'])


# ----------------------------------------------------------------------------
# tête
# ----------------------------------------------------------------------------

def head_shape():
    """Retourne (points P, normales N) de l'ellipsoïde de base déjà modelé en crâne/mâchoire."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=7, radius=1.0)
    d = np.array([v.co[:] for v in bm.verts])
    bm.free()
    return d


def build_head():
    d = head_shape()
    x, y, z = d.T
    rx, ry, rz = 0.0785, 0.1000, 0.1165
    t = np.clip((0.12 - z) / 1.12, 0, 1)
    front = np.clip(-y, 0, 1)
    sx = (1 - 0.09 * t ** 1.5) * (1 + 0.10 * front ** 1.3)
    sy = np.where(y < 0, 1 - 0.02 * t, 1 - 0.22 * t ** 1.2)
    sy = sy * (1 + 0.04 * np.clip(z, 0, 1) * (y > 0))
    P = np.stack([rx * x * sx, ry * y * sy, rz * z], 1)
    N = np.stack([P[:, 0] / rx ** 2, P[:, 1] / ry ** 2, P[:, 2] / rz ** 2], 1)
    N /= np.linalg.norm(N, axis=1)[:, None]
    P0 = P.copy()
    FY = np.array([0, -1.0, 0])

    # --- nez --------------------------------------------------------------
    for k in range(12):
        tt = k / 11.0
        zc = 0.012 - tt * 0.046
        amp = 0.0030 * tt ** 0.6 + 0.0100 * tt ** 1.8
        sxx = 0.0052 + 0.0050 * tt
        P = gauss_disp(P, N, (0, -0.093, zc), (sxx, 0.025, 0.0070), amp, FY)
    P = gauss_disp(P, N, (0, -0.098, -0.0345), (0.0072, 0.02, 0.0068), 0.0034, FY)    # pointe
    P = gauss_disp(P, N, (0.0148, -0.092, -0.0390), (0.0066, 0.02, 0.0060), 0.0045, FY)
    P = gauss_disp(P, N, (-0.0148, -0.092, -0.0390), (0.0066, 0.02, 0.0060), 0.0045, FY)
    P = gauss_disp(P, N, (0.0, -0.09, -0.053), (0.0040, 0.02, 0.0075), -0.0016, FY)  # philtrum
    P = gauss_disp(P, N, (0.0, -0.095, -0.0475), (0.0075, 0.02, 0.0035), -0.0030, FY)  # base du nez
    P = gauss_disp(P, N, (0.0, -0.095, 0.014), (0.012, 0.02, 0.006), -0.0030, FY)    # racine du nez (nasion)

    # --- bouche ----------------------------------------------------------------
    P = gauss_disp(P, N, (0, -0.088, -0.0575), (0.0125, 0.02, 0.0045), 0.0070, FY)    # lèvre sup
    P = gauss_disp(P, N, (0, -0.087, -0.0715), (0.0125, 0.02, 0.0050), 0.0078, FY)    # lèvre inf
    P = gauss_disp(P, N, (0, -0.088, -0.0642), (0.0185, 0.02, 0.0011), -0.0030, FY)   # fente
    P = gauss_disp(P, N, (0.021, -0.082, -0.0645), (0.0035, 0.02, 0.004), -0.0022, FY, mirror=True)  # commissures
    P = gauss_disp(P, N, (0, -0.0885, -0.0845), (0.014, 0.02, 0.0042), -0.0024, FY)   # sillon mentonnier
    P = gauss_disp(P, N, (0, -0.085, -0.1015), (0.016, 0.02, 0.013), 0.0125, FY)      # menton
    # fossette du menton + plis de la bouche (vieillesse)
    P = gauss_disp(P, N, (0.0, -0.0875, -0.1005), (0.0012, 0.02, 0.003), -0.0005, FY)

    # --- yeux : orbite, paupières, poches -------------------------------------
    for sg in (1, -1):
        ex = 0.0315 * sg
        P = gauss_disp(P, N, (ex, -0.085, 0.009), (0.0125, 0.02, 0.0085), -0.0042, None)   # orbite
        P = gauss_disp(P, N, (ex, -0.085, 0.0075), (0.0120, 0.02, 0.0042), -0.0054, FY)       # fente palpébrale
        P = gauss_disp(P, N, (ex, -0.0865, 0.0162), (0.0125, 0.02, 0.0030), 0.0010, FY)      # paupière sup
        P = gauss_disp(P, N, (ex, -0.0865, 0.0225), (0.0140, 0.02, 0.0012), -0.0013, FY)     # pli
        P = gauss_disp(P, N, (ex, -0.0855, -0.0010), (0.0120, 0.02, 0.0024), 0.0024, FY)     # paupière inf
        P = gauss_disp(P, N, (ex * 1.02, -0.0845, -0.0120), (0.0140, 0.02, 0.0050), 0.0016, FY)  # poche
        P = gauss_disp(P, N, (ex * 1.02, -0.0845, -0.0185), (0.0150, 0.02, 0.0013), -0.0013, FY)  # sillon
        # sourcil (broussailleux)
        for k in range(8):
            tt = k / 7.0
            bx = (0.012 + 0.044 * tt) * sg
            bz = 0.0285 + 0.0065 * math.sin(tt * math.pi * 0.85) - 0.003 * tt
            P = gauss_disp(P, N, (bx, -0.08, bz), (0.0050, 0.03, 0.0026), 0.0034 * (1.0 - 0.3 * tt), None)
        # arcade
        P = gauss_disp(P, N, (0.032 * sg, -0.082, 0.026), (0.017, 0.03, 0.0085), 0.0055, None)
        # pattes d'oie
        for a in (-0.35, 0.0, 0.4):
            for k in range(4):
                r = 0.006 + 0.0065 * k
                P = gauss_disp(P, N, (sg * (0.050 + r * math.cos(a)), -0.073, 0.008 + r * math.sin(a)),
                               (0.0035, 0.03, 0.0012), -0.0008, None)
        # pommettes + creux des joues + bajoues
        P = gauss_disp(P, N, (0.056 * sg, -0.066, -0.014), (0.016, 0.02, 0.013), 0.0040, None)
        P = gauss_disp(P, N, (0.045 * sg, -0.071, -0.050), (0.013, 0.02, 0.016), -0.0025, None)
        P = gauss_disp(P, N, (0.037 * sg, -0.062, -0.088), (0.016, 0.02, 0.011), 0.0030, None)
        P = gauss_disp(P, N, (0.063 * sg, -0.04, 0.012), (0.01, 0.03, 0.016), -0.0022, None)   # tempe
        # sillon naso-génien
        for k in range(8):
            tt = k / 7.0
            P = gauss_disp(P, N, ((0.017 + 0.016 * tt) * sg, -0.0865 + 0.01 * tt, -0.040 - 0.032 * tt),
                           (0.0030, 0.03, 0.0032), -0.0021, FY)
        # sillon labial -> menton (plis de marionnette)
        for k in range(5):
            tt = k / 4.0
            P = gauss_disp(P, N, ((0.026 + 0.003 * tt) * sg, -0.079 + 0.002 * tt, -0.074 - 0.020 * tt),
                           (0.0028, 0.03, 0.0035), -0.0016, FY)
    # rides du front (visibles sous la casquette)
    for zz_ in (0.050, 0.062):
        P = gauss_disp(P, N, (0, -0.075, zz_), (0.04, 0.03, 0.0012), -0.0006, None)

    # --- textures de peau -------------------------------------------------------
    q = P * np.array([1, 1, 1])
    macro = fbm(q * 55.0, 4, seed=3)
    P = P + N * (macro * 0.0009)[:, None]
    fine = fbm(q * 420.0, 3, seed=9)
    P = P + N * (fine * 0.00018)[:, None]
    # rides fines horizontales / verticales près des yeux & de la bouche
    P = P + N * ((1 - 2 * np.abs(vnoise(q * np.array([45, 45, 260]), 5))) * 0.00025 *
                 np.exp(-0.5 * (((P[:, 2] - 0.0) / 0.05) ** 2)) * (P[:, 1] < -0.02))[:, None]

    # --- création du mesh ---------------------------------------------------------
    me = bpy.data.meshes.new('head')
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=7, radius=1.0)
    bm.to_mesh(me)
    bm.free()
    me.vertices.foreach_set('co', P.ravel())
    me.update()
    ob = bpy.data.objects.new('head', me)
    bpy.context.scene.collection.objects.link(ob)
    me.materials.append(M_SKIN)
    smooth(ob)
    # position monde
    set_co(ob, get_co(ob) + HEAD_C)
    return ob, P0


def build_eyes(head):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(head, depsgraph)
    objs = []
    for sg in (1, -1):
        ex = 0.0315 * sg
        zc = HEAD_C[2] + 0.0075
        hit, nrm, idx, dist = bvh.ray_cast(V((ex, -0.3, zc)), V((0, 1, 0)))
        r = 0.0128
        prot = 0.0024
        center = V((ex, hit.y - prot + r, zc))
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=r)
        # pôle avant selon -Y : create_uvsphere a ses pôles sur Z -> tourner
        bmesh.ops.rotate(bm, verts=bm.verts[:], cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, 'X'))
        # léger regard vers l'avant/le côté
        bmesh.ops.rotate(bm, verts=bm.verts[:], cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(-5 * sg), 3, 'Z'))
        ob = make_obj('eye', bm, M_EYE)
        smooth(ob)
        # origine objet = centre de l'oeil => on garde l'origine à (0,0,0) pour les coords objet
        # donc on déplace les sommets et on conserve les coordonnées objet en centrant via un empty ? ->
        # plus simple : on met l'origine de l'objet au centre de l'oeil.
        ob.location = center
        objs.append(ob)
    return objs


def build_ears():
    objs = []
    for sg in (1, -1):
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=32, radius=1.0)
        for v in bm.verts:
            a, b, c = v.co  # a: épaisseur(x), b: largeur(y), c: hauteur(z)
            rho = math.sqrt((b / 0.9) ** 2 + (c / 0.97) ** 2)
            th = 0.0105 * (1.0 - 0.3 * rho)
            x = a * th
            if a > 0:   # face externe : conque creuse + hélix en relief
                x -= 0.0042 * math.exp(-(rho / 0.42) ** 2)
                x += 0.0022 * math.exp(-((rho - 0.78) / 0.14) ** 2)
                x -= 0.0016 * math.exp(-((rho - 0.5) / 0.08) ** 2) * (1 if b > -0.05 else 0)
            y = b * 0.0185
            z = c * 0.0345
            # lobe plus épais, haut plus fin
            if c < -0.45:
                x += 0.002 * a * (-c - 0.45)
                y *= 1.0 - 0.15 * (-c - 0.45)
            v.co = (x * sg * 0.95, y * 0.92, z * 0.94)
        ob = make_obj('ear', bm, M_SKIN)
        # rotation: légèrement décollée de la tête + inclinée en arrière
        rot = Matrix.Rotation(math.radians(10) * sg, 4, 'Z') @ Matrix.Rotation(math.radians(-8), 4, 'X')
        ob.matrix_world = Matrix.Translation(V((HEAD_C[0] + 0.0735 * sg, HEAD_C[1] + 0.006, HEAD_C[2] - 0.004))) @ rot
        activate(ob)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        finish(ob, sub=1)
        objs.append(ob)
    return objs


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def build_hair():
    """Cheveux gris courts (nuque, derrière / au-dessus des oreilles, favoris).
    La coque s'amincit progressivement jusqu'au crâne : aucune arête dure."""
    d = head_shape()
    x, y, z = d.T
    rx, ry, rz = 0.0785, 0.1000, 0.1165
    t = np.clip((0.12 - z) / 1.12, 0, 1)
    front = np.clip(-y, 0, 1)
    sx = (1 - 0.09 * t ** 1.5) * (1 + 0.10 * front ** 1.3)
    sy = np.where(y < 0, 1 - 0.02 * t, 1 - 0.22 * t ** 1.2)
    sy = sy * (1 + 0.04 * np.clip(z, 0, 1) * (y > 0))
    P = np.stack([rx * x * sx, ry * y * sy, rz * z], 1)
    N = np.stack([P[:, 0] / rx ** 2, P[:, 1] / ry ** 2, P[:, 2] / rz ** 2], 1)
    N /= np.linalg.norm(N, axis=1)[:, None]
    nz = vnoise(np.stack([P[:, 0] * 55, P[:, 1] * 55, P[:, 2] * 0 + 2.3], 1), 2)
    ax = np.abs(P[:, 0])
    # limite basse du cheveu (z) selon la zone
    zlo = np.full(len(P), -0.052)                                                  # nuque
    zlo = np.where(P[:, 1] < 0.040, 0.030, zlo)                                    # au-dessus et devant l'oreille
    zlo = np.where((P[:, 1] < -0.016) & (ax > 0.050), -0.012, zlo)                 # favoris
    zlo = zlo + 0.004 * nz
    m = smoothstep(0.0, 0.016, P[:, 2] - zlo)                                      # bas
    m *= smoothstep(-0.050, -0.040, P[:, 1])                                       # devant
    m *= smoothstep(0.100, 0.085, P[:, 2])                                         # haut (sous la casquette)
    off = -0.0010 + 0.0050 * m
    P = P + N * off[:, None]
    # mèches
    strands = vnoise(P * np.array([420, 420, 40]), 7) * 0.0006 + vnoise(P * np.array([110, 110, 16]), 8) * 0.0010
    P = P + N * (strands * m)[:, None]
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=7, radius=1.0)
    bm.verts.ensure_lookup_table()
    for i, v in enumerate(bm.verts):
        v.co = P[i]
    drop = [v for i, v in enumerate(bm.verts) if m[i] <= 0.002]
    bmesh.ops.delete(bm, geom=drop, context='VERTS')
    ob = make_obj('hair', bm, M_HAIR)
    smooth(ob)
    set_co(ob, get_co(ob) + HEAD_C)
    return ob


def build_neck():
    rings = [
        (0.0, 0.016, 1.350, 0.080, 0.084),
        (0.0, 0.016, 1.430, 0.068, 0.072),
        (0.0, 0.014, 1.490, 0.060, 0.064),
        (0.0, 0.012, 1.535, 0.058, 0.063),
        (0.0, 0.008, 1.575, 0.058, 0.064),
    ]
    ob = loft('neck', rings, M_SKIN, nseg=64, resample=24)
    finish(ob, sub=3)
    P = get_co(ob)
    N = get_vn(ob)
    FY = None
    # sterno-cléido-mastoïdiens
    for sg in (1, -1):
        for k in range(10):
            tt = k / 9.0
            c = ((0.020 + 0.022 * tt) * sg, -0.045 + 0.02 * tt, 1.395 + 0.15 * tt)
            P = gauss_disp(P, N, c, (0.008, 0.02, 0.012), 0.0030 * math.sin(math.pi * (0.2 + 0.7 * tt)), None)
    # pomme d'Adam et creux sus-sternal
    P = gauss_disp(P, N, (0, -0.055, 1.47), (0.007, 0.02, 0.010), 0.0035, None)
    P = gauss_disp(P, N, (0, -0.07, 1.405), (0.012, 0.03, 0.008), -0.0030, None)
    # rides du cou (plis horizontaux)
    ridg = 1.0 - 2.0 * np.abs(vnoise(P * np.array([40, 40, 210]), 4))
    P = P + N * (ridg * 0.0006 * np.clip((P[:, 1] + 0.0) / -0.05, 0, 1))[:, None]
    P = P + N * (fbm(P * 90, 3, seed=1) * 0.0008)[:, None]
    set_co(ob, P)
    return ob


# ----------------------------------------------------------------------------
# casquette plate
# ----------------------------------------------------------------------------

def build_cap():
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=96, v_segments=48, radius=1.0)
    drop = [v for v in bm.verts if v.co.z < -1e-6]
    bmesh.ops.delete(bm, geom=drop, context='VERTS')
    a, b, h = 0.0815, 0.1045, 0.074
    for v in bm.verts:
        x, y, z = v.co
        zp = max(z, 0.0)
        px = a * x * (1 + 0.07 * zp)
        py = b * y * (1 + (0.14 * zp if y < 0 else 0.04 * zp))
        z0 = 0.035 - 0.021 * y                     # bord avant plus bas, arrière plus bas aussi (inclinaison)
        z0 = 0.026 + 0.034 * max(-y, 0) - 0.012 * max(y, 0)
        pz = z0 + h * (zp ** 0.72)
        # affaissement vers l'avant (casquette plate "tombante")
        pz -= 0.012 * zp * max(-y, 0) ** 2
        v.co = (px, py, pz)
    ob = make_obj('cap', bm, M_CAP)
    finish(ob, sub=2)
    P = get_co(ob)
    N = get_vn(ob)
    # coutures des 8 panneaux + bouton central
    phi = np.arctan2(P[:, 1], P[:, 0])
    r = np.hypot(P[:, 0], P[:, 1])
    for kk in range(8):
        ph = kk * math.pi / 4 + math.pi / 8
        dphi = np.angle(np.exp(1j * (phi - ph)))
        w = np.exp(-0.5 * ((dphi * r) / 0.0022) ** 2) * np.clip((P[:, 2] - 0.045) / 0.04, 0, 1)
        P = P + N * (-0.0012 * w)[:, None]
        P = P + N * (0.0011 * np.exp(-0.5 * ((np.abs(dphi) * r - 0.0055) / 0.0016) ** 2) * np.clip((P[:, 2] - 0.045) / 0.04, 0, 1))[:, None]
    # plis souples du tissu
    P = P + N * (fbm(P * 22, 3, seed=11) * 0.0018)[:, None]
    P = P + N * (fbm(P * np.array([260, 260, 260]), 2, seed=12) * 0.0003)[:, None]
    set_co(ob, P)
    modifier(ob, 'SOLIDIFY', thickness=0.0045, offset=-1.0)
    smooth(ob)
    # bouton
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=0.0075)
    for v in bm.verts:
        v.co.z *= 0.55
        v.co.z += 0.108
    btn = make_obj('cap_button', bm, M_CAP)
    smooth(btn)
    # visière
    rings = [
        (0.0, -0.098, 0.050, 0.066, 0.0026),
        (0.0, -0.116, 0.045, 0.066, 0.0026),
        (0.0, -0.133, 0.039, 0.061, 0.0026),
        (0.0, -0.150, 0.033, 0.050, 0.0026),
        (0.0, -0.158, 0.029, 0.032, 0.0026),
        (0.0, -0.163, 0.027, 0.012, 0.0022),
    ]
    vis = loft('cap_visor', rings, M_CAP, nseg=64, p=3.5, resample=14, cap0=False, cap1=True)
    finish(vis, sub=2)
    # courbure transversale de la visière
    Pv = get_co(vis)
    Pv[:, 2] -= 0.30 * Pv[:, 0] ** 2
    set_co(vis, Pv)
    # transforme en coordonnées monde
    for o in (ob, btn, vis):
        set_co(o, get_co(o) + HEAD_C)
    join([ob, btn, vis], 'cap')
    return ob


# ----------------------------------------------------------------------------
# lunettes
# ----------------------------------------------------------------------------

def curve_obj(name, pts, bevel, cyclic=False, mat=None):
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    cu.bevel_depth = bevel
    cu.bevel_resolution = 3
    cu.use_fill_caps = True
    sp = cu.splines.new('POLY')
    sp.points.add(len(pts) - 1)
    for i, p in enumerate(pts):
        sp.points[i].co = (p[0], p[1], p[2], 1.0)
    sp.use_cyclic_u = cyclic
    ob = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(ob)
    if mat:
        cu.materials.append(mat)
    return ob


def build_glasses():
    objs = []
    C = HEAD_C
    yl = C[1] - 0.0925   # plan des verres
    for sg in (1, -1):
        cx = C[0] + 0.0335 * sg
        cz = C[2] + 0.004
        pts = []
        n = 72
        hw, hh = 0.0265, 0.0185
        for k in range(n):
            a = 2 * math.pi * k / n
            cs, sn = math.cos(a), math.sin(a)
            e = 2 / 4.2
            x = hw * math.copysign(abs(cs) ** e, cs)
            z = hh * math.copysign(abs(sn) ** e, sn)
            z += 0.0015 * (x / hw) * sg   # léger rehaut vers l'extérieur
            pts.append((cx + x, yl, cz + z))
        objs.append(curve_obj('rim', pts, 0.00075, True, M_METAL))
        # verre
        bm = bmesh.new()
        ring = [bm.verts.new(p) for p in pts]
        cen = bm.verts.new((cx, yl, cz))
        for k in range(n):
            bm.faces.new((ring[k], ring[(k + 1) % n], cen))
        lens = make_obj('lens', bm, M_GLASS)
        modifier(lens, 'SOLIDIFY', thickness=0.0016, offset=0)
        objs.append(lens)
        # branche
        ex = cx + hw * sg
        arm = [(ex, yl + 0.001, cz + 0.011),
               (C[0] + 0.0655 * sg, yl + 0.012, cz + 0.0125),
               (C[0] + 0.0745 * sg, C[1] - 0.035, cz + 0.0125),
               (C[0] + 0.0815 * sg, C[1] + 0.000, cz + 0.0125),
               (C[0] + 0.0845 * sg, C[1] + 0.020, cz + 0.0110),
               (C[0] + 0.0840 * sg, C[1] + 0.034, cz - 0.0020),
               (C[0] + 0.0820 * sg, C[1] + 0.040, cz - 0.0150)]
        objs.append(curve_obj('arm', arm, 0.0009, False, M_METAL))
        # charnière
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=0.003)
        for v in bm.verts:
            v.co += V((ex, yl + 0.002, cz + 0.011))
        objs.append(make_obj('hinge', bm, M_METAL))
    # pont
    bridge = [(C[0] + x, yl - 0.0005 - 0.0035 * (1 - (x / 0.0072) ** 2), C[2] + 0.0135 + 0.003 * (1 - (x / 0.0072) ** 2))
              for x in np.linspace(-0.0072, 0.0072, 12)]
    # relie aux deux montures (bord interne)
    bridge = [(C[0] - 0.0072 - 0.0007, yl, C[2] + 0.0105)] + bridge + [(C[0] + 0.0072 + 0.0007, yl, C[2] + 0.0105)]
    objs.append(curve_obj('bridge', bridge, 0.0009, False, M_METAL))
    # plaquettes de nez
    for sg in (1, -1):
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=0.0035)
        for v in bm.verts:
            v.co.x *= 0.5
            v.co += V((C[0] + 0.0105 * sg, yl + 0.0045, C[2] - 0.0045))
        o = make_obj('pad', bm, M_METAL)
        smooth(o)
        objs.append(o)
    return objs


# ----------------------------------------------------------------------------
# corps, vêtements
# ----------------------------------------------------------------------------

def surface_hit(bvh, x, z, y0=-0.6):
    hit, nrm, idx, dist = bvh.ray_cast(V((x, y0, z)), V((0, 1, 0)))
    return hit, nrm


def bvh_of(ob):
    return BVHTree.FromObject(ob, bpy.context.evaluated_depsgraph_get())


SHIRT_RINGS = [
    (0, 0.004, 0.940, 0.170, 0.110),
    (0, 0.004, 0.960, 0.172, 0.112),
    (0, -0.002, 1.030, 0.173, 0.116),
    (0, -0.006, 1.120, 0.178, 0.120),
    (0, -0.004, 1.220, 0.184, 0.117),
    (0, 0.000, 1.300, 0.188, 0.110),
    (0, 0.000, 1.360, 0.198, 0.100),
    (0, 0.004, 1.396, 0.208, 0.092),
    (0, 0.008, 1.424, 0.186, 0.083),
    (0, 0.011, 1.450, 0.128, 0.072),
    (0, 0.012, 1.472, 0.082, 0.066),
    (0, 0.012, 1.490, 0.064, 0.060),
]
SLEEVE_S = np.array([0.178, 0.0, 1.350])
SLEEVE_E = np.array([0.245, 0.005, 1.120])


def boolean(ob, other, op, remove_other=True):
    activate(ob)
    m = ob.modifiers.new('bool', 'BOOLEAN')
    m.operation = op
    m.operand_type = 'OBJECT'
    m.object = other
    m.solver = 'EXACT'
    bpy.ops.object.modifier_apply(modifier=m.name)
    if remove_other:
        bpy.data.objects.remove(other, do_unlink=True)


def sleeve_solid(sg, name='sleeve'):
    S, E = SLEEVE_S, SLEEVE_E
    L = np.linalg.norm(E - S)
    R = 0.0605
    rings = []
    for k in range(0, 7):                      # calotte de l'épaule
        th = math.radians(12 + k * 13)
        a = -R * math.cos(th)
        r = R * math.sin(th)
        f = a / L
        c = S + f * (E - S)
        rings.append((c[0], c[1], c[2], r * 0.98, r * 0.96))
    for f, rx, ry in ((0.22, 0.0605, 0.059), (0.46, 0.0595, 0.0585), (0.66, 0.0585, 0.0575)):
        c = S + f * (E - S)
        rings.append((c[0], c[1], c[2], rx, ry))
    return loft(name, rings, M_SHIRT, nseg=48, resample=24, mirror=sg)


def build_torso():
    objs = []
    # noyau (peau) : ferme le col ouvert
    core = [(r[0], r[1], r[2], r[3] - 0.014, r[4] - 0.014) for r in SHIRT_RINGS]
    core[-1] = (0, 0.012, 1.505, 0.054, 0.054)
    o = loft('chest_core', core, M_SKIN, nseg=48, p=2.2, resample=14)
    finish(o, sub=1)
    objs.append(o)
    # chemise = torse + manches (union booléenne) -> un seul volume, puis remaillage
    sh = loft('shirt', SHIRT_RINGS, M_SHIRT, nseg=96, p=2.2, resample=40)
    for sg in (1, -1):
        boolean(sh, sleeve_solid(sg), 'UNION')
    # ouverture des manches : cavité cylindrique de l'axe du bras
    d = (SLEEVE_E - SLEEVE_S) / np.linalg.norm(SLEEVE_E - SLEEVE_S)
    H = SLEEVE_S + 0.66 * (SLEEVE_E - SLEEVE_S)
    for sg in (1, -1):
        r0 = H + d * 0.03
        r1 = H - d * 0.075
        cav = loft('cav', [(r0[0], r0[1], r0[2], 0.0500, 0.0490), (r1[0], r1[1], r1[2], 0.0500, 0.0490)], M_SHIRT,
                   nseg=48, mirror=sg, resample=4)
        boolean(sh, cav, 'DIFFERENCE')
    modifier(sh, 'REMESH', mode='VOXEL', voxel_size=0.0028, adaptivity=0.0, use_smooth_shade=True)
    modifier(sh, 'SMOOTH', factor=0.7, iterations=7)
    P = get_co(sh)
    N = get_vn(sh)
    belt_w = np.exp(-0.5 * ((P[:, 2] - 1.07) / 0.05) ** 2)
    f1 = fbm(P * np.array([13, 13, 46]), 3, seed=21)
    f2 = fbm(P * 32, 3, seed=22)
    P = P + N * (f1 * 0.0035 * (0.4 + belt_w * 1.8) + f2 * 0.0010)[:, None]
    P = gauss_disp(P, N, (0.0, -0.12, 1.08), (0.10, 0.06, 0.06), 0.0035, None)
    # plis des aisselles et du coude
    for sg in (1, -1):
        for k in range(6):
            P = gauss_disp(P, N, (sg * 0.16, -0.01 - 0.01 * k, 1.30 - 0.02 * k), (0.03, 0.02, 0.006), 0.0020, None)
    P = P + N * (fbm(P * np.array([16, 16, 30]), 3, seed=41) * 0.0020 *
                 (np.abs(P[:, 0]) > 0.19))[:, None]
    set_co(sh, P)
    smooth(sh)
    objs.append(sh)

    bvh = bvh_of(sh)
    # patte de boutonnage
    xs = np.linspace(-0.0135, 0.0135, 5)
    zs = np.linspace(1.00, 1.452, 70)
    bm = bmesh.new()
    grid = []
    for z in zs:
        row = []
        for x in xs:
            hit, nrm = surface_hit(bvh, x, z)
            if hit is None:
                hit = V((x, -0.12, z)); nrm = V((0, -1, 0))
            ridge = 0.0014 + 0.0007 * (1.0 if abs(x) > 0.0115 else 0.0)
            row.append(bm.verts.new(hit + nrm * ridge))
        grid.append(row)
    for i in range(len(zs) - 1):
        for j in range(len(xs) - 1):
            bm.faces.new((grid[i][j], grid[i][j + 1], grid[i + 1][j + 1], grid[i + 1][j]))
    pl = make_obj('placket', bm, M_SHIRT)
    modifier(pl, 'SOLIDIFY', thickness=0.0015, offset=-1)
    smooth(pl)
    objs.append(pl)
    # boutons
    for zb in (1.405, 1.335, 1.265, 1.195, 1.125, 1.055):
        hit, nrm = surface_hit(bvh, 0.0, zb)
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=10, radius=0.0052)
        for v in bm.verts:
            v.co.y *= 0.38
        bm.verts.ensure_lookup_table()
        bmesh.ops.translate(bm, verts=bm.verts[:], vec=hit + V((0, -0.0022, 0)))
        b = make_obj('button', bm, M_SHIRT)
        smooth(b)
        objs.append(b)
    # poche de poitrine (côté +X = côté gauche du personnage)
    x0, x1, z0, z1 = 0.048, 0.118, 1.205, 1.300
    nx, nz = 14, 16
    bm = bmesh.new()
    grid = []
    for i in range(nz):
        zz = z0 + (z1 - z0) * i / (nz - 1)
        row = []
        for j in range(nx):
            xx = x0 + (x1 - x0) * j / (nx - 1)
            zadj = zz + (0.012 * (1 - abs((xx - (x0 + x1) / 2) / ((x1 - x0) / 2))) * (1 if i == 0 else 0))
            hit, nrm = surface_hit(bvh, xx, zadj)
            if hit is None:
                hit = V((xx, -0.12, zadj)); nrm = V((0, -1, 0))
            edge = (i in (0, nz - 1)) or (j in (0, nx - 1))
            row.append(bm.verts.new(hit + nrm * (0.0022 if edge else 0.0013)))
        grid.append(row)
    for i in range(nz - 1):
        for j in range(nx - 1):
            bm.faces.new((grid[i][j], grid[i][j + 1], grid[i + 1][j + 1], grid[i + 1][j]))
    pk = make_obj('pocket', bm, M_SHIRT)
    modifier(pk, 'SUBSURF', levels=1, render_levels=1)
    modifier(pk, 'SOLIDIFY', thickness=0.0016, offset=-1)
    smooth(pk)
    objs.append(pk)
    return objs


def build_collar():
    objs = []
    L = [(0.0, 0.074, 1.484), (0.042, 0.062, 1.485), (0.070, 0.028, 1.476), (0.074, -0.026, 1.456),
         (0.070, -0.058, 1.425), (0.046, -0.080, 1.398)]
    U = [(0.0, 0.082, 1.512), (0.054, 0.070, 1.510), (0.092, 0.022, 1.498), (0.106, -0.030, 1.478),
         (0.110, -0.072, 1.440), (0.112, -0.098, 1.372)]
    n = 26
    Ls = catmull(L, n)
    Us = catmull(U, n)
    for sg in (1, -1):
        bm = bmesh.new()
        cols = 7
        grid = []
        for i in range(n):
            row = []
            for c in range(cols):
                t = c / (cols - 1)
                p = np.array(Ls[i]) * (1 - t) + np.array(Us[i]) * t
                # bombement du pli (le col se replie vers l'extérieur)
                bow = 0.0050 * math.sin(math.pi * t) * math.sin(math.pi * min(i / (n - 1), 1) * 0.5 + 0.2)
                nrm = np.array([p[0], p[1] - 0.012, 0.0])
                if np.linalg.norm(nrm) > 1e-6:
                    nrm /= np.linalg.norm(nrm)
                p = p + nrm * bow
                row.append(bm.verts.new((p[0] * sg, p[1], p[2])))
            grid.append(row)
        for i in range(n - 1):
            for c in range(cols - 1):
                if sg == 1:
                    bm.faces.new((grid[i][c], grid[i + 1][c], grid[i + 1][c + 1], grid[i][c + 1]))
                else:
                    bm.faces.new((grid[i][c], grid[i][c + 1], grid[i + 1][c + 1], grid[i + 1][c]))
        o = make_obj('collar', bm, M_SHIRT)
        modifier(o, 'SUBSURF', levels=2, render_levels=2)
        modifier(o, 'SOLIDIFY', thickness=0.0032, offset=0)
        smooth(o)
        objs.append(o)
    return objs


ARM_RINGS = [
    (0.172, 0.000, 1.360, 0.050, 0.050),
    (0.192, 0.000, 1.300, 0.046, 0.047),
    (0.216, 0.003, 1.230, 0.042, 0.044),
    (0.245, 0.005, 1.120, 0.036, 0.040),
    (0.255, 0.000, 1.020, 0.036, 0.037),
    (0.265, -0.012, 0.930, 0.0295, 0.0305),
    (0.272, -0.020, 0.875, 0.0205, 0.0262),
]


def build_arms():
    objs = []
    for sg in (1, -1):
        a = loft('arm', ARM_RINGS, M_SKIN, nseg=48, resample=26, mirror=sg)
        finish(a, sub=2)
        P = get_co(a)
        N = get_vn(a)
        P = P + N * (fbm(P * 70, 3, seed=31) * 0.0007)[:, None]
        # tendons / veines de l'avant-bras
        for k in range(8):
            tt = k / 7.0
            P = gauss_disp(P, N, (sg * (0.262 - 0.004 * tt), -0.012 - 0.008 * tt, 1.00 - 0.12 * tt),
                           (0.004, 0.03, 0.018), 0.0010, None)
        set_co(a, P)
        objs.append(a)
    return objs


def finger_obj(name, base, lengths, widths, curl, sg, thumb_dirs=None):
    cur = np.array(base, dtype=float)
    pts = [cur.copy()]
    ws = [widths[0]]
    rings = []
    segs = len(lengths)
    dirs = []
    for L, th in zip(lengths, curl):
        d = np.array([-math.sin(th), 0.0, -math.cos(th)])
        dirs.append(d)
    if thumb_dirs is not None:
        dirs = [np.array(t) / np.linalg.norm(t) for t in thumb_dirs]
    total = sum(lengths)
    acc = 0.0
    # anneaux : au début, au milieu et à la fin de chaque segment
    plist = [(cur.copy(), 0.0)]
    for L, d in zip(lengths, dirs):
        mid = cur + d * L * 0.5
        end = cur + d * L
        plist.append((mid, (acc + L * 0.5) / total))
        plist.append((end, (acc + L) / total))
        cur = end
        acc += L
    last_d = dirs[-1]
    plist.append((cur + last_d * widths[-1] * 0.75, 1.02))
    plist.append((cur + last_d * widths[-1] * 1.05, 1.08))
    for i, (p, s) in enumerate(plist):
        w = widths[0] + (widths[-1] - widths[0]) * min(s, 1.0)
        # renflement aux articulations
        joint = 1.0 + 0.07 * (i % 2 == 0 and 0 < i < len(plist) - 2)
        w *= joint
        if s > 1.0:
            w *= 0.62 if s < 1.05 else 0.28
        rings.append((p[0], p[1], p[2], w, w * 0.9))
    o = loft(name, rings, M_SKIN, nseg=16, ref=(0, 1, 0), mirror=sg, resample=len(rings) * 2)
    return o


def build_hands():
    objs = []
    for sg in (1, -1):
        palm = [
            (0.2720, -0.0200, 0.884, 0.0232, 0.0200),
            (0.2735, -0.0210, 0.860, 0.0290, 0.0198),
            (0.2752, -0.0225, 0.832, 0.0362, 0.0188),
            (0.2762, -0.0240, 0.802, 0.0410, 0.0168),
            (0.2764, -0.0247, 0.781, 0.0405, 0.0138),
            (0.2760, -0.0249, 0.770, 0.0330, 0.0100),
        ]
        pm = loft('palm', palm, M_SKIN, nseg=32, ref=(0, 1, 0), mirror=sg, resample=10)
        parts = [pm]
        y0 = -0.0245
        # doigts : index, majeur, annulaire, auriculaire
        specs = [
            (y0 - 0.0285, 0.784, (0.034, 0.024, 0.021), 0.0098, (0.14, 0.46, 0.78)),
            (y0 - 0.0095, 0.781, (0.037, 0.026, 0.022), 0.0096, (0.18, 0.52, 0.85)),
            (y0 + 0.0095, 0.783, (0.034, 0.025, 0.021), 0.0091, (0.24, 0.60, 0.92)),
            (y0 + 0.0275, 0.787, (0.027, 0.018, 0.018), 0.0079, (0.30, 0.68, 1.00)),
        ]
        for i, (yb, zb, lens, w, curl) in enumerate(specs):
            f = finger_obj('finger%d' % i, (0.2762, yb, zb), lens, (w, w * 0.78), curl, sg)
            parts.append(f)
        thumb = finger_obj('thumb', (0.2745, y0 - 0.031, 0.846), (0.037, 0.028, 0.023), (0.0125, 0.0092), (0, 0, 0), sg,
                           thumb_dirs=[(-0.10, -0.20, -0.97), (-0.22, -0.14, -0.96), (-0.36, -0.08, -0.93)])
        parts.append(thumb)
        for p in parts:
            finish(p, sub=2)
        for p in parts:
            P = get_co(p)
            N = get_vn(p)
            P = P + N * (fbm(P * 140, 3, seed=51) * 0.0005)[:, None]
            set_co(p, P)
        h = join(parts, 'hand')
        objs.append(h)
    return objs


def crease_shape(ang, f):
    w = min(max((f - 0.10) / 0.2, 0.0), 1.0)
    m = 1.0
    for a0 in (-math.pi / 2, math.pi / 2):
        d = abs(math.atan2(math.sin(ang - a0), math.cos(ang - a0)))
        m += 0.040 * math.exp(-(d / 0.10) ** 2) * w
    return m


def build_trousers():
    pel = [
        (0, 0.004, 0.740, 0.070, 0.070),
        (0, 0.004, 0.775, 0.150, 0.100),
        (0, 0.002, 0.840, 0.190, 0.121),
        (0, -0.001, 0.920, 0.195, 0.123),
        (0, -0.004, 0.985, 0.185, 0.120),
        (0, -0.006, 1.030, 0.181, 0.119),
        (0, -0.006, 1.044, 0.179, 0.118),
    ]
    tr = loft('trousers', pel, M_PANTS, nseg=96, p=2.2, resample=28)
    leg = [
        (0.086, 0.000, 0.930, 0.102, 0.118),
        (0.090, 0.002, 0.850, 0.106, 0.121),
        (0.091, 0.000, 0.740, 0.098, 0.108),
        (0.092, -0.003, 0.600, 0.085, 0.095),
        (0.093, -0.004, 0.500, 0.079, 0.088),
        (0.094, -0.002, 0.350, 0.072, 0.083),
        (0.095, 0.000, 0.200, 0.066, 0.080),
        (0.095, 0.006, 0.095, 0.061, 0.080),
        (0.095, 0.007, 0.078, 0.0605, 0.0795),
    ]
    for sg in (1, -1):
        l = loft('leg', leg, M_PANTS, nseg=96, resample=44, mirror=sg, shape=crease_shape)
        boolean(tr, l, 'UNION')
    # ourlets creux
    for sg in (1, -1):
        cav = loft('cav', [(0.095, 0.007, 0.060, 0.0540, 0.0730), (0.095, 0.007, 0.150, 0.0550, 0.0740)], M_PANTS,
                   nseg=64, mirror=sg, resample=4)
        boolean(tr, cav, 'DIFFERENCE')
    modifier(tr, 'REMESH', mode='VOXEL', voxel_size=0.0034, adaptivity=0.0, use_smooth_shade=True)
    modifier(tr, 'SMOOTH', factor=0.6, iterations=2)
    P = get_co(tr)
    N = get_vn(tr)
    lowfold = np.exp(-0.5 * ((P[:, 2] - 0.20) / 0.14) ** 2) + np.exp(-0.5 * ((P[:, 2] - 0.52) / 0.09) ** 2)
    P = P + N * (fbm(P * np.array([10, 10, 24]), 3, seed=61) * 0.0042 * (0.5 + lowfold))[:, None]
    P = P + N * (np.clip(vnoise(P * np.array([55, 55, 9]), 3), -1, 1) * 0.0016 * np.exp(-0.5 * ((P[:, 2] - 0.16) / 0.12) ** 2))[:, None]
    P = P + N * (fbm(P * 36, 2, seed=75) * 0.0008)[:, None]
    # poches de côté (fentes), plis de devant, braguette, plis de genou
    for sg in (1, -1):
        for k in range(10):
            tt = k / 9.0
            c = (sg * (0.118 + 0.040 * tt), -0.1, 1.035 - 0.14 * tt)
            P = gauss_disp(P, N, c, (0.0022, 0.06, 0.006), -0.0026, None)
        for k in range(8):
            tt = k / 7.0
            P = gauss_disp(P, N, (sg * 0.046, -0.12, 1.03 - 0.17 * tt), (0.007, 0.06, 0.022), 0.0026 * (1 - tt), None)
        for k in range(6):
            P = gauss_disp(P, N, (sg * 0.093, -0.085, 0.58 - 0.03 * k), (0.045, 0.04, 0.0045), 0.0012, None)
    for k in range(10):
        tt = k / 9.0
        P = gauss_disp(P, N, (0.0, -0.12, 1.03 - 0.13 * tt), (0.0014, 0.05, 0.010), -0.0013, None)
    set_co(tr, P)
    smooth(tr)
    return [tr]


def build_belt():
    objs = []
    rings = [(0, -0.006, 0.985, 0.1853, 0.1243), (0, -0.006, 1.021, 0.1850, 0.1240)]
    b = loft('belt', rings, M_BELT, nseg=96, p=2.2, resample=4, cap0=False, cap1=False)
    modifier(b, 'SOLIDIFY', thickness=0.0032, offset=-1)
    smooth(b)
    objs.append(b)
    # passants
    for ang in (-62, -28, 28, 62, 90, 118, 152, 180):
        pass
    # boucle
    yb = -0.006 - 0.1243 - 0.0010
    def box(sx, sy, sz, cx, cy, cz, mat):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co = (v.co.x * sx + cx, v.co.y * sy + cy, v.co.z * sz + cz)
        o = make_obj('buckle', bm, mat)
        modifier(o, 'BEVEL', width=0.0007, segments=2)
        smooth(o)
        return o
    wd, ht, bar = 0.042, 0.034, 0.0055
    cz = 1.002
    objs.append(box(wd, 0.0042, bar, 0, yb - 0.002, cz + ht / 2 - bar / 2, M_METAL))
    objs.append(box(wd, 0.0042, bar, 0, yb - 0.002, cz - ht / 2 + bar / 2, M_METAL))
    objs.append(box(bar, 0.0042, ht, -wd / 2 + bar / 2, yb - 0.002, cz, M_METAL))
    objs.append(box(bar, 0.0042, ht, wd / 2 - bar / 2, yb - 0.002, cz, M_METAL))
    objs.append(box(0.0022, 0.0030, ht - 0.002, 0.004, yb - 0.003, cz, M_METAL))
    # extrémité de la ceinture passant dans la boucle (côté +X)
    for k in range(40):
        pass
    return objs


def build_shoes():
    objs = []
    rings = [  # (y, zc, hw, hh)
        (0.090, 0.066, 0.032, 0.050),
        (0.072, 0.068, 0.037, 0.054),
        (0.042, 0.064, 0.040, 0.050),
        (0.005, 0.056, 0.041, 0.044),
        (-0.040, 0.049, 0.042, 0.037),
        (-0.090, 0.043, 0.0435, 0.031),
        (-0.140, 0.038, 0.0425, 0.025),
        (-0.180, 0.035, 0.037, 0.020),
        (-0.205, 0.0335, 0.028, 0.0155),
        (-0.219, 0.0330, 0.014, 0.0100),
    ]
    for sg in (1, -1):
        sh = loft('shoe', [(0, r[0], r[1], r[2], r[3]) for r in rings], M_SHOE, nseg=48, p=3.0, resample=26)
        finish(sh, sub=2)
        P = get_co(sh)
        N = get_vn(sh)
        P = P + N * (fbm(P * np.array([60, 60, 60]), 3, seed=81) * 0.0005)[:, None]
        for k in range(9):    # bout rapporté (rainure en arc)
            tt = (k - 4) / 4.0
            P = gauss_disp(P, N, (0.034 * tt, -0.125 + 0.018 * tt * tt, 0.045), (0.0055, 0.0020, 0.02), -0.0016, None)
        for k in range(7):    # plis de flexion
            P = gauss_disp(P, N, (0.0, -0.075 - 0.003 * k, 0.062 - 0.0), (0.035, 0.004, 0.004), 0.0014, None)
        P = gauss_disp(P, N, (0.0, 0.07, 0.07), (0.05, 0.012, 0.012), 0.0, None)
        set_co(sh, P)
        sole_r = [(0, 0.092, 0.0100, 0.034, 0.0100), (0, 0.045, 0.0095, 0.0450, 0.0095), (0, -0.035, 0.0090, 0.0470, 0.0090),
                  (0, -0.100, 0.0090, 0.0485, 0.0090), (0, -0.150, 0.0100, 0.0450, 0.0095), (0, -0.195, 0.0120, 0.0340, 0.0095),
                  (0, -0.216, 0.0135, 0.0200, 0.0090), (0, -0.226, 0.0140, 0.0080, 0.0070)]
        sole = loft('sole', sole_r, M_SHOE, nseg=48, p=3.6, resample=22)
        finish(sole, sub=2)
        heel = loft('heel', [(0, 0.094, 0.0240, 0.0325, 0.0140), (0, 0.062, 0.0240, 0.0395, 0.0140), (0, 0.030, 0.0240, 0.0400, 0.0140)],
                    M_SHOE, nseg=32, p=3.6, resample=8)
        finish(heel, sub=2)
        parts = [sh, sole, heel]
        bvh = bvh_of(sh)
        for k in range(5):    # lacets
            yy = 0.010 - k * 0.0215
            hit, nrm, idx, dist = bvh.ray_cast(V((0, yy, 0.25)), V((0, 0, -1)))
            if hit is None:
                continue
            bm = bmesh.new()
            bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=1.0)
            for v in bm.verts:
                v.co = (v.co.x * 0.0285, v.co.y * 0.0040, v.co.z * 0.0022)
            bmesh.ops.translate(bm, verts=bm.verts[:], vec=hit + V((0, 0, 0.0014)))
            o = make_obj('lace', bm, M_SHOE)
            smooth(o)
            parts.append(o)
        sock = loft('ankle', [(0, 0.012, 0.060, 0.036, 0.040), (0, 0.010, 0.150, 0.036, 0.040)], M_SKIN, nseg=24)
        finish(sock, sub=1)
        parts.append(sock)
        for p_ in parts:
            P = get_co(p_)
            ang = 0.17 * sg
            ca, sa = math.cos(ang), math.sin(ang)
            ax, ay = 0.0, 0.040
            x = P[:, 0] - ax
            y = P[:, 1] - ay
            P[:, 0] = x * ca - y * sa + ax + 0.095 * sg
            P[:, 1] = x * sa + y * ca + ay + 0.004
            set_co(p_, P)
        objs.append(join(parts, 'shoe'))
        smooth(objs[-1])
    return objs


def build_watch():
    objs = []
    # bracelet
    z0 = 0.903
    t = (z0 - 0.875) / (0.930 - 0.875)
    cx = 0.272 + (0.265 - 0.272) * t
    cy = -0.020 + (-0.012 + 0.020) * t
    rx = 0.0205 + (0.0295 - 0.0205) * t
    ry = 0.0262 + (0.0305 - 0.0262) * t
    band = loft('watch_band', [(cx, cy, z0 - 0.0085, rx + 0.0014, ry + 0.0014), (cx, cy, z0 + 0.0085, rx + 0.0014, ry + 0.0014)],
                M_BELT, nseg=48, cap0=False, cap1=False, resample=3)
    modifier(band, 'SOLIDIFY', thickness=0.0025, offset=-1)
    smooth(band)
    objs.append(band)
    cxc = cx + rx + 0.0045
    def cyl(r, depth, xoff, mat, seg=64):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=r, radius2=r, depth=depth)
        bmesh.ops.rotate(bm, verts=bm.verts[:], cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, 'Y'))
        bmesh.ops.translate(bm, verts=bm.verts[:], vec=(xoff, cy, z0))
        o = make_obj('watch', bm, mat)
        smooth(o)
        return o
    objs.append(cyl(0.0190, 0.0085, cxc, M_METAL))
    objs.append(cyl(0.0158, 0.0016, cxc + 0.0046, M_DIAL))   # cadran (tons clairs)
    # lunette
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=64, radius1=0.0192, radius2=0.0192, depth=0.0016)
    bmesh.ops.rotate(bm, verts=bm.verts[:], cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, 'Y'))
    bmesh.ops.translate(bm, verts=bm.verts[:], vec=(cxc + 0.0050, cy, z0))
    bz = make_obj('bezel', bm, M_METAL)
    modifier(bz, 'SOLIDIFY', thickness=0.0016, offset=-1)
    smooth(bz)
    objs.append(bz)
    # index et aiguilles
    for k in range(12):
        a = math.radians(30 * k)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        L = 0.0030 if k % 3 == 0 else 0.0018
        for v in bm.verts:
            v.co = (v.co.x * 0.0006, v.co.y * 0.0007, v.co.z * L)
        bmesh.ops.rotate(bm, verts=bm.verts[:], cent=(0, 0, 0), matrix=Matrix.Rotation(a, 3, 'X'))
        bmesh.ops.translate(bm, verts=bm.verts[:], vec=(cxc + 0.0058, cy - math.sin(a) * 0.0135 * 1.0, z0 + math.cos(a) * 0.0135))
        objs.append(make_obj('tick', bm, M_METAL))
    for ang, ln in ((math.radians(-60), 0.0085), (math.radians(80), 0.0125)):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        for v in bm.verts:
            v.co = (v.co.x * 0.0006, v.co.y * 0.0009, v.co.z * ln + ln / 2)
        bmesh.ops.rotate(bm, verts=bm.verts[:], cent=(0, 0, 0), matrix=Matrix.Rotation(ang, 3, 'X'))
        bmesh.ops.translate(bm, verts=bm.verts[:], vec=(cxc + 0.0060, cy, z0))
        objs.append(make_obj('hand', bm, M_METAL))
    # couronne
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.0023, radius2=0.0023, depth=0.004)
    bmesh.ops.rotate(bm, verts=bm.verts[:], cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, 'X'))
    bmesh.ops.translate(bm, verts=bm.verts[:], vec=(cxc, cy + 0.0205, z0 + 0.003))
    objs.append(make_obj('crown', bm, M_METAL))
    return objs


# ----------------------------------------------------------------------------
# scène, lumières, caméras, rendu
# ----------------------------------------------------------------------------

def setup_scene(samples=96):
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    sc.cycles.device = 'CPU'
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    try:
        sc.cycles.denoiser = 'OPENIMAGEDENOISE'
    except Exception:
        pass
    sc.cycles.max_bounces = 6
    sc.view_settings.view_transform = 'AgX'
    sc.render.film_transparent = False
    # sol
    # sol + cyclorama (le sol se courbe en mur au fond : pas de ligne d'horizon)
    bm = bmesh.new()
    prof = []
    for y in np.linspace(-40, 7, 24):
        prof.append((y, 0.0))
    Rr = 5.0
    for a in np.linspace(0, 90, 14)[1:]:
        prof.append((7 + Rr * math.sin(math.radians(a)), Rr * (1 - math.cos(math.radians(a)))))
    for z in np.linspace(Rr + 1, 40, 8):
        prof.append((7 + Rr, z))
    rows = []
    for (yy, zz) in prof:
        rows.append([bm.verts.new((x, yy, zz)) for x in (-40.0, 0.0, 40.0)])
    for i in range(len(rows) - 1):
        for j in range(2):
            bm.faces.new((rows[i][j], rows[i][j + 1], rows[i + 1][j + 1], rows[i + 1][j]))
    gm = mk_mat('ground', 0.30, 0.92)
    g = make_obj('ground', bm, gm)
    smooth(g)
    # monde
    w = bpy.data.worlds.new('world')
    sc.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes['Background']
    bg.inputs['Color'].default_value = (0.20, 0.20, 0.20, 1)
    bg.inputs['Strength'].default_value = 1.0

    def area(name, loc, energy, size, target=(0, 0, 0.9)):
        ld = bpy.data.lights.new(name, 'AREA')
        ld.energy = energy
        ld.size = size
        ld.shape = 'SQUARE'
        o = bpy.data.objects.new(name, ld)
        bpy.context.scene.collection.objects.link(o)
        o.location = loc
        d = V(target) - V(loc)
        o.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
        return o
    area('key', (-2.8, -3.6, 3.2), 330, 1.5)
    area('fill', (3.8, -2.8, 1.4), 60, 3.0)
    area('rim', (2.4, 3.4, 2.8), 260, 1.4)
    area('top', (0.0, -0.4, 4.2), 45, 3.0)
    return g


def make_camera(az, dist=5.2, z=0.92, f=85.0, tz=None, elev=0.0, ty=0.0, tx=0.0):
    cam = bpy.data.cameras.new('cam')
    cam.lens = f
    cam.clip_start = 0.05
    cam.clip_end = 100
    o = bpy.data.objects.new('cam', cam)
    bpy.context.scene.collection.objects.link(o)
    a = math.radians(az)
    tgt = V((tx, ty, z if tz is None else tz))
    pos = tgt + V((math.sin(a) * dist, -math.cos(a) * dist, math.tan(math.radians(elev)) * dist))
    o.location = pos
    o.rotation_euler = (tgt - pos).to_track_quat('-Z', 'Y').to_euler()
    return o


def render(path, cam, w, h, samples=None):
    sc = bpy.context.scene
    sc.camera = cam
    sc.render.resolution_x = w
    sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    if samples:
        sc.cycles.samples = samples
    sc.render.image_settings.file_format = 'PNG'
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)


HEAD_SCALE = 1.07


def scale_head_group(objs):
    """Agrandit uniformément la tête (et ses accessoires) autour du centre de la tête."""
    C = V(HEAD_C)
    S3 = Matrix.Diagonal((HEAD_SCALE * 1.03, HEAD_SCALE * 0.99, HEAD_SCALE, 1.0))
    M = Matrix.Translation(C) @ S3 @ Matrix.Translation(-C)
    for o in objs:
        if o is None:
            continue
        if o.name.startswith('eye'):
            o.location = M @ o.location
            o.scale = (HEAD_SCALE * 1.01,) * 3
            continue
        o.matrix_world = M @ o.matrix_world
        activate(o)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def build_all():
    reset()
    build_materials()
    head, _ = build_head()
    grp = [head]
    grp += build_eyes(head)
    grp += build_ears()
    grp.append(build_hair())
    grp.append(build_cap())
    grp += build_glasses()
    scale_head_group(grp)
    build_neck()
    build_torso()
    build_collar()
    build_arms()
    build_hands()
    build_trousers()
    build_belt()
    build_shoes()
    build_watch()


if __name__ == '__main__':
    mode = os.environ.get('MODE', 'test')
    build_all()
    setup_scene()
    os.makedirs(os.path.join(HERE, 'renders'), exist_ok=True)
    if mode == 'build':
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, 'elder_man.blend'))
    elif mode == 'face':
        out = os.path.join(HERE, 'renders')
        for nm, az in (('f0', 0), ('f35', -35), ('f90', -90)):
            render(os.path.join(out, nm + '.png'), make_camera(az, 0.9, 1.62, 85, tz=1.60, ty=-0.03), 560, 560, 40)
    elif mode == 'study':
        out = os.path.join(HERE, 'renders')
        for o in list(bpy.data.objects):
            if o.name.startswith(('cap', 'rim', 'lens', 'arm', 'hinge', 'bridge', 'pad')) and o.name != 'arm_x':
                pass
        for o in list(bpy.data.objects):
            if o.name.startswith(('cap', 'rim', 'lens', 'hinge', 'bridge', 'pad', 'hair')) or o.name == 'arm':
                bpy.data.objects.remove(o, do_unlink=True)
        for nm, az in (('s0', 0), ('s35', -38), ('s90', -90)):
            render(os.path.join(out, nm + '.png'), make_camera(az, 0.75, 1.6, 85, tz=1.58, ty=-0.045), 560, 640, 48)
    elif mode == 'views':
        out = os.path.join(HERE, 'renders')
        for nm, az in (('v0', 0), ('v35', -35), ('v90', -90), ('v180', 180)):
            render(os.path.join(out, nm + '.png'), make_camera(az, 5.2, 0.92, 85), 420, 640, 24)
    elif mode == 'detail':
        out = os.path.join(HERE, 'renders')
        render(os.path.join(out, 'd_hand.png'), make_camera(40, 0.8, 0.80, 85, ty=-0.02, tx=0.27), 560, 560, 40)
        render(os.path.join(out, 'd_shoes.png'), make_camera(-35, 1.4, 0.12, 85, elev=18), 560, 560, 40)
        render(os.path.join(out, 'd_torso.png'), make_camera(-30, 1.9, 1.25, 85), 560, 560, 40)
    elif mode == 'final':
        out = os.path.join(HERE, 'renders')
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, 'elder_man.blend'))
        render(os.path.join(out, 'hero.png'), make_camera(-28, 5.4, 0.90, 85), 1400, 2100, 160)
        views = (('front', 0), ('three_quarter', -35), ('side', -90), ('back', 180), ('three_quarter_r', 35))
        for nm, az in views:
            render(os.path.join(out, 'turn_%s.png' % nm), make_camera(az, 5.4, 0.90, 85), 800, 1400, 72)
        for nm, az in (('f0', 0), ('f35', -35), ('f90', -90)):
            render(os.path.join(out, 'head_%s.png' % nm), make_camera(az, 0.95, 1.62, 85, tz=1.60, ty=-0.03), 1000, 1000, 110)
        from PIL import Image
        W, H = 800, 1400
        sheet = Image.new('RGB', (W * 5, H + 1000 * 3 * W * 5 // (1000 * 3 * 5) if False else H + 1000 * 5 * W // 5 // 1000 * 0 + 0), (60, 60, 60))
        ims = [Image.open(os.path.join(out, 'turn_%s.png' % nm)) for nm, _ in views]
        heads = [Image.open(os.path.join(out, 'head_%s.png' % nm)).resize((W * 5 // 3, W * 5 // 3)) for nm in ('f0', 'f35', 'f90')]
        sheet = Image.new('RGB', (W * 5, H + heads[0].height), (60, 60, 60))
        for i, im in enumerate(ims):
            sheet.paste(im, (i * W, 0))
        for i, im in enumerate(heads):
            sheet.paste(im, (i * heads[0].width, H))
        sheet.save(os.path.join(out, 'character_sheet_clay.png'))
    elif mode == 'hero':
        out = os.path.join(HERE, 'renders')
        render(os.path.join(out, 'hero.png'), make_camera(-28, 5.4, 0.90, 85), 900, 1350, 64)
    elif mode == 'test':
        out = os.path.join(HERE, 'renders')
        render(os.path.join(out, 't_front.png'), make_camera(0), 500, 750, 24)
        render(os.path.join(out, 't_head.png'), make_camera(-25, 1.1, 1.6, 85, tz=1.60, ty=-0.02), 800, 800, 48)
