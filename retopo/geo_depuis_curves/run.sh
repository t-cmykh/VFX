#!/bin/sh
# Genere la geo a partir des curves.   Usage : sh run.sh <coque.abc> <curves.blend> <dossier_de_travail>
# 1. curves -> reseau plan (y, w) ; 2. cellules ; 3. cellules -> quads colles sur la coque ;
# 4. ouvertures (rims), hublots, relaxation ; ecrit coque_geo.abc et geo_stage4.blend
ABC=$1; BLEND=$2; W=$3
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$W"; cp "$HERE/geolib.py" "$W/geolib.py"
python3 -I "$HERE/etape1_reseau.py" "$BLEND" "$ABC" "$W"
python3 -I "$HERE/etape2_cellules.py" "$W"
python3 -I "$HERE/etape3_subdivision.py" "$W" "$BLEND"
python3 -I "$HERE/etape4_ouvertures.py" "$W" "$BLEND" "$W/out"
