#!/usr/bin/env python3
"""Rend une scene d'essai (sphere grise, sphere chrome, cube blanc, sol gris) eclairee par une
HDRI EXR ACEScg, via bpy. Sert a verifier visuellement l'eclairage.

python blender_test.py hdri.exr rendu.png [--samples 64] [--rot-z 0]
"""
import argparse
import math
import sys

import bpy


def main():
    p = argparse.ArgumentParser()
    p.add_argument("hdri")
    p.add_argument("out")
    p.add_argument("--samples", type=int, default=64)
    p.add_argument("--rot-z", type=float, default=0.0, help="rotation de l'env autour de Z (deg)")
    p.add_argument("--res", type=int, nargs=2, default=[960, 540])
    p.add_argument("--exposure", type=float, default=0.0)
    a = p.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])

    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = a.samples
    sc.cycles.use_denoising = False
    sc.render.resolution_x, sc.render.resolution_y = a.res
    sc.render.film_transparent = False

    # monde : env texture en ACEScg (Blender convertit vers son espace de travail via OCIO)
    w = bpy.data.worlds.new("W")
    sc.world = w
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(a.hdri)
    env.image.colorspace_settings.name = "ACEScg"
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Rotation"].default_value[2] = math.radians(a.rot_z)
    tc = nt.nodes.new("ShaderNodeTexCoord")
    bg = nt.nodes.new("ShaderNodeBackground")
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    def mat(name, color, rough, metal):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        b = m.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = (*color, 1)
        b.inputs["Roughness"].default_value = rough
        b.inputs["Metallic"].default_value = metal
        return m

    def add(op, loc, m, **kw):
        op(location=loc, **kw)
        o = bpy.context.active_object
        o.data.materials.append(m)
        return o

    add(bpy.ops.mesh.primitive_plane_add, (0, 0, 0), mat("floor", (0.18, 0.18, 0.18), 0.6, 0), size=30)
    s1 = add(bpy.ops.mesh.primitive_uv_sphere_add, (-1.4, 0, 1), mat("grey", (0.18, 0.18, 0.18), 0.5, 0), radius=1)
    s2 = add(bpy.ops.mesh.primitive_uv_sphere_add, (1.4, 0, 1), mat("chrome", (0.9, 0.9, 0.9), 0.02, 1), radius=1)
    add(bpy.ops.mesh.primitive_cube_add, (0, 2.6, 0.6), mat("white", (0.8, 0.8, 0.8), 0.4, 0), size=1.2)
    for o in (s1, s2):
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.shade_smooth()

    bpy.ops.object.camera_add(location=(0, 7.5, 2.0), rotation=(math.radians(82), 0, math.radians(180)))
    sc.camera = bpy.context.active_object
    sc.camera.data.lens = 38

    # vue : on essaie AgX puis Standard
    sc.display_settings.display_device = "sRGB"
    for v in ("AgX", "Standard"):
        try:
            sc.view_settings.view_transform = v
            break
        except TypeError:
            continue
    sc.view_settings.exposure = a.exposure
    sc.render.filepath = a.out
    sc.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    print("vue:", sc.view_settings.view_transform, "->", a.out)


main()
