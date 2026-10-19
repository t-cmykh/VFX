"use strict";
// Earth Importer, version fichier local : meme logique que core/job.py, entierement dans le navigateur.

// Tout ce qui depend du protocole Google (equivalent de config.yaml).
const CONFIG = {
  baseUrl: "https://kh.google.com/rt/earth/",
  planetoid: "PlanetoidMetadata",
  bulk: "BulkMetadata/pb=!1m2!1s{path}!2u{epoch}",
  node: "NodeData/pb=!1m2!1s{path}!2u{epoch}!2e{texture_format}{imagery}!4b0",
  nodeImagery: "!3u{imagery_epoch}",
  workers: 6,
  minInterval: 20,      // ms minimum entre deux requetes
  timeout: 30000,
  maxRetries: 5,
  textureFormats: [1, 6],
  flipV: true,          // comme le decodeur de Google ; la case "Inverser V" de l'interface le contredit
};
const base = new URLSearchParams(location.search).get("base");  // tests : faux serveur
if (base) Object.assign(CONFIG, { baseUrl: base, minInterval: 0, maxRetries: 1 });

const fill = (tpl, vals) => tpl.replace(/\{(\w+)\}/g, (_, k) => vals[k]);

class Cancelled extends Error {}
class FetchError extends Error {}

const CACHE = new Map();  // reponses deja recues (relancer apres une annulation ne retelecharge rien)

// ---------------------------------------------------------------- reseau
class Fetcher {
  constructor(signal, log) {
    this.signal = signal;
    this.log = log;
    this.nextSlot = 0;
    this.bytes = 0;
  }
  async throttle() {
    const now = performance.now(), wait = this.nextSlot - now;
    this.nextSlot = Math.max(now, this.nextSlot) + CONFIG.minInterval;
    if (wait > 0) await sleep(wait, this.signal);
  }
  async get(resource) {
    if (CACHE.has(resource)) return CACHE.get(resource);
    let backoff = 1000, err = "";
    for (let attempt = 1; attempt <= CONFIG.maxRetries; attempt++) {
      if (this.signal.aborted) throw new Cancelled();
      await this.throttle();
      const ctl = new AbortController();
      const onAbort = () => ctl.abort();
      this.signal.addEventListener("abort", onAbort);
      const timer = setTimeout(() => ctl.abort(), CONFIG.timeout);
      try {
        const r = await fetch(CONFIG.baseUrl + resource, { signal: ctl.signal });
        if (r.ok) {
          const data = new Uint8Array(await r.arrayBuffer());
          this.bytes += data.length;
          CACHE.set(resource, data);
          return data;
        }
        if ([400, 403, 404].includes(r.status)) throw new FetchError(`HTTP ${r.status} sur ${resource}`);
        err = `HTTP ${r.status}`;
      } catch (e) {
        if (e instanceof FetchError) throw e;
        if (this.signal.aborted) throw new Cancelled();
        err = e.name === "AbortError" ? "délai dépassé" : "requête bloquée par le navigateur (CORS) ou réseau indisponible";
      } finally {
        clearTimeout(timer);
        this.signal.removeEventListener("abort", onAbort);
      }
      if (attempt === CONFIG.maxRetries) throw new FetchError(`${err} sur ${resource.split("/")[0]} après ${attempt} essai(s)`);
      this.log("WARN", `${err} sur ${resource.split("/")[0]}, nouvel essai dans ${backoff / 1000} s`);
      await sleep(backoff, this.signal);
      backoff = Math.min(backoff * 2, 16000);
    }
  }
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { clearTimeout(t); reject(new Cancelled()); }, { once: true });
  });
}

async function pool(items, n, fn, signal) {
  let next = 0;
  const results = new Array(items.length);
  async function worker() {
    while (next < items.length) {
      if (signal.aborted) throw new Cancelled();
      const k = next++;
      results[k] = await fn(items[k], k);
    }
  }
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, worker));
  return results;
}

