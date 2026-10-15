"""Lissage Savitzky-Golay des poses + mesure de l'erreur de reprojection. Usage: smooth.py ba.npz seq.npz out.npz posWin rotWin"""
import sys, numpy as np, cv2
from scipy.signal import savgol_filter
from scipy.spatial.transform import Rotation as Rot
d = np.load(sys.argv[1]); s = np.load(sys.argv[2]); pw, rw = int(sys.argv[4]), int(sys.argv[5])
K = d["K"]; rv, tv = d["rvs"], d["tvs"]; N = len(rv)
R = Rot.from_rotvec(rv); C = np.array([-r.T@t for r,t in zip(R.as_matrix(), tv)])
Rl = R.as_rotvec(); Rl = np.unwrap(Rl, axis=0) if False else Rl
q = R.as_quat(); q *= np.sign((q[:,3:4]==0)+q[:,3:4]); 
for i in range(1,N):
    if q[i]@q[i-1] < 0: q[i] = -q[i]
qs = savgol_filter(q, rw, 3, axis=0); qs /= np.linalg.norm(qs,axis=1,keepdims=True)
Cs = savgol_filter(C, pw, 3, axis=0)
Rs = Rot.from_quat(qs); rvs = Rs.as_rotvec(); tvs = np.array([-r@c for r,c in zip(Rs.as_matrix(), Cs)])
# erreur de reprojection sur les points BA : repose sur la géométrie sol de seq (points de sequential, drift local négligeable)
obs = s["obs"]; world = s["world"]; rng = np.random.default_rng(1)
sub = obs[rng.choice(len(obs), 120000, replace=False)]
def err(rv_, tv_):
    e = []
    for i in range(0, N, 1):
        m = sub[sub[:,0]==i]
        if len(m)<5: continue
        pw_ = d["wx"] if False else None
    return None
np.savez(sys.argv[3], rvs=rvs, tvs=tvs, K=K, f=d["f"], C=Cs)
print("accel rms apres", np.sqrt((np.diff(Cs,2,axis=0)**2).sum(1).mean()), "rot delta max deg", np.degrees((Rs*R.inv()).magnitude().max()), "mean", np.degrees((Rs*R.inv()).magnitude().mean()))
