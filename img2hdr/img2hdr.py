#!/usr/bin/env python3
"""img2hdr : photo JPG/PNG -> pseudo-HDRI equirectangulaire EXR en ACEScg (pour eclairer une scene 3D).

ATTENTION : c'est une reconstruction plausible, pas une mesure. Les sources ecretees sont
re-eclairees par un gain regle (--peak) et tout ce qui est hors du champ de la photo est invente.

Exemple : python img2hdr.py studio.jpg -o out/ --hfov 69 --width 4096
"""
import argparse
from pathlib import Path

import cv2
import numpy as np
import OpenEXR

import colorspace as cs
import hdr


def write_exr(path, rgb_acescg, half=True):
    data = rgb_acescg.astype(np.float16 if half else np.float32)
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage,
              "envmap": OpenEXR.ENVMAP_LATLONG,
              "colorInteropID": "lin_ap1_scene",
              "chromaticities": OpenEXR.colorInteropIDToChromaticities("lin_ap1_scene")}
    with OpenEXR.File(header, {"RGB": data}) as f:
        f.write(str(path))


def env_from_pano(enc, pano_enc, hfov, width, yaw, pitch, roll, feather, peak, shoulder, exposure):
    """Panorama 360 complet (sRGB encode, 2:1) -> env lineaire Rec.709 a `width`, dynamique etendue.
    Retourne (env, masque des sources, couverture de la photo d'origine)."""
    penc = cv2.resize(pano_enc, (width, width // 2),
                      interpolation=cv2.INTER_AREA if pano_enc.shape[1] > width else cv2.INTER_CUBIC)
    env, em = hdr.expand_highlights(penc, cs.srgb_decode(penc), peak, shoulder, exposure)
    _, w = hdr.project(enc, hfov, width, yaw, pitch, roll, feather)
    return env, em, w


def preview(env_rec709, path, exposure=0.0):
    """Apercu LDR : exposition + Reinhard + sRGB. Uniquement pour regarder le resultat."""
    x = env_rec709 * 2.0 ** exposure
    x = x / (1 + x)
    x = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(np.maximum(x, 1e-9), 1 / 2.4) - 0.055)
    cv2.imwrite(str(path), (np.clip(x, 0, 1)[..., ::-1] * 255 + 0.5).astype(np.uint8))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("-o", "--out", default="out")
    g = p.add_argument_group("projection")
    g.add_argument("--hfov", type=float, default=69.0,
                   help="champ horizontal de la photo en degres (iPhone 1x ~ 69)")
    g.add_argument("--width", type=int, default=4096, help="largeur de l'equirect (hauteur = /2)")
    g.add_argument("--yaw", type=float, default=0.0, help="direction de la photo sur la sphere")
    g.add_argument("--pitch", type=float, default=0.0)
    g.add_argument("--roll", type=float, default=0.0)
    g.add_argument("--feather", type=float, default=4.0, help="fondu photo -> extension (degres)")
    g.add_argument("--fill", type=float, default=0.7,
                   help="gain de l'extension hors photo (0 = noir)")
    g.add_argument("--pano", default=None,
                   help="panorama 360 deja complete (sortie de pano_outpaint.py) : remplace "
                        "l'extension automatique ; l'expansion de dynamique s'applique a tout")
    g = p.add_argument_group("dynamique")
    g.add_argument("--peak", type=float, default=40.0,
                   help="gain des sources ecretees (1 = aucune expansion)")
    g.add_argument("--shoulder", type=float, default=2.5, help="gain max des hautes lumieres non ecretees")
    g.add_argument("--exposure", type=float, default=0.0, help="decalage global en stops")
    p.add_argument("--float32", action="store_true", help="EXR 32 bits (defaut : 16 bits)")
    a = p.parse_args()

    if a.width < 256 or a.width % 2:
        raise SystemExit("--width doit etre pair et >= 256")
    bgr = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"image illisible : {a.image}")
    enc = bgr[..., ::-1].astype(np.float32) / 255.0
    lin = cs.srgb_decode(enc)

    if a.pano:
        pbgr = cv2.imread(a.pano, cv2.IMREAD_COLOR)
        if pbgr is None:
            raise SystemExit(f"panorama illisible : {a.pano}")
        if pbgr.shape[1] != 2 * pbgr.shape[0]:
            raise SystemExit("le panorama doit etre equirectangulaire 2:1")
        env, em, w = env_from_pano(enc, pbgr[..., ::-1].astype(np.float32) / 255.0, a.hfov,
                                   a.width, a.yaw, a.pitch, a.roll, a.feather, a.peak,
                                   a.shoulder, a.exposure)
    else:
        hdr_lin, em = hdr.expand_highlights(enc, lin, a.peak, a.shoulder, a.exposure)
        ldr_lin = lin * 2.0 ** a.exposure
        env, w = hdr.build_env(ldr_lin, hdr_lin, a.hfov, a.width, a.yaw, a.pitch, a.roll,
                               a.fill, a.feather)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(a.image).stem
    exr = out / f"{stem}_hdri_acescg.exr"
    write_exr(exr, cs.srgb_to_acescg(env), half=not a.float32)
    preview(env, out / f"{stem}_hdri_preview.jpg", exposure=-1.0)

    y = hdr.luma(env)
    pos = y[y > 1e-4]
    print(f"{exr}  {env.shape[1]}x{env.shape[0]}  EXR {'32' if a.float32 else '16'} bits, "
          f"ACEScg (lin_ap1_scene)")
    print(f"sources ecretees detectees : {(em > 0.5).mean() * 100:.2f} % des pixels "
          f"{'du panorama' if a.pano else 'de la photo'}")
    print(f"couverture photo : {w.mean() * 100:.1f} % de la sphere ; le reste "
          f"{'vient du panorama fourni (genere)' if a.pano else 'est extrapole'}")
    print(f"luminance : mediane {np.median(pos):.3f}  max {y.max():.1f}  "
          f"plage ~ {np.log2(y.max() / np.percentile(pos, 1)):.1f} stops")
    print("principales sources (part de l'energie, azimut, elevation, pic) :")
    for share, az, el, pk in hdr.find_lights(env):
        print(f"  {share * 100:5.1f} %   az {az:7.1f}°   el {el:6.1f}°   pic {pk:.1f}")


if __name__ == "__main__":
    main()
