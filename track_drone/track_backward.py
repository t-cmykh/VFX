"""Tracking 3D inversé (dernière image -> première) d'un plan de drone sur sol quasi plan.
Monde : Z haut, sol Z=0. Caméra OpenCV (x droite, y bas, z avant). Échelle arbitraire (cam finale à ALT0).
Usage: python3 -I track_backward.py frames_dir out.npz
"""
import sys, glob, math, numpy as np, cv2
fdir, out = sys.argv[1], sys.argv[2]
files = sorted(glob.glob(fdir + "/*.png")); N = len(files)
W, Hh = 1280, 720
F0 = 850.0; K = np.array([[F0,0,W/2],[0,F0,Hh/2],[0,0,1.0]])
ALT0, PITCH0 = 100.0, math.radians(10.5)   # pitch vers le bas: horizon mesuré y≈205px sur la dernière image
def rot_from_pitch(p):          # regarde +Y, incliné vers le bas de p
    zc = np.array([0, math.cos(p), -math.sin(p)]); xc = np.array([1.0,0,0]); yc = np.cross(zc, xc)
    return np.stack([xc, yc, zc])            # lignes = axes cam dans le monde  => R monde->cam
R0 = rot_from_pitch(PITCH0); C0 = np.array([0, 0, ALT0])
rv0 = cv2.Rodrigues(R0)[0]; tv0 = -R0 @ C0
def backproj(px, rvec, tvec, K):
    R = cv2.Rodrigues(rvec)[0]; C = -R.T @ tvec.ravel()
    d = np.linalg.inv(K) @ np.vstack([px.T, np.ones(len(px))]); d = R.T @ d        # 3xn
    t = -C[2] / d[2]; ok = (t > 0) & (t < 25 * ALT0)
    return (C[:, None] + d * t).T, ok
def gray(i): return cv2.imread(files[i], cv2.IMREAD_GRAYSCALE)
def detect(g, existing, n_want, maxd=12):
    m = np.full(g.shape, 255, np.uint8); m[:int(Hh*0.12)] = 0       # coupe ciel/horizon lointain
    for p in existing: cv2.circle(m, (int(p[0]), int(p[1])), maxd, 0, -1)
    p = cv2.goodFeaturesToTrack(g, n_want, 0.005, 10, mask=m, blockSize=7)
    return np.zeros((0,2),np.float32) if p is None else p.reshape(-1,2).astype(np.float32)
poses = [None]*N; poses[N-1] = (rv0, tv0)
g = gray(N-1)
pts = detect(g, [], 900); Wp, ok = backproj(pts, rv0, tv0, K); pts, Wp = pts[ok], Wp[ok]
tracks = {}   # sauvegarde des observations : liste (frame, id, x, y) + points monde
obs = []; world = []; ids = np.arange(len(pts)); nid = len(pts)
for k, (p, w) in enumerate(zip(pts, Wp)): world.append(w); obs.append((N-1, k, p[0], p[1]))
rv, tv = rv0.copy(), tv0.copy(); lk = dict(winSize=(25,25), maxLevel=4, criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT, 30, 0.01))
for i in range(N-1, 0, -1):
    g2 = gray(i-1)
    p1, st, _ = cv2.calcOpticalFlowPyrLK(g, g2, pts, None, **lk)
    p0b, st2, _ = cv2.calcOpticalFlowPyrLK(g2, g, p1, None, **lk)
    good = (st[:,0]==1)&(st2[:,0]==1)&(np.linalg.norm(p0b-pts,axis=1)<0.4)&(p1[:,0]>2)&(p1[:,0]<W-2)&(p1[:,1]>2)&(p1[:,1]<Hh-2)
    pts, p1, Wp, ids = pts[good], p1[good], Wp[good], ids[good]
    ok, rv2, tv2, inl = cv2.solvePnPRansac(Wp, p1, K, None, rv.copy(), tv.copy(), True, 100, 2.0, 0.99, flags=cv2.SOLVEPNP_ITERATIVE)
    inl = inl[:,0]
    rv2, tv2 = cv2.solvePnPRefineLM(Wp[inl], p1[inl], K, None, rv2, tv2)
    rv, tv = rv2, tv2; poses[i-1] = (rv, tv)
    pts, Wp, ids = p1[inl], Wp[inl], ids[inl]
    for k, p in zip(ids, pts): obs.append((i-1, int(k), p[0], p[1]))
    if len(pts) < 700:
        new = detect(g2, pts, 900 - len(pts)); 
        if len(new):
            nw, ok2 = backproj(new, rv, tv, K); new, nw = new[ok2], nw[ok2]
            nids = np.arange(nid, nid+len(new)); nid += len(new)
            for w, k, p in zip(nw, nids, new): world.append(w); obs.append((i-1, int(k), p[0], p[1]))
            pts = np.vstack([pts, new]); Wp = np.vstack([Wp, nw]); ids = np.concatenate([ids, nids])
    g = g2
    if i % 100 == 0: print(i, len(pts), flush=True)
rvs = np.array([p[0].ravel() for p in poses]); tvs = np.array([p[1].ravel() for p in poses])
np.savez(out, rvs=rvs, tvs=tvs, K=K, obs=np.array(obs), world=np.array(world))
print("done", N, len(world), len(obs))
