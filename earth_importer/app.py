"""Earth Importer : fenetre locale (carte OSM + formulaire) qui pilote core/job.py.

    pip install -r requirements.txt
    python app.py              # fenetre native si pywebview est installe, sinon navigateur
    python app.py --browser    # force le navigateur
"""
import argparse
import asyncio
import json
import threading
import time
import webbrowser
from pathlib import Path

import requests
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.job import Job, Params, find_blender

HERE = Path(__file__).resolve().parent
CFG = {}


def load_config(path):
    CFG.clear()
    CFG.update(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


load_config(HERE / "config.yaml")

app = FastAPI(title="Earth Importer")
app.mount("/ui", StaticFiles(directory=HERE / "ui"), name="ui")


class Events:
    """Journal d'evenements partage entre le thread du job et le flux SSE."""

    def __init__(self):
        self.items = []
        self.lock = threading.Lock()

    def emit(self, kind, data):
        data = dict(data, time=time.strftime("%H:%M:%S"))
        with self.lock:
            self.items.append({"id": len(self.items), "kind": kind, "data": data})

    def since(self, idx):
        with self.lock:
            return self.items[idx:]


EVENTS = Events()
STATE = {"job": None, "thread": None}


class Request(BaseModel):
    lat: float
    lon: float
    radius: float = 200
    level: int = 18
    content: str = "geo_tex"
    fmt: str = "obj"
    out_dir: str = ""
    name: str = ""


def start(req, estimate_only):
    job = STATE["job"]
    if job and STATE["thread"].is_alive():
        raise HTTPException(409, "Une operation est deja en cours")
    p = Params(**req.model_dump())
    p.out_dir = p.out_dir or str(Path.home() / "EarthImporter")
    try:
        p.validate()
    except ValueError as e:
        raise HTTPException(400, str(e))
    job = Job(CFG, p, EVENTS.emit, estimate_only=estimate_only)
    EVENTS.emit("state", {"state": "running", "estimate": estimate_only})
    th = threading.Thread(target=job.run, daemon=True)
    STATE.update(job=job, thread=th)
    th.start()
    return {"ok": True, "out": str(job.out)}


@app.get("/")
def index():
    return FileResponse(HERE / "ui" / "index.html")


@app.get("/api/info")
def info():
    return {
        "blender": find_blender(CFG.get("blender_path", "")),
        "default_out": str(Path.home() / "EarthImporter"),
        "native": STATE.get("native", False),
    }


@app.post("/api/estimate")
def estimate(req: Request):
    return start(req, True)


@app.post("/api/download")
def download(req: Request):
    return start(req, False)


@app.post("/api/cancel")
def cancel():
    job = STATE["job"]
    if job:
        job.cancel.set()
    return {"ok": True}


@app.get("/api/events")
async def events(since: int = 0):
    async def stream():
        idx = since
        while True:
            for ev in EVENTS.since(idx):
                idx = ev["id"] + 1
                yield f"id: {ev['id']}\ndata: {json.dumps(ev)}\n\n"
            await asyncio.sleep(0.15)

    return StreamingResponse(stream(), media_type="text/event-stream")


class NativeApi:
    """Expose au JS de la fenetre pywebview un selecteur de dossier natif."""

    def choose_folder(self):
        import webview
        res = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        return res[0] if res else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", action="store_true", help="ouvrir dans le navigateur au lieu d'une fenetre")
    ap.add_argument("--no-open", action="store_true", help="ne rien ouvrir (serveur seul)")
    ap.add_argument("--config", default=str(HERE / "config.yaml"), help="autre fichier de configuration")
    args = ap.parse_args()
    load_config(args.config)
    host, port = CFG.get("host", "127.0.0.1"), CFG.get("port", 8757)
    url = f"http://{host}:{port}/"

    if already_running(url):
        # une instance tourne deja (souvent lancee sans console) : on la reutilise
        print(f"Earth Importer deja lance : {url}")
        if not args.no_open:
            webbrowser.open(url)
        return

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    if args.no_open:
        server.run()
        return
    webview = None
    if not args.browser:
        try:
            import webview
        except Exception as e:  # absent, ou dependance native manquante (pythonnet, WebView2...)
            print(f"pywebview indisponible ({e}) : ouverture dans le navigateur")
    if webview is not None:
        STATE["native"] = True
        threading.Thread(target=server.run, daemon=True).start()
        try:
            webview.create_window("Earth Importer", url, js_api=NativeApi(), width=1280, height=860)
            webview.start()
            return
        except Exception as e:
            print(f"Fenetre native impossible ({e}) : ouverture dans le navigateur")
            STATE["native"] = False
            webbrowser.open(url)
            threading.Event().wait()  # le serveur tourne dans son thread
    threading.Timer(1.0, webbrowser.open, [url]).start()
    print(f"Earth Importer : {url}  (Ctrl+C pour quitter)")
    server.run()


def already_running(url):
    try:
        return requests.get(url + "api/info", timeout=1).ok
    except requests.RequestException:
        return False


if __name__ == "__main__":
    main()
