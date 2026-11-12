#!/data/data/com.termux/files/usr/bin/env python
# -*- coding: utf-8 -*-
"""
svgview.py — a self-contained, one-shot terminal SVG viewer.

Renders SVG files straight into your terminal and automatically saves a
converted PNG next to each source file.

Usage:
    ./svgview.py file.svg               # render; auto-saves file.svg.png
    ./svgview.py *.svg                  # render many
    ./svgview.py --no-save file.svg     # render only
    ./svgview.py --png out.png file.svg # render + save exactly out.png
    ./svgview.py --renderer sixel f.svg # force a terminal backend

Output backend is auto-detected:
    * kitty graphics protocol  (kitty, wezterm, ghostty)
    * DEC sixel                (foot, mlterm, xterm -ti vt340, wezterm)
    * Unicode half-blocks with 24-bit color (everywhere else)

Rendering engine:
    * cairosvg when importable (best fidelity), otherwise
    * built-in pure-Python engine: rect/circle/ellipse/line/polyline/
      polygon/path (bezier + arc), flat fills & strokes, dash patterns,
      caps & joins, opacity, nested transforms, viewBox +
      preserveAspectRatio, <use>/<symbol>.

Transparency is composited over the terminal's own background color
(queried via OSC 11; --bg overrides).  Auto-saved PNGs keep the alpha
channel unless --bg is given.

Environment: Linux, Python 3.12+.  No hard dependencies.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import glob
import math
import os
import re
import select
import signal
import struct
import sys
import termios
import time
import tty
import zlib
import xml.etree.ElementTree as ET
from array import array
from collections import Counter

__version__ = "1.0.0"

# Die quietly on SIGPIPE (e.g. `svgview.py big.svg | head`).
try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError, OSError):
    pass

XLINK_HREF = "{http://www.w3.org/1999/xlink}href"

# ---------------------------------------------------------------------------
# Numbers, colors, lengths
# ---------------------------------------------------------------------------

NUM_RE = re.compile(r"[+-]?(?:\d*\.\d+|\d+\.?)(?:[eE][+-]?\d+)?")


def _f(value, default=0.0):
    """float() with fallback."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# CSS/SVG named colors -> hex
_NAMED_RAW = """
aliceblue f0f8ff antiquewhite faebd7 aqua 00ffff aquamarine 7fffd4 azure f0ffff
beige f5f5dc bisque ffe4c4 black 000000 blanchedalmond ffebcd blue 0000ff
blueviolet 8a2be2 brown a52a2a burlywood deb887 cadetblue 5f9ea0 chartreuse 7fff00
chocolate d2691e coral ff7f50 cornflowerblue 6495ed cornsilk fff8dc crimson dc143c
cyan 00ffff darkblue 00008b darkcyan 008b8b darkgoldenrod b8860b darkgray a9a9a9
darkgreen 006400 darkgrey a9a9a9 darkkhaki bdb76b darkmagenta 8b008b
darkolivegreen 556b2f darkorange ff8c00 darkorchid 9932cc darkred 8b0000
darksalmon e9967a darkseagreen 8fbc8f darkslateblue 483d8b darkslategray 2f4f4f
darkslategrey 2f4f4f darkturquoise 00ced1 darkviolet 9400d3 deeppink ff1493
deepskyblue 00bfff dimgray 696969 dimgrey 696969 dodgerblue 1e90ff firebrick b22222
floralwhite fffaf0 forestgreen 228b22 fuchsia ff00ff gainsboro dcdcdc
ghostwhite f8f8ff gold ffd700 goldenrod daa520 gray 808080 green 008000
greenyellow adff2f grey 808080 honeydew f0fff0 hotpink ff69b4 indianred cd5c5c
indigo 4b0082 ivory fffff0 khaki f0e68c lavender e6e6fa lavenderblush fff0f5
lawngreen 7cfc00 lemonchiffon fffacd lightblue add8e6 lightcoral f08080
lightcyan e0ffff lightgoldenrodyellow fafad2 lightgray d3d3d3 lightgreen 90ee90
lightgrey d3d3d3 lightpink ffb6c1 lightsalmon ffa07a lightseagreen 20b2aa
lightskyblue 87cefa lightslategray 778899 lightslategrey 778899
lightsteelblue b0c4de lightyellow ffffe0 lime 00ff00 limegreen 32cd32 linen faf0e6
magenta ff00ff maroon 800000 mediumaquamarine 66cdaa mediumblue 0000cd
mediumorchid ba55d3 mediumpurple 9370db mediumseagreen 3cb371
mediumslateblue 7b68ee mediumspringgreen 00fa9a mediumturquoise 48d1cc
mediumvioletred c71585 midnightblue 191970 mintcream f5fffa mistyrose ffe4e1
moccasin ffe4b5 navajowhite ffdead navy 000080 oldlace fdf5e6 olive 808000
olivedrab 6b8e23 orange ffa500 orangered ff4500 orchid da70d6 palegoldenrod eee8aa
palegreen 98fb98 paleturquoise afeeee palevioletred db7093 papayawhip ffefd5
peachpuff ffdab9 peru cd853f pink ffc0cb plum dda0dd powderblue b0e0e6 purple 800080
rebeccapurple 663399 red ff0000 rosybrown bc8f8f royalblue 4169e1
saddlebrown 8b4513 salmon fa8072 sandybrown f4a460 seagreen 2e8b57 seashell fff5ee
sienna a0522d silver c0c0c0 skyblue 87ceeb slateblue 6a5acd slategray 708090
slategrey 708090 snow fffafa springgreen 00ff7f steelblue 4682b4 tan d2b48c
teal 008080 thistle d8bfd8 tomato ff6347 turquoise 40e0d0 violet ee82ee
wheat f5deb3 white ffffff whitesmoke f5f5f5 yellow ffff00 yellowgreen 9acd32
"""
_tok = _NAMED_RAW.split()
NAMED_COLORS = {_tok[i]: _tok[i + 1] for i in range(0, len(_tok), 2)}


def _hsl_to_rgb(h, s, l):
    """h in degrees, s/l in 0..1 -> (r, g, b) 0..255."""
    if s <= 0:
        v = int(round(l * 255))
        return (v, v, v)
    h = (h % 360.0) / 360.0
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q

    def cu(t):
        t = t % 1.0
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 0.5:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    return tuple(int(round(c * 255)) for c in (cu(h + 1 / 3), cu(h), cu(h - 1 / 3)))


def parse_color(value, current=(0, 0, 0)):
    """Parse a CSS/SVG color -> (r, g, b, a) with a in 0..1.

    Returns None for 'none'.  Raises ValueError on garbage.
    """
    if value is None:
        raise ValueError("no color")
    v = value.strip()
    if not v:
        raise ValueError("empty color")
    lv = v.lower()
    if lv == "none":
        return None
    if lv == "transparent":
        return (0, 0, 0, 0.0)
    if lv in ("currentcolor", "inherit"):
        return (current[0], current[1], current[2], 1.0)
    if lv.startswith("#"):
        h = lv[1:]
        if re.fullmatch(r"[0-9a-f]{3}", h):
            return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16), 1.0)
        if re.fullmatch(r"[0-9a-f]{4}", h):
            return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16), int(h[3] * 2, 16) / 255.0)
        if re.fullmatch(r"[0-9a-f]{6}", h):
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
        if re.fullmatch(r"[0-9a-f]{8}", h):
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) / 255.0)
        raise ValueError(f"bad hex color {v!r}")
    m = re.match(r"^rgba?\(\s*([^)]*)\)$", lv)
    if m:
        parts = [p for p in re.split(r"[,\s]+", m.group(1).strip()) if p]
        if len(parts) not in (3, 4):
            raise ValueError(f"bad rgb() {v!r}")
        out = []
        for i, p in enumerate(parts):
            if p.endswith("%"):
                fv = _f(p[:-1]) / 100.0
            else:
                fv = _f(p) / 255.0 if i < 3 else _f(p)
            out.append(max(0.0, min(1.0, fv)))
        return (
            int(round(out[0] * 255)),
            int(round(out[1] * 255)),
            int(round(out[2] * 255)),
            out[3] if len(out) > 3 else 1.0,
        )
    m = re.match(r"^hsla?\(\s*([^)]*)\)$", lv)
    if m:
        parts = [p for p in re.split(r"[,\s]+", m.group(1).strip()) if p]
        if len(parts) not in (3, 4):
            raise ValueError(f"bad hsl() {v!r}")
        h = _f(parts[0].rstrip("deg"))
        s = max(0.0, min(1.0, _f(parts[1].rstrip("%")) / 100.0))
        l = max(0.0, min(1.0, _f(parts[2].rstrip("%")) / 100.0))
        a = 1.0
        if len(parts) > 3:
            p = parts[3]
            a = _f(p.rstrip("%"))
            a = max(0.0, min(1.0, a / 100.0 if p.endswith("%") else a))
        r, g, b = _hsl_to_rgb(h, s, l)
        return (r, g, b, a)
    hexv = NAMED_COLORS.get(lv)
    if hexv:
        return (int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16), 1.0)
    raise ValueError(f"unknown color {v!r}")


_UNITS = {
    "": 1.0,
    "px": 1.0,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
    "q": 96.0 / 25.4 / 4.0,
    "em": 16.0,
    "ex": 8.0,
    "rem": 16.0,
}


def parse_length(value, pct_base=None, default=None):
    """Parse an SVG length -> pixels (float), or `default` if absent/invalid."""
    if value is None:
        return default
    v = value.strip()
    if not v or v.lower() == "auto":
        return default
    m = re.fullmatch(r"([+-]?(?:\d*\.\d+|\d+\.?)(?:[eE][+-]?\d+)?)([a-z%]*)", v, re.IGNORECASE)
    if not m:
        return default
    n = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "%":
        if pct_base is None:
            return default
        return n / 100.0 * pct_base
    return n * _UNITS.get(unit, 1.0)


