"""Construit la scène Blender (bpy) du plan : caméra trackée, sol, voiture proxy, route, nuage de points. 
Usage: python3 -I build_scene.py fin.npz ba.npz car2d.npy plate.mp4 out_dir [scale_m_per_unit]
Monde : Z haut, sol Z=0, mètres (échelle estimée à partir de la voiture : empattement/longueur d'une berline BMW Série 3 E46)."""
import sys, os, math, json, numpy as np, cv2, bpy
from mathutils import Matrix, Vector
from scipy.signal import savgol_filter
fin, ba, car2d, plate, outd = sys.argv[1:6]; S = float(sys.argv[6]) if len(sys.argv) > 6 else 0.14
os.makedirs(outd, exist_ok=True)
d = np.load(fin); K = d["K"]; rvs, tvs = d["rvs"], d["tvs"]; N = len(rvs); f = float(K[0,0])
Rw = np.array([cv2.Rodrigues(r)[0] for r in rvs])            # monde -> cam (OpenCV)
C = np.array([-R.T@t for R,t in zip(Rw, tvs)]) * S           # centres caméra en mètres
LAST_VIS = 1185                                               # voiture visible jusqu'ici (puis sous l'arbre)
# ---- voiture : 2D -> sol (plan Z = hauteur du centroïde) ----
c2 = np.load(car2d)[:, :2].copy()
c2[:LAST_VIS+1] = savgol_filter(c2[:LAST_VIS+1], 15, 2, axis=0)
hc = 0.85
def ground_pt(uv, i, z):
    r = Rw[i].T @ np.linalg.inv(K) @ np.array([uv[0], uv[1], 1.0]); t = (z - C[i][2]) / r[2]; return C[i] + r*t
