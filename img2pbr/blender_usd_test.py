#!/usr/bin/env python3
"""Importe un .usda de img2pbr dans Blender (bpy), l'applique a une sphere lisse et rend avec Cycles.
Sert a verifier visuellement le material (reseau UsdPreviewSurface : Blender n'importe pas le MaterialX).

python blender_usd_test.py parquet_material.usda rendu.png [--samples 64] [--tile 2]
"""
import argparse
import math
import sys

import bpy


def main():
    p = argparse.ArgumentParser()
    p.add_argument("usd")
    p.add_argument("out")
    p.add_argument("--samples", type=int, default=64)
    p.add_argument("--tile", type=float, default=2.0, help="repetitions des textures sur la sphere")
    p.add_argument("--res", type=int, default=900)
    a = p.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.usd_import(filepath=a.usd, import_materials=True, import_usd_preview=True,
                          set_material_blend=False)
    mats = list(bpy.data.materials)
    print("materiaux importes :", [m.name for m in mats])
    mat = mats[0]
    print("noeuds :", [(n.type, n.name) for n in mat.node_tree.nodes])
    for o in [o for o in bpy.data.objects]:       # on ne garde que le material
        bpy.data.objects.remove(o)

    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = a.samples
    sc.cycles.use_denoising = False
    sc.render.resolution_x = sc.render.resolution_y = a.res

    bpy.ops.mesh.primitive_uv_sphere_add(segments=128, ring_count=64, radius=1.0)
    s = bpy.context.active_object
    bpy.ops.object.shade_smooth()
    s.data.materials.append(mat)
    s.data.uv_layers.active.name = "st"           # le material USD lit le jeu d'UV "st"
    if a.tile != 1.0:                              # repetition des UV (les textures sont tileables)
        for uv in s.data.uv_layers.active.data:
            uv.uv = (uv.uv[0] * a.tile, uv.uv[1] * a.tile)

    w = bpy.data.worlds.new("W")
    sc.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.35, 0.4, 0.5, 1)
    bg.inputs["Strength"].default_value = 1.0
    for loc, energy, size in [((3, -3, 3), 700, 3), ((-4, -2, 1), 250, 3)]:
        bpy.ops.object.light_add(type="AREA", location=loc)
        l = bpy.context.active_object
        l.data.energy, l.data.size = energy, size
        d = l.location * -1
        l.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()

    bpy.ops.object.camera_add(location=(0, -4.2, 0.3), rotation=(math.radians(88), 0, 0))
    sc.camera = bpy.context.active_object
    sc.render.filepath = a.out
    sc.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    print("->", a.out)


if __name__ == "__main__":
    main()