# ---------------------------------------------------------------------------
# Affine transforms
# ---------------------------------------------------------------------------


class M:
    """SVG-style affine matrix [a b c d e f] (column-vector convention)."""

    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0):
        self.a, self.b, self.c, self.d, self.e, self.f = a, b, c, d, e, f

    def __matmul__(self, o):
        """self @ o == apply o first, then self (SVG nesting order)."""
        return M(
            self.a * o.a + self.c * o.b,
            self.b * o.a + self.d * o.b,
            self.a * o.c + self.c * o.d,
            self.b * o.c + self.d * o.d,
            self.a * o.e + self.c * o.f + self.e,
            self.b * o.e + self.d * o.f + self.f,
        )

    def apply(self, x, y):
        return (x * self.a + y * self.c + self.e, x * self.b + y * self.d + self.f)

    def scale(self):
        det = abs(self.a * self.d - self.b * self.c)
        s = math.sqrt(det)
        return s if s > 1e-12 else 1e-12


IDENT = M()

_TRANSFORM_RE = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")


def parse_transform(text):
    """Parse an SVG transform attribute -> M (identity if absent/broken)."""
    if not text:
        return IDENT
    total = IDENT
    for name, argstr in _TRANSFORM_RE.findall(text):
        args = [float(v) for v in NUM_RE.findall(argstr)]
        name = name.lower()
        if name == "matrix" and len(args) == 6:
            m = M(*args)
        elif name == "translate" and args:
            tx = args[0]
            ty = args[1] if len(args) > 1 else 0.0
            m = M(1, 0, 0, 1, tx, ty)
        elif name == "scale" and args:
            sx = args[0]
            sy = args[1] if len(args) > 1 else sx
            m = M(sx, 0, 0, sy, 0, 0)
        elif name == "rotate" and args:
            r = math.radians(args[0])
            co, si = math.cos(r), math.sin(r)
            m = M(co, si, -si, co, 0, 0)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                m = M(1, 0, 0, 1, cx, cy) @ m @ M(1, 0, 0, 1, -cx, -cy)
        elif name == "skewx" and args:
            m = M(1, 0, math.tan(math.radians(args[0])), 1, 0, 0)
        elif name == "skewy" and args:
            m = M(1, math.tan(math.radians(args[0])), 0, 1, 0, 0)
        else:
            continue
        total = total @ m
    return total


# ---------------------------------------------------------------------------
# Path data: parsing and flattening (bezier + arc sampling)
# ---------------------------------------------------------------------------


class PathError(ValueError):
    pass


class _PP:
    """Token-scanner for SVG path data (handles packed numbers & flags)."""

    __slots__ = ("d", "i", "n")

    def __init__(self, d):
        self.d = d
        self.i = 0
        self.n = len(d)

    def _ws(self):
        d, n = self.d, self.n
        i = self.i
        while i < n and d[i] in " \t\r\n\f\v,":
            i += 1
        self.i = i

    def cmd(self):
        self._ws()
        if self.i >= self.n:
            return None
        c = self.d[self.i]
        if c.isalpha():
            self.i += 1
            return c
        raise PathError(f"unexpected character {c!r} at offset {self.i}")

    def more(self):
        """True if another parameter group follows for the current command."""
        self._ws()
        if self.i >= self.n:
            return False
        c = self.d[self.i]
        if c.isalpha():
            return False
        if c in "+-.0123456789":
            return True
        raise PathError(f"unexpected character {c!r} at offset {self.i}")

    def num(self):
        self._ws()
        m = NUM_RE.match(self.d, self.i)
        if not m:
            raise PathError(f"expected number at offset {self.i}")
        self.i = m.end()
        return float(m.group())

    def flag(self):
        self._ws()
        if self.i < self.n and self.d[self.i] in "01":
            self.i += 1
            return int(self.d[self.i - 1])
        raise PathError(f"expected arc flag at offset {self.i}")


def _cubic(p0, p1, p2, p3, tol, out, depth=0):
    """Adaptively flatten a cubic bezier, appending points (excluding p0)."""
    x0, y0 = p0
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    if depth >= 18:
        out.append(p3)
        return
    # flatness: distance of control points from the 1/3, 2/3 chord points
    t2 = tol * tol
    ax = x0 + (x3 - x0) / 3.0
    ay = y0 + (y3 - y0) / 3.0
    bx = x0 + 2.0 * (x3 - x0) / 3.0
    by = y0 + 2.0 * (y3 - y0) / 3.0
    if (x1 - ax) ** 2 + (y1 - ay) ** 2 <= t2 and (x2 - bx) ** 2 + (y2 - by) ** 2 <= t2:
        out.append(p3)
        return
    x01 = (x0 + x1) * 0.5
    y01 = (y0 + y1) * 0.5
    x12 = (x1 + x2) * 0.5
    y12 = (y1 + y2) * 0.5
    x23 = (x2 + x3) * 0.5
    y23 = (y2 + y3) * 0.5
    xa = (x01 + x12) * 0.5
    ya = (y01 + y12) * 0.5
    xb = (x12 + x23) * 0.5
    yb = (y12 + y23) * 0.5
    xm = (xa + xb) * 0.5
    ym = (ya + yb) * 0.5
    _cubic(p0, (x01, y01), (xa, ya), (xm, ym), tol, out, depth + 1)
    _cubic((xm, ym), (xb, yb), (x23, y23), p3, tol, out, depth + 1)


def _quad(p0, p1, p2, tol, out, depth=0):
    """Adaptively flatten a quadratic bezier."""
    x0, y0 = p0
    x1, y1 = p1
    x2, y2 = p2
    if depth >= 18:
        out.append(p2)
        return
    mx = (x0 + 2 * x1 + x2) * 0.25
    my = (y0 + 2 * y1 + y2) * 0.25
    dx = (x0 + x2) * 0.5 - mx
    dy = (y0 + y2) * 0.5 - my
    if dx * dx + dy * dy <= tol * tol:
        out.append(p2)
        return
    x01 = (x0 + x1) * 0.5
    y01 = (y0 + y1) * 0.5
    x12 = (x1 + x2) * 0.5
    y12 = (y1 + y2) * 0.5
    xm = (x01 + x12) * 0.5
    ym = (y01 + y12) * 0.5
    _quad(p0, (x01, y01), (xm, ym), tol, out, depth + 1)
    _quad((xm, ym), (x12, y12), p2, tol, out, depth + 1)


