# maya_comfy

Panneau Maya qui pilote ComfyUI depuis la scene :

1. **Apercu image** : playblast de la frame courante (+ passe profondeur en option) -> workflow **Qwen-Image**.
2. **Historique** des images Qwen (vignettes, frame, seed, prompt), conserve dans le projet Maya :
   `<workspace>/comfy/<nom_de_scene>/`.
3. **Selection** : on coche les images a garder ; chacune est rattachee a sa frame Maya.
4. **Apercu anim** : playblast de la plage de la timeline -> workflow **LTX** avec les images cochees comme
   images cles, placees a leur frame (frame Maya - debut de plage).

## Installation

    # Maya : Script Editor, onglet Python (ou une etagere)
    import sys; sys.path.append(r"C:/chemin/vers/VFX/maya_comfy")
    import maya_comfy; maya_comfy.show()

- Maya 2022+ (PySide2) ou 2025+ (PySide6). Aucune dependance a installer dans Maya.
- **ffmpeg** dans le PATH (ou chemin complet dans `config.json`) : seulement si le workflow anim recoit une
  video de controle (`MAYA_VIDEO` / `MAYA_DEPTH_VIDEO`).
- ComfyUI + ComfyUI-GGUF (pour les modeles quantifies).

## ComfyUI sur une seule machine avec une RTX 2080 Ti (11 Go)

    python main.py --lowvram --reserve-vram 2.5

- `--reserve-vram` laisse de la place au Viewport 2.0 de Maya.
- Le panneau appelle `/free` quand on passe de Qwen a LTX (et inversement) : un seul gros modele en VRAM.
- Encodeurs de texte sur le CPU (`device: cpu` dans CLIPLoader), 64 Go de RAM conseilles.
- Qwen-Image : GGUF Q4 + LoRA Lightning 8 steps. LTX : resolution basse (512 par defaut) et 49 frames max
  (reglable dans le panneau). Une 2080 Ti est a la limite pour LTX-2 ; si ca ne passe pas, le meme
  panneau marche avec un workflow plus leger (LTX-Video 2B, Wan 2.1 VACE 1.3B) : seul le JSON change.

## Preparer les workflows

Les workflows se construisent dans ComfyUI puis s'exportent avec **Workflow > Export (API)** dans
`workflows/` (chemins dans `config.json`). Le panneau trouve les noeuds a piloter par leur **titre**
(clic droit sur le noeud > Title). Un titre peut contenir plusieurs jetons (`MAYA_SEED MAYA_DENOISE`) et
plusieurs noeuds peuvent porter le meme jeton.

| Jeton | Entree remplie | Valeur |
|---|---|---|
| `MAYA_PROMPT` / `MAYA_NEGATIVE` | `text` | prompts du panneau |
| `MAYA_SEED` | `seed` / `noise_seed` | seed |
| `MAYA_DENOISE` | `denoise` | liberte par rapport au playblast (image) |
| `MAYA_SIZE` | `width` + `height` | taille au ratio du rendu Maya |
| `MAYA_IMAGE` | `image` (LoadImage) | image : playblast ; anim : premiere image cochee |
| `MAYA_DEPTH` | `image` | passe profondeur (image) |
| `MAYA_KEY_1..n` | `image` (LoadImage) | images cochees, triees par frame |
| `MAYA_KEY_n_IDX` | `frame_idx` | position de l'image cle dans le clip |
| `MAYA_FRAMES` | `length` / `num_frames` | nombre de frames (8k+1) |
| `MAYA_FPS` | `frame_rate` / `fps` | fps de la scene |
| `MAYA_VIDEO` / `MAYA_DEPTH_VIDEO` | `video` / `file` | playblast de la plage (beauty / profondeur) en mp4 |

Un noeud `PrimitiveString` / `PrimitiveInt` / `PrimitiveFloat` titre avec un jeton marche aussi (entree `value`).

- `workflows/qwen_image_api.json` : exemple Qwen-Image img2img (GGUF + Lightning). **Noms de fichiers de
  modeles a adapter** a ce que tu as dans `ComfyUI/models/`.
- `workflows/ltx_anim_api.json` : a creer depuis le modele LTX image-to-video de ComfyUI. Titrer le
  chargement de la 1re image `MAYA_KEY_1` (ou `MAYA_IMAGE`), le prompt `MAYA_PROMPT`, le noeud de longueur
  `MAYA_FRAMES`, etc. Pour plusieurs images cles : dupliquer LoadImage + noeud de guide (`frame_idx`) en
  `MAYA_KEY_2` / `MAYA_KEY_2_IDX`... S'il y a plus d'images cochees que d'emplacements, on garde la
  premiere, la derniere et des intermediaires ; s'il y en a moins, la derniere est repetee.
- Plage anim : `playbackOptions` min -> max, ramenee a 8k+1 frames (vers le bas) et au max du panneau.
  Seules les images cochees dans cette plage sont utilisees.

## Passe profondeur

Remplace temporairement tous les materiaux par un `surfaceShader` (samplerInfo.pointCameraZ -> setRange,
proche = blanc), coupe la gestion des couleurs le temps du playblast, puis restaure tout. Les bornes
viennent de la boite englobante des meshes vue par la camera. **Experimental** : si le Viewport 2.0 ne la
rend pas correctement chez toi, laisser la case decochee et estimer la profondeur dans ComfyUI
(Depth Anything) a partir de `MAYA_IMAGE`.

## Tests

    python -m unittest tests.test_comfy

Testes ici (sans Maya ni GPU) : injection dans les workflows, historique, client HTTP contre un faux
ComfyUI, et le panneau complet sous PySide6 hors ecran avec un faux module `maya` (apercu image, selection,
apercu anim avec video ffmpeg, liberation VRAM). **Pas teste dans un vrai Maya ni avec de vrais modeles** :
playblast reel, passe profondeur et noms de noeuds/fichiers de modeles sont a verifier de ton cote.
