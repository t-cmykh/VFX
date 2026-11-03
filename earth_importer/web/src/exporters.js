"use strict";
// Ecriture OBJ / USDA et archive ZIP, sans dependance.
// Une tuile : { name, pos: Float64Array (x,y,z en metres, Y en haut, X = Est, -Z = Nord),
//               faces: Uint32Array (indices locaux), uv: Float64Array|null (convention OpenGL),
//               texture: Uint8Array|null (JPEG) }

const enc = new TextEncoder();

// UV rocktree = (u + offset) * scale. Le decodeur de Google retourne ensuite V : c'est ce que font
// OBJ et USD quand flipV est actif.
function uvForObj(v, flipV) { return flipV ? 1 - v : v; }

const fmt4 = (x) => x.toFixed(4);
const fmt6 = (x) => x.toFixed(6);

// ---------------------------------------------------------------- OBJ
function writeObj(tiles, name, meta, withTex, flipV) {
  const obj = [`# Earth Importer - Y en haut, X = Est, -Z = Nord, metres\n# origine : ${meta.lat.toFixed(7)}, ${meta.lon.toFixed(7)}\n`];
  const mtl = [];
  if (withTex) obj.push(`mtllib ${name}.mtl\n`);
  let cv = 0, cvt = 0;
  for (const t of tiles) {
    const lines = [`o ${t.label || `tile_${t.name}`}\n`];
    const n = t.pos.length / 3;
    for (let i = 0; i < t.pos.length; i += 3) lines.push(`v ${fmt4(t.pos[i])} ${fmt4(t.pos[i + 1])} ${fmt4(t.pos[i + 2])}\n`);
    const hasUv = withTex && t.uv;
    if (hasUv) {
      for (let i = 0; i < n; i++) lines.push(`vt ${fmt6(t.uv[2 * i])} ${fmt6(uvForObj(t.uv[2 * i + 1], flipV))}\n`);
      mtl.push(`newmtl m_${t.name}\nKd 1 1 1\nKs 0 0 0\nd 1\nillum 1\n`);
      if (t.texture) mtl.push(`map_Kd textures/${t.name}.jpg\n`);
      mtl.push("\n");
      lines.push(`usemtl m_${t.name}\n`);
      for (let i = 0; i < t.faces.length; i += 3) {
        const a = t.faces[i] + 1, b = t.faces[i + 1] + 1, c = t.faces[i + 2] + 1;
        lines.push(`f ${a + cv}/${a + cvt} ${b + cv}/${b + cvt} ${c + cv}/${c + cvt}\n`);
      }
      cvt += n;
    } else {
      for (let i = 0; i < t.faces.length; i += 3)
        lines.push(`f ${t.faces[i] + 1 + cv} ${t.faces[i + 1] + 1 + cv} ${t.faces[i + 2] + 1 + cv}\n`);
    }
    cv += n;
    obj.push(lines.join(""));
  }
  const files = [[`${name}/${name}.obj`, obj]];
  if (withTex) files.push([`${name}/${name}.mtl`, mtl]);
  return files;
}