def _arc(x0, y0, rx, ry, phi_deg, laf, sf, x1, y1, tol):
    """Flatten an SVG elliptical arc (endpoint parameterization).

    Returns points excluding the start point; [] if degenerate.
    """
    if abs(x1 - x0) < 1e-12 and abs(y1 - y0) < 1e-12:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-12 or ry < 1e-12:
        return [(x1, y1)]
    phi = math.radians(phi_deg % 360.0)
    cosp = math.cos(phi)
    sinp = math.sin(phi)
    dx2 = (x0 - x1) / 2.0
    dy2 = (y0 - y1) / 2.0
    x1p = cosp * dx2 + sinp * dy2
    y1p = -sinp * dx2 + cosp * dy2
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    if den == 0:
        return [(x1, y1)]
    cq = math.sqrt(max(0.0, num / den))
    if laf == sf:
        cq = -cq
    cxp = cq * rx * y1p / ry
    cyp = -cq * ry * x1p / rx
    cx = cosp * cxp - sinp * cyp + (x0 + x1) / 2.0
    cy = sinp * cxp + cosp * cyp + (y0 + y1) / 2.0

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        n = math.sqrt((ux * ux + uy * uy) * (vx * vx + vy * vy))
        if n == 0:
            return 0.0
        a = math.acos(max(-1.0, min(1.0, dot / n)))
        if ux * vy - uy * vx < 0:
            a = -a
        return a

    theta1 = angle(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sf and dtheta > 0:
        dtheta -= 2 * math.pi
    elif sf and dtheta < 0:
        dtheta += 2 * math.pi
    rmax = max(rx, ry)
    if rmax <= tol:
        n = 2
    else:
        dseg = 2.0 * math.acos(max(-1.0, 1.0 - tol / rmax))
        n = int(math.ceil(abs(dtheta) / max(dseg, 1e-6)))
        n = max(2, min(n, 512))
    pts = []
    for i in range(1, n + 1):
        t = theta1 + dtheta * i / n
        ct, st = math.cos(t), math.sin(t)
        pts.append((cx + rx * ct * cosp - ry * st * sinp, cy + rx * ct * sinp + ry * st * cosp))
    return pts


def flatten_path(d, tol):
    """SVG path data -> [(points, closed), ...] in user coordinates."""
    pp = _PP(d)
    subs = []
    cur = None
    start = None
    closed = False
    x = y = 0.0
    last_c = None
    lcx = lcy = qcx = qcy = 0.0

    def newsub(px, py):
        nonlocal cur, start, closed
        if cur is not None:
            subs.append((cur, closed))
        cur = [(px, py)]
        start = (px, py)
        closed = False

    def ensure():
        if cur is None:
            newsub(x, y)

    while True:
        c = pp.cmd()
        if c is None:
            break
        C = c.upper()
        rel = c != C
        if C == "M":
            first = True
            while pp.more():
                nx = pp.num()
                ny = pp.num()
                if rel:
                    nx += x
                    ny += y
                if first:
                    newsub(nx, ny)
                    first = False
                else:
                    ensure()
                    cur.append((nx, ny))
                x, y = nx, ny
            last_c = "M"
        elif C == "L":
            while pp.more():
                nx = pp.num()
                ny = pp.num()
                if rel:
                    nx += x
                    ny += y
                ensure()
                cur.append((nx, ny))
                x, y = nx, ny
            last_c = "L"
        elif C == "H":
            while pp.more():
                nx = pp.num()
                if rel:
                    nx += x
                ensure()
                cur.append((nx, y))
                x = nx
            last_c = "H"
        elif C == "V":
            while pp.more():
                ny = pp.num()
                if rel:
                    ny += y
                ensure()
                cur.append((x, ny))
                y = ny
            last_c = "V"
        elif C == "C":
            while pp.more():
                x1 = pp.num()
                y1 = pp.num()
                x2 = pp.num()
                y2 = pp.num()
                nx = pp.num()
                ny = pp.num()
                if rel:
                    x1 += x
                    y1 += y
                    x2 += x
                    y2 += y
                    nx += x
                    ny += y
                ensure()
                _cubic((x, y), (x1, y1), (x2, y2), (nx, ny), tol, cur)
                lcx, lcy = x2, y2
                x, y = nx, ny
            last_c = "C"
        elif C == "S":
            while pp.more():
                x2 = pp.num()
                y2 = pp.num()
                nx = pp.num()
                ny = pp.num()
                if rel:
                    x2 += x
                    y2 += y
                    nx += x
                    ny += y
                if last_c in ("C", "S"):
                    x1, y1 = 2 * x - lcx, 2 * y - lcy
                else:
                    x1, y1 = x, y
                ensure()
                _cubic((x, y), (x1, y1), (x2, y2), (nx, ny), tol, cur)
                lcx, lcy = x2, y2
                x, y = nx, ny
            last_c = "S"
        elif C == "Q":
            while pp.more():
                x1 = pp.num()
                y1 = pp.num()
                nx = pp.num()
                ny = pp.num()
                if rel:
                    x1 += x
                    y1 += y
                    nx += x
                    ny += y
                ensure()
                _quad((x, y), (x1, y1), (nx, ny), tol, cur)
                qcx, qcy = x1, y1
                x, y = nx, ny
            last_c = "Q"
        elif C == "T":
            while pp.more():
                nx = pp.num()
                ny = pp.num()
                if rel:
                    nx += x
                    ny += y
                if last_c in ("Q", "T"):
                    x1, y1 = 2 * x - qcx, 2 * y - qcy
                else:
                    x1, y1 = x, y
                ensure()
                _quad((x, y), (x1, y1), (nx, ny), tol, cur)
                qcx, qcy = x1, y1
                x, y = nx, ny
            last_c = "T"
        elif C == "A":
            while pp.more():
                rx = pp.num()
                ry = pp.num()
                rot = pp.num()
                laf = pp.flag()
                sf = pp.flag()
                nx = pp.num()
                ny = pp.num()
                if rel:
                    nx += x
                    ny += y
                ensure()
                cur.extend(_arc(x, y, rx, ry, rot, laf, sf, nx, ny, tol))
                x, y = nx, ny
            last_c = "A"
        elif C == "Z":
            if cur is not None:
                subs.append((cur, True))
                cur = None
                if start is not None:
                    x, y = start
            last_c = "Z"
        else:
            raise PathError(f"unsupported path command {c!r}")
    if cur is not None:
        subs.append((cur, False))
    return subs


# ---------------------------------------------------------------------------
# Basic shapes -> subpaths (user coordinates)
# ---------------------------------------------------------------------------


def _seg_count(radius, tol):
    """Segments for a full circle of `radius` given chord tolerance."""
    if radius <= tol:
        return 8
    dseg = 2.0 * math.acos(max(-1.0, 1.0 - tol / radius))
    return max(8, min(720, int(math.ceil(2 * math.pi / max(dseg, 1e-6)))))


def _ellipse_pts(cx, cy, rx, ry, tol):
    n = _seg_count(max(rx, ry), tol)
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    return pts


def _rounded_rect_pts(x, y, w, h, rx, ry, tol):
    rx = min(rx, w / 2.0)
    ry = min(ry, h / 2.0)
    if rx <= 0 or ry <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    kx = rx * 0.5522847498307936
    ky = ry * 0.5522847498307936
    pts = [(x + rx, y)]

    def cubic(x0, y0, x1, y1, x2, y2, x3, y3):
        _cubic((x0, y0), (x1, y1), (x2, y2), (x3, y3), tol, pts)

    pts.append((x + w - rx, y))
    cubic(x + w - rx, y, x + w - rx + kx, y, x + w, y + ry - ky, x + w, y + ry)
    pts.append((x + w, y + h - ry))
    cubic(x + w, y + h - ry, x + w, y + h - ry + ky, x + w - rx + kx, y + h, x + w - rx, y + h)
    pts.append((x + rx, y + h))
    cubic(x + rx, y + h, x + rx - kx, y + h, x, y + h - ky, x, y + h - ry)
    pts.append((x, y + ry))
    cubic(x, y + ry, x, y + ry - ky, x + rx - kx, y, x + rx, y)
    return pts


def shape_subpaths(tag, el, tol):
    """Elementary shape -> [(points, closed)] in user units (empty if N/A)."""
    if tag == "rect":
        x = _f(el.get("x"))
        y = _f(el.get("y"))
        w = parse_length(el.get("width"), default=0.0)
        h = parse_length(el.get("height"), default=0.0)
        if w <= 0 or h <= 0:
            return []
        rx = parse_length(el.get("rx"), default=0.0) or 0.0
        ry = parse_length(el.get("ry"), default=0.0) or 0.0
        if rx > 0 and ry <= 0:
            ry = rx
        if ry > 0 and rx <= 0:
            rx = ry
        if rx <= 0 and ry <= 0:
            return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)]
        return [(_rounded_rect_pts(x, y, w, h, rx, ry, tol), True)]
    if tag == "circle":
        cx = _f(el.get("cx"))
        cy = _f(el.get("cy"))
        r = parse_length(el.get("r"), default=0.0)
        if r <= 0:
            return []
        return [(_ellipse_pts(cx, cy, r, r, tol), True)]
    if tag == "ellipse":
        cx = _f(el.get("cx"))
        cy = _f(el.get("cy"))
        rx = parse_length(el.get("rx"), default=0.0)
        ry = parse_length(el.get("ry"), default=0.0)
        if rx <= 0 or ry <= 0:
            return []
        return [(_ellipse_pts(cx, cy, rx, ry, tol), True)]
    if tag == "line":
        x1 = _f(el.get("x1"))
        y1 = _f(el.get("y1"))
        x2 = _f(el.get("x2"))
        y2 = _f(el.get("y2"))
        return [([(x1, y1), (x2, y2)], False)]
    if tag in ("polyline", "polygon"):
        nums = [float(v) for v in NUM_RE.findall(el.get("points", ""))]
        pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        if len(pts) < 2:
            return []
        return [(pts, tag == "polygon")]
    if tag == "path":
        d = el.get("d")
        if not d:
            return []
        try:
            return flatten_path(d, tol)
        except PathError:
            return []
    return []


# ---------------------------------------------------------------------------
# Stroke geometry: quads + joins + caps + dashes  (device coordinates)
# ---------------------------------------------------------------------------


def _disc(cx, cy, r, n=12):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]


def _dashes(pts, closed, pattern, offset):
    """Split a polyline into dash runs -> [(points, False)]."""
    segs = []
    n = len(pts)
    count = n if closed else n - 1
    total_len = 0.0
    for i in range(count):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        L = math.hypot(x1 - x0, y1 - y0)
        segs.append(L)
        total_len += L
    pat_total = sum(pattern)
    if pat_total <= 1e-9:
        return [(pts, closed)]
    pos = offset % pat_total
    k = 0
    rem = pattern[0]
    acc = 0.0
    for i, dl in enumerate(pattern):
        if pos < acc + dl:
            k = i
            rem = acc + dl - pos
            break
        acc += dl
    else:
        k = 0
        rem = pattern[0]
    on = k % 2 == 0
    runs = []
    cur = None
    for i, L in enumerate(segs):
        if L <= 1e-12:
            continue
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s = 0.0
        while s < L - 1e-9:
            step = min(rem, L - s)
            e = s + step
            ax = x0 + (x1 - x0) * (s / L)
            ay = y0 + (y1 - y0) * (s / L)
            bx = x0 + (x1 - x0) * (e / L)
            by = y0 + (y1 - y0) * (e / L)
            if on:
                if cur is None:
                    cur = [(ax, ay)]
                cur.append((bx, by))
            else:
                if cur is not None and len(cur) >= 2:
                    runs.append((cur, False))
                cur = None
            s = e
            rem -= step
            if rem <= 1e-9:
                k = (k + 1) % len(pattern)
                rem = pattern[k]
                on = k % 2 == 0
    if cur is not None and len(cur) >= 2:
        runs.append((cur, False))
    return runs


