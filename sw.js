// App shell service worker — same-origin files only (index.html, manifest, icon). CDN scripts
// (Cesium, earcut, gltf-transform, ...) are left untouched, they already get normal HTTP caching
// and this file has no business deciding freshness for someone else's server.
//
// Network-first for index.html: this tool is edited and pushed often, so a stale cache-first copy
// would silently strand a phone on an old version. Try the network, fall back to the cached shell
// only when actually offline (or the fetch fails). Manifest/icon rarely change, cache-first is fine.
const CACHE = "geo-extractor-shell-v1";
const APP_SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icons/icon.svg"];

self.addEventListener("install", function(e){
  e.waitUntil(caches.open(CACHE).then(function(c){ return c.addAll(APP_SHELL); }));
  self.skipWaiting();
});

self.addEventListener("activate", function(e){
  e.waitUntil(
    caches.keys().then(function(keys){
      return Promise.all(keys.filter(function(k){ return k !== CACHE; }).map(function(k){ return caches.delete(k); }));
    }).then(function(){ return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function(e){
  var url = new URL(e.request.url);
  if (url.origin !== location.origin) return; // never intercept CDN/API requests

  var isHtml = e.request.mode === "navigate" || url.pathname.endsWith("index.html") || url.pathname === "/" || url.pathname.endsWith("/");
  if (isHtml){
    e.respondWith(
      fetch(e.request).then(function(res){
        var copy = res.clone();
        caches.open(CACHE).then(function(c){ c.put(e.request, copy); });
        return res;
      }).catch(function(){ return caches.match(e.request).then(function(r){ return r || caches.match("./index.html"); }); })
    );
    return;
  }

  e.respondWith(caches.match(e.request).then(function(r){ return r || fetch(e.request); }));
});
