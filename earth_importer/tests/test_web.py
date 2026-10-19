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


def test_controls_populated(page):
    page.goto((ROOT / "EarthImporter.html").as_uri())
    levels = page.eval_on_selector_all("#level option", "els => els.map(e => e.value)")
    assert levels == [str(n) for n in range(15, 22)] and page.input_value("#level") == "18"
    assert page.input_value("#side") == "400" and page.input_value("#coords")


def test_draw_square(page):
    page.set_viewport_size({"width": 1200, "height": 800})
    page.goto((ROOT / "EarthImporter.html").as_uri())
    box = page.locator("#map").bounding_box()
    x0, y0 = box["x"] + 300, box["y"] + 150
    page.click("#draw")
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x0 + 100, y0 + 40, steps=5)
    page.mouse.move(x0 + 200, y0 + 80, steps=5)  # plus large que haut : le carre suit la plus grande dimension
    page.mouse.up()
    side = float(page.input_value("#side"))
    assert 100 < side < 10000
    assert "active" not in (page.get_attribute("#draw", "class") or "")
    # la zone est un carre : memes dimensions en metres sur les deux axes (projection locale)
    w, h = page.evaluate("""() => {
        const b = square.getBounds(), c = b.getCenter();
        return [map.distance([c.lat, b.getWest()], [c.lat, b.getEast()]), map.distance([b.getSouth(), c.lng], [b.getNorth(), c.lng])];
    }""")
    assert abs(w - side) / side < 0.01 and abs(h - side) / side < 0.01
    # centre du carre = coordonnees affichees, et pas le point du relachement de la souris
    lat, lon = (float(v) for v in page.input_value("#coords").split(","))
    c = page.evaluate("() => square.getBounds().getCenter()")
    assert abs(lat - c["lat"]) < 1e-5 and abs(lon - c["lng"]) < 1e-5


ATLAS_CHECK_JS = """async ([flipV, resolution]) => {
  // 3 tuiles avec des textures en degrade (tailles differentes), UV aleatoires dans une sous-zone
  const rnd = (() => { let s = 7; return () => (s = (s * 16807) % 2147483647) / 2147483647; })();
  async function gradient(w, h, k) {
    const c = new OffscreenCanvas(w, h), g = c.getContext("2d");
    const grad = g.createLinearGradient(0, 0, w, h);
    grad.addColorStop(0, ["#ff0000", "#00ff00", "#0000ff"][k]);
    grad.addColorStop(1, ["#00ffff", "#ff00ff", "#ffff00"][k]);
    g.fillStyle = grad; g.fillRect(0, 0, w, h);
    const blob = await c.convertToBlob({ type: "image/jpeg", quality: 0.95 });
    return new Uint8Array(await blob.arrayBuffer());
  }
  const tiles = [];
  for (const [k, [w, h]] of [[256, 256], [512, 256], [128, 384]].entries()) {
    const n = 30, uv = new Float64Array(2 * n);
    const u0 = 0.1 + 0.3 * rnd(), v0 = 0.1 + 0.3 * rnd();
    for (let i = 0; i < n; i++) { uv[2 * i] = u0 + 0.5 * rnd(); uv[2 * i + 1] = v0 + 0.5 * rnd(); }
    tiles.push({ name: `t${k}`, pos: new Float64Array(3 * n), faces: Uint32Array.from({ length: 27 }, (_, i) => i), uv,
                 texture: await gradient(w, h, k) });
  }
  const merged = await mergeTiles(tiles, { withTex: true, flipV, resolution });
  async function pixels(bytes) {
    const img = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
    const c = new OffscreenCanvas(img.width, img.height), g = c.getContext("2d");
    g.drawImage(img, 0, 0);
    return { w: img.width, h: img.height, d: g.getImageData(0, 0, img.width, img.height).data };
  }
  const top = (v) => (flipV ? v : 1 - v);
  const sample = (p, u, v) => {
    const x = Math.min(p.w - 1, Math.floor(u * p.w)), y = Math.min(p.h - 1, Math.floor(top(v) * p.h));
    const i = 4 * (y * p.w + x);
    return [p.d[i], p.d[i + 1], p.d[i + 2]];
  };
  const atlas = await pixels(merged.texture);
  let worst = 0, off = 0;
  for (const t of tiles) {
    const src = await pixels(t.texture);
    for (let i = 0; i < t.uv.length; i += 2) {
      const a = sample(src, t.uv[i], t.uv[i + 1]);
      const b = sample(atlas, merged.uv[2 * off + i], merged.uv[2 * off + i + 1]);
      worst = Math.max(worst, ...a.map((x, j) => Math.abs(x - b[j])));
    }
    off += t.uv.length / 2;
  }
  return { worst, size: atlas.w, faces: merged.faces.length, maxFace: Math.max(...merged.faces), verts: merged.pos.length / 3 };
}"""


@pytest.mark.parametrize("flip_v", [True, False])
@pytest.mark.parametrize("resolution", ["auto", 256])
def test_atlas_preserves_texture_lookup(page, flip_v, resolution):
    """Chaque UV remappee dans l'atlas doit retomber sur la meme couleur que dans la texture d'origine."""
    page.goto((ROOT / "EarthImporter.html").as_uri())
    r = page.evaluate(ATLAS_CHECK_JS, [flip_v, resolution])
    assert r["faces"] == 81 and r["maxFace"] < r["verts"] == 90  # indices decales par tuile
    assert r["size"] == (512 if resolution == "auto" else 256)
    assert r["worst"] < 40, r  # degrade lisse : seuls JPEG et la reduction d'echelle doivent jouer


def jpeg_size(data):
    i = 2
    while i < len(data):
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + length
    raise ValueError("pas de SOF")


@pytest.mark.parametrize("fmt,ext", [("obj", "obj"), ("glb", "glb")])
def test_web_merged_export(page, tmp_path, fmt, ext):
    srv = fk.FakeServer()
    try:
        page.goto((ROOT / "EarthImporter.html").as_uri() + "?base=" + srv.url)
        page.evaluate("document.getElementById('level').add(new Option('6', '6', true, true))")
        page.select_option("#fmt", fmt)
        page.select_option("#merge", "1")
        page.select_option("#atlas", "1024")
        page.fill("#name", "m")
        with page.expect_download(timeout=30000) as dl:
            page.click("#download")
        dl.value.save_as(tmp_path / "m.zip")
    finally:
        srv.close()
    z = zipfile.ZipFile(tmp_path / "m.zip")
    names = set(z.namelist())
    assert names >= {f"m/m.{ext}", "m/textures/fusion.jpg"} and not any("30604" in n for n in names)
    assert jpeg_size(z.read("m/textures/fusion.jpg")) == (1024, 1024)
    data = z.read(f"m/m.{ext}")
    if fmt == "obj":
        text = data.decode()
        assert text.count("\no ") == 1 and text.count("usemtl") == 1
        assert sum(1 for line in text.splitlines() if line.startswith("f ")) == 44
        vts = [tuple(map(float, l.split()[1:])) for l in text.splitlines() if l.startswith("vt ")]
        assert all(0 <= u <= 1 and 0 <= v <= 1 for u, v in vts)
    else:
        assert data.count(b'"primitives"') == 1
