"""Playblast Workbench : 6 vues + reference, assemblees en une planche."""
import os, bpy
from crane_lib import *
from PIL import Image, ImageDraw

LABELS = ["Reference photo", "Perspective (cam ref)", "Front ortho", "Side ortho", "Top ortho", "3/4 iso"]

def run(sc, L, here, res=640):
    setup_playblast(sc, res)
    out = os.path.join(here, "playblast"); os.makedirs(out, exist_ok=True)
    tgt = (2, 0, 13.5)
    cams = [
        make_cam("c_persp", (4, -85, 15), (3.5, 0, 14), lens=95),
        make_cam("c_front", (2, -100, 14), (2, 0, 14), ortho=34),
        make_cam("c_side", (100, 0, 14), (0, 0, 14), ortho=34),
        make_cam("c_top", (2, 0, 100), (2, 0, 0), ortho=60),
        make_cam("c_iso", (-34, -46, 24), (3, 0, 12), lens=38),
    ]
    paths = []
    for i, c in enumerate(cams):
        p = os.path.join(out, f"_v{i}.png"); render_to(sc, c, p); paths.append(p)
    ims = [Image.open("%s/reference.jpg" % here).convert("RGB").resize((res, res), Image.LANCZOS)] + [Image.open(p).convert("RGB") for p in paths]
    W = Image.new("RGB", (res * 3, res * 2))
    for i, im in enumerate(ims):
        W.paste(im, ((i % 3) * res, (i // 3) * res)); d = ImageDraw.Draw(W)
        d.rectangle(((i % 3) * res, (i // 3) * res, (i % 3) * res + 190, (i // 3) * res + 18), fill=(0, 0, 0))
        d.text(((i % 3) * res + 5, (i // 3) * res + 3), LABELS[i], fill=(255, 255, 255))
    W.save(os.path.join(out, f"stage{L}_sheet.png"))
    ims[2].save(os.path.join(out, f"stage{L}_persp.png"))
    for p in paths: os.remove(p)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(here, f"crane_stage{L}.blend"))
    print("playblast ->", os.path.join(out, f"stage{L}_sheet.png"))
    if L >= 3: closeups(sc, L, here)


def closeups(sc, L, here, res=520):
    """Gros plans : rendu plein (shading harden/soften) puis topologie (Wireframe modifier temporaire)."""
    out = os.path.join(here, "playblast")
    views = [("Maison + cab", (-3, -26, 13), (-2.5, 0, 13), 60), ("Deck + escalier", (7, -22, 8.5), (0, 0, 8.5), 62),
             ("Bogies + touret", (-9, -24, 3), (-6.5, 0, 2.3), 55), ("Pointe de fleche", (15, -30, 20.5), (14.5, 0, 20.5), 85)]
    cams = [make_cam(f"cc{i}", l, t, lens=ln) for i, (_, l, t, ln) in enumerate(views)]
    rows = []
    for mode in ("solid", "topo"):
        if mode == "topo":
            for ob in sc.objects:
                if ob.type != 'MESH': continue
                if ob.name in ("Ground", "Rails", "Ground_Grid_5m"): ob.hide_render = True; continue
                m = ob.modifiers.new("topo", 'WIREFRAME'); m.thickness = 0.012; m.use_replace = True; m.use_even_offset = True
                ob.color = (0.92, 0.92, 0.92, 1)
            sc.display.shading.color_type = 'OBJECT'; sc.display.shading.show_cavity = False; sc.display.shading.show_object_outline = False
            sc.world.color = (0.03, 0.03, 0.035)
        ims = []
        for i, c in enumerate(cams):
            p = os.path.join(out, f"_c{i}.png"); sc.render.resolution_x = sc.render.resolution_y = res
            render_to(sc, c, p); ims.append(Image.open(p).convert("RGB")); os.remove(p)
        rows.append(ims)
    W = Image.new("RGB", (res * 4, res * 2)); d = ImageDraw.Draw(W)
    for r, ims in enumerate(rows):
        for i, im in enumerate(ims):
            W.paste(im, (i * res, r * res)); d.rectangle((i * res, r * res, i * res + 170, r * res + 16), fill=(0, 0, 0))
            d.text((i * res + 4, r * res + 2), views[i][0] + (" - topo" if r else " - shaded"), fill=(255, 255, 255))
    W.save(os.path.join(out, f"stage{L}_closeups.png"))
