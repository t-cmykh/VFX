# Earth Importer

Fenêtre locale pour récupérer la géométrie 3D photogrammétrique de Google Earth sur une zone,
et l'exporter en **OBJ**, **Alembic** ou **USD**, avec ou sans textures.

Le décodage du format « rocktree » est porté en Python depuis
[retroplasma/earth-reverse-engineering](https://github.com/retroplasma/earth-reverse-engineering)
(`client/rocktree_decoder.h`). Ce dépôt est la source à consulter quand Google change quelque chose.

> ⚠️ **Usage personnel / expérimental.** Cet outil interroge des serveurs non documentés de Google, ce
> que ses conditions d'utilisation interdisent. Les données restent protégées par le droit d'auteur.
> Pour un usage commercial ou en production : Google Photorealistic 3D Tiles (API officielle, via Blosm
> ou Cesium) ou des sources libres (IGN LiDAR HD, photomaillage Lyon — voir `../desktop`).

## Installer

```bash
cd earth_importer
pip install -r requirements.txt
pip install pywebview          # optionnel : vraie fenêtre + sélecteur de dossier natif
```

Pour l'**ABC** et l'**USD**, Blender (3.6 à 5.x) doit être installé. L'outil le cherche dans le
`PATH` et les dossiers d'installation habituels ; sinon renseigner `blender_path` dans `config.yaml`.
L'OBJ n'a besoin de rien d'autre.

## Lancer

```bash
python app.py              # fenêtre native si pywebview est installé, sinon navigateur
python app.py --browser    # force le navigateur (http://127.0.0.1:8757)
```

Sous Windows, sans terminal : double-clic sur **`EarthImporter.bat`** (ou `launch.pyw`).
Si rien ne s'ouvre : une boîte d'erreur s'affiche, le détail est dans `earth_importer.log`, et
**`EarthImporter_debug.bat`** relance avec la console visible. Si une instance tourne déjà
(par exemple restée ouverte en arrière-plan), elle est réutilisée au lieu d'échouer sur le port.

## Version fichier local (sans installation)

**`EarthImporter.html`** : double-clic, ça s'ouvre dans le navigateur. Rien à installer (ni Python,
ni Blender). Même carte, mêmes réglages ; le résultat arrive en **`.zip`** dans le dossier
Téléchargements.

- Formats : **OBJ**, **USD** (`.usda`, matériaux `UsdPreviewSurface`, textures en `clamp`) et
  **GLB**. Pas d'Alembic (impossible dans un navigateur) : utiliser la version Python pour l'ABC.
- Tout se passe en mémoire : pour de très grandes zones, préférer la version Python.
- Fermer ou recharger la page vide le cache (relancer après une annulation ne retélécharge rien
  tant que la page reste ouverte).
- Zone **carrée** : bouton **▢ Dessiner** puis cliquer-glisser d'un coin à l'autre (Échap pour
  annuler), ou champ « Côté (m) » + coordonnées du centre. Le marqueur déplace le carré.
- Fond de carte au choix (en haut à droite, mémorisé) : Google Satellite / Plan, Esri, CARTO.
  Les serveurs OpenStreetMap refusent les pages ouvertes en `file://`, et certains fournisseurs
  demandent une clé : si un fond affiche une erreur, en choisir un autre.
- Si le log affiche « requête bloquée par le navigateur (CORS) », Google refuse les requêtes
  venant d'une page locale : utiliser la version Python, qui n'a pas cette limite.
- Le fichier est généré depuis `web/src/` par `python web/build.py` (ne pas le modifier à la main).
  Les réglages liés au protocole (URLs, `flipV`…) sont en haut de `web/src/app.js`.

## Utilisation

1. **Coordonnées** : taper `48.8584, 2.2945` puis Entrée, ou cliquer sur la carte / déplacer le marqueur.
2. **Rayon** : taille de la zone (cercle bleu). Les triangles hors du cercle sont supprimés.
3. **Détail** : niveau de l'octree. Chaque niveau en plus ≈ ×4 tuiles. Les résolutions affichées
   (m/px) sont indicatives ; la vraie valeur s'affiche dans le log après **Estimer**.
