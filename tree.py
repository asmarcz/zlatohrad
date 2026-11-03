# Builds the tile board as a parametric PartDesign tree in the active document:
# a "Params" spreadsheet and a "Board" body whose sketches are fully constrained
# by expressions on that spreadsheet. Edit a cell and recompute to change it.
# Run inside FreeCAD (bridge/fc.py run tree.py). Deletes and rebuilds both.
#
# Layout, with the board centre on a tile corner:
#   tile centres at ((i+1/2)P, (j+1/2)P), tile corners at (iP, jP)
#   an octagonal hole sits on every tile centre and every tile corner
#   a square pad sits on every tile edge midpoint, with a groove along the seam
#   the board outline runs through the centres of the outer tiles (+-1.5P)
# Edge connectors: left/right have a dovetail tab in the middle and sockets at
# +-P; top/bottom have tabs at +-P and a socket in the middle.

import math
import re
import time

import FreeCAD as App
import Part
import Sketcher

V = App.Vector

# (alias, value or =formula, note)
PARAMS = [
    ("Tile", "52", "side of the square tiles"),
    ("Gap", "0.5", "space between neighbouring tiles"),
    ("Pitch", "=Tile + Gap", "tile pitch"),
    ("Thickness", "3", "board thickness"),
    ("PadSize", "20", "side of the square pads under the tile edges"),
    ("Bar", "5.5", "width of the diagonal bars"),
    ("TabBase", "10", "dovetail width at the board edge"),
    ("TabEnd", "13", "dovetail width at its tip"),
    ("TabLen", "=PadSize / 2", "dovetail length: fills the neighbour's half pad"),
    ("Fit", "0.2", "clearance around the dovetail in the socket"),
    ("GrooveW", "0.6", "seam groove width"),
    ("GrooveD", "0.4", "seam groove depth"),
    ("Half", "=1.5 * Pitch", "half the board side, without tabs"),
    ("OctH", "=Pitch / 2 - PadSize / 2", "hole centre to the flats facing the pads"),
    ("OctC", "=PadSize / 2 - Bar / sqrt(2)", "half length of those flats"),
]
ALIASES = [a for a, _, _ in PARAMS]
NAME = re.compile(r"\b(" + "|".join(ALIASES) + r")\b")

values = {"sqrt": math.sqrt}
for alias, value, _ in PARAMS:
    values[alias] = eval(value.lstrip("="), {}, values)


def expr(e):
    """FreeCAD expression text for e, with aliases pointing at the spreadsheet."""
    return NAME.sub(r"Params.\1", e)


def num(e):
    return eval(e, {}, values)


def add(a, b):
    return f"{a} + ({b})"


def sub(a, b):
    return f"{a} - ({b})"


def neg(a):
    return f"-({a})"


def poly(sketch, points):
    """Closed polygon whose every vertex is pinned to the origin by expressions."""
    first = len(sketch.Geometry)
    n = len(points)
    for k in range(n):
        (x0, y0), (x1, y1) = points[k], points[(k + 1) % n]
        sketch.addGeometry(
            Part.LineSegment(V(num(x0), num(y0), 0), V(num(x1), num(y1), 0))
        )
    for k in range(n):
        sketch.addConstraint(
            Sketcher.Constraint("Coincident", first + k, 2, first + (k + 1) % n, 1)
        )
    for k, (x, y) in enumerate(points):
        for kind, e in (("DistanceX", x), ("DistanceY", y)):
            i = sketch.addConstraint(
                Sketcher.Constraint(kind, -1, 1, first + k, 1, num(e))
            )
            sketch.setExpression(f".Constraints[{i}]", expr(e))


def tab_points(along):
    """Dovetail sticking out of the board. `along` maps the outward distance and
    the sideways offset to (x, y) expression strings."""
    a, b, L = "TabBase / 2", "TabEnd / 2", "TabLen"
    local = [("-0.5", a), ("0", a), (L, b), (L, neg(b)), ("0", neg(a)), ("-0.5", neg(a))]
    return [along(u, v) for u, v in local]


def socket_points(along):
    """Socket into the board, Fit wider than the tab on each side, running through
    the half pad into the next hole. `along` maps the inward distance."""
    a, b, L = "TabBase / 2 + Fit", "TabEnd / 2 + Fit", "TabLen"
    deep = add(L, "0.3")
    local = [
        ("-1", a), ("0", a), (L, b), (deep, b),
        (deep, neg(b)), (L, neg(b)), ("0", neg(a)), ("-1", neg(a)),
    ]  # fmt: skip
    return [along(u, v) for u, v in local]


def octagon_points(cx, cy):
    h, c = "OctH", "OctC"
    local = [(h, c), (c, h), (neg(c), h), (neg(h), c),
             (neg(h), neg(c)), (neg(c), neg(h)), (c, neg(h)), (h, neg(c))]  # fmt: skip
    return [(add(cx, x), add(cy, y)) for x, y in local]


def rect_points(cx, cy, hx, hy):
    return [(sub(cx, hx), sub(cy, hy)), (add(cx, hx), sub(cy, hy)),
            (add(cx, hx), add(cy, hy)), (sub(cx, hx), add(cy, hy))]  # fmt: skip


def remove_old(doc):
    for name in ("Board", "Params"):
        obj = doc.getObject(name)
        if obj is None:
            continue
        if obj.TypeId == "PartDesign::Body":
            origin = [obj.Origin.Name] + [f.Name for f in obj.Origin.OriginFeatures]
            obj.removeObjectsFromDocument()
            doc.removeObject(obj.Name)
            for name in origin:
                if doc.getObject(name):
                    doc.removeObject(name)
        else:
            doc.removeObject(obj.Name)


