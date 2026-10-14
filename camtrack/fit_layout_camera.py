"""Caméra 3D calée sur un layout (route de largeur fixe sur sol plan z=0).

Pour chaque image clé : les pixels du bord du masque de route, rétroprojetés au sol, doivent
tomber sur les bords de la bande de route (largeur ROAD_W) ; le flux optique entre images clés
(points du sol) relie les poses. Passe 1 : image par image. Passe 2 : optimisation globale
(poses + axe de la route c(y) + focale).

Usage: python3 fit_layout_camera.py shot.mp4 layout.json [--kf 8] [--road_w 3.5] [--focal_px 425]
Repère: x droite, y le long de la route, z haut. Caméra 0 à y=0.
"""
import argparse, json, math, time
import cv2, numpy as np
from scipy.optimize import least_squares
from scipy.interpolate import CubicSpline
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation as Rot
from road_mask import road_mask

ap = argparse.ArgumentParser()
ap.add_argument("clip"); ap.add_argument("out")
ap.add_argument("--kf", type=int, default=8, help="une image clé toutes les N images")
ap.add_argument("--road_w", type=float, default=3.5, help="largeur de la route (m) = échelle")
ap.add_argument("--focal_px", type=float, default=425, help="focale à 640 px de large")
ap.add_argument("--pitch0", type=float, default=55)
ap.add_argument("--h0", type=float, default=30)
ap.add_argument("--node", type=float, default=20, help="pas des noeuds de l'axe de route (m)")
ap.add_argument("--ymax", type=float, default=700)
ap.add_argument("--speed", type=float, default=6, help="vitesse d'avance initiale (m/s)")
ap.add_argument("--maxit", type=int, default=40)
a = ap.parse_args()

W, H = 640, 360; CX, CY = W / 2, H / 2; HW = a.road_w / 2

# ------------------------------------------------------------------ données
cap = cv2.VideoCapture(a.clip); fps = cap.get(5)
grays, masks, kfi = [], [], []
i = 0
while True:
    ok, im = cap.read()
    if not ok: break
    if i % a.kf == 0:
        im = cv2.resize(im, (W, H), interpolation=cv2.INTER_AREA)
        grays.append(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)); masks.append(road_mask(im)); kfi.append(i)
    i += 1
nfr = i; K = len(kfi)
print("images", nfr, "clés", K, flush=True)

