# Tracking + layout du plan « voiture sur route de campagne » (drone)

Plan : 1280x720, 59.94 fps, 1226 images. Tracking **inversé** (de la dernière image vers la première).

## Livrables (`out/`)
- `shot_track_layout.blend` : caméra `trackCam` animée (1 clé/image), sol Z=0, voiture proxy animée, axe de route, nuage de points sol, plate en fond caméra.
- `trackCam.fbx`, `trackCam.abc`, `camera.chan` (frame tx ty tz rx ry rz vfov), `camera_track.json`.
- `playblast_layout_overlay.mp4` : contrôle (grille 20 m, route, voiture, points) sur la plate.

## Pipeline (`python3 -I script.py ...`)
1. `track_backward.py` : LK + PnP sur le plan du sol, de la dernière image vers la première (init : horizon à y≈205 px => pitch 10.5°).
2. `ba.py` : bundle adjustment (poses + focale + points sol), puis PnP image par image.
3. `repnp.py`, `fix_pos.py` : re-PnP dense, position lissée, rotation recalée.
4. `track_car.py` : suivi 2D de la voiture. 5. `build_scene.py` : scène bpy + exports. 6. `playblast.py` : contrôle.

## Résultats / limites
- Focale 851 px @1280 (HFOV 73.6°, ~24.4 mm sur capteur 36 mm), pas de distorsion modélisée.
- Erreur de reprojection : médiane ~0.7 px (max ~2.6 px sur quelques passages).
- Échelle : 0.14 m/unité, estimée sur la taille de la voiture (E46) => altitude ~14 m en fin de plan, ±15 %. Vitesse voiture ~35 km/h (cohérent).
- Sol supposé plan : les arbres/haies ne sont pas à Z=0 (le proxy du décor 3D reste à faire).
- La voiture passe sous un arbre vers la frame 1186 : sa position est extrapolée à vitesse constante ensuite.
