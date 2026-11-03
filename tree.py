# Builds a 9x9 tile board as interlocking pieces, each a parametric PartDesign
# body in the active document, plus a "Params" spreadsheet that drives every
# dimension through expressions. Edit a cell and recompute to change it.
# Run inside FreeCAD (bridge/fc.py run tree.py). Deletes and rebuilds everything
# it made before. The layout (how the board is split) is set in COLS/ROWS below.
#
# Board coordinates, in tiles, origin at the board's lower left corner:
#   tile centres at (i+1/2, j+1/2), tile corners at (i, j)
#   an octagonal hole sits on every tile centre and every tile corner
#   a square pad sits on every tile edge midpoint, with a raised rib along the
#   seam that keeps the tiles from sliding
# Pieces meet along lines through tile centres, so each seam pad is split
# between two pieces: one has a dovetail tab there, the other the socket.
# Which one is decided by the pad's board position, so that tabs on a right
# (top) edge always face sockets on the next piece's left (bottom) edge:
#   right edge tab where j is even, left edge tab where j is odd
#   top edge tab where i is even, bottom edge tab where i is odd
# The board's outer edges are terminating: straight, with a solid border.

import math
import re
import time

import FreeCAD as App
import Part
import Sketcher

V = App.Vector

# Piece boundaries in half tiles, from one board edge to the other (18 = 9 tiles).
# Connector boundaries must be odd (through tile centres).
COLS = [0, 5, 9, 13, 18]
ROWS = [0, 5, 9, 13, 18]
SIZE = 18

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
    ("Border", "5", "solid strip along the board's outer edges"),
    ("TabBase", "10", "dovetail width at the piece edge"),
    ("TabEnd", "13", "dovetail width at its tip"),
    ("TabLen", "=PadSize / 2", "dovetail length: fills the neighbour's half pad"),
    ("Fit", "0.2", "clearance around the dovetail in the socket"),
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


def at(h):
    """Board coordinate h, in half tiles, as an expression in mm."""
    return f"{h / 2:g} * Pitch"


# ---------------------------------------------------------------- sketch helpers


def fix(sketch, kind, geo, pos, e):
    """Pin one coordinate of a sketch point to an expression."""
    i = sketch.addConstraint(Sketcher.Constraint(kind, -1, 1, geo, pos, num(e)))
    sketch.setExpression(f".Constraints[{i}]", expr(e))


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


def rib_top_cut(sketch, c):
    """Side view of what to cut off the top of a rib centred on sketch x = c,
    leaving a fully rounded top: the strip over the rib, above a semicircle."""
    w = "RibW / 2"
    shoulder = f"Thickness + RibH - ({w})"
    top = "Thickness + RibH + 1"
    p = lambda x, y: V(num(x), num(y), 0)
    g = len(sketch.Geometry)
    sketch.addGeometry(Part.LineSegment(p(sub(c, w), top), p(add(c, w), top)))
    sketch.addGeometry(Part.LineSegment(p(add(c, w), top), p(add(c, w), shoulder)))
    circle = Part.Circle(p(c, shoulder), V(0, 0, 1), num(w))
    sketch.addGeometry(Part.ArcOfCircle(circle, 0, math.pi))
    sketch.addGeometry(Part.LineSegment(p(sub(c, w), shoulder), p(sub(c, w), top)))
    over, right, arc, left = g, g + 1, g + 2, g + 3
    sketch.addConstraint(Sketcher.Constraint("Coincident", over, 2, right, 1))
    sketch.addConstraint(Sketcher.Constraint("Coincident", left, 2, over, 1))
    # The sides join the arc tangentially, which also makes them vertical.
    sketch.addConstraint(Sketcher.Constraint("Tangent", right, 2, arc, 1))
    sketch.addConstraint(Sketcher.Constraint("Tangent", arc, 2, left, 1))
    fix(sketch, "DistanceX", over, 1, sub(c, w))
    fix(sketch, "DistanceY", over, 1, top)
    fix(sketch, "DistanceX", over, 2, add(c, w))
    fix(sketch, "DistanceY", over, 2, top)
    fix(sketch, "DistanceX", arc, 3, c)
    fix(sketch, "DistanceY", arc, 3, shoulder)
    i = sketch.addConstraint(Sketcher.Constraint("Radius", arc, num(w)))
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
    """Dovetail sticking out of the piece. `along` maps the outward distance and
    the sideways offset to (x, y) expression strings."""
    a, b, L = "TabBase / 2", "TabEnd / 2", "TabLen"
    local = [("-0.5", a), ("0", a), (L, b), (L, neg(b)), ("0", neg(a)), ("-0.5", neg(a))]
    return [along(u, v) for u, v in local]


