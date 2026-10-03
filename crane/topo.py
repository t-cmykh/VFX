"""Topologie du modele : fil de fer a faces cachees (Workbench) + statistiques quads/tris/ngons.  python topo.py [etape]"""
import sys, os, bpy, bmesh
from PIL import Image, ImageDraw
from crane_lib import *
HERE = os.path.dirname(os.path.abspath(__file__)); L = int(sys.argv[1]) if len(sys.argv) > 1 else 5
bpy.ops.wm.open_mainfile(filepath=f"{HERE}/crane_stage{L}.blend")
sc = bpy.context.scene; setup_playblast(sc, 1000)
sc.world.color = (0.05, 0.05, 0.06)
sh = sc.display.shading; sh.show_cavity = False; sh.show_object_outline = False; sh.show_specular_highlight = False; sh.light = 'FLAT'
fill = material("topo_fill", (0.10, 0.11, 0.13)); wire = material("topo_wire", (0.95, 0.85, 0.45))

# ---- stats (maillage de base, avant bevel) -----------------------------------------------
rows = []; T = {"v": 0, "q": 0, "t": 0, "n": 0}
for ob in sc.objects:
    if ob.type != 'MESH' or ob.name in ("Ground", "Rails", "Ground_Grid_5m", "Human_1m75"): continue
    bm = bmesh.new(); bm.from_mesh(ob.data)
    q = sum(1 for f in bm.faces if len(f.verts) == 4); t = sum(1 for f in bm.faces if len(f.verts) == 3); n = len(bm.faces) - q - t
    rows.append((ob.name, len(bm.verts), q, t, n)); T["v"] += len(bm.verts); T["q"] += q; T["t"] += t; T["n"] += n; bm.free()
rows.sort(key=lambda r: -(r[2] + r[3] + r[4]))
tot = T["q"] + T["t"] + T["n"]
with open(f"{HERE}/playblast/stage{L}_topo_stats.txt", "w") as f:
    f.write(f"Etape {L} - maillage de base (avant modificateurs Bevel / WeightedNormal)\n")
    f.write(f"TOTAL : {len(rows)} objets, {T['v']} sommets, {tot} faces : {T['q']} quads ({100*T['q']/tot:.0f}%), {T['t']} tris ({100*T['t']/tot:.0f}%), {T['n']} n-gons ({100*T['n']/tot:.0f}%)\n\n")
    f.write(f"{'objet':28s} {'verts':>6s} {'quads':>6s} {'tris':>6s} {'ngons':>6s}\n")
    for r in rows: f.write(f"{r[0]:28s} {r[1]:6d} {r[2]:6d} {r[3]:6d} {r[4]:6d}\n")
print(open(f"{HERE}/playblast/stage{L}_topo_stats.txt").read()[:1800])

# ---- fil de fer a faces cachees : materiau 0 = remplissage sombre, materiau 1 = arêtes (Wireframe modifier) --------
for ob in sc.objects:
    if ob.type != 'MESH': continue
    if ob.name in ("Ground", "Rails", "Ground_Grid_5m", "Human_1m75"): ob.hide_render = True; continue
    ob.data.materials.clear(); ob.data.materials.append(fill); ob.data.materials.append(wire)
    m = ob.modifiers.new("topo", 'WIREFRAME'); m.thickness = 0.022; m.use_replace = False; m.use_even_offset = True
    m.use_boundary = True; m.material_offset = 1
sh.color_type = 'MATERIAL'

views = [("Front ortho", ("o", (2, -100, 14), (2, 0, 14), 34), 1000),
         ("Maison + cabine + contrepoids", ("p", (-2, -26, 14), (-1.5, 0, 14), 55), 1000),
         ("Fleche / apex / mat", ("p", (6, -34, 23), (6, 0, 23), 60), 1000),
         ("Base + touret + bogies", ("p", (-3, -26, 4.5), (-3, 0, 4.5), 50), 1000),
         ("Piedestal + deck", ("p", (2, -22, 8.5), (1, 0, 8.5), 60), 1000),
         ("3/4 iso", ("p", (-30, -40, 20), (3, 0, 11), 38), 1000)]
out = f"{HERE}/playblast"; ims = []
for i, (name, (k, loc, tgt, v), res) in enumerate(views):
    cam = make_cam(f"t{i}", loc, tgt, lens=v if k == "p" else 50, ortho=v if k == "o" else None)
    if k == "p" and name.startswith("Fleche"): pass
    sc.render.resolution_x = sc.render.resolution_y = res
    for ob in sc.objects:
        if ob.type == 'MESH' and ob.modifiers.get("topo"): ob.modifiers["topo"].thickness = 0.03 if k == "o" else (0.012 if "Base" in name or "Pied" in name else 0.016)
    p = f"{out}/_t{i}.png"; render_to(sc, cam, p); ims.append((name, Image.open(p).convert("RGB"))); os.remove(p)
cell = 640; W = Image.new("RGB", (cell * 3, cell * 2)); d = ImageDraw.Draw(W)
for i, (name, im) in enumerate(ims):
    W.paste(im.resize((cell, cell), Image.LANCZOS), ((i % 3) * cell, (i // 3) * cell))
    d.rectangle(((i % 3) * cell, (i // 3) * cell, (i % 3) * cell + 260, (i // 3) * cell + 16), fill=(0, 0, 0)); d.text(((i % 3) * cell + 4, (i // 3) * cell + 2), name, fill=(255, 255, 255))
W.save(f"{out}/stage{L}_topology.png"); ims[0][1].save(f"{out}/stage{L}_topology_front.png"); print("ok")
