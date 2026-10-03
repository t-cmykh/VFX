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

## Limites connues
- Le reflet d'une photo est desature, pas seulement plus clair : le de-eclairage le reduit sans l'effacer.
- Le fondu de pavage melange deux copies decalees : les motifs tres structures peuvent se doubler.
- MiDaS est entraine pour des scenes, pas des surfaces planes : il apporte peu sur un sol (poids 0.3 par defaut).
- Roughness et specular sont des heuristiques, pas des mesures.
