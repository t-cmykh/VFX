#!/usr/bin/env python3
"""pano_outpaint : une photo -> panorama equirectangulaire 360 COMPLET, parties manquantes inventees
par un modele d'inpainting (diffusers). A lancer sur une machine avec GPU.

    pip install -r requirements-gpu.txt
    python pano_outpaint.py studio.jpg -o pano/ --hfov 69 \
        --prompt "interior of a large film studio soundstage, ..."
    python img2hdr.py studio.jpg -o out/ --pano pano/studio_pano.png     # -> EXR ACEScg

Principe : la photo est posee sur la sphere ; le reste est genere par anneaux successifs autour
de la zone connue (chaque anneau voit le precedent), puis une passe decalee de 180 deg corrige la
couture gauche/droite. La generation se fait en basse resolution (--gen-width, 1024 par defaut) :
suffisant pour de l'eclairage ; la photo originale est recollee a pleine resolution a la fin.

Rien n'est mesure : tout ce qui est hors de la photo est une invention plausible du modele.
`--dry-run` ne charge aucun modele (geometrie et masques seulement).
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

import hdr

DEFAULT_MODEL = "diffusers/stable-diffusion-xl-1.0-inpainting-0.1"
DEFAULT_PROMPT = ("equirectangular 360 degree panorama, interior of a large film studio soundstage, "
                  "dark walls, high ceiling with rows of LED light panels, blue screen, wooden floor, "
                  "photorealistic, seamless")
DEFAULT_NEG = "text, watermark, logo, people, blurry, low quality, frame, border, black bars"


# ---------- geometrie (testable sans modele) ----------
def build_canvas(photo_enc, hfov, width, yaw=0.0, pitch=0.0, roll=0.0, known_thresh=0.9):
    """Photo (sRGB encode, [0,1]) posee sur la sphere. Retourne (canvas, w, known bool)."""
    canvas, w = hdr.project(photo_enc, hfov, width, yaw, pitch, roll, feather_deg=2.0)
    return canvas, w, w > known_thresh


def wrapped_distance(unknown):
    """Distance (px) a la zone connue pour chaque pixel inconnu, en tenant compte du wrap en X."""
    h, w = unknown.shape
    pad = w // 2
    padded = np.pad(unknown.astype(np.uint8), ((0, 0), (pad, pad)), mode="wrap")
    d = cv2.distanceTransform(padded, cv2.DIST_L2, 5)
    return d[:, pad:pad + w]


def ring_masks(known, n_rings, overlap_px):
    """Decoupe la zone inconnue en `n_rings` anneaux d'aire egale, du plus proche au plus loin.
    Chaque masque deborde de `overlap_px` sur la zone deja connue (evite les coutures)."""
    unknown = ~known
    dist = wrapped_distance(unknown)
    qs = np.quantile(dist[unknown], np.linspace(0, 1, n_rings + 1))
    qs[0] = 0.0
    masks = []
    for k in range(n_rings):
        ring = unknown & (dist > qs[k]) & (dist <= qs[k + 1] + (1e-3 if k == n_rings - 1 else 0))
        m = ring.astype(np.uint8)
        if overlap_px > 0:
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                       (2 * overlap_px + 1, 2 * overlap_px + 1)))
        masks.append(m.astype(bool))
    return masks


def seam_mask(shape, frac=0.2):
    """Bande verticale centree (apres decalage de 180 deg : l'ancienne couture gauche/droite)."""
    h, w = shape
    m = np.zeros((h, w), bool)
    half = int(w * frac / 2)
    m[:, w // 2 - half:w // 2 + half] = True
    return m


# ---------- modele ----------
def load_pipe(model, lora, lora_scale, low_vram):
    import torch
    from diffusers import AutoPipelineForInpainting

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        print("ATTENTION : pas de GPU detecte, ce sera tres lent.")
    dtype = torch.float16 if device == "cuda" else torch.float32
    try:
        pipe = AutoPipelineForInpainting.from_pretrained(model, torch_dtype=dtype, variant="fp16")
    except (OSError, ValueError):
        pipe = AutoPipelineForInpainting.from_pretrained(model, torch_dtype=dtype)
    if lora:
        pipe.load_lora_weights(lora)
        pipe.fuse_lora(lora_scale=lora_scale)
    if low_vram and device == "cuda":
        pipe.enable_model_cpu_offload()
    else:
        pipe.to(device)
    return pipe


def inpaint(pipe, img_u8, mask, a, seed):
    """img_u8 : HxWx3 uint8 RGB a la resolution de generation ; mask bool (True = a generer)."""
    import torch
    from PIL import Image

    h, w = mask.shape
    gen = torch.Generator(device="cpu").manual_seed(seed)
    out = pipe(prompt=a.prompt, negative_prompt=a.negative, image=Image.fromarray(img_u8),
               mask_image=Image.fromarray((mask * 255).astype(np.uint8)), height=h, width=w,
               num_inference_steps=a.steps, guidance_scale=a.guidance, strength=0.999,
               generator=gen).images[0]
    return np.asarray(out.convert("RGB"))


# ---------- pipeline ----------
def run(photo_enc, a, pipe=None):
    gw = a.gen_width
    canvas, w_full, known_full = build_canvas(photo_enc, a.hfov, a.width, a.yaw, a.pitch, a.roll)

    # etat de generation en basse resolution
    gh = gw // 2
    cur = cv2.resize(canvas, (gw, gh), interpolation=cv2.INTER_AREA)
    known = cv2.resize(known_full.astype(np.uint8), (gw, gh), interpolation=cv2.INTER_AREA) >= 1
    # initialisation de l'inconnu par une extension lisse (le modele ne voit jamais du noir)
    cur = np.where(known[..., None], cur,
                   hdr.push_pull(cur, known.astype(np.float32))).astype(np.float32)

    if pipe is None:
        print("dry-run : aucun modele charge, l'inconnu reste une extension lisse")
    else:
        overlap = max(4, gw // 64)
        for i, m in enumerate(ring_masks(known, a.rings, overlap)):
            print(f"anneau {i + 1}/{a.rings} : {m.mean() * 100:.0f} % de la sphere")
            u8 = (np.clip(cur, 0, 1) * 255 + 0.5).astype(np.uint8)
            gen = inpaint(pipe, u8, m, a, a.seed + i).astype(np.float32) / 255
            cur = np.where(m[..., None], gen, cur)
        # couture gauche/droite : on decale de 180 deg et on regenere une bande centrale
        print("passe couture")
        cur = np.roll(cur, gw // 2, axis=1)
        sm = seam_mask((gh, gw))
        u8 = (np.clip(cur, 0, 1) * 255 + 0.5).astype(np.uint8)
        gen = inpaint(pipe, u8, sm, a, a.seed + 100).astype(np.float32) / 255
        cur = np.roll(np.where(sm[..., None], gen, cur), -gw // 2, axis=1)

    up = cv2.resize(cur, (a.width, a.width // 2), interpolation=cv2.INTER_CUBIC)
    # la photo originale (pleine resolution) est recollee, avec son fondu
    out = w_full[..., None] * canvas + (1 - w_full[..., None]) * up
    return np.clip(out, 0, 1).astype(np.float32), w_full


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("-o", "--out", default="pano")
    p.add_argument("--hfov", type=float, default=69.0)
    p.add_argument("--yaw", type=float, default=0.0)
    p.add_argument("--pitch", type=float, default=0.0)
    p.add_argument("--roll", type=float, default=0.0)
    p.add_argument("--width", type=int, default=4096, help="largeur finale (hauteur = /2)")
    p.add_argument("--gen-width", type=int, default=1024,
                   help="largeur de generation (multiple de 64 ; hauteur = /2)")
    p.add_argument("--rings", type=int, default=4, help="nombre d'anneaux d'outpainting")
    p.add_argument("--model", default=DEFAULT_MODEL, help="depot Hugging Face d'un modele d'inpainting")
    p.add_argument("--lora", default=None, help="LoRA panorama optionnel (depot HF ou chemin)")
    p.add_argument("--lora-scale", type=float, default=0.8)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--negative", default=DEFAULT_NEG)
    p.add_argument("--steps", type=int, default=30)
    p.add_argument("--guidance", type=float, default=7.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--low-vram", action="store_true", help="offload CPU (GPU < 12 Go)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    if a.gen_width % 64 or a.width % 2 or a.width < a.gen_width:
        raise SystemExit("--gen-width multiple de 64, --width pair et >= --gen-width")
    bgr = cv2.imread(a.image, cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"image illisible : {a.image}")
    enc = bgr[..., ::-1].astype(np.float32) / 255.0

    pipe = None if a.dry_run else load_pipe(a.model, a.lora, a.lora_scale, a.low_vram)
    pano, w = run(enc, a, pipe)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(a.image).stem
    cv2.imwrite(str(out / f"{stem}_pano.png"), (pano[..., ::-1] * 255 + 0.5).astype(np.uint8))
    cv2.imwrite(str(out / f"{stem}_pano_known.png"), (w * 255).astype(np.uint8))
    print(f"-> {out / (stem + '_pano.png')}  ({a.width}x{a.width // 2})")


if __name__ == "__main__":
    main()
