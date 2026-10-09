"""Stage 4 : L2 -> carve des ouvertures + anneaux (rim, hublots) -> mesh final."""
import bpy, bmesh, sys, pickle, math, importlib.util, itertools, os
from collections import defaultdict, Counter
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from shapely.geometry import Polygon, Point
D=sys.argv[-3]; BLEND=sys.argv[-2]; OUTDIR=sys.argv[-1]
def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
gl=load(D+"/geolib.py","geolib")
fg=load("/home/user/VFX/retopo/fenetres_gabarit.py","fg")
ec=load("/home/user/VFX/retopo/edgeflow_curves.py","ec")
bpy.ops.wm.open_mainfile(filepath=BLEND)
src=bpy.data.objects["polySurface578"]

def smooth_bm(src_ob,levels=2):
    tmp=src_ob.copy(); tmp.data=src_ob.data.copy(); bpy.context.scene.collection.objects.link(tmp)
    md=tmp.modifiers.new("cc","SUBSURF"); md.subdivision_type='CATMULL_CLARK'; md.levels=levels; md.render_levels=levels
    dg=bpy.context.evaluated_depsgraph_get(); ev=tmp.evaluated_get(dg)
    me=ev.to_mesh(); b=bmesh.new(); b.from_mesh(me); ev.to_mesh_clear()
    bpy.data.objects.remove(tmp)
    return b

bm0=bmesh.new(); bm0.from_mesh(src.data); bm0.verts.ensure_lookup_table(); bm0.faces.ensure_lookup_table()
bms=smooth_bm(src); bvh=BVHTree.FromBMesh(bms)
SEC=gl.Sections(bms)
def snap(p):
    h=bvh.find_nearest(p); return h[0] if h[0] is not None else p
pos,faces,orig,cpos,cfaces,cage_src=pickle.load(open(D+"/l2.pkl","rb"))
he,gfaces=pickle.load(open(D+"/graph.pkl","rb"))
# ------------------------------------------------ mesh L2
bm=bmesh.new()
V=[bm.verts.new(Vector(p)) for p in pos]
lay=bm.faces.layers.int.new("cell")
for f,o in zip(faces,orig):
    try:
        x=bm.faces.new([V[i] for i in f]); x[lay]=cage_src[o]
    except Exception: pass
bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
vote=0
for f in list(bm.faces)[::7]:
    h=bvh.find_nearest(f.calc_center_median())
    if h[0] is not None: vote+= 1 if f.normal.dot(h[1])>0 else -1
if vote<0: bmesh.ops.reverse_faces(bm,faces=list(bm.faces))
print("L2 : v",len(bm.verts),"f",len(bm.faces),"vote orientation",vote)
# polygones cage 2D
cellpoly={}; cellnodes={}
for ci,(A,cyc) in enumerate(gfaces):
    pts=[];[pts.extend(he[h][2][:-1]) for h in cyc]
    try: cellpoly[ci]=Polygon(pts)
    except Exception: pass
    cellnodes[ci]=[he[h][0] for h in cyc]
def find_cell(y,s):
    p=Point(y,s)
    for ci,pg in cellpoly.items():
        if pg.is_valid and pg.contains(p): return ci
    return None
def cell_faces(ci): return [f for f in bm.faces if f.is_valid and f[lay]==ci]
def s_mean(verts):
    ss=[SEC.s_of(v.co)[0] for v in verts]; return sum(ss)/len(ss)

# ------------------------------------------------ ouvertures d'origine
loops=[l for l in fg.boundary_loops(bm0) if len(l)<30]
big=max(fg.boundary_loops(bm0),key=len)
FAIL=[];DONE=[]
def evaluate(new_faces,n):
    inv=pin=0
    for f in new_faces:
        P=[v.co for v in f.verts]
        if len(P)!=4: continue
        i,p=fg.quad_ok(P,n); inv+=i; pin+=p
    return inv,pin
RIM_V=set()
def copy_rim(loop):
    kept=fg.rim_faces(loop)
    vm={}
    def nv(v):
        if v not in vm: vm[v]=bm.verts.new(v.co.copy())
        return vm[v]
    nf=[]
    for f in kept:
        nf.append(bm.faces.new([nv(v) for v in f.verts]))
    n=sum((f.normal for f in kept),Vector()).normalized()
    for f in nf:
        f.normal_update()
        if f.normal.dot(n)<0: f.normal_flip()
    RIM_V.update(vm.values())
    return nf,n,vm[loop[0]]

