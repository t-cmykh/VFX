"use strict";
// Fusion a l'export : toutes les tuiles -> un seul maillage, toutes les textures -> un atlas.
// De chaque texture on ne garde que le rectangle reellement utilise par les UV de la tuile
// (apres decoupe a la zone), puis ces rectangles sont ranges dans une image carree.

const ATLAS_PAD = 2;      // pixels source gardes autour de la zone utilisee (evite les coutures au filtrage)
const ATLAS_FILL = 0.85;  // remplissage vise par le rangement en etageres
const ATLAS_MAX = 16384;
const ATLAS_GREY = "#808080";

// Rangement en etageres, du plus haut au plus bas. Ecrit x/y dans chaque rectangle, false si ca deborde.
function packShelves(rects, size) {
  const order = rects.map((_, i) => i).sort((a, b) => rects[b].h - rects[a].h || rects[b].w - rects[a].w);
  let x = 0, y = 0, rowH = 0;
  for (const i of order) {
    const r = rects[i];
    if (r.w > size || r.h > size) return false;
    if (x + r.w > size) { x = 0; y += rowH; rowH = 0; }
    if (y + r.h > size) return false;
    r.x = x; r.y = y;
    x += r.w;
    rowH = Math.max(rowH, r.h);
  }
  return true;
}

function makeCanvas(size) {
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(size, size);
  const c = document.createElement("canvas");
  c.width = c.height = size;
  return c;
}

async function canvasToJpeg(canvas, quality) {
  const blob = canvas.convertToBlob
    ? await canvas.convertToBlob({ type: "image/jpeg", quality })
    : await new Promise((res) => canvas.toBlob(res, "image/jpeg", quality));
  if (!blob) throw new Error("Atlas trop grand pour ce navigateur : choisis une résolution plus basse.");
  return new Uint8Array(await blob.arrayBuffer());
}

function mergeGeometry(tiles, withUv) {
  let nv = 0, nf = 0;
  for (const t of tiles) { nv += t.pos.length / 3; nf += t.faces.length; }
  const pos = new Float64Array(3 * nv), faces = new Uint32Array(nf);
  const offsets = [];
  let ov = 0, of = 0;
  for (const t of tiles) {
    pos.set(t.pos, 3 * ov);
    for (let i = 0; i < t.faces.length; i++) faces[of + i] = t.faces[i] + ov;
    offsets.push(ov);
    ov += t.pos.length / 3;
    of += t.faces.length;
  }
  return { name: "fusion", label: "fusion", pos, faces, uv: withUv ? new Float64Array(2 * nv) : null, texture: null, offsets };
}

// resolution : nombre (cote de l'atlas en px) ou "auto" (pleine resolution des sources, plafonnee).
// flipV : meme sens que pour les exporteurs ; les UV de sortie gardent la convention des tuiles.
async function mergeTiles(tiles, { withTex, flipV, resolution, quality = 0.9, log = () => {} }) {
  const merged = mergeGeometry(tiles, withTex);
  if (!withTex) return merged;

  // v (convention des tuiles) <-> y depuis le haut de l'image, en fraction
  const toTop = (v) => (flipV ? v : 1 - v);
  const fromTop = (y) => (flipV ? y : 1 - y);

  const rects = [];
  const sources = [];
  for (const [k, t] of tiles.entries()) {
    if (!t.uv || !t.texture) { sources.push(null); continue; }
    let img;
    try {
      img = await createImageBitmap(new Blob([t.texture], { type: "image/jpeg" }));
    } catch (e) {
      log("WARN", `Texture illisible ignorée (tuile ${t.name})`);
      sources.push(null);
      continue;
    }
    const W = img.width, H = img.height;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (let i = 0; i < t.uv.length; i += 2) {
      const x = t.uv[i] * W, y = toTop(t.uv[i + 1]) * H;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    const sx = Math.max(0, Math.floor(x0) - ATLAS_PAD), ex = Math.min(W, Math.ceil(x1) + ATLAS_PAD);
    const sy = Math.max(0, Math.floor(y0) - ATLAS_PAD), ey = Math.min(H, Math.ceil(y1) + ATLAS_PAD);
    const src = { k, img, W, H, sx, sy, sw: Math.max(1, ex - sx), sh: Math.max(1, ey - sy) };
    sources.push(src);
    rects.push(src);
  }
  const grey = { grey: true, sw: 4, sh: 4 };  // pour les tuiles sans texture (CRN, illisible)
  if (sources.some((s, k) => !s && tiles[k].uv)) rects.push(grey);
  if (!rects.length) return merged;

  const area = rects.reduce((a, r) => a + r.sw * r.sh, 0);
  let size = resolution === "auto"
    ? Math.min(ATLAS_MAX, Math.max(256, 2 ** Math.ceil(Math.log2(Math.sqrt(area / ATLAS_FILL)))))
    : Number(resolution);
  let scale = Math.min(1, Math.sqrt((size * size * ATLAS_FILL) / area));
  for (let tries = 0; ; tries++) {
    for (const r of rects) { r.w = Math.max(1, Math.round(r.sw * scale)); r.h = Math.max(1, Math.round(r.sh * scale)); }
    if (packShelves(rects, size)) break;
    if (tries > 60) throw new Error("Impossible de ranger les textures dans l'atlas.");
    scale *= 0.95;
  }

  const canvas = makeCanvas(size);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = ATLAS_GREY;
  ctx.fillRect(0, 0, size, size);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  for (const r of rects) if (!r.grey) ctx.drawImage(r.img, r.sx, r.sy, r.sw, r.sh, r.x, r.y, r.w, r.h);

  for (const [k, t] of tiles.entries()) {
    if (!t.uv) continue;
    const off = 2 * merged.offsets[k], s = sources[k];
    for (let i = 0; i < t.uv.length; i += 2) {
      let ax, ay;
      if (s) {
        ax = s.x + (t.uv[i] * s.W - s.sx) * (s.w / s.sw);
        ay = s.y + (toTop(t.uv[i + 1]) * s.H - s.sy) * (s.h / s.sh);
      } else {
        ax = grey.x + grey.w / 2;
        ay = grey.y + grey.h / 2;
      }
      merged.uv[off + i] = ax / size;
      merged.uv[off + i + 1] = fromTop(ay / size);
    }
  }
  for (const s of sources) if (s) s.img.close();

  merged.texture = await canvasToJpeg(canvas, quality);
  const pct = Math.round(scale * 100);
  log(pct < 100 ? "WARN" : "INFO",
    `Atlas ${size}×${size} px : ${sources.filter(Boolean).length} textures, à ${pct} % de leur résolution d'origine` +
    (pct < 100 ? " (monte la résolution de l'atlas pour garder plus de détail)" : ""));
  return merged;
}
