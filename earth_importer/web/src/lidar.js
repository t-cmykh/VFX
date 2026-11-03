"use strict";
// LiDAR HD de l'IGN (France) : MNS / MNT lus sur le WMS raster de la Geoplateforme (float32, Lambert-93),
// mailles en grille et places dans le meme repere local que la 3D Google. Meme logique que core/lidar.py.
// (Les dalles brutes du nuage de points, 0,5 a 2 Go chacune, ne sont que dans la version Python.)

const LIDAR_CONFIG = {
  wms: "https://data.geopf.fr/wms-r/wms",
  layers: {
    mns: "IGNF_LIDAR-HD_MNS_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93",
    mnt: "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93",
  },
  mntFallback: "ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES",  // RGE ALTI 1 m
  bilSouthFirst: true,   // WMS 1.3.0 x-bil : premiere ligne = bord sud (false si le relief sort inverse N/S)
  maxPx: 2048,
  validRange: [-500, 5000],
  zOffset: 0,
  maxVertices: 16e6,
  res: 0.5,              // pas de la grille (m) : resolution native du LiDAR HD
};
const ignBase = typeof location !== "undefined" ? new URLSearchParams(location.search).get("ign") : null;  // tests : faux services IGN
if (ignBase) LIDAR_CONFIG.wms = ignBase + "/wms";

const L93 = { e: 0.0818191910428158, n: 0.7256077650532670, c: 11754255.426096, xs: 700000, ys: 12655612.049876, lon0: 3 * Math.PI / 180 };

function lonlatToL93(lon, lat) {
  const la = lat * Math.PI / 180, es = L93.e * Math.sin(la);
  const iso = Math.log(Math.tan(Math.PI / 4 + la / 2) * ((1 - es) / (1 + es)) ** (L93.e / 2));
  const r = L93.c * Math.exp(-L93.n * iso), t = L93.n * (lon * Math.PI / 180 - L93.lon0);
  return [L93.xs + r * Math.sin(t), L93.ys - r * Math.cos(t)];
}

function l93ToLonlat(x, y) {
  const dx = x - L93.xs, dy = L93.ys - y;
  const iso = -Math.log(Math.hypot(dx, dy) / L93.c) / L93.n;
  let phi = 2 * Math.atan(Math.exp(iso)) - Math.PI / 2;
  for (let k = 0; k < 12; k++) {
    const es = L93.e * Math.sin(phi);
    phi = 2 * Math.atan(((1 + es) / (1 - es)) ** (L93.e / 2) * Math.exp(iso)) - Math.PI / 2;
  }
  return [(L93.lon0 + Math.atan2(dx, dy) / L93.n) * 180 / Math.PI, phi * 180 / Math.PI];
}

