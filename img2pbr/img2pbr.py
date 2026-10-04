#!/usr/bin/env python3
"""img2pbr v2 : une photo -> cartes PBR tileables (diffuse, height, normal, roughness,
specular, ao), en carre de resolution choisie, avec relief IA optionnel.

Exemple : python img2pbr.py photo.jpg -o out/ --size 2048
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

import export
import pbr
import usd
from seamless import make_seamless, seam_ratio


def parse_offset(s):
    try:
        x, y = (float(v) for v in s.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("attendu : x,y avec x et y entre 0 et 1, ex. 0.5,0.5")
    return x, y


def generate(a):
    """Pipeline complet. `a` : Namespace (memes champs que la ligne de commande).
    Retourne (dossier, apercu ou None). Les messages passent par print()."""
    if not 256 <= a.size <= 8192:
        raise ValueError("size doit etre entre 256 et 8192")
    if a.usd:
        usd.check_available()
        if a.flip_y:
            raise ValueError("USD attend une normal OpenGL : decoche 'Normal DirectX' (--flip-y)")
    bgr = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"image illisible : {a.image}")
    rgb = bgr[..., ::-1].astype(np.float32) / 255.0

    sq = pbr.to_square(rgb, a.size, a.crop_scale, a.crop_offset)
    if min(rgb.shape[:2]) * a.crop_scale < a.size:
        print(f"attention : la source ({int(min(rgb.shape[:2]) * a.crop_scale)} px) est plus "
              f"petite que --size {a.size} : suréchantillonnage, pas de detail en plus")
    before = seam_ratio(sq)
    if not a.no_seamless:
        sq = make_seamless(sq, a.seam_band)

    detail = None
    if a.ai == "midas":
        import ai
        model = ai.Midas(a.model_path or ai.DEFAULT_MODEL)
        detail = ai.ai_detail(sq, model, blur_fn=pbr.blur)

    maps = pbr.build_maps(sq, a, detail)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(a.image).stem
    files = export.write_maps(maps, out, stem, a.format, a.bit16, not a.exr_float32)
    preview = None
    print(f"{a.size}x{a.size} -> {out}  ({', '.join(maps)})  "
          f"{'PNG Rec.709' if a.format == 'png' else 'EXR ACEScg'}")
    print(f"couture (<= 1 invisible) : photo {before:.1f}  ->  diffuse {seam_ratio(maps['diffuse']):.1f}"
          f", normal {seam_ratio(maps['normal']):.1f}")

    if a.preview:
        d = np.clip(maps["diffuse"], 0, 1)
        tiled = np.tile(d, (3, 3, 1))
        preview = out / f"{stem}_preview_tiles.jpg"
        export.save_png(preview, cv2.resize(tiled, (1536, 1536), interpolation=cv2.INTER_AREA))
    if a.usd:
        u = usd.write_usd(out / f"{stem}_material.usda", stem, files, a.format, a.usd, a.disp_scale)
        print(f"USD ({a.usd}) -> {u}")
    return out, preview



def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("-o", "--out", default="out")
    g = p.add_argument_group("resolution / recadrage")
    g.add_argument("--size", type=int, default=1024, help="cote du carre de sortie (256..8192)")
    g.add_argument("--crop-scale", type=float, default=1.0,
                   help="fraction du petit cote retenue (1 = carre maximal, 0.5 = zoom x2)")
    g.add_argument("--crop-offset", type=parse_offset, default=(0.5, 0.5),
                   help="position du carre dans l'image, x,y entre 0 et 1")
    g = p.add_argument_group("seamless")
    g.add_argument("--no-seamless", action="store_true")
    g.add_argument("--seam-band", type=float, default=0.35,
                   help="largeur de la zone de fondu (0.05..0.5) ; plus grand = couture plus douce "
                        "mais plus de flou")
    g = p.add_argument_group("IA")
    g.add_argument("--ai", choices=["off", "midas"], default="midas",
                   help="relief par MiDaS (telecharge ~66 Mo au 1er usage)")
    g.add_argument("--ai-strength", type=float, default=0.3, help="part du relief IA (0..1)")
    g.add_argument("--model-path", default=None)
    g = p.add_argument_group("export")
    g.add_argument("--format", choices=export.FORMATS, default="png",
                   help="png : diffuse Rec.709 (sRGB) | exr : diffuse ACEScg ; les autres cartes "
                        "sont des donnees et ne sont jamais converties")
    g.add_argument("--exr-float32", action="store_true", help="EXR 32 bits (defaut : 16 bits)")
    g.add_argument("--usd", nargs="?", const="both", choices=usd.MODES, default=None,
                   help="ecrit <nom>_material.usda branche sur les cartes : mtlx (MaterialX "
                        "standard_surface), preview (UsdPreviewSurface) ou both (defaut si sans valeur)")
    g.add_argument("--disp-scale", type=float, default=0.01,
                   help="amplitude du displacement dans l'USD, en unites de la scene")
    g = p.add_argument_group("cartes")
    g.add_argument("--normal-strength", type=float, default=4.0)
    g.add_argument("--delight", type=float, default=0.9, help="0 = aucun, 1 = complet")
    g.add_argument("--delight-sigma", type=float, default=25, help="en px a 1024")
    g.add_argument("--roughness-base", type=float, default=0.6)
    g.add_argument("--flip-y", action="store_true", help="normal DirectX au lieu d'OpenGL")
    g.add_argument("--bit16", action="store_true", help="height et normal en PNG 16 bits")
    g.add_argument("--preview", action="store_true", help="ecrit un apercu en pavage 3x3")
    a = p.parse_args()

    try:
        generate(a)
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
