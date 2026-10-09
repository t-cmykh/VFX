"""Edge flow de la demi-coque dessine en curves, collees sur la geo.

Usage :
    python3 -I edgeflow_curves.py <in.abc> <objet> <out_dir>

Les curves ne sont PAS converties en geometrie. Elles sont creees en Bezier,
chaque point est colle sur la surface d'origine (rayon vers la coque ou section
par un plan), et un controle d'ecart a la surface est imprime a la fin.

Reseau dessine (cote bordage, vue de cote = y, z) :
  - BORDURES      contour de la coque + bord de chaque ouverture (existant)
  - HORIZONTALES  lignes longitudinales qui suivent les etages du pont
                  (rangees de fenetres arriere / milieu / avant) avec rampes
  - VERTICALES    une ligne de chaque cote de chaque fenetre, du pont au bouchain
  - CADRES        boucles d'appui autour des ouvertures atypiques (grande decoupe,
                  fenetre haute, petite fenetre)
  - HUBLOTS       deux anneaux concentriques par hublot
  - SECTIONS      couples transversaux (bordage + bouchain + plancher) a l'avant
  - PLANCHER      lignes longitudinales sur le plancher
"""
import math
import os
import sys

import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

# --- reglages de dessin -------------------------------------------------------
MARGE = 24.0                       # distance entre le bord d'une fenetre et sa ligne d'appui
PORT_R = 2.0                       # rayon de l'anneau exterieur d'un hublot (x rayon octogone)
STEP = 8.0                         # pas d'echantillonnage des lignes (unites)
TOL = 0.6                          # tolerance de simplification des curves (unites)
RAMPES = ((-720.0, -590.0), (2380.0, 2432.0))   # marches du pont : arriere->milieu, milieu->avant
# hauteurs z des lignes horizontales par zone (arriere, milieu, avant)
HORIZ = {
    "H1_haut_fenetres": (465.0, 590.0, 479.0),
    "H2_bas_fenetres": (366.0, 471.0, 386.0),
    "H3_bas_hublots": (276.0, 276.0, 276.0),
    "H4": (190.0, 190.0, 190.0),
    "H5_bouchain": (126.0, 126.0, 126.0),
}
SECTIONS_AVANT = [3500, 3750, 4000, 4250, 4500, 4750, 5000, 5150]
SECTIONS_PLANCHER = list(range(-2200, 4100, 400))
PLANCHER_X = [-1950.0, -1800.0, -1650.0]


def smooth(a, b, y):
    t = max(0.0, min(1.0, (y - a) / (b - a)))
    return t * t * (3 - 2 * t)


def zone_value(vals, y):
    w1 = smooth(*RAMPES[0], y)
    w2 = smooth(*RAMPES[1], y)
    return vals[0] + (vals[1] - vals[0]) * w1 + (vals[2] - vals[1]) * w2


# ------------------------------------------------------------------ geometrie
class Hull:
    def __init__(self, bm):
        self.bvh = BVHTree.FromBMesh(bm)
        self.bm = bm

    def ray_x(self, y, z):
        """Point de la coque vu depuis l'exterieur (cote -x), None dans un trou."""
        hit = self.bvh.ray_cast(Vector((-3500.0, y, z)), Vector((1, 0, 0)))
        return hit[0] if hit[0] is not None else None

    def ray_down(self, x, y):
        hit = self.bvh.ray_cast(Vector((x, y, 2500.0)), Vector((0, 0, -1)))
        return hit[0] if hit[0] is not None else None

    def snap(self, p):
        return self.bvh.find_nearest(p)[0]

    def section(self, y):
        """Courbe d'intersection de la coque avec le plan y = const, ordonnee du pont vers le bas."""
        bm = self.bm.copy()
        res = bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
                                     plane_co=Vector((0, y, 0)), plane_no=Vector((0, 1, 0)))
        cut = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
        chains = chains_from_edges(cut)
        out = [[v.co.copy() for v in c] for c in chains]
        bm.free()
        return out


