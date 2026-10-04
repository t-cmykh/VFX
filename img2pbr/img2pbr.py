#!/usr/bin/env python3
"""img2pbr v2 : une photo -> cartes PBR tileables (diffuse, height, normal, roughness,
specular, ao), en carre de resolution choisie (1k / 2k / 4k), avec relief IA optionnel.
Export EXR (diffuse en ACEScg, autres cartes brutes) ou PNG, et USD MaterialX en option.

Exemple : python img2pbr.py photo.jpg -o out/ --res 2k --usd
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

import export
import pbr
from seamless import make_seamless, seam_ratio


def save(path, img, bit16=False):
    img = np.clip(img, 0, 1)
    if img.ndim == 3:
        img = img[..., ::-1]
    if bit16:
        cv2.imwrite(str(path), (img * 65535 + 0.5).astype(np.uint16))
    else:
        cv2.imwrite(str(path), (img * 255 + 0.5).astype(np.uint8))


def parse_offset(s):
    try:
        x, y = (float(v) for v in s.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("attendu : x,y avec x et y entre 0 et 1, ex. 0.5,0.5")
    return x, y


RES = {"1k": 1024, "2k": 2048, "4k": 4096}


def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("-o", "--out", default="out")
    g = p.add_argument_group("resolution / recadrage")
    g.add_argument("--res", choices=list(RES), help="resolution carree d'export : 1k=1024, 2k=2048, 4k=4096 "
                                                    "(prioritaire sur --size)")
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
    g = p.add_argument_group("cartes")
    g.add_argument("--normal-strength", type=float, default=4.0)
    g.add_argument("--delight", type=float, default=0.9, help="0 = aucun, 1 = complet")
    g.add_argument("--delight-sigma", type=float, default=25, help="en px a 1024")
    g.add_argument("--roughness-base", type=float, default=0.6)
    g.add_argument("--flip-y", action="store_true", help="normal DirectX au lieu d'OpenGL")
    g.add_argument("--bit16", action="store_true", help="height et normal en PNG 16 bits (format png)")
    g.add_argument("--preview", action="store_true", help="ecrit un apercu en pavage 3x3")
    g = p.add_argument_group("export")
    g.add_argument("--format", choices=["exr", "png"], default="exr",
                   help="exr : diffuse en ACEScg (half), autres cartes brutes ; png : sRGB 8/16 bits (defaut exr)")
    g.add_argument("--usd", action=argparse.BooleanOptionalAction, default=False,
                   help="exporte aussi un USD dont le shader MaterialX est branche sur les textures")
    g.add_argument("--usd-format", choices=["usda", "usdc"], default="usda")
    g.add_argument("--usd-plane", action="store_true", help="ajoute un plan de test lie au materiau")
    g.add_argument("--disp-scale", type=float, default=0.01,
                   help="echelle du deplacement dans l'USD (unites USD = m, 0.01 = 1 cm)")
    return p


def run(argv=None, log=print):
    """Execute le pipeline. Retourne dict(files, maps, size, seam) ; leve SystemExit si entree invalide."""
    a = build_parser().parse_args(argv)
    if a.res:
        a.size = RES[a.res]
    if not 256 <= a.size <= 8192:
        raise SystemExit("--size doit etre entre 256 et 8192")
    bgr = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"image illisible : {a.image}")
    rgb = bgr[..., ::-1].astype(np.float32) / 255.0

    sq = pbr.to_square(rgb, a.size, a.crop_scale, a.crop_offset)
    if min(rgb.shape[:2]) * a.crop_scale < a.size:
        log(f"attention : la source ({int(min(rgb.shape[:2]) * a.crop_scale)} px) est plus "
            f"petite que {a.size} px : suréchantillonnage, pas de detail en plus")
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
    files = {}
    for k, v in maps.items():
        if a.format == "exr":
            files[k] = out / f"{stem}_{k}.exr"
            export.write_exr(files[k], k, v)
        else:
            files[k] = out / f"{stem}_{k}.png"
            save(files[k], v, a.bit16 and k in ("height", "normal"))
    if a.usd:
        files["usd"] = out / f"{stem}.{a.usd_format}"
        export.write_usd(files["usd"], stem, files, a.format == "exr", a.disp_scale, a.usd_plane)
    seam = (before, seam_ratio(maps["diffuse"]), seam_ratio(maps["normal"]))
    log(f"{a.size}x{a.size} -> {out}  ({', '.join(files)}), {a.format}"
        f"{' ACEScg' if a.format == 'exr' else ''}")
    log(f"couture (<= 1 invisible) : photo {seam[0]:.1f}  ->  diffuse {seam[1]:.1f}, normal {seam[2]:.1f}")

    if a.preview:
        d = np.clip(maps["diffuse"], 0, 1)
        tiled = np.tile(d, (3, 3, 1))
        save(out / f"{stem}_preview_tiles.jpg", cv2.resize(tiled, (1536, 1536),
                                                            interpolation=cv2.INTER_AREA))
    return dict(files=files, maps=maps, size=a.size, seam=seam, format=a.format)


def main():
    run()


if __name__ == "__main__":
    main()
