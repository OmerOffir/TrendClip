"""Price Tag Why's hand-drawn pictures: products (a can, a bottle, a cereal box, a chip bag, a coffee cup, a phone...)
and the things a reason is about (a truck, a factory, a family, an aluminum sheet...), drawn by the marker hand as big
line drawings with marker colour washed in. Our own drawings: never a brand's logo or label, only the shape and the
words the script gives (label: ["ICED", "TEA"]).

A drawing is plain geometry in its own units (DRAWINGS[name]() -> Drawing); modules.pw_render scales it into a box.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Pt = tuple[float, float]


@dataclass
class Drawing:
    w: float
    h: float
    strokes: list[tuple[list[Pt], str]] = field(default_factory=list)  # (points, role: ink | accent | red | thin)
    washes: list[tuple[list[Pt], str]] = field(default_factory=list)  # (polygon, role) coloured in, before the strokes
    label_at: tuple[float, float, float] | None = None  # (x, y, width) where the label words go
    body: list[Pt] = field(default_factory=list)  # the outer silhouette, filled white on a thumbnail (modules.pw_thumb)
    label_rot: float = 0.0


def ellipse(cx: float, cy: float, rx: float, ry: float, a0: float = 0, a1: float = 360, n: int = 36) -> list[Pt]:
    return [(cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a)))
            for a in (a0 + (a1 - a0) * k / n for k in range(n + 1))]


def rect(x0: float, y0: float, x1: float, y1: float) -> list[Pt]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def rrect(x0: float, y0: float, x1: float, y1: float, r: float) -> list[Pt]:
    pts: list[Pt] = []
    for cx, cy, a in ((x1 - r, y0 + r, 270), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        pts += ellipse(cx, cy, r, r, a, a + 90, 6)
    return pts + [pts[0]]


def poly(*pts: Pt) -> list[Pt]:
    return list(pts)


def can() -> Drawing:
    d = Drawing(100, 210)
    d.body = ellipse(50, 14, 40, 9, 180, 360, 12) + [(94, 30)] + ellipse(50, 188, 44, 11, 0, 180, 14) + [(6, 30)]
    d.washes.append((ellipse(50, 70, 44, 8, 0, 180) + ellipse(50, 150, 44, 8, 180, 0), "accent"))
    d.strokes += [(ellipse(50, 14, 40, 9), "ink"), (ellipse(50, 14, 31, 6), "thin"), (ellipse(56, 13, 9, 3.5), "thin"),
                  (poly((10, 14), (6, 30), (6, 188)), "ink"), (poly((90, 14), (94, 30), (94, 188)), "ink"),
                  (ellipse(50, 188, 44, 11, 0, 180), "ink"), (ellipse(50, 70, 44, 8, 0, 180), "ink"),
                  (ellipse(50, 150, 44, 8, 0, 180), "ink"), (poly((18, 40), (18, 60)), "thin"), (poly((18, 162), (18, 178)), "thin")]
    d.label_at = (50, 112, 76)
    return d


def bottle() -> Drawing:
    d = Drawing(100, 240)
    d.body = [(37, 0), (63, 0), (60, 52), (86, 92), (88, 228), (82, 238), (18, 238), (12, 228), (14, 92), (40, 52)]
    d.washes.append((rect(12, 125, 88, 185)[:-1], "accent"))
    d.strokes += [(rrect(37, 0, 63, 20, 4), "ink"), (poly((40, 20), (40, 52), (14, 92), (12, 228)), "ink"),
                  (poly((60, 20), (60, 52), (86, 92), (88, 228)), "ink"), (poly((12, 228), (18, 238), (82, 238), (88, 228)), "ink"),
                  (poly((12, 125), (88, 125)), "ink"), (poly((12, 185), (88, 185)), "ink"), (poly((24, 100), (22, 115)), "thin")]
    d.label_at = (50, 155, 70)
    return d


def box() -> Drawing:
    d = Drawing(150, 200)
    d.body = [(0, 26), (34, 0), (148, 0), (148, 174), (114, 200), (0, 200)]
    d.washes.append((rrect(14, 60, 100, 150, 10)[:-1], "accent"))
    d.strokes += [(rect(0, 26, 114, 200), "ink"), (poly((0, 26), (34, 0), (148, 0), (114, 26)), "ink"),
                  (poly((114, 200), (148, 174), (148, 0)), "ink"), (rrect(14, 60, 100, 150, 10), "ink")]
    d.label_at = (57, 105, 80)
    return d


def bag() -> Drawing:
    d = Drawing(140, 200)
    d.body = [(8, 10), (132, 10), (138, 60), (140, 100), (138, 140), (132, 190), (8, 190), (2, 140), (0, 100), (2, 60)]
    zig_top = [(8 + k * 8, 10 + (5 if k % 2 else 0)) for k in range(16)]
    zig_bot = [(8 + k * 8, 190 - (5 if k % 2 else 0)) for k in range(16)]
    d.washes.append((ellipse(70, 100, 42, 40), "accent"))
    d.strokes += [(zig_top, "ink"), (zig_bot, "ink"), ([(8, 18), (2, 60), (0, 100), (2, 140), (8, 182)], "ink"),
                  ([(132, 18), (138, 60), (140, 100), (138, 140), (132, 182)], "ink"), (poly((8, 22), (132, 22)), "thin"),
                  (poly((8, 178), (132, 178)), "thin"), (ellipse(70, 100, 42, 40), "ink")]
    d.label_at = (70, 100, 70)
    return d


def cup() -> Drawing:
    d = Drawing(120, 190)
    d.body = [(26, 0), (94, 0), (94, 14), (120, 14), (120, 34), (112, 34), (96, 190), (24, 190), (8, 34), (0, 34), (0, 14), (26, 14)]
    d.washes.append((poly((14, 75), (106, 75), (101, 130), (19, 130)), "accent"))
    d.strokes += [(rrect(26, 0, 94, 14, 5), "ink"), (rrect(0, 14, 120, 34, 8), "ink"), (poly((8, 34), (24, 190), (96, 190), (112, 34)), "ink"),
                  (poly((14, 75), (106, 75)), "ink"), (poly((19, 130), (101, 130)), "ink")]
    d.label_at = (60, 102, 76)
    return d


def phone() -> Drawing:
    d = Drawing(110, 210)
    d.body = rrect(0, 0, 110, 210, 18)
    d.washes.append((rrect(10, 22, 100, 188, 8)[:-1], "accent"))
    d.strokes += [(rrect(0, 0, 110, 210, 18), "ink"), (rrect(10, 22, 100, 188, 8), "thin"), (rrect(40, 8, 70, 15, 3), "thin"),
                  (ellipse(55, 199, 6, 6), "thin")]
    d.label_at = (55, 105, 76)
    return d


def jar() -> Drawing:
    d = Drawing(120, 170)
    d.body = [(16, 0), (104, 0), (104, 24), (112, 44), (112, 160), (104, 170), (16, 170), (8, 160), (8, 44), (16, 24)]
    d.washes.append((rect(8, 70, 112, 130)[:-1], "accent"))
    d.strokes += [(rrect(16, 0, 104, 24, 6), "ink"), (poly((20, 24), (8, 44), (8, 160), (16, 170), (104, 170), (112, 160), (112, 44), (100, 24)), "ink"),
                  (poly((8, 70), (112, 70)), "ink"), (poly((8, 130), (112, 130)), "ink")]
    d.label_at = (60, 100, 90)
    return d


def card() -> Drawing:
    d = Drawing(220, 140)
    d.body = rrect(0, 0, 220, 140, 14)
    d.washes.append((rect(0, 30, 220, 56)[:-1], "ink_wash"))
    d.strokes += [(rrect(0, 0, 220, 140, 14), "ink"), (rrect(18, 72, 58, 102, 5), "ink"), (poly((18, 120), (150, 120)), "thin"),
                  (poly((0, 30), (220, 30)), "thin"), (poly((0, 56), (220, 56)), "thin")]
    d.label_at = (150, 90, 110)
    return d


def money() -> Drawing:
    d = Drawing(230, 110)
    d.body = rect(0, 0, 230, 110)
    d.washes.append((rect(0, 0, 230, 110)[:-1], "accent"))
    d.strokes += [(rect(0, 0, 230, 110), "ink"), (rect(12, 10, 218, 100), "thin"), (ellipse(115, 55, 30, 34), "ink")]
    d.label_at = (115, 56, 50)
    return d


def truck() -> Drawing:
    d = Drawing(270, 140)
    d.washes.append((rect(0, 10, 172, 100)[:-1], "accent"))
    d.strokes += [(rect(0, 10, 172, 100), "ink"), (poly((178, 100), (178, 40), (230, 40), (256, 66), (256, 100), (178, 100)), "ink"),
                  (poly((190, 48), (226, 48), (244, 68), (190, 68), (190, 48)), "thin"),
                  (ellipse(42, 108, 17, 17), "ink"), (ellipse(132, 108, 17, 17), "ink"), (ellipse(222, 108, 17, 17), "ink"),
                  (poly((-6, 132), (276, 132)), "thin")]
    d.label_at = (86, 55, 140)
    return d


def factory() -> Drawing:
    d = Drawing(230, 180)
    d.washes.append((rect(0, 90, 230, 180)[:-1], "accent"))
    d.strokes += [(poly((0, 180), (0, 60), (50, 90), (50, 60), (100, 90), (100, 60), (150, 90), (230, 90), (230, 180), (0, 180)), "ink"),
                  (rect(168, 10, 198, 90), "ink"), (ellipse(190, -6, 14, 9), "thin"), (ellipse(212, -18, 12, 8), "thin"),
                  (rect(98, 128, 132, 180), "ink"), (rect(20, 112, 60, 142), "thin"), (rect(170, 112, 210, 142), "thin")]
    return d


def store() -> Drawing:
    d = Drawing(230, 190)
    d.washes.append((poly((10, 52), (220, 52), (220, 76), (10, 76)), "accent"))
    scallop = [p for k in range(7) for p in ellipse(10 + 15 + k * 30, 76, 15, 10, 180, 0, 8)]
    d.strokes += [(rect(10, 52, 220, 190), "ink"), (scallop, "ink"), (rrect(40, 4, 190, 44, 8), "ink"),
                  (rect(95, 120, 135, 190), "ink"), (rect(24, 105, 76, 155), "thin"), (rect(154, 105, 206, 155), "thin")]
    d.label_at = (115, 24, 130)
    return d


def family() -> Drawing:
    d = Drawing(250, 180)
    for x, s in ((40, 1.0), (105, 0.95), (165, 0.7), (215, 0.62)):
        top = 180 - 175 * s
        r = 16 * s
        neck, hip = top + 2 * r, top + 2 * r + 60 * s
        d.strokes += [(ellipse(x, top + r, r, r), "ink"), (poly((x, neck), (x, hip)), "ink"),
                      (poly((x - 26 * s, neck + 40 * s), (x, neck + 12 * s), (x + 26 * s, neck + 40 * s)), "ink"),
                      (poly((x - 20 * s, 180), (x, hip), (x + 20 * s, 180)), "ink")]
    d.washes.append((ellipse(125, 176, 120, 6), "accent"))
    return d


def sheet() -> Drawing:
    d = Drawing(250, 130)
    d.washes.append((poly((0, 60), (60, 20), (250, 20), (190, 60)), "accent"))
    d.strokes += [(poly((0, 60), (60, 20), (250, 20), (190, 60), (0, 60)), "ink"), (poly((0, 60), (0, 74), (190, 74), (190, 60)), "ink"),
                  (poly((190, 74), (250, 34), (250, 20)), "ink"), (poly((95, 128), (95, 84)), "red"), (poly((83, 98), (95, 84), (107, 98)), "red")]
    return d


def megaphone() -> Drawing:
    d = Drawing(210, 140)
    d.washes.append((poly((32, 50), (150, 10), (150, 130), (32, 90)), "accent"))
    d.strokes += [(poly((32, 50), (150, 10), (150, 130), (32, 90), (32, 50)), "ink"), (rect(0, 52, 32, 88), "ink"),
                  (ellipse(150, 70, 14, 60, 270, 450, 16), "ink"), (ellipse(170, 70, 18, 30, 300, 420, 8), "thin"),
                  (ellipse(186, 70, 22, 46, 300, 420, 8), "thin")]
    return d


def tv() -> Drawing:
    d = Drawing(210, 180)
    d.washes.append((rrect(16, 46, 194, 156, 10)[:-1], "accent"))
    d.strokes += [(poly((70, 0), (105, 36), (140, 0)), "ink"), (rrect(0, 36, 210, 166, 16), "ink"), (rrect(16, 46, 194, 156, 10), "thin"),
                  (poly((60, 166), (50, 180)), "ink"), (poly((150, 166), (160, 180)), "ink")]
    d.label_at = (105, 101, 150)
    return d


def coins() -> Drawing:
    d = Drawing(150, 150)
    d.washes.append((poly((5, 40), (145, 40), (145, 130), (5, 130)), "accent"))
    d.strokes += [(ellipse(75, 40, 70, 18), "ink")]
    for y in (40, 70, 100, 130):
        d.strokes.append((ellipse(75, y, 70, 18, 0, 180), "ink"))
    d.strokes += [(poly((5, 40), (5, 130)), "ink"), (poly((145, 40), (145, 130)), "ink")]
    d.label_at = (75, 40, 60)
    return d


def house() -> Drawing:
    d = Drawing(200, 190)
    d.washes.append((poly((10, 80), (100, 6), (190, 80)), "accent"))
    d.strokes += [(poly((10, 80), (100, 6), (190, 80), (10, 80)), "ink"), (rect(26, 80, 174, 190), "ink"), (rect(84, 124, 116, 190), "ink"),
                  (rect(40, 100, 72, 130), "thin"), (rect(128, 100, 160, 130), "thin")]
    return d


def calendar() -> Drawing:
    d = Drawing(170, 170)
    d.washes.append((rect(0, 20, 170, 56)[:-1], "accent"))
    d.strokes += [(rrect(0, 20, 170, 170, 10), "ink"), (poly((0, 56), (170, 56)), "ink"), (poly((45, 6), (45, 32)), "ink"),
                  (poly((125, 6), (125, 32)), "ink")]
    for x in (42, 85, 128):
        d.strokes.append((poly((x, 56), (x, 170)), "thin"))
    for y in (94, 132):
        d.strokes.append((poly((0, y), (170, y)), "thin"))
    return d


def receipt() -> Drawing:
    d = Drawing(140, 210)
    zig = [(140 - k * 10, 200 + (10 if k % 2 else 0)) for k in range(15)]
    d.strokes += [([(0, 200), (0, 0), (140, 0), (140, 200)] + zig, "ink")]
    for y in (30, 60, 90, 120):
        d.strokes.append((poly((16, y), (86, y)), "thin"))
        d.strokes.append((poly((104, y), (124, y)), "thin"))
    d.strokes.append((poly((16, 160), (124, 160)), "ink"))
    d.label_at = (70, 182, 100)
    return d


def cart() -> Drawing:
    d = Drawing(220, 180)
    d.washes.append((poly((40, 40), (210, 40), (190, 120), (60, 120)), "accent"))
    d.strokes += [(poly((0, 10), (28, 10), (60, 120), (190, 120), (210, 40), (40, 40)), "ink"), (poly((60, 120), (52, 146), (196, 146)), "ink"),
                  (ellipse(76, 164, 13, 13), "ink"), (ellipse(180, 164, 13, 13), "ink"), (poly((95, 40), (102, 120)), "thin"),
                  (poly((150, 40), (148, 120)), "thin"), (poly((48, 80), (200, 80)), "thin")]
    return d


def globe() -> Drawing:
    d = Drawing(180, 180)
    d.washes.append((ellipse(90, 90, 86, 86), "accent"))
    d.strokes += [(ellipse(90, 90, 86, 86), "ink"), (ellipse(90, 90, 40, 86), "thin"), (poly((4, 90), (176, 90)), "thin"),
                  (ellipse(90, 90, 80, 30, 180, 360, 12), "thin"), (ellipse(90, 90, 80, 30, 0, 180, 12), "thin")]
    return d


def clock() -> Drawing:
    d = Drawing(170, 170)
    d.washes.append((ellipse(85, 85, 80, 80), "accent"))
    d.strokes += [(ellipse(85, 85, 80, 80), "ink"), (poly((85, 85), (85, 32)), "ink"), (poly((85, 85), (122, 102)), "ink")]
    for k in range(12):
        a = math.radians(k * 30)
        d.strokes.append((poly((85 + 66 * math.cos(a), 85 + 66 * math.sin(a)), (85 + 74 * math.cos(a), 85 + 74 * math.sin(a))), "thin"))
    return d


def bank() -> Drawing:
    d = Drawing(220, 180)
    d.washes.append((poly((0, 50), (110, 4), (220, 50)), "accent"))
    d.strokes += [(poly((0, 50), (110, 4), (220, 50), (0, 50)), "ink"), (rect(10, 50, 210, 64), "ink"), (rect(0, 162, 220, 180), "ink")]
    for x in (34, 82, 138, 186):
        d.strokes += [(poly((x - 10, 64), (x - 10, 162)), "ink"), (poly((x + 10, 64), (x + 10, 162)), "ink")]
    return d


def sun() -> Drawing:
    d = Drawing(200, 200)
    d.washes.append((ellipse(100, 100, 48, 48), "yellow"))  # a sun is yellow whatever the scene's colour
    d.strokes.append((ellipse(100, 100, 48, 48), "ink"))
    for k in range(10):
        a = math.radians(k * 36 + 8)
        d.strokes.append((poly((100 + 62 * math.cos(a), 100 + 62 * math.sin(a)), (100 + 94 * math.cos(a), 100 + 94 * math.sin(a))), "ink"))
    return d


def eye() -> Drawing:
    d = Drawing(220, 120)
    lid = ellipse(110, 135, 125, 125, 216.87, 323.13, 16)  # two arcs meeting at the corners (10, 60) and (210, 60)
    low = ellipse(110, -15, 125, 125, 36.87, 143.13, 16)
    d.washes.append((ellipse(110, 60, 30, 30), "accent"))
    d.strokes += [(lid, "ink"), (low, "ink"), (ellipse(110, 60, 30, 30), "ink"), (ellipse(110, 60, 11, 11), "ink")]
    return d


def bulb() -> Drawing:
    d = Drawing(140, 210)
    glass = ellipse(70, 70, 62, 62, 140, 400, 30) + [(46, 140), (94, 140)]
    d.washes.append((ellipse(70, 70, 56, 56), "accent"))
    d.strokes += [(ellipse(70, 70, 62, 62, 140, 400, 30), "ink"), (poly((22, 110), (46, 140), (46, 170)), "ink"),
                  (poly((118, 110), (94, 140), (94, 170)), "ink"), (rect(46, 170, 94, 200), "ink"),
                  (poly((46, 182), (94, 182)), "thin"), (poly((56, 140), (62, 90), (70, 104), (78, 90), (84, 140)), "thin")]
    return d


def virus() -> Drawing:
    d = Drawing(200, 200)
    d.washes.append((ellipse(100, 100, 52, 52), "accent"))
    d.strokes.append((ellipse(100, 100, 52, 52), "ink"))
    for k in range(9):
        a = math.radians(k * 40)
        d.strokes += [(poly((100 + 52 * math.cos(a), 100 + 52 * math.sin(a)), (100 + 82 * math.cos(a), 100 + 82 * math.sin(a))), "ink"),
                      (ellipse(100 + 88 * math.cos(a), 100 + 88 * math.sin(a), 8, 8), "ink")]
    d.strokes += [(ellipse(84, 88, 9, 9), "thin"), (ellipse(116, 112, 7, 7), "thin")]
    return d


def book() -> Drawing:
    d = Drawing(240, 160)
    d.washes.append((poly((10, 20), (116, 34), (116, 150), (10, 136)), "accent"))
    d.strokes += [(poly((120, 34), (10, 20), (10, 136), (120, 150), (230, 136), (230, 20), (120, 34), (120, 150)), "ink")]
    for y in (52, 76, 100):
        d.strokes += [(poly((30, y - 10), (100, y)), "thin"), (poly((140, y), (210, y - 10)), "thin")]
    return d


def dress() -> Drawing:
    d = Drawing(170, 250)
    shape = [(62, 52), (54, 84), (60, 124), (16, 236), (154, 236), (110, 124), (116, 84), (108, 52)]
    d.body = shape + [shape[0]]
    d.washes.append((poly((60, 124), (16, 236), (154, 236), (110, 124)), "accent"))
    d.strokes += [(ellipse(85, 16, 10, 10, 180, 450, 14), "ink"), (poly((85, 26), (85, 34)), "ink"),
                  (poly((85, 34), (24, 54), (146, 54), (85, 34)), "ink"),
                  (poly((62, 52), (54, 84), (60, 124), (16, 236), (154, 236), (110, 124), (116, 84), (108, 52)), "ink"),
                  (ellipse(85, 52, 23, 18, 0, 180, 10), "ink"), (poly((60, 124), (110, 124)), "ink"),
                  (poly((70, 140), (52, 226)), "thin"), (poly((100, 140), (118, 226)), "thin")]
    return d


def flower() -> Drawing:
    d = Drawing(160, 250)
    bloom = [(40, 52), (56, 22), (80, 46), (104, 22), (120, 52), (116, 100), (80, 124), (44, 100), (40, 52)]
    d.body = bloom
    d.washes.append((bloom, "red"))
    d.strokes += [(bloom, "ink"), (poly((80, 46), (80, 112)), "thin"), (poly((80, 124), (80, 246)), "ink"),
                  (poly((80, 210), (40, 170), (36, 150), (60, 166), (80, 196)), "ink"),
                  (poly((80, 190), (124, 156), (130, 136), (104, 150), (80, 176)), "ink")]
    return d


def body(badge: bool = True) -> Drawing:
    """A bean-head person without the face and the arms (modules.pw_cast draws those alive): a big round head on a
    small shirt with a badge on the chest (the renderer writes a number on it; a map's local has none), short legs."""
    d = Drawing(160, 260)
    hx, hy = 80, 58
    shirt = [(46, 110), (114, 110), (124, 190), (36, 190), (46, 110)]
    d.body = ellipse(hx, hy, 50, 54, 180, 360, 20) + [(130, 58), (116, 108), (124, 190), (114, 250), (46, 250), (36, 190), (44, 108), (30, 58)]
    d.washes.append((shirt, "accent"))
    d.strokes += [(ellipse(hx, hy, 50, 54, 0, 360, 40), "ink"), (shirt, "ink"),
                  (poly((56, 190), (52, 248)), "ink"), (poly((104, 190), (108, 248)), "ink"),
                  (poly((40, 250), (62, 250)), "ink"), (poly((98, 250), (120, 250)), "ink")]
    if badge:
        d.strokes.append((ellipse(80, 150, 22, 22), "ink"))
        d.label_at = (80, 151, 60)  # the badge: the number fills it
    return d


def person() -> Drawing:
    """The whole bean-head person, still (the thumbnail): the body with a smiling face and hanging arms."""
    from modules import pw_cast

    d = body()
    d.strokes += pw_cast.static_strokes()
    return d


def stand() -> Drawing:
    """A market stall / kiosk with a striped awning: a coffee cart, a lemonade stand, a snack kiosk."""
    d = Drawing(240, 220)
    awning = [(10, 60), (40, 10), (200, 10), (230, 60), (10, 60)]
    d.washes.append((awning, "accent"))
    scallop = [p for k in range(6) for p in ellipse(10 + 18 + k * 37, 60, 18, 12, 180, 0, 8)]
    d.strokes += [(awning, "ink"), (scallop, "ink"), (poly((28, 60), (28, 130)), "ink"), (poly((212, 60), (212, 130)), "ink"),
                  (rect(28, 130, 212, 200), "ink"), (poly((60, 200), (54, 218)), "ink"), (poly((180, 200), (186, 218)), "ink"),
                  (poly((80, 10), (70, 60)), "thin"), (poly((160, 10), (170, 60)), "thin"), (rrect(90, 90, 150, 130, 6), "thin")]
    d.label_at = (120, 165, 150)
    return d


def machine() -> Drawing:
    """A vending machine: a tall box with a window of rows, a keypad and the slot at the bottom."""
    d = Drawing(160, 250)
    d.washes.append((rect(20, 24, 100, 160), "accent"))
    d.strokes += [(rrect(6, 6, 154, 244, 10), "ink"), (rect(20, 24, 100, 160), "ink"), (rect(112, 24, 142, 120), "thin"),
                  (rect(30, 180, 130, 224), "ink")]
    for y in (60, 96, 132):
        d.strokes.append((poly((20, y), (100, y)), "thin"))
    for y in (40, 56, 72):
        d.strokes.append((poly((118, y), (136, y)), "thin"))
    d.label_at = (80, 202, 90)
    return d


def contract() -> Drawing:
    """A signed paper: a page with lines, a signature and a stamp (a deal, a loan, an advance paid)."""
    d = Drawing(180, 230)
    page = rect(16, 10, 164, 220)
    d.body = page
    d.washes.append((ellipse(124, 180, 26, 26), "red"))
    d.strokes += [(page, "ink"), (poly((40, 46), (140, 46)), "ink")]
    for y in (76, 100, 124):
        d.strokes.append((poly((40, y), (140, y)), "thin"))
    sig = [(40 + k * 4.2, 176 + 12 * math.sin(k / 1.6) * (1 if k % 2 else 0.6)) for k in range(16)]
    d.strokes += [(sig, "ink"), (ellipse(124, 180, 26, 26), "red"), (ellipse(124, 180, 18, 18), "thin")]
    return d


DRAWINGS = dict(can=can, bottle=bottle, box=box, bag=bag, cup=cup, phone=phone, jar=jar, card=card, money=money,
                truck=truck, factory=factory, store=store, family=family, sheet=sheet, megaphone=megaphone, tv=tv,
                coins=coins, house=house, calendar=calendar, receipt=receipt, cart=cart, globe=globe, clock=clock, bank=bank,
                sun=sun, eye=eye, bulb=bulb, virus=virus, book=book, dress=dress, flower=flower,
                person=person, stand=stand, machine=machine, contract=contract)
# what each drawing looks like, for the script prompt: a drawing is only ever used for the thing it shows
LOOKS = dict(can="a drink can", bottle="a drink bottle", box="a cereal/food box", bag="a crisp/chip bag (snacks only)",
             cup="a takeaway coffee cup", phone="a smartphone", jar="a jar", card="a plastic card (credit, loyalty, gift)",
             money="a banknote", dress="a dress on a hanger (clothes)", flower="a tulip/flower", truck="a lorry/truck",
             factory="a factory", store="a shop front", family="a family of people", sheet="a metal sheet/roll",
             megaphone="a megaphone (ads, announcing, demands)", tv="a TV (ads, shows)", coins="a stack of coins",
             house="a house", calendar="a calendar (dates, months)", receipt="a till receipt (prices, a bill)",
             cart="a shopping cart", globe="the globe (the world, countries)", clock="a clock (time)",
             bank="a bank building (banks, a central bank)", sun="the sun (sunlight, heat)", eye="an eye (attention, seeing)",
             bulb="a light bulb (an idea, lighting)", virus="a virus (a disease)", book="an open book (a story, a name from a book)",
             person="a person standing (a buyer, a seller, a friend, a son)", stand="a market stall/kiosk (a coffee cart, a lemonade stand)",
             machine="a vending machine", contract="a signed paper (a deal, a loan, money paid in advance)")
PACKAGED = ("can", "bottle", "box", "bag", "cup", "jar", "card", "money")  # a label is what is printed on these
PRODUCTS = ("can", "bottle", "box", "bag", "cup", "phone", "jar", "card", "money", "dress", "flower")  # what a product scene can draw
# an icon (modules.pw_write.ICONS) a board names, drawn big instead when there is a drawing of it
FROM_ICON = dict(tv="tv", family="family", truck="truck", factory="factory", store="store", megaphone="megaphone",
                 calendar="calendar", house="house", receipt="receipt", shopping_cart="cart", globe="globe", clock="clock",
                 bank="bank", coin="coins", money_bag="coins", cash="money", dollar="money", credit_card="card",
                 mobile="phone", tea="cup", cup_straw="cup", eyes="eye", scroll="book", tulip="flower")


def scaled(name: str, box: tuple[float, float, float, float]) -> tuple[Drawing, float, float, float]:
    """The drawing and how it fits centred in box (x0, y0, x1, y1): (drawing, scale, dx, dy)."""
    return fit(DRAWINGS[name](), box)


def fit(d: Drawing, box: tuple[float, float, float, float]) -> tuple[Drawing, float, float, float]:
    """How a drawing fits centred in box: (drawing, scale, dx, dy)."""
    x0, y0, x1, y1 = box
    k = min((x1 - x0) / d.w, (y1 - y0) / d.h)
    return d, k, x0 + ((x1 - x0) - d.w * k) / 2, y0 + ((y1 - y0) - d.h * k) / 2


def hatch(polygon: list[Pt], step: float, inset: float) -> list[Pt]:
    """A back-and-forth marker path colouring a polygon in, kept `inset` inside its edge."""
    ys = [p[1] for p in polygon]
    out: list[Pt] = []
    y, flip = min(ys) + inset, False
    edges = list(zip(polygon, polygon[1:] + polygon[:1]))
    while y <= max(ys) - inset:
        xs = sorted(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]) for a, b in edges if (a[1] <= y < b[1]) or (b[1] <= y < a[1]))
        if len(xs) >= 2 and xs[-1] - xs[0] > 2 * inset:
            row = [(xs[0] + inset, y), (xs[-1] - inset, y)]
            out += row[::-1] if flip else row
            flip = not flip
        y += step
    return out
