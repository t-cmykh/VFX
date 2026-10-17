"""Tests hors ligne : decodeur (aller-retour avec l'encodeur de test) et pipeline complet sur faux serveur.

    cd earth_importer && python -m pytest tests -q
"""
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import rocktree as rt  # noqa: E402
from core.geo import LocalFrame, lla_to_ecef  # noqa: E402
from core.job import Job, Params  # noqa: E402
from core.pbwire import Message  # noqa: E402
from tests import fake_rocktree as fk  # noqa: E402


def test_vertices_roundtrip():
    pos = np.random.default_rng(0).integers(0, 256, (50, 3))
    assert (rt.unpack_vertices(fk.encode_vertices(pos)) == pos).all()


def test_texcoords_roundtrip():
    uv = np.random.default_rng(1).integers(0, 1000, (40, 2))
    out, mods = rt.unpack_texcoords(fk.encode_texcoords(uv, (1000, 1000)), 40)
    assert mods == (1000, 1000) and (out == uv).all()


def test_strip_and_triangles():
    strip = [0, 1, 2, 3, 3, 4, 4, 5, 6]
    dec = rt.unpack_indices(fk.encode_strip(strip))
    assert dec.tolist() == strip
    tris = rt.strip_to_triangles(dec).tolist()
    assert tris[0] == [0, 1, 2] and tris[1] == [1, 3, 2]  # orientation alternee
    assert all(len(set(t)) == 3 for t in tris)


def test_path_and_flags():
    for path, flags in [("3", 8), ("0417", 4), ("72", 16)]:
        assert rt.unpack_path_and_flags(fk.pack_path_and_flags(path, flags)) == (path, flags)


def test_node_decode_and_position():
    center = lla_to_ecef(fk.LAT, fk.LON, 35.0)
    node = rt.parse_node(fk.node_bytes(center, 100, 4, lambda i: i % 2))
    mesh = node.meshes[0]
    enu = LocalFrame(fk.LAT, fk.LON).to_enu(rt.mesh_to_ecef(node, mesh))
    assert abs(enu[:, 0].min() + 50) < 0.5 and abs(enu[:, 0].max() - 50) < 0.5
    assert abs(enu[:, 2].mean() - 35) < 0.5
    assert len(mesh.triangles) == 3 * 3 * 2
    assert set(mesh.octants.tolist()) == {0, 1}
    for t in mesh.triangles:  # un triangle n'a qu'un seul octant
        assert len(set(mesh.octants[t].tolist())) == 1
    assert mesh.texture == fk.JPEG and mesh.uv is not None


def test_pbwire_unknown_wiretype():
    with pytest.raises(ValueError):
        Message(bytes([0x0B]))  # type filaire 3 (groupes) non gere


@pytest.fixture
def server():
    s = fk.FakeServer()
    yield s
    s.close()


def make_job(server, tmp_path, events, **kw):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg.update(base_url=server.url, min_interval=0, max_retries=1)
    params = Params(lat=fk.LAT, lon=fk.LON, radius=kw.pop("radius", 200), level=kw.pop("level", 6),
                    out_dir=str(tmp_path), name="test", **kw)
    return Job(cfg, params, lambda k, d: events.append((k, d)))


def logs(events, level=None):
    return [d["msg"] for k, d in events if k == "log" and (level is None or d["level"] == level)]


def test_full_pipeline_obj(server, tmp_path):
    events = []
    make_job(server, tmp_path, events).run()
    assert not logs(events, "ERROR"), logs(events)
    assert events[-1] == ("state", {"state": "finished", "path": str(tmp_path / "test" / "test.obj")})
    # tuile hors zone jamais demandee
    assert not any("306047" in r for r in server.requests)
    obj = (tmp_path / "test" / "test.obj").read_text()
    faces = [l for l in obj.splitlines() if l.startswith("f ")]
    # parent 4x4 : octant 0 (1 colonne, 6 tris) exclu -> 12 tris ; enfant 5x5 : 32 tris
    assert len(faces) == 12 + 32
    assert "usemtl" in obj and (tmp_path / "test" / "test.mtl").exists()
    assert len(list((tmp_path / "test" / "textures").glob("*.jpg"))) == 2
    vs = np.array([[float(x) for x in l.split()[1:]] for l in obj.splitlines() if l.startswith("v ")])
    assert abs(vs[:, 1].mean() - 35) < 0.5  # Y = hauteur


def test_geometry_only_and_crop(server, tmp_path):
    events = []
    make_job(server, tmp_path, events, content="geo", radius=20).run()
    obj = (tmp_path / "test" / "test.obj").read_text()
    assert "vt " not in obj and not (tmp_path / "test" / "textures").exists()
    vs = np.array([[float(x) for x in l.split()[1:]] for l in obj.splitlines() if l.startswith("v ")])
    assert np.hypot(vs[:, 0], vs[:, 2]).min() < 20  # decoupe autour du rayon


def test_estimate_only(server, tmp_path):
    events = []
    job = make_job(server, tmp_path, events)
    job.estimate_only = True
    job.run()
    est = [d for k, d in events if k == "estimate"]
    assert est and est[0]["tiles"] == 2
    assert not any(r.startswith("NodeData") for r in server.requests)


def test_protocol_change_detected(tmp_path):
    files = fk.build_world()
    files["PlanetoidMetadata"] = b"\x0b\x00garbage"
    s = fk.FakeServer(files)
    try:
        events = []
        make_job(s, tmp_path, events).run()
        assert any("Protocole Google modifie" in m for m in logs(events, "ERROR"))
        assert events[-1][1]["state"] == "error"
    finally:
        s.close()


def test_server_unreachable(tmp_path):
    s = fk.FakeServer({})
    try:
        events = []
        make_job(s, tmp_path, events).run()
        assert any("injoignable" in m for m in logs(events, "ERROR"))
    finally:
        s.close()


def test_cancel(server, tmp_path):
    events = []
    job = make_job(server, tmp_path, events)
    job.cancel.set()
    job.run()
    assert events[-1][1]["state"] == "cancelled"