def rim_outer_edges(kept,L):
    Ls=set(L)
    return [e for f in kept for e in f.edges
            if sum(1 for g in e.link_faces if g in kept)==1 and not all(v in Ls for v in e.verts)]

MIND=5.0
def dp_bridge(B,R,rim_long):
    """Associe chaque sommet de B a un sommet du rim ou a un point inseree sur une arete longue du rim."""
    m,N=len(B),len(R)
    INF=1e30
    best=(INF,None)
    dmin=min((B[a]-R[b]).length for a in range(m) for b in range(N))
    for r,k in itertools.product(range(N),range(m)):
        if (B[k]-R[r]).length>dmin+90: continue
        Bk=B[k:]+B[:k]
        f=[[INF]*(N+1) for _ in range(m+1)]
        bk=[[None]*(N+1) for _ in range(m+1)]
        f[1][1]=(Bk[0]-R[r]).length_squared
        for i in range(1,m):
            for j in range(1,N+1):
                c=f[i][j]
                if c>=INF: continue
                if j<N:
                    cc=c+(Bk[i]-R[(r+j)%N]).length_squared
                    if cc<f[i+1][j+1]: f[i+1][j+1]=cc;bk[i+1][j+1]=(j,'m',None)
                e=(r+j-1)%N
                if rim_long[e]:
                    a,b=R[e],R[(e+1)%N]
                    d=b-a;Le=d.length;t=(Bk[i]-a).dot(d)/d.length_squared
                    lo=MIND/Le; t=max(lo,min(1-lo,t))
                    q=a+d*t;cc=c+(Bk[i]-q).length_squared
                    if cc<f[i+1][j]: f[i+1][j]=cc;bk[i+1][j]=(j,'i',(e,t))
        if f[m][N]<best[0]:
            # retour arriere
            asg=[None]*m; i,j=m,N
            while i>1:
                pj,kind,info=bk[i][j]
                asg[i-1]=(kind,(r+pj)%N if kind=='m' else None,info) if False else (kind,(r+j-1)%N if kind=='m' else None,info)
                i-=1; j=pj
            asg[0]=('m',r,None)
            best=(f[m][N],(k,asg,r))
    return best

def rb(faces):
    fs=set(faces)
    return [e for f in fs for e in f.edges if sum(1 for g in e.link_faces if g in fs)==1]

def prepare(fs,loop,n_hint=None):
    """Contour B de la zone a percer + appariement avec le rim, SANS modifier le maillage."""
    B=fg.order_cycle(rb(fs))
    if B is None:
        deg=Counter(v for e in rb(fs) for v in e.verts)
        return "contour de bloc non simple (%d faces, %d sommets de degre != 2)"%(len(fs),sum(1 for d in deg.values() if d!=2))
    kept0=fg.rim_faces(loop)
    rim0=fg.order_cycle(fg.region_boundary(kept0))
    n=sum((f.normal for f in kept0),Vector()).normalized()
    Bc=fg.ccw(B,n,key=lambda v:v.co); Rc=fg.ccw(rim0,n,key=lambda v:v.co)
    Rp=[v.co.copy() for v in Rc]; Bp=[v.co.copy() for v in Bc]
    if len(Bp)<len(Rp): return "contour (%d) plus petit que le rim (%d)"%(len(Bp),len(Rp))
    rim_long=[(Rp[(i+1)%len(Rp)]-Rp[i]).length>2*MIND+4 for i in range(len(Rp))]
    cost,sol=dp_bridge(Bp,Rp,rim_long)
    if sol is None: return "pas d'appariement (m=%d n=%d)"%(len(Bp),len(Rp))
    k,asg,r=sol
    targets={}
    byedge=defaultdict(list)
    for i,a in enumerate(asg):
        if a[0]=='i': byedge[a[2][0]].append([i,a[2][1]])
    for e,lst in byedge.items():
        Le=(Rp[(e+1)%len(Rp)]-Rp[e]).length; lo=MIND/Le; gap=MIND*1.4/Le
        lst.sort(key=lambda x:x[1])
        for q in range(len(lst)):
            lst[q][1]=max(lst[q][1],lo if q==0 else lst[q-1][1]+gap)
        if lst[-1][1]>1-lo+1e-9:
            if len(lst)*gap>1-2*lo: return "trop de coupes sur une arete"
            for q in range(len(lst)): lst[q][1]=lo+(1-2*lo)*(q+1)/(len(lst)+1)
        for i,t in lst: targets[i]=Rp[e].lerp(Rp[(e+1)%len(Rp)],t)
    return dict(B=Bc,Bp=Bp,Rc=Rc,Rp=Rp,n=n,sol=sol,cost=cost,targets=targets)

