"""Etape 5 : affinage des 4 zones d'apres les zooms de la photo de reference (voir README ecart)."""
import math, bmesh
from mathutils import Vector, Matrix
from crane_lib import *
from crane_build import Crane, RED, DARK, YEL, GLASS, GREY

def walkway(bm_deck, bm_rail, pts, spacing, w, off, post_h, tilt=25, rails=(0.5, 1.0), r=0.022):
    """Passerelle sur une polyligne : platelage + poteaux inclines vers l'exterieur + lisses (comme sur la photo)."""
    st = stations(pts, spacing); tl = math.radians(tilt)
    dp = [p + frame(t)[1] * off for p, t in st]
    sweep(bm_deck, dp, [(w, 0.06)] * len(dp))
    for sy in (-1, 1):
        tops = []
        for p, t in st:
            n = frame(t)[1]; base = p + n * off + Y * sy * w / 2
            top = base + (n * math.cos(tl) + Y * sy * math.sin(tl)) * post_h
            tube(bm_rail, base, top, r, 6); tops.append((base, top))
        for k in rails:
            for i in range(len(tops) - 1):
                a = tops[i][0] + (tops[i][1] - tops[i][0]) * k; b = tops[i + 1][0] + (tops[i + 1][1] - tops[i + 1][0]) * k
                tube(bm_rail, a, b, r * 0.9, 6)

def circ(r, dy=0, n=48):
    return [Vector((r * math.cos(2 * math.pi * i / n), dy, r * math.sin(2 * math.pi * i / n))) for i in range(n + 1)]

ARM = [P(*q) for q in ((100, 212), (130, 200), (160, 186), (190, 171), (220, 150), (250, 129), (280, 103), (310, 75), (340, 50), (366, 30))]   # courbe mesuree sur la photo