// ---------------------------------------------------------------- operation
class Job {
  constructor(p, ui, estimateOnly) {
    this.p = p;
    this.ui = ui;
    this.estimateOnly = estimateOnly;
    this.abort = new AbortController();
    this.fetcher = new Fetcher(this.abort.signal, ui.log);
    this.frame = GEO.frame(p.lat, p.lon);
    this.withTex = p.content === "geo_tex";
  }

  async getBulk(path, epoch) {
    return RT.parseBulk(await this.fetcher.get(fill(CONFIG.bulk, { path, epoch })), path);
  }

  nodeResource(path, meta) {
    const texture_format = RT.chooseTextureFormat(meta.texFormats, CONFIG.textureFormats);
    const imagery = meta.imageryEpoch !== null ? fill(CONFIG.nodeImagery, { imagery_epoch: meta.imageryEpoch }) : "";
    return fill(CONFIG.node, { path, epoch: meta.epoch, texture_format, imagery });
  }

  async healthCheck() {
    let planetoid, root;
    try {
      planetoid = RT.parsePlanetoid(await this.fetcher.get(CONFIG.planetoid));
      root = await this.getBulk("", planetoid.rootEpoch);
    } catch (e) {
      if (e instanceof Cancelled) throw e;
      if (e instanceof FetchError) throw new Error(`Serveur injoignable ou accès refusé (${e.message}). Vérifie ta connexion ; si elle est bonne, Google a peut-être changé ses URLs ou bloque les pages locales.`);
      throw new Error(`Protocole Google modifié : réponse illisible (${e.message}). Décodeur à mettre à jour.`);
    }
    if (!root.nodes.size || ![...root.nodes.values()].some((n) => n.obb))
      throw new Error("Protocole Google modifié : métadonnées racine vides. Décodeur à mettre à jour.");
    this.ui.log("INFO", `Protocole OK (epoch racine ${planetoid.rootEpoch})`);
    return root;
  }

  inArea(obb) {
    const [e, n] = this.frame.toEnu(obb.center);
    return Math.max(Math.abs(e), Math.abs(n)) <= this.p.half + obb.radius;
  }

  async collect(root) {
    const max = this.p.level, candidates = new Map();
    let pending = [root], nBulks = 1;
    while (pending.length) {
      const children = [];
      for (const bulk of pending) {
        const accepted = new Set([""]);
        for (const rel of [...bulk.nodes.keys()].sort((a, b) => a.length - b.length)) {
          const meta = bulk.nodes.get(rel), full = bulk.path + rel;
          if (full.length > max || !accepted.has(rel.slice(0, -1))) continue;
          if (!meta.obb || !this.inArea(meta.obb)) continue;
          accepted.add(rel);
          if (meta.hasData) candidates.set(full, meta);
          if (meta.hasBulk && full.length < max) children.push([full, meta.bulkEpoch]);
        }
      }
      const res = await pool(children, CONFIG.workers, async ([path, epoch]) => {
        try { return await this.getBulk(path, epoch); }
        catch (e) {
          if (e instanceof Cancelled) throw e;
          this.ui.log("WARN", `Métadonnées ignorées : ${e.message}`);
          return null;
        }
      }, this.abort.signal);
      pending = res.filter(Boolean);
      nBulks += children.length;
      this.ui.progress("Analyse de l'octree", nBulks, 0);
    }
    this.ui.log("INFO", `${nBulks} blocs de métadonnées lus`);
    return candidates;
  }

  static exclusions(paths) {
    paths = [...paths];
    const excl = new Map([...paths].map((p) => [p, new Set()]));
    for (const p of paths) if (p.length > 1 && excl.has(p.slice(0, -1))) excl.get(p.slice(0, -1)).add(Number(p.at(-1)));
    return excl;
  }

