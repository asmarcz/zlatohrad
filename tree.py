# Builds the tile board as a parametric PartDesign tree in the active document:
# a "Params" spreadsheet and a "Board" body whose sketches are fully constrained
# by expressions on that spreadsheet. Edit a cell and recompute to change it.
# Run inside FreeCAD (bridge/fc.py run tree.py). Deletes and rebuilds both.
#
# Layout, with the board centre on a tile corner:
#   tile centres at ((i+1/2)P, (j+1/2)P), tile corners at (iP, jP)
#   an octagonal hole sits on every tile centre and every tile corner
#   a square pad sits on every tile edge midpoint, with a raised rib along the
#   seam that keeps the tiles from sliding
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
    ("RibW", "1.2", "width of the separator ribs between tiles"),
    ("RibH", "2", "how far the ribs stand above the board"),
    ("TileFit", "0.25", "play between a tile and each rib"),
    ("Pitch", "=Tile + RibW + 2 * TileFit", "tile pitch"),
    ("Thickness", "3", "board thickness"),
    ("PadSize", "20", "side of the square pads under the tile edges"),
    ("Bar", "5.5", "width of the diagonal bars"),
    ("TabBase", "10", "dovetail width at the board edge"),
    ("TabEnd", "13", "dovetail width at its tip"),
    ("TabLen", "=PadSize / 2", "dovetail length: fills the neighbour's half pad"),
    ("Fit", "0.2", "clearance around the dovetail in the socket"),
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
        fix(sketch, "DistanceX", first + k, 1, x)
        fix(sketch, "DistanceY", first + k, 1, y)


def fix(sketch, kind, geo, pos, e):
    """Pin one coordinate of a sketch point to an expression."""
    i = sketch.addConstraint(Sketcher.Constraint(kind, -1, 1, geo, pos, num(e)))
    sketch.setExpression(f".Constraints[{i}]", expr(e))


