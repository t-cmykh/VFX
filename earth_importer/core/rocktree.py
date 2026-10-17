"""Decodage du format "rocktree" de Google Earth 3D (PlanetoidMetadata, BulkMetadata, NodeData).

Porte en Python/numpy depuis retroplasma/earth-reverse-engineering
(client/rocktree_decoder.h, client/rocktree_ex.h, client/rocktree_util.h).
Les numeros de champs viennent de rocktree.proto (copie dans ce dossier).
"""
import math
import struct
from dataclasses import dataclass, field

import numpy as np

from .pbwire import Message, read_varint

# NodeMetadata.Flags
FLAG_LEAF = 4
FLAG_NODATA = 8
FLAG_USE_IMAGERY_EPOCH = 16

# Texture.Format
TEX_JPG = 1
TEX_CRN_DXT1 = 6

# Mesh.Layer : on garde OVERGROUND + TERRAIN_*_WATER, on coupe a TERRAIN_HIDDEN (eau, jupes...)
LAYER_TERRAIN_HIDDEN = 3


@dataclass
class Obb:
    center: np.ndarray
    extents: np.ndarray
    orientation: np.ndarray

    @property
    def radius(self):
        return float(np.linalg.norm(self.extents))


@dataclass
class NodeMeta:
    rel_path: str
    flags: int
    epoch: int
    bulk_epoch: int
    obb: Obb | None
    meters_per_texel: float
    imagery_epoch: int | None
    texture_formats: int

    @property
    def has_data(self):
        return not self.flags & FLAG_NODATA

    @property
    def has_bulk(self):
        return len(self.rel_path) == 4 and not self.flags & FLAG_LEAF


@dataclass
class Bulk:
    path: str
    nodes: dict = field(default_factory=dict)  # chemin relatif (1 a 4 chiffres) -> NodeMeta


@dataclass
class Mesh:
    positions: np.ndarray       # (n, 3) uint8, repere local du noeud
    octants: np.ndarray         # (n,) uint8, octant enfant (0-7) de chaque sommet
    uv: np.ndarray | None       # (n, 2) float64, convention OpenGL (v=0 sur la 1re ligne de l'image)
    triangles: np.ndarray       # (m, 3) int64
    texture: bytes | None
    texture_format: int | None
    texture_size: tuple


@dataclass
class NodeData:
    matrix: np.ndarray          # (4, 4) float64 ; ecef = pos @ matrix[:3, :3] + matrix[3, :3]
    meshes: list


# ---------------------------------------------------------------- metadonnees

def parse_planetoid(buf):
    m = Message(buf)
    root = m.msg(1)
    if root is None or not root.has(2):
        raise ValueError("PlanetoidMetadata sans epoch racine")
    return {"root_epoch": root.uint(2), "radius": m.float(2, 0.0)}


def unpack_path_and_flags(v):
    level = 1 + (v & 3)
    v >>= 2
    path = ""
    for _ in range(level):
        path += str(v & 7)
        v >>= 3
    return path, v


def unpack_obb(packed, head_center, mpt):
    if len(packed) != 15:
        raise ValueError(f"OBB de {len(packed)} octets (15 attendus)")
    cx, cy, cz, ex, ey, ez, e0, e1, e2 = struct.unpack("<hhhBBBHHH", packed)
    center = np.array([cx, cy, cz], dtype=np.float64) * mpt + head_center
    extents = np.array([ex, ey, ez], dtype=np.float64) * mpt
    a0, a1, a2 = e0 * math.pi / 32768.0, e1 * math.pi / 65536.0, e2 * math.pi / 32768.0
    c0, s0, c1, s1, c2, s2 = math.cos(a0), math.sin(a0), math.cos(a1), math.sin(a1), math.cos(a2), math.sin(a2)
    orientation = np.array([
        c0 * c2 - c1 * s0 * s2, c1 * c0 * s2 + c2 * s0, s2 * s1,
        -c0 * s2 - c2 * c1 * s0, c0 * c1 * c2 - s0 * s2, c2 * s1,
        s1 * s0, -c0 * s1, c1,
    ]).reshape(3, 3)
    return Obb(center, extents, orientation)


def parse_bulk(buf, path):
    m = Message(buf)
    head_key = m.msg(2)
    head_epoch = head_key.uint(2, 0) if head_key else 0
    head_center = np.array(m.doubles(3) or [0.0, 0.0, 0.0], dtype=np.float64)
    level_mpt = m.floats(4)
    default_imagery = m.uint(5)
    default_formats = m.uint(6, 0)

    bulk = Bulk(path)
    for nm in m.msgs(1):
        rel, flags = unpack_path_and_flags(nm.uint(1, 0))
        mpt = nm.float(4)
        if mpt is None:
            mpt = level_mpt[len(rel) - 1] if len(level_mpt) >= len(rel) else 0.0
        obb_bytes = nm.bytes(3)
        bulk.nodes[rel] = NodeMeta(
            rel_path=rel,
            flags=flags,
            epoch=nm.uint(2, head_epoch),
            bulk_epoch=nm.uint(5, head_epoch),
            obb=unpack_obb(obb_bytes, head_center, mpt) if obb_bytes else None,
            meters_per_texel=mpt,
            imagery_epoch=nm.uint(7, default_imagery) if flags & FLAG_USE_IMAGERY_EPOCH else None,
            texture_formats=nm.uint(8, default_formats),
        )
    return bulk