  buildTile(name, node, mesh, exclude, counts) {
    const tri = mesh.triangles;
    const enu = this.frame.toEnu(RT.meshToEcef(node, mesh));
    const h = this.p.half;
    const inside = (v) => Math.abs(enu[3 * v]) <= h && Math.abs(enu[3 * v + 1]) <= h;
    const keep = [];
    for (let i = 0; i < tri.length; i += 3) {
      const a = tri[i], b = tri[i + 1], c = tri[i + 2];
      if (exclude.size && exclude.has(mesh.octants[a])) continue;
      if (!inside(a) && !inside(b) && !inside(c)) continue;
      keep.push(a, b, c);
    }
    if (!keep.length) return null;
    const remap = new Map(), faces = new Uint32Array(keep.length);
    keep.forEach((v, i) => {
      if (!remap.has(v)) remap.set(v, remap.size);
      faces[i] = remap.get(v);
    });
    const n = remap.size, pos = new Float64Array(3 * n);
    const uv = this.withTex && mesh.uv ? new Float64Array(2 * n) : null;
    for (const [src, dst] of remap) {
      pos[3 * dst] = enu[3 * src];              // X = Est
      pos[3 * dst + 1] = enu[3 * src + 2];      // Y = Haut
      pos[3 * dst + 2] = -enu[3 * src + 1];     // -Z = Nord
      if (uv) { uv[2 * dst] = mesh.uv[2 * src]; uv[2 * dst + 1] = mesh.uv[2 * src + 1]; }
    }
    let texture = null;
    if (uv && mesh.texture) {
      if (mesh.texFormat === RT.TEX_JPG) texture = mesh.texture;
      else counts.crn++;
    }
    counts.tris += faces.length / 3;
    counts.verts += n;
    return { name, pos, faces, uv, texture };
  }

  async download(todo, excl) {
    const entries = [...todo.entries()].sort(([a], [b]) => (a < b ? -1 : 1));
    const counts = { tris: 0, verts: 0, errors: 0, crn: 0 };
    const tiles = [];
    let done = 0;
    this.ui.progress("Téléchargement", 0, entries.length);
    await pool(entries, CONFIG.workers, async ([path, meta]) => {
      try {
        const node = RT.parseNode(await this.fetcher.get(this.nodeResource(path, meta)), this.withTex);
        node.meshes.forEach((mesh, i) => {
          const t = this.buildTile(`${path}_${i}`, node, mesh, excl.get(path), counts);
          if (t) tiles.push(t);
        });
      } catch (e) {
        if (e instanceof Cancelled) throw e;
        counts.errors++;
        this.ui.log("WARN", `Tuile ignorée : ${e.message}`);
      }
      this.ui.progress("Téléchargement", ++done, entries.length);
    }, this.abort.signal);
    if (counts.errors) this.ui.log("WARN", `${counts.errors} tuile(s) en échec sur ${entries.length}`);
    if (counts.crn) this.ui.log("WARN", `${counts.crn} texture(s) CRN non décodées : maillages concernés sans texture`);
    if (!counts.tris) throw new Error("Aucun triangle dans la zone après découpe.");
    tiles.sort((a, b) => (a.name < b.name ? -1 : 1));
    this.ui.log("INFO", `${counts.tris.toLocaleString("fr")} triangles, ${counts.verts.toLocaleString("fr")} sommets, ${(this.fetcher.bytes / 1e6).toFixed(1)} Mo téléchargés`);
    return tiles;
  }

