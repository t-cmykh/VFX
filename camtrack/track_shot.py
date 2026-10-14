"""Camera tracking headless avec bpy.
Usage: python3 track_shot.py shot.mp4 out.blend [--start 1] [--end 0]
Pipeline: load clip -> detect features -> track -> clean -> solve -> setup scene.
"""
import sys, argparse, bpy

ap = argparse.ArgumentParser()
ap.add_argument("clip"); ap.add_argument("out")
ap.add_argument("--start", type=int, default=1)
ap.add_argument("--end", type=int, default=0, help="0 = tout le clip")
ap.add_argument("--sensor", type=float, default=6.17, help="largeur capteur mm (drone 1/2.3\")")
ap.add_argument("--focal", type=float, default=4.5, help="focale mm réelle")
ap.add_argument("--redetect", type=int, default=30, help="re-détection tous les N images")
ap.add_argument("--max_error", type=float, default=2.0)
a = ap.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:])

bpy.ops.wm.read_factory_settings(use_empty=False)
scn = bpy.context.scene
clip = bpy.data.movieclips.load(a.clip)
scn.active_clip = clip
n = clip.frame_duration
end = a.end or n
scn.frame_start, scn.frame_end = a.start, end
scn.render.fps = round(clip.fps) if clip.fps else 60
scn.render.resolution_x, scn.render.resolution_y = clip.size
scn.render.resolution_percentage = 100

cam = clip.tracking.camera
cam.sensor_width = a.sensor
cam.focal_length = a.focal
cam.units = 'MILLIMETERS'
s = clip.tracking.settings
s.default_motion_model = 'Affine'
s.default_pattern_size = 21
s.default_search_size = 121
s.default_correlation_min = 0.75
s.default_margin = 10
s.use_default_brute = True
s.use_default_normalization = True
s.use_keyframe_selection = True
s.use_tripod_solver = False
clip.tracking.settings.refine_intrinsics_focal_length = True
clip.tracking.settings.refine_intrinsics_radial_distortion = True

def clip_ctx():
    for win in bpy.context.window_manager.windows:
        for ar in win.screen.areas:
            if ar.type in {'TIMELINE', 'VIEW_3D', 'DOPESHEET_EDITOR'} or True:
                ar.type = 'CLIP_EDITOR'
                ar.spaces.active.clip = clip
                reg = next(r for r in ar.regions if r.type == 'WINDOW')
                return dict(window=win, area=ar, region=reg, space_data=ar.spaces.active)
    raise RuntimeError("pas d'area disponible")

ctx = clip_ctx()
with bpy.context.temp_override(**ctx):
    scn.frame_set(a.start)
    bpy.ops.clip.detect_features(threshold=0.1, min_distance=60, margin=20, placement='FRAME')
    print("features détectées:", len(clip.tracking.tracks))
    bpy.ops.clip.select_all(action='SELECT')
    bpy.ops.clip.track_markers(backwards=False, sequence=True)
    # re-détection périodique pour compenser les tracks perdus
    for f in range(a.start + a.redetect, end, a.redetect):
        scn.frame_set(f)
        bpy.ops.clip.select_all(action='DESELECT')
        bpy.ops.clip.detect_features(threshold=0.1, min_distance=60, margin=20, placement='FRAME')
        bpy.ops.clip.track_markers(backwards=False, sequence=True)
    scn.frame_set(a.start)
    bpy.ops.clip.clean_tracks(frames=25, error=0.0, action='DELETE_TRACK')
    print("tracks après nettoyage:", len(clip.tracking.tracks))
    bpy.ops.clip.solve_camera()
    rec = clip.tracking.reconstruction
    print("solve ok:", rec.is_valid, "erreur moyenne px:", rec.average_error)
    bpy.ops.clip.select_all(action='DESELECT')
    bpy.ops.clip.clean_tracks(frames=0, error=a.max_error, action='DELETE_TRACK')
    bpy.ops.clip.solve_camera()
    rec = clip.tracking.reconstruction
    print("re-solve:", rec.is_valid, "erreur:", rec.average_error, "tracks:", len(clip.tracking.tracks))
    bpy.ops.clip.setup_tracking_scene()

bpy.ops.wm.save_as_mainfile(filepath=a.out)
print("sauvé", a.out)
