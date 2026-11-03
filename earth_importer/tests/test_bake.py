"""Texture du LiDAR (version Python) : vue de dessus de la 3D Google, orientation, export, vitesse."""
import sys
import time
from io import BytesIO
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import bake  # noqa: E402
from core.bake import TiledTopView, TopViewBaker, split  # noqa: E402
from core.job import Job, Params  # noqa: E402
from tests import fake_ign as fi  # noqa: E402
from tests import fake_rocktree as fk  # noqa: E402


def jpeg(color, size=16):
    buf = BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def quad(e0, e1, n0, n1, h):
    """Quad horizontal au format OBJ (Y en haut, -Z = Nord), UV OpenGL."""
    v = np.array([[e0, h, -n0], [e1, h, -n0], [e1, h, -n1], [e0, h, -n1]], float)
    return v, np.array([[0, 1, 2], [0, 2, 3]]), np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)


def planar_uv(v_enu, half):
    """UV OpenGL d'une image couvrant toute la zone [-half, half]."""
    return np.stack([(v_enu[:, 0] + half) / (2 * half), (v_enu[:, 1] + half) / (2 * half)], axis=1)


def read(img, uv):
    """Couleur lue comme un logiciel 3D : V OpenGL (0 = bas de l'image)."""
    a = np.asarray(img)
    s = a.shape[0]
    return a[min(int((1 - uv[1]) * s), s - 1), min(int(uv[0] * s), s - 1)]


def test_orientation_and_depth():
    b = TopViewBaker(100, 512)
    b.add(*quad(10, 90, 10, 90, 20), jpeg((255, 0, 0)))        # rouge au Nord-Est
    b.add(*quad(30, 60, 30, 60, 5), jpeg((0, 0, 255)))          # bleu en dessous : cache
    b.add(*quad(-90, -60, 60, 90, 3), jpeg((0, 255, 0)))        # vert au Nord-Ouest
    img = Image.open(BytesIO(b.jpeg()))
    uv = planar_uv(np.array([[50, 50, 0], [-50, -50, 0], [-75, 75, 0], [45, 45, 0]], float), 100)
    ne, sw, nw, top = (read(img, x) for x in uv)
    assert ne[0] > 200 and ne[2] < 60, ne
    assert all(abs(int(c) - 128) < 20 for c in sw), sw
    assert nw[1] > 200 and nw[0] < 60, nw
    assert top[0] > 200 and top[2] < 60, top     # le plus haut gagne


def test_texture_lookup_follows_uv():
    b = TopViewBaker(50, 256)
    tex = Image.new("RGB", (32, 32), (0, 0, 0))
    tex.paste((255, 255, 0), (16, 0, 32, 16))                   # quart haut-droit de l'image = jaune
    buf = BytesIO()
    tex.save(buf, "JPEG", quality=95)
    b.add(*quad(-40, 40, -40, 40, 0), buf.getvalue())           # UV (1,1) au Nord-Est
    img = Image.open(BytesIO(b.jpeg()))
    ne, sw = (read(img, x) for x in planar_uv(np.array([[20, 20, 0], [-20, -20, 0]], float), 50))
    assert ne[0] > 200 and ne[1] > 200 and ne[2] < 80, ne
    assert max(sw) < 60, sw


def test_tiled_resolution_and_split(monkeypatch):
    """Resolution d'origine (0,25 m/texel sur 200 m = 800 px) ; dalles de 256 px max -> 4 x 4, chaque morceau
    du LiDAR lit la bonne couleur dans sa dalle, UV dans [0, 1]."""
    monkeypatch.setattr(bake, "TILE_MAX", 256)
    t = TiledTopView(100)
    t.add(*quad(10, 90, 10, 90, 20), jpeg((255, 0, 0)))
    t.add(*quad(-90, -60, 60, 90, 20), jpeg((0, 255, 0)))
    plan = t.plan(0.25)
    assert plan["n"] == 4 and plan["need"] == 800 and t.bake(plan)
    # LiDAR : grille 2 m sur la zone (Est, Nord, haut)
    g = -100 + 2 * np.arange(101)
    E, N = np.meshgrid(g, g)
    v = np.column_stack([E.ravel(), N.ravel(), np.zeros(E.size)])
    j, i = np.mgrid[0:100, 0:100]
    a = (j * 101 + i).ravel()
    faces = np.concatenate([np.column_stack([a, a + 1, a + 101]), np.column_stack([a + 1, a + 102, a + 101])])
    parts = split(v, faces, plan)
    assert len(parts) == 16 and sum(len(p[3]) for p in parts) == len(faces)
    imgs = {}

    def color(e, n):
        for r, c, pv, pf, uv in parts:
            k = np.flatnonzero((np.abs(pv[:, 0] - e) < 1e-6) & (np.abs(pv[:, 1] - n) < 1e-6))
            if len(k):
                img = imgs.setdefault((r, c), Image.open(BytesIO(plan["images"][r][c])))
                return read(img, uv[k[0]])

    for r, c, pv, pf, uv in parts:
        assert uv.min() >= 0 and uv.max() <= 1
    for e, n, rgb in [(50, 50, "r"), (88, 12, "r"), (-76, 76, "g")]:
        px = color(e, n)
        assert (px[0] > 200 and px[1] < 60) if rgb == "r" else (px[1] > 200 and px[0] < 60), (e, n, px)
    assert all(abs(int(x) - 128) < 20 for x in color(-50, -50))
    assert t.plan(0.25, preview=True)["n"] == 1


def test_speed_large_triangles():
    b = TopViewBaker(200, 4096)
    t0 = time.time()
    b.add(*quad(-200, 200, -200, 200, 0), jpeg((90, 90, 90)))   # toute l'image en 2 triangles
    assert b.jpeg() and time.time() - t0 < 60


def test_pipeline_textured_lidar(tmp_path):
    srv, ign = fk.FakeServer(), fi.FakeIgn()
    try:
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
        cfg.update(base_url=srv.url, min_interval=0, max_retries=1)
        cfg["lidar"].update(ign.config())
        events = []
        p = Params(lat=fk.LAT, lon=fk.LON, radius=100, level=6, out_dir=str(tmp_path), name="t",
                   lidar="both", lidar_res=2)
        Job(cfg, p, lambda k, d: events.append((k, d))).run()
    finally:
        srv.close()
        ign.close()
    assert not [d for k, d in events if k == "log" and d["level"] == "ERROR"], events
    out = tmp_path / "t"
    for prod in ("mns", "mnt"):   # une seule texture, commune au MNS et au MNT
        obj = (out / f"t_lidar_{prod}.obj").read_text()
        assert f"mtllib t_lidar_{prod}.mtl" in obj and "usemtl m_lidar_texture" in obj and "\nvt " in obj
        assert "map_Kd textures/lidar_texture.jpg" in (out / f"t_lidar_{prod}.mtl").read_text()
    assert [x.name for x in (out / "textures").glob("lidar*")] == ["lidar_texture.jpg"]
    img = Image.open(out / "textures" / "lidar_texture.jpg")
    assert img.size == (256, 256)   # faux monde : 1 m/texel sur 200 m -> 200 px, minimum 256
    a = np.asarray(img).astype(int)
    assert (np.abs(a - 128).sum(axis=2) > 30).mean() > 0.05      # la 3D Google y est rendue
    assert any("Texture LiDAR" in d["msg"] for k, d in events if k == "log")