  async run() {
    const t0 = performance.now(), p = this.p;
    try {
      this.ui.log("INFO", `Zone ${p.lat.toFixed(6)}, ${p.lon.toFixed(6)} - carré de ${p.side} m - niveau ${p.level}`);
      const root = await this.healthCheck();
      const candidates = await this.collect(root);
      const excl = Job.exclusions(candidates.keys());
      const todo = new Map([...candidates].filter(([path]) => excl.get(path).size < 8));
      if (!todo.size) throw new Error("Aucune donnée 3D dans cette zone à ce niveau (essaie un niveau plus bas ou une autre zone).");
      const levels = [...new Set([...todo.keys()].map((k) => k.length))].sort((a, b) => a - b);
      const mpts = [...todo.values()].map((m) => m.mpt).filter((x) => x > 0);
      const mpt = mpts.length ? Math.min(...mpts) : 0;
      this.ui.log("INFO", `${todo.size} tuiles à télécharger (niveaux ${levels[0]}-${levels.at(-1)}, ~${mpt.toFixed(2)} m/texel au plus fin)`);
      if (this.estimateOnly) {
        this.ui.done("estimate", `Estimation : ${todo.size} tuiles`);
        return;
      }
      let tiles = await this.download(todo, excl);
      if (p.merge) {
        this.ui.progress("Fusion des tuiles et de l'atlas", 0, 0);
        await sleep(0, this.abort.signal);
        tiles = [await mergeTiles(tiles, { withTex: this.withTex, flipV: p.flipV, resolution: p.atlas, log: this.ui.log })];
        this.ui.log("INFO", `Fusion : 1 objet, ${(tiles[0].faces.length / 3).toLocaleString("fr")} triangles`);
      }
      this.ui.progress("Écriture de l'archive", 0, 0);
      await sleep(0, this.abort.signal);
      const files = EXPORTERS[p.fmt](tiles, p.name, p, this.withTex, p.flipV);
      if (this.withTex) for (const t of tiles) if (t.texture) files.push([`${p.name}/textures/${t.name}.jpg`, t.texture]);
      const zip = makeZip(files);
      saveBlob(zip, `${p.name}.zip`);
      this.ui.log("OK", `Export terminé : ${p.name}.zip (${(zip.size / 1e6).toFixed(1)} Mo, ${((performance.now() - t0) / 1000).toFixed(0)} s) — dossier Téléchargements`);
      this.ui.done("finished", "Terminé");
    } catch (e) {
      if (e instanceof Cancelled) {
        this.ui.log("WARN", "Opération annulée (ce qui a déjà été téléchargé est gardé tant que la page reste ouverte)");
        this.ui.done("cancelled", "Annulé");
      } else {
        this.ui.log("ERROR", e.message || String(e));
        this.ui.done("error", "Erreur");
      }
    }
  }
}

function saveBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}

// ---------------------------------------------------------------- interface
const $ = (id) => document.getElementById(id);
const LEVELS = { 15: "~2 m", 16: "~1 m", 17: "~50 cm", 18: "~25 cm", 19: "~12 cm", 20: "~6 cm", 21: "~3 cm" };
const START = [48.8584, 2.2945];

const map = L.map("map").setView(START, 16);
for (const [lvl, res] of Object.entries(LEVELS)) $("level").add(new Option(`Niveau ${lvl} — ${res}/px`, lvl, false, lvl === "18"));

// Fonds de carte. Les serveurs OpenStreetMap refusent les pages ouvertes en file:// et certains
// fournisseurs demandent une cle : plusieurs choix, le dernier utilise est memorise.
const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
const baseLayers = {
  "Google Satellite": L.tileLayer("https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}", {
    maxZoom: 21, subdomains: "0123", attribution: "Imagerie &copy; Google",
  }),
  "Google Plan": L.tileLayer("https://mt{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}", {
    maxZoom: 21, subdomains: "0123", attribution: "Carte &copy; Google",
  }),
  "Esri Satellite": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19, attribution: "Imagerie &copy; Esri",
  }),
  "CARTO Plan": L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
    maxZoom: 20, subdomains: "abcd", attribution: `${OSM_ATTR} &copy; CARTO`,
  }),
};
let savedLayer = null;
try { savedLayer = localStorage.getItem("earthImporter.layer"); } catch (e) { /* stockage indisponible */ }
(baseLayers[savedLayer] || baseLayers["Google Satellite"]).addTo(map);
L.control.layers(baseLayers, null, { collapsed: false, position: "topright" }).addTo(map);
map.on("baselayerchange", (e) => { try { localStorage.setItem("earthImporter.layer", e.name); } catch (err) { /* idem */ } });

