"""Export USD : un Material (UsdPreviewSurface et/ou MaterialX standard_surface) branche sur les
cartes ecrites par export.write_maps, + un plan 1x1 avec UV pour le voir tout de suite (usdview).

Necessite `pip install usd-core`. Chemins de textures relatifs au fichier .usda (a garder dans
le meme dossier que les cartes).

Branchements : diffuse -> base color ; roughness -> roughness ; normal -> normal (tangent space,
OpenGL) ; height -> displacement ((h - 0.5) * echelle, 0.5 = neutre). `ao` : UsdPreviewSurface seulement
(standard_surface n'a pas d'entree AO). `specular` n'est pas branchee : la carte est constante (0.5 =
~4 % de F0), c'est deja la valeur par defaut des deux shaders.
"""
from pathlib import Path

MODES = ("both", "mtlx", "preview")


def _need_pxr():
    try:
        from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, Vt
    except ImportError:
        raise ValueError("L'export USD demande : pip install usd-core")
    return Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, Vt


def check_available():
    _need_pxr()


def write_usd(path, stem, files, fmt="png", mode="both", disp_scale=0.01):
    """files : {nom_carte: Path} (sortie de write_maps). Retourne le Path du .usda."""
    Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, Vt = _need_pxr()
    if mode not in MODES:
        raise ValueError(f"mode USD inconnu : {mode} ({', '.join(MODES)})")
    path = Path(path)
    name = Tf.MakeValidIdentifier(stem)
    rel = {k: "./" + Path(p).name for k, p in files.items()}
    exr = fmt == "exr"
    T = Sdf.ValueTypeNames

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    root = UsdGeom.Xform.Define(stage, "/Root")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.Scope.Define(stage, "/Root/Looks")
    mat = UsdShade.Material.Define(stage, f"/Root/Looks/{name}")
    base = f"/Root/Looks/{name}"

    def shader(sub, shader_id):
        s = UsdShade.Shader.Define(stage, f"{base}/{sub}")
        s.CreateIdAttr(shader_id)
        return s

    def connect(inp_shader, inp_name, inp_type, src_shader, out_name, out_type):
        out = src_shader.GetOutput(out_name) or src_shader.CreateOutput(out_name, out_type)
        inp_shader.CreateInput(inp_name, inp_type).ConnectToSource(out)

    if mode in ("both", "preview"):
        st = shader("st_preview", "UsdPrimvarReader_float2")
        st.CreateInput("varname", T.String).Set("st")
        st.CreateOutput("result", T.Float2)
        surf = shader("preview_surface", "UsdPreviewSurface")
        surf.CreateInput("metallic", T.Float).Set(0.0)

        def tex(key, out_name, out_type, color=False, scale=None, bias=None):
            t = shader(f"preview_{key}", "UsdUVTexture")
            t.CreateInput("file", T.Asset).Set(Sdf.AssetPath(rel[key]))
            connect(t, "st", T.Float2, st, "result", T.Float2)
            t.CreateInput("wrapS", T.Token).Set("repeat")
            t.CreateInput("wrapT", T.Token).Set("repeat")
            # EXR : donnees lineaires -> "raw" (les valeurs ACEScg de la diffuse sont lues comme
            # du Rec.709 par ce reseau de secours ; le reseau MaterialX, lui, etiquette ACEScg)
            t.CreateInput("sourceColorSpace", T.Token).Set(
                "sRGB" if color and not exr else "raw")
            if scale is not None:
                t.CreateInput("scale", T.Float4).Set(scale)
                t.CreateInput("bias", T.Float4).Set(bias)
            t.CreateOutput(out_name, out_type)
            return t

        connect(surf, "diffuseColor", T.Color3f,
                tex("diffuse", "rgb", T.Float3, color=True), "rgb", T.Float3)
        connect(surf, "roughness", T.Float, tex("roughness", "r", T.Float), "r", T.Float)
        connect(surf, "normal", T.Normal3f,
                tex("normal", "rgb", T.Float3, scale=Gf.Vec4f(2, 2, 2, 1),
                    bias=Gf.Vec4f(-1, -1, -1, 0)), "rgb", T.Float3)
        connect(surf, "occlusion", T.Float, tex("ao", "r", T.Float), "r", T.Float)
        s, b = float(disp_scale), -0.5 * float(disp_scale)
        connect(surf, "displacement", T.Float,
                tex("height", "r", T.Float, scale=Gf.Vec4f(s, s, s, 1), bias=Gf.Vec4f(b, b, b, 0)),
                "r", T.Float)
        surf.CreateOutput("surface", T.Token)
        mat.CreateSurfaceOutput().ConnectToSource(surf.GetOutput("surface"))

    if mode in ("both", "mtlx"):
        uv = shader("mtlx_texcoord", "ND_texcoord_vector2")
        uv.CreateInput("index", T.Int).Set(0)
        uv.CreateOutput("out", T.Float2)
        std = shader("mtlx_standard_surface", "ND_standard_surface_surfaceshader")
        std.CreateInput("base", T.Float).Set(1.0)
        std.CreateOutput("out", T.Token)

        def image(key, node_id, out_type, colorspace=None):
            n = shader(f"mtlx_{key}", node_id)
            f = n.CreateInput("file", T.Asset)
            f.Set(Sdf.AssetPath(rel[key]))
            if colorspace:
                f.GetAttr().SetColorSpace(colorspace)
            connect(n, "texcoord", T.Float2, uv, "out", T.Float2)
            n.CreateInput("uaddressmode", T.String).Set("periodic")
            n.CreateInput("vaddressmode", T.String).Set("periodic")
            n.CreateOutput("out", out_type)
            return n

        connect(std, "base_color", T.Color3f,
                image("diffuse", "ND_image_color3", T.Color3f, "acescg" if exr else "srgb_texture"),
                "out", T.Color3f)
        connect(std, "specular_roughness", T.Float,
                image("roughness", "ND_image_float", T.Float), "out", T.Float)
        nrm = shader("mtlx_normalmap", "ND_normalmap_float")
        connect(nrm, "in", T.Float3, image("normal", "ND_image_vector3", T.Float3), "out", T.Float3)
        nrm.CreateInput("scale", T.Float).Set(1.0)
        nrm.CreateOutput("out", T.Float3)
        connect(std, "normal", T.Float3, nrm, "out", T.Float3)
        mat.CreateSurfaceOutput("mtlx").ConnectToSource(std.GetOutput("out"))

        sub = shader("mtlx_height_centered", "ND_subtract_float")
        connect(sub, "in1", T.Float, image("height", "ND_image_float", T.Float), "out", T.Float)
        sub.CreateInput("in2", T.Float).Set(0.5)
        sub.CreateOutput("out", T.Float)
        disp = shader("mtlx_displacement", "ND_displacement_float")
        connect(disp, "displacement", T.Float, sub, "out", T.Float)
        disp.CreateInput("scale", T.Float).Set(float(disp_scale))
        disp.CreateOutput("out", T.Token)
        mat.CreateDisplacementOutput("mtlx").ConnectToSource(disp.GetOutput("out"))

    # plan de controle 1x1 (UV 0-1) lie au material
    plane = UsdGeom.Mesh.Define(stage, "/Root/preview_plane")
    plane.CreatePointsAttr([(-0.5, -0.5, 0), (0.5, -0.5, 0), (0.5, 0.5, 0), (-0.5, 0.5, 0)])
    plane.CreateFaceVertexCountsAttr([4])
    plane.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    plane.CreateNormalsAttr([(0, 0, 1)] * 4)
    plane.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    plane.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    st_pv = UsdGeom.PrimvarsAPI(plane).CreatePrimvar("st", T.TexCoord2fArray,
                                                    UsdGeom.Tokens.vertex)
    st_pv.Set([(0, 0), (1, 0), (1, 1), (0, 1)])
    UsdShade.MaterialBindingAPI.Apply(plane.GetPrim()).Bind(mat)
    stage.GetRootLayer().Save()
    return path
