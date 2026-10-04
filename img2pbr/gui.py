#!/usr/bin/env python3
"""img2pbr : application de bureau (PySide6). Double-clic sur l'exe, ou : python gui.py

Une image -> cartes PBR carrees (1K/2K/4K), EXR ACEScg ou PNG, USD MaterialX en option.
Le calcul est fait par img2pbr.run() ; ce fichier ne contient que l'interface.
"""
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

import img2pbr

STYLE = """
* { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; font-size: 13px; color: #f2ead8; }
QMainWindow, QWidget#root { background: #1c1a17; }
QGroupBox { background: #262320; border: 1px solid #3a352e; border-radius: 8px; margin-top: 18px; padding: 14px 12px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #a79f8c; font-weight: 600; }
QLineEdit, QDoubleSpinBox { background: #1c1a17; border: 1px solid #3a352e; border-radius: 6px; padding: 6px; }
QPushButton { background: #262320; border: 1px solid #3a352e; border-radius: 6px; padding: 7px 14px; }
QPushButton:hover { border-color: #c2a04e; }
QPushButton:checked { background: #c2a04e; color: #1c1a17; border-color: #c2a04e; font-weight: 700; }
QPushButton#go { background: #c2a04e; color: #1c1a17; font-weight: 700; padding: 11px 26px; font-size: 14px; }
QPushButton#go:disabled { background: #5c5036; color: #8a8068; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 38px; height: 20px; border-radius: 10px; background: #3a352e; }
QCheckBox::indicator:checked { background: #c2a04e; }
QCheckBox:disabled { color: #6f6858; }
QPlainTextEdit { background: #161411; border: 1px solid #3a352e; border-radius: 6px; font-family: Consolas, monospace; font-size: 12px; }
QProgressBar { background: #262320; border: 0; border-radius: 4px; height: 6px; text-align: center; }
QProgressBar::chunk { background: #c2a04e; border-radius: 4px; }
QLabel#hint { color: #a79f8c; font-size: 12px; }
QLabel#drop { border: 2px dashed #3a352e; border-radius: 10px; color: #a79f8c; padding: 10px; }
QLabel#drop[active="true"] { border-color: #c2a04e; color: #f2ead8; }
"""

MAPS = ("diffuse", "height", "normal", "roughness", "specular", "ao")


def to_qimage(arr, size=170):
    a = (np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)
    if a.ndim == 2:
        a = np.stack([a] * 3, -1)
    a = np.ascontiguousarray(cv2.resize(a, (size, size), interpolation=cv2.INTER_AREA))
    return QtGui.QImage(a.data, size, size, size * 3, QtGui.QImage.Format_RGB888).copy()


class Worker(QtCore.QThread):
    log = QtCore.Signal(str)
    done = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, argv):
        super().__init__()
        self.argv = argv

    def run(self):
        try:
            self.done.emit(img2pbr.run(self.argv, log=self.log.emit))
        except SystemExit as e:                      # entree invalide (message texte)
            self.failed.emit(str(e))
        except Exception as e:                       # noqa: BLE001 : on affiche l'erreur dans la fenetre
            self.failed.emit(f"{type(e).__name__} : {e}")