class Crane2(Crane):
    def __init__(self, L, coll):
        super().__init__(L, coll)
        self.white = material("White_paint", (0.85, 0.85, 0.80), 0.5)
        self.dred = material("Crane_red_dark", (0.30, 0.035, 0.025), 0.55)

    # ============================================================ ZONE 4 : base
    def portal(self):
        bm = bmesh.new()            # coque unique, effilee, face plate (comme la photo) ; haute au pied du piedestal
        prism(bm, [(-7.8, 1.4), (9.24, 1.4), (9.24, 3.35), (2.6, 4.125), (-2.0, 4.125), (-7.8, 3.2)], -3.85, 3.85)
        self.add("Portal_Frame", bm, bevel=0.12, segments=3)
        bm = bmesh.new()
        for y in (-3.0, 3.0):
            for x in (-7.1, 5.25): add_cyl(bm, (x, y, 0.85), 0.55, 1.1, segs=24)
        self.add("Portal_Legs", bm, bevel=0.03, segments=2)
        hz, bx, wh, st = bmesh.new(), bmesh.new(), bmesh.new(), bmesh.new()
        for y in (-3.0, 3.0):
            sy = -1 if y < 0 else 1
            for x0, x1 in ((-9.8, -5.0), (4.0, 6.4)):                  # plateaux a bandes de danger
                add_box(hz, ((x0 + x1) / 2, y, 0.45), (x1 - x0, 2.2, 0.9))
                xa = x0 + 0.1; yf = y + sy * 1.1
                while xa < x1:
                    poly = clip_x([(xa, 0.08), (xa + 0.4, 0.08), (xa + 1.1, 0.85), (xa + 0.7, 0.85)], x0 + 0.08, x1 - 0.08)
                    if len(poly) >= 3: prism(st, poly, yf - 0.012, yf + 0.012)
                    xa += 0.8
            for x0, x1 in ((-5.0, -2.4), (1.9, 4.0)): add_box(bx, ((x0 + x1) / 2, y, 0.6), (x1 - x0, 1.8, 0.8))   # caissons noirs
            for x in (-9.3, -5.6, -4.4, -3.0, 2.5, 3.5, 4.9, 5.9): add_cyl(wh, (x, y, 0.4), 0.38, 2.3, 'Y', 24)
        self.add("Bogie_Hazard_Plates", hz, self.dark, bevel=0.04, segments=2)
        self.add("Bogie_Wheel_Boxes", bx, self.dark, bevel=0.04, segments=2)
        self.add("Bogie_Wheels", wh, self.grey, bevel=0.02, segments=1)
        self.add("Hazard_Stripes", st, self.yel)
        bm = bmesh.new(); add_box(bm, (1.0, -3.87, 1.9), (1.5, 0.04, 0.7))
        self.add("Name_Plate", bm, self.grey, bevel=0.01, segments=1)

    def reel(self):
        c = P(88, 458, -4.5)
        bm = bmesh.new(); add_cyl(bm, (0, 0, 0), 1.3, 0.9, 'Y', 48)
        self.add("Cable_Reel_Drum", bm, self.dark, bevel=0.03, segments=2).location = c
        bm = bmesh.new(); add_cyl(bm, (0, 0, 0), 0.55, 1.1, 'Y', 32); add_cyl(bm, (0, -0.58, 0), 0.75, 0.1, 'Y', 32)
        self.add("Cable_Reel_Hub", bm, self.red, bevel=0.02, segments=2).location = c
        bm = bmesh.new()
        for dy in (-0.45, 0.45):
            for rr in (2.1, 1.95):
                pts = circ(rr, dy)
                for i in range(len(pts) - 1): tube(bm, pts[i], pts[i + 1], 0.035, 6)
            for i in range(24):
                a = 2 * math.pi * i / 24; tube(bm, Vector((1.3 * math.cos(a), dy, 1.3 * math.sin(a))), Vector((2.0 * math.cos(a), dy, 2.0 * math.sin(a))), 0.022, 5)
        for i in range(16):
            a = 2 * math.pi * i / 16; q = Vector((2.03 * math.cos(a), 0, 2.03 * math.sin(a)))
            tube(bm, q - Y * 0.45, q + Y * 0.45, 0.03, 5)
        self.add("Cable_Reel_Spokes", bm, self.grey, bevel=0.0).location = c
        bm = bmesh.new()                       # bras de fixation sur la coque + potence
        for dx in (-0.9, 0.9): tube(bm, Vector((c.x + dx, -3.85, c.z - 1.0)), Vector((c.x, -4.5, c.z)), 0.1, 8)
        tube(bm, Vector((c.x, -4.5, c.z)), Vector((c.x, -4.5, 1.5)), 0.07, 8)
        self.add("Cable_Reel_Support", bm, self.dark)

    # ============================================================ piedestal
    def pedestal(self):
        bm = bmesh.new()
        add_cyl(bm, (0, 0, 5.81), 2.54, 3.375, segs=8)
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(22.5), 3, 'Z'), verts=bm.verts)
        add_cyl(bm, (0, 0, 8.375), 2.4, 1.75, segs=48); add_cyl(bm, (0, 0, 7.62), 2.75, 0.25, segs=48)
        self.add("Pedestal", bm, bevel=0.06, segments=2)
        bm = bmesh.new(); add_box(bm, (-0.25, -2.37, 6.0), (1.5, 0.04, 2.8))
        self.add("White_Panel", bm, self.white, bevel=0.01, segments=1)
        bm = bmesh.new(); add_box(bm, (1.15, -3.3, 5.0), (4.1, 2.2, 0.12))              # corniche a garde-corps
        for x in (-0.8, 3.0):
            add_box(bm, (x, -4.3, 4.55), (0.12, 0.12, 0.85))
        self.add("Pedestal_Ledge", bm, self.grey, bevel=0.015, segments=1)
        bm = bmesh.new(); stair(bm, Vector((3.2, 0, 5.0)), Vector((5.6, 0, 4.125)), 0.95, 8, -3.3)
        self.add("Pedestal_Stair", bm, self.yel, bevel=0.01, segments=1)
        bm = bmesh.new()
        railing(bm, [Vector((-0.9, -2.3, 5.06)), Vector((-0.9, -4.4, 5.06)), Vector((3.2, -4.4, 5.06)), Vector((3.2, -2.3, 5.06))], post_every=0.9, r=0.028)
        for yy in (-3.8, -2.8): railing(bm, [Vector((3.2, yy, 5.0)), Vector((5.6, yy, 4.125))], h=1.0, post_every=0.8, r=0.026)
        ladder(bm, Vector((-1.0, 0, 5.1)), Vector((-3.0, 0, 9.2)), 0.7, -3.3)
        railing(bm, circle_pts(4.5, 9.6, 72, 20, 340), post_every=0.55, r=0.028)
        self.add("Pedestal_Rails_Ladder", bm, self.yel)
        bm = bmesh.new(); add_cyl(bm, (0, 0, 9.425), 4.6, 0.35, segs=64)
        for i in range(12): prism(bm, [(2.3, 9.25), (4.2, 9.25), (2.3, 7.9)], -0.1, 0.1, Matrix.Rotation(i * math.radians(30), 4, 'Z'))
        self.add("Slew_Deck", bm, self.red, bevel=0.03, segments=2)
        bm = bmesh.new(); add_cyl(bm, (0, 0, 10.0), 3.0, 0.85, segs=64)
        for z in (9.7, 10.3): add_cyl(bm, (0, 0, z), 3.12, 0.14, segs=64)
        self.add("Slew_Bearing", bm, self.dark, bevel=0.025, segments=2)

    # ============================================================ ZONE 1 + 3 : maison, contrepoids, cabine
    def _box(self, c, size):
        bm = bmesh.new(); add_box(bm, c, size); return bm

    def house(self):
        cuts = []
        for sy in (-1, 1):
            for x in (-6.6, -5.55): cuts.append(self._box((x, sy * 3.5, 14.15), (0.85, 0.4, 0.9)))
        bm = bmesh.new(); prism(bm, [(-8.4, 15.4), (1.9, 15.4), (1.9, 11.2), (-8.4, 11.2)], -3.5, 3.5)
        self.add("Machinery_House", bm, bevel=0.14, segments=4, slew=True, cuts=cuts)
        bm = bmesh.new(); prism(bm, [(-8.4, 11.2), (1.9, 11.2), (1.9, 10.45), (-7.0, 10.2), (-8.5, 10.6)], -3.45, 3.45)
        self.add("House_Skirt", bm, self.dred, bevel=0.05, segments=2, slew=True)
        bm = bmesh.new()                                    # bandes blanches (peinture) + cadre de fenetre
        for sy in (-1, 1):
            yf = sy * 3.51
            for x in (-6.0, -4.5, -2.9, -1.3, 0.3): add_box(bm, (x, yf, 13.85), (0.1, 0.02, 2.7))
            for (x, z, w, h) in ((-6.075, 14.65, 2.15, 0.09), (-6.075, 13.67, 2.15, 0.09), (-7.1, 14.16, 0.09, 1.07), (-6.07, 14.16, 0.09, 1.07), (-5.05, 14.16, 0.09, 1.07)):
                add_box(bm, (x, yf, z), (w, 0.03, h))
        self.add("House_White_Paint", bm, self.white, slew=True)
        bm = bmesh.new()
        for sy in (-1, 1):
            for x in (-6.6, -5.55): add_box(bm, (x, sy * 3.36, 14.15), (0.85, 0.03, 0.9))
        self.add("House_Glass", bm, self.glass, slew=True)
        # bosse en A-frame sur le toit + plateforme + panneau sombre
        hump = [P(*q) for q in ((150, 250), (163, 214), (198, 181), (224, 188), (236, 250))]
        bm = bmesh.new(); prism(bm, [(v.x, v.z) for v in hump], -1.5, 1.5)
        self.add("Roof_Hump", bm, bevel=0.1, segments=3, slew=True)
        bm = bmesh.new(); add_box(bm, (-1.8, 0, 15.45), (7.4, 5.8, 0.12)); add_box(bm, (-3.1, -2.75, 15.9), (2.6, 0.1, 0.55))
        self.add("Roof_Deck_Board", bm, self.dark, bevel=0.01, segments=1, slew=True)
        bm = bmesh.new()
        for sy in (-1, 1):
            a = P(205, 150, sy * 1.25); b = P(246, 240, sy * 1.25); tube(bm, a, b, 0.07, 8)
        self.add("Roof_Braces", bm, self.red, slew=True)
        bm = bmesh.new()
        railing(bm, [Vector((-8.25, -3.35, 15.46)), Vector((1.75, -3.35, 15.46)), Vector((1.75, 3.35, 15.46)), Vector((-8.25, 3.35, 15.46)), Vector((-8.25, -3.35, 15.46))], post_every=1.25, r=0.025)
        self.add("Roof_Rail", bm, self.yel, slew=True)
        # contrepoids : fut VERTICAL coiffe d'un couvercle
        bm = bmesh.new(); add_cyl(bm, (-7.6, 0, 16.9), 1.2, 2.3, 'Z', 48)
        for z in (16.35, 17.45): add_cyl(bm, (-7.6, 0, z), 1.27, 0.12, 'Z', 48)
        add_cyl(bm, (-7.6, 0, 18.1), 1.29, 0.14, 'Z', 48)
        self.add("Counterweight", bm, self.red, bevel=0.04, segments=3, slew=True)
        bm = bmesh.new(); add_box(bm, (-7.6, 0, 15.6), (2.6, 2.8, 0.4)); prism(bm, [(-8.6, 15.4), (-6.6, 15.4), (-7.0, 16.0), (-8.2, 16.0)], -1.0, 1.0)
        self.add("Counterweight_Base", bm, self.dred, bevel=0.04, segments=2, slew=True)
        # cabine : toit incline, vitres 2x2
        ccuts = []
        for x in (3.45, 4.25):
            for z in (12.15, 12.75): ccuts.append(self._box((x, -3.3, z), (0.65, 0.3, 0.5)))
        for y in (-2.55, -1.85):
            for z in (12.15, 12.75): ccuts.append(self._box((5.1, y, z), (0.3, 0.65, 0.5)))
        bm = bmesh.new(); prism(bm, [(2.7, 11.15), (5.1, 11.15), (5.1, 13.0), (4.75, 13.45), (3.0, 13.45), (2.7, 13.2)], -3.3, -1.1)
        self.add("Operator_Cab", bm, bevel=0.06, segments=2, slew=True, cuts=ccuts)
        bm = bmesh.new(); add_box(bm, (3.9, -2.2, 13.52), (2.7, 2.5, 0.12))
        self.add("Cab_Roof", bm, self.red, bevel=0.03, segments=2, slew=True)
        bm = bmesh.new()
        for x in (3.45, 4.25):
            for z in (12.15, 12.75): add_box(bm, (x, -3.18, z), (0.65, 0.03, 0.5))
        for y in (-2.55, -1.85):
            for z in (12.15, 12.75): add_box(bm, (4.98, y, z), (0.03, 0.65, 0.5))
        self.add("Cab_Glass", bm, self.glass, slew=True)

    # ============================================================ ZONE 2 : fleche, contre-fleche, mat
    def linkage(self):
        A = P(362, 20)
        bm = bmesh.new(); sweep(bm, [P(272, 283), P(330, 172), P(390, 52)], [(2.0, 1.9), (1.8, 1.25), (1.6, 0.85)])
        self.add("Strut_Main", bm, bevel=0.1, segments=3, slew=True)
        bm = bmesh.new(); prism(bm, [(-1.4 + A.x, -0.9 + A.z), (0.2 + A.x, -1.3 + A.z), (1.6 + A.x, -0.2 + A.z), (1.2 + A.x, 1.0 + A.z), (-0.6 + A.x, 1.3 + A.z), (-1.5 + A.x, 0.4 + A.z)], -1.0, 1.0)
        self.add("Apex_Node", bm, bevel=0.08, segments=3, slew=True)
        arm = ARM
        bm = bmesh.new(); sweep(bm, arm, [(1.7, 1.15)] * 3 + [(1.7, 1.05)] * 3 + [(1.7, 0.95)] * 2 + [(1.7, 0.9)] * 2)
        self.add("Balance_Arm", bm, bevel=0.07, segments=3, slew=True)
        jib = [P(366, 22), P(487, 172)]
        bm = bmesh.new(); sweep(bm, jib, [(1.5, 1.15), (1.3, 0.85)])
        self.add("Jib_Main", bm, bevel=0.07, segments=3, slew=True)
        bm = bmesh.new()                                    # triangle de haubanage fin
        for sy in (-1, 1):
            pts = [P(380, 28, sy * 0.55), P(437, 50, sy * 0.55), P(466, 138, sy * 0.55)]
            for a, b in zip(pts, pts[1:]): beam(bm, a, b, (0.14, 0.14))
        self.add("Jib_Pendant", bm, self.red, bevel=0.015, segments=1, slew=True)
        E = P(487, 172); tilt = Matrix.Rotation(math.radians(-15), 4, 'Y')
        bm = bmesh.new(); add_box(bm, E + Vector((-0.1, 0, -0.1)), (1.2, 1.7, 1.0))
        for dz in (-0.45, 0.05): prism(bm, [(0, dz - 0.09), (2.4, dz - 0.09), (2.4, dz + 0.09), (0, dz + 0.09)], -0.85, 0.85, Matrix.Translation(E + Vector((-1.7, 0, -0.9))) @ tilt)
        self.add("Jib_Tip_Fork", bm, self.grey, bevel=0.03, segments=2, slew=True)
        bm = bmesh.new(); c = E + Vector((0.4, 0, -0.95))
        for dx in (-0.35, 0.35): add_cyl(bm, c + Vector((dx, 0, 0)), 0.32, 1.2, 'Y', 24)
        self.add("Jib_Sheaves", bm, self.dark, bevel=0.015, segments=1, slew=True)
        bm = bmesh.new()
        for dy in (-0.18, 0.18): tube(bm, P(484, 187, dy), P(497, 255, dy), 0.04, 8)
        self.add("Hoist_Ropes", bm, self.dark, slew=True)
        bm = bmesh.new(); add_box(bm, P(497, 270), (0.7, 0.9, 0.9)); add_cyl(bm, P(497, 262), 0.28, 0.9, 'Y', 24)
        tube(bm, P(497, 278), P(497, 290), 0.09, 12)
        self.add("Hook_Block", bm, self.dark, bevel=0.03, segments=2, slew=True)
        bm = bmesh.new(); h0 = P(497, 292)
        arc = [h0 + Vector((0.28 * math.sin(math.radians(a)), 0, -0.28 * (1 - math.cos(math.radians(a))))) for a in range(0, 230, 25)]
        for i in range(len(arc) - 1): tube(bm, arc[i], arc[i + 1], 0.07, 8)
        self.add("Hook", bm, self.grey, slew=True)
        bm = bmesh.new()
        for q, ln, r in ((A, 2.4, 0.4), (P(272, 280), 2.6, 0.38), (P(390, 52), 2.2, 0.3), (P(100, 212), 2.2, 0.32)): add_cyl(bm, q, r, ln, 'Y', 24)
        self.add("Pivot_Pins", bm, self.dark, bevel=0.02, slew=True)

    def details(self):
        yel, red, grey = self.yel, self.red, self.grey
        sa, sb = P(272, 283), P(390, 52); sn = frame(sb - sa)[1] * 1.7
        bm = bmesh.new(); ladder(bm, sa + (sb - sa) * 0.2 + sn, sa + (sb - sa) * 0.93 + sn, 1.2, -1.0)
        for k in (0.35, 0.6, 0.85): q = sa + (sb - sa) * k; tube(bm, q + sn * 0.0, q + sn, 0.03, 6)
        self.add("Strut_Ladder", bm, yel, slew=True)
        arm = ARM
        d, r_ = bmesh.new(), bmesh.new(); walkway(d, r_, arm, 1.2, 2.2, 0.62, 1.05, 28)
        self.add("Balance_Walkway", d, grey, bevel=0.01, segments=1, slew=True); self.add("Balance_Rails", r_, red, slew=True)
        d, r_ = bmesh.new(), bmesh.new(); walkway(d, r_, [P(366, 22), P(487, 172)], 1.1, 1.9, 0.58, 1.2, 22, rails=(0.35, 0.7, 1.0))
        self.add("Jib_Walkway", d, grey, bevel=0.01, segments=1, slew=True); self.add("Jib_Rails", r_, red, slew=True)
        # diagonales de contreventement sous la contre-fleche (les tirants fins visibles sur la photo)
        bm = bmesh.new()
        for sy in (-1, 1):
            tube(bm, P(205, 150, sy * 0.7), P(235, 215, sy * 0.7), 0.05, 6); tube(bm, P(250, 115, sy * 0.7), P(236, 225, sy * 0.7), 0.05, 6)
        self.add("Balance_Stays", bm, red, slew=True)

    def build(self):
        self.portal(); self.reel(); self.pedestal(); self.house(); self.linkage(); self.details()
        return self
