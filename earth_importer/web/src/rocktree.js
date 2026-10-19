"use strict";
// Decodage rocktree (Google Earth 3D) : portage JS de core/pbwire.py, core/rocktree.py et core/geo.py.
// Source d'origine : retroplasma/earth-reverse-engineering (client/rocktree_decoder.h).

// ---------------------------------------------------------------- protobuf filaire
function readVarint(buf, pos) {
  let result = 0, mul = 1;
  for (;;) {
    if (pos >= buf.length) throw new Error("varint tronqué");
    const b = buf[pos++];
    result += (b & 0x7f) * mul;
    if (!(b & 0x80)) return [result, pos];
    mul *= 128;
  }
}

class Msg {
  constructor(buf) {
    this.f = new Map();
    let pos = 0;
    const end = buf.length;
    while (pos < end) {
      let key, val;
      [key, pos] = readVarint(buf, pos);
      const num = Math.floor(key / 8), wt = key & 7;
      if (wt === 0) [val, pos] = readVarint(buf, pos);
      else if (wt === 1) { val = buf.subarray(pos, pos + 8); pos += 8; }
      else if (wt === 2) { let n; [n, pos] = readVarint(buf, pos); val = buf.subarray(pos, pos + n); pos += n; }
      else if (wt === 5) { val = buf.subarray(pos, pos + 4); pos += 4; }
      else throw new Error(`type filaire ${wt} non géré (champ ${num})`);
      if (pos > end) throw new Error(`champ ${num} dépasse la fin du message`);
      if (!this.f.has(num)) this.f.set(num, []);
      this.f.get(num).push([wt, val]);
    }
  }
  has(n) { return this.f.has(n); }
  last(n) { const v = this.f.get(n); return v ? v[v.length - 1][1] : undefined; }
  uint(n, def = null) { const v = this.last(n); return v === undefined ? def : v; }
  bytes(n) { return this.last(n) || new Uint8Array(0); }
  allBytes(n) { return (this.f.get(n) || []).map(([, v]) => v); }
  msg(n) { const v = this.last(n); return v === undefined ? null : new Msg(v); }
  msgs(n) { return this.allBytes(n).map((v) => new Msg(v)); }
  float(n, def = null) { const v = this.last(n); return v === undefined ? def : dv(v).getFloat32(0, true); }
  repeated(n, size, get) {
    const out = [];
    for (const [wt, v] of this.f.get(n) || []) {
      const d = dv(v);
      if (wt === 2) for (let i = 0; i + size <= v.length; i += size) out.push(get(d, i));
      else out.push(get(d, 0));
    }
    return out;
  }
  doubles(n) { return this.repeated(n, 8, (d, i) => d.getFloat64(i, true)); }
  floats(n) { return this.repeated(n, 4, (d, i) => d.getFloat32(i, true)); }
}

function dv(u8) { return new DataView(u8.buffer, u8.byteOffset, u8.byteLength); }

