"""Playblast de contrôle : projette via la caméra du .blend (bpy) grille sol 20 m, route, voiture proxy, nuage de points sur le plan.
Usage: python3 -I playblast.py shot.blend plate.mp4 out.mp4"""
import sys, subprocess, numpy as np, cv2, bpy
from bpy_extras.object_utils import world_to_camera_view
blend, plate, out = sys.argv[1:4]
bpy.ops.wm.open_mainfile(filepath=blend); sc = bpy.context.scene; cam = sc.camera; car = bpy.data.objects["CAR_proxy_BMW_E46"]
pts = np.array([tuple(v.co) for v in bpy.data.objects["TRACKED_GROUND_POINTS"].data.vertices])[::6]
road = np.array([tuple(p.co)[:3] for p in bpy.data.objects["ROAD_centerline_from_car_path"].data.splines[0].points])
cv = np.array([tuple(v.co) for v in car.data.vertices]); edges = [(a.vertices[0], a.vertices[1]) for a in car.data.edges]
W, H = 1280, 720
def project(P, mw):
    inv = np.linalg.inv(mw); lo = (inv @ np.vstack([P.T, np.ones(len(P))]))[:3].T    # coords caméra Blender (x,y,-z avant)
    z = -lo[:,2]; f = cam.data.lens / cam.data.sensor_width * W
    return np.column_stack([W/2 + f*lo[:,0]/np.maximum(z,1e-6), H/2 - f*lo[:,1]/np.maximum(z,1e-6)]), z
grid = []
for k in range(-600, 601, 20):
    t = np.linspace(-600, 600, 600); grid.append(np.column_stack([t, np.full_like(t, k), np.zeros_like(t)])); grid.append(np.column_stack([np.full_like(t, k), t, np.zeros_like(t)]))
cap = cv2.VideoCapture(plate); pr = subprocess.Popen(["ffmpeg","-v","error","-y","-f","rawvideo","-pix_fmt","bgr24","-s","%dx%d"%(W,H),"-r","60000/1001","-i","-","-c:v","libx264","-crf","20","-pix_fmt","yuv420p",out], stdin=subprocess.PIPE)
for f in range(1, sc.frame_end+1):
    ok, im = cap.read()
    if not ok: break
    sc.frame_set(f); mw = np.array(cam.matrix_world); ov = im.copy()
    for g in grid:
        uv, z = project(g, mw); m = z > 0.5
        for a, b in zip(range(len(uv)-1), range(1, len(uv))):
            pass
        seg = np.where(m[:-1] & m[1:])[0]
        if len(seg): 
            for s in np.split(seg, np.where(np.diff(seg) > 1)[0]+1):
                p = uv[np.r_[s, s[-1]+1]]; cv2.polylines(ov, [p.astype(np.int32)[:, None]], False, (60,255,60), 1, cv2.LINE_AA)
    uv, z = project(road, mw); m = z > 0.5
    if m.sum() > 1: cv2.polylines(ov, [uv[m].astype(np.int32)[:, None]], False, (0,200,255), 2, cv2.LINE_AA)
    uv, z = project(pts, mw)
    for (x, y), zz in zip(uv, z):
        if zz > 0.5 and 0 <= x < W and 0 <= y < H: cv2.circle(ov, (int(x), int(y)), 1, (255,255,0), -1)
    cmw = np.array(car.matrix_world); wc = (cmw @ np.vstack([cv.T, np.ones(len(cv))]))[:3].T; uv, z = project(wc, mw)
    for a, b in edges:
        if z[a] > 0.5 and z[b] > 0.5: cv2.line(ov, tuple(uv[a].astype(int)), tuple(uv[b].astype(int)), (40,40,255), 2, cv2.LINE_AA)
    # horizon ligne
    out_im = cv2.addWeighted(ov, 0.8, im, 0.2, 0); cv2.putText(out_im, "frame %d" % f, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2, cv2.LINE_AA)
    pr.stdin.write(out_im.tobytes())
pr.stdin.close(); pr.wait(); print("ok", out)
