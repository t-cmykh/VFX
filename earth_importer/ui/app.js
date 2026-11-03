"use strict";

const $ = (id) => document.getElementById(id);
const START = [48.8584, 2.2945];

// ---------------------------------------------------------------- carte
const map = L.map("map").setView(START, 16);
// fond de carte unique : Google Satellite (meme imagerie que les textures de la 3D)
L.tileLayer("https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}", {
  maxZoom: 21, subdomains: "0123", attribution: "Imagerie &copy; Google",
}).addTo(map);
const marker = L.marker(START, { draggable: true }).addTo(map);
const circle = L.circle(START, { radius: 200, color: "#4c8dff", weight: 2, fillOpacity: 0.12 }).addTo(map);


function setCenter(lat, lon, pan = false) {
  marker.setLatLng([lat, lon]);
  circle.setLatLng([lat, lon]);
  $("coords").value = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  $("coords").classList.remove("bad");
  if (pan) map.fitBounds(circle.getBounds(), { maxZoom: 18 });
}

function parseCoords(text) {
  const m = text.trim().match(/^(-?\d+(?:[.,]\d+)?)\s*[,;\s]\s*(-?\d+(?:[.,]\d+)?)$/);
  if (!m) return null;
  const lat = parseFloat(m[1].replace(",", ".")), lon = parseFloat(m[2].replace(",", "."));
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null;
}


map.on("click", (e) => setCenter(e.latlng.lat, e.latlng.lng));
marker.on("drag", (e) => setCenter(e.latlng.lat, e.latlng.lng));
$("radius").addEventListener("input", () => {
  const r = parseFloat($("radius").value);
  if (r > 0) circle.setRadius(r);
});
$("radius").addEventListener("change", () => map.fitBounds(circle.getBounds(), { maxZoom: 18 }));
setCenter(...START);

// ---------------------------------------------------------------- lieu : nom (recherche) ou coordonnees
setupPlaceSearch({
  input: $("coords"), button: $("go"), list: $("places"), parseCoords, log,
  onPick(lat, lon, place) {
    const label = $("coords").value;
    setCenter(lat, lon, true);
    if (place) $("coords").value = label;  // garder le nom tape ; les coordonnees sont dans le log
    showView("map");
  },
});

// ---------------------------------------------------------------- carte / apercu 3D
function showView(which) {
  $("map").hidden = which !== "map";
  $("viewer").hidden = which !== "3d";
  $("tab-map").classList.toggle("active", which === "map");
  $("tab-3d").classList.toggle("active", which === "3d");
  if (which === "map") map.invalidateSize();
}
$("tab-map").onclick = () => showView("map");
$("tab-3d").onclick = () => showView("3d");

// apercu empaquete par core/job.py (publish_preview) : [u32 taille JSON][JSON][donnees alignees]
function parsePreview(buf) {
  const n = new DataView(buf).getUint32(0, true);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, n)));
  let off = 4 + n;
  const take = (Type, count) => { const a = new Type(buf, off, count); off += count * Type.BYTES_PER_ELEMENT; return a; };
  return meta.tiles.map((t) => {
    const tile = { name: t.name, pos: take(Float32Array, 3 * t.nv), faces: take(Uint32Array, 3 * t.nf), uv: null, texture: null };
    if (t.uv) tile.uv = take(Float32Array, 2 * t.nv);
    if (t.tex) tile.texture = new Uint8Array(buf, off, t.tex);
    off += t.tex + ((4 - (t.tex % 4)) % 4);
    if (t.color !== null && t.color !== undefined) tile.color = t.color;
    return tile;
  });
}

async function loadPreview() {
  try {
    const r = await fetch("/api/preview");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const tiles = parsePreview(await r.arrayBuffer());
    showView("3d");
    Preview.show($("viewer"), tiles);
    $("tab-3d").disabled = false;
    log("INFO", "Aperçu 3D : clic gauche = tourner, clic droit = déplacer, molette = zoom");
  } catch (e) {
    log("ERROR", `Aperçu 3D impossible (${e.message})`);
  }
}