// ---------------------------------------------------------------- rocktree
const RT = {
  FLAG_LEAF: 4, FLAG_NODATA: 8, FLAG_USE_IMAGERY_EPOCH: 16,
  TEX_JPG: 1, TEX_CRN_DXT1: 6,
  LAYER_TERRAIN_HIDDEN: 3,

  parsePlanetoid(buf) {
    const m = new Msg(buf), root = m.msg(1);
    if (!root || !root.has(2)) throw new Error("PlanetoidMetadata sans epoch racine");
    return { rootEpoch: root.uint(2), radius: m.float(2, 0) };
  },

  unpackPathAndFlags(v) {
    const level = 1 + (v & 3);
    v = Math.floor(v / 4);
    let path = "";
    for (let i = 0; i < level; i++) { path += String(v % 8); v = Math.floor(v / 8); }
    return [path, v];
  },

  unpackObb(b, head, mpt) {
    if (b.length !== 15) throw new Error(`OBB de ${b.length} octets (15 attendus)`);
    const d = dv(b);
    const center = [0, 1, 2].map((i) => d.getInt16(2 * i, true) * mpt + head[i]);
    const ext = [6, 7, 8].map((o) => d.getUint8(o) * mpt);
    return { center, radius: Math.hypot(...ext) };
  },

  parseBulk(buf, path) {
    const m = new Msg(buf);
    const hk = m.msg(2);
    const headEpoch = hk ? hk.uint(2, 0) : 0;
    const headCenter = m.doubles(3);
    if (headCenter.length < 3) headCenter.push(0, 0, 0);
    const levelMpt = m.floats(4);
    const defImagery = m.uint(5), defFormats = m.uint(6, 0);
    const nodes = new Map();
    for (const nm of m.msgs(1)) {
      const [rel, flags] = RT.unpackPathAndFlags(nm.uint(1, 0));
      let mpt = nm.float(4);
      if (mpt === null) mpt = levelMpt.length >= rel.length ? levelMpt[rel.length - 1] : 0;
      const ob = nm.bytes(3);
      nodes.set(rel, {
        rel, flags,
        epoch: nm.uint(2, headEpoch),
        bulkEpoch: nm.uint(5, headEpoch),
        obb: ob.length ? RT.unpackObb(ob, headCenter, mpt) : null,
        mpt,
        imageryEpoch: flags & RT.FLAG_USE_IMAGERY_EPOCH ? nm.uint(7, defImagery) : null,
        texFormats: nm.uint(8, defFormats),
        get hasData() { return !(this.flags & RT.FLAG_NODATA); },
        get hasBulk() { return this.rel.length === 4 && !(this.flags & RT.FLAG_LEAF); },
      });
    }
    return { path, nodes };
  },

  chooseTextureFormat(available, preferred = [RT.TEX_JPG, RT.TEX_CRN_DXT1]) {
    for (const f of preferred) if (available & (1 << (f - 1))) return f;
    return preferred[0];
  },

  unpackVertices(p) {
    const n = Math.floor(p.length / 3), out = new Uint8Array(3 * n);
    let x = 0, y = 0, z = 0;
    for (let i = 0; i < n; i++) {
      x = (x + p[i]) & 255; y = (y + p[n + i]) & 255; z = (z + p[2 * n + i]) & 255;
      out[3 * i] = x; out[3 * i + 1] = y; out[3 * i + 2] = z;
    }
    return out;
  },

  unpackTexcoords(p, n) {
    if (p.length < 4 || p.length - 4 !== 4 * n) throw new Error(`coordonnées de texture : ${p.length} octets pour ${n} sommets`);
    const d = dv(p), uMod = 1 + d.getUint16(0, true), vMod = 1 + d.getUint16(2, true);
    const b = p.subarray(4), out = new Float64Array(2 * n);
    let u = 0, v = 0;
    for (let i = 0; i < n; i++) {
      u = (u + b[i] + (b[2 * n + i] << 8)) % uMod;
      v = (v + b[n + i] + (b[3 * n + i] << 8)) % vMod;
      out[2 * i] = u; out[2 * i + 1] = v;
    }
    return [out, uMod, vMod];
  },

  unpackIndices(p) {
    let count, pos;
    [count, pos] = readVarint(p, 0);
    const strip = new Int32Array(count);
    let zeros = 0;
    for (let i = 0; i < count; i++) {
      let val;
      [val, pos] = readVarint(p, pos);
      strip[i] = zeros - val;
      if (val === 0) zeros++;
    }
    return strip;
  },

  unpackOctants(p, strip, nVerts) {
    let count, pos;
    [count, pos] = readVarint(p, 0);
    const oct = new Uint8Array(nVerts), bounds = [];
    let k = 0;
    for (let i = 0; i < count; i++) {
      if (i % 8 === 0) bounds.push(k);
      let v;
      [v, pos] = readVarint(p, pos);
      if (k + v > strip.length) throw new Error("compteurs d'octants incohérents avec le strip");
      for (let j = k; j < k + v; j++) {
        const idx = strip[j];
        if (idx < 0 || idx >= nVerts) throw new Error("compteurs d'octants incohérents avec le strip");
        oct[idx] = i & 7;
      }
      k += v;
    }
    while (bounds.length < 10) bounds.push(k);
    return [oct, bounds];
  },

  stripToTriangles(s) {
    const out = [];
    for (let i = 0; i + 2 < s.length; i++) {
      const a = s[i], b = s[i + 1], c = s[i + 2];
      if (a === b || a === c || b === c) continue;
      if (i & 1) out.push(a, c, b); else out.push(a, b, c);
    }
    return Uint32Array.from(out);
  },

  parseMesh(mm, withTex) {
    const positions = RT.unpackVertices(mm.bytes(1));
    const n = positions.length / 3;
    let strip = RT.unpackIndices(mm.bytes(3));
    const [octants, bounds] = RT.unpackOctants(mm.bytes(8), strip, n);
    strip = strip.subarray(0, bounds[RT.LAYER_TERRAIN_HIDDEN]);
    const triangles = RT.stripToTriangles(strip);
    const mesh = { positions, octants, triangles, uv: null, texture: null, texFormat: null };
    if (withTex && mm.has(7)) {
      const [raw, uMod, vMod] = RT.unpackTexcoords(mm.bytes(7), n);
      const oas = mm.floats(10);
      let off, scale;
      if (oas.length === 4) { off = [oas[0], oas[1]]; scale = [oas[2], oas[3]]; }
      else {
        off = [0.5, 0.5]; scale = [1 / uMod, 1 / vMod];
        off[1] -= 1 / scale[1]; scale[1] *= -1;
      }
      const uv = new Float64Array(raw.length);
      for (let i = 0; i < n; i++) {
        uv[2 * i] = (raw[2 * i] + off[0]) * scale[0];
        uv[2 * i + 1] = (raw[2 * i + 1] + off[1]) * scale[1];
      }
      mesh.uv = uv;
      const tex = mm.msgs(6)[0];
      if (tex) {
        const data = tex.allBytes(1);
        mesh.texture = data.length ? data[0] : null;
        mesh.texFormat = tex.uint(2, RT.TEX_JPG);
      }
    }
    return mesh;
  },

  parseNode(buf, withTex) {
    const m = new Msg(buf), mat = m.doubles(1);
    if (mat.length !== 16) throw new Error(`matrix_globe_from_mesh : ${mat.length} valeurs (16 attendues)`);
    return { matrix: mat, meshes: m.msgs(2).map((mm) => RT.parseMesh(mm, withTex)) };
  },

  meshToEcef(node, mesh) {
    const m = node.matrix, p = mesh.positions, out = new Float64Array(p.length);
    for (let i = 0; i < p.length; i += 3) {
      const x = p[i], y = p[i + 1], z = p[i + 2];
      out[i] = x * m[0] + y * m[4] + z * m[8] + m[12];
      out[i + 1] = x * m[1] + y * m[5] + z * m[9] + m[13];
      out[i + 2] = x * m[2] + y * m[6] + z * m[10] + m[14];
    }
    return out;
  },
};

