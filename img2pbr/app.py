#!/usr/bin/env python3
"""Interface locale d'img2pbr : python3 app.py  ->  http://127.0.0.1:8765

Serveur standard library uniquement (le calcul est fait par img2pbr.run). Rien ne quitte la machine.
"""
import base64
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

import img2pbr

JOBS = {}  # id -> chemin du zip
LOCK = threading.Lock()
MAX_BYTES = 300 * 1024 * 1024
WORK = tempfile.mkdtemp(prefix="img2pbr_")

PAGE = r"""<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>img2pbr</title>
<style>
:root{--ink:#1c1a17;--panel:#262320;--cream:#f2ead8;--muted:#a79f8c;--ocre:#c2a04e;--line:#3a352e}
*{box-sizing:border-box}body{margin:0;background:var(--ink);color:var(--cream);font:15px/1.45 system-ui,sans-serif}
main{max-width:920px;margin:0 auto;padding:28px 18px 60px}h1{font:600 30px 'Saira Condensed',system-ui;letter-spacing:.04em;margin:0 0 4px}
h1 b{color:var(--ocre)}p.sub{color:var(--muted);margin:0 0 22px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}
label.t{display:block;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
#drop{border:2px dashed var(--line);border-radius:10px;padding:26px;text-align:center;cursor:pointer;color:var(--muted)}
#drop.on{border-color:var(--ocre);color:var(--cream)}#drop img{max-width:200px;max-height:200px;border-radius:6px;display:block;margin:0 auto 8px}
.seg{display:flex;gap:8px}.seg input{display:none}.seg span{display:block;padding:10px 22px;border:1px solid var(--line);border-radius:8px;cursor:pointer;font-weight:600}
.seg input:checked+span{background:var(--ocre);color:var(--ink);border-color:var(--ocre)}
.row{display:flex;gap:22px;flex-wrap:wrap;align-items:flex-start}.row>div{flex:1;min-width:240px}
.tog{display:flex;align-items:center;gap:12px;cursor:pointer}.tog input{display:none}
.sw{width:46px;height:26px;background:var(--line);border-radius:13px;position:relative;transition:.15s;flex:none}
.sw:after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:var(--cream);transition:.15s}
.tog input:checked+.sw{background:var(--ocre)}.tog input:checked+.sw:after{left:23px;background:var(--ink)}
small{color:var(--muted);display:block;margin-top:6px}input[type=text],input[type=number]{background:var(--ink);color:var(--cream);border:1px solid var(--line);border-radius:6px;padding:8px;width:100%}
details summary{cursor:pointer;color:var(--muted)}details .g{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:12px}
button{background:var(--ocre);color:var(--ink);border:0;border-radius:8px;padding:12px 28px;font:700 15px system-ui;cursor:pointer}button:disabled{opacity:.45;cursor:wait}
#out{display:none}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin:12px 0}
.grid figure{margin:0}.grid img{width:100%;border-radius:6px;display:block}.grid figcaption{font-size:12px;color:var(--muted);margin-top:4px}
a.dl{display:inline-block;background:var(--cream);color:var(--ink);padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:700}
ul{margin:8px 0;padding-left:18px;color:var(--muted);font-size:13px}#err{color:#e7806f;margin-top:10px}
</style>
<main>
<h1>img2<b>pbr</b></h1><p class="sub">Une image → cartes PBR carrées en EXR ACEScg, avec USD MaterialX en option.</p>
<div class="card"><label class="t">Image source</label>
<div id="drop"><span id="dtxt">Glisse une image ici ou clique pour choisir (JPG, PNG, TIFF, WebP)</span></div>
<input id="file" type="file" accept="image/*" hidden>
<small>L'image est recadrée au centre en carré avant export.</small></div>
<div class="card row">
<div><label class="t">Résolution d'export (carré)</label>
<div class="seg"><label><input type="radio" name="res" value="1k"><span>1K</span></label><label><input type="radio" name="res" value="2k" checked><span>2K</span></label><label><input type="radio" name="res" value="4k"><span>4K</span></label></div>
<small>1024 · 2048 · 4096 px. Une source plus petite est agrandie (signalé).</small></div>
<div><label class="t">USD / MaterialX</label>
<label class="tog"><input type="checkbox" id="usd"><span class="sw"></span><span>Exporter aussi un USD avec shader MaterialX</span></label>
<small>Matériau standard_surface avec basecolor, roughness, normal et déplacement branchés sur les EXR.</small>
<label class="tog" style="margin-top:10px"><input type="checkbox" id="plane"><span class="sw"></span><span>Ajouter un plan de test</span></label></div>
</div>
<div class="card"><label class="t">Nom</label><input type="text" id="name" value="texture">
<div class="row" style="margin-top:14px"><div><label class="t">Format des cartes</label>
<div class="seg"><label><input type="radio" name="fmt" value="exr" checked><span>EXR ACEScg</span></label><label><input type="radio" name="fmt" value="png"><span>PNG sRGB</span></label></div>
<small>EXR : diffuse en ACEScg (half), autres cartes brutes.</small></div>
<div><label class="tog"><input type="checkbox" id="seam" checked><span class="sw"></span><span>Rendre tileable (seamless)</span></label>
<label class="tog" style="margin-top:10px"><input type="checkbox" id="ai"><span class="sw"></span><span>Relief IA (MiDaS, ~66 Mo au 1er usage)</span></label></div></div>
<details style="margin-top:14px"><summary>Réglages des cartes</summary><div class="g">
<div><label class="t">Force du normal</label><input type="number" id="ns" value="4" min="0" step="0.5"></div>
<div><label class="t">Retrait éclairage (0..1)</label><input type="number" id="delight" value="0.9" min="0" max="1" step="0.1"></div>
<div><label class="t">Rugosité de base</label><input type="number" id="rb" value="0.6" min="0" max="1" step="0.05"></div>
<div><label class="t">Déplacement USD (m)</label><input type="number" id="ds" value="0.01" min="0" step="0.005"></div>
<label class="tog"><input type="checkbox" id="dx"><span class="sw"></span><span>Normal DirectX (Y inversé)</span></label>
</div></details></div>
<button id="go" disabled>Générer</button><div id="err"></div>
<div class="card" id="out"><label class="t">Résultat</label><div class="grid" id="grid"></div><div id="meta"></div><ul id="files"></ul><a class="dl" id="dl" href="#">Télécharger le ZIP</a></div>
</main>
<script>
const $=id=>document.getElementById(id);let blob=null;
function pick(f){if(!f)return;blob=f;$('name').value=f.name.replace(/\.[^.]+$/,'').replace(/[^\w-]+/g,'_')||'texture';
 const u=URL.createObjectURL(f);$('drop').innerHTML='<img src="'+u+'"><span>'+f.name+'</span>';$('go').disabled=false}
$('drop').onclick=()=>$('file').click();$('file').onchange=e=>pick(e.target.files[0]);
$('drop').ondragover=e=>{e.preventDefault();$('drop').classList.add('on')};$('drop').ondragleave=()=>$('drop').classList.remove('on');
$('drop').ondrop=e=>{e.preventDefault();$('drop').classList.remove('on');pick(e.dataTransfer.files[0])};
$('go').onclick=async()=>{$('err').textContent='';$('go').disabled=true;$('go').textContent='Calcul en cours…';
 const q=new URLSearchParams({res:document.querySelector('[name=res]:checked').value,fmt:document.querySelector('[name=fmt]:checked').value,usd:$('usd').checked?1:0,plane:$('plane').checked?1:0,name:$('name').value,
  seam:$('seam').checked?1:0,ai:$('ai').checked?1:0,delight:$('delight').value,ns:$('ns').value,rb:$('rb').value,ds:$('ds').value,dx:$('dx').checked?1:0});
 try{const r=await fetch('/convert?'+q,{method:'POST',body:blob});const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);
  $('grid').innerHTML=Object.entries(j.previews).map(([k,v])=>'<figure><img src="data:image/png;base64,'+v+'"><figcaption>'+k+'</figcaption></figure>').join('');
  $('meta').innerHTML='<small>'+j.meta.resolution+' × '+j.meta.resolution+' px · '+j.meta.format.toUpperCase()+(j.meta.format==='exr'?' ACEScg':'')+' · couture '+j.meta.seam+(j.meta.upscaled?' · <b>source agrandie</b>':'')+'</small>';
  $('files').innerHTML=j.files.map(f=>'<li>'+f+'</li>').join('');$('dl').href='/download/'+j.job;$('dl').download=j.zip;$('out').style.display='block'}
 catch(e){$('err').textContent='Erreur : '+e.message}
 $('go').disabled=false;$('go').textContent='Générer'};
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            b = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif u.path.startswith("/download/"):
            with LOCK:
                p = JOBS.get(u.path.split("/")[-1])
            if not p or not os.path.exists(p):
                return self._json(404, {"error": "introuvable"})
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(os.path.getsize(p)))
            self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(p)}"')
            self.end_headers()
            with open(p, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
        else:
            self._json(404, {"error": "introuvable"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/convert":
            return self._json(404, {"error": "introuvable"})
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        n = int(self.headers.get("Content-Length", 0))
        if not 0 < n <= MAX_BYTES:
            return self._json(413, {"error": "image absente ou trop lourde (300 Mo max)"})
        data = self.rfile.read(n)
        try:
            res = q.get("res", "2k").lower()
            if res not in img2pbr.RES:
                raise ValueError("résolution : 1k, 2k ou 4k")
            name = re.sub(r"[^\w-]+", "_", q.get("name", "texture")).strip("_") or "texture"
            job = uuid.uuid4().hex[:10]
            d = os.path.join(WORK, job, name)
            os.makedirs(d)
            os.makedirs(os.path.join(WORK, job, "src"))
            src = os.path.join(WORK, job, "src", f"{name}.img")        # le stem donne le nom des cartes
            with open(src, "wb") as f:
                f.write(data)
            if cv2.imread(src, cv2.IMREAD_COLOR) is None:
                raise ValueError("image illisible (formats : JPG, PNG, TIFF, WebP)")
            argv = [src, "-o", d, "--res", res, "--format", q.get("fmt", "exr"),
                    "--ai", "midas" if q.get("ai") == "1" else "off",
                    "--normal-strength", q.get("ns", "4"), "--delight", q.get("delight", "0.9"),
                    "--roughness-base", q.get("rb", "0.6"), "--disp-scale", q.get("ds", "0.01")]
            if q.get("seam") != "1":
                argv.append("--no-seamless")
            if q.get("dx") == "1":
                argv.append("--flip-y")
            if q.get("usd") == "1":
                argv.append("--usd")
                if q.get("plane") == "1":
                    argv.append("--usd-plane")
            logs = []
            r = img2pbr.run(argv, log=logs.append)
            os.remove(src)
            files = {k: str(p) for k, p in r["files"].items()}
            previews = {k: preview_png(v, k) for k, v in r["maps"].items()}
            r = dict(r, files=files, previews=previews,
                     meta=dict(resolution=r["size"], format=r["format"], seam=f"{r['seam'][1]:.1f}",
                               upscaled=any("suréchantillonnage" in l for l in logs)))
            zp = os.path.join(WORK, job, f"{name}_{res}.zip")
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:  # EXR déjà compressés
                for p in r["files"].values():
                    z.write(p, os.path.join(name, os.path.basename(p)))
            with LOCK:
                JOBS[job] = zp
            self._json(200, dict(job=job, zip=os.path.basename(zp), meta=r["meta"],
                                 files=[os.path.basename(p) for p in r["files"].values()],
                                 previews={k: base64.b64encode(v).decode() for k, v in r["previews"].items()}))
        except (Exception, SystemExit) as e:  # image ou paramètre invalide…
            self._json(400, {"error": str(e)})


def preview_png(arr, key, size=256):
    """aperçu PNG 8 bits d'une carte (valeurs déjà en sRGB pour diffuse)."""
    a = np.clip(arr, 0, 1)
    a = (a * 255 + 0.5).astype(np.uint8)
    if a.ndim == 3:
        a = a[..., ::-1]
    ok, buf = cv2.imencode(".png", cv2.resize(a, (size, size), interpolation=cv2.INTER_AREA))
    return buf.tobytes()


def main():
    port = int(os.environ.get("PORT", 8765))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"img2pbr : http://127.0.0.1:{port}  (Ctrl+C pour quitter)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
