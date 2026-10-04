#!/usr/bin/env python3
"""Fenetre pour img2hdri_pc : une image -> HDRI 360 (EXR ACEScg), resolution 1k / 2k / 4k.

    python img2hdri_gui.py
"""
import contextlib
import queue
import threading
import tkinter as tk
from argparse import Namespace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pano_outpaint as po
from img2hdri_pc import RESOLUTIONS, generate

IMAGE_TYPES = [("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp"), ("Tous", "*.*")]


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
        root.title("img2hdri - image vers HDRI 360 (EXR ACEScg)")
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.q = queue.Queue()
        self.pipe = None
        self.pipe_key = None
        self.photo = None
        self.busy = False

        self.v = {
            "image": tk.StringVar(), "out": tk.StringVar(value=str(Path.cwd() / "out")),
            "res": tk.StringVar(value="2k"), "hfov": tk.StringVar(value="69"),
            "yaw": tk.StringVar(value="0"), "pitch": tk.StringVar(value="0"),
            "roll": tk.StringVar(value="0"), "seed": tk.StringVar(value="0"),
            "exposure": tk.StringVar(value="0"), "peak": tk.StringVar(value="40"),
            "model": tk.StringVar(value=po.DEFAULT_MODEL), "lora": tk.StringVar(),
            "low_vram": tk.BooleanVar(), "dry_run": tk.BooleanVar(), "float32": tk.BooleanVar(),
        }
        r = 0
        ttk.Label(self, text="Image").grid(row=r, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.v["image"]).grid(row=r, column=1, sticky="ew", padx=4)
        ttk.Button(self, text="Parcourir...", command=self.pick_image).grid(row=r, column=2)
        r += 1
        ttk.Label(self, text="Dossier de sortie").grid(row=r, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.v["out"]).grid(row=r, column=1, sticky="ew", padx=4)
        ttk.Button(self, text="Parcourir...", command=self.pick_out).grid(row=r, column=2)
        r += 1
        ttk.Label(self, text="Resolution EXR").grid(row=r, column=0, sticky="w", pady=6)
        box = ttk.Frame(self)
        box.grid(row=r, column=1, sticky="w", padx=4)
        for k, w in RESOLUTIONS.items():
            ttk.Radiobutton(box, text=f"{k.upper()}  ({w}x{w // 2})", value=k,
                            variable=self.v["res"]).pack(side="left", padx=(0, 12))
        r += 1
        ttk.Label(self, text="Prompt de la scene").grid(row=r, column=0, sticky="nw")
        self.prompt = tk.Text(self, height=4, wrap="word")
        self.prompt.insert("1.0", po.DEFAULT_PROMPT)
        self.prompt.grid(row=r, column=1, columnspan=2, sticky="ew", padx=4)
        r += 1

        adv = ttk.LabelFrame(self, text="Reglages", padding=6)
        adv.grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        fields = [("Champ photo (hfov, deg)", "hfov"), ("Yaw", "yaw"), ("Pitch", "pitch"),
                  ("Roll", "roll"), ("Seed", "seed"), ("Exposition (stops)", "exposure"),
                  ("Gain sources (peak)", "peak")]
        for i, (label, key) in enumerate(fields):
            ttk.Label(adv, text=label).grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 4))
            ttk.Entry(adv, textvariable=self.v[key], width=8).grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 16), pady=2)
        ttk.Label(adv, text="Modele HF").grid(row=4, column=0, sticky="w")
        ttk.Entry(adv, textvariable=self.v["model"]).grid(row=4, column=1, columnspan=3, sticky="ew")
        ttk.Label(adv, text="LoRA (option)").grid(row=5, column=0, sticky="w")
        ttk.Entry(adv, textvariable=self.v["lora"]).grid(row=5, column=1, columnspan=3, sticky="ew")
        adv.columnconfigure(3, weight=1)
        opts = ttk.Frame(adv)
        opts.grid(row=6, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Checkbutton(opts, text="GPU < 12 Go (low-vram)", variable=self.v["low_vram"]).pack(side="left")
        ttk.Checkbutton(opts, text="EXR 32 bits", variable=self.v["float32"]).pack(side="left", padx=12)
        ttk.Checkbutton(opts, text="Sans IA (test, extension lisse)",
                        variable=self.v["dry_run"]).pack(side="left")
        r += 1

        self.btn = ttk.Button(self, text="Generer la HDRI", command=self.start)
        self.btn.grid(row=r, column=0, columnspan=3, sticky="ew", pady=4)
        r += 1
        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.grid(row=r, column=0, columnspan=3, sticky="ew")
        r += 1
        self.log = tk.Text(self, height=9, state="disabled", wrap="word")
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=4)
        self.rowconfigure(r, weight=1)
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
            image=image, out=v["out"].get().strip() or "out", res=v["res"].get(),
            hfov=f("hfov"), yaw=f("yaw"), pitch=f("pitch"), roll=f("roll"),
            prompt=self.prompt.get("1.0", "end").strip(), negative=po.DEFAULT_NEG,
            model=v["model"].get().strip() or po.DEFAULT_MODEL,
            lora=v["lora"].get().strip() or None, lora_scale=0.8, gen_width=1024, rings=4,
            steps=30, guidance=7.0, seed=int(f("seed")), low_vram=v["low_vram"].get(),
            dry_run=v["dry_run"].get(), peak=f("peak"), shoulder=2.5, exposure=f("exposure"),
            feather=4.0, float32=v["float32"].get())

    def start(self):
        if self.busy:
            return
        try:
            a = self.args()
        except ValueError as e:
            messagebox.showerror("Reglage invalide", str(e))
            return
        self.busy = True
        self.btn.configure(state="disabled")
        self.bar.start(12)
        self.say(f"\n--- {Path(a.image).name}  {a.res.upper()} ---\n")
        threading.Thread(target=self.work, args=(a,), daemon=True).start()

    def work(self, a):
        try:
            with contextlib.redirect_stdout(QueueWriter(self.q)):
                if not a.dry_run:
                    key = (a.model, a.lora, a.low_vram)
                    if self.pipe is None or key != self.pipe_key:
                        print("chargement du modele IA (1er lancement : telechargement possible)...")
                        self.pipe = po.load_pipe(a.model, a.lora, a.lora_scale, a.low_vram)
                        self.pipe_key = key
                exr, prev = generate(a, None if a.dry_run else self.pipe)
            self.q.put(("done", (exr, prev)))
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
                        self.show_preview(data[1])
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def show_preview(self, path):
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            im.thumbnail((640, 320))
            self.photo = ImageTk.PhotoImage(im)
            self.preview.configure(image=self.photo)
        except Exception as e:
            self.say(f"(apercu non affiche : {e})\n")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
