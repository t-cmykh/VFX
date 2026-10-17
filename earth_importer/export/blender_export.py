"""Conversion OBJ -> Alembic / USD, lancee par l'outil dans Blender en arriere-plan.

    blender -b --factory-startup --python blender_export.py -- in.obj out.usdc usd 1

Arguments : OBJ source, fichier de sortie, format (abc|usd), textures (1|0).
Les lignes prefixees "[earth] " remontent dans le panneau de log de l'interface.
"""
import sys

import bpy  # avant bmesh (obligatoire quand bpy est le module pip)
import bmesh  # noqa: E402


def say(msg):
    print(f"[earth] {msg}", flush=True)


def call(op, **kwargs):
    """Appelle un operateur en ignorant les options absentes de cette version de Blender."""
    known = {p.identifier for p in op.get_rna_type().properties}
    return op(**{k: v for k, v in kwargs.items() if k in known})


def main():
    src, dst, fmt, textures = sys.argv[sys.argv.index("--") + 1:][:4]
    textures = textures == "1"

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.obj_import(filepath=src, forward_axis="NEGATIVE_Z", up_axis="Y")
    objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    say(f"{len(objects)} tuiles importees dans Blender")

    # l'import OBJ pose une rotation de 90 deg sur chaque objet : on l'applique aux sommets
    for o in objects:
        o.data.transform(o.matrix_world)
        o.matrix_world.identity()

    # soude les sommets dupliques par l'OBJ (un par coin de triangle cote UV)
    for o in objects:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
        bm.to_mesh(o.data)
        bm.free()

    if fmt == "usd":
        call(bpy.ops.wm.usd_export, filepath=dst, selected_objects_only=False,
             export_materials=textures, export_uvmaps=textures, relative_paths=True,
             export_textures=textures,                                   # Blender 3.x / 4.x
             export_textures_mode="NEW" if textures else "KEEP")         # Blender 4.2+
    elif fmt == "abc":
        call(bpy.ops.wm.alembic_export, filepath=dst, selected=False, uvs=textures,
             face_sets=textures, start=1, end=1)
    else:
        raise SystemExit(f"format inconnu : {fmt}")
    say(f"{fmt.upper()} ecrit")


try:
    main()
except Exception as e:
    print(f"ERREUR export Blender : {e}", flush=True)
    sys.exit(1)