rng = np.random.default_rng(1)
edge_pts, in_pts = [], []
for m in masks:
    m2 = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    cs, _ = cv2.findContours(m2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    P = np.concatenate([c.reshape(-1, 2) for c in cs]) if cs else np.zeros((0, 2), int)
    keep = (P[:, 0] > 6) & (P[:, 0] < W - 7) & (P[:, 1] > 6) & (P[:, 1] < H - 7) if len(P) else np.zeros(0, bool)
    P = P[keep]
    if len(P) > 150: P = P[rng.choice(len(P), 150, replace=False)]
    edge_pts.append(P.astype(np.float64))
    er = cv2.erode(m2, np.ones((13, 13), np.uint8)); ys, xs = np.nonzero(er)
    if len(xs) > 80:
        s = rng.choice(len(xs), 80, replace=False); Q = np.stack([xs[s], ys[s]], 1).astype(np.float64)
    else: Q = np.stack([xs, ys], 1).astype(np.float64)
    in_pts.append(Q)

def lk(g0, g1):
    p0 = cv2.goodFeaturesToTrack(g0, 500, 0.01, 10)
    if p0 is None: return np.zeros((0, 2)), np.zeros((0, 2))
    p1, s1, _ = cv2.calcOpticalFlowPyrLK(g0, g1, p0, None, winSize=(31, 31), maxLevel=4)
    pb, s2, _ = cv2.calcOpticalFlowPyrLK(g1, g0, p1, None, winSize=(31, 31), maxLevel=4)
    p0, p1, pb = p0.reshape(-1, 2), p1.reshape(-1, 2), pb.reshape(-1, 2)
    g = (s1[:, 0] == 1) & (s2[:, 0] == 1) & (np.linalg.norm(pb - p0, axis=1) < 0.7)
    return p0[g].astype(np.float64), p1[g].astype(np.float64)
flows = [lk(grays[k], grays[k + 1]) for k in range(K - 1)]
print("flux: points/paire médian", int(np.median([len(f[0]) for f in flows])), flush=True)

# ------------------------------------------------------------------ modèle
nodes = np.arange(-a.node, a.ymax + a.node, a.node); NN = len(nodes)
i0 = int(np.argmin(np.abs(nodes)))          # noeud pinné c=0

def road_fn(cv):
    sp = CubicSpline(nodes, cv, extrapolate=True)
    return sp, sp.derivative()

def rot_cw(w): return Rot.from_rotvec(w).as_matrix()   # cam -> monde

def rays(px, f):
    return np.stack([(px[:, 0] - CX) / f, (px[:, 1] - CY) / f, np.ones(len(px))], 1)

def ground(px, C, R, f):
    rw = rays(px, f) @ R.T
    ok = rw[:, 2] < -1e-3
    t = np.where(ok, -C[2] / np.where(ok, rw[:, 2], -1), 0)
    return C + rw * t[:, None], t, ok

def project(X, C, R, f):
    x = (X - C) @ R               # monde -> cam (R^T)
    z = x[:, 2]; ok = z > 0.05
    zz = np.where(ok, z, 1)
    return np.stack([f * x[:, 0] / zz + CX, f * x[:, 1] / zz + CY], 1), ok

PEN = 40.0
def res_road(k, C, R, f, sp, dsp):
    out = []
    for P, inside in ((edge_pts[k], False), (in_pts[k], True)):
        if len(P) == 0: continue
        X, t, ok = ground(P, C, R, f)
        lat = (X[:, 0] - sp(X[:, 1])) / np.sqrt(1 + dsp(X[:, 1]) ** 2)
        e = np.maximum(np.abs(lat) - HW, 0) if inside else (np.abs(lat) - HW)
        r = np.where(ok, e * f / np.maximum(t, 1e-3), PEN)
        out.append(np.clip(r, -PEN, PEN) * (0.5 if inside else 1.0))
    return np.concatenate(out) if out else np.zeros(0)

def res_flow(k, C0, R0, C1, R1, f):
    p0, p1 = flows[k]
    if len(p0) == 0: return np.zeros(0)
    X, t, ok = ground(p0, C0, R0, f)
    uv, ok2 = project(X, C1, R1, f)
    r = np.linalg.norm(uv - p1, axis=1)
    return np.where(ok & ok2, r, PEN)

def init_pose():
    p = math.radians(a.pitch0)
    xc = np.array([1.0, 0, 0]); zc = np.array([0, math.cos(p), -math.sin(p)]); yc = np.cross(zc, xc)
    return np.array([0, 0, a.h0]), Rot.from_matrix(np.stack([xc, yc, zc], 1)).as_rotvec()

# ------------------------------------------------------------------ horizon (ciel/terre) par image clé
def detect_horizon(bgr):
    """Retourne des points (u, v) de la ligne d'horizon (bas du ciel) ou None."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue = hsv[..., 0]
    sky = (((hsv[..., 1] < 25) | ((hsv[..., 1] < 70) & (hue > 85) & (hue < 135))) & (hsv[..., 2] > 150)).astype(np.uint8)
    sky[:, :] = cv2.morphologyEx(sky, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    pts = []
    for u0 in range(16, W - 16, 32):
        col = sky[:, u0 - 8:u0 + 8].mean(1) > 0.6
        if not col[0] and not col[3]: continue          # pas de ciel en haut de cette colonne
        nz = np.nonzero(~col)[0]
        if len(nz) == 0: continue
        v = int(nz[0])
        if 4 < v < H - 20: pts.append((u0, v))
    return np.array(pts, dtype=np.float64) if len(pts) >= 8 else None

hor_pts = []
cap = cv2.VideoCapture(a.clip); i = 0; kset = set(kfi)
while True:
    ok, im = cap.read()
    if not ok: break
    if i in kset:
        P = detect_horizon(cv2.resize(im, (W, H), interpolation=cv2.INTER_AREA))
        if P is not None:                               # ligne robuste (médiane sur les colonnes) + rejet des aberrants
            c = np.polyfit(P[:, 0], P[:, 1], 1); r = np.abs(np.polyval(c, P[:, 0]) - P[:, 1])
            P = P[r < max(6.0, 2 * np.median(r))]
        hor_pts.append(P if P is not None and len(P) >= 8 else None)
    i += 1
nh = sum(p is not None for p in hor_pts)
first_h = next((k for k, p in enumerate(hor_pts) if p is not None), K)
print("horizon visible sur", nh, "images clés (à partir de", kfi[first_h] if first_h < K else None, ")", flush=True)

def res_horizon(k, R, f):
    P = hor_pts[k]
    if P is None: return np.zeros(0)
    n = R.T @ np.array([0.0, 0.0, 1.0])                 # haut du monde dans le repère caméra
    v = CY - (n[0] * (P[:, 0] - CX) + n[2] * f) / n[1] if abs(n[1]) > 1e-6 else np.full(len(P), 1e3)
    return np.clip(v - P[:, 1], -PEN, PEN)

# ------------------------------------------------------------------ initialisation paramétrique
pit_h = np.full(K, np.nan)
for k in range(K):
    P = hor_pts[k]
    if P is not None:
        c = np.polyfit(P[:, 0], P[:, 1], 1); vh = np.polyval(c, CX)
        pit_h[k] = math.atan((CY - vh) / a.focal_px)
t_k = np.array(kfi, dtype=float)
good = ~np.isnan(pit_h)
if good.sum() >= 2:
    t_g = t_k[good]; p_g = pit_h[good]
    slope = np.polyfit(t_g, p_g, 1)                       # extrapolation linéaire vers le début
    pitch_k = np.where(good, pit_h, np.polyval(slope, t_k))
    pitch_k = np.clip(pitch_k, math.radians(2), math.radians(85))
else:
    pitch_k = np.radians(np.linspace(a.pitch0, 10, K))
print("inclinaison init (deg): début", round(math.degrees(pitch_k[0]), 1), "fin", round(math.degrees(pitch_k[-1]), 1), flush=True)
speed = a.speed
poses = []
for k in range(K):
    p = pitch_k[k]
    xc = np.array([1.0, 0, 0]); zc = np.array([0, math.cos(p), -math.sin(p)]); yc = np.cross(zc, xc)
    poses.append((np.array([0.0, speed * t_k[k] / fps, a.h0]), Rot.from_matrix(np.stack([xc, yc, zc], 1)).as_rotvec()))
f_cur = a.focal_px
cv_init = np.zeros(NN)

# ------------------------------------------------------------------ passe 2 : globale
def unpack(x):
    Cs = x[:3 * K].reshape(K, 3); ws = x[3 * K:6 * K].reshape(K, 3)
    cvv = np.zeros(NN); free = [j for j in range(NN) if j != i0]; cvv[free] = x[6 * K:6 * K + NN - 1]
    return Cs, ws, cvv, x[-1]
x0 = np.concatenate([np.array([p[0] for p in poses]).ravel(), np.array([p[1] for p in poses]).ravel(),
                     np.delete(cv_init, i0), [f_cur]])
WS = 20.0   # lissage
def fun(x):
    Cs, ws, cvv, f = unpack(x)
    sp, dsp = road_fn(cvv)
    out = []
    Rs = [rot_cw(w) for w in ws]
    for k in range(K):
        out.append(res_road(k, Cs[k], Rs[k], f, sp, dsp))
        out.append(res_horizon(k, Rs[k], f) * 1.5)
        if k < K - 1: out.append(res_flow(k, Cs[k], Rs[k], Cs[k + 1], Rs[k + 1], f))
    d2C = Cs[2:] - 2 * Cs[1:-1] + Cs[:-2]; d2w = ws[2:] - 2 * ws[1:-1] + ws[:-2]
    out.append(WS * d2C.ravel()); out.append(WS * 20 * d2w.ravel())
    out.append(np.array([0.2 * (cvv[j + 1] - 2 * cvv[j] + cvv[j - 1]) for j in range(1, NN - 1)]) * 3)
    out.append(np.array([Cs[0, 1] * 100.0]))             # jauge: caméra 0 à y=0
    return np.concatenate(out)
t0 = time.time(); r_init = fun(x0); print("globale: résidus", len(r_init), "coût init", round(float(np.sum(np.minimum(r_init ** 2, 100))), 0), flush=True)
# structure creuse
nres_k = []
off = 0; blocks = []
for k in range(K):
    n1 = len(res_road(k, poses[0][0], rot_cw(poses[0][1]), f_cur, *road_fn(cv_init))) + len(res_horizon(k, np.eye(3), f_cur))
    n2 = len(flows[k][0]) if k < K - 1 else 0
    blocks.append((off, n1, n2)); off += n1 + n2
nsm = (K - 2) * 3 * 2; ncv = NN - 2
A = lil_matrix((len(r_init), len(x0)), dtype=int)
cvcols = list(range(6 * K, 6 * K + NN - 1)); fcol = len(x0) - 1
for k, (o, n1, n2) in enumerate(blocks):
    cols = list(range(3 * k, 3 * k + 3)) + list(range(3 * K + 3 * k, 3 * K + 3 * k + 3))
    for c in cols + cvcols + [fcol]: A[o:o + n1, c] = 1
    if n2:
        cols2 = list(range(3 * (k + 1), 3 * (k + 1) + 3)) + list(range(3 * K + 3 * (k + 1), 3 * K + 3 * (k + 1) + 3))
        for c in cols + cols2 + [fcol]: A[o + n1:o + n1 + n2, c] = 1
o = off
for j in range(K - 2):
    for d in range(3):
        for kk in (j, j + 1, j + 2):
            A[o + 3 * j + d, 3 * kk + d] = 1; A[o + 3 * (K - 2) + 3 * j + d, 3 * K + 3 * kk + d] = 1
o += 2 * 3 * (K - 2)
for j in range(NN - 2):
    for c in cvcols: A[o + j, c] = 1
A[o + NN - 2, 1] = 1
assert o + NN - 1 == len(r_init), (o + NN - 1, len(r_init))
sol = least_squares(fun, x0, jac_sparsity=A.tocsr(), loss="soft_l1", f_scale=3.0, method="trf", x_scale="jac",
                    max_nfev=a.maxit, verbose=2)
print("passe 2 terminée en", round(time.time() - t0), "s ; coût", round(float(sol.cost), 1), flush=True)
Cs, ws, cvv, f = unpack(sol.x)

# ------------------------------------------------------------------ sortie : toutes les images
from scipy.spatial.transform import Slerp
allf = np.arange(nfr); kfa = np.array(kfi)
Rk = Rot.from_rotvec(ws)
Rfull = Slerp(kfa, Rk)(np.clip(allf, kfa[0], kfa[-1]))
Cfull = np.stack([np.interp(allf, kfa, Cs[:, d]) for d in range(3)], 1)
# focale en mm sur un capteur de 36 mm de large (convention Blender) : f_mm = f_px / W * 36
out = dict(pass1=dict(C=[p[0].tolist() for p in poses], w=[p[1].tolist() for p in poses], road_x=cv_init.tolist()), fps=fps, frames=nfr, work_size=[W, H], focal_px=float(f), road_w=a.road_w,
           road_nodes=nodes.tolist(), road_x=cvv.tolist(), kf=kfi,
           C=Cfull.tolist(), R_cam_to_world=[m.tolist() for m in Rfull.as_matrix()])
json.dump(out, open(a.out, "w"))
print("focale px", round(float(f), 1), "-> HFOV", round(math.degrees(2 * math.atan(W / 2 / f)), 1), "deg ; hauteur ini/fin", round(Cs[0, 2], 1), round(Cs[-1, 2], 1))
print("OK ->", a.out)
