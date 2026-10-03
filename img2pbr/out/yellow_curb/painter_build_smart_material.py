"""A coller dans Python > Console de Substance 3D Painter (projet ouvert, texture set actif).
Cree un calque de remplissage 'Yellow Curb' avec les 5 canaux PBR. NON TESTE (pas de Painter ici) :
si un nom d'API differe dans votre version, la console indiquera la ligne.
Ensuite : clic droit sur le calque > Create smart material."""
import os
import substance_painter.project as project
import substance_painter.resource as resource
import substance_painter.textureset as ts
import substance_painter.layerstack as ls

D = r"CHEMIN/VERS/out/yellow_curb"          # <- a adapter
MAPS = {
    ls.ChannelType.BaseColor: "yellow_curb_basecolor.png",
    ls.ChannelType.Roughness: "yellow_curb_roughness.png",
    ls.ChannelType.Metallic:  "yellow_curb_metallic.png",
    ls.ChannelType.Normal:    "yellow_curb_normal.png",
    ls.ChannelType.Height:    "yellow_curb_height.png",
}
assert project.is_open(), "Ouvrez un projet d'abord"
stack = ts.get_active_stack() if hasattr(ts, "get_active_stack") else ts.all_texture_sets()[0].get_stack()
layer = ls.insert_fill(ls.InsertPosition.from_textureset_stack(stack))
layer.set_name("Yellow Curb")
layer.active_channels = set(MAPS)
for ch, fn in MAPS.items():
    res = resource.import_project_resource(os.path.join(D, fn), resource.Usage.TEXTURE)
    layer.set_source(ch, res.identifier())
print("OK : calque cree")
