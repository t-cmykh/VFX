"""Ajustement de faisceaux (poses + focale + points posés sur le sol Z=0), puis re-solve PnP de chaque image.
Usage: python3 -I ba.py seq.npz out.npz"""
import sys, numpy as np, cv2
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation as Rot
d = np.load(sys.argv[1]); rvs, tvs, K, obs, world = d["rvs"], d["tvs"], d["K"], d["obs"], d["world"]
N = len(rvs); STEP = 4
cx, cy = K[0,2], K[1,2]
fr = obs[:,0].astype(int); pid = obs[:,1].astype(int); xy = obs[:,2:4]
# longueur de piste ; garde les pistes longues
cnt = np.bincount(pid, minlength=len(world))
rng = np.random.default_rng(0)
cand = np.where(cnt >= 6)[0]; sel = rng.choice(cand, min(20000, len(cand)), replace=False)
kf = sorted(set(range(0, N, STEP)) | {N-1}); kfi = {f:i for i,f in enumerate(kf)}
Cc = np.array([-cv2.Rodrigues(r)[0].T @ t for r,t in zip(rvs,tvs)])
def run(sel, kf):
    kfi = {f:i for i,f in enumerate(kf)}; pidx = {p:i for i,p in enumerate(sel)}
    m = np.array([(f in kfi) and (p in pidx) for f,p in zip(fr,pid)])
    cf = np.array([kfi[f] for f in fr[m]]); cp = np.array([pidx[p] for p in pid[m]]); uv = xy[m]
    nk, npt = len(kf), len(sel)
    x0 = np.concatenate([np.array([np.concatenate([rvs[f].ravel(), Cc[f]]) for f in kf]).ravel(), world[sel,:2].ravel(), [K[0,0]]])
    def proj(x):
        P = x[:6*nk].reshape(nk,6); X = x[6*nk:6*nk+2*npt].reshape(npt,2); f = x[-1]
        R = Rot.from_rotvec(P[cf,:3]).as_matrix()
        pw = np.column_stack([X[cp], np.zeros(len(cp))]) - P[cf,3:]
        pc = np.einsum('nij,nj->ni', R, pw)
        return np.column_stack([cx + f*pc[:,0]/pc[:,2], cy + f*pc[:,1]/pc[:,2]])
    def res(x): return (proj(x) - uv).ravel()
    A = lil_matrix((2*len(cf), len(x0)), dtype=int)
    r = np.arange(len(cf))
    for k in range(6):
        A[2*r, 6*cf+k] = 1; A[2*r+1, 6*cf+k] = 1
    for k in range(2):
        A[2*r, 6*nk+2*cp+k] = 1; A[2*r+1, 6*nk+2*cp+k] = 1
    A[:, -1] = 1
    # jauge : dernière image fixe
    lo = -np.inf*np.ones(len(x0)); hi = np.inf*np.ones(len(x0)); last = kfi[N-1]
    for k in range(6): lo[6*last+k] = x0[6*last+k]-1e-9; hi[6*last+k] = x0[6*last+k]+1e-9
    s = least_squares(res, x0, jac_sparsity=A, loss='soft_l1', f_scale=1.5, x_scale='jac', bounds=(lo,hi), max_nfev=20, verbose=1, method='trf')
    e = np.linalg.norm((proj(s.x)-uv),axis=1)
    return s.x, e, cp, nk, npt
x, e, cp, nk, npt = run(sel, kf)
print("pass1 med/mean err px:", np.median(e), e.mean(), "f=", x[-1])
# rejette les points dont l'erreur moyenne est élevée (arbres, voiture...)
pe = np.bincount(cp, e, npt)/np.maximum(np.bincount(cp, minlength=npt),1)
keep = pe < 1.5; print("points gardés", keep.sum(), "/", npt)
sel2 = sel[keep]
x, e, cp, nk, npt = run(sel2, kf)
print("pass2 med/mean err px:", np.median(e), e.mean(), "f=", x[-1])
f = x[-1]; Kf = K.copy(); Kf[0,0] = Kf[1,1] = f
P = x[:6*nk].reshape(nk,6); X = x[6*nk:6*nk+2*npt].reshape(npt,2)
Xw = dict(zip(sel2, np.column_stack([X, np.zeros(npt)])))
# PnP image par image (points BA fixes), init interpolée
kfa = np.array(kf); rv_all = np.zeros((N,3)); t_all = np.zeros((N,3)); inl_n = np.zeros(N, int); errs = np.zeros(N)
byf = {}
for k,(f_,p_) in enumerate(zip(fr,pid)):
    if p_ in Xw: byf.setdefault(f_, []).append(k)
from scipy.interpolate import interp1d
rvi = interp1d(kfa, P[:,:3], axis=0)(np.arange(N)); Ci = interp1d(kfa, P[:,3:], axis=0)(np.arange(N))
for i in range(N):
    ks = byf[i]; pw = np.array([Xw[pid[k]] for k in ks]); uv = xy[ks]
    R = cv2.Rodrigues(rvi[i])[0]; t = -R @ Ci[i]
    ok, rv, tv, inl = cv2.solvePnPRansac(pw, uv, Kf, None, rvi[i].reshape(3,1).copy(), t.reshape(3,1).copy(), True, 50, 2.0, 0.99, flags=cv2.SOLVEPNP_ITERATIVE)
    inl = inl[:,0]; rv, tv = cv2.solvePnPRefineLM(pw[inl], uv[inl], Kf, None, rv, tv)
    pr = cv2.projectPoints(pw[inl], rv, tv, Kf, None)[0][:,0]
    rv_all[i] = rv.ravel(); t_all[i] = tv.ravel(); inl_n[i] = len(inl); errs[i] = np.linalg.norm(pr-uv[inl],axis=1).mean()
print("PnP final: inliers min/mean", inl_n.min(), inl_n.mean(), "err px mean/max", errs.mean(), errs.max())
np.savez(sys.argv[2], rvs=rv_all, tvs=t_all, K=Kf, f=f, err=errs, ninl=inl_n, wx=np.array(list(Xw.values())))