// ---- zone carree : centre + cote en metres
const MAX_SIDE = 10000;
function metersPerDegree(lat) {
  const f = lat * Math.PI / 180;
  return [111132.954 - 559.822 * Math.cos(2 * f) + 1.175 * Math.cos(4 * f),
          111412.84 * Math.cos(f) - 93.5 * Math.cos(3 * f)];
}
function squareBounds(lat, lon, side) {
  const [mLat, mLon] = metersPerDegree(lat), h = side / 2;
  return [[lat - h / mLat, lon - h / mLon], [lat + h / mLat, lon + h / mLon]];
}

const zone = { lat: START[0], lon: START[1], side: 400 };
const marker = L.marker(START, { draggable: true, icon: L.divIcon({ className: "pin", iconSize: [16, 16] }) }).addTo(map);
const square = L.rectangle(squareBounds(zone.lat, zone.lon, zone.side), { color: "#4c8dff", weight: 2, fillOpacity: 0.12, interactive: false }).addTo(map);

function setZone(lat, lon, side = zone.side, pan = false) {
  Object.assign(zone, { lat, lon, side: Math.min(MAX_SIDE, Math.round(side)) });
  marker.setLatLng([lat, lon]);
  square.setBounds(squareBounds(lat, lon, zone.side));
  $("coords").value = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  $("coords").classList.remove("bad");
  $("side").value = zone.side;
  if (pan) map.fitBounds(square.getBounds(), { maxZoom: 18, padding: [30, 30] });
}

function parseCoords(text) {
  const m = text.trim().match(/^(-?\d+(?:[.,]\d+)?)\s*[,;\s]\s*(-?\d+(?:[.,]\d+)?)$/);
  if (!m) return null;
  const lat = parseFloat(m[1].replace(",", ".")), lon = parseFloat(m[2].replace(",", "."));
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
}

function goToCoords() {
  const c = parseCoords($("coords").value);
  if (!c) { $("coords").classList.add("bad"); ui.log("ERROR", "Coordonnées invalides (format : 48.8584, 2.2945)"); return; }
  setZone(c[0], c[1], zone.side, true);
}

// ---- dessin a la main : bouton "Dessiner", puis cliquer-glisser d'un coin a l'autre (toujours un carre)
let drawMode = false, drawStart = null, justDrew = false;
function setDrawMode(on) {
  drawMode = on;
  drawStart = null;
  $("draw").classList.toggle("active", on);
  map.getContainer().classList.toggle("drawing", on);
  if (on) map.dragging.disable(); else map.dragging.enable();
}
function squareFrom(a, b) {
  const [mLat, mLon] = metersPerDegree(a.lat);
  const dx = (b.lng - a.lng) * mLon, dy = (b.lat - a.lat) * mLat;
  const s = Math.min(MAX_SIDE, Math.max(Math.abs(dx), Math.abs(dy)));
  const lat2 = a.lat + Math.sign(dy || 1) * s / mLat, lon2 = a.lng + Math.sign(dx || 1) * s / mLon;
  return { lat: (a.lat + lat2) / 2, lon: (a.lng + lon2) / 2, side: s, bounds: [[a.lat, a.lng], [lat2, lon2]] };
}
map.on("mousedown", (e) => { if (drawMode) drawStart = e.latlng; });
map.on("mousemove", (e) => {
  if (!drawStart) return;
  const sq = squareFrom(drawStart, e.latlng);
  square.setBounds(sq.bounds);
  $("side").value = Math.round(sq.side);
});
map.on("mouseup", (e) => {
  if (!drawStart) return;
  const sq = squareFrom(drawStart, e.latlng);
  setDrawMode(false);
  justDrew = true;
  setTimeout(() => { justDrew = false; }, 50);
  if (sq.side < 5) { setZone(zone.lat, zone.lon); return; }  // simple clic : on garde l'ancienne zone
  setZone(sq.lat, sq.lon, sq.side);
  ui.log("INFO", `Zone dessinée : carré de ${zone.side} m`);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && drawMode) { setDrawMode(false); setZone(zone.lat, zone.lon); } });

