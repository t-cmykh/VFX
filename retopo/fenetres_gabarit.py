"""Gabarit propre autour des fenetres de la demi-coque (version automatique).

Usage :
    python3 -I fenetres_gabarit.py <in.abc> <objet> <out_dir> [y_fenetre ...]

Meme gabarit que fenetre_gabarit.py, applique a chaque fenetre qui a un defaut
(triangle / ngon dans la couronne qui entoure le rim) :
  1. on cherche le bloc de grille autour de la fenetre (rectangle de faces dont
     le contour est une boucle simple de points reguliers) ;
  2. on coupe le rim par des boucles verticales pour que le rim ait autant de
     points que le contour du bloc ;
  3. on relie le rim au contour 1 pour 1 : une seule couronne de quads.
Les fenetres sans defaut, ou dont aucun bloc valide n'existe, ne sont pas
touchees (elles sont listees a la fin).
"""
import itertools
import math
import os
import sys

import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

MARGINS = [14, 20, 26, 32, 40, 48, 56, 64, 72, 84]
LONG_EDGE = 10.0          # un cote "long" du rim (les chanfreins des coins font ~2)


# ---------------------------------------------------------------- utilitaires
def order_cycle(edges):
    adj = {}
    for e in edges:
        a, b = e.verts
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    if not adj or any(len(n) != 2 for n in adj.values()):
        return None
    start = next(iter(adj))
    order, prev, cur = [start], start, adj[start][0]
    while cur is not start:
        order.append(cur)
        nxt = adj[cur][0] if adj[cur][0] is not prev else adj[cur][1]
        prev, cur = cur, nxt
        if len(order) > len(adj):
            return None
    return order if len(order) == len(adj) else None


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
            cyc = order_cycle(comp)
            if cyc:
                loops.append(cyc)
    return loops


def region_boundary(faces):
    return [e for f in faces for e in f.edges
            if not e.is_boundary and sum(1 for g in e.link_faces if g in faces) == 1]


def rim_faces(loop):
    """Couronnes fines autour de l'ouverture (autant de quads que de points)."""
    kept, frontier, seen_v = set(), set(loop), set(loop)
    for _ in range(3):
        ring = {f for v in frontier for f in v.link_faces} - kept
        if len(ring) != len(loop) or any(len(f.verts) != 4 for f in ring):
            break
        kept |= ring
        frontier = {v for f in ring for v in f.verts} - seen_v
        seen_v |= frontier
    return kept


def ccw(pts_or_verts, n, key=lambda x: x):
    pts = [key(v) for v in pts_or_verts]
    c = sum(pts, Vector()) / len(pts)
    a = Vector()
    for i, p in enumerate(pts):
        a += (p - c).cross(pts[(i + 1) % len(pts)] - c)
    return list(pts_or_verts) if a.dot(n) > 0 else list(pts_or_verts)[::-1]


def seg_param(p, a, b):
    d = b - a
    t = (p - a).dot(d) / d.length_squared
    return t, (a + d * max(0.0, min(1.0, t)) - p).length


def strip_through(e0, side):
    g, edges, strip = side, [e0], []
    while True:
        e = edges[-1]
        opp = [x for x in g.edges if not set(x.verts) & set(e.verts)]
        edges.append(opp[0])
        strip.append(g)
        if opp[0].is_boundary:
            return edges, strip
        g = [f for f in opp[0].link_faces if f is not g][0]


def loop_cut_at(snap, e0, kept, point):
    """Coupe e0 et le couloir du rim au niveau du point (projete sur e0)."""
    a0, b0 = e0.verts[0].co, e0.verts[1].co
    d = (b0 - a0).normalized()
    s = point.dot(d)
    side = [f for f in e0.link_faces if f in kept][0]
    edges, strip = strip_through(e0, side)
    mids = []
    for e in edges:
        a, b = sorted(e.verts, key=lambda v: v.co.dot(d))
        fac = (s - a.co.dot(d)) / (b.co.dot(d) - a.co.dot(d))
        _, v = bmesh.utils.edge_split(e, a, max(0.02, min(0.98, fac)))
        v.co = snap(v.co)
        mids.append(v)
    for g, a, b in zip(strip, mids, mids[1:]):
        bmesh.utils.face_split(g, a, b)


def quad_ok(P, n):
    """(inversee, pincee) pour un quad de positions P."""
    nrm = Vector()
    for i in range(4):
        nrm += P[i].cross(P[(i + 1) % 4])
    if nrm.dot(n) <= 0:
        return True, True
    for i in range(4):
        u, w = (P[i - 1] - P[i]).normalized(), (P[(i + 1) % 4] - P[i]).normalized()
        ang = math.degrees(math.acos(max(-1, min(1, u.dot(w)))))
        if ang > 165 or ang < 25:
            return False, True
    return False, False


def best_shift(A, B):
    N = len(A)
    costs = [sum((A[i] - B[(i + s) % N]).length_squared for i in range(N)) for s in range(N)]
    s = min(range(N), key=lambda s: costs[s])
    return s, costs[s]


