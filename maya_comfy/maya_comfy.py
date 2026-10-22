"""Maya -> ComfyUI : apercu image (Qwen) de la frame courante, historique, selection des images,
puis apercu anim (LTX) guide par les images selectionnees et le playblast de la plage de temps.

Dans Maya (Script Editor, onglet Python) :
    import sys; sys.path.append(r"C:/chemin/vers/VFX/maya_comfy")
    import maya_comfy; maya_comfy.show()
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager

from maya import cmds, mel
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import comfy_client  # noqa: E402
import comfy_history  # noqa: E402
import comfy_workflow as cwf  # noqa: E402

DEFAULT_CONFIG = {
    "server": "127.0.0.1:8188",
    "workflow_image": "workflows/qwen_image_api.json",
    "workflow_anim": "workflows/ltx_anim_api.json",
    "image_long_side": 1024,
    "image_size_multiple": 16,
    "anim_long_side": 512,
    "anim_size_multiple": 32,
    "anim_frame_step": 8,
    "anim_max_frames": 49,
    "free_vram_between_models": True,
    "ffmpeg": "ffmpeg",
    "poll_seconds": 1.0,
    "timeout_seconds": 3600,
    "open_anim_when_done": True,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    path = os.path.join(HERE, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    for key in ("workflow_image", "workflow_anim"):
        if not os.path.isabs(cfg[key]):
            cfg[key] = os.path.join(HERE, cfg[key])
    return cfg


# --- scene Maya -----------------------------------------------------------------

def scene_fps():
    try:
        return float(mel.eval("currentTimeUnitToFPS()"))
    except RuntimeError:
        return 24.0


def history_root():
    ws = cmds.workspace(q=True, rootDirectory=True)
    scene = cmds.file(q=True, sceneName=True, shortName=True) or "sans_titre"
    return os.path.join(ws, "comfy", os.path.splitext(scene)[0])


def render_resolution():
    return cmds.getAttr("defaultResolution.width"), cmds.getAttr("defaultResolution.height")


def model_panel():
    panel = cmds.getPanel(withFocus=True)
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
        return panel
    for panel in cmds.getPanel(visiblePanels=True) or []:
        if cmds.getPanel(typeOf=panel) == "modelPanel":
            return panel
    raise RuntimeError("Aucun viewport visible pour le playblast.")


def camera_transform(cam):
    if cmds.nodeType(cam) == "camera":
        return cmds.listRelatives(cam, parent=True, fullPath=False)[0]
    return cam


def camera_shape(cam):
    if cmds.nodeType(cam) == "camera":
        return cam
    return cmds.listRelatives(cam, shapes=True, type="camera")[0]


# elements masques pendant les playblasts (les image planes Qwen en particulier)
_HIDDEN_IN_PLAYBLAST = ("grid", "headsUpDisplay", "manipulators", "nurbsCurves", "joints", "ikHandles",
                        "locators", "cameras", "lights", "deformers", "dimensions", "handles", "pivots",
                        "follicles", "dynamics", "motionTrails", "imagePlane", "selectionHiliteDisplay")


@contextmanager
def clean_viewport(panel, camera):
    saved = {}
    for flag in _HIDDEN_IN_PLAYBLAST:
        try:
            saved[flag] = cmds.modelEditor(panel, q=True, **{flag: True})
            cmds.modelEditor(panel, e=True, **{flag: False})
        except (TypeError, RuntimeError):
            pass
    old_cam = cmds.modelPanel(panel, q=True, camera=True)
    selection = cmds.ls(selection=True)
    cmds.select(clear=True)
    switch = camera and camera_transform(camera) != camera_transform(old_cam)
    if switch:
        cmds.lookThru(panel, camera)
    try:
        yield
    finally:
        if switch:
            cmds.lookThru(panel, old_cam)
        for flag, value in saved.items():
            try:
                cmds.modelEditor(panel, e=True, **{flag: value})
            except (TypeError, RuntimeError):
                pass
        if selection:
            cmds.select([s for s in selection if cmds.objExists(s)], replace=True)


def playblast(out_dir, name, start, end, size, panel, camera):
    """Sequence PNG name.####.png ; renvoie la liste des fichiers."""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, name)
    with clean_viewport(panel, camera):
        cmds.setFocus(panel)
        cmds.playblast(filename=base, format="image", compression="png", startTime=start, endTime=end,
                       forceOverwrite=True, viewer=False, showOrnaments=False, offScreen=True, percent=100,
                       quality=100, widthHeight=size, framePadding=4, clearCache=True)
    files = ["%s.%04d.png" % (base, f) for f in range(int(start), int(end) + 1)]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        raise RuntimeError("Playblast incomplet, fichier absent : %s" % missing[0])
    return files