def rib_section(sketch, cu):
    """Cross-section of a rib centred on sketch x = cu: straight sides standing on
    the board's top face and a fully rounded top, so it is gentle on fingers."""
    w, h, base = "RibW / 2", "RibH", "Thickness"
    shoulder = f"{base} + {h} - ({w})"  # where the sides meet the round top
    p = lambda x, y: V(num(x), num(y), 0)
    g = len(sketch.Geometry)
    sketch.addGeometry(Part.LineSegment(p(sub(cu, w), base), p(add(cu, w), base)))
    sketch.addGeometry(Part.LineSegment(p(add(cu, w), base), p(add(cu, w), shoulder)))
    circle = Part.Circle(p(cu, shoulder), V(0, 0, 1), num(w))
    sketch.addGeometry(Part.ArcOfCircle(circle, 0, math.pi))
    sketch.addGeometry(Part.LineSegment(p(sub(cu, w), shoulder), p(sub(cu, w), base)))
    bottom, right, top, left = g, g + 1, g + 2, g + 3
    sketch.addConstraint(Sketcher.Constraint("Coincident", bottom, 2, right, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", left, 2, bottom, 1))
    # The sides join the arc tangentially, which also makes them vertical.
    sketch.addConstraint(Sketcher.Constraint("Tangent", right, 2, top, 1))
    sketch.addConstraint(Sketcher.Constraint("Tangent", top, 2, left, 1))
    fix(sketch, "DistanceX", bottom, 1, sub(cu, w))
    fix(sketch, "DistanceY", bottom, 1, base)
    fix(sketch, "DistanceX", bottom, 2, add(cu, w))
    fix(sketch, "DistanceY", bottom, 2, base)
    fix(sketch, "DistanceX", top, 3, cu)
    fix(sketch, "DistanceY", top, 3, shoulder)
    i = sketch.addConstraint(Sketcher.Constraint("Radius", top, num(w)))
    sketch.setExpression(f".Constraints[{i}]", expr(w))


def rib_end_cut(sketch, end, sign):
    """Side view of what to cut off one rib end so it curves down to the board in
    a quarter circle of radius RibH. `end` is where the rib ends along sketch x,
    `sign` is +1 if the rib lies towards -x from there, -1 if towards +x."""
    r, base = "RibH", "Thickness"
    step = add if sign > 0 else sub  # away from the rib
    out = step(end, "1")  # a bit past the end, where there is no rib
    centre = sub(end, r) if sign > 0 else add(end, r)
    above = f"{base} + {r} + 1"
    p = lambda x, y: V(num(x), num(y), 0)
    g = len(sketch.Geometry)
    # A (over the arc top) -> B -> C (past the end, on the board) -> D (rib end)
    sketch.addGeometry(Part.LineSegment(p(centre, f"{base} + {r}"), p(centre, above)))
    sketch.addGeometry(Part.LineSegment(p(centre, above), p(out, above)))
    sketch.addGeometry(Part.LineSegment(p(out, above), p(out, base)))
    sketch.addGeometry(Part.LineSegment(p(out, base), p(end, base)))
    circle = Part.Circle(p(centre, base), V(0, 0, 1), num(r))
    a0, a1 = (0, math.pi / 2) if sign > 0 else (math.pi / 2, math.pi)
    sketch.addGeometry(Part.ArcOfCircle(circle, a0, a1))
    up, over, down, back, arc = g, g + 1, g + 2, g + 3, g + 4
    # Arc start/end: at the rib end (on the board) and at the top, in either order.
    at_end, at_top = (1, 2) if sign > 0 else (2, 1)
    for a, pa, b, pb in ((up, 2, over, 1), (over, 2, down, 1), (down, 2, back, 1),
                         (back, 2, arc, at_end), (arc, at_top, up, 1)):  # fmt: skip
        sketch.addConstraint(Sketcher.Constraint("Coincident", a, pa, b, pb))
    for x, y, geo, pos in ((centre, above, up, 2), (out, above, over, 2),
                           (out, base, down, 2), (end, base, back, 2)):  # fmt: skip
        fix(sketch, "DistanceX", geo, pos, x)
        fix(sketch, "DistanceY", geo, pos, y)
    fix(sketch, "DistanceX", arc, 3, centre)
    fix(sketch, "DistanceY", arc, 3, base)
    sketch.addConstraint(Sketcher.Constraint("Vertical", up))


def tab_points(along):
    """Dovetail sticking out of the board. `along` maps the outward distance and
    the sideways offset to (x, y) expression strings."""
    a, b, L = "TabBase / 2", "TabEnd / 2", "TabLen"
    local = [("-0.5", a), ("0", a), (L, b), (L, neg(b)), ("0", neg(a)), ("-0.5", neg(a))]
    return [along(u, v) for u, v in local]


def socket_points(along):
    """Socket into the board, Fit wider than the tab on each side, running through
    the half pad into the next hole. `along` maps the inward distance. It also
    reaches a tab length outside the board, to clear the rib that the pad's rib
    pattern leaves hanging over the edge there."""
    # At depth L the socket is wider than the hole's flat there, so the flared
    # walls carry on 2 mm into the hole; stopping at L would notch the bars.
    a, b, L = "TabBase / 2 + Fit", "TabEnd / 2 + Fit", "TabLen"
    deep = add(L, "2")
    b_deep = f"{a} + ({b} - ({a})) * ({deep}) / ({L})"
    out = neg(add(L, "1"))
    local = [
        (out, a), ("0", a), (deep, b_deep),
        (deep, neg(b_deep)), ("0", neg(a)), (out, neg(a)),
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
    origin = {f.Role: f for f in body.Origin.OriginFeatures}
    xy = origin["XY_Plane"]

    def sketch(name, points_list, top=False, plane=xy, offset=None):
        s = body.newObject("Sketcher::SketchObject", name)
        s.AttachmentSupport = [(plane, "")]
        s.MapMode = "FlatFace"
        if top:
            # On the top face: cuts go down from it.
            offset = "Thickness"
        if offset:
            # Moves the sketch along its own normal.
            s.setExpression(".AttachmentOffset.Base.z", expr(offset))
        for pts in points_list:
            poly(s, pts)
        return s

    def pad(name, s, length="Params.Thickness", both_ways=False):
        f = body.newObject("PartDesign::Pad", name)
        f.Profile = s
        f.setExpression("Length", length)
        f.Midplane = both_ways
        return f

    def pocket(name, s, both_ways=False):
        # Through everything below the sketch, and above it too if both_ways.
        f = body.newObject("PartDesign::Pocket", name)
        f.Profile = s
        f.Type = "ThroughAll"
        f.Midplane = both_ways
        return f

    def mirror(name, feature, s, axis):
        m = body.newObject("PartDesign::Mirrored", name)
        m.Originals = [feature]
        m.MirrorPlane = (s, [axis])
        body.Tip = m  # patterns don't take the tip themselves; the next feature must follow them
        return m

    def grid(name, feature, nx, ny):
        # nx copies along +x and ny along +y, one pitch apart.
        g = body.newObject("PartDesign::LinearPattern", name)
        g.Originals = [feature]
        g.Mode = g.Mode2 = "Extent"
        g.Direction = (origin["X_Axis"], [""])
        g.Direction2 = (origin["Y_Axis"], [""])
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

    # Separator ribs along the seams, one per pad. On the tabs they run on to the
    # tip, completing the rib over the neighbour's half pad. They come before the
    # sockets, which cut away the ribs of the half pads they replace.
    # Seams between tiles side by side run along y, seams between stacked tiles along x.
    # Each rib is its cross-section, drawn on a vertical plane through the rib's
    # centre and padded PadSize along the seam, half each way.
    # XZ_Plane: sketch x = global x, its normal points to -y.
    s = sketch("YRibSketch", [], plane=origin["XZ_Plane"], offset="1.5 * Pitch")
    rib_section(s, "-Pitch")
    grid("YRibs", pad("YRib", s, "Params.PadSize", both_ways=True), 3, 4)
    # YZ_Plane: sketch x = global y, its normal points to +x.
    s = sketch("XRibSketch", [], plane=origin["YZ_Plane"], offset="-1.5 * Pitch")
    rib_section(s, "-Pitch")
    grid("XRibs", pad("XRib", s, "Params.PadSize", both_ways=True), 4, 3)

    # Rounded rib ends: a side-view cut through everything above the board,
    # both ways, so one row of cuts trims every rib whose ends line up with it.
    def rib_ends(name, plane, c, nx, ny):
        s = sketch(name + "Sketch", [], plane=plane)
        rib_end_cut(s, add(c, "PadSize / 2"), +1)
        rib_end_cut(s, sub(c, "PadSize / 2"), -1)
        grid(name + "s", pocket(name, s, both_ways=True), nx, ny)

    rib_ends("YRibEnd", origin["YZ_Plane"], "-1.5 * Pitch", 1, 4)
    rib_ends("XRibEnd", origin["XZ_Plane"], "-1.5 * Pitch", 4, 1)

    pts = [
        socket_points(lambda u, v, cy=cy: (sub("Half", u), add(cy, v)))
        for cy in ("Pitch", "-Pitch")
    ]
    s = sketch("SideSocketSketch", pts, top=True)
    mirror("SideSockets", pocket("SideSocket", s, both_ways=True), s, "V_Axis")

    s = sketch("EndSocketSketch", [socket_points(lambda u, v: (v, sub("Half", u)))], top=True)
    mirror("EndSockets", pocket("EndSocket", s, both_ways=True), s, "H_Axis")

    s = sketch("CornerHoleSketch", [octagon_points("-Pitch", "-Pitch")], top=True)
    grid("CornerHoles", pocket("CornerHole", s), 3, 3)
    s = sketch("CentreHoleSketch", [octagon_points("-1.5 * Pitch", "-1.5 * Pitch")], top=True)
    last = grid("CentreHoles", pocket("CentreHole", s), 4, 4)
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