def _stroke_run(pts, closed, hw, cap, join, miter):
    """Stroke one polyline run -> list of polygons (device coords)."""
    polys = []
    n = len(pts)
    count = n if closed else n - 1
    dirs = []
    norms = []
    for i in range(count):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy)
        if L <= 1e-12:
            dirs.append(None)
            norms.append(None)
            continue
        ux, uy = dx / L, dy / L
        dirs.append((ux, uy))
        nx, ny = -uy * hw, ux * hw
        norms.append((nx, ny))
        polys.append([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)])
    # joins
    jcount = count if closed else count - 1
    for i in range(max(0, jcount)):
        d1 = dirs[i % len(dirs)] if dirs else None
        d2 = dirs[(i + 1) % len(dirs)] if dirs else None
        if not d1 or not d2:
            continue
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        dot = d1[0] * d2[0] + d1[1] * d2[1]
        if abs(cross) < 1e-9 and dot > 0:
            continue
        v = pts[(i + 1) % n]
        n1 = norms[i % len(norms)]
        n2 = norms[(i + 1) % len(norms)]
        if join == "round":
            polys.append(_disc(v[0], v[1], hw))
            continue
        if cross > 0:
            o1 = (-n1[0], -n1[1])
            o2 = (-n2[0], -n2[1])
        else:
            o1 = n1
            o2 = n2
        a1 = (v[0] + o1[0], v[1] + o1[1])
        a2 = (v[0] + o2[0], v[1] + o2[1])
        polys.append([a1, v, a2])
        if join == "miter":
            denom = 1.0 + dot
            if denom > 1e-6 and math.sqrt(2.0 / denom) <= miter:
                mx = v[0] + (o1[0] + o2[0]) / denom
                my = v[1] + (o1[1] + o2[1]) / denom
                polys.append([a1, (mx, my), a2, v])
    # caps on open runs
    if not closed and n >= 2 and cap != "butt":
        p0 = pts[0]
        p1 = pts[-1]
        d0 = dirs[0] if dirs and dirs[0] else (1.0, 0.0)
        d1 = dirs[-1] if dirs and dirs[-1] else (1.0, 0.0)
        if cap == "round":
            polys.append(_disc(p0[0], p0[1], hw))
            polys.append(_disc(p1[0], p1[1], hw))
        elif cap == "square":
            ux, uy = d0
            nx, ny = -uy * hw, ux * hw
            polys.append([
                (p0[0] + nx, p0[1] + ny),
                (p0[0] + nx - ux * hw, p0[1] + ny - uy * hw),
                (p0[0] - nx - ux * hw, p0[1] - ny - uy * hw),
                (p0[0] - nx, p0[1] - ny),
            ])
            ux, uy = d1
            nx, ny = -uy * hw, ux * hw
            polys.append([
                (p1[0] + nx, p1[1] + ny),
                (p1[0] + nx + ux * hw, p1[1] + ny + uy * hw),
                (p1[0] - nx + ux * hw, p1[1] - ny + uy * hw),
                (p1[0] - nx, p1[1] - ny),
            ])
    return polys


def stroke_polygons(subpaths, hw, cap, join, miter, dash, dashoff):
    """All stroke geometry for a shape -> list of polygons (device coords)."""
    polys = []
    if hw <= 0:
        return polys
    for pts, closed in subpaths:
        if len(pts) < 2:
            if pts and cap == "round":
                polys.append(_disc(pts[0][0], pts[0][1], hw))  # zero-length dot
            continue
        if dash:
            runs = _dashes(pts, closed, dash, dashoff)
        else:
            runs = [(pts, closed)]
        for rpts, rclosed in runs:
            if len(rpts) >= 2:
                polys.extend(_stroke_run(rpts, rclosed, hw, cap, join, miter))
    return polys


# ---------------------------------------------------------------------------
# SVG document model (built-in engine)
# ---------------------------------------------------------------------------

SHAPE_TAGS = {"rect", "circle", "ellipse", "line", "polyline", "polygon", "path"}
GROUP_TAGS = {"g", "a", "switch"}
# never rendered directly; silent (library / metadata / animation elements)
SILENT_TAGS = {
    "defs",
    "symbol",
    "marker",
    "title",
    "desc",
    "metadata",
    "script",
    "style",
    "font",
    "font-face",
    "cursor",
    "view",
    "animate",
    "animateTransform",
    "animateMotion",
    "set",
    "stop",
    "linearGradient",
    "radialGradient",
}
# presentational constructs we cannot draw with the built-in engine
UNSUPPORTED_TAGS = {"clipPath", "mask", "pattern", "filter"}

_PRESENTATION = (
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
    "stroke-dashoffset",
    "fill-rule",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
    "color",
    "display",
    "visibility",
)

_FRIENDLY = {
    "linearGradient": "gradient",
    "radialGradient": "gradient",
    "clipPath": "clipPath",
    "text": "text",
    "image": "image",
    "textPath": "text",
    "tspan": "text",
    "use": "use",
}


def _local(tag):
    if isinstance(tag, str):
        return tag.split("}", 1)[1] if tag.startswith("{") else tag
    return None


def _style(el, inherited):
    st = dict(inherited)
    for k in _PRESENTATION:
        v = el.get(k)
        if v is not None:
            v = v.strip()
            if v and v.lower() != "inherit":
                st[k] = v
    s = el.get("style")
    if s:
        for decl in s.split(";"):
            if ":" not in decl:
                continue
            k, v = decl.split(":", 1)
            k = k.strip().lower()
            v = v.strip()
            if k in _PRESENTATION:
                if v and v.lower() != "inherit":
                    st[k] = v
                else:
                    st.pop(k, None)
    return st


class Warnings:
    def __init__(self):
        self.counts = Counter()
        self.errors = 0

    def add(self, what):
        self.counts[what] += 1

    @property
    def empty(self):
        return not self.counts and not self.errors

    def text(self):
        bits = []
        for what, n in sorted(self.counts.items()):
            bits.append(f"{what} x{n}" if n > 1 else what)
        if self.errors:
            bits.append(f"{self.errors} broken element(s)")
        return ", ".join(bits)


def _parse_view_box(v):
    if not v:
        return None
    nums = [float(x) for x in NUM_RE.findall(v)]
    if len(nums) != 4 or nums[2] <= 0 or nums[3] <= 0:
        return None
    return nums  # minx, miny, w, h


def root_matrix(root, pw, ph):
    """Map root user space onto the (pw, ph) pixel canvas."""
    par = (root.get("preserveAspectRatio") or "xMidYMid meet").strip()
    vb = _parse_view_box(root.get("viewBox"))
    if vb is None:
        w = parse_length(root.get("width"), default=None)
        h = parse_length(root.get("height"), default=None)
        if not w or w <= 0:
            w = 300.0
        if not h or h <= 0:
            h = 150.0
        vb = [0.0, 0.0, w, h]
    minx, miny, vw, vh = vb
    sx = pw / vw
    sy = ph / vh
    if par.lower() == "none":
        tx = -minx * sx
        ty = -miny * sy
        return M(sx, 0, 0, sy, tx, ty)
    parts = par.split()
    align = parts[0] if parts else "xMidYMid"
    meet = (parts[1].lower() != "slice") if len(parts) > 1 else True
    s = min(sx, sy) if meet else max(sx, sy)
    sx = sy = s
    if "xMin" in align:
        tx = -minx * s
    elif "xMax" in align:
        tx = pw - vw * s - minx * s
    else:
        tx = (pw - vw * s) / 2.0 - minx * s
    if "yMin" in align:
        ty = -miny * s
    elif "yMax" in align:
        ty = ph - vh * s - miny * s
    else:
        ty = (ph - vh * s) / 2.0 - miny * s
    return M(s, 0, 0, s, tx, ty)


def intrinsic_size(root):
    """(w, h) in user units, for aspect + PNG export sizing."""
    vb = _parse_view_box(root.get("viewBox"))
    if vb:
        return vb[2], vb[3]
    w = parse_length(root.get("width"), default=None)
    h = parse_length(root.get("height"), default=None)
    if w and w > 0 and h and h > 0:
        return w, h
    return 300.0, 150.0


