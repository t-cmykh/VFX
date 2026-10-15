"""Position lissée (SG large) fixée, rotation re-résolue image par image sur les points sol, puis léger lissage rotation.
Usage: fix_pos.py seq.npz ba2.npz out.npz posWin rotWin"""
import sys, numpy as np, cv2
from scipy.signal import savgol_filter
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation as Rot
s = np.load(sys.argv[1]); b = np.load(sys.argv[2]); pwin, rwin = int(sys.argv[4]), int(sys.argv[5])
obs = s["obs"]; K = b["K"]; N = len(b["rvs"]); Ki = np.linalg.inv(K)
fr = obs[:,0].astype(int); pid = obs[:,1].astype(int); xy = obs[:,2:4]
Rm = np.array([cv2.Rodrigues(r)[0] for r in b["rvs"]]); Cc = np.array([-R.T@t for R,t in zip(Rm,b["tvs"])])
Cs = savgol_filter(Cc, pwin, 3, axis=0)
# monde des pistes : intersection sol depuis keyframes (poses b), comme repnp
iskf = np.zeros(N,bool); iskf[::4] = True; iskf[N-1] = True; m = iskf[fr]
d = Ki @ np.vstack([xy[m].T, np.ones(m.sum())]); d = np.einsum('nji,jn->ni', Rm[fr[m]], d)
t = -Cc[fr[m],2]/d[:,2]; ok=(t>0)&(t<2500); P = Cc[fr[m]]+d*t[:,None]; pm = pid[m][ok]; P = P[ok]
o = np.argsort(pm); pm=pm[o]; P=P[o]; u,st = np.unique(pm,return_index=True); en=np.append(st[1:],len(pm))
Wd = {}
for k,a,e in zip(u,st,en):
    if e-a>=2:
        med=np.median(P[a:e,:2],axis=0)
        if np.linalg.norm(P[a:e,:2]-med,axis=1).max()<3.0: Wd[k]=np.array([med[0],med[1],0.0])
byf = {}
for k,(f_,p_) in enumerate(zip(fr,pid)):
    if p_ in Wd: byf.setdefault(f_,[]).append(k)
rv = np.zeros((N,3)); er = np.zeros(N)
for i in range(N):
    ks=byf[i]; pw=np.array([Wd[pid[k]] for k in ks]); uv=xy[ks]
    def res(r):
        pr = cv2.projectPoints(pw, r.reshape(3,1), (-cv2.Rodrigues(r)[0]@Cs[i]).reshape(3,1), K, None)[0][:,0]
        return (pr-uv).ravel()
    s_ = least_squares(res, b["rvs"][i], loss='soft_l1', f_scale=1.0)
    rv[i] = s_.x; e = np.linalg.norm(res(s_.x).reshape(-1,2),axis=1); er[i] = np.median(e)
R = Rot.from_rotvec(rv); q = R.as_quat()
for i in range(1,N):
    if q[i]@q[i-1]<0: q[i]=-q[i]
qs = savgol_filter(q, rwin, 3, axis=0); qs /= np.linalg.norm(qs,axis=1,keepdims=True); Rs = Rot.from_quat(qs)
tvs = np.array([-r@c for r,c in zip(Rs.as_matrix(), Cs)]); rvs = Rs.as_rotvec()
print("erreur mediane px: moy", er.mean(), "max", er.max())
np.savez(sys.argv[3], rvs=rvs, tvs=tvs, K=K, f=b["f"], C=Cs, err=er)
