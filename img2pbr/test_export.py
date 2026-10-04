"""python -m pytest test_export.py  (ou: python test_export.py)  -- export EXR ACEScg + USD MaterialX."""
import tempfile
from pathlib import Path

import numpy as np

import colorspace as cs
import img2pbr

SAMPLE = Path(__file__).parent / "samples" / "parquet.jpg"


def _run(*extra):
    d = tempfile.mkdtemp()
    r = img2pbr.run([str(SAMPLE), "-o", d, "--ai", "off", "--res", "1k", *extra], log=lambda *_: None)
    return Path(d), r


def test_exr_acescg_and_square_resolution():
    import OpenEXR
    d, r = _run()
    assert r["size"] == 1024 and r["format"] == "exr"
    f = OpenEXR.File(str(d / "parquet_diffuse.exr"))
    assert f.header()["colorInteropID"] == "lin_ap1_scene" and "chromaticities" in f.header()
    px = list(f.channels().values())[0].pixels
    assert px.shape == (1024, 1024, 3) and px.dtype == np.float16
    # diffuse EXR = diffuse PNG-equivalent (sRGB) decode -> ACEScg
    expected = cs.srgb_to_acescg(cs.srgb_decode(np.clip(r["maps"]["diffuse"], 0, 1)))
    assert np.abs(px.astype(np.float32) - expected).max() < 2e-3
    n = OpenEXR.File(str(d / "parquet_normal.exr"))          # donnee : non convertie
    assert "chromaticities" not in n.header()
    assert np.abs(list(n.channels().values())[0].pixels.astype(np.float32) - r["maps"]["normal"]).max() < 1e-3


def test_usd_materialx_wiring():
    from pxr import Usd, UsdShade
    d, r = _run("--usd", "--usd-plane")
    st = Usd.Stage.Open(str(d / "parquet.usda"))
    mat = UsdShade.Material(st.GetPrimAtPath("/parquet/Materials/parquet"))
    assert mat.GetSurfaceOutput("mtlx").HasConnectedSource() and mat.GetDisplacementOutput("mtlx").HasConnectedSource()
    ss = UsdShade.Shader(st.GetPrimAtPath("/parquet/Materials/parquet/surface"))
    assert ss.GetIdAttr().Get() == "ND_standard_surface_surfaceshader"
    for inp in ("base_color", "specular_roughness", "normal"):
        assert ss.GetInput(inp).HasConnectedSource(), inp
    for node, cspace in (("diffuse", "acescg"), ("roughness", "raw"), ("normal", "raw"), ("height", "raw")):
        a = UsdShade.Shader(st.GetPrimAtPath(f"/parquet/Materials/parquet/{node}")).GetInput("file")
        assert a.GetAttr().GetColorSpace() == cspace and (d / a.Get().path.lstrip("./")).exists(), node


def test_no_usd_by_default_and_png_still_works():
    d, r = _run()
    assert not list(d.glob("*.usd*"))
    d, r = _run("--format", "png")
    assert (d / "parquet_diffuse.png").exists() and not list(d.glob("*.exr"))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