def choose_texture_format(available, preferred=(TEX_JPG, TEX_CRN_DXT1)):
    for f in preferred:
        if available & (1 << (f - 1)):
            return f
    return preferred[0]


# ---------------------------------------------------------------- geometrie

def unpack_vertices(packed):
    n = len(packed) // 3
    d = np.frombuffer(packed, dtype=np.uint8, count=3 * n).reshape(3, n)
    return (np.cumsum(d, axis=1, dtype=np.int64) & 0xFF).astype(np.uint8).T.copy()


def unpack_texcoords(packed, n):
    if len(packed) < 4 or len(packed) - 4 != 4 * n:
        raise ValueError(f"coordonnees de texture : {len(packed)} octets pour {n} sommets")
    u_mod, v_mod = (1 + x for x in struct.unpack("<HH", packed[:4]))
    d = np.frombuffer(packed, dtype=np.uint8, offset=4).reshape(4, n).astype(np.int64)
    u = np.cumsum(d[0] + (d[2] << 8)) % u_mod
    v = np.cumsum(d[1] + (d[3] << 8)) % v_mod
    return np.stack([u, v], axis=1), (u_mod, v_mod)


def unpack_indices(packed):
    count, pos = read_varint(packed, 0)
    strip = np.empty(count, dtype=np.int64)
    zeros = 0
    for i in range(count):
        val, pos = read_varint(packed, pos)
        strip[i] = zeros - val
        if val == 0:
            zeros += 1
    return strip


def unpack_octants_and_layers(packed, strip, n_vertices):
    """Octant (0-7) de chaque sommet, et debut de chaque couche dans le strip."""
    count, pos = read_varint(packed, 0)
    octants = np.zeros(n_vertices, dtype=np.uint8)
    layer_bounds = []
    k = 0
    for i in range(count):
        if i % 8 == 0:
            layer_bounds.append(k)
        v, pos = read_varint(packed, pos)
        if v:
            idx = strip[k:k + v]
            if len(idx) != v or idx.min() < 0 or idx.max() >= n_vertices:
                raise ValueError("compteurs d'octants incoherents avec le strip")
            octants[idx] = i & 7
        k += v
    while len(layer_bounds) < 10:
        layer_bounds.append(k)
    return octants, layer_bounds


def strip_to_triangles(strip):
    if len(strip) < 3:
        return np.zeros((0, 3), dtype=np.int64)
    a, b, c = strip[:-2], strip[1:-1], strip[2:]
    odd = (np.arange(len(a)) & 1).astype(bool)
    tris = np.stack([a, np.where(odd, c, b), np.where(odd, b, c)], axis=1)
    keep = (a != b) & (a != c) & (b != c)
    return tris[keep]


def parse_mesh(mm, with_texture):
    positions = unpack_vertices(mm.bytes(1))
    n = len(positions)
    strip = unpack_indices(mm.bytes(3))
    octants, layer_bounds = unpack_octants_and_layers(mm.bytes(8), strip, n)
    strip = strip[:layer_bounds[LAYER_TERRAIN_HIDDEN]]
    triangles = strip_to_triangles(strip)

    uv = texture = fmt = None
    size = (0, 0)
    if with_texture and mm.has(7):
        raw_uv, (u_mod, v_mod) = unpack_texcoords(mm.bytes(7), n)
        oas = mm.floats(10)
        if len(oas) == 4:
            off, scale = np.array(oas[:2]), np.array(oas[2:])
        else:
            off, scale = np.array([0.5, 0.5]), np.array([1.0 / u_mod, 1.0 / v_mod])
            off[1] -= 1.0 / scale[1]
            scale[1] *= -1
        uv = (raw_uv + off) * scale
        textures = mm.msgs(6)
        if textures:
            t = textures[0]
            data = t.all_bytes(1)
            texture = data[0] if data else None
            fmt = t.uint(2, TEX_JPG)
            size = (t.uint(3, 256), t.uint(4, 256))
    return Mesh(positions, octants, uv, triangles, texture, fmt, size)


def parse_node(buf, with_texture=True):
    m = Message(buf)
    mat = m.doubles(1)
    if len(mat) != 16:
        raise ValueError(f"matrix_globe_from_mesh : {len(mat)} valeurs (16 attendues)")
    return NodeData(np.array(mat, dtype=np.float64).reshape(4, 4),
                    [parse_mesh(mm, with_texture) for mm in m.msgs(2)])


def mesh_to_ecef(node, mesh):
    p = mesh.positions.astype(np.float64)
    return p @ node.matrix[:3, :3] + node.matrix[3, :3]
