# Extracteur géométries OSM/IGN — version desktop

Même outil que la version navigateur (`../index.html`), mais lancé depuis
ton ordinateur au lieu de GitHub Pages. Raison d'être : un backend Python
local fait les requêtes réseau (Overpass, IGN, Lyon) à ta place — CORS est
une restriction propre au navigateur, un script qui fait sa propre requête
n'y est jamais soumis. Ça débloque des sources dont le serveur n'envoie pas
les en-têtes nécessaires (le photomaillage 2023 de la Métropole de Lyon,
`data.grandlyon.com`, en est l'exemple concret qui a motivé ce chantier).

Toute la génération de géométrie (toits, murs, terrain, export OBJ/GLB) reste
le code JS déjà testé de la version navigateur — rien n'a été réécrit,
seules les URLs de fetch réseau ont été redirigées vers ce backend local.

## Installer

```bash
cd desktop
pip install -r requirements.txt
```

## Lancer

```bash
python3 server.py
```

Puis ouvre **http://127.0.0.1:8756** dans ton navigateur.

## Ce qui change par rapport à la version navigateur

- **Plus de mur CORS** : le photomaillage Lyon 2023 (vrai mesh photogrammétrique
  texturé, Licence Ouverte 2.0 — donc légalement réutilisable, contrairement
  aux bâtiments Google) se charge maintenant correctement, case "Mesh réel"
  dans le panel. N'importe quelle autre source qui bloquerait pour la même
  raison (en-têtes CORS absents côté serveur) passe par le même mécanisme —
  ajouter un domaine à `ALLOWED_HOSTS` dans `server.py` suffit.
- Pas de PWA (manifest/service worker) — ça n'a pas de sens pour une page
  servie localement par ce serveur, retiré proprement plutôt que laissé
  à moitié fonctionnel.
- Le reste (dessin de zone par glisser-déposer, lien de zone partageable,
  Cesium Ion optionnel, bâtiments Google en aperçu) est identique.

## Sécurité du proxy local

`server.py` n'écoute que sur `127.0.0.1` (jamais `0.0.0.0`) — seul ce poste
peut le joindre. Le proxy `/api/proxy` et `/api/lyon/*` ne relaient que vers
une liste explicite de domaines (`ALLOWED_HOSTS`), jamais vers une URL
arbitraire — ce n'est pas un relais ouvert.

## Limites connues (pistes pour la suite)

- Le mesh Lyon est affiché en aperçu uniquement — pas encore inclus dans
  l'export `.glb`/`.OBJ` (extraire/découper un tileset 3D Tiles à la zone
  choisie est un chantier à part, non trivial).
- Le vrai gain "qualité Blosm" (hauteurs de bâtiments et terrain dérivés du
  nuage de points LIDAR HD IGN plutôt que d'estimations) n'est pas encore
  fait — possible maintenant qu'on est en Python (`laspy`, sans dépendance
  GDAL/PDAL système), mais c'est un chantier séparé.