# ---------------------------------------------------------------- fenetre
class Window:
    def __init__(self, bm, snap, loop, forbidden_extra):
        self.bm, self.snap, self.loop0 = bm, snap, loop
        self.c = sum((v.co for v in loop), Vector()) / len(loop)
        self.forbidden_extra = forbidden_extra

    def fresh(self):
        self.bm.verts.index_update()
        self.bm.faces.ensure_lookup_table()
        cands = [l for l in boundary_loops(self.bm)
                 if len(l) < 30 and (sum((v.co for v in l), Vector()) / len(l) - self.c).length < 30]
        loop = min(cands, key=lambda l: (sum((v.co for v in l), Vector()) / len(l) - self.c).length)
        return loop, rim_faces(loop)

    def plan(self):
        """Choisit le bloc et les coupes. Renvoie dict ou message d'echec."""
        bm = self.bm
        loop, kept = self.fresh()
        if not kept:
            return "pas de rim"
        ring1 = {f for v in {v for f in kept for v in f.verts} for f in v.link_faces}
        if all(len(f.verts) == 4 for f in ring1):
            return "rien a faire"
        inner0 = order_cycle(region_boundary(kept))
        n = sum((f.normal for f in kept), Vector()).normalized()
        inner0 = ccw(inner0, n, key=lambda v: v.co)
        ys = [v.co.y for v in inner0]
        zs = [v.co.z for v in inner0]
        loopset = set(loop)
        forbidden = {f for v in bm.verts if v.is_boundary and v not in loopset for f in v.link_faces}
        forbidden |= self.forbidden_extra
        forbidden -= kept
        near = [f for f in bm.faces if abs(f.calc_center_median().y - self.c.y) < 220
                and abs(f.calc_center_median().z - self.c.z) < 220 and f not in kept]
        ctr = {f: f.calc_center_median() for f in near}
        bad = {f for f in ring1 if len(f.verts) != 4}
        seen_blocks, best = {}, None
        rimlong = []
        for i in range(len(inner0)):
            a, b = inner0[i], inner0[(i + 1) % len(inner0)]
            if (a.co - b.co).length > LONG_EDGE:
                rimlong.append((a, b))
        for ml, mr, mt, mb in itertools.product(MARGINS, repeat=4):
            y0, y1, z0, z1 = min(ys) - ml, max(ys) + mr, min(zs) - mb, max(zs) + mt
            block = frozenset(f for f in near if y0 < ctr[f].y < y1 and z0 < ctr[f].z < z1)
            if block in seen_blocks:
                continue
            seen_blocks[block] = None
            if not bad <= block or block & forbidden:
                continue
            full = set(block) | kept
            outer = order_cycle(region_boundary(full))
            if outer is None or set(outer) & set(inner0):
                continue
            if any(len(g.verts) != 4 for v in outer for g in v.link_faces if g not in full):
                continue
            res = self.score_block(full, kept, outer, inner0, rimlong, n)
            if res and (best is None or res["score"] < best["score"]):
                best = res
                best["box"] = (y0, y1, z0, z1)
        if best is None:
            return "aucun bloc valide"
        best["n"] = n
        return best

    def score_block(self, full, kept, outer, inner0, rimlong, n):
        outer = ccw(outer, n, key=lambda v: v.co)
        N_in, N_out = len(inner0), len(outer)
        c = N_out - N_in
        if c < 0 or c > 5:
            return None
        # candidats de coupe : projection des points du bloc sur le cote long le plus proche
        cands = []
        for k, b in enumerate(outer):
            best = None
            for (a, bb) in rimlong:
                t, d = seg_param(b.co, a.co, bb.co)
                if 0.06 < t < 0.94 and (best is None or d < best[0]):
                    best = (d, a, bb, t)
            if best:
                cands.append((k, best[1], best[2], best[3]))
        if c > len(cands):
            return None
        inner_pos = [v.co for v in inner0]
        idx = {v: i for i, v in enumerate(inner0)}
        out_pos = [v.co for v in outer]
        res = None
        for sub in itertools.combinations(cands, c):
            ins = {}
            for (k, a, bb, t) in sub:
                ins.setdefault((idx[a], idx[bb]), []).append((t, a.co.lerp(bb.co, t)))
            if any(len(v) > 2 for v in ins.values()):
                pass
            pos = []
            for i in range(N_in):
                pos.append(inner_pos[i])
                j = (i + 1) % N_in
                for (t, p) in sorted(ins.get((i, j), [])):
                    pos.append(p)
                for (t, p) in sorted(ins.get((j, i), []), reverse=True):
                    pos.append(p)
            if len(pos) != N_out:
                continue
            s, cost = best_shift(pos, out_pos)
            bad_q = 0
            for i in range(N_out):
                P = [pos[i], out_pos[(i + s) % N_out], out_pos[(i + s + 1) % N_out], pos[(i + 1) % N_out]]
                inv, pin = quad_ok(P, n)
                bad_q += 2 * inv + pin
            r = {"sub": sub, "cost": cost, "bad_q": bad_q, "outer": outer}
            if res is None or (bad_q, cost) < (res["bad_q"], res["cost"]):
                res = r
        if res is None:
            return None
        # poles crees sur le contour : valence apres = aretes qui restent + 1 rayon
        poles = 0
        for b in outer:
            if b.is_boundary:
                continue
            keep_edges = sum(1 for e in b.link_edges if any(g not in full for g in e.link_faces))
            poles += (keep_edges + 1) != 4
        res["poles"] = poles
        res["score"] = (res["bad_q"], poles, len(full), res["cost"])
        return res

    def apply(self, plan, protected):
        bm, snap = self.bm, self.snap
        n = plan["n"]
        for (k, a, bb, t) in plan["sub"]:
            point = a.co.lerp(bb.co, t)
            loop, kept = self.fresh()
            best = None
            for e in region_boundary(kept):
                if e.calc_length() <= LONG_EDGE:
                    continue
                tt, d = seg_param(point, e.verts[0].co, e.verts[1].co)
                if 0.0 < tt < 1.0 and (best is None or d < best[0]):
                    best = (d, e)
            loop_cut_at(snap, best[1], kept, point)
        loop, kept = self.fresh()
        inner = order_cycle(region_boundary(kept))
        y0, y1, z0, z1 = plan["box"]
        near = [f for f in bm.faces if abs(f.calc_center_median().y - self.c.y) < 220
                and abs(f.calc_center_median().z - self.c.z) < 220]
        block = {f for f in near if y0 < f.calc_center_median().y < y1 and z0 < f.calc_center_median().z < z1} | kept
        outer = order_cycle(region_boundary(block))
        assert len(inner) == len(outer), (len(inner), len(outer))
        inner, outer = ccw(inner, n, key=lambda v: v.co), ccw(outer, n, key=lambda v: v.co)
        old = block - kept
        keep_v = set(inner) | set(outer)
        verts = {v for f in old for v in f.verts}
        bmesh.ops.delete(bm, geom=list(old), context='FACES_ONLY')
        bmesh.ops.delete(bm, geom=[v for v in verts if v.is_valid and v not in keep_v and not v.link_faces], context='VERTS')
        N = len(inner)
        s, _ = best_shift([v.co for v in inner], [v.co for v in outer])
        new = [bm.faces.new([inner[i], outer[(i + s) % N], outer[(i + s + 1) % N], inner[(i + 1) % N]]) for i in range(N)]
        for f in new:
            f.normal_update()
            if f.normal.dot(n) < 0:
                f.normal_flip()
        protected |= set(new) | kept
        return len(old), N


