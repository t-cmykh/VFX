"""Rend l'overlay layout (grille sol, voiture proxy, route) en PNG transparents (Workbench) depuis la caméra trackée.
Usage: python3 -I render_qc.py shot.blend out_dir step [frames...]"""
import sys, os, bpy
blend, outd, step = sys.argv[1], sys.argv[2], int(sys.argv[3]); only = [int(x) for x in sys.argv[4:]]
os.makedirs(outd, exist_ok=True); bpy.ops.wm.open_mainfile(filepath=blend); sc = bpy.context.scene
sc.render.engine = 'BLENDER_WORKBENCH'; sc.render.film_transparent = True; sc.render.resolution_x, sc.render.resolution_y = 1280, 720
sc.render.image_settings.file_format = 'PNG'; sc.render.image_settings.color_mode = 'RGBA'
sh = sc.display.shading; sh.light = 'FLAT'; sh.color_type = 'TEXTURE'; sh.show_object_outline = False
img = bpy.data.images.new("grid", 2048, 2048, alpha=True); img.generated_type = 'COLOR_GRID'; img.generated_width = img.generated_height = 2048
mat = bpy.data.materials.new("gridmat"); mat.use_nodes = True; nt = mat.node_tree
tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img; tex.interpolation = 'Linear'; nt.links.new(tex.outputs[0], nt.nodes["Principled BSDF"].inputs[0])
mat.diffuse_color = (0.2, 1, 0.3, 1)
bpy.data.materials.new("white").diffuse_color = (1, 1, 1, 1)
sc.display.render_aa = '8'
for o in sc.objects:
    if o.name.startswith("GROUND"): o.data.materials.append(mat)
    if o.name.startswith("ROAD"): o.color = (1, 0.8, 0, 1)
    if o.name.startswith("TRACKED"): o.hide_render = True
bpy.data.objects["CAR_proxy_BMW_E46"].show_wire = True; bpy.data.objects["CAR_proxy_BMW_E46"].color = (1, 0.1, 0.1, 1)
frames = only or range(1, sc.frame_end + 1, step)
for f in frames:
    sc.frame_set(f); sc.render.filepath = os.path.join(outd, "%04d.png" % f); bpy.ops.render.render(write_still=True)
print("rendered", len(list(frames)))