def bridge_opening(fs,loop,pl):
    """Carve, copie du rim, coupes du rim, quads 1 pour 1."""
    B,Bp,Rc,Rp,n,sol=pl["B"],pl["Bp"],pl["Rc"],pl["Rp"],pl["n"],pl["sol"]
    nf,n2,anchor=copy_rim(loop)
    cand_verts={v for f in fs for v in f.verts}
    bmesh.ops.delete(bm,geom=fs,context='FACES_ONLY')
    Bset=set(B)
    inner_verts=[v for v in cand_verts if v.is_valid and not v.link_faces and v not in Bset]
    bmesh.ops.delete(bm,geom=inner_verts,context='VERTS')
    bm.verts.index_update(); bm.faces.ensure_lookup_table()
    def cur_loop():
        return [l for l in fg.boundary_loops(bm) if anchor in l and len(l)<400][0]
    k,asg,r=sol
    Brot=B[k:]+B[:k]
    targets=pl['targets']
    for i,pt in targets.items():
        L=cur_loop(); kept=fg.rim_faces(L)
        best=None
        for e0 in rim_outer_edges(kept,L):
            if e0.calc_length()<=fg.LONG_EDGE: continue
            tt,d=fg.seg_param(pt,e0.verts[0].co,e0.verts[1].co)
            if 0.0<tt<1.0 and (best is None or d<best[0]): best=(d,e0)
        if best is None: return "coupe impossible"
        fg.loop_cut_at(snap,best[1],kept,pt)
    L=cur_loop(); kept=fg.rim_faces(L)
    rim2=fg.order_cycle(rim_outer_edges(kept,L))
    if len(rim2)!=len(Brot): return "comptes differents apres coupes (%d vs %d)"%(len(rim2),len(Brot))
    rim2=fg.ccw(rim2,n,key=lambda v:v.co)
    used=set();pair=[]
    for i,b in enumerate(Brot):
        c=asg[i]
        tgt=Rc[c[1]].co if c[0]=='m' else targets[i]
        cand=min((v for v in rim2 if v not in used),key=lambda v:(v.co-tgt).length_squared)
        used.add(cand);pair.append(cand)
    newf=[]
    for i in range(len(Brot)):
        j=(i+1)%len(Brot)
        try: f=bm.faces.new([pair[i],Brot[i],Brot[j],pair[j]])
        except Exception as ex: return "face refusee: "+str(ex)
        f.normal_update()
        if f.normal.dot(n)<0: f.normal_flip()
        newf.append(f)
    inv,pin=evaluate(newf,n)
    return ("ok",len(Brot),len(Rp),inv,pin)


# ------------------------------------------------ zones candidates pour percer une ouverture
face2d={}
for f in bm.faces:
    c=f.calc_center_median(); face2d[f]=(c.y,SEC.s_of(c)[0])
def faces_in(poly,exclude=()):
    return [f for f,(y,w) in face2d.items() if f.is_valid and poly.contains(Point(y,w))]
def loop2d(loop):
    return [(v.co.y,SEC.s_of(v.co)[0]) for v in loop]

