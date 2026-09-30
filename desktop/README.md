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

## Bâtiments via OSM2World (qualité Blosm, optionnel)

Une troisième source de bâtiments, en plus d'OSM et IGN : [OSM2World](https://osm2world.org)
(licence MIT sur le dépôt GitHub — le `LICENSE.txt` du zip binaire 0.4.0 est une LGPLv3
visiblement pas mise à jour depuis la migration du projet vers MIT, incohérence côté
OSM2World ; sans conséquence ici de toute façon, cet outil l'appelle en sous-processus CLI
externe, jamais en code embarqué — aucune obligation de copyleft ne s'applique à ce repo
quel que soit le cas). Convertisseur OSM->3D bien plus complet que notre extrusion JS maison
(vraies formes de toit, fenêtres, plus proche de ce que produit le plugin Blosm dans Blender).

**Installer :**

1. Un JRE 17 ou plus récent (pas besoin du JDK complet) — [adoptium.net](https://adoptium.net),
   build "JRE". Vérifie avec `java -version`.
2. Télécharge `OSM2World-<version>-bin.zip` depuis
   [osm2world.org/download](https://osm2world.org/download/) et extrait-le dans
   `desktop/osm2world/` (le dossier doit contenir `OSM2World.jar` directement à sa racine,
   avec `lib/`, `resources/`, etc. à côté — exactement l'arborescence du zip, pas de
   sous-dossier intermédiaire). Ce dossier est gros (~450 Mo) et volontairement exclu de git
   (`.gitignore`) — dépendance tierce à télécharger soi-même, comme le JRE.
3. Relance `python3 server.py`. L'option **« OSM2World (qualité Blosm) »** apparaît dans le
   menu déroulant "Source des bâtiments" ; sans Java ou sans le jar, elle reste sélectionnable
   mais la récupération échoue avec un message explicite (`OSM2World indisponible : ...`).

**Ce qui est utilisé / pas utilisé :** seule la géométrie (murs, toit, fenêtres) est reprise —
les textures qu'OSM2World applique par défaut (ardoise, crépi, etc., toutes libres/bundlées
avec l'outil, aucune requête réseau supplémentaire) sont aplaties en couleur unie (la couleur
`Kd` du matériau), cohérent avec le reste de l'outil : Thomas re-shade lui-même dans Blender
ensuite, ce tool ne fournit que la géométrie. Le sol synthétique qu'OSM2World génère par
défaut est désactivé (`createTerrain=false`, voir `osm2world_buildings_only.properties`) —
l'outil a déjà son propre relief IGN drapé sur la zone, les deux feraient doublon.
Comme pour IGN, seule la couche "Bâtiments" passe par cette source ; les autres couches
(routes, végétation, etc.) restent récupérées via Overpass normalement.

## Sécurité du proxy local

`server.py` n'écoute que sur `127.0.0.1` (jamais `0.0.0.0`) — seul ce poste
peut le joindre. Le proxy `/api/proxy` et `/api/lyon/*` ne relaient que vers
une liste explicite de domaines (`ALLOWED_HOSTS`), jamais vers une URL
arbitraire — ce n'est pas un relais ouvert.

## Limites connues (pistes pour la suite)

- Le mesh Lyon est affiché en aperçu uniquement — pas encore inclus dans
  l'export `.glb`/`.OBJ` (extraire/découper un tileset 3D Tiles à la zone
  choisie est un chantier à part, non trivial).
- Le vrai gain "qualité Blosm" pour les hauteurs/le terrain (dérivés du
  nuage de points LIDAR HD IGN plutôt que d'estimations) n'est pas encore
  fait — possible maintenant qu'on est en Python (`laspy`, sans dépendance
  GDAL/PDAL système), mais c'est un chantier séparé. Le gain "qualité Blosm"
  côté géométrie des bâtiments, lui, est fait (source OSM2World ci-dessus).
- OSM2World (ci-dessus) drape tout son lot de bâtiments avec un seul décalage
  d'altitude (échantillonné au centre de la zone), pas bâtiment par bâtiment
  comme les sources OSM/IGN — sur une zone avec un relief marqué, certains
  bâtiments peuvent donc reposer légèrement au-dessus/en dessous du sol
  IGN drapé. OSM2World ne renvoie pas de référence géographique par
  bâtiment qui permettrait un drapage individuel comme pour les autres
  sources.
