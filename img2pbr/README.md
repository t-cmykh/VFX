# img2pbr

Photo -> cartes PBR tileables : diffuse, height, normal (OpenGL), roughness, specular, AO.
Export **EXR ACEScg** (defaut) ou PNG, et **USD avec shader MaterialX** en option.

    pip install -r requirements.txt
    python img2pbr.py photo.jpg -o out/ --res 2k --usd        # ligne de commande
    python app.py                                             # interface locale http://127.0.0.1:8765

| Option | Effet |
|---|---|
| `--res 1k\|2k\|4k` | resolution carree d'export : 1024 / 2048 / 4096 px (prioritaire sur `--size`) |
| `--size N` | cote du carre de sortie (256-8192, defaut 1024) |
| `--crop-scale`, `--crop-offset x,y` | carre recadre dans la photo (zoom / position) |
| `--no-seamless`, `--seam-band` | desactive / regle le fondu de pavage |
| `--ai off\|midas`, `--ai-strength` | relief MiDaS (modele ONNX ~66 Mo telecharge dans `models/`) |
| `--format exr\|png` | `exr` (defaut) : diffuse en ACEScg, autres cartes brutes ; `png` : sRGB 8/16 bits |
| `--usd` / `--no-usd` | exporte aussi un `.usda` (ou `--usd-format usdc`) avec shader MaterialX branche ; `--usd-plane` ajoute un plan de test, `--disp-scale` regle le deplacement (m) |
| `--bit16` | height et normal en PNG 16 bits (format `png`) |
| `--flip-y` | normal DirectX |

Le programme affiche un score de couture (<= 1 : invisible) ; `--preview` ecrit un pavage 3x3.

## Export EXR ACEScg
- **diffuse** : sRGB -> lineaire -> **ACEScg** (AP1, Bradford D65->D60, `colorspace.py`, copie de celle d'`img2hdr`), half RGB,
  `colorInteropID = lin_ap1_scene` + chromaticites AP1 (meme en-tete qu'`img2hdr`).
- **height, normal, roughness, specular, ao** : donnees **brutes**, non converties (`oiio:ColorSpace = Raw`). Normal OpenGL
  (+Y), encode 0..1 ; 1 canal = `Y` ; height en float32, le reste en half.

## USD / MaterialX (`--usd`)
`Material` avec `outputs:mtlx:surface` / `outputs:mtlx:displacement` : `ND_standard_surface_surfaceshader` <- `ND_image_color3`
(diffuse, `acescg` ou `srgb_texture` en PNG), `ND_image_float` (roughness), `ND_normalmap_float` <- `ND_image_vector3` (normal),
et `height - 0.5` -> `ND_displacement_float`. Chemins des textures relatifs au USD : garder les fichiers dans le meme dossier.
AO et specular ne sont **pas branches** (standard_surface n'a pas d'entree d'occlusion ; son IOR 1.5 donne deja F0 ~ 4 %).
Les ids de noeuds suivent MaterialX 1.39 (`ND_normalmap_float`) : un hote en 1.38 attend `ND_normalmap`.
Verifie : noeuds/ports/types contre la librairie standard MaterialX 1.39 et connexions resolues par `pxr.Usd` (`test_export.py`).
**Non verifie** : le rendu dans un moteur MaterialX (Karma, Storm, Arnold).

## Interface locale
`python app.py` : choix 1K/2K/4K, format EXR/PNG, toggle USD (+ plan de test), seamless, relief IA ; apercus et ZIP.
Serveur standard library, 127.0.0.1 uniquement, rien ne quitte la machine.

## Tests
`python test_export.py` (ou pytest) : EXR ACEScg, resolution carree, USD MaterialX branche, PNG inchange.

## Limites connues
- Le reflet d'une photo est desature, pas seulement plus clair : le de-eclairage le reduit sans l'effacer.
- Le fondu de pavage melange deux copies decalees : les motifs tres structures peuvent se doubler.
- MiDaS est entraine pour des scenes, pas des surfaces planes : il apporte peu sur un sol (poids 0.3 par defaut).
- Roughness et specular sont des heuristiques, pas des mesures.