P = np.array([ground_pt(c2[i], i, hc) for i in range(LAST_VIS+1)])
P = savgol_filter(P, 31, 2, axis=0)
v = np.gradient(P, axis=0)[:, :2] * (60000/1001)              # m/s
spd = np.linalg.norm(v, axis=1)
# extrapolation occultée : vitesse moyenne des 30 dernières images visibles
vend = np.median(np.gradient(P[LAST_VIS-40:LAST_VIS+1], axis=0), axis=0)
Pall = np.vstack([P, [P[-1] + vend*(k) for k in range(1, N-LAST_VIS)]])
yaw = np.zeros(N)
vv = savgol_filter(np.gradient(Pall, axis=0)[:, :2], 41, 2, axis=0)
for i in range(N): yaw[i] = math.atan2(vv[i,1], vv[i,0]) if np.linalg.norm(vv[i]) > 1e-4 else (yaw[i-1] if i else 0)
yaw = np.unwrap(yaw)
print("vitesse voiture m/s (début/milieu/fin visible): %.1f %.1f %.1f  -> km/h %.0f" % (spd[10], spd[LAST_VIS//2], spd[LAST_VIS-5], spd[10:LAST_VIS].mean()*3.6))
# ---- scène ----
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene; sc.render.fps = 60; sc.render.fps_base = 1.001
sc.frame_start, sc.frame_end = 1, N; sc.render.resolution_x, sc.render.resolution_y = 1280, 720; sc.render.resolution_percentage = 100
sc.unit_settings.system = 'METRIC'; sc.unit_settings.scale_length = 1.0
def coll(name):
    c = bpy.data.collections.new(name); sc.collection.children.link(c); return c
cC, cG, cL = coll("CAMERA_TRACK"), coll("LAYOUT"), coll("TRACK_DATA")
def link(o, c): c.objects.link(o); return o
# caméra
cam = bpy.data.cameras.new("trackCam"); cam.sensor_fit = 'HORIZONTAL'; cam.sensor_width = 36.0; cam.lens = f / 1280.0 * 36.0
cam.clip_start, cam.clip_end = 0.5, 20000
co = link(bpy.data.objects.new("trackCam", cam), cC); sc.camera = co; co.rotation_mode = 'XYZ'
cv2bl = np.diag([1.0, -1.0, -1.0]); prev = None
for i in range(N):
    Rb = Rw[i].T @ cv2bl; M = Matrix(Rb.tolist()).to_4x4(); M.translation = Vector(C[i].tolist())
    eul = M.to_euler('XYZ', prev) if prev is not None else M.to_euler('XYZ'); prev = eul
    co.location = M.translation; co.rotation_euler = eul
    co.keyframe_insert("location", frame=i+1); co.keyframe_insert("rotation_euler", frame=i+1)
for fc in co.animation_data.action.layers[0].strips[0].channelbags[0].fcurves if hasattr(co.animation_data.action, "layers") else co.animation_data.action.fcurves:
    for kp in fc.keyframe_points: kp.interpolation = 'LINEAR'
# fond vidéo dans la caméra
clip = bpy.data.movieclips.load(plate); cam.show_background_images = True
bg = cam.background_images.new(); bg.source = 'MOVIE_CLIP'; bg.clip = clip; bg.clip_user.use_render_undistorted = False; bg.frame_method = 'FIT'; bg.alpha = 1.0
bg.clip_user.proxy_render_size = 'FULL'
# sol : grille 20 m (60x60 cases)
import bmesh
bm = bmesh.new(); bmesh.ops.create_grid(bm, x_segments=60, y_segments=60, size=600.0)
me = bpy.data.meshes.new("ground_grid"); bm.to_mesh(me); bm.free()
g = link(bpy.data.objects.new("GROUND_Z0_grid20m", me), cG); g.display_type = 'WIRE'; g.hide_render = False
# nuage de points sol solvés (BA)
wx = np.load(ba)["wx"] * S
pc = bpy.data.meshes.new("track_points"); pc.from_pydata([tuple(p) for p in wx], [], []); link(bpy.data.objects.new("TRACKED_GROUND_POINTS", pc), cL)
# repère d'origine (position caméra à la dernière image = (0,0,h))
e = bpy.data.objects.new("ORIGIN_below_last_frame", None); e.empty_display_type = 'ARROWS'; e.empty_display_size = 10; link(e, cG)
# voiture proxy
L_, W_, H_ = 4.47, 1.74, 1.42
bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
for vtx in bm.verts: vtx.co = (vtx.co.x*L_, vtx.co.y*W_, vtx.co.z*H_ + H_/2)
cm = bpy.data.meshes.new("car_proxy"); bm.to_mesh(cm); bm.free()
car = link(bpy.data.objects.new("CAR_proxy_BMW_E46", cm), cG); car.color = (0.05, 0.12, 0.45, 1); car.rotation_mode = 'XYZ'
for i in range(N):
    car.location = (Pall[i,0], Pall[i,1], 0.0); car.rotation_euler = (0, 0, yaw[i])
    car.keyframe_insert("location", frame=i+1); car.keyframe_insert("rotation_euler", frame=i+1)
    car.hide_viewport = False
    if i >= LAST_VIS:   # occultée par l'arbre -> trajectoire extrapolée : marqueur custom
        pass
car["visible_until_frame"] = LAST_VIS + 1; car["note"] = "position extrapolee a vitesse constante apres la frame visible_until_frame (voiture sous l'arbre)"
# route : courbe suivant la trajectoire visible de la voiture
cu = bpy.data.curves.new("road_centerline", 'CURVE'); cu.dimensions = '3D'; sp = cu.splines.new('POLY'); idx = list(range(0, N, 15))
sp.points.add(len(idx)-1)
for k, i in enumerate(idx): sp.points[k].co = (Pall[i,0], Pall[i,1], 0.02, 1)
ro = link(bpy.data.objects.new("ROAD_centerline_from_car_path", cu), cG)
# métadonnées
sc["scale_m_per_unit"] = S; sc["focal_px_1280"] = f; sc["hfov_deg"] = math.degrees(2*math.atan(640/f)); sc["tracked_backward_from_frame"] = N
out_blend = os.path.join(outd, "shot_track_layout.blend"); bpy.ops.wm.save_as_mainfile(filepath=out_blend)
# exports caméra : .chan (Nuke/Houdini), json, fbx, abc
vfov = 2*math.degrees(math.atan(360/f))
with open(os.path.join(outd, "camera.chan"), "w") as fh:
    for i in range(N):
        M = Matrix(((Rw[i].T @ cv2bl).tolist())).to_4x4(); eu = M.to_euler('XYZ')
        fh.write("%d %.5f %.5f %.5f %.4f %.4f %.4f %.4f\n" % (i+1, C[i,0], C[i,1], C[i,2], *[math.degrees(a) for a in eu], vfov))
json.dump(dict(frames=N, fps=60000/1001, focal_px=f, hfov_deg=sc["hfov_deg"], scale_m_per_unit=S, cam_pos_m=C.tolist(),
               cam_R_world2cv=Rw.tolist(), car_pos_m=Pall.tolist(), car_yaw=yaw.tolist(), car_visible_until=LAST_VIS+1), open(os.path.join(outd, "camera_track.json"), "w"))
bpy.ops.object.select_all(action='DESELECT'); co.select_set(True); bpy.context.view_layer.objects.active = co
bpy.ops.export_scene.fbx(filepath=os.path.join(outd, "trackCam.fbx"), use_selection=True, bake_anim=True, object_types={'CAMERA'}, add_leaf_bones=False)
bpy.ops.wm.alembic_export(filepath=os.path.join(outd, "trackCam.abc"), selected=True, start=1, end=N)
print("OK", out_blend)
