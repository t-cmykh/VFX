#!/usr/bin/env python3
"""Construit l'application de bureau autonome (dist/img2pbr/…) avec PyInstaller.

    pip install -r requirements.txt pyinstaller
    python build_exe.py

PyInstaller ne cross-compile pas : lancer ce script sur Windows pour un .exe, sur macOS pour un .app
(le workflow GitHub `img2pbr-exe.yml` le fait pour les deux).
"""
import importlib.util
import subprocess
import sys

args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "img2pbr",
        "--hidden-import", "ai",                       # import paresseux (relief IA)
        "--collect-all", "pxr", "--collect-all", "OpenEXR"]
if importlib.util.find_spec("onnxruntime"):
    args += ["--collect-all", "onnxruntime"]
args.append("gui.py")
sys.exit(subprocess.call(args))