class DropLabel(QtWidgets.QLabel):
    picked = QtCore.Signal(str)

    def __init__(self):
        super().__init__("Glisse une image ici\nou clique pour choisir")
        self.setObjectName("drop")
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumHeight(190)
        self.setAcceptDrops(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def _active(self, on):
        self.setProperty("active", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._active(True)

    def dragLeaveEvent(self, e):
        self._active(False)

    def dropEvent(self, e):
        self._active(False)
        urls = e.mimeData().urls()
        if urls:
            self.picked.emit(urls[0].toLocalFile())

    def mousePressEvent(self, e):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choisir une image", "", "Images (*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp)")
        if p:
            self.picked.emit(p)


class Window(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("img2pbr")
        self.resize(1080, 760)
        self.cfg = QtCore.QSettings("une.deux", "img2pbr")
        self.src = None
        self.out_dir = None
        self.worker = None
        root = QtWidgets.QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QtWidgets.QHBoxLayout(root)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(16)

        # ---- colonne gauche : reglages
        left = QtWidgets.QVBoxLayout()
        lay.addLayout(left, 0)
        title = QtWidgets.QLabel("img2<span style='color:#c2a04e'>pbr</span>")
        title.setStyleSheet("font-size:26px;font-weight:700;")
        left.addWidget(title)
        sub = QtWidgets.QLabel("Image → cartes PBR carrées · EXR ACEScg · USD MaterialX")
        sub.setObjectName("hint")
        left.addWidget(sub)

        self.drop = DropLabel()
        self.drop.picked.connect(self.set_source)
        left.addWidget(self.drop)

        g = QtWidgets.QGroupBox("Résolution d'export (carré)")
        gl = QtWidgets.QHBoxLayout(g)
        self.res = QtWidgets.QButtonGroup(self)
        for k in img2pbr.RES:
            b = QtWidgets.QPushButton(k.upper())
            b.setCheckable(True)
            b.setProperty("res", k)
            self.res.addButton(b)
            gl.addWidget(b)
            if k == self.cfg.value("res", "2k"):
                b.setChecked(True)
        self.res.setExclusive(True)
        left.addWidget(g)

        g = QtWidgets.QGroupBox("Export")
        gl = QtWidgets.QVBoxLayout(g)
        row = QtWidgets.QHBoxLayout()
        self.fmt = QtWidgets.QButtonGroup(self)
        for k, label in (("exr", "EXR ACEScg"), ("png", "PNG sRGB")):
            b = QtWidgets.QPushButton(label)
            b.setCheckable(True)
            b.setProperty("fmt", k)
            self.fmt.addButton(b)
            row.addWidget(b)
            if k == self.cfg.value("fmt", "exr"):
                b.setChecked(True)
        self.fmt.setExclusive(True)
        gl.addLayout(row)
        self.usd = QtWidgets.QCheckBox("Exporter aussi un USD avec shader MaterialX")
        self.usd.setChecked(self.cfg.value("usd", False, type=bool))
        self.plane = QtWidgets.QCheckBox("Ajouter un plan de test dans l'USD")
        self.plane.setEnabled(self.usd.isChecked())
        self.usd.toggled.connect(self.plane.setEnabled)
        self.seam = QtWidgets.QCheckBox("Rendre tileable (seamless)")
        self.seam.setChecked(self.cfg.value("seam", True, type=bool))
        self.ai = QtWidgets.QCheckBox("Relief IA (MiDaS, ~66 Mo au 1er usage)")
        self.ai.setChecked(self.cfg.value("ai", False, type=bool))
        for w in (self.usd, self.plane, self.seam, self.ai):
            gl.addWidget(w)
        left.addWidget(g)

        g = QtWidgets.QGroupBox("Réglages des cartes")
        g.setCheckable(True)
        g.setChecked(False)
        gl = QtWidgets.QFormLayout(g)
        self.ns = self._spin(4.0, 0, 20, 0.5)
        self.delight = self._spin(0.9, 0, 1, 0.1)
        self.rough = self._spin(0.6, 0, 1, 0.05)
        self.disp = self._spin(0.01, 0, 1, 0.005, 3)
        self.flip = QtWidgets.QCheckBox("Normal DirectX (Y inversé)")
        for label, w in (("Force du normal", self.ns), ("Retrait éclairage", self.delight),
                         ("Rugosité de base", self.rough), ("Déplacement USD (m)", self.disp), ("", self.flip)):
            gl.addRow(label, w)
        self.adv = g
        left.addWidget(g)

        g = QtWidgets.QGroupBox("Dossier de sortie")
        gl = QtWidgets.QHBoxLayout(g)
        self.out_edit = QtWidgets.QLineEdit()
        self.out_edit.setPlaceholderText("par défaut : à côté de l'image, dossier <nom>_pbr")
        self.out_edit.setText(self.cfg.value("out", ""))
        b = QtWidgets.QPushButton("…")
        b.clicked.connect(self.pick_out)
        gl.addWidget(self.out_edit, 1)
        gl.addWidget(b)
        left.addWidget(g)
        left.addStretch(1)

        self.go = QtWidgets.QPushButton("Générer")
        self.go.setObjectName("go")
        self.go.setEnabled(False)
        self.go.clicked.connect(self.start)
        left.addWidget(self.go)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setTextVisible(False)
        left.addWidget(self.bar)

        # ---- colonne droite : apercus + journal
        right = QtWidgets.QVBoxLayout()
        lay.addLayout(right, 1)
        g = QtWidgets.QGroupBox("Résultat")
        gl = QtWidgets.QGridLayout(g)
        self.thumbs = {}
        for i, k in enumerate(MAPS):
            lab = QtWidgets.QLabel()
            lab.setFixedSize(170, 170)
            lab.setStyleSheet("background:#1c1a17;border-radius:6px;")
            cap = QtWidgets.QLabel(k)
            cap.setObjectName("hint")
            gl.addWidget(lab, (i // 3) * 2, i % 3)
            gl.addWidget(cap, (i // 3) * 2 + 1, i % 3)
            self.thumbs[k] = lab
        right.addWidget(g)
        self.log_box = QtWidgets.QPlainTextEdit()
        self.log_box.setReadOnly(True)
        right.addWidget(self.log_box, 1)
        row = QtWidgets.QHBoxLayout()
        self.open_btn = QtWidgets.QPushButton("Ouvrir le dossier")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.out_dir))))
        row.addStretch(1)
        row.addWidget(self.open_btn)
        right.addLayout(row)

    def _spin(self, v, lo, hi, step, dec=2):
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(dec)
        s.setValue(v)
        return s

    def set_source(self, path):
        pm = QtGui.QPixmap(path)
        if pm.isNull():
            self.say(f"image illisible : {path}")
            return
        self.src = path
        self.drop.setPixmap(pm.scaled(220, 170, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        self.drop.setToolTip(path)
        self.go.setEnabled(True)
        self.say(f"source : {path}  ({pm.width()}×{pm.height()})")

    def pick_out(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Dossier de sortie", self.out_edit.text() or "")
        if d:
            self.out_edit.setText(d)

    def say(self, t):
        self.log_box.appendPlainText(t)

    def argv(self):
        res = self.res.checkedButton().property("res")
        stem = Path(self.src).stem
        out = Path(self.out_edit.text().strip() or (Path(self.src).parent / f"{stem}_pbr"))
        a = [self.src, "-o", str(out), "--res", res, "--format", self.fmt.checkedButton().property("fmt"),
             "--ai", "midas" if self.ai.isChecked() else "off"]
        if not self.seam.isChecked():
            a.append("--no-seamless")
        if self.usd.isChecked():
            a.append("--usd")
            if self.plane.isChecked():
                a.append("--usd-plane")
        if self.adv.isChecked():
            a += ["--normal-strength", str(self.ns.value()), "--delight", str(self.delight.value()),
                  "--roughness-base", str(self.rough.value()), "--disp-scale", str(self.disp.value())]
            if self.flip.isChecked():
                a.append("--flip-y")
        self.out_dir = out
        return a

    def start(self):
        if not self.src or self.worker:
            return
        argv = self.argv()
        for k in ("res", "fmt"):
            self.cfg.setValue(k, (self.res if k == "res" else self.fmt).checkedButton().property(k))
        self.cfg.setValue("usd", self.usd.isChecked())
        self.cfg.setValue("seam", self.seam.isChecked())
        self.cfg.setValue("ai", self.ai.isChecked())
        self.cfg.setValue("out", self.out_edit.text())
        self.go.setEnabled(False)
        self.open_btn.setEnabled(False)
        self.bar.setRange(0, 0)
        self.say("— calcul en cours…")
        self.worker = Worker(argv)
        self.worker.log.connect(self.say)
        self.worker.done.connect(self.finished)
        self.worker.failed.connect(self.error)
        self.worker.start()

    def _end(self):
        self.bar.setRange(0, 1)
        self.go.setEnabled(True)
        self.worker = None

    def finished(self, r):
        for k, arr in r["maps"].items():
            self.thumbs[k].setPixmap(QtGui.QPixmap.fromImage(to_qimage(arr)))
        self.say("fichiers : " + ", ".join(Path(p).name for p in r["files"].values()))
        self.open_btn.setEnabled(True)
        self._end()

    def error(self, msg):
        self.say("ERREUR : " + msg)
        self._end()


def main():
    if "--cli" in sys.argv:                      # meme exe en ligne de commande : img2pbr --cli photo.jpg --res 2k --usd
        sys.argv.remove("--cli")
        return img2pbr.main()
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = Window()
    w.show()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):   # image donnee en argument / glissee sur l'exe
        w.set_source(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
