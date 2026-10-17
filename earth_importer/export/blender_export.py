"""Conversion OBJ -> Alembic / USD, lancee par l'outil dans Blender en arriere-plan.

    blender -b --factory-startup --python blender_export.py -- in.obj out.usdc usd 1

Arguments : OBJ source, fichier de sortie, format (abc|usd), textures (1|0).
Les lignes prefixees "[earth] " remontent dans le panneau de log de l'interface.
"""
import sys

import bmesh
import bpy


def say(msg):
    print(f"[earth] {msg}", flush=True)


def main():
    src, dst, fmt, textures = sys.argv[sys.argv.index("--") + 1:][:4]
    textures = textures == "1"

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.obj_import(filepath=src, forward_axis="NEGATIVE_Z", up_axis="Y")
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    say(f"{len(objects)} tuiles importees dans Blender")

    # soude les sommets dupliques par l'OBJ (un par coin de triangle cote UV)
    for o in objects:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
        bm.to_mesh(o.data)
        bm.free()

    if fmt == "usd":
        bpy.ops.wm.usd_export(filepath=dst, selected_objects_only=False,
                              export_materials=textures, export_textures=textures,
                              relative_paths=True, export_uvmaps=textures)
    elif fmt == "abc":
        bpy.ops.wm.alembic_export(filepath=dst, selected=False, uvs=textures,
                                  face_sets=textures, start=1, end=1)
    else:
        raise SystemExit(f"format inconnu : {fmt}")
    say(f"{fmt.upper()} ecrit")


try:
    main()
except Exception as e:
    print(f"ERREUR export Blender : {e}", flush=True)
    sys.exit(1)
