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
    monkeypatch.setattr(build, "SITE_INDEX", None)
    built = build.build().read_text(encoding="utf-8")
    assert built == (ROOT / "EarthImporter.html").read_text(encoding="utf-8"), \
        "EarthImporter.html n'est pas a jour : lancer python web/build.py"
    assert built == (ROOT.parent / "index.html").read_text(encoding="utf-8"), \
        "index.html (accueil du site) n'est pas a jour : lancer python web/build.py"


@pytest.fixture
def page():
    sp = pytest.importorskip("playwright.sync_api")
    with sp.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            exe = next((x for pat in ("chromium-*/chrome-linux*/chrome", "chromium")
                        for x in Path("/opt/pw-browsers").glob(pat) if x.is_file()), None)
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
        lines = data.decode().splitlines()
        assert sum(1 for line in lines if line.startswith("f ")) == 44  # comme test_pipeline
        ys = [float(line.split()[2]) for line in lines if line.startswith("v ")]
        assert abs(sum(ys) / len(ys) - 35) < 0.5  # hauteur du faux monde : meme repere (sphere) que Python
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
    assert page.locator("#level").count() == 0          # toujours la meilleure definition
    assert page.locator(".leaflet-control-layers").count() == 0  # un seul fond de carte
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


def test_web_frame_matches_reference_camera(page):
    page.goto((ROOT / "EarthImporter.html").as_uri())
    enu = page.evaluate("""() => Array.from(GEO.frame(40.7005, -74.0186).toEnu([1329866.230289, -4643494.267515, 4154677.131562]))""")
    assert abs(enu[0]) < 30 and abs(enu[1]) < 30 and abs(enu[2] - 169) < 5


def test_web_lidar(page, tmp_path):
    """LiDAR seul (faux services IGN) : meme placement et meme orientation que la version Python."""
    import numpy as np
    from tests import fake_ign as fi
    ign = fi.FakeIgn()
    try:
        page.goto((ROOT / "EarthImporter.html").as_uri() + "?ign=" + ign.url)
        page.select_option("#content", "none")
        page.select_option("#lidar", "both")
        page.select_option("#lidarres", "2")
        page.fill("#side", "200")
        page.press("#side", "Enter")
        page.fill("#name", "t")
        with page.expect_download(timeout=30000) as dl:
            page.click("#download")
        path = tmp_path / "t.zip"
        dl.value.save_as(path)
    finally:
        ign.close()
    z = zipfile.ZipFile(path)
    assert set(z.namelist()) == {"t/t_lidar_mns.obj", "t/t_lidar_mnt.obj"}
    lines = z.read("t/t_lidar_mns.obj").decode().splitlines()
    v = np.array([[float(x) for x in l.split()[1:]] for l in lines if l.startswith("v ")])
    north = -v[:, 2]
    assert np.abs(v[:, [0, 2]]).max() < 105  # carre de 200 m
    ground = np.abs(v[:, 0] + 60) < 40
    assert abs(np.polyfit(north[ground], v[ground, 1], 1)[0] - 0.1) < 0.01
    roof = (np.abs(v[:, 0] - 60) < 15) & (np.abs(north) < 15)
    assert (v[roof, 1] - (100 + 0.1 * north[roof]) > 19).all()


def test_preview_3d(page):
    """Tester : la geometrie s'affiche en 3D, sans telechargement de fichier."""
    srv = fk.FakeServer()
    try:
        page.set_viewport_size({"width": 1200, "height": 800})
        page.goto((ROOT / "EarthImporter.html").as_uri() + "?base=" + srv.url)
        downloads = []
        page.on("download", lambda d: downloads.append(d))
        page.click("#estimate")
        page.wait_for_function("document.body.dataset.state !== 'running'", timeout=30000)
        assert page.evaluate("document.body.dataset.state") == "estimate"
        assert page.is_visible("#viewer canvas") and not page.is_visible("#map")
        assert "Aperçu : 44 triangles" in page.inner_text("#log")
        # l'image n'est pas vide : des pixels differents du fond
        n = page.evaluate("""() => {
            const c = document.querySelector('#viewer canvas'), gl = c.getContext('webgl2') || c.getContext('webgl');
            const px = new Uint8Array(c.width * c.height * 4);
            gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
            let k = 0; for (let i = 0; i < px.length; i += 4) if (Math.abs(px[i] - 0x1b) > 8) k++;
            return k; }""")
        assert n > 1000, n
        page.click("#tab-map")
        assert page.is_visible("#map") and not page.is_visible("#viewer")
        assert not downloads
    finally:
        srv.close()


