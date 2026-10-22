"""Client minimal de l'API HTTP de ComfyUI (bibliotheque standard uniquement : tourne dans mayapy)."""
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ComfyError(RuntimeError):
    pass


class ComfyCancelled(ComfyError):
    pass


class ComfyClient:
    def __init__(self, server="127.0.0.1:8188", timeout=30):
        base = server.strip().rstrip("/")
        self.base = base if base.startswith("http") else "http://" + base
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex

    # --- bas niveau -------------------------------------------------------
    def _request(self, method, path, data=None, headers=None, timeout=None):
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise ComfyError("%s %s -> HTTP %d\n%s" % (method, path, e.code, _explain_error(body)))
        except (urllib.error.URLError, OSError) as e:
            raise ComfyError("ComfyUI injoignable sur %s (%s)" % (self.base, getattr(e, "reason", e)))

    def _json(self, method, path, payload=None, timeout=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data is not None else None
        out = self._request(method, path, data, headers, timeout)
        return json.loads(out) if out else {}

    # --- API --------------------------------------------------------------
    def ping(self, timeout=3):
        return self._json("GET", "/system_stats", timeout=timeout)

    def upload(self, path, subfolder="maya_comfy"):
        """Envoie un fichier dans le dossier input/ de ComfyUI. Renvoie la valeur a mettre dans LoadImage."""
        name = os.path.basename(path)
        with open(path, "rb") as f:
            content = f.read()
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        body, content_type = _multipart({"subfolder": subfolder, "type": "input", "overwrite": "true"},
                                        {"image": (name, content, ctype)})
        res = json.loads(self._request("POST", "/upload/image", body, {"Content-Type": content_type}))
        return "%s/%s" % (res["subfolder"], res["name"]) if res.get("subfolder") else res["name"]

    def queue(self, workflow):
        res = self._json("POST", "/prompt", {"prompt": workflow, "client_id": self.client_id})
        if res.get("node_errors"):
            raise ComfyError("Workflow refuse :\n" + _explain_error(json.dumps(res)))
        return res["prompt_id"]

    def wait(self, prompt_id, timeout=3600, poll=1.0, on_progress=None, should_stop=None):
        """Attend la fin du calcul et renvoie le dict 'outputs' de l'historique ComfyUI."""
        t0 = time.time()
        while True:
            if should_stop and should_stop():
                self.cancel(prompt_id)
                raise ComfyCancelled("Annule")
            entry = self._json("GET", "/history/" + prompt_id).get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ComfyError(_status_message(status))
                return entry.get("outputs") or {}
            if on_progress:
                on_progress(self._queue_state(prompt_id, time.time() - t0))
            if time.time() - t0 > timeout:
                raise ComfyError("Delai depasse (%d s) pour %s" % (timeout, prompt_id))
            time.sleep(poll)

    def _queue_state(self, prompt_id, elapsed):
        try:
            q = self._json("GET", "/queue")
        except ComfyError:
            return "Calcul... %d s" % elapsed
        if any(item[1] == prompt_id for item in q.get("queue_running", [])):
            return "Calcul en cours... %d s" % elapsed
        pending = [item[1] for item in sorted(q.get("queue_pending", []), key=lambda i: i[0])]
        if prompt_id in pending:
            return "En file d'attente (position %d)... %d s" % (pending.index(prompt_id) + 1, elapsed)
        return "Calcul... %d s" % elapsed

    def cancel(self, prompt_id=None):
        if prompt_id:
            try:
                self._json("POST", "/queue", {"delete": [prompt_id]})
            except ComfyError:
                pass
        self._request("POST", "/interrupt", b"", {"Content-Type": "application/json"})

    def free(self, unload_models=True):
        """Decharge les modeles de la VRAM (indispensable entre Qwen et LTX sur une seule carte)."""
        self._json("POST", "/free", {"unload_models": unload_models, "free_memory": True})

    def download(self, item, dest_dir):
        query = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                        "type": item.get("type", "output")})
        data = self._request("GET", "/view?" + query, timeout=max(self.timeout, 120))
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, os.path.basename(item["filename"]))
        with open(path, "wb") as f:
            f.write(data)
        return path


def output_files(outputs):
    """Fichiers produits (SaveImage, SaveVideo, VHS_VideoCombine...) ; les apercus 'temp' seulement a defaut."""
    found = []
    for node_id in sorted(outputs):
        for value in outputs[node_id].values():
            if isinstance(value, list):
                found += [it for it in value if isinstance(it, dict) and "filename" in it]
    saved = [it for it in found if it.get("type", "output") == "output"]
    return saved or found


def _multipart(fields, files):
    boundary = "----mayacomfy" + uuid.uuid4().hex
    b = boundary.encode()
    parts = []
    for key, value in fields.items():
        parts += [b"--" + b, ('Content-Disposition: form-data; name="%s"' % key).encode(), b"", str(value).encode()]
    for key, (filename, content, ctype) in files.items():
        parts += [b"--" + b,
                  ('Content-Disposition: form-data; name="%s"; filename="%s"' % (key, filename)).encode("utf-8"),
                  ("Content-Type: %s" % ctype).encode(), b"", content]
    parts += [b"--" + b + b"--", b""]
    return b"\r\n".join(parts), "multipart/form-data; boundary=" + boundary


def _explain_error(body):
    """Rend lisibles les erreurs de validation de /prompt (noeud manquant, fichier introuvable...)."""
    try:
        data = json.loads(body)
    except ValueError:
        return body[:2000]
    lines = []
    err = data.get("error")
    if isinstance(err, dict):
        lines.append(("%s %s" % (err.get("message", ""), err.get("details", ""))).strip())
    for node_id, info in (data.get("node_errors") or {}).items():
        for e in info.get("errors", []):
            lines.append("noeud %s (%s) : %s %s" % (node_id, info.get("class_type"), e.get("message"),
                                                    e.get("details", "")))
    return "\n".join(l for l in lines if l) or body[:2000]


def _status_message(status):
    for kind, data in status.get("messages", []):
        if kind == "execution_error":
            return "Erreur ComfyUI dans %s (noeud %s) :\n%s" % (data.get("node_type"), data.get("node_id"),
                                                               data.get("exception_message", "").strip())
        if kind == "execution_interrupted":
            return "Calcul interrompu"
    return "Erreur ComfyUI (%s)" % status.get("status_str")
