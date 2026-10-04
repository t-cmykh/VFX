"""Ecriture des cartes : PNG (Rec.709 / sRGB) ou EXR (ACEScg), selon la nature de la carte.

Seule la diffuse est une COULEUR : elle change d'espace colorimetrique.
height, normal, roughness, specular, ao sont des DONNEES : jamais converties, ecrites telles quelles
(en EXR, sans etiquette de primaires). La normal reste encodee [0,1] comme dans le PNG.
"""
import cv2
import numpy as np

import colorspace as cs

COLOR_MAPS = ("diffuse",)
FORMATS = ("png", "exr")


def save_png(path, img, bit16=False):
    """PNG. La diffuse est deja encodee sRGB (Rec.709) : rien a convertir."""
    img = np.clip(img, 0, 1)
    if img.ndim == 3:
        img = img[..., ::-1]
    if bit16:
        cv2.imwrite(str(path), (img * 65535 + 0.5).astype(np.uint16))
    else:
        cv2.imwrite(str(path), (img * 255 + 0.5).astype(np.uint8))


def save_exr(path, img, color, half=True):
    """EXR. `color` : diffuse sRGB encodee -> lineaire ACEScg (lin_ap1_scene). Sinon donnee brute :
    1 canal 'Y' (cartes scalaires) ou 'RGB' (normal), sans etiquette de primaires."""
    import OpenEXR

    if color:
        img = cs.srgb_to_acescg(cs.srgb_decode(np.clip(img, 0, 1)))
    data = np.ascontiguousarray(img, dtype=np.float16 if half else np.float32)
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    if color:
        header["colorInteropID"] = "lin_ap1_scene"
        header["chromaticities"] = OpenEXR.colorInteropIDToChromaticities("lin_ap1_scene")
    with OpenEXR.File(header, {"RGB" if data.ndim == 3 else "Y": data}) as f:
        f.write(str(path))


def write_maps(maps, out, stem, fmt="png", bit16=False, exr_half=True):
    """Ecrit toutes les cartes. Retourne {nom: Path}."""
    if fmt not in FORMATS:
        raise ValueError(f"format inconnu : {fmt} (png ou exr)")
    paths = {}
    for k, v in maps.items():
        p = out / f"{stem}_{k}.{fmt}"
        if fmt == "png":
            save_png(p, v, bit16 and k in ("height", "normal"))
        else:
            save_exr(p, v, k in COLOR_MAPS, exr_half)
        paths[k] = p
    return paths


def exr_to_display(path, color):
    """Lit un EXR ecrit par save_exr -> uint8 (H,W[,3]) pour l'affichage (diffuse : ACEScg -> sRGB)."""
    import OpenEXR

    with OpenEXR.File(str(path)) as f:
        img = np.asarray(next(iter(f.channels().values())).pixels, np.float32)
    if color:
        img = cs.acescg_to_srgb(img)
        img = np.where(img <= 0.0031308, img * 12.92,
                       1.055 * np.power(np.maximum(img, 1e-9), 1 / 2.4) - 0.055)
    return (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