def socket_points(along):
    """Socket into the piece, Fit wider than the tab on each side, running through
    the half pad into the next hole. `along` maps the inward distance."""
    # At depth L the socket is wider than the hole's flat there, so the flared
    # walls carry on 2 mm into the hole; stopping at L would notch the bars.
    a, b, L = "TabBase / 2 + Fit", "TabEnd / 2 + Fit", "TabLen"
    deep = add(L, "2")
    b_deep = f"{a} + ({b} - ({a})) * ({deep}) / ({L})"
    local = [("-1", a), ("0", a), (deep, b_deep),
             (deep, neg(b_deep)), ("0", neg(a)), ("-1", neg(a))]  # fmt: skip
    return [along(u, v) for u, v in local]


def octagon_points(cx, cy):
    h, c = "OctH", "OctC"
    local = [(h, c), (c, h), (neg(c), h), (neg(h), c),
             (neg(h), neg(c)), (neg(c), neg(h)), (c, neg(h)), (h, neg(c))]  # fmt: skip
    return [(add(cx, x), add(cy, y)) for x, y in local]


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ---------------------------------------------------------------- piece layout


class Piece:
    def __init__(self, x0, x1, y0, y1):
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y0, y1
        self.term = {
            "L": x0 == 0, "R": x1 == SIZE, "B": y0 == 0, "T": y1 == SIZE,
        }  # fmt: skip

    def kind(self):
        n = sum(self.term.values())
        return {0: "Centre", 1: "Edge", 2: "Corner"}.get(n, "Piece")

    def turned(self):
        """This piece turned a quarter turn anticlockwise about the board centre."""
        return Piece(SIZE - self.y1, SIZE - self.y0, self.x0, self.x1)

    def signature(self):
        """What makes a design: size, terminating sides, and where its tabs and
        sockets are, relative to the piece's lower left corner."""
        pads = set()
        for side, c, tab in connectors(self):
            x, y = {"R": (self.x1, c), "L": (self.x0, c),
                    "T": (c, self.y1), "B": (c, self.y0)}[side]  # fmt: skip
            pads.add((x - self.x0, y - self.y0, tab))
        terms = frozenset(s for s, t in self.term.items() if t)
        return (self.x1 - self.x0, self.y1 - self.y0, terms, frozenset(pads))


def ribs(p):
    """Every rib on a piece: (axis, centre across, start along, end along, rounded
    start, rounded end), all in half tiles except the ends, which are expressions.
    Ribs along x sit on pads (odd, even); ribs along y on pads (even, odd)."""
    L = "PadSize / 2"
    out = []
    for axis, (lo, hi), (clo, chi) in (
        ("x", (p.x0, p.x1), (p.y0, p.y1)),
        ("y", (p.y0, p.y1), (p.x0, p.x1)),
    ):
        for a in range(lo, hi + 1):
            if a % 2 == 0:
                continue
            for c in range(clo, chi + 1):
                if c % 2:
                    continue
                start, end = sub(at(a), L), add(at(a), L)
                round_start = round_end = True
                on_lo, on_hi = a == lo, a == hi
                if on_lo or on_hi:
                    # A pad on a connector seam.
                    if c in (clo, chi):
                        # Where the seam meets the board's outer edge: no
                        # connector, the rib just stops at the seam.
                        if on_hi:
                            end, round_end = at(a), False
                        else:
                            start, round_start = at(a), False
                    elif not tab_here(axis, on_hi, c):
                        continue  # the neighbour's tab brings this rib
                out.append((axis, c, start, end, round_start, round_end))
    return out


