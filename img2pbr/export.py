"""Export des cartes : EXR (couleur ACEScg, donnees raw) et USD avec shader MaterialX."""
import os

import numpy as np

import colorspace as cs

COLOR_MAPS = ("diffuse",)                                  # tout le reste est de la donnee
HALF_EXCEPT = ("height",)                                  # height en float32 (deplacement)


def write_exr(path, key, img):
    """diffuse : sRGB -> lineaire -> ACEScg, half RGB, chromaticites AP1.
    Autres cartes : valeurs brutes (non converties), 'Y' pour 1 canal, half (height : float32)."""
    import OpenEXR
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    if key in COLOR_MAPS:
        header["colorInteropID"] = "lin_ap1_scene"
        header["chromaticities"] = OpenEXR.colorInteropIDToChromaticities("lin_ap1_scene")
        data = {"RGB": cs.srgb_to_acescg(cs.srgb_decode(np.clip(img, 0, 1))).astype(np.float16)}
    else:
        header["oiio:ColorSpace"] = "Raw"
        dt = np.float32 if key in HALF_EXCEPT else np.float16
        data = {"RGB": img.astype(dt)} if img.ndim == 3 else {"Y": img.astype(dt)}
    with OpenEXR.File(header, data) as f:
        f.write(str(path))


def write_usd(path, name, files, fmt_exr, disp_scale=0.01, plane=False):
    """USD avec un Material MaterialX (standard_surface) dont les textures sont branchees :
    diffuse -> base_color, roughness -> specular_roughness, normal -> normalmap -> normal,
    height -> (height - 0.5) -> displacement. AO et specular ne sont pas branches
    (standard_surface n'a pas d'entree d'occlusion ; son IOR 1.5 donne deja F0 ~ 4 %)."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt
    T = Sdf.ValueTypeNames
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, f"/{name}")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.Scope.Define(stage, f"/{name}/Materials")
    base = f"/{name}/Materials/{name}"
    mat = UsdShade.Material.Define(stage, base)

    def node(nid, nname, out_type):
        s = UsdShade.Shader.Define(stage, f"{base}/{nname}")
        s.CreateIdAttr(nid)
        return s, s.CreateOutput("out", out_type)

    uv, uv_out = node("ND_texcoord_vector2", "uv", T.Float2)

    def image(nid, nname, out_type, key, cs_name):
        s, o = node(nid, nname, out_type)
        f = s.CreateInput("file", T.Asset)
        f.Set(Sdf.AssetPath("./" + os.path.basename(files[key])))
        f.GetAttr().SetColorSpace(cs_name)
        s.CreateInput("texcoord", T.Float2).ConnectToSource(uv_out)
        return o

    ss, ss_out = node("ND_standard_surface_surfaceshader", "surface", T.Token)
    ss.CreateInput("base", T.Float).Set(1.0)
    ss.CreateInput("metalness", T.Float).Set(0.0)
    ss.CreateInput("base_color", T.Color3f).ConnectToSource(
        image("ND_image_color3", "diffuse", T.Color3f, "diffuse", "acescg" if fmt_exr else "srgb_texture"))
    ss.CreateInput("specular_roughness", T.Float).ConnectToSource(
        image("ND_image_float", "roughness", T.Float, "roughness", "raw"))
    nmap, nmap_out = node("ND_normalmap_float", "normal_map", T.Float3)   # nom MaterialX 1.39
    nmap.CreateInput("in", T.Float3).ConnectToSource(image("ND_image_vector3", "normal", T.Float3, "normal", "raw"))
    nmap.CreateInput("scale", T.Float).Set(1.0)
    ss.CreateInput("normal", T.Float3).ConnectToSource(nmap_out)
    mat.CreateSurfaceOutput("mtlx").ConnectToSource(ss_out)

    sub, sub_out = node("ND_subtract_float", "height_centered", T.Float)
    sub.CreateInput("in1", T.Float).ConnectToSource(image("ND_image_float", "height", T.Float, "height", "raw"))
    sub.CreateInput("in2", T.Float).Set(0.5)
    disp, disp_out = node("ND_displacement_float", "displacement", T.Token)
    disp.CreateInput("displacement", T.Float).ConnectToSource(sub_out)
    disp.CreateInput("scale", T.Float).Set(float(disp_scale))
    mat.CreateDisplacementOutput("mtlx").ConnectToSource(disp_out)

    if plane:                                                          # plan 1 m x 1 m pour tester
        q = UsdGeom.Mesh.Define(stage, f"/{name}/Preview")
        q.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(-.5, 0, -.5), Gf.Vec3f(.5, 0, -.5), Gf.Vec3f(.5, 0, .5), Gf.Vec3f(-.5, 0, .5)]))
        q.CreateFaceVertexCountsAttr([4])
        q.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        q.CreateNormalsAttr(Vt.Vec3fArray([Gf.Vec3f(0, 1, 0)] * 4))
        q.SetNormalsInterpolation("vertex")
        pv = UsdGeom.PrimvarsAPI(q).CreatePrimvar("st", T.TexCoord2fArray, UsdGeom.Tokens.vertex)
        pv.Set(Vt.Vec2fArray([Gf.Vec2f(0, 0), Gf.Vec2f(1, 0), Gf.Vec2f(1, 1), Gf.Vec2f(0, 1)]))
        UsdShade.MaterialBindingAPI.Apply(q.GetPrim()).Bind(mat)
    stage.GetRootLayer().documentation = f"img2pbr : materiau MaterialX {name}."
    stage.GetRootLayer().Save()
