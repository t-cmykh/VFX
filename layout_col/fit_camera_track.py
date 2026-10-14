"""Suivi de caméra image par image : silhouette de la route + flux optique du sol."""
import cv2, numpy as np, json, math
from scipy.optimize import minimize
from scipy.signal import savgol_filter
import fit as F
S=592; H=388   # résolution de travail
def proj(P,cam,f):
    x,y,z,yaw,pit=cam
    fwd=np.array([math.cos(pit)*math.cos(yaw),math.cos(pit)*math.sin(yaw),-math.sin(pit)])
    right=np.cross(fwd,[0,0,1.0]); right/=np.linalg.norm(right); up=np.cross(right,fwd)
    d=P-np.array([x,y,z]); dep=d@fwd; fp=f/36.0*S
    return np.stack([S/2+fp*(d@right)/np.maximum(dep,1e-3),H/2-fp*(d@up)/np.maximum(dep,1e-3)],1),dep
def backproj(pts,cam,f):
    x,y,z,yaw,pit=cam
    fwd=np.array([math.cos(pit)*math.cos(yaw),math.cos(pit)*math.sin(yaw),-math.sin(pit)])
    right=np.cross(fwd,[0,0,1.0]); right/=np.linalg.norm(right); up=np.cross(right,fwd)
    fp=f/36.0*S
    d=(pts[:,0:1]-S/2)/fp*right[None]+(-(pts[:,1:2]-H/2))/fp*up[None]+fwd[None]
    t=-z/d[:,2]; ok=t>0
    P=np.array([x,y,z])[None]+d*t[:,None]; return P,ok
fit=json.load(open("vid/fit.json")); R=fit["R"]; f=fit["f"]
N=113
gray=[cv2.cvtColor(cv2.resize(cv2.imread(f"vid/full/{n:03d}.png"),(S,H),interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2GRAY) for n in range(1,N+1)]
masks=[F.road_mask(n) for n in range(1,N+1)]     # 296x194
cams=[np.array(fit["cam1"]+[f])]
for t in range(N-1):
    m_full=cv2.resize(masks[t].astype(np.uint8),(S,H),interpolation=cv2.INTER_NEAREST)
    m_full=cv2.erode(m_full,np.ones((9,9),np.uint8))
    pts=cv2.goodFeaturesToTrack(gray[t],400,0.01,6,mask=m_full)
    p0=pts.reshape(-1,2).astype(np.float32)
    p1,st,_=cv2.calcOpticalFlowPyrLK(gray[t],gray[t+1],p0,None,winSize=(21,21),maxLevel=3)
    # contrôle retour (forward-backward)
    p0b,st2,_=cv2.calcOpticalFlowPyrLK(gray[t+1],gray[t],p1,None,winSize=(21,21),maxLevel=3)
    good=(st[:,0]==1)&(st2[:,0]==1)&(np.linalg.norm(p0b-p0,axis=1)<0.5)
    p0,p1=p0[good],p1[good]
    cam=cams[-1]; W,ok=backproj(p0,cam[:5],cam[5]); W,p0,p1=W[ok],p0[ok],p1[ok]
    def loss(c):
        uv,dep=proj(W,c[:5],c[5]); r=np.linalg.norm(uv-p1,axis=1)
        flow=np.mean(np.minimum(r,6.0)**2)
        iou=F.loss(R,c[:5],c[5],masks[t+1])
        return flow+60.0*iou
    r=minimize(loss,cam,method="Nelder-Mead",options=dict(maxiter=600,xatol=1e-4,fatol=1e-6,initial_simplex=None))
    cams.append(r.x)
    if t%10==0: print(t+1,len(p0),round(r.fun,3),flush=True)
cams=np.array(cams)
sm=savgol_filter(cams,window_length=11,polyorder=2,axis=0)
ious=[1-F.loss(R,sm[i][:5],sm[i][5],masks[i]) for i in range(N)]
print("IoU lissé min/moy:",min(ious),np.mean(ious))
fit["cams"]=sm.tolist()
print("f:",sm[0,5],sm[N//2,5],sm[-1,5])
json.dump(fit,open("vid/fit_track2.json","w"))
