import os, math, bpy, bmesh
from crane_lib import harden_soften
HERE = os.path.dirname(os.path.abspath(__file__))
bpy.ops.wm.open_mainfile(filepath=f"{HERE}/crane_stage6.blend")
vl = bpy.context.view_layer; out = []
def nm(ob):
    bm = bmesh.new(); bm.from_mesh(ob.data); n = sum(1 for e in bm.edges if not e.is_manifold); bm.free(); return n
for name, size in (("Machinery_House", 0.28), ("Operator_Cab", 0.2)):
    ob = bpy.data.objects[name]; vl.objects.active = ob; before = len(ob.data.polygons); n0 = nm(ob)
    bm = bmesh.new(); bm.from_mesh(ob.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-5)
    bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=64)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(ob.data); bm.free(); ob.data.update(); n1 = nm(ob)
    area = sum(p.area for p in ob.data.polygons); n = int(max(120, area / size ** 2))
    status = f"non-manifold {n0}->{n1} aretes ; "
    try:
        with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
            r = bpy.ops.object.quadriflow_remesh(mode='FACES', target_faces=n, use_preserve_sharp=True, use_preserve_boundary=True, use_mesh_symmetry=False, smooth_normals=True, seed=1)
        status += "OK" if 'FINISHED' in r else "echec"
        if 'FINISHED' in r: harden_soften(ob, angle=30, bevel=0.0, weighted=True)
    except Exception as e: status += "echec (QuadriFlow refuse encore le maillage)"
    out.append((name, before, len(ob.data.polygons), status)); print(out[-1], flush=True)
bpy.ops.wm.save_as_mainfile(filepath=f"{HERE}/crane_stage6.blend")
with open(f"{HERE}/playblast/stage6_retopo_report.txt", "a") as f:
    f.write("\nSecond essai (nettoyage : fusion des doublons + normales + trous) :\n")
    for o in out: f.write(f"  {o[0]:20s} {o[1]:5d} -> {o[2]:5d}  {o[3]}\n")