def build(doc):
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    for row, (alias, value, note) in enumerate(PARAMS, start=1):
        sheet.set(f"A{row}", alias)
        sheet.set(f"B{row}", value)
        sheet.setAlias(f"B{row}", alias)
        sheet.set(f"C{row}", note)
    doc.recompute()

    body = doc.addObject("PartDesign::Body", "Board")
    xy = body.Origin.OriginFeatures[3]  # XY_Plane
    assert xy.Role == "XY_Plane", xy.Role

    def sketch(name, points_list, top=False):
        s = body.newObject("Sketcher::SketchObject", name)
        s.AttachmentSupport = [(xy, "")]
        s.MapMode = "FlatFace"
        if top:
            # Cutting sketches sit on the top face and cut downward.
            s.setExpression(".AttachmentOffset.Base.z", "Params.Thickness")
        for pts in points_list:
            poly(s, pts)
        return s

    def pad(name, s):
        f = body.newObject("PartDesign::Pad", name)
        f.Profile = s
        f.setExpression("Length", "Params.Thickness")
        return f

    def pocket(name, s, depth=None):
        f = body.newObject("PartDesign::Pocket", name)
        f.Profile = s
        if depth is None:
            f.Type = "ThroughAll"
        else:
            f.setExpression("Length", depth)
        return f

    def mirror(name, feature, s, axis):
        m = body.newObject("PartDesign::Mirrored", name)
        m.Originals = [feature]
        m.MirrorPlane = (s, [axis])
        body.Tip = m  # patterns don't take the tip themselves; the next feature must follow them
        return m

    def grid(name, feature, s, nx, ny):
        g = body.newObject("PartDesign::LinearPattern", name)
        g.Originals = [feature]
        g.Mode = g.Mode2 = "Extent"
        g.Direction = (s, ["H_Axis"])
        g.Direction2 = (s, ["V_Axis"])
        g.setExpression("Length", f"{nx - 1} * Params.Pitch")
        g.setExpression("Length2", f"{ny - 1} * Params.Pitch")
        g.Occurrences, g.Occurrences2 = nx, ny
        body.Tip = g
        return g

    s = sketch("OutlineSketch", [rect_points("0", "0", "Half", "Half")])
    pad("Outline", s)

    s = sketch("SideTabSketch", [tab_points(lambda u, v: (add("Half", u), v))])
    mirror("SideTabs", pad("SideTab", s), s, "V_Axis")

    pts = [
        tab_points(lambda u, v, cx=cx: (add(cx, v), add("Half", u)))
        for cx in ("Pitch", "-Pitch")
    ]
    s = sketch("EndTabSketch", pts)
    mirror("EndTabs", pad("EndTab", s), s, "H_Axis")

    # Grooves come before the sockets so every groove still has material to cut.
    # Seams between tiles side by side run along y, seams between stacked tiles along x.
    s = sketch("YGrooveSketch", [rect_points("-Pitch", "-1.5 * Pitch", "GrooveW / 2", "PadSize / 2")], top=True)
    grid("YGrooves", pocket("YGroove", s, "Params.GrooveD"), s, 3, 4)
    s = sketch("XGrooveSketch", [rect_points("-1.5 * Pitch", "-Pitch", "PadSize / 2", "GrooveW / 2")], top=True)
    grid("XGrooves", pocket("XGroove", s, "Params.GrooveD"), s, 4, 3)

    pts = [
        socket_points(lambda u, v, cy=cy: (sub("Half", u), add(cy, v)))
        for cy in ("Pitch", "-Pitch")
    ]
    s = sketch("SideSocketSketch", pts, top=True)
    mirror("SideSockets", pocket("SideSocket", s), s, "V_Axis")

    s = sketch("EndSocketSketch", [socket_points(lambda u, v: (v, sub("Half", u)))], top=True)
    mirror("EndSockets", pocket("EndSocket", s), s, "H_Axis")

    s = sketch("CornerHoleSketch", [octagon_points("-Pitch", "-Pitch")], top=True)
    grid("CornerHoles", pocket("CornerHole", s), s, 3, 3)
    s = sketch("CentreHoleSketch", [octagon_points("-1.5 * Pitch", "-1.5 * Pitch")], top=True)
    last = grid("CentreHoles", pocket("CentreHole", s), s, 4, 4)
    last.Refine = True

    return sheet, body


doc = App.ActiveDocument
remove_old(doc)
sheet, body = build(doc)
start = time.time()
doc.recompute()
elapsed = time.time() - start

problems = [
    f"{o.Name}: {o.getStatusString()}"
    for o in body.Group
    if "Invalid" in o.State or "Error" in o.State
]
for o in body.Group:
    if o.isDerivedFrom("Sketcher::SketchObject"):
        if not o.FullyConstrained:
            problems.append(f"{o.Name}: not fully constrained")

shape = body.Shape
bb = shape.BoundBox
result = {
    "recompute_s": round(elapsed, 2),
    "problems": problems,
    "valid": shape.isValid(),
    "solids": len(shape.Solids),
    "size": [round(bb.XLength, 2), round(bb.YLength, 2), round(bb.ZLength, 2)],
    "volume_cm3": round(shape.Volume / 1000, 2),
}

try:
    import FreeCADGui as Gui

    for o in body.Group:
        o.Visibility = o == body.Tip
    body.Visibility = True
    Gui.ActiveDocument.ActiveView.viewTop()
    Gui.SendMsgToActiveView("ViewFit")
except Exception:
    pass
