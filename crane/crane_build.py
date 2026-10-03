"""Grue portuaire a portique (4 barres) - construction par etapes.
Usage : python crane_build.py <etape 1..4>      (1 blocking, 2 formes primaires/secondaires, 3 details, 4 harden/soften + finition)"""
import sys, os, math, bpy, bmesh
from mathutils import Vector, Matrix
from crane_lib import *

HERE = os.path.dirname(os.path.abspath(__file__))

RED = (0.62, 0.07, 0.04); DARK = (0.07, 0.07, 0.08); YEL = (0.85, 0.62, 0.02); GLASS = (0.25, 0.4, 0.5)
GREY = (0.34, 0.34, 0.35); CONC = (0.40, 0.40, 0.39); SKIN = (0.55, 0.62, 0.72)

# ------------------------------------------------------------------ reference : sol + humain
def build_ground(coll):
    bm = bmesh.new()
    add_box(bm, (3, 0, -0.25), (110, 80, 0.5))
    g = make_obj("Ground", bm, material("Concrete", CONC, 0.9), coll)
    bm = bmesh.new()                                    # rails le long de X, ecartement 6 m
    for y in (-3.0, 3.0): add_box(bm, (3, y, 0.09), (110, 0.18, 0.18))
    r = make_obj("Rails", bm, material("Steel_dark", (0.12, 0.12, 0.13), 0.5, 0.8), coll)
    # repere metrique : dalles de 5 m au sol (lignes fines)
    bm = bmesh.new()
    for i in range(-10, 12): add_box(bm, (i * 5, 0, 0.003), (0.04, 80, 0.006))
    for j in range(-8, 9): add_box(bm, (3, j * 5, 0.003), (110, 0.04, 0.006))
    make_obj("Ground_Grid_5m", bm, material("Grid", (0.28, 0.28, 0.28), 1), coll)
    return g

def limb(bm, a, b, r0, r1, segs=14):
    a, b = Vector(a), Vector(b); d = b - a
    q = d.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r0, radius2=r1, depth=d.length,
                          matrix=Matrix.Translation((a + b) / 2) @ q)

def ball(bm, c, r, sc=(1, 1, 1)):
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=1.0,
                              matrix=Matrix.Translation(c) @ Matrix.Diagonal(Vector((r * sc[0], r * sc[1], r * sc[2], 1))))

def build_human(coll, loc, height=1.75):
    """Mannequin 1,75 m (8 tetes) : reference d'echelle, subsurf pour un volume lisse."""
    k = height / 1.75; bm = bmesh.new()
    V = lambda x, y, z: Vector((x * k, y * k, z * k))
    for s in (-1, 1):
        add_box(bm, V(s * 0.095, 0.03, 0.035), (0.10 * k, 0.26 * k, 0.07 * k))                    # pied
        limb(bm, V(s * 0.095, 0, 0.09), V(s * 0.10, 0, 0.48), 0.040 * k, 0.058 * k)               # tibia
        ball(bm, V(s * 0.10, 0, 0.49), 0.058 * k)                                                    # genou
        limb(bm, V(s * 0.10, 0, 0.49), V(s * 0.095, 0, 0.90), 0.062 * k, 0.088 * k)               # cuisse
        ball(bm, V(s * 0.20, 0, 1.455), 0.055 * k)                                                   # epaule
        limb(bm, V(s * 0.20, 0, 1.455), V(s * 0.26, 0, 1.19), 0.048 * k, 0.040 * k)               # bras
        ball(bm, V(s * 0.26, 0, 1.19), 0.040 * k)                                                    # coude
        limb(bm, V(s * 0.26, 0, 1.19), V(s * 0.30, 0.02, 0.93), 0.040 * k, 0.030 * k)             # avant-bras
        ball(bm, V(s * 0.30, 0.02, 0.89), 0.036 * k, (0.7, 1.0, 1.4))                                # main
    ball(bm, V(0, 0, 0.93), 0.17 * k, (1.0, 0.68, 0.72))                                             # bassin
    limb(bm, V(0, 0, 0.95), V(0, 0, 1.40), 0.145 * k, 0.185 * k, 20)                                 # torse
    ball(bm, V(0, 0, 1.42), 0.19 * k, (1.0, 0.62, 0.45))                                             # poitrine
    limb(bm, V(0, 0, 1.45), V(0, 0, 1.58), 0.05 * k, 0.045 * k)                                      # cou
    ball(bm, V(0, 0.005, 1.645), 0.085 * k, (0.92, 1.0, 1.28))                                       # tete
    ob = make_obj("Human_1m75", bm, material("Mannequin", SKIN, 0.7), coll, loc=loc)
    ob.rotation_euler[2] = math.radians(-35)
    sub = ob.modifiers.new("Subsurf", 'SUBSURF'); sub.levels = 1; sub.render_levels = 2
    for p in ob.data.polygons: p.use_smooth = True
    return ob

