# img2hdr

Photo JPG/PNG -> **pseudo-HDRI** equirectangulaire (2:1) en **EXR ACEScg**, pour eclairer une scene 3D.

    pip install numpy opencv-python-headless OpenEXR     # + bpy pour blender_test.py
    python img2hdr.py studio.jpg -o out/ --hfov 69 --width 4096
    python blender_test.py out/studio_hdri_acescg.exr rendu.png   # rendu de controle (Cycles, CPU)

## Ce que fait l'outil
1. **Decodage sRGB -> lineaire**, puis conversion **Rec.709 -> ACEScg (AP1)** (matrice calculee depuis les
   primaires, Bradford D65->D60, verifiee contre les valeurs publiees ; `test_colorspace.py`).
2. **Expansion des hautes lumieres** : les noyaux ecretes (3 canaux ~ 255) de taille raisonnable sont
   traites comme des sources et multiplies par `--peak` ; les autres hautes lumieres montent jusqu'a
   `--shoulder`. `--exposure` decale le tout en stops.
3. **Projection** de la photo (vue rectiligne, champ `--hfov`) sur la sphere (`--yaw/--pitch/--roll`).
4. **Extension hors photo** : continuite lisse pres de la photo, puis moyenne par latitude (plafond en
   haut, sol en bas), le tout x `--fill`.
5. **EXR** 16 bits (ou `--float32`), `envmap=latlong`, `colorInteropID=lin_ap1_scene` + `chromaticities` AP1.

## Verifications faites
- Blender (OCIO, espace `ACEScg`) relit l'EXR et retombe sur ma conversion a ~1e-8 pres.
- Rendu Cycles : scene d'essai eclairee par la HDRI vs la meme photo sans expansion
  (`renders/compare_ldr_vs_hdr.jpg`).

## Limites (a lire avant de s'en servir)
- **Ce n'est pas une mesure.** L'information ecretee n'existe pas dans un JPG : `--peak` est un gain regle
  a la main, pas une intensite physique. Echelle absolue arbitraire (regler `--exposure`).
- **Une photo couvre ~5 % de la sphere** (69 deg de large). Le reste est extrapole en lumiere quasi
  uniforme : peu d'ombres directionnelles, aucune lampe hors champ. Pour un eclairage fidele :
  panorama 360 + bracketing, ou RAW.
- `--hfov` est suppose (69 deg ~ iPhone 1x). Mauvaise valeur = reflets deformes.
- La detection de sources est une heuristique (pixels blancs ecretes) : un reflet speculaire blanc sur
  une surface peut etre pris pour une lampe.
- L'orientation dans Blender depend de son mapping equirect ; ajuster avec `--yaw` ou la rotation du monde.