// ---------------------------------------------------------------- log / progression
function log(level, msg, time) {
  const line = document.createElement("div");
  line.className = level;
  const t = time || new Date().toTimeString().slice(0, 8);
  line.innerHTML = `<span class="t">${t}</span><span class="lv">${level}</span>`;
  line.append(document.createTextNode(msg));
  const el = $("log");
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  el.append(line);
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function progress(phase, done, total) {
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
}

function setRunning(running) {
  for (const id of ["download", "estimate", "fmt", "content", "radius", "coords", "go", "outdir", "lidar", "lidarres", "laz"]) $(id).disabled = running;
  if (!running) syncLidar();
  $("cancel").disabled = !running;
}

// ---------------------------------------------------------------- serveur
function params() {
  const typed = parseCoords($("coords").value);  // coordonnees tapees sans Entree
  const c = typed || [marker.getLatLng().lat, marker.getLatLng().lng];
  return {
    lat: c[0], lon: c[1],
    radius: parseFloat($("radius").value),
    content: $("content").value,
    fmt: $("fmt").value,
    out_dir: $("outdir").value.trim(),
    name: $("name").value.trim(),
    lidar: $("lidar").value,
    lidar_res: parseFloat($("lidarres").value),
    laz: $("laz").checked,
  };
}

function syncLidar() {
  $("lidarres").disabled = $("lidar").value === "none";
}
$("lidar").addEventListener("change", syncLidar);
syncLidar();

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${r.status}`);
  return data;
}

async function launch(kind) {
  let p;
  try { p = params(); } catch (e) { log("ERROR", e.message); return; }
  try {
    setRunning(true);
    progress("Démarrage", 0, 0);
    const res = await post(`/api/${kind}`, p);
    if (kind === "download") log("INFO", `Sortie : ${res.out}`);
  } catch (e) {
    setRunning(false);
    progress("Prêt", 0, 0);
    $("fill").classList.remove("indeterminate");
    log("ERROR", e.message);
  }
}

$("download").onclick = () => launch("download");
$("estimate").onclick = () => launch("estimate");
$("cancel").onclick = () => post("/api/cancel").catch((e) => log("ERROR", e.message));

let lastEvent = 0;
function listen() {
  const es = new EventSource(`/api/events?since=${lastEvent}`);
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    lastEvent = ev.id + 1;
    const d = ev.data;
    if (ev.kind === "log") log(d.level, d.msg, d.time);
    else if (ev.kind === "progress") progress(d.phase, d.done, d.total);
    else if (ev.kind === "estimate") $("ptext").textContent = `Estimation : ${d.tiles} tuiles`;
    else if (ev.kind === "preview") loadPreview();
    else if (ev.kind === "state" && d.state !== "running") {
      setRunning(false);
      $("fill").classList.remove("indeterminate");
      if (d.state === "finished" && d.path) { $("fill").style.width = "100%"; $("ptext").textContent = "Terminé"; }
      else if (d.state === "finished" && d.preview) { $("ptext").textContent = "Aperçu prêt"; }
      else if (d.state !== "finished") { $("ptext").textContent = d.state === "cancelled" ? "Annulé" : "Erreur"; }
    }
  };
  es.onerror = () => { es.close(); setTimeout(listen, 1500); };
}

(async function init() {
  try {
    const info = await (await fetch("/api/info")).json();
    $("outdir").value = info.default_out;
    if (info.native) $("pick").hidden = false;
    if (!info.blender) log("WARN", "Blender introuvable : seuls les exports OBJ fonctionneront (voir blender_path dans config.yaml)");
    else log("INFO", `Blender détecté : ${info.blender}`);
  } catch (e) {
    log("ERROR", "Serveur local injoignable");
  }
  listen();
})();

$("pick").onclick = async () => {
  const dir = await window.pywebview.api.choose_folder();
  if (dir) $("outdir").value = dir;
};
