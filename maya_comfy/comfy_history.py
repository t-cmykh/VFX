"""Historique des generations : un fichier par resultat + un .json a cote (frame, seed, prompt, selection)."""
import glob
import json
import os
import shutil
import time

MEDIA_EXT = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".gif")


class History:
    def __init__(self, root):
        self.root = root

    @staticmethod
    def _meta_path(path):
        return os.path.splitext(path)[0] + ".json"

    def add(self, src, meta, move=True):
        os.makedirs(self.root, exist_ok=True)
        ext = os.path.splitext(src)[1].lower()
        base = "%s_f%04d" % (time.strftime("%Y%m%d_%H%M%S"), int(meta.get("frame", 0)))
        dst, i = os.path.join(self.root, base + ext), 1
        while os.path.exists(dst):
            dst, i = os.path.join(self.root, "%s_%d%s" % (base, i, ext)), i + 1
        (shutil.move if move else shutil.copy2)(src, dst)
        meta = dict(meta, file=os.path.basename(dst), created=time.time())
        meta.setdefault("selected", False)
        self._write(dst, meta)
        return dict(meta, path=dst)

    def _write(self, path, meta):
        meta = {k: v for k, v in meta.items() if k != "path"}
        with open(self._meta_path(path), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def entries(self):
        """Plus recentes en premier ; les .json orphelins ou illisibles sont ignores."""
        out = []
        for meta_path in glob.glob(os.path.join(self.root, "*.json")):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            path = os.path.join(self.root, meta.get("file", ""))
            if meta.get("file") and os.path.splitext(path)[1].lower() in MEDIA_EXT and os.path.exists(path):
                out.append(dict(meta, path=path))
        return sorted(out, key=lambda e: e.get("created", 0), reverse=True)

    def update(self, path, **changes):
        with open(self._meta_path(path), encoding="utf-8") as f:
            meta = json.load(f)
        meta.update(changes)
        self._write(path, meta)

    def selected(self):
        return sorted((e for e in self.entries() if e.get("selected")),
                      key=lambda e: (e.get("frame", 0), e.get("created", 0)))

    def remove(self, path):
        for p in (path, self._meta_path(path)):
            if os.path.exists(p):
                os.remove(p)
