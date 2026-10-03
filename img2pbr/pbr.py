"""Derivation des cartes PBR. Tous les filtres sont wrap-safe : une entree tileable
donne des cartes tileables."""
import cv2
import numpy as np


# ---------- filtres wrap-safe ----------
def _wrap(img, p):
    return np.pad(img, [(p, p), (p, p)] + [(0, 0)] * (img.ndim - 2), mode="wrap")


def _crop(img, p):
    return img[p:-p, p:-p]


def blur(img, sigma):
    p = int(np.ceil(sigma * 3)) + 1
    return _crop(cv2.GaussianBlur(_wrap(img, p), (0, 0), sigma), p)


def morph(img, op, k):
    p = k + 1
    kernel = np.ones((k, k), np.uint8)
    return _crop(cv2.morphologyEx(_wrap(img, p), op, kernel), p)


def masked_blur(img, mask, sigma):
    """Flou normalise : seuls les pixels dont mask > 0 comptent."""
    m = mask[..., None] if img.ndim == 3 else mask
    return blur(img * m, sigma) / np.maximum(blur(mask, sigma)[..., None] if img.ndim == 3
                                             else blur(mask, sigma), 1e-4)


def luminance(rgb):
    return rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


# ---------- entree : carre de base ----------
def to_square(rgb, size, crop_scale=1.0, offset=(0.5, 0.5)):
    """Recadre un carre (fraction `crop_scale` du petit cote, position `offset`) puis
    le met a `size` x `size`."""
    h, w = rgb.shape[:2]
    c = max(8, int(round(min(h, w) * float(np.clip(crop_scale, 0.05, 1.0)))))
    x0 = int(round((w - c) * float(np.clip(offset[0], 0, 1))))
    y0 = int(round((h - c) * float(np.clip(offset[1], 0, 1))))
    sq = rgb[y0:y0 + c, x0:x0 + c]
    interp = cv2.INTER_AREA if c > size else cv2.INTER_CUBIC
    return np.clip(cv2.resize(sq, (size, size), interpolation=interp), 0, 1).astype(np.float32)


# ---------- cartes ----------
def gap_mask(lum):
    """Joints / zones tres sombres : 1 = joint."""
    otsu, _ = cv2.threshold((lum * 255).astype(np.uint8), 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thresh = min(otsu / 255.0 * 0.6, 0.22)
    return morph((lum < thresh).astype(np.float32), cv2.MORPH_OPEN, 3)


def delight(rgb, lum, valid, strength, sigma):
    illum = masked_blur(lum, valid, sigma)
    illum = illum / max(float(illum[valid > 0.5].mean()), 1e-4)
    return np.clip(rgb / (illum ** strength)[..., None], 0, 1), illum


def normal_from_height(h, strength, flip_y=False):
    p = 2
    hp = _wrap(h, p)
    gx = _crop(cv2.Scharr(hp, cv2.CV_32F, 1, 0), p) / 32.0
    gy = _crop(cv2.Scharr(hp, cv2.CV_32F, 0, 1), p) / 32.0
    if not flip_y:                       # OpenGL (Blender, USD) : +Y vers le haut de l'image
        gy = -gy
    # le gradient depend de la resolution : on normalise pour garder le meme rendu a 512 ou 4096
    scale = strength * h.shape[0] / 1024.0
    n = np.dstack([-gx * scale, -gy * scale, np.ones_like(h)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    return n * 0.5 + 0.5


def build_maps(rgb, a, ai_detail=None):
    """rgb : carre float32 [0,1] (deja seamless si voulu). ai_detail : relief IA [-1,1] ou None."""
    s = rgb.shape[0] / 1024.0            # les rayons en pixels sont definis pour 1024
    lum = luminance(rgb)
    gaps = gap_mask(lum)
    valid = 1.0 - morph(gaps, cv2.MORPH_DILATE, 5)

    albedo, illum = delight(rgb, lum, valid, a.delight, a.delight_sigma * s)
    alum = luminance(albedo)

    detail = alum - masked_blur(alum, valid, 6 * s)
    detail = detail / (np.percentile(np.abs(detail[valid > 0.5]), 99) + 1e-6)
    relief = 0.25 * np.clip(detail, -1, 1)
    if ai_detail is not None:
        ai = cv2.resize(ai_detail, rgb.shape[:2][::-1], interpolation=cv2.INTER_CUBIC)
        relief = (1 - a.ai_strength) * relief + a.ai_strength * 0.25 * ai
    height = 0.5 + relief
    gaps_soft = blur(gaps, 1.2 * s)
    height = blur((height * (1 - gaps_soft)).astype(np.float32), 0.8 * s)

    normal = normal_from_height(height, a.normal_strength, a.flip_y)

    shine = np.clip((illum - 1.0) / 0.4, 0, 1)
    rough = a.roughness_base - 0.12 * blur(shine, 15 * s)
    rough = rough + 0.075 * np.clip(-detail, 0, 1)
    rough = rough * (1 - gaps_soft) + 0.8 * gaps_soft
    rough = np.clip(blur(rough.astype(np.float32), 1.0 * s), 0.05, 1.0)

    spec = np.full_like(height, 0.5)     # F0 dielectrique ~4 %

    ao = 1.0 - np.clip((blur(height, 6 * s) - height) * 3.0, 0, 1)
    ao = np.clip(ao * (1 - 0.6 * gaps_soft), 0, 1).astype(np.float32)

    return dict(diffuse=albedo, height=height, normal=normal, roughness=rough,
                specular=spec, ao=ao)
