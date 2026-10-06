"""Importe une zone OpenStreetMap (bâtiments, routes, eau, parcs) dans Blender via bpy.

Usage (headless) :
    python osm_to_blender.py --out choisy.blend --glb choisy.glb
    blender -b -P osm_to_blender.py -- --lat 48.8265 --lon 2.3625 --radius 200

Par défaut : avenue de Choisy, Paris 13e, rayon 150 m.
Les données Overpass sont mises en cache (--cache) ; --json permet de travailler hors ligne.
Données © OpenStreetMap contributors (ODbL).
"""
import argparse, json, math, os, sys, time, urllib.parse, urllib.request

import bpy, bmesh
from mathutils import Vector

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
ROAD_WIDTH = {"motorway": 12, "trunk": 12, "primary": 10, "secondary": 9, "tertiary": 8,
              "residential": 6, "unclassified": 6, "service": 4, "living_street": 5,
              "pedestrian": 5, "footway": 2, "path": 1.5, "cycleway": 2, "steps": 2}
DEFAULT_HEIGHT = 18.0   # immeubles haussmanniens ~ 6 étages
LEVEL_HEIGHT = 3.0


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--lat", type=float, default=48.8265)
    p.add_argument("--lon", type=float, default=2.3625)
    p.add_argument("--radius", type=float, default=150, help="demi-côté de la zone en mètres")
    p.add_argument("--json", help="fichier Overpass JSON déjà téléchargé (hors ligne)")
    p.add_argument("--cache", default="osm_choisy.json")
    p.add_argument("--out", default="choisy.blend")
    p.add_argument("--glb", help="export glTF optionnel")
    return p.parse_args(argv)


def fetch(lat, lon, r, cache):
    if os.path.exists(cache):
        return json.load(open(cache))
    dlat = r / 110540.0
    dlon = r / (111320.0 * math.cos(math.radians(lat)))
    bb = f"{lat-dlat},{lon-dlon},{lat+dlat},{lon+dlon}"
    q = f"""[out:json][timeout:60];
(way["building"]({bb}); way["highway"]({bb}); way["natural"="water"]({bb});
 way["leisure"~"park|garden"]({bb}); way["landuse"="grass"]({bb}););
out geom;"""
    body = urllib.parse.urlencode({"data": q}).encode()
    for url in OVERPASS:
        try:
            req = urllib.request.Request(url, body, {"User-Agent": "vfx-osm2blender/1.0"})
            data = json.load(urllib.request.urlopen(req, timeout=90))
            json.dump(data, open(cache, "w"))
            return data
        except Exception as e:
            print(f"[osm] {url} : {e}")
            time.sleep(1)
    sys.exit("Overpass inaccessible : téléchargez la requête ailleurs et passez --json")


def make_proj(lat0, lon0):
    kx = 111320.0 * math.cos(math.radians(lat0))
    return lambda lat, lon: ((lon - lon0) * kx, (lat - lat0) * 110540.0)


def mat(name, rgba):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.diffuse_color = rgba
    m.use_nodes = True
    m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = rgba
    return m


def new_obj(name, me, col, material):
    me.materials.append(material)
    o = bpy.data.objects.new(name, me)
    col.objects.link(o)
    return o


def area(pts):
    return 0.5 * sum(a[0]*b[1] - b[0]*a[1] for a, b in zip(pts, pts[1:] + pts[:1]))


def height_of(tags):
    for k in ("height", "building:height"):
        try:
            return float(str(tags[k]).split()[0].replace(",", "."))
        except (KeyError, ValueError):
            pass
    try:
        return float(tags["building:levels"]) * LEVEL_HEIGHT
    except (KeyError, ValueError):
        return DEFAULT_HEIGHT


def polygon_mesh(name, pts_list, z_list):
    """Un seul mesh pour tous les polygones (prismes extrudés de z_list[i])."""
    bm = bmesh.new()
    for pts, h in zip(pts_list, z_list):
        if area(pts) < 0:
            pts = pts[::-1]
        try:
            f = bm.faces.new([bm.verts.new((x, y, 0)) for x, y in pts])
        except ValueError:
            continue
        if h > 0:
            res = bmesh.ops.extrude_face_region(bm, geom=[f])
            vs = [e for e in res["geom"] if isinstance(e, bmesh.types.BMVert)]
            bmesh.ops.translate(bm, vec=(0, 0, h), verts=vs)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    return me


def ribbon_mesh(name, lines):
    """Rubans plats le long des polylignes (largeur par ligne), miter simple."""
    bm = bmesh.new()
    for pts, w in lines:
        if len(pts) < 2:
            continue
        L, R = [], []
        for i, p in enumerate(pts):
            d = Vector((0, 0))
            if i > 0:
                d += (Vector(p) - Vector(pts[i-1])).normalized()
            if i < len(pts) - 1:
                d += (Vector(pts[i+1]) - Vector(p)).normalized()
            if d.length < 1e-6:
                continue
            d.normalize()
            n = Vector((-d.y, d.x)) * (w / 2)
            L.append(bm.verts.new((p[0] + n.x, p[1] + n.y, 0.02)))
            R.append(bm.verts.new((p[0] - n.x, p[1] - n.y, 0.02)))
        for i in range(len(L) - 1):
            try:
                bm.faces.new((L[i], L[i+1], R[i+1], R[i]))
            except ValueError:
                pass
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    return me


def main():
    a = parse_args()
    data = json.load(open(a.json)) if a.json else fetch(a.lat, a.lon, a.radius, a.cache)
    proj = make_proj(a.lat, a.lon)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    col = bpy.data.collections.new("OSM")
    bpy.context.scene.collection.children.link(col)

    buildings, roads, water, green = [], [], [], []
    for el in data["elements"]:
        if el["type"] != "way" or "geometry" not in el:
            continue
        pts = [proj(n["lat"], n["lon"]) for n in el["geometry"]]
        t = el.get("tags", {})
        if "building" in t and len(pts) > 3:
            buildings.append((pts[:-1], height_of(t)))
        elif "highway" in t:
            roads.append((pts, ROAD_WIDTH.get(t["highway"], 4)))
        elif t.get("natural") == "water" and len(pts) > 3:
            water.append(pts[:-1])
        elif len(pts) > 3:
            green.append(pts[:-1])

    if buildings:
        new_obj("Buildings", polygon_mesh("Buildings", [b[0] for b in buildings], [b[1] for b in buildings]),
                col, mat("Building", (0.82, 0.78, 0.70, 1)))
    if roads:
        new_obj("Roads", ribbon_mesh("Roads", roads), col, mat("Road", (0.12, 0.12, 0.13, 1)))
    if water:
        new_obj("Water", polygon_mesh("Water", water, [0]*len(water)), col, mat("Water", (0.1, 0.3, 0.6, 1)))
    if green:
        new_obj("Green", polygon_mesh("Green", green, [0]*len(green)), col, mat("Green", (0.2, 0.5, 0.2, 1)))

    r = a.radius * 1.2
    ground = bpy.data.meshes.new("Ground")
    ground.from_pydata([(-r, -r, -0.05), (r, -r, -0.05), (r, r, -0.05), (-r, r, -0.05)], [], [(0, 1, 2, 3)])
    new_obj("Ground", ground, col, mat("Ground", (0.35, 0.35, 0.33, 1)))

    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    col.objects.link(sun)

    print(f"[osm] {len(buildings)} bâtiments, {len(roads)} routes, {len(water)} eau, {len(green)} verts")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out))
    if a.glb:
        bpy.ops.export_scene.gltf(filepath=os.path.abspath(a.glb))


if __name__ == "__main__":
    main()
