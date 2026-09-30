"""
Backend local pour l'outil desktop d'extraction de geometries OSM/IGN.

Le seul role de ce serveur est de faire les requetes reseau (Overpass, IGN,
Lyon, Nominatim) depuis le processus Python plutot que depuis le navigateur —
ca elimine le probleme de CORS a la racine (CORS est une restriction du
navigateur, un script qui fait ses propres requetes n'y est jamais soumis).
Toute la logique de construction de geometrie (toits, murs, terrain, export
OBJ/GLB) reste le code JS deja teste/valide de la version navigateur, servi
tel quel par ce serveur — on ne re-porte pas ce qui marche deja.

Lance avec : python3 server.py
Ouvre ensuite http://127.0.0.1:8756 dans un navigateur.
"""
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

HOST = "127.0.0.1"  # jamais 0.0.0.0 : ce proxy ne doit etre joignable que depuis cette machine
PORT = 8756

# allowlist de domaines — le proxy ne doit jamais devenir un relais ouvert vers
# n'importe quelle URL, meme si tout ca tourne en local. Un nouveau domaine a
# ajouter (nouvelle source de donnees) = une ligne ici, rien d'autre a changer
# cote backend.
ALLOWED_HOSTS = {
    "overpass-api.de",
    "overpass.kumi.systems",
    "overpass.openstreetmap.ru",
    "data.geopf.fr",
    "nominatim.openstreetmap.org",
    "data.grandlyon.com",
}

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Geo Extractor — backend local")


def _check_allowed(url: str) -> None:
    host = urlparse(url).hostname or ""
    if host not in ALLOWED_HOSTS:
        raise HTTPException(status_code=403, detail=f"hote non autorise par le proxy local : {host}")


@app.get("/api/proxy")
def proxy_get(url: str = Query(...)):
    _check_allowed(url)
    try:
        upstream = requests.get(url, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"echec requete vers {url} : {e}")
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
    )


@app.post("/api/proxy")
async def proxy_post(request: Request, url: str = Query(...)):
    _check_allowed(url)
    body = await request.body()
    content_type = request.headers.get("content-type", "text/plain")
    try:
        upstream = requests.post(url, data=body, headers={"Content-Type": content_type}, timeout=120)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"echec requete vers {url} : {e}")
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
    )


LYON_MESH_BASE = "https://data.grandlyon.com/files/grandlyon/2023/mesh/"


@app.get("/api/lyon/{path:path}")
def lyon_proxy(path: str):
    # path-based (not query-param) so Cesium's own relative-URL resolution for the 3D Tiles
    # pyramid keeps working: a tileset.json served from /api/lyon/tileset.json that references
    # "pyramid/tileset.json" has Cesium resolve that to /api/lyon/pyramid/tileset.json, which
    # this route maps straight back to the matching path on the real server.
    url = LYON_MESH_BASE + path
    try:
        upstream = requests.get(url, timeout=60)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"echec requete vers {url} : {e}")
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
    )


# sert le frontend (copie adaptee de la version navigateur — memes fichiers
# statiques, Cesium/earcut/etc restent charges depuis les CDN comme avant,
# seules les URLs d'API externes sont redirigees vers /api/proxy). Monte a la
# racine (pas sous /static) pour que les chemins relatifs du HTML (./icons/...)
# resolvent naturellement ; les routes /api/* enregistrees plus haut passent
# en premier, ce mount ne recoit que ce qu'aucune route plus specifique n'a pris.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
def main():
    print(f"Geo Extractor (desktop) -> http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
