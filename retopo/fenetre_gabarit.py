"""Gabarit propre autour d'UNE fenetre de la demi-coque (essai a valider).

Usage :
    python3 -I fenetre_gabarit.py <in.abc> <objet> <out_dir>

Probleme d'origine
------------------
Sous la fenetre, un triangle (A, B, M) + deux poles de valence 6. Les deux coins
du bas partent en eventail vers le meme point M, ce qui pince le triangle.

Gabarit
-------
1. Trois boucles verticales (loop cuts) traversent le rim de la fenetre (les 3
   couronnes fines) : une au milieu du bas, deux sur le haut. Le contour de
   l'ouverture passe de 12 a 15 points.
2. Le rim (15 points) est relie 1 pour 1 aux 15 points du bloc de grille qui
   l'entoure : une seule couronne de quads, zero triangle, zero ngon, pas de
   nouveau pole a l'interieur. Les coins du rim (3 points chacun) partent en
   eventail vers 3 points distincts du bloc.
3. Les anciens points interieurs du bloc sont supprimes. Le bord du bloc ne
   change pas, le reste de la coque n'est pas touche.
"""
import math
import os
import sys

import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

# --- reglages de la fenetre traitee (coordonnees monde, vue de cote = y,z)
WINDOW_Y = -1019.0                     # centre de la fenetre en y
BOX = (-1108.0, -930.0, 364.0, 466.0)  # bloc de grille remplace : y0, y1, z0, z1
CUT_BOTTOM = [-1020.0]                 # y des coupes sur l'arete du bas du rim
CUT_TOP = [-991.0, -1051.0]            # y des coupes sur l'arete du haut du rim


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


def strip_through(e0, start_side):
    """Couloir de quads traverse depuis e0, du cote de `start_side`, jusqu'a un bord."""
    g, edges, strip = start_side, [e0], []
    while True:
        e = edges[-1]
        opp = [x for x in g.edges if not set(x.verts) & set(e.verts)]
        edges.append(opp[0])
        strip.append(g)
        if opp[0].is_boundary:
            return edges, strip
        g = [f for f in opp[0].link_faces if f is not g][0]


def loop_cut(snap, e0, kept, y):
    """Coupe e0 et tout le couloir du rim a la position y (monde)."""
    side = [f for f in e0.link_faces if f in kept][0]
    edges, strip = strip_through(e0, side)
    mids = []
    for e in edges:
        a, b = sorted(e.verts, key=lambda v: v.co.y)
        _, v = bmesh.utils.edge_split(e, a, (y - a.co.y) / (b.co.y - a.co.y))
        v.co = snap(v.co)
        mids.append(v)
    for g, a, b in zip(strip, mids, mids[1:]):
        bmesh.utils.face_split(g, a, b)


def ccw(verts, n):
    pts = [v.co for v in verts]
    c = sum(pts, Vector()) / len(pts)
    a = Vector()
    for i, p in enumerate(pts):
        a += (p - c).cross(pts[(i + 1) % len(pts)] - c)
    return verts if a.dot(n) > 0 else verts[::-1]


def bridge(bm, inner, outer, n):
    """Une couronne de quads 1 pour 1 entre deux boucles de meme taille."""
    inner, outer = ccw(inner, n), ccw(outer, n)
    N = len(inner)
    assert N == len(outer)
    s = min(range(N), key=lambda s: sum((inner[i].co - outer[(i + s) % N].co).length_squared for i in range(N)))
    faces = [bm.faces.new([inner[i], outer[(i + s) % N], outer[(i + s + 1) % N], inner[(i + 1) % N]])
             for i in range(N)]
    for f in faces:
        f.normal_update()
        if f.normal.dot(n) < 0:
            f.normal_flip()
    return faces


# ---------------------------------------------------------------- principal
def main(abc, name, out_dir):
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

    def fresh():
        bm.verts.index_update()
        bm.faces.ensure_lookup_table()
        loop = [l for l in boundary_loops(bm)
                if len(l) < 20 and abs(sum(v.co.y for v in l) / len(l) - WINDOW_Y) < 30][0]
        return loop, rim_faces(loop)

    # 1. loop cuts dans le rim (bas puis haut)
    for ys, is_top in [(y, False) for y in CUT_BOTTOM] + [(y, True) for y in CUT_TOP]:
        loop, kept = fresh()
        long_edges = [e for e in region_boundary(kept) if e.calc_length() > 40]
        side = [e for e in long_edges if (min(v.co.z for v in e.verts) > 420) == is_top
                and min(v.co.y for v in e.verts) < ys < max(v.co.y for v in e.verts)]
        loop_cut(snap, side[0], kept, ys)

    # 2. couronne 1 pour 1 entre le rim et le bloc de grille
    loop, kept = fresh()
    inner = order_cycle(region_boundary(kept))
    inside = lambda f: BOX[0] < f.calc_center_median().y < BOX[1] and BOX[2] < f.calc_center_median().z < BOX[3]
    block = {f for f in bm.faces if inside(f)} | kept
    outer = order_cycle(region_boundary(block))
    print("rim", len(inner), "bloc", len(outer), "faces remplacees", len(block - kept))
    n = sum((f.normal for f in kept), Vector()).normalized()
    old = block - kept
    keep_v = set(inner) | set(outer)
    verts = {v for f in old for v in f.verts}
    bmesh.ops.delete(bm, geom=list(old), context='FACES_ONLY')
    bmesh.ops.delete(bm, geom=[v for v in verts if v.is_valid and v not in keep_v and not v.link_faces], context='VERTS')
    new = bridge(bm, inner, outer, n)
    bm.normal_update()

    # 3. controles
    sizes = {len(f.verts) for f in bm.faces}
    dev = max(bvh.find_nearest(f.calc_center_median())[3] for f in new)
    print("tailles de faces :", sorted(sizes), " non-manifold :", sum(len(e.link_faces) > 2 for e in bm.edges),
          " ecart max a la surface d'origine : %.2f" % dev)

    mesh = bpy.data.meshes.new("coque_FENETRE1")
    bm.to_mesh(mesh)
    res = bpy.data.objects.new("coque_FENETRE1", mesh)
    bpy.context.scene.collection.objects.link(res)
    ob.hide_set(True)
    os.makedirs(out_dir, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, "coque_fenetre1.blend"))
    for o in bpy.context.view_layer.objects:
        o.select_set(o == res)
    bpy.ops.wm.alembic_export(filepath=os.path.join(out_dir, "coque_fenetre1.abc"), selected=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