def tab_here(axis, high_side, c):
    """Whether the seam pad at board position c (half tiles, even) gets a tab on
    the given side of the seam. axis 'x' means a left/right seam, 'y' a
    bottom/top seam; high_side means the piece right of / above the seam.

    A socket runs through its half pad, so a piece corner flanked by two sockets
    would leave the bar between them loose. Around each point where four pieces
    meet the tabs therefore go round like a pinwheel, giving every piece one tab
    at that corner: on a left/right seam the pad just below the point belongs to
    the left piece and the one just above to the right piece; on a bottom/top
    seam the pad just left of it to the upper piece, just right to the lower.
    Pads further away alternate from there."""
    across = ROWS if axis == "x" else COLS
    junctions = [b for b in across if b % 2]
    if not junctions:
        owner_high = (c // 2) % 2 == 0
    else:
        y = min(junctions, key=lambda b: abs(c - b))
        d = c - y
        owner_high = (d > 0) if axis == "x" else (d < 0)
        if ((abs(d) - 1) // 2) % 2:
            owner_high = not owner_high
    return owner_high == high_side


def connectors(p):
    """(side, position along the side in half tiles, is_tab) for each seam pad."""
    out = []
    for side, coord, lo, hi in (
        ("R", p.x1, p.y0, p.y1), ("L", p.x0, p.y0, p.y1),
        ("T", p.y1, p.x0, p.x1), ("B", p.y0, p.x0, p.x1),
    ):  # fmt: skip
        if p.term[side]:
            continue
        for c in range(lo + 1, hi):
            if c % 2 == 0:
                axis = "x" if side in "RL" else "y"
                out.append((side, c, tab_here(axis, side in "RT", c)))
    return out


def lattice(lo, hi, odd):
    """Hole centres (half tiles) of one parity whose holes reach into [lo, hi]."""
    return [h for h in range(lo - 1, hi + 2) if h % 2 == odd and lo - 1 < h < hi + 1]


# ---------------------------------------------------------------- building


def remove_old(doc, names):
    for name in ["Layout"] + names + ["Board", "Params"]:
        obj = doc.getObject(name)
        if obj is None:
            continue
        if obj.TypeId == "PartDesign::Body":
            origin = [obj.Origin.Name] + [f.Name for f in obj.Origin.OriginFeatures]
            obj.removeObjectsFromDocument()
            doc.removeObject(obj.Name)
            for n in origin:
                if doc.getObject(n):
                    doc.removeObject(n)
        else:
            if obj.TypeId == "App::DocumentObjectGroup":
                obj.removeObjectsFromDocument()
            doc.removeObject(obj.Name)


def build_sheet(doc):
    sheet = doc.addObject("Spreadsheet::Sheet", "Params")
    for row, (alias, value, note) in enumerate(PARAMS, start=1):
        sheet.set(f"A{row}", alias)
        sheet.set(f"B{row}", value)
        sheet.setAlias(f"B{row}", alias)
        sheet.set(f"C{row}", note)
    doc.recompute()
    return sheet


def build_piece(doc, p, tag):
    body = doc.addObject("PartDesign::Body", tag)
    origin = {f.Role: f for f in body.Origin.OriginFeatures}

    def sketch(name, points_list=(), plane="XY_Plane", offset=None):
        s = body.newObject("Sketcher::SketchObject", f"{tag}_{name}Sketch")
        s.AttachmentSupport = [(origin[plane], "")]
        s.MapMode = "FlatFace"
        if offset:
            # Moves the sketch along its own normal.
            s.setExpression(".AttachmentOffset.Base.z", expr(offset))
        for pts in points_list:
            poly(s, pts)
        return s

    def pad(name, s, length="Params.Thickness"):
        f = body.newObject("PartDesign::Pad", f"{tag}_{name}")
        f.Profile = s
        f.setExpression("Length", length)
        return f

    def pocket(name, s, both_ways=False):
        # Through everything below the sketch, and above it too if both_ways.
        f = body.newObject("PartDesign::Pocket", f"{tag}_{name}")
        f.Profile = s
        f.Type = "ThroughAll"
        f.Midplane = both_ways
        return f

    def grid(name, feature, nx, ny):
        # nx copies along +x and ny along +y, one pitch apart.
        g = body.newObject("PartDesign::LinearPattern", f"{tag}_{name}")
        g.Originals = [feature]
        g.Mode = g.Mode2 = "Extent"
        g.Direction = (origin["X_Axis"], [""])
        g.Direction2 = (origin["Y_Axis"], [""])
        g.setExpression("Length", f"{max(nx - 1, 0)} * Params.Pitch")
        g.setExpression("Length2", f"{max(ny - 1, 0)} * Params.Pitch")
        g.Occurrences, g.Occurrences2 = nx, ny
        body.Tip = g  # patterns don't take the tip themselves
        return g

    X0, X1, Y0, Y1 = at(p.x0), at(p.x1), at(p.y0), at(p.y1)
    pad("Outline", sketch("Outline", [rect(X0, Y0, X1, Y1)]))

    conns = connectors(p)

    def placed(side, c, points_fn, outward):
        # Map (u, v) = (distance across the edge, offset along it) to (x, y).
        # outward=True: u points out of the piece (tabs), else into it (sockets).
        X = {"R": X1, "L": X0}.get(side)
        Y = {"T": Y1, "B": Y0}.get(side)
        sign_out = side in "RT"
        step = add if sign_out == outward else sub
        if side in "RL":
            return points_fn(lambda u, v: (step(X, u), add(at(c), v)))
        return points_fn(lambda u, v: (add(at(c), v), step(Y, u)))

    tabs = [placed(s, c, tab_points, True) for s, c, t in conns if t]
    if tabs:
        pad("Tabs", sketch("Tabs", tabs))

    # Holes: one octagon on the tile-corner lattice and one on the tile-centre
    # lattice, each repeated over the piece.
    for name, odd in (("CornerHole", 0), ("CentreHole", 1)):
        xs, ys = lattice(p.x0, p.x1, odd), lattice(p.y0, p.y1, odd)
        s = sketch(name, [octagon_points(at(xs[0]), at(ys[0]))], offset="Thickness")
        grid(name + "s", pocket(name, s), len(xs), len(ys))

    # Solid border along the board's outer edges. Left and right strips take the
    # corners, so strips never overlap.
    B = "Border"
    for side in "LRBT":
        if not p.term[side]:
            continue
        if side in "LR":
            x = X0 if side == "L" else X1
            xa, xb = (sub(x, B), x) if side == "L" else (x, add(x, B))
            ya = sub(Y0, B) if p.term["B"] else Y0
            yb = add(Y1, B) if p.term["T"] else Y1
        else:
            y = Y0 if side == "B" else Y1
            ya, yb = (sub(y, B), y) if side == "B" else (y, add(y, B))
            xa, xb = X0, X1
        pad(f"Border{side}", sketch(f"Border{side}", [rect(xa, ya, xb, yb)]))

    # Ribs: footprints padded up from the top face, then shaped by side-view
    # cuts through everything above the board: one per rib line rounds the tops,
    # one per rib end curves the end down. A cut along a line trims every rib on
    # it, and no rib of the other direction crosses those lines.
    rs = ribs(p)
    w = "RibW / 2"
    feet = []
    for axis, c, start, end, _, _ in rs:
        if axis == "x":
            feet.append(rect(start, sub(at(c), w), end, add(at(c), w)))
        else:
            feet.append(rect(sub(at(c), w), start, add(at(c), w), end))
    pad("Ribs", sketch("Ribs", feet, offset="Thickness"), "Params.RibH")

    for axis in "xy":
        lines = sorted({c for a, c, *_ in rs if a == axis})
        ends = {}
        for a, c, start, end, round_start, round_end in rs:
            if a != axis:
                continue
            if round_start:
                ends[(round(num(start), 6), -1)] = start
            if round_end:
                ends[(round(num(end), 6), +1)] = end
        # x ribs: their cross-section lies in YZ (sketch x = global y), their
        # side view in XZ (sketch x = global x). The other way round for y ribs.
        across, along = ("YZ_Plane", "XZ_Plane") if axis == "x" else ("XZ_Plane", "YZ_Plane")
        s = sketch(f"{axis.upper()}RibTops", plane=across)
        for c in lines:
            rib_top_cut(s, at(c))
        pocket(f"{axis.upper()}RibTops", s, both_ways=True)
        s = sketch(f"{axis.upper()}RibEnds", plane=along)
        for (_, sign), e in sorted(ends.items()):
            rib_end_cut(s, e, sign)
        pocket(f"{axis.upper()}RibEnds", s, both_ways=True)

    sockets = [placed(s, c, socket_points, False) for s, c, t in conns if not t]
    if sockets:
        pocket("Sockets", sketch("Sockets", sockets, offset="Thickness"), both_ways=True)
    body.Tip.Refine = True
    return body


def build(doc):
    pieces = [
        Piece(COLS[i], COLS[i + 1], ROWS[j], ROWS[j + 1])
        for j in range(len(ROWS) - 1)
        for i in range(len(COLS) - 1)
    ]
    # A piece shares a design with an earlier one if that one, turned some
    # quarter turns about the board centre and moved, has the same tabs,
    # sockets and edges in the same places.
    designs = []  # (first piece, [(piece, quarter turns, first piece so turned)])
    for p in pieces:
        for first, members in designs:
            t = first
            for k in range(4):
                if t.signature() == p.signature():
                    members.append((p, k, t))
                    break
                t = t.turned()
            else:
                continue
            break
        else:
            designs.append((p, [(p, 0, p)]))

    names, seen = [], {}
    for first, _ in designs:
        kind = first.kind()
        seen[kind] = seen.get(kind, 0) + 1
        names.append(kind if seen[kind] == 1 else f"{kind}{seen[kind]}")
    # Names used by earlier versions of this script, so reruns clean them up.
    legacy = ["CornerBL", "CornerBR", "CornerTL", "CornerTR",
              "EdgeB", "EdgeT", "EdgeL", "EdgeR"]  # fmt: skip
    remove_old(doc, names + legacy)

    sheet = build_sheet(doc)
    layout = doc.addObject("App::DocumentObjectGroup", "Layout")
    bodies = []
    centre = SIZE / 4  # board centre, in tiles
    for name, (first, members) in zip(names, designs):
        body = build_piece(doc, first, name)
        bodies.append(body)
        for n, (p, k, t) in enumerate(members[1:], start=2):
            link = doc.addObject("App::Link", f"{name}_{n}")
            link.LinkedObject = body
            # Turn about the board centre, then move from where the turn lands
            # the piece (t) to where it belongs (p). As a placement that is the
            # turn about the origin plus this offset, in tiles.
            cos, sin = [(1, 0), (0, 1), (-1, 0), (0, -1)][k]
            dx = centre - (cos * centre - sin * centre) + (p.x0 - t.x0) / 2
            dy = centre - (sin * centre + cos * centre) + (p.y0 - t.y0) / 2
            link.Placement = App.Placement(V(), App.Rotation(V(0, 0, 1), 90 * k))
            link.setExpression(".Placement.Base.x", f"{dx:g} * Params.Pitch")
            link.setExpression(".Placement.Base.y", f"{dy:g} * Params.Pitch")
            layout.addObject(link)
    return sheet, bodies, layout


doc = App.ActiveDocument
sheet, bodies, layout = build(doc)
start = time.time()
doc.recompute()
elapsed = time.time() - start

report = {}
for body in bodies:
    problems = [
        f"{o.Name}: {o.getStatusString()}"
        for o in body.Group
        if "Invalid" in o.State or "Error" in o.State
    ]
    for o in body.Group:
        if o.isDerivedFrom("Sketcher::SketchObject") and not o.FullyConstrained:
            problems.append(f"{o.Name}: not fully constrained")
    shape = body.Shape
    bb = shape.BoundBox
    report[body.Name] = {
        "problems": problems,
        "valid": shape.isValid() if not shape.isNull() else False,
        "solids": len(shape.Solids),
        "size": [round(bb.XLength, 1), round(bb.YLength, 1)],
    }
result = {"recompute_s": round(elapsed, 1), "pieces": report}

try:
    import FreeCADGui as Gui

    for body in bodies:
        for o in body.Group:
            o.Visibility = o == body.Tip
        body.Visibility = True
    Gui.ActiveDocument.ActiveView.viewTop()
    Gui.SendMsgToActiveView("ViewFit")
except Exception:
    pass