def depth_range(camera, frames):
    """Distances proche / lointaine des objets vus par la camera (boite englobante), sur quelques frames."""
    meshes = cmds.ls(type="mesh", noIntermediate=True, long=True)
    near_clip = cmds.getAttr(camera_shape(camera) + ".nearClipPlane")
    if not meshes:
        return near_clip, near_clip + 100.0
    current = cmds.currentTime(q=True)
    depths = []
    try:
        for f in frames:
            cmds.currentTime(f, update=True)
            x0, y0, z0, x1, y1, z1 = cmds.exactWorldBoundingBox(meshes)
            m = cmds.getAttr(camera_transform(camera) + ".worldInverseMatrix[0]")
            for x in (x0, x1):
                for y in (y0, y1):
                    for z in (z0, z1):
                        depths.append(-(x * m[2] + y * m[6] + z * m[10] + m[14]))
    finally:
        cmds.currentTime(current, update=True)
    near, far = max(near_clip, min(depths)), max(depths)
    return near, max(far, near + 1e-3)


@contextmanager
def depth_shading(near, far):
    """Remplace temporairement tous les materiaux par une profondeur : proche = blanc, loin = noir."""
    saved = [(sg, cmds.sets(sg, q=True)) for sg in cmds.ls(type="shadingEngine")]
    saved = [(sg, members) for sg, members in saved if members]
    shader = cmds.shadingNode("surfaceShader", asShader=True, name="mayaComfy_depth")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="mayaComfy_depthSG")
    info = cmds.shadingNode("samplerInfo", asUtility=True, name="mayaComfy_depthInfo")
    rng = cmds.shadingNode("setRange", asUtility=True, name="mayaComfy_depthRange")
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader")
    cmds.connectAttr(info + ".pointCameraZ", rng + ".valueX")      # z camera negatif devant l'objectif
    cmds.setAttr(rng + ".oldMinX", -far)
    cmds.setAttr(rng + ".oldMaxX", -near)
    cmds.setAttr(rng + ".maxX", 1.0)
    for channel in "RGB":
        cmds.connectAttr(rng + ".outValueX", shader + ".outColor" + channel)
    cm_enabled = cmds.colorManagementPrefs(q=True, cmEnabled=True)
    cmds.colorManagementPrefs(e=True, cmEnabled=False)                  # valeurs brutes, pas de view transform
    meshes = cmds.ls(type="mesh", noIntermediate=True, long=True)
    if meshes:
        cmds.sets(meshes, e=True, forceElement=sg)
    try:
        yield
    finally:
        for engine, members in saved:
            cmds.sets(members, e=True, forceElement=engine)
        cmds.colorManagementPrefs(e=True, cmEnabled=cm_enabled)
        cmds.delete(shader, sg, info, rng)


