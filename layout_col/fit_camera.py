import cv2, numpy as np, json, math, sys
from scipy.optimize import differential_evolution, minimize
W_PX,H_PX=296,194
def road_mask(n):
    im=cv2.imread(f"vid/full/{n:03d}.png"); h,w=im.shape[:2]
    hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
    m=((hsv[...,1]<45)&(hsv[...,2]>70)&(hsv[...,2]<210)).astype(np.uint8)
    m[:int(h*0.35)]=0
    m=cv2.morphologyEx(m,cv2.MORPH_OPEN,np.ones((7,7),np.uint8)); m=cv2.morphologyEx(m,cv2.MORPH_CLOSE,np.ones((15,15),np.uint8))
    n_,lab,st,_=cv2.connectedComponentsWithStats(m)
    k=1+np.argmax(st[1:,cv2.CC_STAT_AREA]); m=(lab==k).astype(np.uint8)
    return cv2.resize(m,(W_PX,H_PX),interpolation=cv2.INTER_AREA)>0.5
WROAD=6.5
def centerline(R,L=150.0,step=2.0):
    pts=[(L-i*step,-R) for i in range(int(L/step)+1)]
    na=max(8,int(math.pi*R/2.0))
    for i in range(1,na):
        th=-math.pi/2-math.pi*i/na; pts.append((R*math.cos(th),R*math.sin(th)))
    pts+=[(i*step,R) for i in range(int(L/step)+1)]
    return np.array(pts)
def tangent(c):
    t=np.gradient(c,axis=0); return t/np.linalg.norm(t,axis=1,keepdims=True)
def project(P,cam,f):
    x,y,z,yaw,pit=cam
    fwd=np.array([math.cos(pit)*math.cos(yaw),math.cos(pit)*math.sin(yaw),-math.sin(pit)])
    right=np.cross(fwd,[0,0,1.0]); right/=np.linalg.norm(right); up=np.cross(right,fwd)
    d=P-np.array([x,y,z]); dep=d@fwd
    fp=f/36.0*W_PX
    u=W_PX/2+fp*(d@right)/np.maximum(dep,1e-3); v=H_PX/2-fp*(d@up)/np.maximum(dep,1e-3)
    return np.stack([u,v],1),dep
def render_mask(R,cam,f):
    c=centerline(R); t=tangent(c); n=np.stack([-t[:,1],t[:,0]],1)
    L=np.c_[c+n*WROAD/2,np.zeros(len(c))]; Rr=np.c_[c-n*WROAD/2,np.zeros(len(c))]
    pl,dl=project(L,cam,f); pr,dr=project(Rr,cam,f)
    m=np.zeros((H_PX,W_PX),np.uint8)
    for i in range(len(c)-1):
        if min(dl[i],dl[i+1],dr[i],dr[i+1])<0.5: continue
        q=np.array([pl[i],pl[i+1],pr[i+1],pr[i]]).astype(np.int32)
        if np.abs(q).max()>5000: continue
        cv2.fillConvexPoly(m,q,1)
    return m>0
def loss(R,cam,f,mask):
    m=render_mask(R,cam,f); i=(m&mask).sum(); u=(m|mask).sum()
    return 1-i/max(u,1)
if __name__=="__main__":
    m1=road_mask(1); mN=road_mask(113)
    np.save("vid/m1.npy",m1); np.save("vid/mN.npy",mN)
    # frame 1 : R, f, cam(5)
    b=[(5,14),(18,130),(-80,120),(-200,-3),(4,120),(math.radians(60),math.radians(170)),(math.radians(8),math.radians(65))]
    fun=lambda p: loss(p[0],p[2:],p[1],m1)
    r=differential_evolution(fun,b,maxiter=250,popsize=14,seed=3,tol=1e-6,polish=False,workers=1)
    print("frame1",r.fun,r.x.tolist())
    r2=minimize(fun,r.x,method="Nelder-Mead",options=dict(maxiter=1500,xatol=1e-3,fatol=1e-5))
    print("refined",r2.fun,r2.x.tolist())
    p1=r2.x
    # last frame : R,f fixés, cam libre autour de p1
    funN=lambda c: loss(p1[0],c,p1[1],mN)
    cb=[(p1[2]-25,p1[2]+25),(p1[3]-25,p1[3]+25),(max(3,p1[4]-15),p1[4]+25),(p1[5]-0.5,p1[5]+0.5),(max(0.05,p1[6]-0.4),p1[6]+0.4)]
    rN=differential_evolution(funN,cb,maxiter=250,popsize=20,seed=4,tol=1e-6,polish=False)
    rN2=minimize(funN,rN.x,method="Nelder-Mead",options=dict(maxiter=1500,xatol=1e-3,fatol=1e-5))
    print("frameN",rN2.fun,rN2.x.tolist())
    json.dump(dict(R=p1[0],f=p1[1],cam1=p1[2:].tolist(),camN=rN2.x.tolist()),open("vid/fit.json","w"))
