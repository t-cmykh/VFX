"""Assemble web/src/ en un seul fichier autonome : earth_importer/EarthImporter.html

    python web/build.py

Les commentaires <!-- @inline-css chemin --> et <!-- @inline-js chemin --> de src/index.html
sont remplaces par le contenu du fichier (chemin relatif a src/).
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
OUT = SRC.parent.parent / "EarthImporter.html"


def inline(match):
    kind, rel = match.group(1), match.group(2)
    text = (SRC / rel).read_text(encoding="utf-8")
    if kind == "js":
        text = text.replace("</script", "<\\/script")
        return f"<script>\n{text}\n</script>"
    return f"<style>\n{text}\n</style>"


def build():
    html = (SRC / "index.html").read_text(encoding="utf-8")
    html = re.sub(r"<!-- @inline-(css|js) (\S+) -->", inline, html)
    OUT.write_text(html, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    out = build()
    print(f"{out} ({out.stat().st_size / 1024:.0f} Ko)")
