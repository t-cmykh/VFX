import bpy, math, sys, os
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=48, ring_count=24)
sphere = bpy.context.active_object
bpy.ops.mesh.primitive_cylinder_add(radius=0.4, depth=3.0, vertices=48)
cyl = bpy.context.active_object

mod = sphere.modifiers.new("Bool", 'BOOLEAN')
mod.operation = 'DIFFERENCE'; mod.object = cyl; mod.solver = 'EXACT'
bpy.context.view_layer.objects.active = sphere
bpy.ops.object.modifier_apply(modifier=mod.name)
bpy.data.objects.remove(cyl)
print("après Boolean :", len(sphere.data.vertices), "verts,", len(sphere.data.polygons), "faces")
sphere.name = "AfterBoolean"

import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

def render(name, obj, title):
    me = obj.data
    polys = [[tuple(me.vertices[v].co) for v in p.vertices] for p in me.polygons]
    ntri = sum(len(p.vertices) == 3 for p in me.polygons)
    nq = sum(len(p.vertices) == 4 for p in me.polygons)
    ng = len(me.polygons) - ntri - nq
    fig = plt.figure(figsize=(9, 9)); ax = fig.add_subplot(111, projection="3d")
    ax.add_collection3d(Poly3DCollection(polys, facecolor=(0.85, 0.87, 0.95), edgecolor="k", linewidths=0.4))
    ax.set_xlim(-1, 1); ax.set_ylim(-1, 1); ax.set_zlim(-1, 1); ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=35, azim=40); ax.set_axis_off()
    ax.set_title(f"{title}\n{len(me.vertices)} verts · {len(me.polygons)} faces ({nq} quads, {ntri} tris, {ng} n-gons)")
    fig.savefig(os.path.join(OUT, name), dpi=100); plt.close(fig)

render("1_after_boolean.png", sphere, "Après Boolean (EXACT)")

import bmesh
bm = bmesh.new(); bm.from_mesh(sphere.data)
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
print("non-manifold edges:", sum(not e.is_manifold for e in bm.edges), "| non-manifold verts:", sum(not v.is_manifold for v in bm.verts))
bm.to_mesh(sphere.data); bm.free()
bpy.ops.object.select_all(action='DESELECT')
sphere.select_set(True); bpy.context.view_layer.objects.active = sphere
bpy.ops.object.quadriflow_remesh(target_faces=1500, use_preserve_sharp=True,
                                 use_preserve_boundary=True, seed=0)
render("2_after_quadriflow.png", sphere, "Après Quadriflow (1500 faces)")
print("après Quadriflow :", len(sphere.data.vertices), "verts,", len(sphere.data.polygons), "faces")
