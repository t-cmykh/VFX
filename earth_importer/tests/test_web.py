"""Version fichier local (EarthImporter.html) : a jour avec web/src, et meme resultat que la version Python.

Le test navigateur demande playwright + Chromium ; il est saute s'ils sont absents.
"""
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import build  # noqa: E402
from tests import fake_rocktree as fk  # noqa: E402


def test_built_file_is_up_to_date(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "OUT", tmp_path / "out.html")
    assert build.build().read_text(encoding="utf-8") == (ROOT / "EarthImporter.html").read_text(encoding="utf-8"), \
        "EarthImporter.html n'est pas a jour : lancer python web/build.py"


@pytest.fixture
def page():
    sp = pytest.importorskip("playwright.sync_api")
    with sp.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            exe = next(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"), None)
            if exe is None:
                pytest.skip("Chromium introuvable")
            browser = p.chromium.launch(executable_path=str(exe))
        pg = browser.new_page(accept_downloads=True)
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        yield pg
        browser.close()
        assert not errors, errors


@pytest.mark.parametrize("fmt,ext", [("obj", "obj"), ("usd", "usda"), ("glb", "glb")])
def test_web_export(page, tmp_path, fmt, ext):
    srv = fk.FakeServer()
    try:
        page.goto((ROOT / "EarthImporter.html").as_uri() + "?base=" + srv.url)
        page.evaluate("document.getElementById('level').add(new Option('6', '6', true, true))")
        page.select_option("#fmt", fmt)
        page.fill("#name", "t")
        with page.expect_download(timeout=30000) as dl:
            page.click("#download")
        path = tmp_path / "t.zip"
        dl.value.save_as(path)
    finally:
        srv.close()
    z = zipfile.ZipFile(path)
    assert z.testzip() is None
    names = set(z.namelist())
    assert f"t/t.{ext}" in names and {"t/textures/30604_0.jpg", "t/textures/306040_0.jpg"} <= names
    assert z.read("t/textures/30604_0.jpg") == fk.JPEG
    data = z.read(f"t/t.{ext}")
    if fmt == "obj":
        assert sum(1 for line in data.decode().splitlines() if line.startswith("f ")) == 44  # comme test_pipeline
    elif fmt == "usd":
        assert data.decode().count("faceVertexCounts") == 2
    else:
        assert data[:4] == b"glTF"
    assert not any(f"306047" in n for n in names)  # tuile hors zone


def test_web_protocol_error(page):
    files = fk.build_world()
    files["PlanetoidMetadata"] = b"\x0b\x00garbage"
    srv = fk.FakeServer(files)
    try:
        page.goto((ROOT / "EarthImporter.html").as_uri() + "?base=" + srv.url)
        page.click("#download")
        page.wait_for_function("document.body.dataset.state === 'error'", timeout=20000)
        assert "Protocole Google modifié" in page.inner_text("#log")
    finally:
        srv.close()
