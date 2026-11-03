"""Calage LiDAR / Google : decalage connu retrouve, inversion Nord/Sud detectee (Python et JS)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import align  # noqa: E402

SHIFT = (7.5, -12.0, 40.0)   # a ajouter au LiDAR pour le poser sur "Google"


def city(e, n):
    rng = np.random.default_rng(3)
    blds = np.column_stack([rng.uniform(-180, 180, 120), rng.uniform(-180, 180, 120),
                            rng.uniform(8, 33, 120), rng.uniform(6, 26, 120)])
    z = 30 + 0.02 * e
    for be, bn, bh, bs in blds:
        z = np.where((np.abs(e - be) < bs) & (np.abs(n - bn) < bs), np.maximum(z, 30 + bh), z)
    return z


def grid_mesh(step, dE=0.0, dN=0.0, dH=0.0, flip=False):
    n = int(round(400 / step)) + 1
    t = -200 + np.arange(n) * step
    E, N = np.meshgrid(t, t)
    Z = city(E, -N if flip else N)
    v = np.column_stack([(E + dE).ravel(), (N + dN).ravel(), (Z + dH).ravel()])
    j, i = np.mgrid[0:n - 1, 0:n - 1]
    a = (j * n + i).ravel()
    faces = np.concatenate([np.column_stack([a, a + n, a + 1]), np.column_stack([a + 1, a + n, a + n + 1])])
    return v, faces


def test_python_finds_shift_and_flip():
    ref = align.samples(*grid_mesh(1.5), 200)
    sol = align.solve(ref, align.samples(*grid_mesh(1.0, -SHIFT[0], -SHIFT[1], -SHIFT[2]), 200), 200)
    assert abs(sol["dE"] - SHIFT[0]) <= 0.5 and abs(sol["dN"] - SHIFT[1]) <= 0.5
    assert abs(sol["dH"] - SHIFT[2]) < 0.5 and sol["score"] > 0.9
    bad = align.solve(ref, align.samples(*grid_mesh(1.0, -SHIFT[0], -SHIFT[1], -SHIFT[2], flip=True), 200), 200)
    assert bad["coarse"] < sol["coarse"] - 0.3   # le LiDAR inverse Nord/Sud se voit


def test_js_matches_python():
    node = shutil.which("node")
    if not node:
        pytest.skip("node absent")
    ref_v, ref_f = grid_mesh(1.5)
    lid_v, lid_f = grid_mesh(1.0, -SHIFT[0], -SHIFT[1], -SHIFT[2])
    to_tile = lambda v, f: {"pos": np.column_stack([v[:, 0], v[:, 2], -v[:, 1]]).ravel().tolist(),
                            "faces": f.ravel().tolist()}
    script = f"""
      const {{ ALIGN }} = require({json.dumps(str(ROOT / 'web' / 'src' / 'align.js'))});
      const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
      const ref = ALIGN.samples([d.ref], 200), lid = ALIGN.samples([d.lid], 200);
      const s = ALIGN.solve(ref, lid, 200);
      const t = {{ pos: Float64Array.from(d.lid.pos), faces: d.lid.faces }};
      ALIGN.apply(t, s);
      console.log(JSON.stringify({{ s, first: Array.from(t.pos.slice(0, 3)) }}));"""
    out = subprocess.run([node, "-e", script], input=json.dumps({"ref": to_tile(ref_v, ref_f), "lid": to_tile(lid_v, lid_f)}),
                         capture_output=True, text=True, check=True).stdout
    res = json.loads(out)
    s = res["s"]
    assert abs(s["dE"] - SHIFT[0]) <= 0.5 and abs(s["dN"] - SHIFT[1]) <= 0.5 and abs(s["dH"] - SHIFT[2]) < 0.5
    # apply : Y en haut, -Z = Nord
    x, y, z = res["first"]
    assert abs(x - (lid_v[0, 0] + s["dE"])) < 1e-9 and abs(y - (lid_v[0, 2] + s["dH"])) < 1e-9
    assert abs(z - (-(lid_v[0, 1] + s["dN"]))) < 1e-9
