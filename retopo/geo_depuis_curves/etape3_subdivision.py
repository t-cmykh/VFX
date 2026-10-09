"""Stage 3 : graphe -> cage 3D -> subdivision x2 (quads) collee sur la coque."""
import bpy, bmesh, sys, pickle, math, importlib.util
from collections import defaultdict
from mathutils import Vector
from mathutils.bvhtree import BVHTree
D=sys.argv[-2]; BLEND=sys.argv[-1]
spec=importlib.util.spec_from_file_location("geolib",D+"/geolib.py");gl=importlib.util.module_from_spec(spec);spec.loader.exec_module(gl)
bpy.ops.wm.open_mainfile(filepath=BLEND)
src=bpy.data.objects["polySurface578"]

def smooth_bm(src_ob,levels=2):
    tmp=src_ob.copy(); tmp.data=src_ob.data.copy(); bpy.context.scene.collection.objects.link(tmp)
    md=tmp.modifiers.new("cc","SUBSURF"); md.subdivision_type='CATMULL_CLARK'; md.levels=levels; md.render_levels=levels
    dg=bpy.context.evaluated_depsgraph_get(); ev=tmp.evaluated_get(dg)
    me=ev.to_mesh(); b=bmesh.new(); b.from_mesh(me); ev.to_mesh_clear()
    bpy.data.objects.remove(tmp)
    return b

bm0=bmesh.new(); bm0.from_mesh(src.data)
bms=smooth_bm(src); bvh=BVHTree.FromBMesh(bms)
SEC=gl.Sections(bms)
he,faces=pickle.load(open(D+"/graph.pkl","rb"))

def P3(c):  # (y,s) -> point 3D sur la coque
    p=SEC.p_of(c[0],c[1]); return p
# --- noeuds
nid={}; npos=[]
def node(c):
    if c not in nid:
        nid[c]=len(npos); npos.append(P3(c))
    return nid[c]
# --- aretes cage : cle (min,max) -> chaine 3D de min vers max
chains={}
cage=[]; cage_src=[]
for gi,(A,cyc) in enumerate(faces):
    vs=[]
    for h in cyc:
        u,v,cs=he[h]
        a,b=node(u),node(v)
        vs.append(a)
        key=(min(a,b),max(a,b))
        if key not in chains:
            ch=[P3(c) for c in cs]
            chains[key]=ch if a<b else ch[::-1]
    # retire doublons consecutifs
    cleaned=[vs[0]]
    for x in vs[1:]:
        if x!=cleaned[-1]: cleaned.append(x)
    if cleaned[0]==cleaned[-1]: cleaned.pop()
    if len(cleaned)>=3: cage.append(cleaned); cage_src.append(gi)
print("cage : noeuds",len(npos),"faces",len(cage))

def chain_mid(ch):
    L=[0.0]
    for i in range(1,len(ch)): L.append(L[-1]+(ch[i]-ch[i-1]).length)
    t=L[-1]/2
    for i in range(1,len(ch)):
        if L[i]>=t:
            w=(t-L[i-1])/max(L[i]-L[i-1],1e-9); return ch[i-1].lerp(ch[i],w),i,w
    return ch[-1].copy(),len(ch)-1,1.0
def split_chain(ch):
    m,i,w=chain_mid(ch)
    return ch[:i]+[m], [m]+ch[i:]

def snap(p):
    h=bvh.find_nearest(p); return h[0] if h[0] is not None else p

class Mesh:
    def __init__(s,pos,faces,chains,origin):
        s.pos=pos; s.faces=faces; s.chains=chains; s.origin=origin
MISSING=[]
def cc(M):
    pos=list(M.pos); chains={}; mid={}
    def getmid(a,b):
        k=(min(a,b),max(a,b))
        if k not in mid:
            ch=M.chains.get(k)
            if ch is None:
                ch=[pos[k[0]],pos[k[1]]]; MISSING.append(k)
            c1,c2=split_chain(ch)
            if len(ch)==2:                          # arete interieure (pas de courbe) : milieu colle a la coque
                sp=snap(c1[-1]); c1[-1]=sp; c2[0]=sp
            pos.append(c1[-1].copy()); m=len(pos)-1; mid[k]=m
            # c1 va de min vers m, c2 de m vers max
            chains[(k[0],m)]=c1                       # k0 < m
            chains[(k[1],m)]=c2[::-1]                 # cle (min,max) = (k1,m), orientee de k1 vers m
        return mid[k]
    newf=[]; newo=[]
    for f,o in zip(M.faces,M.origin):
        mids=[getmid(f[i],f[(i+1)%len(f)]) for i in range(len(f))]
        c=sum((pos[m] for m in mids),Vector())/len(mids)
        pos.append(snap(c)); ci=len(pos)-1
        for i in range(len(f)):
            newf.append([f[i],mids[i],ci,mids[i-1]]); newo.append(o)
    return Mesh(pos,newf,chains,newo)
M0=Mesh(npos,cage,chains,list(range(len(cage))))
M1=cc(M0); M2=cc(M1)
print("L1",len(M1.faces),"L2",len(M2.faces),"aretes sans chaine",len(MISSING))
pickle.dump(([tuple(p) for p in M2.pos],M2.faces,M2.origin,[tuple(p) for p in M0.pos],M0.faces,cage_src),open(D+"/l2.pkl","wb"))
# mesh bmesh de controle
bmx=bmesh.new()
vs=[bmx.verts.new(p) for p in M2.pos]
bad=0
for f in M2.faces:
    try: bmx.faces.new([vs[i] for i in f])
    except Exception as e: bad+=1
print("faces refusees",bad)
bmx.normal_update()
me=bpy.data.meshes.new("l2"); bmx.to_mesh(me)
ob=bpy.data.objects.new("l2",me); bpy.context.scene.collection.objects.link(ob)
bpy.ops.wm.save_as_mainfile(filepath=D+"/l2.blend")
