#!/usr/bin/env python3
"""Fenetre pour img2pbr : photo -> cartes PBR tileables (diffuse, height, normal, roughness,
specular, ao), taille au choix.

    python img2pbr_gui.py
"""
import contextlib
import queue
import threading
import tkinter as tk
from argparse import Namespace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import export
from img2pbr import generate

IMAGE_TYPES = [("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp"), ("Tous", "*.*")]
USD_CHOICES = ["Pas d'USD", "MaterialX + UsdPreviewSurface", "MaterialX seul", "UsdPreviewSurface seul"]
USD_MODES = [None, "both", "mtlx", "preview"]
SIZES = ["512", "1024", "2048", "4096", "8192"]
MAPS = ["diffuse", "height", "normal", "roughness", "specular", "ao", "apercu pavage 3x3"]


class QueueWriter:
    """Redirige print() du thread de travail vers la fenetre."""
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(("log", s))

    def flush(self):
        pass


class App(ttk.Frame):
    def __init__(self, root):
        super().__init__(root, padding=10)
        root.title("img2pbr - photo vers cartes PBR")
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.q = queue.Queue()
        self.busy = False
        self.photo = None
        self.result = None   # (dossier, stem)
        self.fmt = "png"

        self.v = {
            "image": tk.StringVar(), "out": tk.StringVar(value=str(Path.cwd() / "out")),
            "size": tk.StringVar(value="1024"),
            "crop_scale": tk.StringVar(value="1"), "ox": tk.StringVar(value="0.5"),
            "oy": tk.StringVar(value="0.5"), "seam_band": tk.StringVar(value="0.35"),
            "ai_strength": tk.StringVar(value="0.3"), "normal_strength": tk.StringVar(value="4"),
            "delight": tk.StringVar(value="0.9"), "roughness_base": tk.StringVar(value="0.6"),
            "seamless": tk.BooleanVar(value=True), "ai": tk.BooleanVar(value=True),
            "bit16": tk.BooleanVar(), "flip_y": tk.BooleanVar(), "preview": tk.BooleanVar(value=True),
            "show": tk.StringVar(value="diffuse"), "format": tk.StringVar(value="png"),
            "exr32": tk.BooleanVar(), "usd": tk.StringVar(value=USD_CHOICES[0]),
            "disp_scale": tk.StringVar(value="0.01"),
        }
        r = 0
        ttk.Label(self, text="Photo").grid(row=r, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.v["image"]).grid(row=r, column=1, sticky="ew", padx=4)
        ttk.Button(self, text="Parcourir...", command=self.pick_image).grid(row=r, column=2)
        r += 1
        ttk.Label(self, text="Dossier de sortie").grid(row=r, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.v["out"]).grid(row=r, column=1, sticky="ew", padx=4)
        ttk.Button(self, text="Parcourir...", command=self.pick_out).grid(row=r, column=2)
        r += 1
        ttk.Label(self, text="Taille (px)").grid(row=r, column=0, sticky="w", pady=6)
        box = ttk.Frame(self)
        box.grid(row=r, column=1, sticky="w", padx=4)
        for s in SIZES:
            ttk.Radiobutton(box, text=s, value=s, variable=self.v["size"]).pack(side="left", padx=(0, 10))
        r += 1

        ttk.Label(self, text="Format des cartes").grid(row=r, column=0, sticky="w")
        box = ttk.Frame(self)
        box.grid(row=r, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Radiobutton(box, text="PNG  (diffuse Rec.709 / sRGB)", value="png",
                        variable=self.v["format"]).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(box, text="EXR  (diffuse ACEScg)", value="exr",
                        variable=self.v["format"]).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(box, text="EXR 32 bits", variable=self.v["exr32"]).pack(side="left")
        r += 1
        ttk.Label(self, text="Export USD").grid(row=r, column=0, sticky="w", pady=4)
        box = ttk.Frame(self)
        box.grid(row=r, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Combobox(box, values=USD_CHOICES, textvariable=self.v["usd"], state="readonly",
                     width=32).pack(side="left")
        ttk.Label(box, text="  Displacement (echelle)").pack(side="left")
        ttk.Entry(box, textvariable=self.v["disp_scale"], width=7).pack(side="left", padx=4)
        r += 1

        adv = ttk.LabelFrame(self, text="Reglages", padding=6)
        adv.grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        fields = [("Zoom recadrage (1 = max)", "crop_scale"), ("Largeur fondu pavage", "seam_band"),
                  ("Position X (0-1)", "ox"), ("Position Y (0-1)", "oy"),
                  ("Force normal", "normal_strength"), ("De-eclairage (0-1)", "delight"),
                  ("Roughness de base", "roughness_base"), ("Part relief IA (0-1)", "ai_strength")]
        for i, (label, key) in enumerate(fields):
            ttk.Label(adv, text=label).grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 4))
            ttk.Entry(adv, textvariable=self.v[key], width=8).grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 16), pady=2)
        opts = ttk.Frame(adv)
        opts.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        for text, key in [("Pavage seamless", "seamless"), ("Relief IA (MiDaS)", "ai"),
                          ("PNG 16 bits (height, normal)", "bit16"),
                          ("Normal DirectX", "flip_y"), ("Apercu 3x3", "preview")]:
            ttk.Checkbutton(opts, text=text, variable=self.v[key]).pack(side="left", padx=(0, 10))
        r += 1

        self.btn = ttk.Button(self, text="Generer les cartes", command=self.start)
        self.btn.grid(row=r, column=0, columnspan=3, sticky="ew", pady=4)
        r += 1
        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.grid(row=r, column=0, columnspan=3, sticky="ew")
        r += 1
        self.log = tk.Text(self, height=7, state="disabled", wrap="word")
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=4)
        self.rowconfigure(r, weight=1)
        r += 1
        view = ttk.Frame(self)
        view.grid(row=r, column=0, columnspan=3, sticky="w")
        ttk.Label(view, text="Carte affichee").pack(side="left")
        cb = ttk.Combobox(view, values=MAPS, textvariable=self.v["show"], state="readonly", width=20)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.show_map())
        ttk.Button(view, text="Ouvrir le dossier", command=self.open_folder).pack(side="left")
        r += 1
        self.preview = ttk.Label(self, anchor="center")
        self.preview.grid(row=r, column=0, columnspan=3)
        self.after(100, self.poll)

    def pick_image(self):
        f = filedialog.askopenfilename(filetypes=IMAGE_TYPES)
        if f:
            self.v["image"].set(f)

    def pick_out(self):
        d = filedialog.askdirectory()
        if d:
            self.v["out"].set(d)

    def say(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.configure(state="disabled")

    def args(self):
        v = self.v
        f = lambda k: float(v[k].get().replace(",", "."))
        image = v["image"].get().strip()
        if not image or not Path(image).is_file():
            raise ValueError("Choisis une image existante.")
        return Namespace(
            image=image, out=v["out"].get().strip() or "out", size=int(v["size"].get()),
            crop_scale=f("crop_scale"), crop_offset=(f("ox"), f("oy")),
            no_seamless=not v["seamless"].get(), seam_band=f("seam_band"),
            ai="midas" if v["ai"].get() else "off", ai_strength=f("ai_strength"), model_path=None,
            normal_strength=f("normal_strength"), delight=f("delight"), delight_sigma=25,
            roughness_base=f("roughness_base"), flip_y=v["flip_y"].get(), bit16=v["bit16"].get(),
            preview=v["preview"].get(), format=v["format"].get(), exr_float32=v["exr32"].get(),
            usd=USD_MODES[USD_CHOICES.index(v["usd"].get())], disp_scale=f("disp_scale"))

    def start(self):
        if self.busy:
            return
        try:
            a = self.args()
        except ValueError as e:
            messagebox.showerror("Reglage invalide", f"Valeur non numerique ou image absente.\n{e}")
            return
        self.busy = True
        self.fmt = a.format
        self.btn.configure(state="disabled")
        self.bar.start(12)
        self.say(f"\n--- {Path(a.image).name}  {a.size}x{a.size} ---\n")
        threading.Thread(target=self.work, args=(a,), daemon=True).start()

    def work(self, a):
        try:
            with contextlib.redirect_stdout(QueueWriter(self.q)):
                out, _ = generate(a)
            self.q.put(("done", (Path(out), Path(a.image).stem)))
        except Exception as e:  # affiche dans la fenetre au lieu de tuer le thread en silence
            self.q.put(("error", f"{type(e).__name__}: {e}"))

    def poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self.say(data)
                else:
                    self.bar.stop()
                    self.btn.configure(state="normal")
                    self.busy = False
                    if kind == "error":
                        self.say(f"ERREUR : {data}\n")
                        messagebox.showerror("Echec", data)
                    else:
                        self.result = data
                        self.show_map()
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def show_map(self):
        if not self.result:
            return
        out, stem = self.result
        name = self.v["show"].get()
        if name.startswith("apercu"):
            path = out / f"{stem}_preview_tiles.jpg"
        else:
            path = out / f"{stem}_{name}.{self.fmt}"
            if not path.exists():
                path = path.with_suffix(".png" if self.fmt == "exr" else ".exr")
        try:
            from PIL import Image, ImageTk
            if path.suffix == ".exr":
                im = Image.fromarray(export.exr_to_display(path, name in export.COLOR_MAPS))
            else:
                im = Image.open(path)
            im.thumbnail((420, 420))
            self.photo = ImageTk.PhotoImage(im)
            self.preview.configure(image=self.photo, text="")
        except Exception as e:
            self.preview.configure(image="", text=f"(rien a afficher : {path.name} - {e})")

    def open_folder(self):
        import os
        import subprocess
        import sys
        if not self.result:
            return
        p = str(self.result[0])
        if sys.platform == "win32":
            os.startfile(p)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", p])


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
