"""Re-PnP dense de chaque image : tous les points de piste, monde = intersection rayon/sol depuis la keyframe BA voisine.
Usage: repnp.py seq.npz ba.npz out.npz"""
import sys, numpy as np, cv2
s = np.load(sys.argv[1]); b = np.load(sys.argv[2]); obs = s["obs"]; K = b["K"]; N = len(b["rvs"])
rvs, tvs = b["rvs"], b["tvs"]; Ki = np.linalg.inv(K)
fr = obs[:,0].astype(int); pid = obs[:,1].astype(int); xy = obs[:,2:4]
Rm = np.array([cv2.Rodrigues(r)[0] for r in rvs]); Cc = np.array([-R.T@t for R,t in zip(Rm,tvs)])
kf = np.arange(0, N, 4); iskf = np.zeros(N, bool); iskf[kf] = True; iskf[N-1] = True
# monde d'une piste : moyenne des intersections sur les keyframes BA où elle est vue (médiane)
m = iskf[fr]
d = Ki @ np.vstack([xy[m].T, np.ones(m.sum())]); d = np.einsum('nji,jn->ni', Rm[fr[m]], d)   # R^T d
t = -Cc[fr[m],2] / d[:,2]; ok = (t>0)&(t<2500); P = Cc[fr[m]] + d*t[:,None]
pm = pid[m][ok]; P = P[ok]
order = np.argsort(pm); pm = pm[order]; P = P[order]
u, st = np.unique(pm, return_index=True); en = np.append(st[1:], len(pm))
Wd = {}
for k,a,e in zip(u,st,en):
    if e-a >= 2:
        med = np.median(P[a:e,:2],axis=0)
        if np.linalg.norm(P[a:e,:2]-med,axis=1).max() < 3.0: Wd[k] = np.array([med[0],med[1],0.0])
print("pistes avec monde:", len(Wd))
byf = {}
for k,(f_,p_) in enumerate(zip(fr,pid)):
    if p_ in Wd: byf.setdefault(f_, []).append(k)
rv_o = np.zeros((N,3)); tv_o = np.zeros((N,3)); nin = np.zeros(N,int); er = np.zeros(N)
for i in range(N):
    ks = byf[i]; pw = np.array([Wd[pid[k]] for k in ks]); uv = xy[ks]
    ok_, rv, tv, inl = cv2.solvePnPRansac(pw, uv, K, None, rvs[i].reshape(3,1).copy(), tvs[i].reshape(3,1).copy(), True, 80, 1.5, 0.99, flags=cv2.SOLVEPNP_ITERATIVE)
    inl = inl[:,0]; rv, tv = cv2.solvePnPRefineLM(pw[inl], uv[inl], K, None, rv, tv)
    pr = cv2.projectPoints(pw[inl], rv, tv, K, None)[0][:,0]
    rv_o[i] = rv.ravel(); tv_o[i] = tv.ravel(); nin[i] = len(inl); er[i] = np.linalg.norm(pr-uv[inl],axis=1).mean()
print("inliers min/mean", nin.min(), nin.mean(), "err", er.mean(), er.max())
np.savez(sys.argv[3], rvs=rv_o, tvs=tv_o, K=K, f=b["f"], ninl=nin, err=er)