def playblast_depth(out_dir, name, start, end, size, panel, camera):
    cam = camera or cmds.modelPanel(panel, q=True, camera=True)
    near, far = depth_range(cam, sorted({start, (start + end) // 2, end}))
    with depth_shading(near, far):
        return playblast(out_dir, name, start, end, size, panel, cam)


def find_ffmpeg(cfg):
    exe = cfg.get("ffmpeg") or "ffmpeg"
    return exe if os.path.isfile(exe) else shutil.which(exe)


def encode_video(ffmpeg, first_file, start, fps, out):
    pattern = first_file[:-len("%04d.png" % start)] + "%04d.png"
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-framerate", "%g" % fps, "-start_number", str(start),
           "-i", pattern, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", out]
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}       # pas de console sous Windows
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode:
        raise RuntimeError("ffmpeg a echoue :\n" + p.stderr[-1500:])
    return out


def resize_image(src, size, dst):
    img = QtGui.QImage(src)
    if img.isNull():
        raise RuntimeError("Image illisible : %s" % src)
    img = img.scaled(size[0], size[1], QtCore.Qt.KeepAspectRatioByExpanding, QtCore.Qt.SmoothTransformation)
    x, y = (img.width() - size[0]) // 2, (img.height() - size[1]) // 2
    if not img.copy(x, y, size[0], size[1]).save(dst):
        raise RuntimeError("Ecriture impossible : %s" % dst)
    return dst


def open_file(path):
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def _opt(name, default):
    return cmds.optionVar(q=name) if cmds.optionVar(exists=name) else default


# --- interface ------------------------------------------------------------------

class _Signals(QtCore.QObject):
    status = QtCore.Signal(str)
    finished = QtCore.Signal(object)


class MayaComfyWindow(MayaQWidgetDockableMixin, QtWidgets.QWidget):
    OBJECT_NAME = "mayaComfyWindow"

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setWindowTitle("Maya > ComfyUI")
        self.cfg = load_config()
        self.history = comfy_history.History(history_root())
        self.anims = comfy_history.History(os.path.join(history_root(), "anim"))
        self.signals = _Signals()
        self.signals.status.connect(self._set_status)
        self.signals.finished.connect(self._job_finished)
        self._cancel = threading.Event()
        self._busy = False
        self._last_kind = None
        self._build()
        self._refresh_cameras()
        self._refresh_lists()

    # construction --------------------------------------------------------------
    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)

        row = QtWidgets.QHBoxLayout()
        self.server = QtWidgets.QLineEdit(_opt("mayaComfy_server", self.cfg["server"]))
        test = QtWidgets.QPushButton("Tester")
        test.clicked.connect(self._test_server)
        row.addWidget(QtWidgets.QLabel("Serveur"))
        row.addWidget(self.server, 1)
        row.addWidget(test)
        lay.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        self.camera = QtWidgets.QComboBox()
        refresh = QtWidgets.QPushButton("Actualiser")
        refresh.clicked.connect(lambda: (self._refresh_cameras(), self._refresh_lists()))
        row.addWidget(QtWidgets.QLabel("Caméra"))
        row.addWidget(self.camera, 1)
        row.addWidget(refresh)
        lay.addLayout(row)

        # image (Qwen)
        box = QtWidgets.QGroupBox("Image (Qwen) - frame courante")
        form = QtWidgets.QFormLayout(box)
        self.prompt = QtWidgets.QPlainTextEdit(_opt("mayaComfy_prompt", ""))
        self.prompt.setFixedHeight(60)
        self.negative = QtWidgets.QLineEdit(_opt("mayaComfy_negative", ""))
        form.addRow("Prompt", self.prompt)
        form.addRow("Négatif", self.negative)
        row = QtWidgets.QHBoxLayout()
        self.seed = QtWidgets.QSpinBox()
        self.seed.setRange(0, 2 ** 31 - 1)
        self.seed.setValue(int(_opt("mayaComfy_seed", 0)))
        self.seed_random = QtWidgets.QCheckBox("aléatoire")
        self.seed_random.setChecked(bool(_opt("mayaComfy_seedRandom", 1)))
        self.denoise = QtWidgets.QDoubleSpinBox()
        self.denoise.setRange(0.05, 1.0)
        self.denoise.setSingleStep(0.05)
        self.denoise.setValue(float(_opt("mayaComfy_denoise", 0.65)))
        self.denoise.setToolTip("Liberté de l'IA par rapport au playblast (1 = ignore l'image)")
        row.addWidget(self.seed, 1)
        row.addWidget(self.seed_random)
        row.addWidget(QtWidgets.QLabel("Denoise"))
        row.addWidget(self.denoise)
        form.addRow("Seed", row)
        row = QtWidgets.QHBoxLayout()
        self.img_long = QtWidgets.QSpinBox()
        self.img_long.setRange(256, 2048)
        self.img_long.setSingleStep(64)
        self.img_long.setValue(self.cfg["image_long_side"])
        self.img_depth = QtWidgets.QCheckBox("passe profondeur (MAYA_DEPTH)")
        self.img_depth.setChecked(bool(_opt("mayaComfy_depth", 0)))
        row.addWidget(self.img_long)
        row.addWidget(self.img_depth)
        form.addRow("Côté long", row)
        self.btn_image = QtWidgets.QPushButton("Aperçu image")
        self.btn_image.setMinimumHeight(32)
        self.btn_image.clicked.connect(self._preview_image)
        form.addRow(self.btn_image)
        lay.addWidget(box)

        # historique
        box = QtWidgets.QGroupBox("Historique Qwen - cocher les images à donner à LTX")
        vbox = QtWidgets.QVBoxLayout(box)
        self.list = QtWidgets.QListWidget()
        self.list.setViewMode(QtWidgets.QListView.IconMode)
        self.list.setIconSize(QtCore.QSize(160, 120))
        self.list.setResizeMode(QtWidgets.QListView.Adjust)
        self.list.setMovement(QtWidgets.QListView.Static)
        self.list.setSpacing(4)
        self.list.setMinimumHeight(180)
        self.list.itemChanged.connect(self._item_checked)
        self.list.itemDoubleClicked.connect(lambda it: open_file(it.data(QtCore.Qt.UserRole)["path"]))
        self.list.currentItemChanged.connect(lambda *_: self._show_info())
        vbox.addWidget(self.list, 1)
        self.info = QtWidgets.QLabel("")
        self.info.setWordWrap(True)
        vbox.addWidget(self.info)
        row = QtWidgets.QHBoxLayout()
        for label, slot in (("Aller à la frame", self._goto_frame), ("Image plane", self._image_plane),
                            ("Reprendre prompt/seed", self._reuse), ("Supprimer", self._delete),
                            ("Dossier", lambda: open_file(self._ensure_root()))):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        vbox.addLayout(row)
        self.sel_label = QtWidgets.QLabel("")
        vbox.addWidget(self.sel_label)
        lay.addWidget(box, 1)

        # anim (LTX)
        box = QtWidgets.QGroupBox("Anim (LTX) - plage de la timeline")
        form = QtWidgets.QFormLayout(box)
        self.anim_prompt = QtWidgets.QPlainTextEdit(_opt("mayaComfy_animPrompt", ""))
        self.anim_prompt.setFixedHeight(50)
        self.anim_prompt.setPlaceholderText("vide = prompt image")
        form.addRow("Prompt", self.anim_prompt)
        row = QtWidgets.QHBoxLayout()
        self.anim_long = QtWidgets.QSpinBox()
        self.anim_long.setRange(256, 1280)
        self.anim_long.setSingleStep(32)
        self.anim_long.setValue(self.cfg["anim_long_side"])
        self.anim_max = QtWidgets.QSpinBox()
        self.anim_max.setRange(9, 513)
        self.anim_max.setValue(self.cfg["anim_max_frames"])
        self.anim_depth = QtWidgets.QCheckBox("profondeur")
        self.anim_depth.setChecked(True)
        row.addWidget(self.anim_long)
        row.addWidget(QtWidgets.QLabel("Frames max"))
        row.addWidget(self.anim_max)
        row.addWidget(self.anim_depth)
        form.addRow("Côté long", row)
        self.btn_anim = QtWidgets.QPushButton("Aperçu anim")
        self.btn_anim.setMinimumHeight(32)
        self.btn_anim.clicked.connect(self._preview_anim)
        form.addRow(self.btn_anim)
        self.anim_list = QtWidgets.QListWidget()
        self.anim_list.setMaximumHeight(90)
        self.anim_list.itemDoubleClicked.connect(lambda it: open_file(it.data(QtCore.Qt.UserRole)["path"]))
        form.addRow("Résultats", self.anim_list)
        lay.addWidget(box)

        row = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(80)
        self.status = QtWidgets.QLabel("Prêt")
        self.status.setWordWrap(True)
        self.btn_cancel = QtWidgets.QPushButton("Annuler")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel.set)
        row.addWidget(self.progress)
        row.addWidget(self.status, 1)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)

    # listes ----------------------------------------------------------------------
    def _refresh_cameras(self):
        current = self.camera.currentText()
        self.camera.clear()
        self.camera.addItem("(caméra du viewport)")
        cams = sorted(camera_transform(c) for c in cmds.ls(type="camera"))
        self.camera.addItems(cams)
        if current in cams:
            self.camera.setCurrentText(current)

    def _camera(self, panel):
        if self.camera.currentIndex() <= 0:
            return camera_transform(cmds.modelPanel(panel, q=True, camera=True))
        return self.camera.currentText()

    def _ensure_root(self):
        os.makedirs(self.history.root, exist_ok=True)
        return self.history.root

    def _refresh_lists(self):
        root = history_root()
        self.history.root, self.anims.root = root, os.path.join(root, "anim")
        self.list.blockSignals(True)
        self.list.clear()
        for e in self.history.entries():
            item = QtWidgets.QListWidgetItem("f%d  #%s" % (e.get("frame", 0), e.get("seed", "")))
            pix = QtGui.QPixmap(e["path"])
            if not pix.isNull():
                item.setIcon(QtGui.QIcon(pix.scaled(160, 120, QtCore.Qt.KeepAspectRatio,
                                                    QtCore.Qt.SmoothTransformation)))
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if e.get("selected") else QtCore.Qt.Unchecked)
            item.setData(QtCore.Qt.UserRole, e)
            item.setToolTip(e.get("prompt", ""))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.anim_list.clear()
        for e in self.anims.entries():
            item = QtWidgets.QListWidgetItem("f%d-%d  %s  (%s)" % (e.get("frame", 0), e.get("end", 0),
                                             os.path.basename(e["path"]), ", ".join("f%d" % k for k in e.get("keys", []))))
            item.setData(QtCore.Qt.UserRole, e)
            self.anim_list.addItem(item)
        self._update_selection_label()

    def _item_checked(self, item):
        e = item.data(QtCore.Qt.UserRole)
        self.history.update(e["path"], selected=item.checkState() == QtCore.Qt.Checked)
        self._update_selection_label()

    def _update_selection_label(self):
        sel = self.history.selected()
        self.sel_label.setText("Sélection pour l'anim : " + (", ".join("f%d" % e.get("frame", 0) for e in sel)
                                                            if sel else "aucune"))

    def _entry(self):
        item = self.list.currentItem()
        if not item:
            raise RuntimeError("Clique d'abord une image de l'historique.")
        return item.data(QtCore.Qt.UserRole)

    def _show_info(self):
        item = self.list.currentItem()
        if item:
            e = item.data(QtCore.Qt.UserRole)
            self.info.setText("f%d | seed %s | denoise %s | %s | %s" % (
                e.get("frame", 0), e.get("seed"), e.get("denoise"), e.get("camera"), e.get("prompt", "")[:200]))

    # actions sur l'historique ------------------------------------------------------
    def _goto_frame(self):
        self._guard(lambda: cmds.currentTime(self._entry().get("frame", 0)))

    def _image_plane(self):
        def run():
            e = self._entry()
            cam = self._camera(model_panel())
            for shape in cmds.listConnections(camera_shape(cam) + ".imagePlane", shapes=True) or []:
                if cmds.attributeQuery("mayaComfy", node=shape, exists=True):
                    cmds.setAttr(shape + ".imageName", e["path"], type="string")
                    return
            _, shape = cmds.imagePlane(camera=cam, fileName=e["path"], name="mayaComfyPlane")
            cmds.addAttr(shape, longName="mayaComfy", attributeType="bool", defaultValue=True)
        self._guard(run)

    def _reuse(self):
        def run():
            e = self._entry()
            self.prompt.setPlainText(e.get("prompt", ""))
            self.negative.setText(e.get("negative", ""))
            self.seed.setValue(int(e.get("seed", 0)))
            self.seed_random.setChecked(False)
            if e.get("denoise") is not None:
                self.denoise.setValue(float(e["denoise"]))
        self._guard(run)

    def _delete(self):
        def run():
            e = self._entry()
            if QtWidgets.QMessageBox.question(self, "Supprimer", "Supprimer %s ?" % os.path.basename(e["path"])) \
                    == QtWidgets.QMessageBox.Yes:
                self.history.remove(e["path"])
                self._refresh_lists()
        self._guard(run)

    def _test_server(self):
        try:
            stats = comfy_client.ComfyClient(self.server.text()).ping()
            dev = (stats.get("devices") or [{}])[0]
            self._set_status("ComfyUI OK - %s, VRAM libre %.1f Go" % (
                dev.get("name", "?"), dev.get("vram_free", 0) / 1024.0 ** 3))
        except comfy_client.ComfyError as e:
            self._set_status(str(e))

    # generations ---------------------------------------------------------------------
    def _save_options(self):
        cmds.optionVar(sv=("mayaComfy_server", self.server.text()))
        cmds.optionVar(sv=("mayaComfy_prompt", self.prompt.toPlainText()))
        cmds.optionVar(sv=("mayaComfy_negative", self.negative.text()))
        cmds.optionVar(sv=("mayaComfy_animPrompt", self.anim_prompt.toPlainText()))
        cmds.optionVar(iv=("mayaComfy_seed", self.seed.value()))
        cmds.optionVar(iv=("mayaComfy_seedRandom", int(self.seed_random.isChecked())))
        cmds.optionVar(fv=("mayaComfy_denoise", self.denoise.value()))
        cmds.optionVar(iv=("mayaComfy_depth", int(self.img_depth.isChecked())))

    def _next_seed(self):
        if self.seed_random.isChecked():
            self.seed.setValue(random.randint(0, 2 ** 31 - 1))
        return self.seed.value()

    def _preview_image(self):
        if self._busy:
            return
        self._save_options()
        try:
            wf = cwf.load_api_workflow(self.cfg["workflow_image"])
            panel = model_panel()
            cam = self._camera(panel)
            frame = int(round(cmds.currentTime(q=True)))
            size = cwf.size_from_aspect(*render_resolution(), long_side=self.img_long.value(),
                                        multiple=self.cfg["image_size_multiple"])
            tmp = os.path.join(self._ensure_root(), "_playblast")
            name = "img_" + time.strftime("%H%M%S")
            beauty = playblast(tmp, name, frame, frame, size, panel, cam)[0]
            depth = None
            if self.img_depth.isChecked() and "MAYA_DEPTH" in cwf.tokens(wf):
                depth = playblast_depth(tmp, name + "_depth", frame, frame, size, panel, cam)[0]
        except Exception as e:
            self._set_status("Erreur : %s" % e)
            return
        prompt, negative, seed, denoise = (self.prompt.toPlainText(), self.negative.text(), self._next_seed(),
                                           round(self.denoise.value(), 3))
        meta = {"kind": "image", "frame": frame, "seed": seed, "prompt": prompt, "negative": negative,
                "denoise": denoise, "camera": cam, "size": list(size),
                "workflow": os.path.basename(self.cfg["workflow_image"])}
        history = self.history

        def job(client, report):
            params = {"MAYA_PROMPT": prompt, "MAYA_NEGATIVE": negative, "MAYA_SEED": seed,
                      "MAYA_DENOISE": denoise, "MAYA_SIZE": size}
            report("Envoi du playblast f%d..." % frame)
            params["MAYA_IMAGE"] = client.upload(beauty)
            if depth:
                params["MAYA_DEPTH"] = client.upload(depth)
            return self._run(client, "image", wf, params, report, meta, history)

        self._start(job, "Image f%d" % frame)

    def _preview_anim(self):
        if self._busy:
            return
        self._save_options()
        try:
            keys = self.history.selected()
            if not keys:
                raise RuntimeError("Coche au moins une image Qwen dans l'historique.")
            wf = cwf.load_api_workflow(self.cfg["workflow_anim"])
            toks = cwf.tokens(wf)
            slots = cwf.key_slots(wf)
            if not slots and "MAYA_IMAGE" not in toks:
                raise RuntimeError("Le workflow anim n'a ni MAYA_KEY_1.. ni MAYA_IMAGE pour recevoir les images Qwen.")
            start = int(cmds.playbackOptions(q=True, minTime=True))
            n = cwf.snap_frames(int(cmds.playbackOptions(q=True, maxTime=True)) - start + 1,
                                self.cfg["anim_frame_step"], self.anim_max.value())
            end = start + n - 1
            keys = [e for e in keys if start <= e.get("frame", 0) <= end]
            if not keys:
                raise RuntimeError("Aucune image cochée dans la plage f%d-f%d." % (start, end))
            fps = scene_fps()
            size = cwf.size_from_aspect(*render_resolution(), long_side=self.anim_long.value(),
                                        multiple=self.cfg["anim_size_multiple"])
            panel = model_panel()
            cam = self._camera(panel)
            tmp = os.path.join(self._ensure_root(), "_playblast")
            name = "anim_" + time.strftime("%H%M%S")
            videos = {}
            if "MAYA_VIDEO" in toks or ("MAYA_DEPTH_VIDEO" in toks and self.anim_depth.isChecked()):
                ffmpeg = find_ffmpeg(self.cfg)
                if not ffmpeg:
                    raise RuntimeError("ffmpeg introuvable (chemin dans config.json) : requis pour MAYA_VIDEO.")
                if "MAYA_VIDEO" in toks:
                    videos["MAYA_VIDEO"] = playblast(tmp, name, start, end, size, panel, cam)
                if "MAYA_DEPTH_VIDEO" in toks and self.anim_depth.isChecked():
                    videos["MAYA_DEPTH_VIDEO"] = playblast_depth(tmp, name + "_depth", start, end, size, panel, cam)
            resized = {}
            for e in keys:
                resized[e["path"]] = resize_image(e["path"], size, os.path.join(
                    tmp, "%s_key_f%04d_%s.png" % (name, e["frame"], os.path.splitext(e["file"])[0][-6:])))
        except Exception as e:
            self._set_status("Erreur : %s" % e)
            return
        prompt = self.anim_prompt.toPlainText().strip() or self.prompt.toPlainText()
        negative, seed = self.negative.text(), self._next_seed()
        meta = {"kind": "anim", "frame": start, "end": end, "frames": n, "fps": fps, "seed": seed,
                "prompt": prompt, "negative": negative, "camera": cam, "size": list(size),
                "keys": [e["frame"] for e in keys], "workflow": os.path.basename(self.cfg["workflow_anim"])}
        anims = self.anims

        def job(client, report):
            params = {"MAYA_PROMPT": prompt, "MAYA_NEGATIVE": negative, "MAYA_SEED": seed, "MAYA_SIZE": size,
                      "MAYA_FRAMES": n, "MAYA_FPS": fps}
            report("Envoi de %d image(s) cle(s)..." % len(keys))
            uploaded = {path: client.upload(p) for path, p in resized.items()}
            params["MAYA_IMAGE"] = uploaded[keys[0]["path"]]
            for i, e in enumerate(cwf.assign_keys(keys, slots), 1):
                params["MAYA_KEY_%d" % i] = uploaded[e["path"]]
                params["MAYA_KEY_%d_IDX" % i] = e["frame"] - start
            for token, files in videos.items():
                report("Encodage %s..." % token)
                mp4 = encode_video(find_ffmpeg(self.cfg), files[0], start, fps,
                                   os.path.join(os.path.dirname(files[0]), "%s_%s.mp4" % (name, token.lower())))
                params[token] = client.upload(mp4)
            return self._run(client, "anim", wf, params, report, meta, anims)

        self._start(job, "Anim f%d-f%d (%d frames)" % (start, end, n))

    def _run(self, client, kind, wf, params, report, meta, history):
        """Execute dans le thread : injection, file d'attente, attente, telechargement dans l'historique."""
        wf, missing = cwf.inject(wf, params)
        if self.cfg["free_vram_between_models"] and self._last_kind not in (None, kind):
            report("Liberation de la VRAM...")
            client.free()
        prompt_id = client.queue(wf)
        outputs = client.wait(prompt_id, timeout=self.cfg["timeout_seconds"], poll=self.cfg["poll_seconds"],
                              on_progress=report, should_stop=self._cancel.is_set)
        self._last_kind = kind
        files = comfy_client.output_files(outputs)
        if not files:
            raise comfy_client.ComfyError("Le workflow n'a produit aucun fichier (SaveImage / SaveVideo manquant ?).")
        tmp = tempfile.mkdtemp(prefix="mayacomfy_")
        entries = [history.add(client.download(item, tmp), dict(meta, comfy_file=item["filename"]))
                   for item in files]
        shutil.rmtree(tmp, ignore_errors=True)
        expected = ("MAYA_PROMPT", "MAYA_SEED", "MAYA_SIZE") + (("MAYA_IMAGE",) if kind == "image" else ("MAYA_FRAMES",))
        return kind, entries, [t for t in missing if t in expected]

    # thread de travail -----------------------------------------------------------------
    def _start(self, job, label):
        self._busy = True
        self._cancel.clear()
        for b in (self.btn_image, self.btn_anim):
            b.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setRange(0, 0)
        self._set_status(label + "...")
        client = comfy_client.ComfyClient(self.server.text())

        def target():
            try:
                result = job(client, lambda msg: self.signals.status.emit("%s : %s" % (label, msg)))
            except Exception as e:  # remonte dans l'interface
                result = e
            self.signals.finished.emit(result)

        threading.Thread(target=target, daemon=True).start()

    def _job_finished(self, result):
        self._busy = False
        for b in (self.btn_image, self.btn_anim):
            b.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress.setRange(0, 1)
        if isinstance(result, Exception):
            cancelled = isinstance(result, comfy_client.ComfyCancelled)
            self._set_status("Annulé" if cancelled else "Erreur : %s" % result)
            return
        kind, entries, ignored = result
        self._refresh_lists()
        msg = "%d fichier(s) reçu(s)" % len(entries)
        if ignored:
            msg += " - absents du workflow (ignorés) : " + ", ".join(ignored)
        self._set_status(msg)
        if kind == "image" and self.list.count():
            self.list.setCurrentRow(0)
        if kind == "anim" and entries and self.cfg["open_anim_when_done"]:
            open_file(entries[0]["path"])

    def _set_status(self, text):
        self.status.setText(text)
        if text:
            print("[maya_comfy] " + text.splitlines()[0])

    def _guard(self, fn):
        try:
            fn()
        except Exception as e:
            self._set_status("Erreur : %s" % e)


_window = None


def show():
    global _window
    control = MayaComfyWindow.OBJECT_NAME + "WorkspaceControl"
    if cmds.workspaceControl(control, exists=True):
        cmds.deleteUI(control)
    _window = MayaComfyWindow()
    _window.show(dockable=True)
    return _window