# ------------------------------------------------------------------ grue
class Crane:
    def __init__(self, L, coll):
        self.L, self.coll = L, coll
        self.root = empty("CRANE_ROOT", coll)
        self.slew = empty("SLEW_AXIS (pivot Z)", coll, (0, 0, 10.45), self.root)    # tout ce qui tourne est parente ici
        self.red = material("Crane_red", RED, 0.45)
        self.dark = material("Dark", DARK, 0.5, 0.6)
        self.yel = material("Hazard_yellow", YEL, 0.5)
        self.glass = material("Glass", GLASS, 0.1)
        self.grey = material("Grey_steel", GREY, 0.5, 0.5)

    def add(self, name, bm, mat=None, slew=False, cuts=None, **hard):
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        ob = make_obj(name, bm, mat or self.red, self.coll, parent=self.slew if slew else self.root)
        if slew: ob.matrix_parent_inverse = Matrix.Translation((0, 0, -10.45))
        if cuts: bool_cut(ob, cuts)
        if self.L >= 2: harden_soften(ob, **{'angle': 30, 'bevel': 0.0, **hard})
        return ob

    # ---- base roulante ------------------------------------------------------------------
    def portal(self):
        L = self.L; bm = bmesh.new()
        if L == 1:
            for y in (-3.0, 3.0): add_box(bm, (0.72, y, 2.75), (17.04, 1.7, 2.75))
            add_box(bm, (0, 0, 2.75), (5.2, 6.0, 2.75))
        else:   # poutres-caissons en profil effile : haute au centre, plus basse aux extremites
            prof = [(-7.8, 1.4), (9.24, 1.4), (9.24, 2.9), (3.2, 4.125), (-3.2, 4.125), (-7.8, 3.3)]
            for y in (-3.0, 3.0): prism(bm, prof, y - 0.85, y + 0.85)
            add_box(bm, (0, 0, 2.75), (5.6, 4.3, 2.75))                            # traverse centrale
            for x in (-7.0, 8.4): add_box(bm, (x, 0, 2.15), (1.3, 4.3, 1.5))        # traverses d'extremite
        self.add("Portal_Frame", bm, bevel=0.10, segments=2)
        bm = bmesh.new()
        for y in (-3.0, 3.0):
            if L == 1:
                add_box(bm, (-6.2, y, 0.69), (6.8, 1.9, 1.375)); add_box(bm, (4.25, y, 0.69), (4.7, 1.9, 1.375))
            else:
                for x0, x1 in ((-9.6, -2.8), (1.9, 6.6)):
                    prism(bm, [(x0, 0), (x1, 0), (x1, 1.0), (x1 - 0.7, 1.375), (x0 + 0.7, 1.375), (x0, 1.0)], y - 0.95, y + 0.95)
        self.add("Bogies", bm, self.dark, bevel=0.05, segments=2)
        bm = bmesh.new()
        if L == 1: add_cyl(bm, (0, 0, 0), 2.1, 0.9, 'Y', 32)
        else:
            add_cyl(bm, (0, 0, 0), 1.25, 0.9, 'Y', 32)                                   # tambour
            for dy in (-0.5, 0.5): add_cyl(bm, (0, dy, 0), 2.1, 0.12, 'Y', 48)           # flasques
        ob = self.add("Cable_Reel", bm, self.dark, bevel=0.03, segments=2); ob.location = P(88, 458, -4.4)
        if L >= 2:
            bm = bmesh.new()                                                              # potence du touret
            for dy in (-0.9, 0.9): prism(bm, [(-0.35, -2.2), (0.35, -2.2), (0.35, 0.0), (-0.35, 0.0)], dy - 0.1, dy + 0.1, Matrix.Translation(P(88, 458, -4.4)))
            self.add("Cable_Reel_Support", bm, self.grey, bevel=0.03)

    def pedestal(self):
        L = self.L; bm = bmesh.new()
        if L == 1:
            add_box(bm, (0, 0, 6.06), (4.7, 4.7, 3.9)); add_cyl(bm, (0, 0, 8.62), 2.4, 1.25)
        else:
            add_cyl(bm, (0, 0, 6.06), 2.54, 3.9, segs=8, r2=None)                         # fut octogonal
            bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(22.5), 3, 'Z'), verts=bm.verts)
            add_cyl(bm, (0, 0, 8.62), 2.4, 1.25, segs=48)                                 # fut cylindrique
            add_cyl(bm, (0, 0, 8.12), 2.75, 0.25, segs=48)                                # bride de transition
        self.add("Pedestal", bm, bevel=0.06, segments=2)
        bm = bmesh.new()
        if L == 1: add_box(bm, (1.25, -2.9, 6.05), (4.1, 3.0, 2.2))
        else: prism(bm, [(-0.8, 4.95), (3.3, 4.95), (3.3, 6.75), (2.6, 7.2), (-0.8, 7.2)], -4.2, -1.7)
        self.add("Pedestal_Machine_Room", bm, bevel=0.06, segments=2)
        bm = bmesh.new(); add_cyl(bm, (0, 0, 9.425), 4.6, 0.35, segs=64)
        if L >= 2:
            for i in range(12):                                                           # goussets radiaux sous le deck
                M = Matrix.Rotation(i * math.radians(30), 4, 'Z')
                prism(bm, [(2.3, 9.25), (4.2, 9.25), (2.3, 7.9)], -0.1, 0.1, M)
        self.add("Slew_Deck", bm, self.grey, bevel=0.03, segments=2)
        bm = bmesh.new(); add_cyl(bm, (0, 0, 10.0), 3.0, 0.85, segs=64)
        if L >= 2:
            for z in (9.7, 10.3): add_cyl(bm, (0, 0, z), 3.12, 0.14, segs=64)
        self.add("Slew_Bearing", bm, self.dark, bevel=0.025, segments=2)

    # ---- partie tournante -------------------------------------------------------------
    def house(self):
        L = self.L; bm = bmesh.new(); cuts = []; cab_cuts = []
        if L == 1: add_box(bm, (-3.25, 0, 12.925), (10.3, 7.0, 4.95))
        else: prism(bm, [(-8.4, 15.4), (1.9, 15.4), (1.9, 10.45), (-6.6, 10.45), (-8.4, 11.6)], -3.5, 3.5)
        if L >= 3:      # 5 rainures de panneau, 2 fenetres, 1 trappe d'acces : faces avant et arriere
            for sy in (-1, 1):
                yf = sy * 3.5
                for x in (-5.5, -4.2, -2.9, -1.3, 0.4): cuts.append(self._box((x, yf, 13.15), (0.07, 0.12, 4.2)))
                for x in (-6.45, -5.35): cuts.append(self._box((x, yf, 11.8), (0.85, 0.3, 0.8)))
                cuts.append(self._box((-0.6, yf, 11.5), (1.1, 0.14, 1.3)))
        self.add("Machinery_House", bm, bevel=0.12, segments=3, slew=True, cuts=cuts or None)
        if L >= 3:
            bm = bmesh.new()
            for sy in (-1, 1):
                for x in (-6.45, -5.35): add_box(bm, (x, sy * 3.37, 11.8), (0.8, 0.03, 0.75))
            self.add("House_Glass", bm, self.glass, slew=True)
            bm = bmesh.new(); add_box(bm, (-1.8, 0, 15.75), (6.9, 5.6, 0.7)); add_box(bm, (-6.8, 0, 15.65), (1.6, 3.0, 0.5))
            self.add("House_Roof_Boxes", bm, bevel=0.06, segments=2, slew=True)
            bm = bmesh.new()
            railing(bm, [Vector((-8.25, -3.35, 15.4)), Vector((1.75, -3.35, 15.4)), Vector((1.75, 3.35, 15.4)), Vector((-8.25, 3.35, 15.4)), Vector((-8.25, -3.35, 15.4))], post_every=1.25, r=0.025)
            self.add("House_Roof_Rail", bm, self.yel, slew=True)
        bm = bmesh.new()
        if L == 1: add_box(bm, (3.8, -2.2, 12.35), (2.1, 2.2, 1.9))
        else: prism(bm, [(2.75, 11.4), (4.85, 11.4), (4.85, 12.85), (4.4, 13.3), (2.75, 13.3)], -3.3, -1.1)
        if L >= 3: cab_cuts = [self._box((3.8, -3.3, 12.35), (1.5, 0.3, 0.8)), self._box((4.85, -2.2, 12.3), (0.3, 1.5, 0.8))]
        self.add("Operator_Cab", bm, bevel=0.06, segments=2, slew=True, cuts=cab_cuts or None)
        if L >= 3:
            bm = bmesh.new(); add_box(bm, (3.8, -3.17, 12.35), (1.45, 0.03, 0.75)); add_box(bm, (4.72, -2.2, 12.3), (0.03, 1.45, 0.75))
            self.add("Cab_Glass", bm, self.glass, slew=True)
        bm = bmesh.new()
        if L == 1:
            add_cyl(bm, (-8.0, 0, 17.0), 1.0, 3.2, 'Y', 32); add_box(bm, (-8.0, 0, 16.2), (2.0, 3.0, 1.6))
        else:
            add_cyl(bm, (-8.0, 0, 17.0), 1.0, 3.0, 'Y', 48)                              # tambour contrepoids
            for y in (-1.6, 1.6): add_cyl(bm, (-8.0, y, 17.0), 1.2, 0.18, 'Y', 48)       # flasques
            for y in (-1.0, 1.0): prism(bm, [(-9.1, 15.4), (-6.9, 15.4), (-7.2, 17.0), (-8.0, 18.1), (-8.8, 17.0)], y - 0.12, y + 0.12)
        self.add("Counterweight", bm, self.grey, bevel=0.04, segments=2, slew=True)

    def linkage(self):
        L = self.L; apex = P(372, 26)
        # --- mat principal (fut caisson, plus large en pied)
        bm = bmesh.new()
        if L == 1: sweep(bm, [P(275, 270), P(376, 45)], [(1.6, 2.2), (1.6, 1.5)])
        else: sweep(bm, [P(275, 270), P(325, 160), P(376, 45)], [(1.9, 2.5), (1.9, 1.75), (1.7, 1.5)])
        self.add("Strut_Main", bm, bevel=0.08, segments=2, slew=True)
        # --- contre-fleche : treillis
        a, b = P(85, 218), P(362, 32)
        bm = bmesh.new()
        if L == 1: sweep(bm, [a, b], [(1.8, 1.4), (1.8, 1.4)])
        else: truss(bm, stations([a, b], 2.4), h=1.3, w=1.8, r_chord=0.13, r_diag=0.07, diag=False)
        self.add("Balance_Arm", bm, bevel=0.0, slew=True)
        # --- fleche courbe : caisson exterieur + membrure interieure treillis
        pts = [apex, P(410, 32), P(445, 54), P(470, 100), P(485, 150), P(487, 165)]
        bm = bmesh.new()
        if L == 1: sweep(bm, pts, [(1.3, 1.3)] * 6)
        else:
            sweep(bm, pts, [(1.4, 0.8), (1.4, 0.8), (1.3, 0.75), (1.2, 0.7), (1.1, 0.65), (1.0, 0.6)])
        self.add("Jib_Main", bm, bevel=0.06, segments=2, slew=True)
        if L >= 2:
            bm = bmesh.new()
            st = stations([Vector(p) for p in pts], 1.9)
            st = [(p, t) for p, t in st]
            truss(bm, st, h=1.9, w=1.3, r_chord=0.09, r_diag=0.05, diag=False)
            self.add("Jib_Lattice", bm, bevel=0.0, slew=True)
        bm = bmesh.new()
        if L == 1: add_box(bm, P(475, 170), (2.2, 1.8, 1.6))
        else: prism(bm, [(-1.1, 0.8), (1.1, 0.8), (0.75, -0.85), (-0.75, -0.85)], -1.0, 1.0, Matrix.Translation(P(475, 170)))
        self.add("Jib_Tip", bm, self.grey, bevel=0.05, segments=2, slew=True)
        bm = bmesh.new(); tube(bm, P(487, 185), P(493, 262), 0.05, 8)
        self.add("Hoist_Rope", bm, self.dark, slew=True)
        bm = bmesh.new()
        if L == 1: add_box(bm, P(494, 277), (0.6, 0.6, 1.6))
        else:
            add_box(bm, P(494, 270), (0.7, 0.9, 0.9)); add_cyl(bm, P(494, 262), 0.28, 0.9, 'Y', 24)
            tube(bm, P(494, 278), P(494, 290), 0.09, 12)
        self.add("Hook_Block", bm, self.dark, bevel=0.03, segments=2, slew=True)
        if L >= 2:                                                                       # axes d'articulation
            bm = bmesh.new()
            for q, ln, r in ((apex, 2.4, 0.38), (P(280, 266), 2.6, 0.4), (P(85, 218), 2.2, 0.32)):
                add_cyl(bm, q, r, ln, 'Y', 24)
            self.add("Pivot_Pins", bm, self.dark, bevel=0.02, slew=True)

    # ---- etape 3 : details tertiaires ---------------------------------------------------
    def details(self):
        yel, red, dark, grey = self.yel, self.red, self.dark, self.grey
        top = lambda x: (3.3 + (x + 7.8) / 4.6 * 0.825) if x < -3.2 else (4.125 if x <= 3.2 else 4.125 - (x - 3.2) / 6.04 * 1.225)
        # portique : raidisseurs verticaux + plaque
        bm = bmesh.new()
        for y in (-3.0, 3.0):
            for sy in (-1, 1):
                for x in [-7.0 + 1.5 * i for i in range(11)]:
                    h = top(x) - 1.4 - 0.08; add_box(bm, (x, y + sy * 0.93, 1.4 + h / 2 + 0.04), (0.12, 0.2, h))
        self.add("Portal_Ribs", bm, bevel=0.015, segments=1)
        bm = bmesh.new(); add_box(bm, (0.5, -3.95, 2.0), (1.1, 0.04, 0.5))
        self.add("Name_Plate", bm, self.grey, bevel=0.01, segments=1)
        # bogies : roues + bandes de danger
        bm = bmesh.new(); st = bmesh.new()
        for y in (-3.0, 3.0):
            for x0, x1, n in ((-9.6, -2.8, 3), (1.9, 6.6, 2)):
                for i in range(n):
                    x = x0 + (x1 - x0) * (i + 0.5) / n; add_cyl(bm, (x, y, 0.68), 0.5, 2.1, 'Y', 32); add_cyl(bm, (x, y, 0.68), 0.6, 0.12 , 'Y', 32)
                for sy in (-1, 1):
                    xa = x0 + 0.1
                    while xa < x1:
                        poly = clip_x([(xa, 0.12), (xa + 0.38, 0.12), (xa + 0.38 + 0.55, 1.0), (xa + 0.55, 1.0)], x0 + 0.1, x1 - 0.1)
                        if len(poly) >= 3: prism(st, poly, y + sy * 0.955 - 0.012, y + sy * 0.955 + 0.012)
                        xa += 0.76
        self.add("Bogie_Wheels", bm, grey, bevel=0.02, segments=1); self.add("Hazard_Stripes", st, yel)
        # touret : rayons + cable enroule
        bm = bmesh.new(); c = P(88, 458, -4.4)
        for i in range(8):
            a = math.radians(i * 45); tube(bm, c + Vector((math.cos(a) * 0.3, 0, math.sin(a) * 0.3)), c + Vector((math.cos(a) * 2.0, 0, math.sin(a) * 2.0)), 0.04, 6)
        add_cyl(bm, c, 1.7, 0.7, 'Y', 48)
        self.add("Cable_Reel_Detail", bm, dark, bevel=0.01, segments=1)
        # piedestal : escalier + garde-corps (jaune), garde-corps du deck
        bm = bmesh.new(); stair(bm, Vector((0.6, 0, 7.2)), Vector((-2.8, 0, 9.4)), 0.95, 12, -3.3)
        self.add("Pedestal_Stair", bm, yel, bevel=0.01, segments=1)
        bm = bmesh.new()
        railing(bm, circle_pts(4.5, 9.6, 72, 20, 340), post_every=0.55, r=0.028)
        railing(bm, [Vector((-0.8, -4.2, 7.2)), Vector((3.3, -4.2, 7.2)), Vector((3.3, -1.7, 7.2))], post_every=1.0, r=0.028)
        self.add("Deck_Handrails", bm, yel)
        # contre-fleche : passerelle, diagonales, garde-corps
        a, b = P(85, 218), P(362, 32); t, n = frame(b - a)
        bm = bmesh.new(); sweep(bm, [a + n * 0.72, b + n * 0.72], [(1.9, 0.06), (1.9, 0.06)])
        self.add("Balance_Walkway", bm, grey, bevel=0.01, segments=1, slew=True)
        bm = bmesh.new()
        for sy in (-1, 1):
            railing(bm, [a + n * 0.75 + Y * sy * 0.95, b + n * 0.75 + Y * sy * 0.95], h=1.0, post_every=1.4, r=0.022, up=n)
        self.add("Balance_Handrails", bm, red, slew=True)
        bm = bmesh.new(); truss(bm, stations([a, b], 2.4), h=1.3, w=1.8, r_chord=0.0, r_diag=0.07, diag=True, top_solid=True)
        self.add("Balance_Diagonals", bm, red, slew=True)
        # mat : echelle + garde-corps
        sa, sb = P(275, 270), P(376, 45); sn = frame(sb - sa)[1] * 2.15       # decalee a gauche du mat, comme sur la photo
        bm = bmesh.new(); ladder(bm, sa + (sb - sa) * 0.22 + sn, sa + (sb - sa) * 0.93 + sn, 1.05, -1.1)
        for k in (0.3, 0.55, 0.8):
            q = sa + (sb - sa) * k; tube(bm, q + sn, q + Vector((0, 0, 0)) - Y * 0.0, 0.035, 6)
        self.add("Strut_Ladder", bm, yel, slew=True)
        # fleche : diagonales, garde-corps, poulies
        pts = [P(372, 26), P(410, 32), P(445, 54), P(470, 100), P(485, 150), P(487, 165)]
        bm = bmesh.new(); truss(bm, stations(pts, 1.9), h=1.9, w=1.3, r_chord=0.0, r_diag=0.05, diag=True, top_solid=True)
        self.add("Jib_Diagonals", bm, red, slew=True)
        bm = bmesh.new()
        st = stations(pts, 1.1)
        for sy in (-1, 1):
            q = [p + frame(t)[1] * 0.7 + Y * sy * 0.7 for p, t in st]; upv = [frame(t)[1] for p, t in st]
            for i in range(len(q)): tube(bm, q[i], q[i] + upv[i] * 0.9, 0.02, 6)
            for i in range(len(q) - 1): tube(bm, q[i] + upv[i] * 0.9, q[i + 1] + upv[i + 1] * 0.9, 0.02, 6)
        self.add("Jib_Handrails", bm, red, slew=True)
        bm = bmesh.new(); c = P(475, 172)
        for dx in (-0.65, 0.0, 0.65): add_cyl(bm, c + Vector((dx, 0, -0.3)), 0.38, 0.5, 'Y', 32)
        add_cyl(bm, c + Vector((0, 0, 0.55)), 0.3, 1.9, 'Y', 24)
        self.add("Jib_Sheaves", bm, dark, bevel=0.015, segments=1, slew=True)
        # crochet : double cable + poulies + crochet
        bm = bmesh.new()
        for dy in (-0.18, 0.18): tube(bm, P(487, 185, dy), P(493, 255, dy), 0.04, 8)
        self.add("Hoist_Ropes", bm, dark, slew=True)
        bm = bmesh.new(); h0 = P(494, 292)
        arc = [h0 + Vector((0.28 * math.sin(math.radians(a)), 0, -0.28 * (1 - math.cos(math.radians(a))) - 0.0)) for a in range(0, 230, 25)]
        for i in range(len(arc) - 1): tube(bm, arc[i], arc[i + 1], 0.07, 8)
        self.add("Hook", bm, grey, slew=True)

    def _box(self, c, size):
        bm = bmesh.new(); add_box(bm, c, size); return bm

    def build(self):
        self.portal(); self.pedestal(); self.house(); self.linkage()
        if self.L >= 3: self.details()
        return self

