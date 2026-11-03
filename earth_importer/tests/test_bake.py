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

from core.bake import TopViewBaker, planar_uv  # noqa: E402
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
                   lidar="mns", lidar_res=2, lidar_tex=2048)
        Job(cfg, p, lambda k, d: events.append((k, d))).run()
    finally:
        srv.close()
        ign.close()
    assert not [d for k, d in events if k == "log" and d["level"] == "ERROR"], events
    out = tmp_path / "t"
    obj = (out / "t_lidar_mns.obj").read_text()
    assert "mtllib t_lidar_mns.mtl" in obj and "usemtl m_lidar_mns" in obj and "\nvt " in obj
    assert "map_Kd textures/lidar_mns.jpg" in (out / "t_lidar_mns.mtl").read_text()
    img = Image.open(out / "textures" / "lidar_mns.jpg")
    assert img.size == (2048, 2048)
    a = np.asarray(img).astype(int)
    assert (np.abs(a - 128).sum(axis=2) > 30).mean() > 0.05      # la 3D Google y est rendue
    assert any("Texture LiDAR" in d["msg"] for k, d in events if k == "log")
