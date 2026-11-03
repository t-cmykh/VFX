"""Faux services IGN pour les tests : WMS raster (image/x-bil;bits=32) et WFS des dalles LAZ.

Relief synthetique en Lambert-93, autour du point de test :
    z = 100 + 0.1 * (Y - Y0)          pente vers le NORD (sert a verifier le sens des lignes du BIL)
      + 20 dans un carre de 40 m centre a 60 m a l'EST (un "batiment", MNS seulement)
Comme le vrai service (selon la doc trouvee), la premiere ligne du BIL est le bord SUD.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

from core.lidar import lonlat_to_lambert93

LAT, LON = 48.8584, 2.2945
X0, Y0 = (float(v) for v in lonlat_to_lambert93(LON, LAT))
LAZ = b"LASF" + bytes(2000)


def relief(x, y, layer):
    z = 100 + 0.1 * (y - Y0)
    if "MNS" in layer:
        z = z + 20 * ((np.abs(x - (X0 + 60)) < 20) & (np.abs(y - Y0) < 20))
    return z


class FakeIgn:
    def __init__(self, empty_layers=()):
        self.requests = []
        empty = set(empty_layers)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, body, ctype="application/octet-stream"):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                outer.requests.append(self.path)
                u = urlparse(self.path)
                q = {k.upper(): v[0] for k, v in parse_qs(u.query).items()}
                if u.path.endswith("/wms"):
                    x0, y0, x1, y1 = (float(v) for v in q["BBOX"].split(","))
                    w, h = int(q["WIDTH"]), int(q["HEIGHT"])
                    layer = q["LAYERS"]
                    xs = x0 + (np.arange(w) + 0.5) * (x1 - x0) / w
                    ys = y0 + (np.arange(h) + 0.5) * (y1 - y0) / h   # du sud vers le nord
                    X, Y = np.meshgrid(xs, ys)
                    z = relief(X, Y, layer).astype("<f4")
                    if layer in empty:
                        z[:] = -99999
                    self.send(z.tobytes())
                elif u.path.endswith("/wfs"):
                    x0, y0 = int(X0 // 1000) * 1000, int(Y0 // 1000 + 1) * 1000
                    feats = [{"type": "Feature", "properties": {
                        "name": f"LHD_FXX_{x0 // 1000:04d}_{y0 // 1000:04d}",
                        "url": f"{outer.url}/laz/LHD_FXX_{x0 // 1000:04d}_{y0 // 1000:04d}_PTS_C_LAMB93_IGN69.copc.laz"}}]
                    self.send(json.dumps({"type": "FeatureCollection", "features": feats}).encode(), "application/json")
                elif u.path.startswith("/laz/"):
                    self.send(LAZ)
                else:
                    self.send_response(404)
                    self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def config(self):
        return {"wms": self.url + "/wms", "wfs": self.url + "/wfs", "min_interval": 0}

    def close(self):
        self.httpd.shutdown()
