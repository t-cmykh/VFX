"""Solve caméra par fenêtres glissantes (bpy) + raccord par points 3D communs.
Usage: python3 solve_windows.py tracked.blend out.json [--win 200] [--step 100]
Sortie JSON: par image, matrice caméra->monde 4x4 (repère Blender, z-up), focale, nuage de points.
"""
import sys, json, argparse
import numpy as np, bpy
from mathutils import Matrix

ap = argparse.ArgumentParser()
ap.add_argument("blend"); ap.add_argument("out")
ap.add_argument("--win", type=int, default=200); ap.add_argument("--step", type=int, default=100)
ap.add_argument("--max_error", type=float, default=1.5)
a = ap.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:])

bpy.ops.wm.open_mainfile(filepath=a.blend)
scn = bpy.context.scene; clip = bpy.data.movieclips[0]; trk = clip.tracking
n = clip.frame_duration; st = trk.settings
win = bpy.context.window_manager.windows[0]; ar = win.screen.areas[0]
ar.type = 'CLIP_EDITOR'; ar.spaces.active.clip = clip
ctx = dict(window=win, area=ar, region=next(r for r in ar.regions if r.type == 'WINDOW'), space_data=ar.spaces.active)
st.use_keyframe_selection = True
W, H = clip.size

def umeyama(src, dst):
    mu_s, mu_d = src.mean(0), dst.mean(0)
    S, D = src - mu_s, dst - mu_d
    U, sig, Vt = np.linalg.svd(D.T @ S / len(src))
    d = np.sign(np.linalg.det(U) * np.linalg.det(Vt)); E = np.diag([1, 1, d])
    R = U @ E @ Vt; s = (sig * np.diag(E)).sum() / (S ** 2).sum() * len(src)
    t = mu_d - s * R @ mu_s
    return s, R, t

def sim_ransac(src, dst, iters=500, thr=None):
    rng = np.random.default_rng(0); best = None
    scale = np.median(np.linalg.norm(dst - dst.mean(0), axis=1)) + 1e-9
    thr = thr or 0.05 * scale
    for _ in range(iters):
        i = rng.choice(len(src), 4, replace=False)
        try: s, R, t = umeyama(src[i], dst[i])
        except Exception: continue
        err = np.linalg.norm((s * (R @ src.T)).T + t - dst, axis=1)
        inl = err < thr
        if best is None or inl.sum() > best[0].sum(): best = (inl, )
    inl = best[0]
    s, R, t = umeyama(src[inl], dst[inl]); return s, R, t, int(inl.sum())

wins = []
starts = list(range(1, n - 60, a.step))
for k, s0 in enumerate(starts):
    e0 = min(s0 + a.win - 1, n); scn.frame_start, scn.frame_end = s0, e0
    # les features sont ceux de la fenêtre: on déselectionne rien, le solver ignore les tracks hors plage
    st.refine_intrinsics_focal_length = (k == 0)
    st.refine_intrinsics_radial_distortion = False
    try:
        with bpy.context.temp_override(**ctx):
            bpy.ops.clip.solve_camera()
            bpy.ops.clip.select_all(action='DESELECT')
            bpy.ops.clip.clean_tracks(frames=0, error=a.max_error, action='SELECT')
            bpy.ops.clip.delete_track()
            bpy.ops.clip.solve_camera()
    except RuntimeError as ex:
        print("fenêtre", s0, e0, "échec:", ex); continue
    r = trk.reconstruction
    cams = {}
    for f in range(s0, e0 + 1):
        c = r.cameras.find_frame(frame=f)
        if c: cams[f] = [list(row) for row in c.matrix]
    pts = {t.name: list(t.bundle) for t in trk.tracks if t.has_bundle}
    print(f"fenêtre {s0}-{e0}: valid={r.is_valid} err={r.average_error:.3f} images={len(cams)} pts={len(pts)} focal={trk.camera.focal_length:.3f}", flush=True)
    if r.is_valid and len(cams) > 20: wins.append(dict(s=s0, e=e0, cams=cams, pts=pts, err=r.average_error))
json.dump(dict(wins=wins, focal=trk.camera.focal_length, sensor=trk.camera.sensor_width, size=[W, H], fps=clip.fps), open(a.out, "w"))
print("OK", a.out)