// ---------------------------------------------------------------- USDA
function writeUsda(tiles, name, meta, withTex, flipV) {
  const out = [`#usda 1.0
(
    defaultPrim = "World"
    doc = "Earth Importer - origine ${meta.lat.toFixed(7)}, ${meta.lon.toFixed(7)}"
    metersPerUnit = 1
    upAxis = "Y"
)

def Xform "World"
{
`];
  if (withTex) {
    out.push(`    def Scope "Materials"\n    {\n`);
    for (const t of tiles) {
      if (!t.uv) continue;
      const m = `/World/Materials/m_${t.name}`;
      const diffuse = t.texture
        ? `color3f inputs:diffuseColor.connect = <${m}/Tex.outputs:rgb>`
        : "color3f inputs:diffuseColor = (0.5, 0.5, 0.5)";
      out.push(`        def Material "m_${t.name}"
        {
            token outputs:surface.connect = <${m}/Shader.outputs:surface>

            def Shader "Shader"
            {
                uniform token info:id = "UsdPreviewSurface"
                ${diffuse}
                float inputs:roughness = 1
                float inputs:metallic = 0
                token outputs:surface
            }
${t.texture ? `
            def Shader "Tex"
            {
                uniform token info:id = "UsdUVTexture"
                asset inputs:file = @./textures/${t.name}.jpg@
                token inputs:sourceColorSpace = "sRGB"
                float2 inputs:st.connect = <${m}/St.outputs:result>
                token inputs:wrapS = "clamp"
                token inputs:wrapT = "clamp"
                float3 outputs:rgb
            }

            def Shader "St"
            {
                uniform token info:id = "UsdPrimvarReader_float2"
                string inputs:varname = "st"
                float2 outputs:result
            }
` : ""}        }
`);
    }
    out.push(`    }\n\n`);
  }
  for (const t of tiles) {
    const n = t.pos.length / 3, nf = t.faces.length / 3;
    const pts = [], st = [];
    for (let i = 0; i < t.pos.length; i += 3) pts.push(`(${fmt4(t.pos[i])}, ${fmt4(t.pos[i + 1])}, ${fmt4(t.pos[i + 2])})`);
    const binding = withTex && t.uv;
    if (binding) for (let i = 0; i < n; i++) st.push(`(${fmt6(t.uv[2 * i])}, ${fmt6(uvForObj(t.uv[2 * i + 1], flipV))})`);
    out.push(`    def Mesh "${t.label || `tile_${t.name}`}"${binding ? ` (\n        prepend apiSchemas = ["MaterialBindingAPI"]\n    )` : ""}
    {
        uniform token subdivisionScheme = "none"
        int[] faceVertexCounts = [${new Array(nf).fill(3).join(", ")}]
        int[] faceVertexIndices = [${Array.from(t.faces).join(", ")}]
        point3f[] points = [${pts.join(", ")}]
${binding ? `        texCoord2f[] primvars:st = [${st.join(", ")}] (
            interpolation = "vertex"
        )
        rel material:binding = </World/Materials/m_${t.name}>
` : ""}    }

`);
  }
  out.push("}\n");
  return [[`${name}/${name}.usda`, out]];
}

// ---------------------------------------------------------------- ZIP (stockage sans compression)
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(parts) {
  let c = 0xffffffff;
  for (const p of parts) for (let i = 0; i < p.length; i++) c = CRC_TABLE[(c ^ p[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function toParts(content) {
  if (content instanceof Uint8Array) return [content];
  if (typeof content === "string") return [enc.encode(content)];
  return content.map((c) => (typeof c === "string" ? enc.encode(c) : c));
}

function concat(parts) {
  const out = new Uint8Array(parts.reduce((s, p) => s + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

// files : [[chemin, contenu (Uint8Array | string | tableau des deux)], ...] -> Blob
function makeZip(files) {
  const out = [], central = [];
  let offset = 0;
  const now = new Date();
  const time = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
  const date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  for (const [path, content] of files) {
    const parts = toParts(content);
    const size = parts.reduce((s, p) => s + p.length, 0);
    const crc = crc32(parts), nameBytes = enc.encode(path);
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true);
    lh.setUint16(8, 0, true); lh.setUint16(10, time, true); lh.setUint16(12, date, true);
    lh.setUint32(14, crc, true); lh.setUint32(18, size, true); lh.setUint32(22, size, true);
    lh.setUint16(26, nameBytes.length, true); lh.setUint16(28, 0, true);
    out.push(new Uint8Array(lh.buffer), nameBytes, ...parts);
    const ch = new DataView(new ArrayBuffer(46));
    ch.setUint32(0, 0x02014b50, true); ch.setUint16(4, 20, true); ch.setUint16(6, 20, true);
    ch.setUint16(8, 0x0800, true); ch.setUint16(10, 0, true); ch.setUint16(12, time, true);
    ch.setUint16(14, date, true); ch.setUint32(16, crc, true); ch.setUint32(20, size, true);
    ch.setUint32(24, size, true); ch.setUint16(28, nameBytes.length, true);
    ch.setUint32(42, offset, true);
    central.push(new Uint8Array(ch.buffer), nameBytes);
    offset += 30 + nameBytes.length + size;
  }
  const cdSize = central.reduce((s, p) => s + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, cdSize, true); end.setUint32(16, offset, true);
  return new Blob([...out, ...central, new Uint8Array(end.buffer)], { type: "application/zip" });
}

const EXPORTERS = { obj: writeObj, usd: writeUsda };

if (typeof module !== "undefined") module.exports = { EXPORTERS, makeZip, crc32 };
