"""Stage 1 : curves (blend) -> reseau plan (y, s) -> cellules."""
import bpy, bmesh, sys, math, pickle, os
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import interpolate_bezier
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

BLEND=sys.argv[-3]; ABC=sys.argv[-2]; OUT=sys.argv[-1]

# ------------------------------------------------ coque + sections
bpy.ops.wm.open_mainfile(filepath=BLEND)
src=bpy.data.objects["polySurface578"]
bm=bmesh.new(); bm.from_mesh(src.data)
bvh=BVHTree.FromBMesh(bm)

import importlib.util
_sp=importlib.util.spec_from_file_location("geolib",os.path.dirname(os.path.abspath(__file__))+"/geolib.py");_gl=importlib.util.module_from_spec(_sp);_sp.loader.exec_module(_gl)
Sections=_gl.Sections
SEC=Sections(bm)

# ------------------------------------------------ lecture des curves
def eval_curve(ob,res=10):
    sp=ob.data.splines[0]; b=sp.bezier_points; n=len(b)
    pairs=[(b[i],b[(i+1)%n]) for i in range(n if sp.use_cyclic_u else n-1)]
    pts=[]
    for a,c in pairs:
        seg=interpolate_bezier(a.co,a.handle_right,c.handle_left,c.co,res)
        pts+= [Vector(p) for p in seg[:-1]]
    if not sp.use_cyclic_u: pts.append(Vector(b[-1].co))
    else: pts.append(pts[0].copy())
    # densifier : pas max 12
    out=[pts[0]]
    for p in pts[1:]:
        d=(p-out[-1]).length
        k=int(d//12)
        for j in range(1,k+1): out.append(out[-1].lerp(p,1.0/(k-j+2)) if False else out[-1])
        out.append(p)
    return pts, sp.use_cyclic_u

curves={}
for ob in bpy.data.objects:
    if ob.type!="CURVE": continue
    cat=[c.name for c in bpy.data.collections if ob.name in c.objects and c.name!="EDGEFLOW"]
    cat=cat[0] if cat else "?"
    curves[ob.name]=(cat,)+eval_curve(ob)
print("curves lues",len(curves))
pickle.dump({k:(v[0],[tuple(p) for p in v[1]],v[2]) for k,v in curves.items()},open(OUT+"/curves.pkl","wb"))

# ------------------------------------------------ plan (y, s)
USE={"HORIZONTALES","VERTICALES","SECTIONS","PLANCHER"}
lines2d={}; pts3d={}
for nm,(cat,pts,cyc) in curves.items():
    if cat in USE or nm=="bordure_coque":
        P=[]
        for p in pts:
            s,_=SEC.s_of(p)
            P.append((p.y,s))
        lines2d[nm]=(P,cyc); pts3d[nm]=pts
print("lignes",len(lines2d))
pickle.dump((lines2d,{k:[tuple(p) for p in v] for k,v in pts3d.items()}),open(OUT+"/lines2d.pkl","wb"))
pickle.dump({k:(v[0],v[1],v[2]) for k,v in SEC.cache.items()},open(OUT+"/sections.pkl","wb")) if False else None