def chains_from_edges(edges):
    adj = {}
    for e in edges:
        a, b = e.verts
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen, chains = set(), []
    starts = [v for v, n in adj.items() if len(n) == 1] + list(adj)
    for s in starts:
        if s in seen:
            continue
        chain, cur, prev = [s], s, None
        seen.add(s)
        while True:
            nxt = [n for n in adj[cur] if n is not prev and n not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            chain.append(cur)
            seen.add(cur)
        if len(chain) > 2:
            chains.append(chain)
    return chains


def chine_cut(chain):
    """Coupe une section au bouchain : avant le premier grand segment du plancher."""
    for i in range(len(chain) - 1):
        if (chain[i] - chain[i + 1]).length > 150:
            return chain[: i + 1], chain[i:]
    return chain, []


def order_top_down(chain):
    return chain if chain[0].z >= chain[-1].z else chain[::-1]


# ------------------------------------------------------------------ curves
def rdp(pts, tol):
    """Douglas-Peucker sur une liste de Vector."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    d = (b - a)
    L = d.length
    best, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        dist = (pts[i] - a).length if L == 0 else (pts[i] - a).cross(d).length / L
        if dist > best:
            best, idx = dist, i
    if best <= tol:
        return [a, b]
    return rdp(pts[: idx + 1], tol)[:-1] + rdp(pts[idx:], tol)


def handles(P, cyclic):
    """Poignees Bezier : tangentes lissees, coins vifs gardes droits."""
    n = len(P)
    H = []
    for i in range(n):
        if not cyclic and i in (0, n - 1):
            nb = P[1] if i == 0 else P[n - 2]
            v = (nb - P[i]) / 3
            H.append((P[i] - v, P[i] + v) if i else (P[i] + v * 0, P[i] + v))
            if i == n - 1:
                H[-1] = (P[i] + v, P[i] - v * 0)
            continue
        prev, nxt = P[(i - 1) % n], P[(i + 1) % n]
        a, b = P[i] - prev, nxt - P[i]
        la, lb = a.length, b.length
        if la < 1e-6 or lb < 1e-6:
            H.append((P[i], P[i]))
            continue
        if a.angle(b) > math.radians(35):
            H.append((P[i] - a / 3, P[i] + b / 3))
            continue
        d = (nxt - prev).normalized()
        H.append((P[i] - d * la / 3, P[i] + d * lb / 3))
    return H


def dist_poly(q, pts, a, b):
    """Distance de q a la polyligne pts[a..b] (b peut valoir len(pts) pour reboucler)."""
    best = 1e30
    for k in range(a, b):
        p0, p1 = pts[k % len(pts)], pts[(k + 1) % len(pts)]
        d = p1 - p0
        L2 = d.length_squared
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, (q - p0).dot(d) / L2))
        best = min(best, (p0 + d * t - q).length)
    return best


def seg_error(P, H, i, j, pts, a, b, res=6):
    from mathutils.geometry import interpolate_bezier
    return max(dist_poly(q, pts, a, b) for q in interpolate_bezier(P[i], H[i][1], H[j][0], P[j], res))


def fit(pts, cyclic, hull, tol):
    """Points de controle : on part des extremites et on ajoute le point le plus mal servi
    tant que la curve s'ecarte du trace colle sur la surface de plus de tol."""
    n = len(pts)
    idx = [0, n - 1] if not cyclic else [0, n // 4, n // 2, 3 * n // 4]
    while True:
        P = [pts[k] for k in idx]
        H = handles(P, cyclic)
        worst, where = 0.0, None
        m = len(idx)
        for s in range(m if cyclic else m - 1):
            t = (s + 1) % m
            a, b = idx[s], (idx[t] if t else n)
            e = seg_error(P, H, s, t, pts, a, b)
            if e > worst and b - a >= 2:
                worst, where = e, (s, (a + b) // 2)
        if worst <= tol or where is None:
            return idx
        idx.insert(where[0] + 1, where[1])


def make_curve(coll, name, pts, cyclic=False, color=(1, 1, 1, 1), hull=None, tol=0.8):
    if len(pts) < 3:
        return None
    idx = fit(pts, cyclic, hull, tol)
    P = [pts[k] for k in idx]
    H = handles(P, cyclic)
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = '3D'
    sp = cu.splines.new('BEZIER')
    sp.bezier_points.add(len(P) - 1)
    for bp, p, (hl, hr) in zip(sp.bezier_points, P, H):
        bp.handle_left_type = bp.handle_right_type = 'FREE'
        bp.co = p
        bp.handle_left, bp.handle_right = hl, hr
    sp.use_cyclic_u = cyclic
    ob = bpy.data.objects.new(name, cu)
    ob.color = color
    coll.objects.link(ob)
    return ob


def curve_error(ob, hull, res=6):
    """Ecart max entre la curve evaluee et la surface d'origine."""
    from mathutils.geometry import interpolate_bezier
    sp = ob.data.splines[0]
    bps = sp.bezier_points
    n = len(bps)
    pairs = [(bps[i], bps[(i + 1) % n]) for i in range(n if sp.use_cyclic_u else n - 1)]
    worst = 0.0
    for a, b in pairs:
        for p in interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, res):
            worst = max(worst, (hull.snap(p) - p).length)
    return worst


# ------------------------------------------------------------------ construction
def line_y(hull, zfun, y0, y1):
    """Polylines collees sur la coque : z = zfun(y), coupees aux trous et aux bords."""
    segs, cur = [], []
    y = y0
    while y <= y1:
        p = hull.ray_x(y, zfun(y))
        if p is None:
            if len(cur) > 2:
                segs.append(cur)
            cur = []
        else:
            if cur and (p - cur[-1]).length > 4 * STEP:      # saut de surface : on coupe
                if len(cur) > 2:
                    segs.append(cur)
                cur = []
            cur.append(p)
        y += STEP
    if len(cur) > 2:
        segs.append(cur)
    return segs


def deck_profile(boundary):
    """z du bord haut en fonction de y (cote bordage)."""
    pts = sorted((v.co.y, v.co.z) for v in boundary if v.co.x < -1650 and v.co.z > 300)
    ys = [p[0] for p in pts]
    zs = [p[1] for p in pts]
    def f(y):
        if y < ys[0] or y > ys[-1]:
            return None
        k = max(i for i in range(len(ys)) if ys[i] <= y)
        k2 = min(k + 1, len(ys) - 1)
        w = 0 if ys[k2] == ys[k] else (y - ys[k]) / (ys[k2] - ys[k])
        return zs[k] + (zs[k2] - zs[k]) * w
    return f


def offset_loop(pts2d, d):
    """Decale un polygone (liste de (y, z)) vers l'exterieur de d, coins en onglet."""
    n = len(pts2d)
    cy = sum(p[0] for p in pts2d) / n
    cz = sum(p[1] for p in pts2d) / n
    out = []
    for i in range(n):
        a, b, c = pts2d[i - 1], pts2d[i], pts2d[(i + 1) % n]
        e1 = (b[0] - a[0], b[1] - a[1])
        e2 = (c[0] - b[0], c[1] - b[1])
        n1 = (e1[1], -e1[0])
        n2 = (e2[1], -e2[0])
        l1 = math.hypot(*n1) or 1
        l2 = math.hypot(*n2) or 1
        nx = n1[0] / l1 + n2[0] / l2
        nz = n1[1] / l1 + n2[1] / l2
        ln = math.hypot(nx, nz) or 1
        nx, nz = nx / ln, nz / ln
        if nx * (b[0] - cy) + nz * (b[1] - cz) < 0:      # la normale doit pointer vers l'exterieur
            nx, nz = -nx, -nz
        out.append((b[0] + nx * d, b[1] + nz * d))
    return out


def resample_closed(pts, n):
    cum = [0.0]
    for i in range(len(pts)):
        cum.append(cum[-1] + math.dist(pts[i], pts[(i + 1) % len(pts)]))
    out = []
    for k in range(n):
        t = cum[-1] * k / n
        j = max(i for i in range(len(pts)) if cum[i] <= t)
        a, b = pts[j], pts[(j + 1) % len(pts)]
        w = (t - cum[j]) / ((cum[j + 1] - cum[j]) or 1)
        out.append((a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w))
    return out


def boundary_loops(bm):
    seen, loops = set(), []
    for e in bm.edges:
        if e.is_boundary and e not in seen:
            comp, st = [], [e]
            seen.add(e)
            while st:
                x = st.pop()
                comp.append(x)
                for v in x.verts:
                    for y in v.link_edges:
                        if y.is_boundary and y not in seen:
                            seen.add(y)
                            st.append(y)
            adj = {}
            for e2 in comp:
                a, b = e2.verts
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)
            if any(len(n) != 2 for n in adj.values()):
                continue
            start = next(iter(adj))
            order, prev, cur = [start], start, adj[start][0]
            while cur is not start:
                order.append(cur)
                nxt = adj[cur][0] if adj[cur][0] is not prev else adj[cur][1]
                prev, cur = cur, nxt
            loops.append(order)
    return loops


def find_portholes(bm):
    """Centres et rayons des hublots : groupes de 4 sommets de valence 3 en cercle."""
    P = [v.co.copy() for v in bm.verts if not v.is_boundary and len(v.link_edges) == 3]
    used, out = set(), []
    for i, p in enumerate(P):
        if i in used:
            continue
        grp = [j for j, q in enumerate(P) if j not in used and (q - p).length < 45]
        used |= set(grp)
        if len(grp) >= 4:
            c = sum((P[j] for j in grp), Vector()) / len(grp)
            r = sum((P[j] - c).length for j in grp) / len(grp)
            out.append((c.y, c.z, r))
    return sorted(out)


def main(abc, name, out_dir):
    bpy.ops.wm.alembic_import(filepath=os.path.abspath(abc))
    ob = bpy.data.objects[name]
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    hull = Hull(bm)
    loops = boundary_loops(bm)
    big = max(loops, key=len)
    openings = [l for l in loops if l is not big]
    deck = deck_profile(big)
    y_min = min(v.co.y for v in big)
    y_max = max(v.co.y for v in big)

    root = bpy.data.collections.new("EDGEFLOW")
    bpy.context.scene.collection.children.link(root)
    cols = {}
    palette = {
        "BORDURES": (1.0, 0.25, 0.25, 1), "HORIZONTALES": (0.2, 0.8, 1.0, 1), "VERTICALES": (1.0, 0.8, 0.1, 1),
        "CADRES": (0.4, 1.0, 0.4, 1), "HUBLOTS": (1.0, 0.5, 1.0, 1), "SECTIONS": (1.0, 0.55, 0.15, 1),
        "PLANCHER": (0.6, 0.6, 1.0, 1),
    }
    for k in palette:
        c = bpy.data.collections.new(k)
        root.children.link(c)
        cols[k] = c
    made = []

    def add(cat, nm, pts, cyclic=False):
        o = make_curve(cols[cat], nm, pts, cyclic, palette[cat], hull)
        if o:
            made.append(o)

    # 1. bordures (existantes)
    add("BORDURES", "bordure_coque", [v.co.copy() for v in big], True)
    info = []
    for i, l in enumerate(sorted(openings, key=lambda l: sum(v.co.y for v in l) / len(l))):
        pts = [v.co.copy() for v in l]
        add("BORDURES", "bord_ouverture_%02d" % i, pts, True)
        ys = [p.y for p in pts]
        zs = [p.z for p in pts]
        info.append((i, len(l), min(ys), max(ys), min(zs), max(zs), l))

    # 2. lignes horizontales (rampes aux marches du pont), coupees aux ouvertures
    for nm, vals in HORIZ.items():
        for k, seg in enumerate(line_y(hull, lambda y, v=vals: zone_value(v, y), y_min, y_max)):
            add("HORIZONTALES", "%s_%d" % (nm, k), seg)
    # ligne mediane sous le pont a l'avant (la ou l'ecart pont / fenetres s'ouvre)
    def h0(y):
        d = deck(y)
        return None if d is None else 0.5 * (d + zone_value(HORIZ["H1_haut_fenetres"], y))
    segs, cur = [], []
    y = 2392.0
    while y <= y_max:
        z = h0(y)
        p = hull.ray_x(y, z) if z else None
        if p is None:
            if len(cur) > 2:
                segs.append(cur)
            cur = []
        else:
            cur.append(p)
        y += STEP
    if len(cur) > 2:
        segs.append(cur)
    for k, s in enumerate(segs):
        add("HORIZONTALES", "H0_sous_pont_%d" % k, s)

    # 3. verticales : une de chaque cote de chaque fenetre (celles qui se recouvrent sont fusionnees)
    edges = []
    for (i, n, y0, y1, z0, z1, l) in info:
        if z1 - z0 > 180 and (y1 - y0) > 600:       # grande decoupe : traitee en cadre
            edges += [y0 - MARGE, y1 + MARGE]
        elif z0 > 300:
            edges += [y0 - MARGE, y1 + MARGE]
    edges.sort()
    merged, cur = [], [edges[0]]
    for e in edges[1:]:
        if e - cur[-1] < 30:
            cur.append(e)
        else:
            merged.append(sum(cur) / len(cur))
            cur = [e]
    merged.append(sum(cur) / len(cur))
    for k, y in enumerate(merged):
        for sec in hull.section(y):
            top, _ = chine_cut(order_top_down(sec))
            add("VERTICALES", "V_y%+05d" % y, top)

    # 4. cadres d'appui autour des ouvertures atypiques (decoupe, fenetre haute, petite fenetre)
    for (i, n, y0, y1, z0, z1, l) in info:
        atypique = (z1 - z0 > 120) or n == 6
        if not atypique:
            continue
        poly = [(v.co.y, v.co.z) for v in l]
        loop = resample_closed(offset_loop(poly, MARGE), 60)
        pts = [hull.ray_x(a, b) for a, b in loop]
        if all(p is not None for p in pts):
            add("CADRES", "cadre_ouverture_%02d" % i, pts, True)

    # 5. hublots : cercle de l'octogone existant + anneau exterieur
    for k, (cy, cz, r) in enumerate(find_portholes(bm)):
        for tag, rad in (("int", r), ("ext", r * PORT_R)):
            pts = []
            for j in range(32):
                a = 2 * math.pi * j / 32
                pts.append(hull.ray_x(cy + rad * math.cos(a), cz + rad * math.sin(a)))
            if all(p is not None for p in pts):
                add("HUBLOTS", "hublot_%02d_%s" % (k, tag), pts, True)

    # 6. sections transversales a l'avant (bordage + etrave) et couples du plancher
    for y in SECTIONS_AVANT:
        for sec in hull.section(y):
            add("SECTIONS", "S_avant_y%+05d" % y, order_top_down(sec))
    for y in SECTIONS_PLANCHER:
        for sec in hull.section(y):
            _, floor = chine_cut(order_top_down(sec))
            if len(floor) > 2:
                add("SECTIONS", "S_plancher_y%+05d" % y, floor)

    # 7. plancher : lignes longitudinales
    for x in PLANCHER_X:
        pts = []
        y = -2250.0
        while y <= 4000.0:
            p = hull.ray_down(x, y)
            if p is not None and p.z < 120:
                pts.append(p)
            y += 40.0
        add("PLANCHER", "P_x%+05d" % x, pts)

    # controles
    errs = sorted(((curve_error(o, hull), o.name) for o in made), reverse=True)
    worst = errs[0][0]
    for e, nm in errs[:6]:
        print("   ecart %.2f  %s" % (e, nm))
    print("curves :", len(made), "| ecart max a la surface : %.2f" % worst)
    for k, c in cols.items():
        print("  %-13s %d" % (k, len(c.objects)))

    hull_ob = ob
    hull_ob.display_type = 'WIRE'
    hull_ob.hide_render = True
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, "coque_edgeflow_curves.blend"))


if __name__ == "__main__":
    main(*sys.argv[1:4])
