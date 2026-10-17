"use strict";

const $ = (id) => document.getElementById(id);
const LEVELS = { 15: "~2 m", 16: "~1 m", 17: "~50 cm", 18: "~25 cm", 19: "~12 cm", 20: "~6 cm", 21: "~3 cm" };
const START = [48.8584, 2.2945];

// ---------------------------------------------------------------- carte
const map = L.map("map").setView(START, 16);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
const marker = L.marker(START, { draggable: true }).addTo(map);
const circle = L.circle(START, { radius: 200, color: "#4c8dff", weight: 2, fillOpacity: 0.12 }).addTo(map);

for (const [lvl, res] of Object.entries(LEVELS)) {
  $("level").add(new Option(`Niveau ${lvl} — ${res}/px`, lvl, false, lvl === "18"));
}

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

function goToCoords() {
  const c = parseCoords($("coords").value);
  if (!c) { $("coords").classList.add("bad"); log("ERROR", "Coordonnées invalides (format : 48.8584, 2.2945)"); return; }
  setCenter(c[0], c[1], true);
}

map.on("click", (e) => setCenter(e.latlng.lat, e.latlng.lng));
marker.on("drag", (e) => setCenter(e.latlng.lat, e.latlng.lng));
$("go").onclick = goToCoords;
$("coords").addEventListener("keydown", (e) => { if (e.key === "Enter") goToCoords(); });
$("radius").addEventListener("input", () => {
  const r = parseFloat($("radius").value);
  if (r > 0) circle.setRadius(r);
});
$("radius").addEventListener("change", () => map.fitBounds(circle.getBounds(), { maxZoom: 18 }));
setCenter(...START);

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
  for (const id of ["download", "estimate", "fmt", "content", "level", "radius", "coords", "go", "outdir"]) $(id).disabled = running;
  $("cancel").disabled = !running;
}

// ---------------------------------------------------------------- serveur
function params() {
  const c = parseCoords($("coords").value);
  if (!c) throw new Error("Coordonnées invalides");
  return {
    lat: c[0], lon: c[1],
    radius: parseFloat($("radius").value),
    level: parseInt($("level").value, 10),
    content: $("content").value,
    fmt: $("fmt").value,
    out_dir: $("outdir").value.trim(),
    name: $("name").value.trim(),
  };
}

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
    else if (ev.kind === "state" && d.state !== "running") {
      setRunning(false);
      $("fill").classList.remove("indeterminate");
      if (d.state === "finished" && d.path) { $("fill").style.width = "100%"; $("ptext").textContent = "Terminé"; }
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