# ---------------------------------------------------------------- principal
def main(abc, name, out_dir, only):
    bpy.ops.wm.alembic_import(filepath=os.path.abspath(abc))
    ob = bpy.data.objects[name]
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    ref = bmesh.new()
    ref.from_mesh(ob.data)
    bvh = BVHTree.FromBMesh(ref)

    def snap(co):
        hit = bvh.find_nearest(co)
        return hit[0] if hit[0] is not None else co

    loops = [l for l in boundary_loops(bm) if len(l) < 30]
    loops.sort(key=lambda l: sum(v.co.y for v in l) / len(l))
    rims = {tuple(l): rim_faces(l) for l in loops}
    protected, report = set(), []
    for l in loops:
        yc = sum(v.co.y for v in l) / len(l)
        if only and not any(abs(yc - y) < 30 for y in only):
            continue
        others = set().union(*[r for k, r in rims.items() if k != tuple(l)]) if len(rims) > 1 else set()
        w = Window(bm, snap, l, protected | others)
        plan = w.plan()
        tag = "%2d pts  y=%6.0f z=%4.0f" % (len(l), yc, sum(v.co.z for v in l) / len(l))
        if isinstance(plan, str):
            report.append((tag, plan))
            print(tag, ":", plan)
            continue
        nf, N = w.apply(plan, protected)
        msg = "OK  rim %d -> %d points, %d coupes, %d faces remplacees, poles=%d, quads_pinces=%d" % (len(l), N, len(plan["sub"]), nf, plan["poles"], plan["bad_q"])
        report.append((tag, msg))
        print(tag, ":", msg)

    bm.normal_update()
    nb = sum(len(f.verts) != 4 for f in bm.faces)
    dev = max(bvh.find_nearest(f.calc_center_median())[3] for f in bm.faces)
    print("faces non-quad restantes :", nb, "| non-manifold :", sum(len(e.link_faces) > 2 for e in bm.edges),
          "| aretes de bord :", sum(e.is_boundary for e in bm.edges), "| ecart max a la surface d'origine : %.2f" % dev)
    mesh = bpy.data.meshes.new("coque_FENETRES")
    bm.to_mesh(mesh)
    res = bpy.data.objects.new("coque_FENETRES", mesh)
    bpy.context.scene.collection.objects.link(res)
    ob.hide_set(True)
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, "coque_fenetres.blend"))
    for o in bpy.context.view_layer.objects:
        o.select_set(o == res)
    bpy.ops.wm.alembic_export(filepath=os.path.join(out_dir, "coque_fenetres.abc"), selected=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], [float(x) for x in sys.argv[4:]])
