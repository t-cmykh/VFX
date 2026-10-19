"""Faux serveur rocktree pour les tests : encode des donnees synthetiques au format filaire.

Monde de test, autour de (LAT, LON) :
  - "3060"   : noeud de niveau 4 avec bloc enfant
  - "30604"  : tuile parente (grille 4x4 sur 100 m, octants 0 et 1)
  - "306040" : tuile enfant, plus fine, qui remplace l'octant 0 du parent
  - "306047" : tuile hors zone (a ~5 km), doit etre ignoree
"""
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from core.geo import enu_rotation, lla_to_ecef

LAT, LON = 48.8584, 2.2945
JPEG = bytes.fromhex(  # JPEG 16x16 valide
    "ffd8ffe000104a46494600010100000100010000ffdb004300100b0c0e0c0a100e0d0e1211101318281a181616183123251d283a333d3c3933383740485c4e404457453738506d51575f626768673e4d71797064785c656763ffdb0043011112121815182f1a1a2f634238426363636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363636363ffc00011080010001003012200021101031101ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffc4001f0100030101010101010101010000000000000102030405060708090a0bffc400b51100020102040403040705040400010277000102031104052131061241510761711322328108144291a1b1c109233352f0156272d10a162434e125f11718191a262728292a35363738393a434445464748494a535455565758595a636465666768696a737475767778797a82838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9faffda000c03010002110311003f00928a28af18f58fffd9")


# ---------------------------------------------------------------- encodeur filaire
def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def f_varint(num, v):
    return varint(num << 3) + varint(v)


def f_bytes(num, b):
    return varint(num << 3 | 2) + varint(len(b)) + b


def f_float(num, v):
    return varint(num << 3 | 5) + struct.pack("<f", v)


def f_packed_doubles(num, vals):
    return f_bytes(num, struct.pack(f"<{len(vals)}d", *vals))


def f_packed_floats(num, vals):
    return f_bytes(num, struct.pack(f"<{len(vals)}f", *vals))


# ---------------------------------------------------------------- encodage rocktree
def pack_path_and_flags(path, flags):
    v = flags
    for c in reversed(path):
        v = (v << 3) | int(c)
    return (v << 2) | (len(path) - 1)


def pack_obb(center, head, mpt, extent_m):
    c = np.round((np.asarray(center) - head) / mpt).astype(int)
    e = min(255, int(np.ceil(extent_m / mpt)))
    return struct.pack("<hhhBBBHHH", *c, e, e, e, 0, 0, 0)


def encode_vertices(pos):
    d = np.diff(np.vstack([[0, 0, 0], pos]).astype(np.int64), axis=0) & 0xFF
    return d.T.astype(np.uint8).tobytes()


def encode_texcoords(uv, mod=(65536, 65536)):
    d = np.diff(np.vstack([[0, 0], uv]).astype(np.int64), axis=0)
    du, dv = d[:, 0] % mod[0], d[:, 1] % mod[1]
    return struct.pack("<HH", mod[0] - 1, mod[1] - 1) + np.stack(
        [du & 0xFF, dv & 0xFF, du >> 8, dv >> 8]).astype(np.uint8).tobytes()


def encode_strip(strip):
    out, zeros = bytearray(varint(len(strip))), 0
    for s in strip:
        val = zeros - s
        assert val >= 0, "le strip doit introduire les sommets dans l'ordre"
        out += varint(val)
        if val == 0:
            zeros += 1
    return bytes(out)


def encode_octant_counts(counts):
    """counts : liste par (couche*8 + octant) du nombre d'indices du strip."""
    return varint(len(counts)) + b"".join(varint(c) for c in counts)


