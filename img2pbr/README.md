# img2pbr

Photo -> cartes PBR tileables : diffuse, height, normal (OpenGL), roughness, specular, AO.

    pip install numpy opencv-python-headless onnxruntime
    python img2pbr.py photo.jpg -o out/ --size 2048 --preview

| Option | Effet |
|---|---|
| `--size N` | cote du carre de sortie (256-8192, defaut 1024) |
| `--crop-scale`, `--crop-offset x,y` | carre recadre dans la photo (zoom / position) |
| `--no-seamless`, `--seam-band` | desactive / regle le fondu de pavage |
| `--ai off\|midas`, `--ai-strength` | relief MiDaS (modele ONNX ~66 Mo telecharge dans `models/`) |
| `--bit16` | height et normal en PNG 16 bits |
| `--flip-y` | normal DirectX |

Le programme affiche un score de couture (<= 1 : invisible) ; `--preview` ecrit un pavage 3x3.

## Export : PNG ou EXR, et USD
| Option | Effet |
|---|---|
| `--format png` (defaut) | PNG ; la diffuse est en **Rec.709 (sRGB)** |
| `--format exr` | EXR 16 bits (`--exr-float32` : 32) ; la diffuse est en **ACEScg** (`lin_ap1_scene`) |
| `--usd [both\|mtlx\|preview]` | ecrit `<nom>_material.usda` : un Material branche sur les cartes |
| `--disp-scale` | amplitude du displacement dans l'USD (0.01 par defaut, unites de la scene) |

    pip install OpenEXR usd-core        # seulement si tu utilises --format exr / --usd
    python img2pbr.py photo.jpg -o out/ --size 2048 --format exr --usd

- **Seule la diffuse est une couleur** et change d'espace. `height`, `normal`, `roughness`, `specular`,
  `ao` sont des *donnees* : jamais converties, ecrites telles quelles (EXR : canal `Y`, ou `RGB` pour la
  normal, sans etiquette de primaires). La normal reste encodee [0,1], comme dans le PNG.
- **USD** : `mtlx` = MaterialX `standard_surface` (+ `normalmap`, `displacement`), `preview` =
  UsdPreviewSurface, `both` (defaut) met les deux sur le meme Material (le moteur prend celui qu'il
  comprend). Un plan 1x1 avec UV est ajoute pour voir le resultat dans usdview. Chemins de textures
  relatifs : garde le `.usda` dans le dossier des cartes. Le displacement vaut `(height - 0.5) * echelle`.
- Branchements : diffuse -> base color, roughness, normal (tangent space, OpenGL : incompatible avec
  `--flip-y`), height -> displacement ; `ao` seulement dans UsdPreviewSurface (standard_surface n'a pas
  d'entree AO). `specular` n'est pas branchee (carte constante, deja la valeur par defaut).
- **Verifie** : PNG inchange par rapport a avant ; EXR relus (valeurs = PNG a la quantification 8 bits
  pres, diffuse = conversion ACEScg attendue, etiquette ACEScg sur la diffuse seulement) ; USD relu avec
  `pxr`, textures resolues, noeuds/entrees/types MaterialX compares aux definitions officielles de la
  bibliotheque MaterialX 1.39. **Non verifie** : un vrai rendu (Houdini/Karma, Blender, usdview, Omniverse...)
  - je n'ai pas de moteur ici. Les noms d'espaces couleur (`srgb_texture`, `acescg`) doivent exister dans
  ta config OCIO. En EXR, le reseau UsdPreviewSurface lit la diffuse comme "raw" (valeurs ACEScg vues comme
  Rec.709) : preferer le reseau MaterialX avec des EXR.

### Test dans Blender
    pip install bpy                      # Python 3.11, ~300 Mo
    python blender_usd_test.py out/parquet_material.usda rendu.png --samples 64

Importe le `.usda`, l'applique a une sphere lisse et rend avec Cycles. Blender n'importe que le reseau
UsdPreviewSurface (pas le MaterialX), et ignore `ao` et le displacement : c'est un controle des cartes,
du sens de la normal et des espaces couleur, pas du reseau MaterialX. Avec `--format exr`, la diffuse
parait desaturee (ACEScg lue comme Rec.709 par le reseau de secours) : attendu, cf. plus haut.

## Fenetre
    python img2pbr_gui.py        # ou double-clic sur lancer_img2pbr.bat (Windows)

Choix de la photo, du dossier, de la taille (512 a 8192), du format (PNG Rec.709 / EXR ACEScg) et de l'export USD, reglages de recadrage / pavage / normal /
de-eclairage / relief IA, journal, et visualisation de chaque carte generee (ou de l'apercu 3x3).
Tkinter est fourni avec Python sur Windows (Linux : `apt install python3-tk`). Pour l'apercu :
`pip install pillow`. Teste sous Xvfb avec `--ai off` ; le relief MiDaS (telechargement) n'a pas ete
relance depuis la fenetre.

## Limites connues
- Le reflet d'une photo est desature, pas seulement plus clair : le de-eclairage le reduit sans l'effacer.
- Le fondu de pavage melange deux copies decalees : les motifs tres structures peuvent se doubler.
- MiDaS est entraine pour des scenes, pas des surfaces planes : il apporte peu sur un sol (poids 0.3 par defaut).
- Roughness et specular sont des heuristiques, pas des mesures.
