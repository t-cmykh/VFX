"""Superpose la route (bords ±road_w/2) projetée par la caméra ajustée. Usage: viz_layout.py shot.mp4 layout.json out.jpg [pass1]"""
import sys, json, math, cv2, numpy as np
from scipy.interpolate import CubicSpline
from scipy.spatial.transform import Rotation as Rot
d = json.load(open(sys.argv[2])); use1 = len(sys.argv) > 4
W, H = d["work_size"]; f = d["focal_px"]; HW = d["road_w"] / 2
nodes = np.array(d["road_nodes"]); sp = CubicSpline(nodes, d["pass1"]["road_x"] if use1 else d["road_x"])
cap = cv2.VideoCapture(sys.argv[1]); kf = d["kf"]; pick = np.linspace(0, len(kf) - 1, 8).astype(int)
tiles = []; i = 0; want = {kf[k]: k for k in pick}
while True:
    ok, im = cap.read()
    if not ok: break
    if i in want:
        k = want[i]; im = cv2.resize(im, (W, H))
        if use1: C = np.array(d["pass1"]["C"][k]); R = Rot.from_rotvec(d["pass1"]["w"][k]).as_matrix()
        else: C = np.array(d["C"][i]); R = np.array(d["R_cam_to_world"][i])
        ys = np.linspace(C[1] - 10, C[1] + 250, 400)
        for sgn, col in ((-1, (0, 0, 255)), (1, (0, 255, 0))):
            X = np.stack([sp(ys) + sgn * HW, ys, np.zeros_like(ys)], 1)
            x = (X - C) @ R; z = x[:, 2]; ok2 = z > 0.1
            uv = np.stack([f * x[:, 0] / np.where(ok2, z, 1) + W / 2, f * x[:, 1] / np.where(ok2, z, 1) + H / 2], 1)
            pts = uv[ok2].astype(np.int32)
            if len(pts) > 1: cv2.polylines(im, [pts.reshape(-1, 1, 2)], False, col, 2)
        cv2.putText(im, f"f{i} h={C[2]:.1f} y={C[1]:.0f}", (8, 20), 0, 0.6, (255, 255, 0), 2)
        tiles.append(im)
    i += 1
rows = [np.hstack(tiles[j:j + 4]) for j in (0, 4)]
cv2.imwrite(sys.argv[3], np.vstack(rows))