const LIDAR = {
  products(choice) { return { mns: ["mns"], mnt: ["mnt"], both: ["mns", "mnt"] }[choice] || []; },

  // rectangle Lambert-93 cale sur la grille, qui couvre le carre de cote 2 * half
  bbox(p, res) {
    const R = GEO.RADIUS, h = p.half * 1.02 + 2 * res;
    const dlat = h / R * 180 / Math.PI, dlon = h / (R * Math.cos(p.lat * Math.PI / 180)) * 180 / Math.PI;
    const xs = [], ys = [];
    for (const [lo, la] of [[-1, -1], [1, -1], [-1, 1], [1, 1], [0, -1], [0, 1]]) {
      const [x, y] = lonlatToL93(p.lon + lo * dlon, p.lat + la * dlat);
      xs.push(x); ys.push(y);
    }
    return [Math.floor(Math.min(...xs) / res) * res, Math.floor(Math.min(...ys) / res) * res,
            Math.ceil(Math.max(...xs) / res) * res, Math.ceil(Math.max(...ys) / res) * res];
  },

  shape(p, res) {
    const [x0, y0, x1, y1] = LIDAR.bbox(p, res);
    return [Math.round((y1 - y0) / res), Math.round((x1 - x0) / res)];
  },

  checkSize(p) {
    const [h, w] = LIDAR.shape(p, p.lidarRes);
    if (h * w > LIDAR_CONFIG.maxVertices)
      throw new Error(`Zone trop grande pour le LiDAR à ${p.lidarRes} m (${w} x ${h} points) : réduis la zone`);
    return [h, w];
  },

  wmsUrl(layer, x0, y0, x1, y1, w, h) {
    return `${LIDAR_CONFIG.wms}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=${layer}&STYLES=` +
      `&CRS=EPSG:2154&BBOX=${x0.toFixed(3)},${y0.toFixed(3)},${x1.toFixed(3)},${y1.toFixed(3)}` +
      `&WIDTH=${w}&HEIGHT=${h}&FORMAT=image/x-bil%3Bbits%3D32`;
  },

  // grille d'altitude (lignes du nord vers le sud), NaN hors donnees
  async fetchGrid(fetcher, p, layer, res, label, ui) {
    const [x0, y0, x1, y1] = LIDAR.bbox(p, res);
    const H = Math.round((y1 - y0) / res), W = Math.round((x1 - x0) / res), step = LIDAR_CONFIG.maxPx;
    const grid = new Float32Array(H * W).fill(NaN);
    const [lo, hi] = LIDAR_CONFIG.validRange;
    const blocks = [];
    for (let r = 0; r < H; r += step) for (let c = 0; c < W; c += step) blocks.push([r, c]);
    let done = 0;
    for (const [r, c] of blocks) {
      const h = Math.min(step, H - r), w = Math.min(step, W - c), bx0 = x0 + c * res, by1 = y1 - r * res;
      const data = await fetcher.get(LIDAR.wmsUrl(layer, bx0, by1 - h * res, bx0 + w * res, by1, w, h));
      if (data.length !== w * h * 4) {
        const text = new TextDecoder().decode(data.slice(0, 300)).replace(/<[^>]+>/g, " ").trim().slice(0, 200);
        throw new Error(`Réponse IGN inattendue pour ${layer} (${data.length} octets au lieu de ${w * h * 4}) : ${text}`);
      }
      const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
      for (let j = 0; j < h; j++) {
        const src = LIDAR_CONFIG.bilSouthFirst ? h - 1 - j : j;
        for (let i = 0; i < w; i++) {
          const z = view.getFloat32(4 * (src * w + i), true);
          if (z >= lo && z <= hi) grid[(r + j) * W + c + i] = z;
        }
      }
      ui.progress(`LiDAR ${label}`, ++done, blocks.length);
    }
    return { grid, W, H, x0, y1, res };
  },

  // tuile au format des exporteurs (Y en haut, X = Est, -Z = Nord), ou null sans donnees
  async tile(fetcher, p, frame, prod, ui) {
    let g = await LIDAR.fetchGrid(fetcher, p, LIDAR_CONFIG.layers[prod], p.lidarRes, prod.toUpperCase(), ui);
    const hasData = (gr) => gr.grid.some((z) => !Number.isNaN(z));
    if (!hasData(g) && prod === "mnt" && LIDAR_CONFIG.mntFallback) {
      const res = Math.max(p.lidarRes, 1);
      ui.log("WARN", `Pas de MNT LiDAR HD ici : repli sur le RGE ALTI (${res} m)`);
      g = await LIDAR.fetchGrid(fetcher, p, LIDAR_CONFIG.mntFallback, res, "RGE ALTI", ui);
    }
    if (!hasData(g)) {
      ui.log("WARN", `Aucune donnée ${prod.toUpperCase()} LiDAR HD sur cette zone (hors France ou zone pas encore couverte)`);
      return null;
    }
    const { grid, W, H, x0, y1, res } = g, n = W * H, R = GEO.RADIUS;
    const enu = new Float64Array(3 * n), out = new Uint8Array(n);
    const ecef = new Float64Array(3);
    for (let j = 0; j < H; j++) {
      const y = y1 - (j + 0.5) * res;
      for (let i = 0; i < W; i++) {
        const k = j * W + i, [lon, lat] = l93ToLonlat(x0 + (i + 0.5) * res, y);
        const z = Number.isNaN(grid[k]) ? 0 : grid[k] + LIDAR_CONFIG.zOffset;
        const la = lat * Math.PI / 180, lo = lon * Math.PI / 180, r = R + z;
        ecef[0] = r * Math.cos(la) * Math.cos(lo); ecef[1] = r * Math.cos(la) * Math.sin(lo); ecef[2] = r * Math.sin(la);
        const e = frame.toEnu(ecef);
        enu[3 * k] = e[0]; enu[3 * k + 1] = e[1]; enu[3 * k + 2] = e[2];
        out[k] = Math.max(Math.abs(e[0]), Math.abs(e[1])) > p.half ? 1 : 0;
      }
    }
    // 2 triangles par cellule aux 4 coins valides ; retires s'ils sont entierement hors du carre
    const ok = (k) => !Number.isNaN(grid[k]);
    const keep = [];
    for (let j = 0; j < H - 1; j++) for (let i = 0; i < W - 1; i++) {
      const a = j * W + i, b = a + 1, c = a + W, d = c + 1;
      if (!(ok(a) && ok(b) && ok(c) && ok(d))) continue;
      if (!(out[a] && out[c] && out[b])) keep.push(a, c, b);
      if (!(out[b] && out[c] && out[d])) keep.push(b, c, d);
    }
    if (!keep.length) return null;
    const remap = new Int32Array(n).fill(-1);
    let m = 0;
    const faces = new Uint32Array(keep.length);
    for (let t = 0; t < keep.length; t++) {
      const v = keep[t];
      if (remap[v] < 0) remap[v] = m++;
      faces[t] = remap[v];
    }
    const pos = new Float64Array(3 * m);
    for (let v = 0; v < n; v++) {
      const d = remap[v];
      if (d < 0) continue;
      pos[3 * d] = enu[3 * v]; pos[3 * d + 1] = enu[3 * v + 2]; pos[3 * d + 2] = -enu[3 * v + 1];
    }
    ui.log("INFO", `LiDAR ${prod.toUpperCase()} : ${(faces.length / 3).toLocaleString("fr")} triangles`);
    return { name: `lidar_${prod}`, label: `lidar_${prod}`, pos, faces, uv: null, texture: null };
  },
};

if (typeof module !== "undefined") module.exports = { LIDAR, LIDAR_CONFIG, lonlatToL93, l93ToLonlat };
