"""Stage 2 : lignes 2D -> graphe plan -> faces (cellules)."""
import pickle, sys, math
from collections import defaultdict
import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import unary_union, nearest_points
from shapely.strtree import STRtree
import shapely

D=sys.argv[-1]
l2,p3=pickle.load(open(D+"/lines2d.pkl","rb"))
TOL=10.0

def clean(P):
    out=[P[0]]
    for p in P[1:]:
        if math.dist(p,out[-1])>1e-6: out.append(p)
    return out

names=list(l2); lines=[]
for nm in names:
    P,c=l2[nm]; P=clean(P)
    if len(P)>=2: lines.append((nm,P,c))
geoms=[LineString(P) for nm,P,c in lines]
tree=STRtree(geoms)
# --- accrochage des extremites libres
fixed=0
for gi,(nm,P,c) in enumerate(lines):
    if c: continue
    for end in (0,-1):
        pt=Point(P[end])
        best=None
        for gj in tree.query(pt.buffer(TOL)):
            if gj==gi: continue
            d=geoms[gj].distance(pt)
            if d<1e-6: best=None;break
            if d<=TOL and (best is None or d<best[0]):
                q=nearest_points(geoms[gj],pt)[0]; best=(d,(q.x,q.y))
        if best:
            if end==0: P.insert(0,best[1])
            else: P.append(best[1])
            fixed+=1
    geoms[gi]=LineString(P)
print("extremites accrochees",fixed)
U=shapely.unary_union(geoms,grid_size=0.05)
pieces=list(U.geoms) if hasattr(U,"geoms") else [U]
print("pieces apres noeuds",len(pieces))

def key(c): return (round(c[0],1),round(c[1],1))
nodes={}; edges=[]
for g in pieces:
    cs=list(g.coords)
    a,b=key(cs[0]),key(cs[-1])
    if a==b and len(cs)<4: continue
    edges.append([a,b,cs])
# --- fusion des noeuds trop proches (moins de MERGE unites) : evite les mini-aretes aux croisements
MERGE=3.0
reps=[]
def rep(k):
    for r in reps:
        if math.dist(r,k)<MERGE: return r
    reps.append(k); return k
for e in edges:
    e[0]=rep(e[0]); e[1]=rep(e[1])
def clen(cs): return sum(math.dist(cs[i],cs[i+1]) for i in range(len(cs)-1))
seen_pairs={}
new=[]
for a,b,cs in edges:
    if a==b and clen(cs)<MERGE*6: continue
    kk=(min(a,b),max(a,b))
    if kk in seen_pairs and abs(seen_pairs[kk]-clen(cs))<MERGE*3: continue
    seen_pairs[kk]=clen(cs); new.append([a,b,cs])
edges=new
print("aretes",len(edges))
# --- elagage des pendants
changed=True
while changed:
    changed=False
    deg=defaultdict(int)
    for a,b,cs in edges: deg[a]+=1;deg[b]+=1
    keep=[e for e in edges if deg[e[0]]>1 and deg[e[1]]>1]
    if len(keep)!=len(edges): changed=True; edges=keep
print("aretes apres elagage",len(edges))
# --- demi-aretes
he=[]  # (u,v,chain,twin_index)
for i,(a,b,cs) in enumerate(edges):
    he.append((a,b,cs)); he.append((b,a,cs[::-1]))
out=defaultdict(list)
def ang(he_i):
    u,v,cs=he[he_i]
    # direction au depart : premier point a plus d'1 unite
    for p in cs[1:]:
        if math.dist(p,cs[0])>1.0: return math.atan2(p[1]-cs[0][1],p[0]-cs[0][0])
    return math.atan2(cs[-1][1]-cs[0][1],cs[-1][0]-cs[0][0])
for i,(u,v,cs) in enumerate(he): out[u].append(i)
for u in out: out[u].sort(key=ang)
pos={}
for u,L in out.items():
    for k,i in enumerate(L): pos[i]=k
def nxt(i):
    u,v,cs=he[i]; tw=i^1
    L=out[v]; k=pos[tw]
    return L[(k-1)%len(L)]
seen=set(); faces=[]
for i in range(len(he)):
    if i in seen: continue
    cyc=[];j=i
    while j not in seen:
        seen.add(j);cyc.append(j);j=nxt(j)
    # aire signee
    pts=[]
    for h in cyc: pts+=he[h][2][:-1]
    A=0.5*sum(pts[k][0]*pts[(k+1)%len(pts)][1]-pts[(k+1)%len(pts)][0]*pts[k][1] for k in range(len(pts)))
    faces.append((A,cyc))
inner=[f for f in faces if f[0]>1.0]
outer=[f for f in faces if f[0]<=1.0]
print("faces",len(faces),"internes",len(inner),"externes",len(outer))
from collections import Counter
print("sommets par cellule",sorted(Counter(len(c) for A,c in inner).items()))
pickle.dump((he,[ (A,c) for A,c in inner]),open(D+"/graph.pkl","wb"))
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MP
fig,ax=plt.subplots(figsize=(30,9))
cols={3:"#f66",4:"#ddd",5:"#fb4",6:"#fa8"}
for A,c in inner:
    pts=[];[pts.extend(he[h][2][:-1]) for h in c]
    ax.add_patch(MP(pts,closed=True,fc=cols.get(len(c),"#9cf"),ec="k",lw=0.3))
ax.set_xlim(-2400,5300);ax.set_ylim(1300,-50);ax.set_aspect("equal")
plt.tight_layout();plt.savefig(D+"/cells.png",dpi=45)