def test_place_search(page):
    """Nom de lieu -> recherche (Photon simule), la zone se centre sur le premier resultat."""
    import json
    photon = {"features": [
        {"geometry": {"coordinates": [2.3376, 48.8606]}, "properties": {"name": "Musée du Louvre", "city": "Paris", "country": "France"}},
        {"geometry": {"coordinates": [0.1, 45.0]}, "properties": {"name": "Louvre", "country": "France"}},
    ]}
    page.route("https://photon.komoot.io/**", lambda r: r.fulfill(
        status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"}, body=json.dumps(photon)))
    page.goto((ROOT / "EarthImporter.html").as_uri())
    page.fill("#coords", "louvre")
    page.press("#coords", "Enter")
    page.wait_for_selector("#places li")
    center = page.evaluate("[marker.getLatLng().lat, marker.getLatLng().lng]")
    assert abs(center[0] - 48.8606) < 1e-6 and abs(center[1] - 2.3376) < 1e-6
    assert page.locator("#places li").count() == 2
    page.click("#places li:nth-child(2)")
    center = page.evaluate("[marker.getLatLng().lat, marker.getLatLng().lng]")
    assert abs(center[0] - 45.0) < 1e-6 and page.is_hidden("#places")
    # des coordonnees restent acceptees telles quelles
    page.fill("#coords", "48.1, 2.2")
    page.press("#coords", "Enter")
    assert page.evaluate("marker.getLatLng().lat") == 48.1


@pytest.mark.parametrize("flip_v", [True, False])
def test_lidar_texture_orientation(page, flip_v):
    """Texture LiDAR : une tuile Google rouge seulement au Nord-Est -> le sommet LiDAR Nord-Est lit du rouge,
    le sommet Sud-Ouest lit le fond gris (vue de dessus, UV de projection verticale, conventions des exporteurs)."""
    page.goto((ROOT / "EarthImporter.html").as_uri())
    res = page.evaluate("""async (flipV) => {
        const c = document.createElement('canvas'); c.width = c.height = 16;
        const x = c.getContext('2d'); x.fillStyle = '#ff0000'; x.fillRect(0, 0, 16, 16);
        const jpeg = new Uint8Array(await (await new Promise(r => c.toBlob(r, 'image/jpeg', 0.95))).arrayBuffer());
        // quad Google : Est 10..90, Nord 10..90, a 20 m de haut (Y en haut, -Z = Nord), UV rocktree
        const pos = [10, 20, -10, 90, 20, -10, 90, 20, -90, 10, 20, -90];
        const uv = [0, 0, 1, 0, 1, 1, 0, 1];
        const google = { name: 'g', pos: Float64Array.from(pos), faces: Uint32Array.from([0, 1, 2, 0, 2, 3]), uv: Float64Array.from(uv), texture: jpeg };
        const out = await BAKE.topView([google], 100, 512, flipV, { progress() {} });
        // sommets LiDAR : Nord-Est (50, 50) et Sud-Ouest (-50, -50)
        const lid = { pos: Float64Array.from([50, 0, -50, -50, 0, 50]) };
        const uvL = BAKE.planarUv(lid, 100, flipV);
        // lecture comme un logiciel 3D : V OpenGL (bas = 0) apres uvForObj
        const img = new Image(); img.src = URL.createObjectURL(new Blob([out.jpeg], { type: 'image/jpeg' })); await img.decode();
        const cv = document.createElement('canvas'); cv.width = cv.height = out.size;
        const g = cv.getContext('2d'); g.drawImage(img, 0, 0);
        const read = (k) => { const u = uvL[2 * k], v = uvForObj(uvL[2 * k + 1], flipV);
            return Array.from(g.getImageData(Math.floor(u * out.size), Math.floor((1 - v) * out.size), 1, 1).data.slice(0, 3)); };
        return { ne: read(0), sw: read(1) };
    }""", flip_v)
    assert res["ne"][0] > 200 and res["ne"][1] < 60, res
    assert all(abs(c - 128) < 20 for c in res["sw"]), res
