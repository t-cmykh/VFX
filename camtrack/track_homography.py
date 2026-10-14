"""Tracking caméra par homographie du plan du sol (drone sur terrain plat).
Flux optique LK (pas de STEP images) -> homographie RANSAC -> décomposition (R, t/d, n)
-> pose monde (sol z=0, hauteur initiale = 1) -> lissage -> JSON.
Usage: python3 track_homography.py shot.mp4 poses.json [--focal_px 853] [--pitch0 60] [--step 4]
"""
import argparse, json, math
import cv2, numpy as np
from scipy.spatial.transform import Rotation as Rot, Slerp
from scipy.signal import savgol_filter

ap = argparse.ArgumentParser()
ap.add_argument("clip"); ap.add_argument("out")
ap.add_argument("--focal_px", type=float, default=0, help="focale en px à la résolution de travail (0 = FOV horizontal 74°)")
ap.add_argument("--pitch0", type=float, default=60, help="inclinaison initiale sous l'horizontale (deg)")
ap.add_argument("--step", type=int, default=4)
ap.add_argument("--w_h", type=float, default=300, help="poids anti-variation de hauteur")
ap.add_argument("--w_v", type=float, default=30, help="poids anti-variation de vitesse")
ap.add_argument("--max_range", type=float, default=8, help="portée max de rétroprojection (en hauteurs)")
ap.add_argument("--width", type=int, default=640)
a = ap.parse_args()

cap = cv2.VideoCapture(a.clip)
W0, H0 = int(cap.get(3)), int(cap.get(4)); nfr = int(cap.get(7))
sc = a.width / W0; W, H = a.width, int(H0 * sc)
f = a.focal_px or (W / 2) / math.tan(math.radians(37))
K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1.0]])
frames = []
while True:
    ok, im = cap.read()
    if not ok: break
    frames.append(cv2.cvtColor(cv2.resize(im, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY))
n = len(frames); idx = list(range(0, n, a.step))
if idx[-1] != n - 1: idx.append(n - 1)
print("frames", n, "keys", len(idx), "f_px", round(f, 1), flush=True)

def flow_H(g0, g1):
    p0 = cv2.goodFeaturesToTrack(g0, 800, 0.005, 8)
    if p0 is None: return None, 0
    p1, st, _ = cv2.calcOpticalFlowPyrLK(g0, g1, p0, None, winSize=(25, 25), maxLevel=4)
    pb, st2, _ = cv2.calcOpticalFlowPyrLK(g1, g0, p1, None, winSize=(25, 25), maxLevel=4)
    p0, p1, pb = p0.reshape(-1, 2), p1.reshape(-1, 2), pb.reshape(-1, 2)
    good = (st[:, 0] == 1) & (st2[:, 0] == 1) & (np.linalg.norm(pb - p0, axis=1) < 0.6)
    if good.sum() < 30: return None, 0
    Hm, inl = cv2.findHomography(p0[good], p1[good], cv2.RANSAC, 1.5, maxIters=3000, confidence=0.999)
    return Hm, int(inl.sum()) if inl is not None else 0

# état initial: monde z-up, caméra regardant vers +y incliné de pitch0, hauteur 1
from scipy.optimize import least_squares
up = np.array([0, 0, 1.0])
p0r = math.radians(a.pitch0)
fw = np.array([0, math.cos(p0r), -math.sin(p0r)]); rt = np.array([1.0, 0, 0]); dn = np.cross(fw, rt)
R = np.stack([rt, dn, fw])        # world -> cam
C = np.array([0, 0, 1.0])

def backproject(pts, R, C):
    d = np.stack([(pts[:, 0] - W / 2) / f, (pts[:, 1] - H / 2) / f, np.ones(len(pts))], 1) @ R   # rayons monde
    t = -C[2] / d[:, 2]; ok = (t > 0) & (t < a.max_range * C[2])
    return C + d * t[:, None], ok

def project(Wp, R, C):
    x = (Wp - C) @ R.T
    return np.stack([f * x[:, 0] / np.maximum(x[:, 2], 1e-3) + W / 2, f * x[:, 1] / np.maximum(x[:, 2], 1e-3) + H / 2], 1), x[:, 2]

poses = [(R.copy(), C.copy())]; stats = []; vel = np.zeros(3)
for k in range(1, len(idx)):
    g0, g1 = frames[idx[k - 1]], frames[idx[k]]
    p0 = cv2.goodFeaturesToTrack(g0, 800, 0.005, 8).reshape(-1, 2).astype(np.float32)
    p1, st, _ = cv2.calcOpticalFlowPyrLK(g0, g1, p0, None, winSize=(25, 25), maxLevel=4)
    pb, st2, _ = cv2.calcOpticalFlowPyrLK(g1, g0, p1, None, winSize=(25, 25), maxLevel=4)
    p1 = p1.reshape(-1, 2); pb = pb.reshape(-1, 2)
    good = (st[:, 0] == 1) & (st2[:, 0] == 1) & (np.linalg.norm(pb - p0, axis=1) < 0.6)
    Wp, okb = backproject(p0, R, C); m = good & okb
    Wp, q = Wp[m], p1[m]
    if len(Wp) < 30:
        poses.append((R.copy(), C.copy())); stats.append((idx[k], 0, 0)); continue
    def res(x):
        Cn = C + np.array([x[0], x[1], 0.0]); Rn = Rot.from_rotvec(x[3:]).as_matrix() @ R
        uv, dep = project(Wp, Rn, Cn)
        r = (uv - q).ravel()
        prior = a.w_v * (x[:2] - vel[:2])
        return np.concatenate([r, prior])
    sol = least_squares(res, np.concatenate([vel, np.zeros(3)]), loss="soft_l1", f_scale=1.5)
    x = sol.x; x[2] = 0.0; C = C + x[:3]; R = Rot.from_rotvec(x[3:]).as_matrix() @ R; vel = 0.7 * vel + 0.3 * x[:3]
    rmse = float(np.median(np.linalg.norm(project(Wp, R, C)[0] - q, axis=1)))
    poses.append((R.copy(), C.copy())); stats.append((idx[k], int(len(Wp)), round(rmse, 3)))
    if k % 40 == 0:
        pitch = math.degrees(math.asin(-(R[2] @ up)))
        print(k, idx[k], "n", len(Wp), "med_err_px", round(rmse, 3), "C", np.round(C, 2), "pitch", round(pitch, 1), flush=True)

# lissage + interpolation à toutes les images
Rs = Rot.from_matrix([p[0] for p in poses]); Cs = np.array([p[1] for p in poses])
win = min(len(idx) // 2 * 2 - 1, 15)
Cs_s = np.stack([savgol_filter(Cs[:, i], win, 2) for i in range(3)], 1) if win >= 5 else Cs
eul = np.unwrap(Rs.as_euler("zyx"), axis=0)
eul_s = np.stack([savgol_filter(eul[:, i], win, 2) for i in range(3)], 1) if win >= 5 else eul
Rs_s = Rot.from_euler("zyx", eul_s)
allf = np.arange(n)
sl = Slerp(idx, Rs_s)
Rfull = sl(allf)
Cfull = np.stack([np.interp(allf, idx, Cs_s[:, i]) for i in range(3)], 1)
out = {"fps": cap.get(5), "frames": n, "width": W0, "height": H0, "focal_px_work": f, "work_width": W,
       "R_world_to_cam": [m.tolist() for m in Rfull.as_matrix()], "C": Cfull.tolist(),
       "stats": stats}
json.dump(out, open(a.out, "w"))
print("OK ->", a.out)
