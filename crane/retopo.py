"""Etape 6 : QuadriFlow sur toutes les pieces de l'etape 5 (non destructif : ecrit crane_stage6.blend + rapport)."""
import os, math, time, bpy, bmesh
from crane_lib import harden_soften
HERE = os.path.dirname(os.path.abspath(__file__))
bpy.ops.wm.open_mainfile(filepath=f"{HERE}/crane_stage5.blend")
sc = bpy.context.scene; vl = bpy.context.view_layer
SKIP = ("Ground", "Rails", "Ground_Grid_5m", "Human_1m75")
SIZE = 0.28            # taille cible de quad (m) pour les grosses pieces

def faces(ob):
    return len(ob.data.polygons)

def area(ob):
    bm = bmesh.new(); bm.from_mesh(ob.data); a = sum(f.calc_area() for f in bm.faces); bm.free(); return a

def parts(ob):
    bm = bmesh.new(); bm.from_mesh(ob.data); seen = set(); n = 0
    for v in bm.verts:
        if v.index in seen: continue
        n += 1; stack = [v]; seen.add(v.index)
        while stack:
            c = stack.pop()
            for e in c.link_edges:
                o = e.other_vert(c)
                if o.index not in seen: seen.add(o.index); stack.append(o)
    bm.free(); return n

def bbox(ob):
    xs = [v.co for v in ob.data.vertices]
    lo = [min(c[i] for c in xs) for i in range(3)]; hi = [max(c[i] for c in xs) for i in range(3)]
    return math.dist(lo, hi)

rep = []; t0 = time.time()
objs = [o for o in sc.objects if o.type == 'MESH' and o.name not in SKIP]
for ob in objs:
    vl.objects.active = ob
    for o in objs: o.select_set(o is ob)
    with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
        for m in list(ob.modifiers): bpy.ops.object.modifier_apply(modifier=m.name)       # fige bevels / WeightedNormal
    before = faces(ob); a = area(ob); np_ = parts(ob)
    n = int(min(max(a / SIZE ** 2, 120), 30000)); t = time.time(); status = "ok"
    dims = sorted(ob.dimensions)
    if np_ > 6:
        status = f"saute : {np_} pieces separees (tubes / rails / echelles)"
    elif any(k in ob.name for k in ("Brace", "Stays", "Rope", "Hook", "Pins", "Pendant", "Sheaves", "Support")):
        status = "saute : piece de type tube / barre / axe (nom)"
    elif dims[1] < 0.6:
        status = "saute : piece trop fine (tube / barre)"
    else:
        backup = ob.data.copy(); d0 = bbox(ob)
        try:
            with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
                r = bpy.ops.object.quadriflow_remesh(mode='FACES', target_faces=n, use_preserve_sharp=True,
                                                     use_preserve_boundary=True, use_mesh_symmetry=False, smooth_normals=True, seed=1)
            if 'FINISHED' not in r: status = "echec : maillage non manifold"
        except Exception as e:
            status = "echec : maillage non manifold" if "manifold" in str(e) or "failed" in str(e) else "echec : " + str(e).strip().splitlines()[-1][:60]
        if status == "ok":
            a1 = area(ob); d1 = bbox(ob)
            if not (0.75 < a1 / a < 1.3) or abs(d1 - d0) / d0 > 0.05 or faces(ob) < 0.2 * min(n, before):
                status = f"rejete : deforme (surface x{a1/a:.2f})"
        if status != "ok":
            old = ob.data; ob.data = backup; bpy.data.meshes.remove(old)
        else: harden_soften(ob, angle=30, bevel=0.0, weighted=True)
    after = faces(ob)
    rep.append((ob.name, before, n, after, status, time.time() - t))
    print(f"{ob.name:26s} {before:5d} -> {after:5d}  {status}  ({time.time()-t:.1f}s)", flush=True)

bpy.ops.wm.save_as_mainfile(filepath=f"{HERE}/crane_stage6.blend")
ok = [r for r in rep if r[4] == "ok"]
with open(f"{HERE}/playblast/stage6_retopo_report.txt", "w") as f:
    f.write(f"QuadriFlow sur {len(rep)} objets : {len(ok)} reussis, {len(rep)-len(ok)} echecs/inchanges ({time.time()-t0:.0f}s)\n")
    f.write(f"{'objet':26s} {'avant':>6s} {'cible':>6s} {'apres':>6s}  statut\n")
    for r in rep: f.write(f"{r[0]:26s} {r[1]:6d} {r[2]:6d} {r[3]:6d}  {r[4]}\n")
print("fini", len(ok), "/", len(rep))
