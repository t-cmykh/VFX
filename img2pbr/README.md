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

## Fenetre
    python img2pbr_gui.py        # ou double-clic sur lancer_img2pbr.bat (Windows)

Choix de la photo, du dossier et de la taille (512 a 8192), reglages de recadrage / pavage / normal /
de-eclairage / relief IA, journal, et visualisation de chaque carte generee (ou de l'apercu 3x3).
Tkinter est fourni avec Python sur Windows (Linux : `apt install python3-tk`). Pour l'apercu :
`pip install pillow`. Teste sous Xvfb avec `--ai off` ; le relief MiDaS (telechargement) n'a pas ete
relance depuis la fenetre.

## Limites connues
- Le reflet d'une photo est desature, pas seulement plus clair : le de-eclairage le reduit sans l'effacer.
- Le fondu de pavage melange deux copies decalees : les motifs tres structures peuvent se doubler.
- MiDaS est entraine pour des scenes, pas des surfaces planes : il apporte peu sur un sol (poids 0.3 par defaut).
- Roughness et specular sont des heuristiques, pas des mesures.