class Doc:
    """Parsed SVG ready to rasterize at any size."""

    def __init__(self, data):
        if isinstance(data, bytes):
            text = data.decode("utf-8", "replace")
        else:
            text = data
        self.root = ET.fromstring(text)  # may raise ET.ParseError
        if _local(self.root.tag) != "svg":
            raise ValueError("not an SVG document (root element is not <svg>)")
        self.ids = {}
        for el in self.root.iter():
            if isinstance(el.tag, str):
                eid = el.get("id")
                if eid:
                    self.ids[eid] = el

    def build(self, pw, ph):
        """Walk the tree -> (items, warnings).  Items are device-space."""
        warn = Warnings()
        items = []
        st = _style(self.root, {})
        st["_alpha"] = 1.0
        self._walk(self.root, root_matrix(self.root, pw, ph), st, items, warn, frozenset())
        return items, warn

    def _walk(self, el, mat, st, items, warn, use_stack):
        tag = _local(el.tag)
        if tag is None:
            return
        if tag == "svg":
            # root: viewBox already baked into `mat`; nested: treated as group
            tag = "g"
        if st.get("display") == "none":
            return
        if tag in SILENT_TAGS:
            return
        if tag in UNSUPPORTED_TAGS:
            warn.add(tag)
            return
        if tag in GROUP_TAGS:
            st = _style(el, st)
            if "opacity" in st:
                st["_alpha"] = st.get("_alpha", 1.0) * max(0.0, min(1.0, _f(st["opacity"], 1.0)))
                del st["opacity"]
            tr = parse_transform(el.get("transform"))
            m = mat @ tr
            for child in el:
                self._walk(child, m, st, items, warn, use_stack)
            return
        if tag == "use":
            href = el.get("href") or el.get(XLINK_HREF) or ""
            st = _style(el, st)
            if not href.startswith("#"):
                warn.add("use")
                return
            tgt = self.ids.get(href[1:])
            if tgt is None or id(tgt) in use_stack:
                warn.add("use")
                return
            x = _f(el.get("x"))
            y = _f(el.get("y"))
            tr = parse_transform(el.get("transform"))
            m = mat @ tr @ M(1, 0, 0, 1, x, y)
            self._walk(tgt, m, st, items, warn, use_stack | {id(tgt)})
            return
        if tag not in SHAPE_TAGS:
            warn.add(_FRIENDLY.get(tag, tag))
            return
        # --- a shape ---
        st = _style(el, st)
        if st.get("visibility") in ("hidden", "collapse"):
            return
        tr = parse_transform(el.get("transform"))
        m = mat @ tr
        tol = 0.2 / m.scale()
        try:
            subs = shape_subpaths(tag, el, tol)
        except (PathError, ValueError, ZeroDivisionError):
            warn.errors += 1
            return
        if not subs:
            return
        dev = []
        for pts, closed in subs:
            dev.append(([m.apply(px, py) for (px, py) in pts], closed))
        alpha = st.get("_alpha", 1.0)
        if "opacity" in st:
            alpha *= max(0.0, min(1.0, _f(st["opacity"], 1.0)))
        # fill
        fill = None
        fillv = st.get("fill", "black")
        if isinstance(fillv, str) and fillv.lower().startswith("url("):
            warn.add("gradient")
        else:
            try:
                cur = parse_color(st.get("color", "black"), (0, 0, 0))
                fill = parse_color(fillv, cur[:3])
            except ValueError:
                fill = (0, 0, 0, 1.0)
        if fill is not None:
            fo = max(0.0, min(1.0, _f(st.get("fill-opacity"), 1.0)))
            fill = (fill[0], fill[1], fill[2], fill[3] * fo * alpha)
            if fill[3] <= 0.003:
                fill = None
        # stroke
        stroke = None
        strokev = st.get("stroke", "none")
        if isinstance(strokev, str) and strokev.lower().startswith("url("):
            warn.add("gradient")
        else:
            try:
                cur = parse_color(st.get("color", "black"), (0, 0, 0))
                stroke = parse_color(strokev, cur[:3])
            except ValueError:
                stroke = None
        hw = 0.0
        dash = None
        dashoff = 0.0
        if stroke is not None:
            sw = parse_length(st.get("stroke-width"), default=1.0)
            if sw is None or sw <= 0:
                sw = 1.0
            hw = sw * m.scale() / 2.0
            so = max(0.0, min(1.0, _f(st.get("stroke-opacity"), 1.0)))
            stroke = (stroke[0], stroke[1], stroke[2], stroke[3] * so * alpha)
            if stroke[3] <= 0.003 or hw <= 0.01:
                stroke = None
        if stroke is not None:
            dstr = st.get("stroke-dasharray", "none")
            if dstr and dstr.lower() not in ("none", "inherit"):
                vals = [parse_length(v, default=0.0) for v in re.split(r"[\s,]+", dstr) if v]
                vals = [v for v in vals if v is not None and v > 0]
                if vals:
                    if len(vals) % 2:
                        vals = vals + vals
                    dash = [v * m.scale() for v in vals]
                    do = parse_length(st.get("stroke-dashoffset"), default=0.0)
                    dashoff = (do or 0.0) * m.scale()
        cap = st.get("stroke-linecap", "butt")
        if cap not in ("round", "square", "butt"):
            cap = "butt"
        join = st.get("stroke-linejoin", "miter")
        if join not in ("round", "bevel", "miter"):
            join = "miter"
        miter = max(1.0, _f(st.get("stroke-miterlimit"), 4.0))
        rule = "evenodd" if st.get("fill-rule") == "evenodd" else "nonzero"
        if fill is None and stroke is None:
            return
        items.append((dev, fill, rule, stroke, hw, cap, join, miter, dash, dashoff))


# ---------------------------------------------------------------------------
# Rasterizer: scanline fill + supersampling
# ---------------------------------------------------------------------------


def _bbox(polys):
    minx = miny = 1e30
    maxx = maxy = -1e30
    for pts, _closed in polys:
        for x, y in pts:
            if x < minx:
                minx = x
            if x > maxx:
                maxx = x
            if y < miny:
                miny = y
            if y > maxy:
                maxy = y
    return minx, miny, maxx, maxy