def grid_mesh(n, octant_of_col):
    """Grille n x n en triangle strips (un strip par rangee de colonnes, avec degeneres)."""
    # cles (octant, i, j) : comme chez Google, chaque octant a ses propres sommets
    groups = {}
    for i in range(n - 1):
        o = octant_of_col(i)
        col = []
        for j in range(n):
            col += [(o, i, j), (o, i + 1, j)]
        groups.setdefault(o, []).append(col)
    keys, counts = [], [0] * 8
    for o in sorted(groups):
        if keys:  # liaison degeneree entre octants, comptee dans l'octant precedent
            keys += [keys[-1], groups[o][0][0]]
            counts[prev] += 2
        seg = []
        for col in groups[o]:
            if seg:
                seg += [seg[-1], col[0]]  # triangles degeneres de liaison
            seg += col
        keys += seg
        counts[o] = len(seg)
        prev = o
    order = {}
    strip = [order.setdefault(k, len(order)) for k in keys]
    pos = np.array([(i * 255 // (n - 1), j * 255 // (n - 1), 128) for (_, i, j) in order])
    return pos, strip, counts


def node_bytes(center_ecef, size_m, n, octant_of_col):
    pos, strip, counts = grid_mesh(n, octant_of_col)
    # matrice : pos (0-255) -> ECEF, la grille couvre size_m x size_m dans le plan Est/Nord
    rot = enu_rotation(LAT, LON)
    s = size_m / 255.0
    m = np.zeros((4, 4))
    m[0, :3] = rot[0] * s
    m[1, :3] = rot[1] * s
    m[2, :3] = rot[2] * s
    m[3, :3] = center_ecef - (rot[0] + rot[1] + rot[2]) * s * 127.5
    m[3, 3] = 1
    uv = (pos[:, :2].astype(np.int64) * 257)  # 0..65535
    tex = f_bytes(1, JPEG) + f_varint(2, 1) + f_varint(3, 16) + f_varint(4, 16)
    mesh = (f_bytes(1, encode_vertices(pos)) + f_bytes(7, encode_texcoords(uv)) +
            f_bytes(3, encode_strip(strip)) + f_bytes(8, encode_octant_counts(counts)) +
            f_bytes(6, tex))
    return f_packed_doubles(1, m.ravel().tolist()) + f_bytes(2, mesh)


def build_world():
    center = lla_to_ecef(LAT, LON, 35.0)
    far = lla_to_ecef(LAT + 0.045, LON, 35.0)
    head = center
    planetoid = f_bytes(1, f_varint(2, 100)) + f_float(2, 6371010.0)

    def meta(path, flags, c, ext, mpt=1.0):
        return f_bytes(1, f_varint(1, pack_path_and_flags(path, flags)) + f_varint(2, 7) +
                       f_bytes(3, pack_obb(c, head, mpt, ext)) + f_float(4, mpt) + f_varint(5, 200) +
                       f_varint(8, 1))

    root = f_bytes(2, f_varint(2, 100)) + f_packed_doubles(3, head.tolist()) + f_packed_floats(4, [100, 50, 20, 10])
    for p in ("3", "30", "306"):
        root += meta(p, 8, center, 250, 100)          # NODATA
    root += meta("3060", 8, center, 250, 10)          # NODATA mais bloc enfant
    child = f_bytes(2, f_varint(2, 200)) + f_packed_doubles(3, head.tolist()) + f_packed_floats(4, [1, 1])
    child += meta("4", 4 * 0, center, 100)            # 30604
    child += meta("40", 4, center, 60)                # 306040 (feuille)
    child += meta("47", 4, far, 60)                   # 306047 (hors zone)

    nodes = {
        "30604": node_bytes(center, 100, 4, lambda i: 0 if i == 0 else 1),
        "306040": node_bytes(center, 50, 5, lambda i: 0),
        "306047": node_bytes(far, 50, 3, lambda i: 0),
    }
    files = {"PlanetoidMetadata": planetoid,
             "BulkMetadata/pb=!1m2!1s!2u100": root,
             "BulkMetadata/pb=!1m2!1s3060!2u200": child}
    for p, data in nodes.items():
        files[f"NodeData/pb=!1m2!1s{p}!2u7!2e1!4b0"] = data
    return files


class FakeServer:
    def __init__(self, files=None):
        self.files = files if files is not None else build_world()
        self.requests = []
        files_ref, log = self.files, self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                key = self.path.lstrip("/")
                log.append(key)
                data = files_ref.get(key)
                self.send_response(200 if data is not None else 404)
                self.send_header("Access-Control-Allow-Origin", "*")  # version web (fichier local)
                self.end_headers()
                if data is not None:
                    self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
