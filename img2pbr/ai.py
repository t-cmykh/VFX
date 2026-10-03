"""Estimation de relief par IA : MiDaS v2.1 small (ONNX, CPU).

MiDaS donne une profondeur relative a basse resolution (256x256). On l'applique par
tuiles recouvrantes sur un tore (indices modulo N) pour que le resultat reste tileable,
puis on ne garde que le detail (passe-haut) : la pente globale d'une photo de sol n'est
pas du relief.
"""
import urllib.request
from pathlib import Path

import cv2
import numpy as np

MODEL_URL = "https://github.com/isl-org/MiDaS/releases/download/v2_1/model-small.onnx"
DEFAULT_MODEL = Path(__file__).parent / "models" / "midas_v21_small.onnx"
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_IN = 256


def ensure_model(path=DEFAULT_MODEL, download=True):
    path = Path(path)
    if path.exists():
        return path
    if not download:
        raise FileNotFoundError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"telechargement du modele -> {path}")
    tmp = path.with_suffix(".part")
    urllib.request.urlretrieve(MODEL_URL, tmp)
    tmp.rename(path)
    return path


class Midas:
    def __init__(self, path=DEFAULT_MODEL):
        import onnxruntime as ort
        self.sess = ort.InferenceSession(str(ensure_model(path)),
                                         providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name

    def __call__(self, rgb):
        """rgb float32 HxWx3 [0,1] -> profondeur inverse relative, meme taille que l'entree."""
        h, w = rgb.shape[:2]
        x = cv2.resize(rgb, (_IN, _IN), interpolation=cv2.INTER_AREA)
        x = ((x - _MEAN) / _STD).transpose(2, 0, 1)[None].astype(np.float32)
        d = self.sess.run(None, {self.inp: x})[0][0]
        return cv2.resize(d, (w, h), interpolation=cv2.INTER_CUBIC)


def ai_detail(rgb, model, analysis=1024, tile=384, hp_sigma=40, blur_fn=None):
    """Detail de relief (moyenne 0, ecart-type ~ 1/2.5) tileable, a la resolution `analysis`.

    rgb : carre float32 [0,1]. blur_fn(img, sigma) : flou wrap-safe fourni par l'appelant.
    """
    n = analysis
    img = cv2.resize(rgb, (n, n), interpolation=cv2.INTER_AREA if rgb.shape[0] > n
                     else cv2.INTER_CUBIC)
    stride = tile // 2
    ramp = np.sin(np.pi * (np.arange(tile) + 0.5) / tile) ** 2
    win = np.outer(ramp, ramp).astype(np.float32)
    acc = np.zeros((n, n), np.float32)
    wsum = np.zeros((n, n), np.float32)
    for y0 in range(0, n, stride):
        rows = (y0 + np.arange(tile)) % n
        for x0 in range(0, n, stride):
            cols = (x0 + np.arange(tile)) % n
            ix = np.ix_(rows, cols)
            d = model(img[ix])
            d = (d - d.mean()) / (d.std() + 1e-6)
            acc[ix] += d * win
            wsum[ix] += win
    depth = acc / np.maximum(wsum, 1e-6)
    detail = depth - blur_fn(depth, hp_sigma)
    detail /= np.percentile(np.abs(detail), 99) + 1e-6
    return np.clip(detail, -1, 1).astype(np.float32)