def _edges_of(polys, closed_ring):
    """polys -> edge list [(y0, y1, x_at_y0, dxdy, wind)] with y0 < y1."""
    edges = []
    for pts, closed in polys:
        n = len(pts)
        if n < 2:
            continue
        if closed_ring and n > 2 and pts[0] != pts[-1]:
            pts = pts + [pts[0]]
            n += 1
        for i in range(n - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            if y0 == y1:
                continue
            if y0 < y1:
                edges.append((y0, y1, x0, (x1 - x0) / (y1 - y0), 1))
            else:
                edges.append((y1, y0, x1, (x0 - x1) / (y0 - y1), -1))
    return edges


def _sweep(W, H, edges, y_lo, y_hi, on_span):
    """Active-edge scanline sweep; calls on_span(x0, x1, y)."""
    if not edges:
        return
    edges.sort(key=lambda e: e[0])
    y_lo = max(0, int(math.floor(y_lo)))
    y_hi = min(H - 1, int(math.ceil(y_hi)))
    if y_hi < y_lo:
        return
    aet = []
    j = 0
    ne = len(edges)
    for y in range(y_lo, y_hi + 1):
        yc = y + 0.5
        while j < ne and edges[j][0] <= yc:
            e = edges[j]
            if e[1] > yc:
                aet.append([e[2] + e[3] * (yc - e[0]), e[1], e[3], e[4]])
            j += 1
        if aet:
            aet = [e for e in aet if e[1] > yc]
        if not aet:
            if j >= ne:
                break
            continue
        xs = sorted((e[0], e[3]) for e in aet)
        i = 0
        nx = len(xs)
        while i < nx:
            x0 = xs[i][0]
            if i + 1 >= nx:
                break
            x1 = xs[i + 1][0]
            on_span(x0, x1, y)
            i += 2
        for e in aet:
            e[0] += e[2]


def _fill_evenodd(buf, W, H, polys, cr, cg, cb, ca):
    """Fill polys even-odd with (cr,cg,cb,ca) into premultiplied buffer."""
    minx, miny, maxx, maxy = _bbox(polys)
    if minx > W or maxx < 0 or miny > H or maxy < 0:
        return
    edges = _edges_of(polys, closed_ring=True)
    opaque = ca >= 0.999
    inv = 1.0 - ca

    def span(x0, x1, y):
        if x1 <= x0:
            return
        xi0 = int(math.ceil(x0 - 0.5))
        xi1 = int(math.floor(x1 - 0.5))
        if xi0 < 0:
            xi0 = 0
        if xi1 > W - 1:
            xi1 = W - 1
        if xi1 < xi0:
            return
        base = (y * W + xi0) * 4
        if opaque:
            for i in range(xi0, xi1 + 1):
                b = base
                buf[b] = cr
                buf[b + 1] = cg
                buf[b + 2] = cb
                buf[b + 3] = 1.0
                base += 4
        else:
            for i in range(xi0, xi1 + 1):
                b = base
                buf[b] = buf[b] * inv + cr * ca
                buf[b + 1] = buf[b + 1] * inv + cg * ca
                buf[b + 2] = buf[b + 2] * inv + cb * ca
                buf[b + 3] = buf[b + 3] * inv + ca
                base += 4

    _sweep(W, H, edges, miny, maxy, span)


def _fill_nonzero(buf, W, H, polys, cr, cg, cb, ca):
    """Fill polys with the nonzero winding rule."""
    minx, miny, maxx, maxy = _bbox(polys)
    if minx > W or maxx < 0 or miny > H or maxy < 0:
        return
    edges = _edges_of(polys, closed_ring=True)
    opaque = ca >= 0.999
    inv = 1.0 - ca

    # Nonzero needs winding accumulation across crossings, so we re-derive
    # spans inside a custom loop (still using the same AET idea).
    if not edges:
        return
    edges.sort(key=lambda e: e[0])
    y_lo = max(0, int(math.floor(miny)))
    y_hi = min(H - 1, int(math.ceil(maxy)))
    if y_hi < y_lo:
        return
    aet = []
    j = 0
    ne = len(edges)
    for y in range(y_lo, y_hi + 1):
        yc = y + 0.5
        while j < ne and edges[j][0] <= yc:
            e = edges[j]
            if e[1] > yc:
                aet.append([e[2] + e[3] * (yc - e[0]), e[1], e[3], e[4]])
            j += 1
        if aet:
            aet = [e for e in aet if e[1] > yc]
        if not aet:
            if j >= ne:
                break
            continue
        xs = sorted((e[0], e[3]) for e in aet)
        wind = 0
        start = None
        for x, w in xs:
            prev = wind
            wind += w
            if prev == 0 and wind != 0:
                start = x
            elif prev != 0 and wind == 0 and start is not None:
                x0, x1 = start, x
                if x1 > x0:
                    xi0 = int(math.ceil(x0 - 0.5))
                    xi1 = int(math.floor(x1 - 0.5))
                    if xi0 < 0:
                        xi0 = 0
                    if xi1 > W - 1:
                        xi1 = W - 1
                    if xi1 >= xi0:
                        base = (y * W + xi0) * 4
                        if opaque:
                            for i in range(xi0, xi1 + 1):
                                b = base
                                buf[b] = cr
                                buf[b + 1] = cg
                                buf[b + 2] = cb
                                buf[b + 3] = 1.0
                                base += 4
                        else:
                            for i in range(xi0, xi1 + 1):
                                b = base
                                buf[b] = buf[b] * inv + cr * ca
                                buf[b + 1] = buf[b + 1] * inv + cg * ca
                                buf[b + 2] = buf[b + 2] * inv + cb * ca
                                buf[b + 3] = buf[b + 3] * inv + ca
                                base += 4
        for e in aet:
            e[0] += e[2]


def _mask_polys(mask, W, H, polys):
    """OR-fill polygons into a byte mask (1 = covered)."""
    minx, miny, maxx, maxy = _bbox(polys)
    if minx > W or maxx < 0 or miny > H or maxy < 0:
        return

    def span(x0, x1, y):
        if x1 <= x0:
            return
        xi0 = int(math.ceil(x0 - 0.5))
        xi1 = int(math.floor(x1 - 0.5))
        if xi0 < 0:
            xi0 = 0
        if xi1 > W - 1:
            xi1 = W - 1
        if xi1 < xi0:
            return
        base = y * W
        mask[base + xi0 : base + xi1 + 1] = b"\x01" * (xi1 - xi0 + 1)

    _sweep(W, H, _edges_of(polys, closed_ring=True), miny, maxy, span)


def _blend_mask(buf, W, H, mask, cr, cg, cb, ca):
    """Blend a color where mask==1 (used for strokes)."""
    inv = 1.0 - ca
    crp, cgp, cbp = cr * ca, cg * ca, cb * ca
    total = W * H
    pos = 0
    while True:
        s = mask.find(1, pos)
        if s < 0 or s >= total:
            break
        e = mask.find(0, s)
        if e < 0 or e > total:
            e = total
        for i in range(s, e):
            b = i * 4
            buf[b] = buf[b] * inv + crp
            buf[b + 1] = buf[b + 1] * inv + cgp
            buf[b + 2] = buf[b + 2] * inv + cbp
            buf[b + 3] = buf[b + 3] * inv + ca
        pos = e


def _downsample(buf, W, H, ss, pw, ph):
    """Average ss x ss blocks of the premultiplied buffer -> RGBA bytes."""
    out = bytearray(pw * ph * 4)
    n = ss * ss
    for py in range(ph):
        dstrow = py * pw * 4
        srow = py * ss * W * 4
        for px in range(pw):
            sr = sg = sb = sa = 0.0
            col = px * ss * 4
            for dy in range(ss):
                base = srow + dy * W * 4 + col
                for dx in range(ss):
                    b = base + dx * 4
                    sr += buf[b]
                    sg += buf[b + 1]
                    sb += buf[b + 2]
                    sa += buf[b + 3]
            d = dstrow + px * 4
            a = sa / n
            if a <= 0.004:
                continue
            out[d + 3] = int(0.5 + a * 255)
            ia = 1.0 / a
            out[d] = max(0, min(255, int(0.5 + sr / n * ia * 255)))
            out[d + 1] = max(0, min(255, int(0.5 + sg / n * ia * 255)))
            out[d + 2] = max(0, min(255, int(0.5 + sb / n * ia * 255)))
    return bytes(out)


def rasterize(items, pw, ph, ss=3):
    """Render device-space items -> (pw, ph, rgba bytes)."""
    W, H = pw * ss, ph * ss
    buf = array("f", [0.0]) * (W * H * 4)  # premultiplied RGBA
    for dev, fill, rule, stroke, hw, cap, join, miter, dash, dashoff in items:
        if ss != 1:
            sdev = [([(x * ss, y * ss) for (x, y) in pts], closed) for pts, closed in dev]
        else:
            sdev = dev
        if fill is not None:
            cr, cg, cb, ca = (fill[0] / 255.0, fill[1] / 255.0, fill[2] / 255.0, fill[3])
            if rule == "evenodd":
                _fill_evenodd(buf, W, H, sdev, cr, cg, cb, ca)
            else:
                _fill_nonzero(buf, W, H, sdev, cr, cg, cb, ca)
        if stroke is not None:
            polys = stroke_polygons(
                sdev, hw * ss, cap, join, miter, [d * ss for d in dash] if dash else None, dashoff * ss
            )
            if polys:
                mask = bytearray(W * H)
                for p in polys:
                    _mask_polys(mask, W, H, [(p, True)])
                sr, sg, sb, sa = (stroke[0] / 255.0, stroke[1] / 255.0, stroke[2] / 255.0, stroke[3])
                _blend_mask(buf, W, H, mask, sr, sg, sb, sa)
    return pw, ph, _downsample(buf, W, H, ss, pw, ph)


# ---------------------------------------------------------------------------
# Minimal PNG codec (stdlib zlib only)
# ---------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def encode_png(w, h, rgba):
    """RGBA bytes (w*h*4) -> PNG file bytes."""

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = bytearray()
    step = w * 4
    for y in range(h):
        raw.append(0)  # filter: none
        raw += rgba[y * step : (y + 1) * step]
    return (
        _PNG_SIG
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def _paeth(a, b, c):
    pa = abs(b - c)
    pb = abs(a - c)
    pc = abs(a + b - 2 * c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png(data):
    """PNG bytes (8-bit, non-interlaced, ct 0/2/3/4/6) -> (w, h, rgba)."""
    if data[:8] != _PNG_SIG:
        raise ValueError("not a PNG")
    i = 8
    ihdr = None
    plte = b""
    trns = b""
    idat = bytearray()
    ln = len(data)
    while i + 8 <= ln:
        clen = struct.unpack(">I", data[i : i + 4])[0]
        tag = data[i + 4 : i + 8]
        chunk = data[i + 8 : i + 8 + clen]
        i += 12 + clen
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", chunk)
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"PLTE":
            plte = chunk
        elif tag == b"tRNS":
            trns = chunk
        elif tag == b"IEND":
            break
    if not ihdr:
        raise ValueError("PNG missing IHDR")
    w, h, bd, ct, _comp, _filt, inter = ihdr
    if bd != 8 or inter != 0:
        raise ValueError("unsupported PNG (need 8-bit non-interlaced)")
    if ct not in (0, 2, 3, 4, 6):
        raise ValueError(f"unsupported PNG color type {ct}")
    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    stride = w * nch
    raw = zlib.decompress(bytes(idat))
    if len(raw) < h * (stride + 1):
        raise ValueError("truncated PNG")
    out = bytearray(w * h * 4)
    prev = bytearray(stride)
    for y in range(h):
        ro = y * (stride + 1)
        ft = raw[ro]
        line = bytearray(raw[ro + 1 : ro + 1 + stride])
        if ft == 1:
            for x in range(nch, stride):
                line[x] = (line[x] + line[x - nch]) & 255
        elif ft == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif ft == 3:
            for x in range(stride):
                a = line[x - nch] if x >= nch else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif ft == 4:
            for x in range(stride):
                a = line[x - nch] if x >= nch else 0
                b = prev[x]
                c = prev[x - nch] if x >= nch else 0
                line[x] = (line[x] + _paeth(a, b, c)) & 255
        elif ft != 0:
            raise ValueError(f"bad PNG filter {ft}")
        if ct == 6:
            out[y * w * 4 : (y + 1) * w * 4] = line
        else:
            base = y * w * 4
            for x in range(w):
                p = x * nch
                d = base + x * 4
                if ct == 2:
                    out[d], out[d + 1], out[d + 2], out[d + 3] = (line[p], line[p + 1], line[p + 2], 255)
                elif ct == 0:
                    v = line[p]
                    out[d], out[d + 1], out[d + 2], out[d + 3] = v, v, v, 255
                elif ct == 4:
                    v = line[p]
                    out[d], out[d + 1], out[d + 2], out[d + 3] = (v, v, v, line[p + 1])
                else:  # 3
                    idx = line[p]
                    if idx * 3 + 2 < len(plte):
                        out[d] = plte[idx * 3]
                        out[d + 1] = plte[idx * 3 + 1]
                        out[d + 2] = plte[idx * 3 + 2]
                    out[d + 3] = trns[idx] if idx < len(trns) else 255
        prev = line
    return w, h, bytes(out)


# ---------------------------------------------------------------------------
# Terminal capabilities
# ---------------------------------------------------------------------------


class TermInfo:
    def __init__(self):
        self.cols, self.rows, self.xpix, self.ypix = _term_size()
        self.is_tty = sys.stdout.isatty()
        self.kitty = False
        self.sixel = False
        self.truecolor = False
        self.bg = (0, 0, 0)
        self.cell_w, self.cell_h = 9, 18
        self._probe()
        env_term = os.environ.get("TERM", "")
        if os.environ.get("KITTY_WINDOW_ID") or env_term in ("xterm-kitty", "xterm-ghostty", "wezterm"):
            self.kitty = True
        if env_term in ("mlterm", "foot", "foot-extra", "yaft-256color"):
            self.sixel = True
        ct = (os.environ.get("COLORTERM") or "").lower()
        self.truecolor = (
            "truecolor" in ct or "24bit" in ct or env_term in ("xterm-kitty", "xterm-ghostty", "wezterm", "foot")
        )
        if self.xpix and self.cols:
            self.cell_w = max(1, self.xpix // self.cols)
            self.cell_h = max(1, self.ypix // self.rows) if self.ypix else 18

    def _probe(self):
        """Ask the terminal for DA1 (sixel), OSC 11 (bg) and CSI 16t (cell)."""
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return
        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
        except termios.error:
            return
        try:
            tty.setraw(fd)
            sys.stdout.write("\x1b[c\x1b]11;?\x07\x1b[16t")
            sys.stdout.flush()
            resp = b""
            deadline = time.monotonic() + 0.30
            quiet = 0.0
            while time.monotonic() < deadline:
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    resp += os.read(fd, 4096)
                    quiet = 0.0
                    if len(resp) > 2048:
                        break
                else:
                    quiet += 0.05
                    if resp and quiet >= 0.10:
                        break
            self._parse_probe(resp)
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except termios.error:
                pass

    def _parse_probe(self, resp):
        m = re.search(rb"\x1b\[\?([0-9;]+)c", resp)
        if m:
            attrs = m.group(1).decode("ascii", "replace").split(";")
            if "4" in attrs:
                self.sixel = True
        m = re.search(
            rb"\x1b\]11;rgb:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})"
            rb"/([0-9a-fA-F]{1,4})(?:\x07|\x1b\\)",
            resp,
        )
        if m:
            chan = []
            for g in (m.group(1), m.group(2), m.group(3)):
                v = int(g, 16)
                maxi = (1 << (4 * len(g))) - 1
                chan.append(round(v * 255 / maxi))
            self.bg = (chan[0], chan[1], chan[2])
        m = re.search(rb"\x1b\[6;(\d+);(\d+)t", resp)
        if m:
            self.cell_h = max(1, int(m.group(1)))
            self.cell_w = max(1, int(m.group(2)))


def _term_size():
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            ws = fcntl.ioctl(stream.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
            cols, rows, xp, yp = struct.unpack("hhhh", ws)
            if cols >= 4 and rows >= 2:
                return cols, rows, max(0, xp), max(0, yp)
        except Exception:
            pass
    try:
        sz = os.get_terminal_size()
        if sz.columns >= 4 and sz.lines >= 2:
            return sz.columns, sz.lines, 0, 0
    except Exception:
        pass
    return 80, 24, 0, 0


# ---------------------------------------------------------------------------
# Terminal output renderers
# ---------------------------------------------------------------------------


def composite_rgb(w, h, rgba, bg):
    """Blend RGBA over a background color -> RGB bytearray."""
    out = bytearray(w * h * 3)
    br, bgr, bb = bg
    mv = rgba
    for i in range(w * h):
        j = i * 4
        a = mv[j + 3]
        if a >= 255:
            k = i * 3
            out[k] = mv[j]
            out[k + 1] = mv[j + 1]
            out[k + 2] = mv[j + 2]
        elif a <= 0:
            k = i * 3
            out[k] = br
            out[k + 1] = bgr
            out[k + 2] = bb
        else:
            ia = 1.0 - a / 255.0
            k = i * 3
            out[k] = int(0.5 + mv[j] * (1 - ia) + br * ia)
            out[k + 1] = int(0.5 + mv[j + 1] * (1 - ia) + bgr * ia)
            out[k + 2] = int(0.5 + mv[j + 2] * (1 - ia) + bb * ia)
    return out


_CUBE = (0, 95, 135, 175, 215, 255)
_PAL256 = [(r, g, b) for r in _CUBE for g in _CUBE for b in _CUBE] + [(v, v, v) for v in range(8, 248, 10)]
_TO256_CACHE = {}


def _to256(c):
    v = _TO256_CACHE.get(c)
    if v is not None:
        return v
    r, g, b = c
    best = 0
    bd = 1 << 30
    for i, (pr, pg, pb) in enumerate(_PAL256):
        d = (r - pr) * (r - pr) + (g - pg) * (g - pg) + (b - pb) * (b - pb)
        if d < bd:
            bd = d
            best = i
    idx = 16 + best
    _TO256_CACHE[c] = idx
    return idx


def render_blocks(w, h, rgb, bg, truecolor):
    """RGB pixel grid -> half-block text (2 vertical pixels per cell)."""
    lines = []
    state = [None, None]  # current fg / bg as emitted
    for cy in range(h // 2):
        row = []
        for cx in range(w):
            i1 = (cy * 2 * w + cx) * 3
            i2 = ((cy * 2 + 1) * w + cx) * 3
            t = (rgb[i1], rgb[i1 + 1], rgb[i1 + 2])
            b = (rgb[i2], rgb[i2 + 1], rgb[i2 + 2])
            if t == bg and b == bg:
                if state[0] is not None:
                    row.append("\x1b[0m")
                    state[0] = state[1] = None
                row.append(" ")
                continue
            if not truecolor:
                t = _to256(t)
                b = _to256(b)
            if state[0] != t or state[1] != b:
                esc = []
                if state[0] != t:
                    if truecolor:
                        esc.append(f"\x1b[38;2;{t[0]};{t[1]};{t[2]}m")
                    else:
                        esc.append(f"\x1b[38;5;{t}m")
                if state[1] != b:
                    if truecolor:
                        esc.append(f"\x1b[48;2;{b[0]};{b[1]};{b[2]}m")
                    else:
                        esc.append(f"\x1b[48;5;{b}m")
                row.append("".join(esc))
                state[0], state[1] = t, b
            row.append("\u2580")  # upper half block
        while row and row[-1] == " ":
            row.pop()
        lines.append("".join(row))
    return "\n".join(lines) + "\n\x1b[0m"


def render_sixel(w, h, rgb):
    """RGB pixel grid -> DEC sixel byte stream."""
    cnt = Counter()
    mv = rgb
    for i in range(w * h):
        cnt[(mv[i * 3], mv[i * 3 + 1], mv[i * 3 + 2])] += 1
    if len(cnt) <= 250:
        pal = list(cnt.keys())
    else:
        pal = sorted(cnt, key=cnt.get, reverse=True)[:250]
        palset = {c: i for i, c in enumerate(pal)}
        qcache = {}

        def nearest(c):
            """Nearest palette color (not index) for an off-palette color."""
            q = (c[0] >> 3, c[1] >> 3, c[2] >> 3)
            v = qcache.get(q)
            if v is None:
                v = min(pal, key=lambda p: (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2)
                qcache[q] = v
            return v

        mv = bytearray(rgb)
        for i in range(w * h):
            c = (mv[i * 3], mv[i * 3 + 1], mv[i * 3 + 2])
            if c not in palset:
                mv[i * 3 : i * 3 + 3] = bytes(nearest(c))
    palidx = {c: i for i, c in enumerate(pal)}
    out = bytearray()
    out += b"\x1bPq"
    out += f'"1;1;{w};{h}'.encode("ascii")
    for i, (r, g, b) in enumerate(pal):
        out += f"#{i};2;{r * 100 // 255};{g * 100 // 255};{b * 100 // 255}".encode()
    for band in range(0, h, 6):
        colmap = {}
        ymax = min(band + 6, h)
        for x in range(w):
            for y in range(band, ymax):
                p = (y * w + x) * 3
                c = (mv[p], mv[p + 1], mv[p + 2])
                idx = palidx[c]
                cols = colmap.get(idx)
                if cols is None:
                    cols = colmap[idx] = {}
                cols[x] = cols.get(x, 0) | (1 << (y - band))
        if not colmap:
            out += b"-"
            continue
        for idx, cols in colmap.items():
            out += f"#{idx}$".encode()
            runv = None
            runn = 0
            for x in range(w + 1):
                v = cols.get(x, 0)
                if v == runv:
                    runn += 1
                    continue
                if runv is not None:
                    if runn >= 3:
                        out += f"!{runn}".encode() + bytes([63 + runv])
                    else:
                        out += bytes([63 + runv]) * runn
                runv = v
                runn = 1
            out += b"-"
    out += b"\x1b\\"
    return bytes(out)


def render_kitty(w, h, rgba, cols, rows):
    """RGBA -> kitty graphics protocol escape sequence (PNG upload)."""
    png = encode_png(w, h, rgba)
    b64 = base64.b64encode(png)
    aspect = w / h if h else 1.0
    c = min(cols, max(1, round(2.0 * aspect * rows)))
    r = max(1, min(rows, round(c / (2.0 * aspect))))
    out = []
    first = True
    for i in range(0, len(b64), 4096):
        chunk = b64[i : i + 4096]
        m = 1 if i + 4096 < len(b64) else 0
        if first:
            out.append(f"\x1b_Gf=100,a=T,q=2,c={c},r={r},m={m};".encode() + chunk + b"\x1b\\")
            first = False
        else:
            out.append(f"\x1b_Gm={m};".encode() + chunk + b"\x1b\\")
    out.append(f"\x1b[{r}B".encode())
    return b"".join(out)


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------


def paste_center(img, pw, ph):
    """Place (w, h, rgba) centered on a transparent pw*ph canvas."""
    w, h, rgba = img
    if w > pw or h > ph:
        # rare: clip by simple crop of the bottom/right overflow
        ww, hh = min(w, pw), min(h, ph)
        cropped = bytearray(ww * hh * 4)
        for y in range(hh):
            s = (y * w * 4, y * w * 4 + ww * 4)
            cropped[y * ww * 4 : (y + 1) * ww * 4] = rgba[s[0] : s[1]]
        w, h, rgba = ww, hh, bytes(cropped)
    canvas = bytearray(pw * ph * 4)
    x0 = (pw - w) // 2
    y0 = (ph - h) // 2
    for y in range(h):
        d = ((y0 + y) * pw + x0) * 4
        s = y * w * 4
        canvas[d : d + w * 4] = rgba[s : s + w * 4]
    return pw, ph, bytes(canvas)


def load_cairosvg():
    try:
        import cairosvg  # type: ignore

        return cairosvg
    except Exception:
        return None


def cairosvg_render(cs, data, pw, ph, intrinsic):
    """Rasterize with cairosvg, fitted into pw x ph; None on any failure."""
    try:
        if intrinsic:
            w0, h0 = intrinsic
            s = min(pw / w0, ph / h0)
            png = cs.svg2png(bytestring=data, scale=s)
        else:
            png = cs.svg2png(bytestring=data, output_width=pw)
        img = decode_png(png)
        return paste_center(img, pw, ph)
    except Exception:
        return None


def cairosvg_png(cs, data, width):
    try:
        png = cs.svg2png(bytestring=data, output_width=width)
        return png
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

EPILOG = """examples:
  svgview.py logo.svg                     render; saves logo.svg.png beside it
  svgview.py --no-save logo.svg           render only, write no PNG
  svgview.py --png out.png logo.svg       render + save exactly out.png
  svgview.py --bg white icon.svg          composite transparency over white
  svgview.py --renderer sixel *.svg       force sixel output
  svgview.py --engine builtin logo.svg    skip cairosvg, use built-in engine
"""


def _auto_ss(pw, ph, ss):
    """Cap supersampling so the internal buffer stays sane."""
    if pw * ph <= 0:
        return 1
    cap = 4_000_000.0 / (pw * ph)
    return max(1, min(ss, int(math.sqrt(cap)) or 1))


def render_file(args, term, renderer, data, name, status):
    """Render one file's bytes -> (out_bytes, engine_name, warn_or_None)."""
    cols = args.width or term.cols
    if renderer == "blocks":
        pw = max(4, min(cols, 600))
        ph = max(4, (term.rows - 1) * 2)
    elif renderer == "sixel":
        pw = max(4, min(cols * term.cell_w, 1600))
        ph = max(4, min(term.rows * term.cell_h, 1200))
    else:  # kitty scales on the GPU; keep the raster modest
        pw = max(4, min(cols * term.cell_w, 900))
        ph = max(4, min(term.rows * term.cell_h, 700))
        w0, h0 = 300.0, 150.0
        try:
            root = ET.fromstring(data.decode("utf-8", "replace"))
            w0, h0 = intrinsic_size(root)
        except Exception:
            pass
        s = min(pw / w0, ph / h0)
        pw = max(4, int(w0 * s))
        ph = max(4, int(h0 * s))

    cs = status["cairosvg"]
    img = None
    engine = "builtin"
    intrinsic = None
    try:
        root = ET.fromstring(data.decode("utf-8", "replace"))
        intrinsic = intrinsic_size(root)
    except Exception:
        intrinsic = None
    if cs is not None:
        img = cairosvg_render(cs, data, pw, ph, intrinsic)
        if img is not None:
            engine = "cairosvg"
        elif args.engine == "cairosvg":
            return None, "cairosvg", None, 0, 0
    if img is None:
        doc = Doc(data)  # may raise
        items, warn = doc.build(pw, ph)
        img = rasterize(items, pw, ph, _auto_ss(pw, ph, args.ss))
        return (img, engine, warn, pw, ph)

    # cairosvg path: attach a no-op warning collector
    return (img, engine, None, pw, ph)


def emit(renderer, img, term, bg, truecolor):
    """Image -> terminal bytes for the chosen renderer."""
    w, h, rgba = img
    if renderer == "kitty":
        return render_kitty(w, h, rgba, term.cols, term.rows)
    if renderer == "sixel":
        return render_sixel(w, h, composite_rgb(w, h, rgba, bg)) + b"\n"
    return render_blocks(w, h, composite_rgb(w, h, rgba, bg), bg, truecolor).encode("utf-8")


def export_png(args, status, data, name):
    """PNG bytes for auto-save / --png (best engine available)."""
    cs = status["cairosvg"]
    if cs is not None:
        try:
            root = ET.fromstring(data.decode("utf-8", "replace"))
            w0, h0 = intrinsic_size(root)
        except Exception:
            w0, h0 = 300.0, 150.0
        target = args.png_width or (int(w0) if max(w0, h0) <= 512 else max(16, int(w0 * 512 / max(w0, h0))))
        png = cairosvg_png(cs, data, max(1, target))
        if png is not None:
            return png
    doc = Doc(data)
    if args.png_width:
        w0, h0 = intrinsic_size(doc.root)
        tw = args.png_width
        th = max(1, round(h0 * tw / w0)) if w0 else tw
    else:
        w0, h0 = intrinsic_size(doc.root)
        if max(w0, h0) <= 512:
            tw, th = max(1, int(w0)), max(1, int(h0))
        else:
            s = 512.0 / max(w0, h0)
            tw, th = max(1, int(w0 * s)), max(1, int(h0 * s))
    items, _warn = doc.build(tw, th)
    _w, _h, rgba = rasterize(items, tw, th, _auto_ss(tw, th, 2))
    return encode_png(tw, th, rgba)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="svgview.py",
        description="One-shot terminal SVG viewer with PNG auto-save.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("files", nargs="+", metavar="SVG", help="SVG file(s), glob patterns allowed")
    ap.add_argument(
        "--renderer",
        default="auto",
        choices=("auto", "blocks", "halfblock", "sixel", "kitty"),
        help="terminal output backend (default: auto-detect)",
    )
    ap.add_argument(
        "--engine",
        default="auto",
        choices=("auto", "cairosvg", "builtin"),
        help="rendering engine (default: cairosvg if importable)",
    )
    ap.add_argument(
        "--bg",
        default=None,
        metavar="COLOR",
        help="background color for transparency (name or hex); 'none' keeps alpha in saved PNGs only",
    )
    ap.add_argument("--png", metavar="PATH", help="save PNG to exactly this path (single input only)")
    ap.add_argument(
        "--png-width",
        type=int,
        metavar="PX",
        help="width of saved PNGs (default: intrinsic, capped at 512 px on the long side)",
    )
    ap.add_argument("--no-save", action="store_true", help="do not auto-save <file>.svg.png")
    ap.add_argument("--ss", type=int, default=3, metavar="1-5", help="supersampling factor for the built-in engine")
    ap.add_argument("--width", type=int, metavar="COLS", help="maximum terminal columns to use")
    ap.add_argument(
        "--color", default="auto", choices=("auto", "truecolor", "256"), help="color depth for half-block output"
    )
    ap.add_argument("-q", "--quiet", action="store_true", help="suppress status messages on stderr")
    ap.add_argument("--verbose", action="store_true", help="extra diagnostics on stderr")
    ap.add_argument("--version", action="version", version=f"svgview.py {__version__}")
    args = ap.parse_args(argv)

    # expand globs (shell may have done it already)
    files = []
    for pat in args.files:
        if any(ch in pat for ch in "*?["):
            hits = sorted(glob.glob(pat))
            files.extend(hits if hits else [pat])
        else:
            files.append(pat)
    if args.png and len(files) > 1:
        ap.error("--png needs exactly one input file")

    if args.ss < 1 or args.ss > 5:
        ap.error("--ss must be between 1 and 5")

    term = TermInfo()
    renderer = args.renderer
    if renderer == "halfblock":
        renderer = "blocks"
    if renderer == "auto":
        renderer = "kitty" if term.kitty else "sixel" if term.sixel else "blocks"
        if not term.is_tty:
            renderer = "blocks"

    truecolor = term.truecolor
    if args.color == "truecolor":
        truecolor = True
    elif args.color == "256":
        truecolor = False

    bg = term.bg
    keep_alpha = False
    if args.bg:
        if args.bg.lower() == "none":
            keep_alpha = True
        else:
            try:
                c = parse_color(args.bg)
                bg = (c[0], c[1], c[2])
            except ValueError:
                ap.error(f"unknown --bg color {args.bg!r}")

    cs = load_cairosvg() if args.engine in ("auto", "cairosvg") else None
    status = {"cairosvg": cs}

    def note(msg):
        if not args.quiet:
            print(msg, file=sys.stderr)

    failures = 0
    for path in files:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            print(f"svgview: {path}: {e}", file=sys.stderr)
            failures += 1
            continue

        try:
            result = render_file(args, term, renderer, data, path, status)
        except (ET.ParseError, ValueError, PathError) as e:
            print(f"svgview: {path}: cannot parse SVG ({e})", file=sys.stderr)
            failures += 1
            continue
        if result is None:
            print(f"svgview: {path}: cairosvg failed and --engine cairosvg was forced", file=sys.stderr)
            failures += 1
            continue
        img, engine, warn, pw, ph = result
        out = emit(renderer, img, term, bg, truecolor)
        try:
            sys.stdout.buffer.write(out)
            sys.stdout.buffer.flush()
        except BrokenPipeError:
            return 0

        png_path = None
        try:
            if args.png:
                png_path = args.png
            elif not args.no_save:
                png_path = path + ".png"
            if png_path:
                png = export_png(args, status, data, path)
                if args.bg and not keep_alpha:
                    w, h, rgba = decode_png(png)
                    rgb = composite_rgb(w, h, rgba, bg)
                    ba = bytearray(w * h * 4)
                    for i in range(w * h):
                        ba[i * 4 : i * 4 + 3] = rgb[i * 3 : i * 3 + 3]
                        ba[i * 4 + 3] = 255
                    png = encode_png(w, h, bytes(ba))
        #                with open(png_path, "wb") as fh:
        #                    fh.write(png)
        except OSError as e:
            print(f"svgview: {path}: cannot write PNG ({e})", file=sys.stderr)
        except Exception as e:
            print(f"svgview: {path}: PNG export failed ({e})", file=sys.stderr)

        if not args.quiet:
            w, h = img[0], img[1]
            eng = engine if engine == "cairosvg" else "builtin"
            bits = [f"{path}: {w}x{h} px via {renderer} [{eng}]"]
            if png_path:
                try:
                    iw, ih, _ = decode_png(open(png_path, "rb").read())
                    bits.append(f"saved {png_path} ({iw}x{ih})")
                except Exception:
                    bits.append(f"saved {png_path}")
            note(" ".join(bits))
        if warn is not None and not warn.empty:
            note(f"  skipped: {warn.text()} — install cairosvg for full fidelity")
        if args.verbose:
            note(f"  engine={engine} renderer={renderer} truecolor={truecolor} bg={bg} term={term.cols}x{term.rows}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