// ---------------------------------------------------------------- WGS84 / ENU
const GEO = {
  A: 6378137.0, E2: 6.69437999014e-3,
  llaToEcef(lat, lon, alt = 0) {
    const la = lat * Math.PI / 180, lo = lon * Math.PI / 180;
    const n = GEO.A / Math.sqrt(1 - GEO.E2 * Math.sin(la) ** 2);
    return [(n + alt) * Math.cos(la) * Math.cos(lo), (n + alt) * Math.cos(la) * Math.sin(lo), (n * (1 - GEO.E2) + alt) * Math.sin(la)];
  },
  frame(lat, lon) {
    const la = lat * Math.PI / 180, lo = lon * Math.PI / 180;
    const sl = Math.sin(la), cl = Math.cos(la), so = Math.sin(lo), co = Math.cos(lo);
    const o = GEO.llaToEcef(lat, lon);
    const R = [[-so, co, 0], [-sl * co, -sl * so, cl], [cl * co, cl * so, sl]];
    return {
      // ECEF (n*3) -> ENU (n*3), en float64
      toEnu(ecef) {
        const out = new Float64Array(ecef.length);
        for (let i = 0; i < ecef.length; i += 3) {
          const dx = ecef[i] - o[0], dy = ecef[i + 1] - o[1], dz = ecef[i + 2] - o[2];
          for (let k = 0; k < 3; k++) out[i + k] = R[k][0] * dx + R[k][1] * dy + R[k][2] * dz;
        }
        return out;
      },
      horizontalDistance(p) {
        const e = this.toEnu(p);
        return Math.hypot(e[0], e[1]);
      },
    };
  },
};

if (typeof module !== "undefined") module.exports = { Msg, RT, GEO, readVarint };