4. **Contenu** : géométrie + textures, ou géométrie seule (pas d'UV, pas de matériaux).
5. **Estimer** (optionnel) : lit seulement les métadonnées et annonce le nombre de tuiles.
6. **Télécharger** + format (OBJ / ABC / USD à côté du bouton).

Sortie dans `<dossier>/<nom>/` :

| Format | Fichiers |
|---|---|
| OBJ | `nom.obj`, `nom.mtl`, `textures/*.jpg` |
| USD | `nom.usdc` + `textures/` (matériaux `UsdPreviewSurface`) |
| ABC | `nom.abc` + `textures/`, et `nom.obj/.mtl` gardés comme référence des assignations : **Alembic ne stocke pas les matériaux**, il faut réassigner les textures (les noms d'objets `tile_<octant>_<n>` = noms de fichiers de texture) |

Repère : origine au centre de la zone, mètres. OBJ en Y-up (X = Est, −Z = Nord) ; dans Blender et
dans les exports ABC/USD, Z-up (X = Est, Y = Nord). Une tuile = un objet.

Les réponses brutes sont gardées dans `<dossier>/.earth_cache/` : relancer après une annulation ne
retélécharge pas ce qui l'a déjà été. Supprimer ce dossier pour repartir de zéro.

## Ce que fait l'outil

1. **Test de santé** : lit `PlanetoidMetadata` et le bloc racine. Si la réponse est illisible, le log
   dit « Protocole Google modifié » et rien n'est écrit.
2. **Parcours de l'octree** : descend les `BulkMetadata` (par paquets de 4 niveaux), garde les nœuds
   dont la boîte englobante touche le cercle, jusqu'au niveau choisi.
3. **Téléchargement** des `NodeData` en parallèle (limite de débit, nouvelles tentatives sur 429/5xx).
4. **Décodage** : sommets en delta, UV, triangle strips, masque d'octants. Un parent n'exporte que les
   octants qui n'ont pas d'enfant plus détaillé (pas de couches superposées). Eau et jupes ignorées.
5. **ECEF → ENU** en float64 autour du centre, écriture OBJ, puis Blender en arrière-plan pour ABC/USD
   (sommets soudés par tuile).

## Quand ça casse

Tout ce qui dépend de Google est isolé :

- `config.yaml` : URLs et paramètres de requête (le plus souvent, c'est là qu'il faut retoucher) ;
- `core/rocktree.py` : numéros de champs protobuf et décodage (schéma : `core/rocktree.proto`) ;
- `core/pbwire.py` : lecteur protobuf maison, aucune dépendance à une version de la lib `protobuf`.

L'interface, le parcours, l'export et Blender ne bougent pas. Les tests (`tests/`) tournent sur un faux
serveur qui encode des données synthétiques : après une correction du décodeur, les adapter et
vérifier avec `python -m pytest tests -q`.

## Limites connues

- **Non testé contre les vrais serveurs Google** : l'environnement de développement n'y avait pas accès.
  Décodeur, parcours, exports et interface sont testés sur données synthétiques ; les points suivants
  sont à vérifier au premier essai réel :
  - le sens des textures : aligné sur le décodeur de Google ; en secours, `flip_v` dans `config.yaml`
    (version Python) ou la case « Inverser V » (version fichier local) ;
  - la correspondance niveau ↔ résolution affichée dans le menu Détail.
- Textures **CRN/DXT1** non décodées : l'outil demande du JPG ; si une tuile n'existe qu'en CRN, elle
  est exportée sans texture (avertissement dans le log).
- Les normales Google ne sont pas reprises (le logiciel 3D les recalcule).
- Les tuiles voisines ne sont pas soudées entre elles (`Merge by Distance` global si besoin).
- Carte : tuiles OpenStreetMap, connexion Internet nécessaire pour le fond de carte. Leaflet est
  embarqué (`ui/vendor/leaflet`, BSD-2).

## Tests

```bash
pip install pytest
python -m pytest tests -q
```