map.on("click", (e) => { if (!drawMode && !justDrew) setZone(e.latlng.lat, e.latlng.lng); });
marker.on("drag", (e) => setZone(e.latlng.lat, e.latlng.lng));
$("draw").onclick = () => setDrawMode(!drawMode);
$("go").onclick = goToCoords;
$("coords").addEventListener("keydown", (e) => { if (e.key === "Enter") goToCoords(); });
$("side").addEventListener("input", () => {
  const v = parseFloat($("side").value);
  if (v > 0) { zone.side = Math.min(MAX_SIDE, v); square.setBounds(squareBounds(zone.lat, zone.lon, zone.side)); }
});
$("side").addEventListener("change", () => setZone(zone.lat, zone.lon, parseFloat($("side").value) || zone.side, true));
setZone(...START);

const ui = {
  log(level, msg) {
    const line = document.createElement("div");
    line.className = level;
    line.innerHTML = `<span class="t">${new Date().toTimeString().slice(0, 8)}</span><span class="lv">${level}</span>`;
    line.append(document.createTextNode(msg));
    const el = $("log"), atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
    el.append(line);
    if (atBottom) el.scrollTop = el.scrollHeight;
  },
  progress(phase, done, total) {
    const fill = $("fill");
    if (total > 0) {
      fill.classList.remove("indeterminate");
      fill.style.width = `${(100 * done / total).toFixed(1)}%`;
      $("ptext").textContent = `${phase} : ${done} / ${total}`;
    } else {
      fill.classList.add("indeterminate");
      fill.style.width = "";
      $("ptext").textContent = done ? `${phase} (${done})` : phase;
    }
  },
  done(state, text) {
    setRunning(false);
    $("fill").classList.remove("indeterminate");
    $("fill").style.width = state === "finished" ? "100%" : $("fill").style.width;
    $("ptext").textContent = text;
    document.body.dataset.state = state;
  },
};

function setRunning(running) {
  for (const id of ["download", "estimate", "fmt", "content", "level", "side", "coords", "go", "draw", "name", "flipv", "merge", "atlas"]) $(id).disabled = running;
  if (!running) syncAtlas();
  if (running) setDrawMode(false);
  $("cancel").disabled = !running;
}

let current = null;
function launch(estimateOnly) {
  const c = parseCoords($("coords").value);
  const side = parseFloat($("side").value), level = parseInt($("level").value, 10);
  if (!c) return ui.log("ERROR", "Coordonnées invalides");
  if (!(side >= 2 && side <= MAX_SIDE)) return ui.log("ERROR", `Côté du carré entre 2 et ${MAX_SIDE} m`);
  let name = $("name").value.trim() || `earth_${c[0].toFixed(5)}_${c[1].toFixed(5)}_c${side}_l${level}`;
  name = name.replace(/[^\w.-]+/g, "_");
  const p = { lat: c[0], lon: c[1], side, half: side / 2, level, content: $("content").value, fmt: $("fmt").value, name,
              flipV: CONFIG.flipV !== $("flipv").checked, merge: $("merge").value === "1", atlas: $("atlas").value };
  setRunning(true);
  document.body.dataset.state = "running";
  ui.progress("Démarrage", 0, 0);
  current = new Job(p, ui, estimateOnly);
  current.run();
}

// l'atlas n'a de sens que si on fusionne avec les textures
function syncAtlas() {
  $("atlas").disabled = $("merge").value !== "1" || $("content").value !== "geo_tex";
}
$("merge").addEventListener("change", syncAtlas);
$("content").addEventListener("change", syncAtlas);
syncAtlas();

$("download").onclick = () => launch(false);
$("estimate").onclick = () => launch(true);
$("cancel").onclick = () => current && current.abort.abort();

ui.log("INFO", "Version fichier local : rien à installer, le résultat arrive en .zip dans ton dossier Téléchargements.");
if (location.protocol === "file:" || base) ui.log("INFO", `Source : ${CONFIG.baseUrl}`);
