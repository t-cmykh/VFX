#!/usr/bin/env python3
"""img2pbr v1 : une photo -> cartes PBR (heuristiques, sans IA).

Sorties : diffuse, height, normal, roughness, specular, ao (PNG).
Usage   : python img2pbr.py photo.jpg -o out/ [--normal-strength 4] [--delight 0.8]
"""
import argparse
from pathlib import Path

import cv2
import numpy as np


def luminance(rgb):
    return rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def gap_mask(lum, thresh=None):
    """Masque des zones tres sombres (joints, trous) : 1 = joint."""
    if thresh is None:
        # seuil auto : Otsu sur l'image 8 bits, borne pour ne pas manger le bois sombre
        otsu, _ = cv2.threshold((lum * 255).astype(np.uint8), 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        thresh = min(otsu / 255.0 * 0.6, 0.22)
    m = (lum < thresh).astype(np.float32)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return m


def masked_blur(img, mask, sigma):
    """Flou gaussien normalise : ignore les pixels hors masque (mask = poids)."""
    num = cv2.GaussianBlur(img * mask[..., None] if img.ndim == 3 else img * mask, (0, 0), sigma)
    den = cv2.GaussianBlur(mask, (0, 0), sigma)
    if img.ndim == 3:
        den = den[..., None]
    return num / np.maximum(den, 1e-4)


def delight(rgb, lum, valid, strength, sigma):
    """Retire l'eclairage basse frequence (reflets, degrades) estime hors joints."""
    illum = masked_blur(lum, valid, sigma)
    illum = illum / max(float(illum[valid > 0.5].mean()), 1e-4)
    gain = illum ** strength
    return np.clip(rgb / gain[..., None], 0, 1), illum


def normal_from_height(h, strength, flip_y=False):
    gx = cv2.Scharr(h, cv2.CV_32F, 1, 0) / 32.0
    gy = cv2.Scharr(h, cv2.CV_32F, 0, 1) / 32.0
    if not flip_y:      # convention OpenGL (Blender) : +Y vers le haut de l'image
        gy = -gy
    n = np.dstack([-gx * strength, -gy * strength, np.ones_like(h)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    return n * 0.5 + 0.5


def process(rgb, a):
    lum = luminance(rgb)
    gaps = gap_mask(lum)
    valid = 1.0 - cv2.dilate(gaps, np.ones((5, 5), np.uint8))

    albedo, illum = delight(rgb, lum, valid, a.delight, a.delight_sigma)
    alum = luminance(albedo)

    # Height : detail du grain (high-pass) + joints creuses + cassures de planches
    detail = alum - masked_blur(alum, valid, 6)
    detail = detail / (np.percentile(np.abs(detail[valid > 0.5]), 99) + 1e-6)
    height = 0.5 + 0.25 * np.clip(detail, -1, 1)
    gaps_soft = cv2.GaussianBlur(gaps, (0, 0), 1.2)
    height = height * (1 - gaps_soft) + 0.0 * gaps_soft          # joints = creux
    height = cv2.GaussianBlur(height, (0, 0), 0.8).astype(np.float32)

    normal = normal_from_height(height, a.normal_strength, a.flip_y)

    # Roughness : bois mat par defaut ; zone luisante (illum haute) = plus lisse ; joints = plus rugueux
    shine = np.clip((illum - 1.0) / 0.4, 0, 1)
    rough = a.roughness_base - 0.12 * cv2.GaussianBlur(shine, (0, 0), 15)
    rough = rough + 0.15 * np.clip(-detail, 0, 1) * 0.5           # creux du grain un peu plus rugueux
    rough = rough * (1 - gaps_soft) + 0.8 * gaps_soft
    rough = np.clip(cv2.GaussianBlur(rough.astype(np.float32), (0, 0), 1.0), 0.05, 1.0)

    # Specular : F0 dielectrique ~4 % -> 0.5 dans la convention Principled ; joints plastique pareil
    spec = np.full_like(height, 0.5)

    # AO : cavites = height local < height flou large
    ao = 1.0 - np.clip((cv2.GaussianBlur(height, (0, 0), 6) - height) * 3.0, 0, 1)
    ao = np.clip(ao * (1 - 0.6 * gaps_soft) + 0.0, 0, 1).astype(np.float32)

    return dict(diffuse=albedo, height=height, normal=normal, roughness=rough,
                specular=spec, ao=ao, _gaps=gaps, _illum=illum)


def save(path, img):
    img = np.clip(img, 0, 1)
    if img.ndim == 3:
        img = img[..., ::-1]                                       # RGB -> BGR pour OpenCV
    cv2.imwrite(str(path), (img * 255 + 0.5).astype(np.uint8))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("image")
    p.add_argument("-o", "--out", default="out")
    p.add_argument("--normal-strength", type=float, default=4.0)
    p.add_argument("--delight", type=float, default=0.9, help="0 = aucun, 1 = complet")
    p.add_argument("--delight-sigma", type=float, default=25)
    p.add_argument("--roughness-base", type=float, default=0.6)
    p.add_argument("--flip-y", action="store_true", help="normal DirectX au lieu d'OpenGL")
    a = p.parse_args()

    bgr = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"image illisible : {a.image}")
    rgb = bgr[..., ::-1].astype(np.float32) / 255.0
    maps = process(rgb, a)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(a.image).stem
    for k, v in maps.items():
        if not k.startswith("_"):
            save(out / f"{stem}_{k}.png", v)
    print("ecrit :", ", ".join(k for k in maps if not k.startswith("_")), "->", out)


if __name__ == "__main__":
    main()