def hublot_cap(region,center,r,n_hint):
    B=fg.order_cycle(rb(region))
    if B is None: return "contour non simple"
    n=sum((f.normal for f in region),Vector()).normalized()
    Bc=fg.ccw(B,n,key=lambda v:v.co); m=len(Bc)
    if m%2 or m<8 or m>16: return "contour de %d points"%m
    c3=snap(center)
    ref=Vector((1,0,0)) if abs(n.x)<0.9 else Vector((0,1,0))
    e1=(ref-n*ref.dot(n)).normalized(); e2=n.cross(e1)
    ring_pts=[snap(c3+(e1*math.cos(2*math.pi*k/m)+e2*math.sin(2*math.pi*k/m))*r) for k in range(m)]
    # rotation qui minimise la distance contour -> anneau
    best=min(range(m),key=lambda sft:sum((Bc[i].co-ring_pts[(i+sft)%m]).length_squared for i in range(m)))
    cand=set(v for f in region for v in f.verts)
    bmesh.ops.delete(bm,geom=list(region),context='FACES_ONLY')
    Bs=set(Bc)
    bmesh.ops.delete(bm,geom=[v for v in cand if v.is_valid and not v.link_faces and v not in Bs],context='VERTS')
    ring=[bm.verts.new(p) for p in ring_pts]; ctr=bm.verts.new(c3)
    RIM_V.update(ring); RIM_V.add(ctr)
    newf=[]
    def mk(vs):
        f=bm.faces.new(vs); f.normal_update()
        if f.normal.dot(n)<0: f.normal_flip()
        newf.append(f)
    for i in range(m):
        j=(i+1)%m
        mk([Bc[i],Bc[j],ring[(j+best)%m],ring[(i+best)%m]])
    rr=[ring[(k+best)%m] for k in range(m)]
    for k in range(m//2):
        mk([ctr,rr[2*k],rr[2*k+1],rr[(2*k+2)%m]])
    inv,pin=evaluate(newf,n)
    return ("ok",m,inv,pin)

DONE=[];FAIL=[]
def process_opening(loop):
    cy=sum(v.co.y for v in loop)/len(loop); cz=sum(v.co.z for v in loop)/len(loop)
    s_=sum(SEC.s_of(v.co)[0] for v in loop)/len(loop)
    tag="ouverture %2d pts y=%6.0f z=%4.0f"%(len(loop),cy,cz)
    tries=[]
    cid=find_cell(cy,s_)
    if cid is not None:
        # la cellule doit englober l'ouverture
        poly=Polygon(loop2d(loop))
        if cellpoly[cid].buffer(1).contains(poly): tries.append(("cellule %d (%d noeuds)"%(cid,len(cellnodes[cid])),cell_faces(cid)))
    base=Polygon(loop2d(loop))
    for d in (0.5,4,8,12,15,19,22,26,30,35,40,48,55,70,90):
        tries.append(("tampon %d"%d,faces_in(base.buffer(d))))
    last="aucune zone"
    for name,fs in tries:
        if not fs: continue
        pl=prepare(fs,loop)
        if isinstance(pl,str): last=(last+" | " if last!="aucune zone" else "")+name+": "+pl; continue
        try: res=bridge_opening(fs,loop,pl)
        except Exception as ex:
            import traceback; res="exception "+repr(ex)+" "+traceback.format_exc().splitlines()[-3].strip()
        if isinstance(res,tuple):
            DONE.append((tag,name,res)); print(tag,": OK via",name,"| contour",res[1],"rim",res[2],"inv",res[3],"pincees",res[4]); return
        last=name+": "+str(res)
        break
    FAIL.append((tag,last)); print(tag,": ECHEC",last)

loops_sorted=sorted(loops,key=lambda l:sum(v.co.y for v in l)/len(l))
for loop in loops_sorted: process_opening(loop)
print("ouvertures : ok",len(DONE),"echecs",len(FAIL))
# ------------------------------------------------ hublots
HUB=[]
for k,(cy,cz,r) in enumerate(ec.find_portholes(bm0)):
    p3=bvh.ray_cast(Vector((-3500,cy,cz)),Vector((1,0,0)))[0]
    if p3 is None: HUB.append(("hublot %d"%k,"pas de point")); continue
    poly=Point(cy,cz).buffer(r)
    res=None
    for d in (10,12,14,16,18,20,22,25,28,32,36,40,46,52,60):
        fs=[f for f in faces_in(poly.buffer(d)) if f.is_valid]
        if not fs: continue
        res=hublot_cap(fs,p3,r,None)
        if isinstance(res,tuple): break
    tag="hublot %2d y=%6.0f z=%4.0f"%(k,cy,cz)
    if isinstance(res,tuple): HUB.append((tag,res)); print(tag,": OK contour",res[1],"inv",res[2],"pincees",res[3])
    else: HUB.append((tag,res)); print(tag,": ECHEC",res)


# ------------------------------------------------ relaxation tangentielle (hors courbes, rims, hublots, bords)
import numpy as np
from shapely.strtree import STRtree
from shapely.geometry import LineString
l2,_p3=pickle.load(open(D+"/lines2d.pkl","rb"))
cl=[LineString(P) for nm,(P,c) in l2.items() if len(P)>=2]
ctree=STRtree(cl)
def near_curve(v):
    pt=Point(v.co.y,SEC.s_of(v.co)[0]); i=ctree.nearest(pt); return cl[i].distance(pt)
pinned={v for v in bm.verts if v in RIM_V or v.is_boundary or near_curve(v)<1.5}
free=[v for v in bm.verts if v not in pinned and len(v.link_edges)>=3]
print("relaxation : sommets libres",len(free),"/",len(bm.verts))
for it in range(12):
    tgt=[]
    for v in free:
        nb=[e.other_vert(v).co for e in v.link_edges]
        tgt.append(snap(v.co.lerp(sum(nb,Vector())/len(nb),0.5)))
    for v,p in zip(free,tgt): v.co=p
import numpy as np
def flush_hublot(c3,r):
    """Aplatit la surface autour du hublot (ajustement quadratique local) puis recree le creux en cuvette."""
    near=[v for v in bm.verts if (v.co-c3).length<r*2.8]
    ref=[v for v in bm.verts if r*2.8<=(v.co-c3).length<r*5.0]
    if len(ref)<8 or not near: return 0
    # normale locale : moyenne des normales de faces voisines
    n=sum((f.normal for v in ref for f in v.link_faces),Vector()).normalized()
    refv=Vector((1,0,0)) if abs(n.x)<0.9 else Vector((0,1,0))
    e1=(refv-n*refv.dot(n)).normalized(); e2=n.cross(e1)
    def loc(p): d=p-c3; return d.dot(e1),d.dot(e2),d.dot(n)
    U=np.array([loc(v.co) for v in ref])
    M=np.stack([np.ones(len(U)),U[:,0],U[:,1],U[:,0]**2,U[:,0]*U[:,1],U[:,1]**2],axis=1)
    coef,*_=np.linalg.lstsq(M,U[:,2],rcond=None)
    def h(u,v): return coef[0]+coef[1]*u+coef[2]*v+coef[3]*u*u+coef[4]*u*v+coef[5]*v*v
    depth=max(0.0,min(14.0,h(0,0)-loc(snap(c3))[2]))
    for v in near:
        u,w,_=loc(v.co); rho=math.hypot(u,w); z=h(u,w)
        if rho<r: z-=depth*(1-(rho/r)**2)
        v.co=c3+e1*u+e2*w+n*z
    return depth
FLUSH=[]
for k,(cy,cz,r) in enumerate(ec.find_portholes(bm0)):
    p3=bvh.ray_cast(Vector((-3500,cy,cz)),Vector((1,0,0)))[0]
    if p3 is not None: FLUSH.append((k,flush_hublot(p3,r)))
print("hublots aplatis (profondeur du creux) :",[(k,round(d,1)) for k,d in FLUSH])
bmesh.ops.delete(bm,geom=[v for v in bm.verts if not v.link_faces],context='VERTS')
bm.normal_update()
print("faces",len(bm.faces),Counter(len(f.verts) for f in bm.faces))
me=bpy.data.meshes.new("coque_GEO"); bm.to_mesh(me)
ob=bpy.data.objects.new("coque_GEO",me); bpy.context.scene.collection.objects.link(ob)
os.makedirs(OUTDIR,exist_ok=True)
for o in bpy.context.view_layer.objects: o.select_set(o is ob)
bpy.context.view_layer.objects.active=ob
bpy.ops.wm.alembic_export(filepath=OUTDIR+"/coque_geo.abc",selected=True)
src.hide_set(True)
bpy.ops.wm.save_as_mainfile(filepath=OUTDIR+"/geo_stage4.blend")
