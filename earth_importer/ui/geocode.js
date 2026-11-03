"use strict";
// Recherche d'un lieu par son nom (commun a la version Python et a la version fichier local).
// Photon (komoot, base OpenStreetMap, sans cle, accepte les pages locales), puis Nominatim en secours.
// Resultat : [{ label, lat, lon, bounds: [[s, w], [n, e]] | null }]

const GEOCODERS = [
  {
    name: "Photon",
    url: (q) => `https://photon.komoot.io/api/?q=${encodeURIComponent(q)}&limit=6&lang=fr`,
    parse: (data) => (data.features || []).map((f) => {
      const p = f.properties || {}, [lon, lat] = f.geometry.coordinates;
      const parts = [p.name, p.street && `${p.housenumber ? p.housenumber + " " : ""}${p.street}`, p.city, p.state, p.country];
      const ext = p.extent;  // [ouest, nord, est, sud]
      return { label: [...new Set(parts.filter(Boolean))].join(", "), lat, lon,
               bounds: ext ? [[ext[3], ext[0]], [ext[1], ext[2]]] : null };
    }),
  },
  {
    name: "Nominatim",
    url: (q) => `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&accept-language=fr&q=${encodeURIComponent(q)}`,
    parse: (data) => data.map((r) => ({
      label: r.display_name, lat: parseFloat(r.lat), lon: parseFloat(r.lon),
      bounds: r.boundingbox ? [[+r.boundingbox[0], +r.boundingbox[2]], [+r.boundingbox[1], +r.boundingbox[3]]] : null,
    })),
  },
];

async function geocode(query) {
  let lastError = null;
  for (const g of GEOCODERS) {
    try {
      const r = await fetch(g.url(query));
      if (!r.ok) throw new Error(`${g.name} : HTTP ${r.status}`);
      const res = g.parse(await r.json()).filter((x) => Number.isFinite(x.lat) && Number.isFinite(x.lon));
      if (res.length) return res;
    } catch (e) {
      lastError = e;
    }
  }
  if (lastError) throw new Error(`Recherche de lieu impossible (${lastError.message || "réseau"})`);
  return [];
}

// Champ "Lieu ou coordonnees" : Entree / bouton Aller -> coordonnees, sinon recherche du nom.
// onPick(lat, lon) est appele pour le resultat choisi ; la liste montre les autres resultats.
function setupPlaceSearch({ input, button, list, parseCoords, onPick, log }) {
  let results = [];
  const hide = () => { list.hidden = true; list.innerHTML = ""; };
  function show(items) {
    list.innerHTML = "";
    items.forEach((r, k) => {
      const li = document.createElement("li");
      li.textContent = r.label;
      li.tabIndex = 0;
      if (k === 0) li.className = "current";
      const pick = () => {
        [...list.children].forEach((c) => c.classList.toggle("current", c === li));
        onPick(r.lat, r.lon, r);
        input.value = r.label;
        hide();
      };
      li.onclick = pick;
      li.onkeydown = (e) => { if (e.key === "Enter") pick(); };
      list.append(li);
    });
    list.hidden = items.length < 2;
  }
  async function go() {
    const text = input.value.trim();
    if (!text) return;
    const c = parseCoords(text);
    if (c) { hide(); onPick(c[0], c[1], null); return; }
    input.classList.remove("bad");
    button.disabled = true;
    try {
      results = await geocode(text);
      if (!results.length) {
        input.classList.add("bad");
        log("WARN", `Aucun lieu trouvé pour « ${text} »`);
        hide();
        return;
      }
      onPick(results[0].lat, results[0].lon, results[0]);
      log("INFO", `Lieu : ${results[0].label} (${results[0].lat.toFixed(6)}, ${results[0].lon.toFixed(6)})`);
      show(results);
    } catch (e) {
      input.classList.add("bad");
      log("ERROR", e.message);
    } finally {
      button.disabled = false;
    }
  }
  button.addEventListener("click", go);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") go();
    else if (e.key === "Escape") hide();
    else if (e.key === "ArrowDown" && !list.hidden) { e.preventDefault(); list.firstChild.focus(); }
  });
  list.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && document.activeElement.nextSibling) { e.preventDefault(); document.activeElement.nextSibling.focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); (document.activeElement.previousSibling || input).focus(); }
    if (e.key === "Escape") { hide(); input.focus(); }
  });
  document.addEventListener("click", (e) => { if (!list.contains(e.target) && e.target !== input && e.target !== button) hide(); });
  return { hide };
}
