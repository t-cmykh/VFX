"""LiDAR HD IGN : projection Lambert-93, maillage MNS/MNT et dalles LAZ, sur faux services IGN."""
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.job import Job, Params  # noqa: E402
from core.lidar import grid_mesh, lambert93_to_lonlat, lonlat_to_lambert93  # noqa: E402
from tests import fake_ign as fi  # noqa: E402


def test_lambert93_reference_points():
    # origine de la projection et Tour Eiffel (valeurs de reference IGN / geoportail)
    x, y = lonlat_to_lambert93(3.0, 46.5)
    assert abs(x - 700000) < 1e-3 and abs(y - 6600000) < 1e-3
    x, y = lonlat_to_lambert93(2.2945, 48.8584)
    assert abs(x - 648237.3) < 1 and abs(y - 6862271.7) < 1
    lon, lat = lambert93_to_lonlat(x, y)
    assert abs(lon - 2.2945) < 1e-9 and abs(lat - 48.8584) < 1e-9


def test_grid_mesh_faces_up_and_holes():
    z = np.zeros((3, 4))
    valid = np.ones_like(z, bool)
    tris = grid_mesh(z, valid)
    assert len(tris) == 2 * 2 * 3
    # sommets : i = Est, j = Sud (lignes du nord vers le sud) -> normales vers le haut
    pts = np.stack([np.tile(np.arange(4), 3), -np.repeat(np.arange(3), 4), np.zeros(12)], 1).astype(float)
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    assert (np.cross(b - a, c - a)[:, 2] > 0).all()
    valid[1, 1] = False
    assert len(grid_mesh(z, valid)) == 2 * (6 - 4)


@pytest.fixture
def ign():
    s = fi.FakeIgn()
    yield s
    s.close()


def make_job(ign, tmp_path, events, **kw):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg["lidar"].update(ign.config())
    params = Params(lat=fi.LAT, lon=fi.LON, radius=kw.pop("radius", 100), out_dir=str(tmp_path), name="t",
                    content=kw.pop("content", "none"), **kw)
    return Job(cfg, params, lambda k, d: events.append((k, d)))


def read_obj(path):
    lines = path.read_text().splitlines()
    v = np.array([[float(x) for x in l.split()[1:]] for l in lines if l.startswith("v ")])
    f = np.array([[int(x) for x in l.split()[1:]] for l in lines if l.startswith("f ")]) - 1
    return v, f


def errors(events):
    return [d["msg"] for k, d in events if k == "log" and d["level"] == "ERROR"]


def test_lidar_mns_mnt_obj(ign, tmp_path):
    events = []
    make_job(ign, tmp_path, events, lidar="both", lidar_res=2).run()
    assert not errors(events), errors(events)
    assert events[-1][1]["state"] == "finished"
    v, f = read_obj(tmp_path / "t" / "t_lidar_mns.obj")
    # OBJ : X = Est, Y = haut, -Z = Nord ; zone ronde de 100 m
    assert np.hypot(v[:, 0], v[:, 2]).min() < 5 and np.hypot(v[:, 0], v[:, 2]).max() < 110
    north = -v[:, 2]
    ground = np.abs(v[:, 0] + 60) < 40  # loin du "batiment" (a l'Est)
    slope = np.polyfit(north[ground], v[ground, 1], 1)[0]
    assert abs(slope - 0.1) < 0.01, slope   # pente vers le Nord : sens des lignes du BIL correct
    assert abs(np.median(v[np.hypot(v[:, 0], v[:, 2]) < 3, 1]) - 100) < 1
    roof = (np.abs(v[:, 0] - 60) < 15) & (np.abs(north) < 15)
    assert (v[roof, 1] - (100 + 0.1 * north[roof]) > 19).all()   # le batiment est bien a l'Est
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    assert (np.cross(b - a, c - a)[:, 1] > 0).mean() > 0.95      # faces vers le haut (Y)
    vt, _ = read_obj(tmp_path / "t" / "t_lidar_mnt.obj")
    assert vt[:, 1].max() < 112                                  # MNT : pas de batiment


def test_lidar_missing_mnt_falls_back(tmp_path):
    cfg_layer = "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"
    s = fi.FakeIgn(empty_layers=[cfg_layer])
    try:
        events = []
        make_job(s, tmp_path, events, lidar="mnt", lidar_res=2).run()
        assert not errors(events), errors(events)
        assert any("RGE ALTI" in r for r in s.requests[-1:]) or any("HIGHRES" in r for r in s.requests)
        assert (tmp_path / "t" / "t_lidar_mnt.obj").exists()
    finally:
        s.close()


def test_lidar_estimate_and_laz(ign, tmp_path):
    events = []
    job = make_job(ign, tmp_path, events, lidar="mns", lidar_res=1, laz=True)
    job.estimate_only = True
    job.run()
    msgs = [d["msg"] for k, d in events if k == "log"]
    assert any("grille" in m and "1 m" in m for m in msgs)
    assert any("1 dalle(s) LAZ" in m for m in msgs)
    assert any(k == "preview" for k, _ in events)       # apercu 3D du MNS
    assert not (tmp_path / "t" / "lidar_laz").exists()   # Tester ne telecharge pas les dalles LAZ

    events = []
    make_job(ign, tmp_path, events, laz=True).run()
    assert not errors(events), errors(events)
    laz = list((tmp_path / "t" / "lidar_laz").glob("*.laz"))
    assert len(laz) == 1 and laz[0].read_bytes() == fi.LAZ
    assert "Lambert-93" in (tmp_path / "t" / "lidar_laz" / "origine_repere_local.txt").read_text()


def test_nothing_requested(tmp_path):
    with pytest.raises(ValueError):
        Params(lat=fi.LAT, lon=fi.LON, content="none").validate()
