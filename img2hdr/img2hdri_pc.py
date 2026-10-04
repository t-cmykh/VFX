#!/usr/bin/env python3
"""img2hdri_pc : UNE image -> HDRI 360 complet (EXR ACEScg), en une commande, sur ton PC (GPU).

    python img2hdri_pc.py photo.jpg --res 4k
    python img2hdri_pc.py photo.jpg --res 2k --prompt "forest clearing at sunset" --hfov 60

Etapes : la photo est posee sur la sphere, le reste du 360 est invente par un modele d'inpainting
(pano_outpaint.py), la dynamique est etendue, puis export EXR 16 bits en ACEScg (lin_ap1_scene).

--res : 1k = 1024x512, 2k = 2048x1024, 4k = 4096x2048. La generation IA se fait a --gen-width
(1024 par defaut) et est agrandie ; seule la photo d'origine est a pleine resolution.
Rien n'est mesure : tout ce qui est hors de la photo est une invention plausible.
`--dry-run` : sans modele IA (extension lisse), pour tester sans GPU.
"""
import argparse
from argparse import Namespace
from pathlib import Path

import cv2
import numpy as np

import colorspace as cs
import hdr
import pano_outpaint as po
from img2hdr import env_from_pano, preview, write_exr

RESOLUTIONS = {"1k": 1024, "2k": 2048, "4k": 4096}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("-o", "--out", default="out")
    p.add_argument("--res", choices=list(RESOLUTIONS), default="2k",
                   help="resolution de l'EXR (largeur ; hauteur = /2)")
    g = p.add_argument_group("photo")
    g.add_argument("--hfov", type=float, default=69.0,
                   help="champ horizontal de la photo en degres (iPhone 1x ~ 69)")
    g.add_argument("--yaw", type=float, default=0.0, help="direction de la photo sur la sphere")
    g.add_argument("--pitch", type=float, default=0.0)
    g.add_argument("--roll", type=float, default=0.0)
    g = p.add_argument_group("generation IA")
    g.add_argument("--prompt", default=po.DEFAULT_PROMPT)
    g.add_argument("--negative", default=po.DEFAULT_NEG)
    g.add_argument("--model", default=po.DEFAULT_MODEL, help="depot Hugging Face d'inpainting")
    g.add_argument("--lora", default=None, help="LoRA panorama optionnel")
    g.add_argument("--lora-scale", type=float, default=0.8)
    g.add_argument("--gen-width", type=int, default=1024, help="largeur de generation (multiple de 64)")
    g.add_argument("--rings", type=int, default=4)
    g.add_argument("--steps", type=int, default=30)
    g.add_argument("--guidance", type=float, default=7.0)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--low-vram", action="store_true", help="offload CPU (GPU < 12 Go)")
    g.add_argument("--dry-run", action="store_true", help="aucun modele IA (extension lisse)")
    g = p.add_argument_group("dynamique")
    g.add_argument("--peak", type=float, default=40.0, help="gain des sources ecretees")
    g.add_argument("--shoulder", type=float, default=2.5)
    g.add_argument("--exposure", type=float, default=0.0, help="decalage global en stops")
    g.add_argument("--feather", type=float, default=4.0)
    p.add_argument("--float32", action="store_true", help="EXR 32 bits (defaut : 16 bits)")
    a = p.parse_args()

    width = RESOLUTIONS[a.res]
    if a.gen_width % 64:
        raise SystemExit("--gen-width doit etre un multiple de 64")
    gen_width = min(a.gen_width, width)
    bgr = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"image illisible : {a.image}")
    enc = bgr[..., ::-1].astype(np.float32) / 255.0

    pipe = None if a.dry_run else po.load_pipe(a.model, a.lora, a.lora_scale, a.low_vram)
    opts = Namespace(gen_width=gen_width, width=width, hfov=a.hfov, yaw=a.yaw, pitch=a.pitch,
                     roll=a.roll, rings=a.rings, prompt=a.prompt, negative=a.negative,
                     steps=a.steps, guidance=a.guidance, seed=a.seed)
    pano, _ = po.run(enc, opts, pipe)

    env, em, w = env_from_pano(enc, pano, a.hfov, width, a.yaw, a.pitch, a.roll, a.feather,
                               a.peak, a.shoulder, a.exposure)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(a.image).stem
    exr = out / f"{stem}_hdri_{a.res}_acescg.exr"
    write_exr(exr, cs.srgb_to_acescg(env), half=not a.float32)
    preview(env, out / f"{stem}_hdri_{a.res}_preview.jpg", exposure=-1.0)

    y = hdr.luma(env)
    print(f"{exr}  {env.shape[1]}x{env.shape[0]}  EXR {'32' if a.float32 else '16'} bits, "
          f"ACEScg (lin_ap1_scene)")
    print(f"photo d'origine : {w.mean() * 100:.1f} % de la sphere ; le reste est "
          f"{'une extension lisse (dry-run)' if a.dry_run else 'genere par IA'}")
    print(f"luminance max {y.max():.1f}")


if __name__ == "__main__":
    main()
