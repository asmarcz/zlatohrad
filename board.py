# Tile board: 2x2 full tiles plus a half tile on every side, as an octagon lattice.
# Run inside FreeCAD (bridge/fc.py run board.py). Rebuilds the "Board" object
# in the active document every time.
#
# Layout, with the board centre on a tile corner:
#   tile centres at ((i+1/2)P, (j+1/2)P), tile corners at (iP, jP)
#   an octagonal hole sits on every tile centre and every tile corner
#   a square pad sits on every tile edge midpoint, with a groove along the seam
#   the board outline runs through the centres of the outer tiles (+-1.5P)
# Edge connectors: pads cut by the outline get a dovetail tab, the others a
# matching socket. Left/right: tab at the middle, sockets at +-P.
# Top/bottom: tabs at +-P, socket at the middle.

import math

import FreeCAD as App
import Part

V = App.Vector

TILE = 52.0  # side of the square tiles the board holds
GAP = 0.5  # space between neighbouring tiles
P = TILE + GAP  # tile pitch

T = 3.0  # board thickness
PAD = 20.0  # pad side
BAR = 5.5  # width of the diagonal bars between pads

TAB_BASE = 10.0  # dovetail width at the board edge
TAB_END = 13.0  # dovetail width at its tip
TAB_LEN = PAD / 2  # how far the dovetail sticks out: it fills the neighbour's half pad
FIT = 0.2  # clearance added around the socket

GROOVE_W = 0.6
GROOVE_D = 0.4

HALF = 1.5 * P  # half of the board side, not counting tabs
TALL = T + 2  # height of cutting tools, so they pass cleanly through
Z0 = -1


def prism(points, z=Z0, height=TALL):
    wire = Part.makePolygon([V(x, y, z) for x, y in points] + [V(*points[0], z)])
    return Part.Face(wire).extrude(V(0, 0, height))


def octagon(cx, cy):
    # Flats facing the axes sit at distance h from the centre (they meet the pads),
    # the diagonal flats at distance d (they leave a BAR-wide bar to the next hole).
    h = P / 2 - PAD / 2
    d = P / (2 * math.sqrt(2)) - BAR / 2
    c = d * math.sqrt(2) - h
    pts = [(h, c), (c, h), (-c, h), (-h, c), (-h, -c), (-c, -h), (c, -h), (h, -c)]
    return prism([(cx + x, cy + y) for x, y in pts])


def trapezoid(near, far, length, extra=0.0):
    # Pointing in +x from x=0: width `near` at x=0, `far` at x=length.
    a, b = near / 2, far / 2
    return [(-extra, a), (length, b), (length, -b), (-extra, -a)]


def placed(points, cx, cy, angle):
    # Rotate local points by `angle` (degrees) and move them to (cx, cy).
    r = math.radians(angle)
    cs, sn = math.cos(r), math.sin(r)
    return [(cx + x * cs - y * sn, cy + x * sn + y * cs) for x, y in points]


# Each edge: where it is, which way points outward, and where tabs and sockets go.
EDGES = [
    # (outward angle, point on edge for offset t)
    (180, lambda t: (-HALF, t)),  # left
    (0, lambda t: (HALF, t)),  # right
    (90, lambda t: (t, HALF)),  # top
    (270, lambda t: (t, -HALF)),  # bottom
]


def build():
    board = Part.makeBox(2 * HALF, 2 * HALF, T, V(-HALF, -HALF, 0))

    tabs, cuts = [], []
    for angle, at in EDGES:
        sideways = angle in (0, 180)
        tab_offsets = [0.0] if sideways else [-P, P]
        socket_offsets = [-P, P] if sideways else [0.0]
        for t in tab_offsets:
            x, y = at(t)
            # Overlap 0.01 into the board so the fuse is clean.
            pts = trapezoid(TAB_BASE, TAB_END, TAB_LEN, extra=0.01)
            tabs.append(prism(placed(pts, x, y, angle), z=0, height=T))
        for t in socket_offsets:
            x, y = at(t)
            # A socket points inward, is a bit wider than the tab and runs through
            # the whole half pad, so the tab tip ends flush with the next hole.
            pts = trapezoid(TAB_BASE + 2 * FIT, TAB_END + 2 * FIT, TAB_LEN + 1, extra=1)
            cuts.append(prism(placed(pts, x, y, angle + 180)))

    board = board.fuse(tabs)

    n = 3
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            cuts.append(octagon(i * P, j * P))  # tile corners
            cuts.append(octagon((i + 0.5) * P, (j + 0.5) * P))  # tile centres

    # Grooves along the seams, one per pad, kept inside the board outline.
    def groove(cx, cy, along_x):
        lx, ly = (PAD, GROOVE_W) if along_x else (GROOVE_W, PAD)
        x0, x1 = max(cx - lx / 2, -HALF), min(cx + lx / 2, HALF)
        y0, y1 = max(cy - ly / 2, -HALF), min(cy + ly / 2, HALF)
        return Part.makeBox(x1 - x0, y1 - y0, GROOVE_D + 1, V(x0, y0, T - GROOVE_D))

    for i in range(-2, 2):
        for j in range(-1, 2):
            # Horizontal tile edges: midpoint ((i+1/2)P, jP), seam runs along x.
            cuts.append(groove((i + 0.5) * P, j * P, along_x=True))
            # Vertical tile edges: midpoint (jP, (i+1/2)P), seam runs along y.
            cuts.append(groove(j * P, (i + 0.5) * P, along_x=False))

    return board.cut(cuts).removeSplitter()


doc = App.ActiveDocument
old = doc.getObject("Board")
if old:
    doc.removeObject(old.Name)
obj = doc.addObject("Part::Feature", "Board")
obj.Shape = build()
doc.recompute()

shape = obj.Shape
bb = shape.BoundBox
result = {
    "valid": shape.isValid(),
    "solids": len(shape.Solids),
    "size": [round(bb.XLength, 2), round(bb.YLength, 2), round(bb.ZLength, 2)],
    "volume_cm3": round(shape.Volume / 1000, 2),
}

try:
    import FreeCADGui as Gui

    Gui.ActiveDocument.ActiveView.viewTop()
    Gui.SendMsgToActiveView("ViewFit")
except Exception:
    pass