GROUPS = [("01_Portal", ("Portal", "Bogie", "Cable_Reel", "Hazard", "Name_Plate")),
          ("02_Pedestal", ("Pedestal", "Slew_", "Deck_")),
          ("03_Slewing_House", ("Machinery", "House_", "Operator", "Cab_", "Counterweight")),
          ("04_Linkage", ("Strut", "Balance", "Jib", "Pivot", "Hoist", "Hook"))]

def finalize(cr):
    """Etape 4 : collections par assemblage, UV, marquage final."""
    subs = {n: collection(n, cr) for n, _ in GROUPS}
    for ob in list(cr.objects):
        for n, keys in GROUPS:
            if ob.name.startswith(keys):
                cr.objects.unlink(ob); subs[n].objects.link(ob); break
    vl = bpy.context.view_layer
    for n, c in subs.items():
        for ob in c.objects:
            if ob.type != 'MESH': continue
            vl.objects.active = ob; ob.select_set(True)
            bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.003)
            bpy.ops.object.mode_set(mode='OBJECT'); ob.select_set(False)

def build(L):
    sc = reset_scene()
    ref = collection("REF"); cr = collection("CRANE")
    build_ground(ref); build_human(ref, (11.0, -6.5, 0.0))
    Crane(L, cr).build()
    if L >= 4: finalize(cr)
    return sc

if __name__ == "__main__":
    from playblast import run
    L = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    sc = build(L)
    run(sc, L, HERE)
