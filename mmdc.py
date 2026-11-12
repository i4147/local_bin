#!/data/data/com.termux/files/usr/bin/env python
"""mmdc -- a pure-Python implementation of the mermaid-cli command line tool.

This is a drop-in replacement for the Node.js based `mmdc` command from
@mermaid-js/mermaid-cli (upstream v12.0.0).  It mirrors the upstream CLI
interface and the JS module exports (``run``, ``renderMermaid``, ``cli``,
``error``), but renders Mermaid diagrams with a built-in dependency-free
Python renderer instead of a headless Chromium browser.

Why: a browser (and therefore Puppeteer/Playwright) is not available on many
platforms -- e.g. 32-bit Termux on Android 7 (armv8l).  This module runs
anywhere Python >= 3.12 runs and never launches any external program.

Supported diagram types (common syntax subset):

* ``flowchart`` / ``graph``       (TB/BT/LR/RL, node shapes, edge labels,
                                   subgraphs, classDef/class/style/linkStyle)
* ``sequenceDiagram``             (participants/actors, notes, activations,
                                   autonumber, loop/alt/opt/par/box/rect)
* ``classDiagram``                (members, annotations, relationships)
* ``stateDiagram`` / ``-v2``      (incl. composite states and notes)
* ``erDiagram``                   (entities, attributes, cardinalities)
* ``pie``

Output formats:

* SVG -- pure Python, no third party libraries needed
* PDF -- pure Python (built-in writer, Helvetica base font, Latin-1 text)
* PNG -- needs the optional Pillow library (``pip install pillow``, or
  ``pkg install python-pillow`` on Termux)

Everything (CLI + library API + parsers + layouts + renderers) is packaged
in this single file.  All file handling uses :mod:`pathlib`; batch rendering
of the diagrams found in a Markdown file runs on a thread pool
(``-j/--jobs``), mirroring upstream's parallel jobs option.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

__version__ = "12.0.0"

# --------------------------------------------------------------------------
# constants mirrored from upstream src/index.js
# --------------------------------------------------------------------------

#: Themes accepted by ``-t/--theme`` (upstream v12).
THEMES: tuple[str, ...] = (
    "base",
    "dark",
    "default",
    "forest",
    "neutral",
    "neo",
    "neo-dark",
    "redux",
    "redux-dark",
    "redux-color",
    "redux-dark-color",
    "null",
)

#: PDF paper formats accepted by ``--pdf-paper-format`` (values in points).
PAPER_FORMATS: dict[str, tuple[float, float]] = {
    "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0),
    "Tabloid": (792.0, 1224.0),
    "Ledger": (1224.0, 792.0),
    "A0": (2384.0, 3370.0),
    "A1": (1684.0, 2384.0),
    "A2": (1191.0, 1684.0),
    "A3": (842.0, 1191.0),
    "A4": (595.0, 842.0),
    "A5": (420.0, 595.0),
    "A6": (298.0, 420.0),
}

#: Diagram types this renderer understands.
SUPPORTED_DIAGRAMS: tuple[str, ...] = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "classDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "pie",
)

#: Outer margin (px) kept around every rendered diagram.
DIAGRAM_MARGIN = 10.0


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class MermaidError(Exception):
    """Base class for all mmdc rendering errors."""


class MermaidParseError(MermaidError):
    """Raised when a mermaid definition cannot be parsed."""


class UnsupportedDiagramError(MermaidParseError):
    """Raised for diagram types the pure-Python renderer does not implement."""

    def __init__(self, diagram_type: str) -> None:
        self.diagram_type = diagram_type
        super().__init__(
            f"Diagram type '{diagram_type}' is not supported by the "
            "pure-Python renderer. Supported types: " + ", ".join(SUPPORTED_DIAGRAMS)
        )


# --------------------------------------------------------------------------
# logging (mirrors upstream's chalk-coloured error/warn/info helpers)
# --------------------------------------------------------------------------

_QUIET = False


def _supports_color(stream: Any) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _red(message: str) -> str:
    if _supports_color(sys.stderr):
        return f"\x1b[31m{message}\x1b[0m"
    return message


def _yellow(message: str) -> str:
    if _supports_color(sys.stderr):
        return f"\x1b[33m{message}\x1b[0m"
    return message


def error(message: str) -> None:
    """Print an error to stderr, then exit with code 1 (mirrors upstream)."""
    sys.stderr.write(f"\n{_red(str(message))}\n\n")
    sys.exit(1)


def warn(message: str) -> None:
    """Print a warning to stderr (mirrors upstream)."""
    sys.stderr.write(f"\n{_yellow(str(message))}\n\n")


def info(message: str) -> None:
    """Log to stdout unless quiet mode is active (mirrors upstream)."""
    if not _QUIET:
        sys.stdout.write(f"{message}\n")


def set_quiet(quiet: bool) -> None:
    """Switch the module level quiet flag used by :func:`info`."""
    global _QUIET
    _QUIET = bool(quiet)


# --------------------------------------------------------------------------
# small path helpers (pure pathlib)
# --------------------------------------------------------------------------


def relative_to(target: Path | str, base: Path | str) -> str:
    """Return ``target`` relative to ``base``, using ``..`` when needed."""
    target = Path(target).resolve()
    base = Path(base).resolve()
    try:
        return str(target.relative_to(base))
    except ValueError:
        pass
    base_parts = base.parts
    target_parts = target.parts
    i = 0
    for a, b in zip(base_parts, target_parts):
        if a != b:
            break
        i += 1
    ups = [".."] * (len(base_parts) - i)
    downs = list(target_parts[i:])
    return str(Path(*ups, *downs)) if (ups or downs) else "."


# --------------------------------------------------------------------------
# text metrics (approximate, font-independent measurements used for layout)
# --------------------------------------------------------------------------

#: Line height as a multiple of the font size.
LINE_H = 1.25

_NARROW = frozenset("iljt.,:;'`!|()[]{}/\\ ")
_WIDE = frozenset("mMWw@%&")
_UPPER_DIGIT = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#$+=")


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1100 <= cp <= 0x115F
        or 0x2E80 <= cp <= 0xA4CF
        or 0xAC00 <= cp <= 0xD7A3
        or 0xF900 <= cp <= 0xFAFF
        or 0xFE30 <= cp <= 0xFE4F
        or 0xFF00 <= cp <= 0xFF60
        or 0xFFE0 <= cp <= 0xFFE6
        or 0x20000 <= cp <= 0x3FFFD
    )


def char_width(ch: str, size: float, bold: bool = False) -> float:
    """Approximate the width of ``ch`` rendered at ``size`` pixels."""
    if ch == "\t":
        ch = "    "
    if ch in _NARROW:
        w = 0.30
    elif ch in _WIDE:
        w = 0.86
    elif _is_cjk(ch):
        w = 1.00
    elif ch in _UPPER_DIGIT:
        w = 0.62
    else:
        w = 0.53
    if bold:
        w *= 1.07
    return w * size


def text_width(s: str, size: float, bold: bool = False) -> float:
    """Approximate the pixel width of ``s`` at ``size`` pixels."""
    return sum(char_width(ch, size, bold) for ch in s)


def wrap_text(s: str, size: float, max_width: float, bold: bool = False) -> list[str]:
    """Word-wrap ``s`` so no line exceeds ``max_width`` pixels."""
    out: list[str] = []
    for para in s.split("\n"):
        words = para.split(" ")
        cur = ""
        for word in words:
            candidate = f"{cur} {word}" if cur else word
            if text_width(candidate, size, bold) <= max_width or not cur:
                cur = candidate
            else:
                out.append(cur)
                cur = word
            while text_width(cur, size, bold) > max_width and len(cur) > 1:
                # hard-break words longer than the whole line
                k = len(cur)
                while k > 1 and text_width(cur[:k], size, bold) > max_width:
                    k -= 1
                out.append(cur[:k])
                cur = cur[k:]
        out.append(cur)
    return [line for line in out if line != ""] or [""]


_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)

_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&nbsp;": " ",
}


def unescape_entities(s: str) -> str:
    for k, v in _ENTITIES.items():
        s = s.replace(k, v)
    return s


def split_label_lines(label: str) -> list[str]:
    """Split a node label on ``<br>`` markers and unescape common entities."""
    label = _BR_RE.sub("\n", label)
    return [unescape_entities(line) for line in label.split("\n")]


def fit_label(
    label: str,
    size: float,
    max_width: float,
    bold: bool = False,
) -> list[str]:
    """Split ``label`` on ``<br>`` and word-wrap each part to ``max_width``."""
    lines: list[str] = []
    for raw in split_label_lines(label):
        if raw == "":
            lines.append("")
            continue
        if text_width(raw, size, bold) <= max_width:
            lines.append(raw)
        else:
            lines.extend(wrap_text(raw, size, max_width, bold))
    return lines or [""]


def lines_size(lines: Sequence[str], size: float) -> tuple[float, float]:
    """Return ``(width, height)`` of the rendered line block."""
    w = max((text_width(line, size) for line in lines), default=0.0)
    h = len(lines) * size * LINE_H
    return w, h


# --------------------------------------------------------------------------
# colors
# --------------------------------------------------------------------------

_NAMED_COLORS: dict[str, str] = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#008000",
    "lime": "#00ff00",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "orange": "#ffa500",
    "purple": "#800080",
    "pink": "#ffc0cb",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "maroon": "#800000",
    "olive": "#808000",
    "navy": "#000080",
    "teal": "#008080",
    "aqua": "#00ffff",
    "cyan": "#00ffff",
    "fuchsia": "#ff00ff",
    "magenta": "#ff00ff",
    "brown": "#a52a2a",
    "beige": "#f5f5dc",
    "gold": "#ffd700",
    "indigo": "#4b0082",
    "ivory": "#fffff0",
    "khaki": "#f0e68c",
    "lavender": "#e6e6fa",
    "linen": "#faf0e6",
    "salmon": "#fa8072",
    "sienna": "#a0522d",
    "snow": "#fffafa",
    "tan": "#d2b48c",
    "thistle": "#d8bfd8",
    "tomato": "#ff6347",
    "turquoise": "#40e0d0",
    "violet": "#ee82ee",
    "wheat": "#f5deb3",
    "azure": "#f0ffff",
    "bisque": "#ffe4c4",
    "coral": "#ff7f50",
    "crimson": "#dc143c",
    "darkblue": "#00008b",
    "darkgreen": "#006400",
    "darkred": "#8b0000",
    "dimgray": "#696969",
    "firebrick": "#b22222",
    "gainsboro": "#dcdcdc",
    "goldenrod": "#daa520",
    "hotpink": "#ff69b4",
    "lightblue": "#add8e6",
    "lightgray": "#d3d3d3",
    "lightgreen": "#90ee90",
    "lightyellow": "#ffffe0",
    "limegreen": "#32cd32",
    "midnightblue": "#191970",
    "orangered": "#ff4500",
    "orchid": "#da70d6",
    "plum": "#dda0dd",
    "seagreen": "#2e8b57",
    "skyblue": "#87ceeb",
    "slategray": "#708090",
    "steelblue": "#4682b4",
}

_RGB_RE = re.compile(r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$")


def parse_color(color: str | None) -> tuple[float, float, float, float] | None:
    """Parse a CSS-ish color to ``(r, g, b, a)`` floats in 0..1.

    Returns ``None`` for ``None``/``none``/``transparent``.
    """
    if color is None:
        return None
    s = str(color).strip()
    if s.lower() in ("none", "transparent"):
        return None
    if s.startswith("#"):
        h = s[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        try:
            r = int(h[0:2], 16) / 255.0
            g = int(h[2:4], 16) / 255.0
            b = int(h[4:6], 16) / 255.0
            a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        except ValueError:
            return None
        return (r, g, b, a)
    m = _RGB_RE.match(s)
    if m:
        r = min(1.0, max(0.0, float(m.group(1)) / 255.0))
        g = min(1.0, max(0.0, float(m.group(2)) / 255.0))
        b = min(1.0, max(0.0, float(m.group(3)) / 255.0))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (r, g, b, min(1.0, max(0.0, a)))
    hexv = _NAMED_COLORS.get(s.lower())
    if hexv:
        return parse_color(hexv)
    return None


def normalize_color(color: str | None) -> str | None:
    """Normalize a color to something every backend understands.

    Hex/rgb/rgba/named colors are normalized to ``#rrggbb`` (or kept as
    ``rgba(...)`` when an alpha channel is present).  ``none`` and
    ``transparent`` return ``None``.
    """
    rgba = parse_color(color)
    if rgba is None:
        return None
    r, g, b, a = rgba
    if a < 0.999:
        return f"rgba({round(r * 255)}, {round(g * 255)}, {round(b * 255)}, {round(a, 3)})"
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


# --------------------------------------------------------------------------
# themes (approximate mermaid theme palettes)
# --------------------------------------------------------------------------


@dataclass
class Theme:
    """Resolved palette used by all renderers."""

    name: str = "default"
    background: str = "white"
    font_family: str = '"trebuchet ms", verdana, arial, sans-serif'
    font_size: float = 16.0
    node_fill: str = "#ececff"
    node_stroke: str = "#9370db"
    node_text: str = "#333333"
    node_stroke_width: float = 1.0
    edge_stroke: str = "#333333"
    edge_text: str = "#333333"
    edge_label_bg: str = "#e8e8e8"
    cluster_fill: str = "rgba(236, 236, 255, 0.5)"
    cluster_stroke: str = "#9370db"
    cluster_text: str = "#333333"
    text_color: str = "#333333"
    note_fill: str = "#fff5ad"
    note_stroke: str = "#aaaa33"
    note_text: str = "#333333"
    actor_fill: str = "#ececff"
    actor_stroke: str = "#9370db"
    actor_text: str = "#333333"
    signal_color: str = "#333333"
    signal_text: str = "#333333"
    loop_text: str = "#0000ff"
    activation_fill: str = "#dddddd"
    label_box_fill: str = "#ececff"
    label_box_stroke: str = "#9370db"
    pie_colors: tuple[str, ...] = (
        "#6f42c1",
        "#00cfce",
        "#fa8f16",
        "#5aa469",
        "#967aa8",
        "#f9db6d",
        "#7fb7be",
        "#d7263d",
        "#a3d9ff",
        "#865d36",
        "#c3f584",
        "#9e0031",
    )


#: Per-theme overrides on top of :class:`Theme` defaults.
_THEME_OVERRIDES: dict[str, dict[str, Any]] = {
    "default": {},
    "null": {},  # mermaid's "null" theme falls back to built-in defaults
    "neutral": {
        "node_fill": "#f6f6f6",
        "node_stroke": "#999999",
        "cluster_fill": "rgba(246, 246, 246, 0.5)",
        "cluster_stroke": "#999999",
        "actor_fill": "#f6f6f6",
        "actor_stroke": "#999999",
        "label_box_fill": "#f6f6f6",
    },
    "base": {
        "node_fill": "#efefef",
        "node_stroke": "#9a9a9a",
        "cluster_fill": "rgba(239, 239, 239, 0.5)",
        "cluster_stroke": "#9a9a9a",
        "actor_fill": "#efefef",
        "actor_stroke": "#9a9a9a",
        "edge_label_bg": "#ffffff",
    },
    "dark": {
        "background": "#14161f",
        "node_fill": "#1f2024",
        "node_stroke": "#9370db",
        "node_text": "#e8e8f0",
        "edge_stroke": "#c8c8d4",
        "edge_text": "#e8e8f0",
        "edge_label_bg": "#14161f",
        "cluster_fill": "rgba(31, 32, 36, 0.6)",
        "cluster_stroke": "#6f6f9d",
        "cluster_text": "#e8e8f0",
        "text_color": "#e8e8f0",
        "note_fill": "#4a4a33",
        "note_text": "#e8e8f0",
        "note_stroke": "#777755",
        "actor_fill": "#1f2024",
        "actor_stroke": "#9370db",
        "actor_text": "#e8e8f0",
        "signal_color": "#c8c8d4",
        "signal_text": "#e8e8f0",
        "loop_text": "#9db4ff",
        "activation_fill": "#3a3a44",
        "label_box_fill": "#1f2024",
        "label_box_stroke": "#6f6f9d",
    },
    "forest": {
        "node_fill": "#cde498",
        "node_stroke": "#13540c",
        "node_text": "#1f3905",
        "edge_stroke": "#576d41",
        "edge_text": "#1f3905",
        "edge_label_bg": "#ffffff",
        "cluster_fill": "rgba(205, 228, 152, 0.5)",
        "cluster_stroke": "#62933f",
        "cluster_text": "#1f3905",
        "text_color": "#1f3905",
        "note_fill": "#d9e8b5",
        "note_stroke": "#8a9a5b",
        "actor_fill": "#cde498",
        "actor_stroke": "#13540c",
        "actor_text": "#1f3905",
        "signal_color": "#576d41",
        "signal_text": "#1f3905",
        "loop_text": "#13540c",
        "label_box_fill": "#cde498",
        "label_box_stroke": "#62933f",
    },
    "neo": {
        "node_fill": "#eef2ff",
        "node_stroke": "#3b82f6",
        "node_text": "#1e293b",
        "edge_stroke": "#64748b",
        "edge_text": "#1e293b",
        "cluster_fill": "rgba(238, 242, 255, 0.5)",
        "cluster_stroke": "#93a5f0",
        "cluster_text": "#1e293b",
        "text_color": "#1e293b",
        "actor_fill": "#eef2ff",
        "actor_stroke": "#3b82f6",
        "signal_color": "#64748b",
        "loop_text": "#2563eb",
        "label_box_fill": "#eef2ff",
        "label_box_stroke": "#3b82f6",
    },
    "neo-dark": {
        "background": "#0f172a",
        "node_fill": "#1e293b",
        "node_stroke": "#38bdf8",
        "node_text": "#e2e8f0",
        "edge_stroke": "#94a3b8",
        "edge_text": "#e2e8f0",
        "edge_label_bg": "#0f172a",
        "cluster_fill": "rgba(30, 41, 59, 0.6)",
        "cluster_stroke": "#38bdf8",
        "cluster_text": "#e2e8f0",
        "text_color": "#e2e8f0",
        "actor_fill": "#1e293b",
        "actor_stroke": "#38bdf8",
        "actor_text": "#e2e8f0",
        "signal_color": "#94a3b8",
        "signal_text": "#e2e8f0",
        "loop_text": "#7dd3fc",
        "activation_fill": "#334155",
        "label_box_fill": "#1e293b",
        "label_box_stroke": "#38bdf8",
    },
    "redux": {
        "node_fill": "#f3e8ff",
        "node_stroke": "#764abc",
        "node_text": "#2a1a4a",
        "edge_stroke": "#8a7fa8",
        "edge_text": "#2a1a4a",
        "cluster_fill": "rgba(243, 232, 255, 0.5)",
        "cluster_stroke": "#a98bd4",
        "cluster_text": "#2a1a4a",
        "text_color": "#2a1a4a",
        "actor_fill": "#f3e8ff",
        "actor_stroke": "#764abc",
        "actor_text": "#2a1a4a",
        "signal_color": "#8a7fa8",
        "loop_text": "#764abc",
        "label_box_fill": "#f3e8ff",
        "label_box_stroke": "#764abc",
    },
    "redux-dark": {
        "background": "#1b1b23",
        "node_fill": "#2d2d44",
        "node_stroke": "#b57bee",
        "node_text": "#efe6ff",
        "edge_stroke": "#8f8fb0",
        "edge_text": "#efe6ff",
        "edge_label_bg": "#1b1b23",
        "cluster_fill": "rgba(45, 45, 68, 0.6)",
        "cluster_stroke": "#b57bee",
        "cluster_text": "#efe6ff",
        "text_color": "#efe6ff",
        "actor_fill": "#2d2d44",
        "actor_stroke": "#b57bee",
        "actor_text": "#efe6ff",
        "signal_color": "#8f8fb0",
        "signal_text": "#efe6ff",
        "loop_text": "#c9a4ff",
        "activation_fill": "#3d3d58",
        "label_box_fill": "#2d2d44",
        "label_box_stroke": "#b57bee",
    },
    "redux-color": {
        "node_fill": "#ede7f6",
        "node_stroke": "#5e35b1",
        "node_text": "#311b92",
        "edge_stroke": "#5e35b1",
        "cluster_fill": "rgba(237, 230, 246, 0.5)",
        "cluster_stroke": "#7e57c2",
        "actor_fill": "#ede7f6",
        "actor_stroke": "#5e35b1",
        "label_box_fill": "#ede7f6",
        "label_box_stroke": "#5e35b1",
    },
    "redux-dark-color": {
        "background": "#17151e",
        "node_fill": "#292536",
        "node_stroke": "#7e57c2",
        "node_text": "#ede7f6",
        "edge_stroke": "#9f94c9",
        "edge_text": "#ede7f6",
        "edge_label_bg": "#17151e",
        "cluster_fill": "rgba(41, 37, 54, 0.6)",
        "cluster_stroke": "#7e57c2",
        "cluster_text": "#ede7f6",
        "text_color": "#ede7f6",
        "actor_fill": "#292536",
        "actor_stroke": "#7e57c2",
        "actor_text": "#ede7f6",
        "signal_color": "#9f94c9",
        "signal_text": "#ede7f6",
        "activation_fill": "#3a3450",
        "label_box_fill": "#292536",
        "label_box_stroke": "#7e57c2",
    },
}

#: ``themeVariables`` keys (camelCase, like mermaid) mapped to Theme fields.
_THEME_VARIABLE_MAP: dict[str, str] = {
    "background": "background",
    "fontFamily": "font_family",
    "fontSize": "font_size",
    "primaryColor": "node_fill",
    "primaryBorderColor": "node_stroke",
    "primaryTextColor": "node_text",
    "lineColor": "edge_stroke",
    "edgeLabelBackground": "edge_label_bg",
    "clusterBkg": "cluster_fill",
    "clusterBorder": "cluster_stroke",
    "textColor": "text_color",
    "noteBkgColor": "note_fill",
    "noteBorderColor": "note_stroke",
    "noteTextColor": "note_text",
    "actorBkg": "actor_fill",
    "actorBorder": "actor_stroke",
    "actorTextColor": "actor_text",
    "signalColor": "signal_color",
    "signalTextColor": "signal_text",
    "loopTextColor": "loop_text",
    "labelBoxBkgColor": "label_box_fill",
    "labelBoxBorderColor": "label_box_stroke",
    "activationBkgColor": "activation_fill",
}


def get_theme(name: str | None, theme_variables: dict[str, Any] | None = None) -> Theme:
    """Build a :class:`Theme` from a theme name and ``themeVariables`` overrides."""
    theme = Theme(name=name or "default")
    for key, value in _THEME_OVERRIDES.get(theme.name, {}).items():
        setattr(theme, key, value)
    for key, value in (theme_variables or {}).items():
        attr = _THEME_VARIABLE_MAP.get(key)
        if attr and value is not None:
            setattr(theme, attr, value)
    if "pie1..12" in (theme_variables or {}):
        pass  # mermaid allows overriding pie palette; keep built-in
    return theme


# --------------------------------------------------------------------------
# drawing model: backend-agnostic primitives
# --------------------------------------------------------------------------

type Pt = tuple[float, float]

#: Shape names understood by :func:`shape_prims`.
NODE_SHAPES = (
    "rect",
    "rounded",
    "stadium",
    "circle",
    "double_circle",
    "rhombus",
    "hexagon",
    "para",
    "para_alt",
    "trapezoid",
    "trapezoid_alt",
    "cylinder",
    "subroutine",
    "asym",
)


@dataclass
class Prim:
    """A single drawing primitive shared by the SVG/PNG/PDF backends."""

    kind: str  # rect|ellipse|polygon|polyline|text
    x: float = 0.0  # rect: left, ellipse: cx, text: anchor x
    y: float = 0.0  # rect: top, ellipse: cy, text: top
    w: float = 0.0
    h: float = 0.0
    rx: float = 0.0  # ellipse x-radius / rect corner radius
    ry: float = 0.0  # ellipse y-radius
    pts: tuple[Pt, ...] = ()  # polygon / polyline points
    fill: str | None = None
    stroke: str | None = None
    stroke_width: float = 1.0
    dash: tuple[float, ...] | None = None
    opacity: float = 1.0
    lines: tuple[str, ...] = ()  # text lines
    size: float = 16.0  # font size
    anchor: str = "middle"  # text-anchor
    bold: bool = False
    color: str = "#333333"
    cls: str | None = None  # CSS class (mermaid-compatible)
    eid: str | None = None  # element id


@dataclass
class Canvas:
    """A fully laid out diagram as a list of primitives."""

    w: float
    h: float
    prims: list[Prim] = field(default_factory=list)


def prim_bbox(prims: Iterable[Prim]) -> tuple[float, float, float, float]:
    """Bounding box ``(x, y, w, h)`` of the given primitives."""
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for p in prims:
        if p.kind == "rect":
            x0, y0, x1, y1 = p.x, p.y, p.x + p.w, p.y + p.h
        elif p.kind == "ellipse":
            x0, y0 = p.x - p.rx, p.y - p.ry
            x1, y1 = p.x + p.rx, p.y + p.ry
        elif p.kind in ("polygon", "polyline"):
            if not p.pts:
                continue
            xs = [q[0] for q in p.pts]
            ys = [q[1] for q in p.pts]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        elif p.kind == "text":
            tw = max((text_width(line, p.size, p.bold) for line in p.lines), default=0.0)
            th = len(p.lines) * p.size * LINE_H
            if p.anchor == "middle":
                x0, x1 = p.x - tw / 2, p.x + tw / 2
            elif p.anchor == "end":
                x0, x1 = p.x - tw, p.x
            else:
                x0, x1 = p.x, p.x + tw
            y0, y1 = p.y, p.y + th + p.size * 0.22  # descender allowance
        else:
            continue
        min_x, min_y = min(min_x, x0), min(min_y, y0)
        max_x, max_y = max(max_x, x1), max(max_y, y1)
    if min_x is math.inf:
        return (0.0, 0.0, 0.0, 0.0)
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def shift_prims(prims: Iterable[Prim], dx: float, dy: float) -> list[Prim]:
    """Return a copy of ``prims`` translated by ``(dx, dy)``."""
    out = []
    for p in prims:
        q = replace(p)
        q.x += dx
        q.y += dy
        if p.kind in ("polygon", "polyline"):
            q.pts = tuple((x + dx, y + dy) for (x, y) in p.pts)
        out.append(q)
    return out


def with_margin(canvas: Canvas, margin: float) -> Canvas:
    """Expand a canvas by a uniform margin around its content."""
    return Canvas(
        w=canvas.w + 2 * margin,
        h=canvas.h + 2 * margin,
        prims=shift_prims(canvas.prims, margin, margin),
    )


# --------------------------------------------------------------------------
# text helper
# --------------------------------------------------------------------------


def center_text(
    cx: float,
    cy: float,
    lines: Sequence[str],
    size: float,
    color: str,
    bold: bool = False,
    cls: str = "label",
    eid: str | None = None,
) -> Prim:
    """A centered text block whose middle is ``(cx, cy)``."""
    total_h = len(lines) * size * LINE_H
    return Prim(
        kind="text",
        x=cx,
        y=cy - total_h / 2,
        lines=tuple(lines),
        size=size,
        anchor="middle",
        color=color,
        bold=bold,
        cls=cls,
        eid=eid,
    )


def left_text(
    x: float,
    y_top: float,
    lines: Sequence[str],
    size: float,
    color: str,
    bold: bool = False,
    cls: str = "label",
) -> Prim:
    """A left-aligned text block starting at ``(x, y_top)``."""
    return Prim(
        kind="text",
        x=x,
        y=y_top,
        lines=tuple(lines),
        size=size,
        anchor="start",
        color=color,
        bold=bold,
        cls=cls,
    )


# --------------------------------------------------------------------------
# node shapes
# --------------------------------------------------------------------------


def shape_prims(
    shape: str,
    cx: float,
    cy: float,
    w: float,
    h: float,
    fill: str | None,
    stroke: str | None,
    stroke_width: float = 1.0,
    dash: tuple[float, ...] | None = None,
    cls: str = "node",
    eid: str | None = None,
) -> list[Prim]:
    """Build the outline primitives for a node of the given shape."""
    x, y = cx - w / 2, cy - h / 2
    kw: dict[str, Any] = dict(
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
        dash=dash,
        cls=cls,
        eid=eid,
    )

    def rect(r: float) -> list[Prim]:
        return [Prim(kind="rect", x=x, y=y, w=w, h=h, rx=r, **kw)]

    if shape == "rect":
        return rect(0.0)
    if shape == "rounded":
        return rect(min(10.0, h / 2))
    if shape == "stadium":
        return rect(h / 2)
    if shape == "circle":
        return [Prim(kind="ellipse", x=cx, y=cy, rx=w / 2, ry=h / 2, **kw)]
    if shape == "double_circle":
        return [
            Prim(kind="ellipse", x=cx, y=cy, rx=w / 2, ry=h / 2, **kw),
            Prim(
                kind="ellipse",
                x=cx,
                y=cy,
                rx=max(1.0, w / 2 - 5),
                ry=max(1.0, h / 2 - 5),
                fill=None,
                stroke=stroke,
                stroke_width=stroke_width,
                dash=dash,
                cls=cls,
            ),
        ]
    if shape == "rhombus":
        pts = ((cx, y), (x + w, cy), (cx, y + h), (x, cy))
        return [Prim(kind="polygon", pts=pts, **kw)]
    if shape == "hexagon":
        ins = min(18.0, w * 0.18)
        pts = (
            (x + ins, y),
            (x + w - ins, y),
            (x + w, cy),
            (x + w - ins, y + h),
            (x + ins, y + h),
            (x, cy),
        )
        return [Prim(kind="polygon", pts=pts, **kw)]
    if shape in ("para", "para_alt"):
        ins = min(22.0, w * 0.22)
        if shape == "para":
            pts = ((x + ins, y), (x + w, y), (x + w - ins, y + h), (x, y + h))
        else:
            pts = ((x, y), (x + w - ins, y), (x + w, y + h), (x + ins, y + h))
        return [Prim(kind="polygon", pts=pts, **kw)]
    if shape == "trapezoid":
        ins = min(20.0, w * 0.2)
        pts = ((x + ins, y), (x + w - ins, y), (x + w, y + h), (x, y + h))
        return [Prim(kind="polygon", pts=pts, **kw)]
    if shape == "trapezoid_alt":
        ins = min(20.0, w * 0.2)
        pts = ((x, y), (x + w, y), (x + w - ins, y + h), (x + ins, y + h))
        return [Prim(kind="polygon", pts=pts, **kw)]
    if shape == "cylinder":
        ry = min(8.0, h * 0.15)
        body = Prim(
            kind="rect",
            x=x,
            y=y + ry,
            w=w,
            h=max(1.0, h - 2 * ry),
            fill=fill,
            stroke=None,
            cls=cls,
        )
        left = Prim(
            kind="polyline",
            pts=((x, y + ry), (x, y + h - ry)),
            stroke=stroke,
            stroke_width=stroke_width,
            cls=cls,
        )
        right = Prim(
            kind="polyline",
            pts=((x + w, y + ry), (x + w, y + h - ry)),
            stroke=stroke,
            stroke_width=stroke_width,
            cls=cls,
        )
        top = Prim(kind="ellipse", x=cx, y=y + ry, rx=w / 2, ry=ry, **kw)
        bottom = Prim(kind="ellipse", x=cx, y=y + h - ry, rx=w / 2, ry=ry, **kw)
        return [body, left, right, top, bottom]
    if shape == "subroutine":
        ins = min(8.0, w * 0.12)
        return [
            Prim(kind="rect", x=x, y=y, w=w, h=h, rx=0.0, **kw),
            Prim(
                kind="polyline", pts=((x + ins, y), (x + ins, y + h)), stroke=stroke, stroke_width=stroke_width, cls=cls
            ),
            Prim(
                kind="polyline",
                pts=((x + w - ins, y), (x + w - ins, y + h)),
                stroke=stroke,
                stroke_width=stroke_width,
                cls=cls,
            ),
        ]
    if shape == "asym":
        ins = min(24.0, w * 0.3)
        pts = ((x, y), (x + w - ins, y), (x + w, cy), (x + w - ins, y + h), (x, y + h))
        return [Prim(kind="polygon", pts=pts, **kw)]
    # unknown shape: fall back to a rectangle
    return rect(0.0)


# --------------------------------------------------------------------------
# arrow heads
# --------------------------------------------------------------------------


def arrowhead(
    p_from: Pt,
    p_to: Pt,
    kind: str,
    color: str,
    size: float = 10.5,
) -> Prim | None:
    """Build an arrow head primitive at ``p_to`` pointing from ``p_from``.

    ``kind`` is one of: arrow, arrow_open, triangle_hollow, diamond_fill,
    diamond_hollow, circle_fill, circle_hollow, dot, none.  (Cross "x" heads
    are built with :func:`crosshead` instead.)
    """
    if kind in ("", "none", None):
        return None
    dx = p_to[0] - p_from[0]
    dy = p_to[1] - p_from[1]
    length = math.hypot(dx, dy)
    if length < 0.01:
        dx, dy, length = 0.0, 1.0, 1.0
    ux, uy = dx / length, dy / length
    px, py = -uy, ux  # unit perpendicular

    if kind in ("arrow", "arrow_open"):
        L, W = size, size * 0.62
        base = (p_to[0] - ux * L, p_to[1] - uy * L)
        w1 = (base[0] + px * W, base[1] + py * W)
        w2 = (base[0] - px * W, base[1] - py * W)
        if kind == "arrow":
            return Prim(
                kind="polygon",
                pts=(w1, p_to, w2),
                fill=color,
                stroke=color,
                stroke_width=1.0,
                cls="arrowHead",
            )
        return Prim(
            kind="polyline",
            pts=(w1, p_to, w2),
            stroke=color,
            stroke_width=1.6,
            cls="arrowHead",
        )
    if kind == "triangle_hollow":
        L, W = size * 1.15, size * 0.72
        base = (p_to[0] - ux * L, p_to[1] - uy * L)
        w1 = (base[0] + px * W, base[1] + py * W)
        w2 = (base[0] - px * W, base[1] - py * W)
        return Prim(
            kind="polygon",
            pts=(w1, p_to, w2),
            fill="white",
            stroke=color,
            stroke_width=1.4,
            cls="arrowHead",
        )
    if kind in ("diamond_fill", "diamond_hollow"):
        L, W = size * 1.5, size * 0.75
        a = (p_to[0] - ux * L, p_to[1] - uy * L)
        b = (p_to[0] - ux * L * 0.45 + px * W, p_to[1] - uy * L * 0.45 + py * W)
        c = (p_to[0] - ux * L * 0.45 - px * W, p_to[1] - uy * L * 0.45 - py * W)
        return Prim(
            kind="polygon",
            pts=(a, b, p_to, c),
            fill=color if kind == "diamond_fill" else "white",
            stroke=color,
            stroke_width=1.3,
            cls="arrowHead",
        )
    if kind in ("circle_fill", "circle_hollow", "dot"):
        r = size * 0.4
        center = (p_to[0] - ux * r, p_to[1] - uy * r)
        return Prim(
            kind="ellipse",
            x=center[0],
            y=center[1],
            rx=r,
            ry=r,
            fill=color if kind != "circle_hollow" else "white",
            stroke=color,
            stroke_width=1.2,
            cls="arrowHead",
        )
    return None


def crosshead(p_from: Pt, p_to: Pt, color: str, size: float = 10.5) -> list[Prim]:
    """An 'x' arrow head made of two crossing lines (flowchart ``--x``)."""
    dx = p_to[0] - p_from[0]
    dy = p_to[1] - p_from[1]
    length = math.hypot(dx, dy)
    if length < 0.01:
        dx, dy, length = 0.0, 1.0, 1.0
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    L, W = size * 0.5, size * 0.5
    c = (p_to[0] - ux * L, p_to[1] - uy * L)
    a = (c[0] + px * W, c[1] + py * W)
    b = (c[0] - px * W, c[1] - py * W)
    d = (c[0] + ux * W, c[1] + uy * W)
    e = (c[0] - ux * W, c[1] - uy * W)
    return [
        Prim(kind="polyline", pts=(a, b), stroke=color, stroke_width=1.8),
        Prim(kind="polyline", pts=(d, e), stroke=color, stroke_width=1.8),
    ]


# --------------------------------------------------------------------------
# direction transform (layout is computed top-down, then rotated)
# --------------------------------------------------------------------------


def transform_prims(prims: Iterable[Prim], direction: str, w: float, h: float) -> list[Prim]:
    """Rotate/flip prims laid out top-down into the requested direction.

    ``direction`` is one of TB, TD, BT, LR, RL.  ``w``/``h`` are the size of
    the top-down layout; the result has swapped size for LR/RL.
    """
    direction = (direction or "TB").upper()
    if direction in ("TB", "TD"):
        return list(prims)
    out: list[Prim] = []
    for p in prims:
        q = replace(p)
        if direction == "BT":
            if p.kind == "rect":
                q.y = h - p.y - p.h
            elif p.kind == "ellipse":
                q.y = h - p.y
            elif p.kind in ("polygon", "polyline"):
                q.pts = tuple((x, h - y) for (x, y) in p.pts)
            elif p.kind == "text":
                th = len(p.lines) * p.size * LINE_H
                q.y = h - p.y - th
        elif direction in ("LR", "RL"):
            if p.kind == "rect":
                nx, ny = p.y, p.x
                nw, nh = p.h, p.w
                if direction == "RL":
                    nx = h - nx - nw
                q.x, q.y, q.w, q.h = nx, ny, nw, nh
            elif p.kind == "ellipse":
                nx, ny = p.y, p.x
                nrx, nry = p.ry, p.rx
                if direction == "RL":
                    nx = h - nx
                q.x, q.y, q.rx, q.ry = nx, ny, nrx, nry
            elif p.kind in ("polygon", "polyline"):
                pts = [(y, x) for (x, y) in p.pts]
                if direction == "RL":
                    pts = [(h - x, y) for (x, y) in pts]
                q.pts = tuple(pts)
            elif p.kind == "text":
                tw = max((text_width(line, p.size, p.bold) for line in p.lines), default=0.0)
                th = len(p.lines) * p.size * LINE_H
                if p.anchor == "middle":
                    bx0 = p.x - tw / 2
                elif p.anchor == "end":
                    bx0 = p.x - tw
                else:
                    bx0 = p.x
                bcx, bcy = bx0 + tw / 2, p.y + th / 2
                if direction == "BT":
                    ncx, ncy = bcx, h - bcy
                elif direction == "LR":
                    ncx, ncy = bcy, bcx
                else:  # RL
                    ncx, ncy = h - bcy, bcx
                if p.anchor == "middle":
                    q.x = ncx
                elif p.anchor == "end":
                    q.x = ncx + tw / 2
                else:
                    q.x = ncx - tw / 2
                q.y = ncy - th / 2
        out.append(q)
    return out


def transformed_size(direction: str, w: float, h: float) -> tuple[float, float]:
    direction = (direction or "TB").upper()
    if direction in ("LR", "RL"):
        return (h, w)
    return (w, h)


# --------------------------------------------------------------------------
# SVG backend
# --------------------------------------------------------------------------


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _num(v: float) -> str:
    s = f"{v:.2f}"
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _pts_attr(pts: Sequence[Pt]) -> str:
    return " ".join(f"{_num(x)},{_num(y)}" for (x, y) in pts)


def _prim_svg(p: Prim) -> str:
    attrs: list[str] = []
    if p.cls:
        attrs.append(f'class="{_xml_escape(p.cls)}"')
    if p.eid:
        attrs.append(f'id="{_xml_escape(p.eid)}"')
    if p.opacity < 0.999:
        attrs.append(f'opacity="{p.opacity:.3f}"')

    if p.kind == "rect":
        attrs.append(f'x="{_num(p.x)}"')
        attrs.append(f'y="{_num(p.y)}"')
        attrs.append(f'width="{_num(max(p.w, 0))}"')
        attrs.append(f'height="{_num(max(p.h, 0))}"')
        if p.rx > 0:
            attrs.append(f'rx="{_num(min(p.rx, p.h / 2 if p.h else p.rx))}"')
        if p.fill:
            attrs.append(f'fill="{_xml_escape(p.fill)}"')
        else:
            attrs.append('fill="none"')
        if p.stroke:
            attrs.append(f'stroke="{_xml_escape(p.stroke)}"')
            attrs.append(f'stroke-width="{_num(p.stroke_width)}"')
            if p.dash:
                attrs.append('stroke-dasharray="' + " ".join(_num(d) for d in p.dash) + '"')
        return f"<rect {' '.join(attrs)} />"

    if p.kind == "ellipse":
        attrs.append(f'cx="{_num(p.x)}"')
        attrs.append(f'cy="{_num(p.y)}"')
        attrs.append(f'rx="{_num(max(p.rx, 0))}"')
        attrs.append(f'ry="{_num(max(p.ry, 0))}"')
        if p.fill:
            attrs.append(f'fill="{_xml_escape(p.fill)}"')
        else:
            attrs.append('fill="none"')
        if p.stroke:
            attrs.append(f'stroke="{_xml_escape(p.stroke)}"')
            attrs.append(f'stroke-width="{_num(p.stroke_width)}"')
            if p.dash:
                attrs.append('stroke-dasharray="' + " ".join(_num(d) for d in p.dash) + '"')
        return f"<ellipse {' '.join(attrs)} />"

    if p.kind == "polygon":
        attrs.append(f'points="{_pts_attr(p.pts)}"')
        if p.fill:
            attrs.append(f'fill="{_xml_escape(p.fill)}"')
        else:
            attrs.append('fill="none"')
        if p.stroke:
            attrs.append(f'stroke="{_xml_escape(p.stroke)}"')
            attrs.append(f'stroke-width="{_num(p.stroke_width)}"')
            if p.dash:
                attrs.append('stroke-dasharray="' + " ".join(_num(d) for d in p.dash) + '"')
        return f"<polygon {' '.join(attrs)} />"

    if p.kind == "polyline":
        attrs.append(f'points="{_pts_attr(p.pts)}"')
        attrs.append('fill="none"')
        if p.stroke:
            attrs.append(f'stroke="{_xml_escape(p.stroke)}"')
            attrs.append(f'stroke-width="{_num(p.stroke_width)}"')
        attrs.append('stroke-linejoin="round"')
        attrs.append('stroke-linecap="round"')
        if p.dash:
            attrs.append('stroke-dasharray="' + " ".join(_num(d) for d in p.dash) + '"')
        return f"<polyline {' '.join(attrs)} />"

    if p.kind == "text":
        lh = p.size * LINE_H
        attrs.append(f'x="{_num(p.x)}"')
        attrs.append(f'y="{_num(p.y + 0.8 * lh)}"')
        attrs.append(f'text-anchor="{p.anchor}"')
        attrs.append(f'font-size="{_num(p.size)}"')
        attrs.append(f'fill="{_xml_escape(p.color)}"')
        if p.bold:
            attrs.append('font-weight="bold"')
        body = []
        for i, line in enumerate(p.lines):
            if i == 0:
                body.append(f'<tspan x="{_num(p.x)}" y="{_num(p.y + 0.8 * lh)}">{_xml_escape(line)}</tspan>')
            else:
                body.append(f'<tspan x="{_num(p.x)}" dy="{_num(lh)}">{_xml_escape(line)}</tspan>')
        return f"<text {' '.join(attrs)}>{''.join(body)}</text>"

    return ""


def canvas_to_svg(
    canvas: Canvas,
    *,
    background: str | None = "white",
    svg_id: str = "my-svg",
    css: str | None = None,
    font_family: str = '"trebuchet ms", verdana, arial, sans-serif',
    size: int | None = None,
    title: str | None = None,
    desc: str | None = None,
) -> str:
    """Serialize a :class:`Canvas` to an SVG string."""
    W, H = canvas.w, canvas.h
    attrs = [
        'xmlns="http://www.w3.org/2000/svg"',
        f'width="{_num(W)}"',
    ]
    if not size:
        attrs.append(f'height="{_num(H)}"')
    attrs.append(f'viewBox="0 0 {_num(W)} {_num(H)}"')
    style: list[str] = []
    if background:
        style.append(f"background-color: {background}")
    if size:
        style.append(f"max-width: {size}px")
        style.append(f"max-height: {size}px")
    if style:
        attrs.append(f'style="{"; ".join(style)}"')
    attrs.append(f'font-family="{_xml_escape(font_family)}"')
    attrs.append(f'id="{_xml_escape(svg_id)}"')

    out = ['<?xml version="1.0" encoding="UTF-8"?>', f"<svg {' '.join(attrs)}>"]
    if title:
        out.append(f"<title>{_xml_escape(title)}</title>")
    if desc:
        out.append(f"<desc>{_xml_escape(desc)}</desc>")
    if css:
        safe_css = css.replace("]]>", "]]]]><![CDATA[>")
        out.append(f"<style><![CDATA[{safe_css}]]></style>")
    out.extend(_prim_svg(p) for p in canvas.prims)
    out.append("</svg>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# PDF backend (a minimal, dependency-free PDF 1.4 writer)
# --------------------------------------------------------------------------

_KAPPA = 0.5522847498307936


def _pdf_escape_text(s: str) -> str:
    out = []
    for ch in s:
        if ch in ("\\", "(", ")"):
            out.append("\\" + ch)
        else:
            try:
                ch.encode("cp1252")
                out.append(ch)
            except UnicodeEncodeError:
                out.append("?")
    return "".join(out)


def _pdf_color_ops(color: str | None, alpha: float, gstates: dict[float, str]) -> list[str]:
    """Return the ops selecting fill/stroke colour and alpha helpers."""
    ops: list[str] = []
    rgba = parse_color(color)
    if rgba is not None:
        r, g, b = rgba[0], rgba[1], rgba[2]
        ops.append(f"{r:.4f} {g:.4f} {b:.4f} rg")
        ops.append(f"{r:.4f} {g:.4f} {b:.4f} RG")
    if alpha < 0.999:
        key = round(alpha, 3)
        name = gstates.setdefault(key, f"GS{len(gstates)}")
        ops.append(f"/{name} gs")
    return ops


def _rounded_rect_ops(x: float, y: float, w: float, h: float, r: float) -> list[str]:
    r = max(0.0, min(r, min(w, h) / 2))
    if r <= 0.01:
        return [f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re"]
    k = r * _KAPPA
    return [
        f"{x + r:.2f} {y:.2f} m",
        f"{x + w - r:.2f} {y:.2f} l",
        f"{x + w - r + k:.2f} {y:.2f} {x + w:.2f} {y + r - k:.2f} {x + w:.2f} {y + r:.2f} c",
        f"{x + w:.2f} {y + h - r:.2f} l",
        f"{x + w:.2f} {y + h - r + k:.2f} {x + w - r + k:.2f} {y + h:.2f} {x + w - r:.2f} {y + h:.2f} c",
        f"{x + r:.2f} {y + h:.2f} l",
        f"{x + r - k:.2f} {y + h:.2f} {x:.2f} {y + h - r + k:.2f} {x:.2f} {y + h - r:.2f} c",
        f"{x:.2f} {y + r:.2f} l",
        f"{x:.2f} {y + r - k:.2f} {x + r - k:.2f} {y:.2f} {x + r:.2f} {y:.2f} c",
        "h",
    ]


def _ellipse_ops(cx: float, cy: float, rx: float, ry: float) -> list[str]:
    kx, ky = rx * _KAPPA, ry * _KAPPA
    return [
        f"{cx + rx:.2f} {cy:.2f} m",
        f"{cx + rx:.2f} {cy + ky:.2f} {cx + kx:.2f} {cy + ry:.2f} {cx:.2f} {cy + ry:.2f} c",
        f"{cx - kx:.2f} {cy + ry:.2f} {cx - rx:.2f} {cy + ky:.2f} {cx - rx:.2f} {cy:.2f} c",
        f"{cx - rx:.2f} {cy - ky:.2f} {cx - kx:.2f} {cy - ry:.2f} {cx:.2f} {cy - ry:.2f} c",
        f"{cx + kx:.2f} {cy - ry:.2f} {cx + rx:.2f} {cy - ky:.2f} {cx + rx:.2f} {cy:.2f} c",
    ]


def _paint_op(prim: Prim) -> str:
    """Choose the PDF painting operator for a path prim."""
    if prim.fill and prim.stroke:
        return "B"
    if prim.fill:
        return "f"
    return "S"


def canvas_to_pdf(
    canvas: Canvas,
    *,
    background: str | None = "white",
    paper: str | None = None,
    size: int | None = None,
) -> bytes:
    """Serialize a :class:`Canvas` to a single-page PDF file."""
    import zlib

    px2pt = 0.75  # CSS px (1/96 in) to PDF points (1/72 in)
    eff = min(1.0, size / max(canvas.w, canvas.h)) if size else 1.0
    cw = canvas.w * eff
    ch = canvas.h * eff

    if paper:
        pw, ph = PAPER_FORMATS[paper]
        s = min(pw / cw, ph / ch, 1.0) if (cw > 0 and ch > 0) else 1.0
        scale = eff * s * px2pt
        page_w, page_h = pw, ph
        off_x = (pw - canvas.w * scale) / 2
        off_y = (ph - canvas.h * scale) / 2
    else:
        scale = eff * px2pt
        page_w = max(1.0, canvas.w * scale)
        page_h = max(1.0, canvas.h * scale)
        off_x = off_y = 0.0

    def X(x: float) -> float:
        return off_x + x * scale

    def Y(y: float) -> float:
        return page_h - (off_y + y * scale)

    gstates: dict[float, str] = {}
    ops: list[str] = ["1 J", "1 j"]  # round caps & joins

    bg = parse_color(background)
    if bg is not None and background not in ("none", "transparent"):
        r, g, b = bg[0], bg[1], bg[2]
        ops.append(f"{r:.4f} {g:.4f} {b:.4f} rg")
        ops.append(f"0 0 {page_w:.2f} {page_h:.2f} re f")

    for p in canvas.prims:
        if p.kind == "rect":
            ops.extend(_pdf_color_ops(p.fill, p.opacity, gstates))
            if p.stroke:
                ops.append(f"{p.stroke_width * scale:.2f} w")
                if p.dash:
                    ops.append("[" + " ".join(f"{d * scale:.2f}" for d in p.dash) + "] 0 d")
                else:
                    ops.append("[] 0 d")
            ops.extend(_rounded_rect_ops(X(p.x), Y(p.y + p.h), max(p.w, 0) * scale, max(p.h, 0) * scale, p.rx * scale))
            ops.append(_paint_op(p))
        elif p.kind == "ellipse":
            ops.extend(_pdf_color_ops(p.fill, p.opacity, gstates))
            if p.stroke:
                ops.append(f"{p.stroke_width * scale:.2f} w")
                ops.append("[] 0 d")
            ops.extend(_ellipse_ops(X(p.x), Y(p.y), max(p.rx, 0) * scale, max(p.ry, 0) * scale))
            ops.append(_paint_op(p))
        elif p.kind == "polygon":
            if not p.pts:
                continue
            ops.extend(_pdf_color_ops(p.fill, p.opacity, gstates))
            if p.stroke:
                ops.append(f"{p.stroke_width * scale:.2f} w")
                if p.dash:
                    ops.append("[" + " ".join(f"{d * scale:.2f}" for d in p.dash) + "] 0 d")
                else:
                    ops.append("[] 0 d")
            pts = [(X(x), Y(y)) for (x, y) in p.pts]
            ops.append(f"{pts[0][0]:.2f} {pts[0][1]:.2f} m")
            for x, y in pts[1:]:
                ops.append(f"{x:.2f} {y:.2f} l")
            ops.append("h")
            ops.append(_paint_op(p))
        elif p.kind == "polyline":
            if len(p.pts) < 2:
                continue
            rgba = parse_color(p.stroke)
            if rgba is not None:
                r, g, b = rgba[0], rgba[1], rgba[2]
                ops.append(f"{r:.4f} {g:.4f} {b:.4f} RG")
            if p.opacity < 0.999:
                key = round(p.opacity, 3)
                name = gstates.setdefault(key, f"GS{len(gstates)}")
                ops.append(f"/{name} gs")
            ops.append(f"{max(p.stroke_width, 0.1) * scale:.2f} w")
            if p.dash:
                ops.append("[" + " ".join(f"{d * scale:.2f}" for d in p.dash) + "] 0 d")
            else:
                ops.append("[] 0 d")
            pts = [(X(x), Y(y)) for (x, y) in p.pts]
            ops.append(f"{pts[0][0]:.2f} {pts[0][1]:.2f} m")
            for x, y in pts[1:]:
                ops.append(f"{x:.2f} {y:.2f} l")
            ops.append("S")
        elif p.kind == "text":
            rgba = parse_color(p.color)
            if rgba is None:
                continue
            if p.opacity < 0.999:
                key = round(p.opacity, 3)
                name = gstates.setdefault(key, f"GS{len(gstates)}")
                ops.append(f"/{name} gs")
            r, g, b = rgba[0], rgba[1], rgba[2]
            fnum = 2 if p.bold else 1
            lh = p.size * LINE_H * scale
            for i, line in enumerate(p.lines):
                w = text_width(line, p.size, p.bold) * scale
                if p.anchor == "middle":
                    tx = X(p.x) - w / 2
                elif p.anchor == "end":
                    tx = X(p.x) - w
                else:
                    tx = X(p.x)
                ty = Y(p.y + (i + 0.8) * p.size * LINE_H)
                ops.append("BT")
                ops.append(f"/F{fnum} {p.size * scale:.2f} Tf")
                ops.append(f"{r:.4f} {g:.4f} {b:.4f} rg")
                ops.append(f"{tx:.2f} {ty:.2f} Td")
                ops.append(f"({_pdf_escape_text(line)}) Tj")
                ops.append("ET")

    content = "\n".join(ops).encode("latin-1", "replace")
    stream = zlib.compress(content)

    # Fixed object layout: 1 catalog, 2 pages, 3 page, 4 stream,
    # 5 F1 (Helvetica), 6 F2 (Helvetica-Bold), 7+ ExtGStates.
    num_catalog = 1
    num_pages = 2
    num_page = 3
    num_stream = 4
    num_f1 = 5
    num_f2 = 6

    gs_names: list[tuple[str, float]] = sorted(gstates.items(), key=lambda kv: kv[1])
    gs_num: dict[str, int] = {name: 7 + i for i, (name, _key) in enumerate(gs_names)}

    objects: dict[int, bytes] = {}

    objects[num_catalog] = b"<< /Type /Catalog /Pages " + str(num_pages).encode() + b" 0 R >>"
    objects[num_pages] = b"<< /Type /Pages /Kids [" + str(num_page).encode() + b" 0 R] /Count 1 >>"
    objects[num_stream] = (
        b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream"
    )
    objects[num_f1] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    objects[num_f2] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    for name, key in gs_names:
        objects[gs_num[name]] = f"<< /Type /ExtGState /ca {key} /CA {key} >>".encode()

    res_parts = [
        b"<< /Font << /F1 ",
        str(num_f1).encode(),
        b" 0 R /F2 ",
        str(num_f2).encode(),
        b" 0 R >>",
    ]
    if gs_num:
        res_parts.append(b" /ExtGState << ")
        for name in gs_num:
            res_parts.append(f"/{name} ".encode() + str(gs_num[name]).encode() + b" 0 R ")
        res_parts.append(b">>")
    res_parts.append(b" >>")
    resources = b"".join(res_parts)
    # rebuild the page object now that resources is final
    objects[num_page] = (
        b"<< /Type /Page /Parent "
        + str(num_pages).encode()
        + b" 0 R /MediaBox [0 0 "
        + f"{page_w:.2f} {page_h:.2f}".encode()
        + b"] /Resources "
        + resources
        + b" /Contents "
        + str(num_stream).encode()
        + b" 0 R >>"
    )

    total = max(objects)
    out = bytearray()
    out += b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0] * (total + 1)
    for i in range(1, total + 1):
        offsets[i] = len(out)
        out += str(i).encode() + b" 0 obj\n" + objects[i] + b"\nendobj\n"
    xref_pos = len(out)
    n = total + 1
    out += b"xref\n0 " + str(n).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for i in range(1, total + 1):
        out += f"{offsets[i]:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(n).encode()
        + b" /Root "
        + str(num_catalog).encode()
        + b" 0 R >>\nstartxref\n"
        + str(xref_pos).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


# --------------------------------------------------------------------------
# PNG backend (optional, uses Pillow when available)
# --------------------------------------------------------------------------

_FONT_CANDIDATES: tuple[str, ...] = (
    # Android / Termux
    "/system/fonts/Roboto-Regular.ttf",
    "/system/fonts/DroidSans.ttf",
    "/system/fonts/NotoSans-Regular.ttf",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/data/data/com.termux/files/usr/share/fonts/TTF/Tinos-Regular.ttf",
    "/data/data/com.termux/files/usr/share/fonts/TTF/Roboto-Regular.ttf",
    # Debian/Ubuntu
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    # Arch/Fedora
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)

_FONT_GLOBS: tuple[tuple[str, str], ...] = (
    ("/usr/share/fonts", "**/DejaVuSans.ttf"),
    ("/usr/share/fonts", "**/NotoSans-Regular.ttf"),
    ("/usr/share/fonts", "**/NotoSansSC-Regular.ttf"),
    ("/usr/share/fonts", "**/LiberationSans-Regular.ttf"),
)


def _font_candidates(bold: bool) -> list[str]:
    """Build an ordered list of font file candidates (bold variants first)."""
    out: list[str] = []
    override = os.environ.get("MMDC_FONT")
    if override:
        out.append(override)
        if bold:
            out.insert(0, override.replace("Regular", "Bold"))
    for cand in _FONT_CANDIDATES:
        out.append(cand.replace("Regular", "Bold") if bold else cand)
        out.append(cand)
    for root, pattern in _FONT_GLOBS:
        try:
            found = sorted(Path(root).glob(pattern))
        except Exception:
            continue
        for p in found:
            out.append(str(p).replace("Regular", "Bold") if bold else str(p))
            out.append(str(p))
    return out


def _find_pil_font(px: int, bold: bool = False) -> Any:
    """Locate a truetype font, falling back to Pillow's default font."""
    from PIL import ImageFont

    seen: set[str] = set()
    for cand in _font_candidates(bold):
        if not cand or cand in seen:
            continue
        seen.add(cand)
        path = Path(cand)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), px)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=px)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _pil_color(color: str | None) -> Any:
    """Convert a color string to an RGBA tuple for Pillow (None = skip)."""
    if color is None:
        return None
    rgba = parse_color(color)
    if rgba is None:
        return None
    return (
        int(round(rgba[0] * 255)),
        int(round(rgba[1] * 255)),
        int(round(rgba[2] * 255)),
        int(round(rgba[3] * 255)),
    )


def _dashed_segments(pts: Sequence[Pt], dash: Sequence[float]) -> list[tuple[Pt, Pt]]:
    """Break a polyline into dashed sub-segments."""
    on = True
    segs: list[tuple[Pt, Pt]] = []
    di = 0
    remaining = dash[0] if dash else 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        length = math.hypot(x2 - x1, y2 - y1)
        if length == 0:
            continue
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        cur = (x1, y1)
        travelled = 0.0
        while travelled < length:
            step = min(remaining, length - travelled)
            nxt = (cur[0] + ux * step, cur[1] + uy * step)
            if on:
                segs.append((cur, nxt))
            cur = nxt
            travelled += step
            remaining -= step
            if remaining <= 1e-9:
                di = (di + 1) % len(dash)
                remaining = dash[di]
                on = not on
    return segs


def canvas_to_png(
    canvas: Canvas,
    *,
    background: str | None = "white",
    scale: float = 1.0,
    size: int | None = None,
) -> bytes:
    """Rasterize a :class:`Canvas` to PNG bytes using Pillow."""
    import io

    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise MermaidError(
            "PNG output requires the optional Pillow library. "
            "Install it with 'pip install pillow' (on Termux: "
            "'pkg install python-pillow'). SVG and PDF output work without "
            "any extra libraries."
        ) from exc

    eff = min(1.0, size / max(canvas.w, canvas.h)) if size else 1.0
    final = max(0.05, float(scale)) * eff
    px_w = max(1, int(round(canvas.w * final)))
    px_h = max(1, int(round(canvas.h * final)))
    ss = 2  # supersampling factor for smoother output
    big_w, big_h = px_w * ss, px_h * ss
    k = final * ss

    bg_rgba = _pil_color(background) if background not in ("transparent", "none") else None
    if bg_rgba is None:
        img = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    else:
        img = Image.new("RGBA", (big_w, big_h), bg_rgba)

    overlay = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    fonts: dict[tuple[int, bool], Any] = {}

    def font_for(size: float, bold: bool) -> Any:
        key = (int(round(size * k)), bold)
        if key not in fonts:
            fonts[key] = _find_pil_font(key[0], bold)
        return fonts[key]

    def pt(p: Pt) -> tuple[float, float]:
        return (p[0] * k, p[1] * k)

    def fade(color: str | None, opacity: float) -> Any:
        base = _pil_color(color)
        if base is None:
            return None
        return (base[0], base[1], base[2], int(round(base[3] * opacity)))

    def draw_prim(p: Prim, d: Any) -> None:
        opacity = max(0.0, min(1.0, p.opacity))
        if opacity <= 0.0:
            return
        solid = opacity >= 0.999

        def fill_c() -> Any:
            return _pil_color(p.fill) if solid else fade(p.fill, opacity)

        def stroke_c() -> Any:
            return _pil_color(p.stroke) if solid else fade(p.stroke, opacity)

        if p.kind == "rect":
            x0, y0 = p.x * k, p.y * k
            x1, y1 = x0 + p.w * k, y0 + p.h * k
            r = p.rx * k
            fill, stroke = fill_c(), stroke_c()
            if fill or stroke:
                width = max(1, int(round(p.stroke_width * k))) if stroke else 0
                if r > 0.5 and hasattr(d, "rounded_rectangle"):
                    d.rounded_rectangle((x0, y0, x1, y1), radius=r, fill=fill, outline=stroke, width=width)
                else:
                    d.rectangle((x0, y0, x1, y1), fill=fill, outline=stroke, width=width)
        elif p.kind == "ellipse":
            fill, stroke = fill_c(), stroke_c()
            if fill or stroke:
                d.ellipse(
                    ((p.x - p.rx) * k, (p.y - p.ry) * k, (p.x + p.rx) * k, (p.y + p.ry) * k),
                    fill=fill,
                    outline=stroke,
                    width=max(1, int(round(p.stroke_width * k))) if stroke else 0,
                )
        elif p.kind == "polygon":
            if not p.pts:
                return
            fill, stroke = fill_c(), stroke_c()
            pts = [pt(q) for q in p.pts]
            if fill:
                d.polygon(pts, fill=fill)
            if stroke:
                d.line(pts + [pts[0]], fill=stroke, width=max(1, int(round(p.stroke_width * k))), joint="curve")
        elif p.kind == "polyline":
            if len(p.pts) < 2:
                return
            color = stroke_c()
            if color is None:
                return
            width = max(1, int(round(p.stroke_width * k)))
            if p.dash:
                for a, b in _dashed_segments(p.pts, p.dash):
                    d.line([pt(a), pt(b)], fill=color, width=width)
            else:
                d.line([pt(q) for q in p.pts], fill=color, width=width, joint="curve")
        elif p.kind == "text":
            color = _pil_color(p.color) if solid else fade(p.color, opacity)
            if color is None:
                return
            f = font_for(p.size, p.bold)
            lh = p.size * LINE_H * k
            anchor = {"start": "la", "middle": "ma", "end": "ra"}[p.anchor]
            for i, line in enumerate(p.lines):
                d.text((p.x * k, p.y * k + i * lh), line, font=f, fill=color, anchor=anchor)

    for p in canvas.prims:
        if p.opacity >= 0.999:
            draw_prim(p, draw)
        else:
            layer = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
            draw_prim(p, ImageDraw.Draw(layer))
            overlay.alpha_composite(layer)

    img.alpha_composite(overlay)

    if ss > 1 and (big_w != px_w or big_h != px_h):
        img = img.resize((px_w, px_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------
# shared mermaid source preprocessing
# --------------------------------------------------------------------------

_INIT_RE = re.compile(r"%%\{\s*init\s*:((?:[^%]|%[^}])*)\}\s*%%")
_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def strip_comments(text: str) -> str:
    """Remove ``%%`` comments (full-line and trailing) outside quotes."""
    out_lines: list[str] = []
    for line in text.split("\n"):
        quote: str | None = None
        cut = None
        for i, ch in enumerate(line):
            if quote is not None:
                if ch == quote:
                    quote = None
                continue
            if ch in ('"', "'"):
                quote = ch
                continue
            if ch == "%" and line[i : i + 2] == "%%":
                cut = i
                break
        out_lines.append(line if cut is None else line[:cut])
    return "\n".join(out_lines)


def split_statements(text: str) -> list[str]:
    """Split mermaid source into statements on newlines and ``;``."""
    statements: list[str] = []
    for line in text.split("\n"):
        parts: list[str] = []
        buf: list[str] = []
        depth = 0
        quote: str | None = None
        for ch in line:
            if quote is not None:
                buf.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in ('"', "'"):
                quote = ch
                buf.append(ch)
                continue
            if ch in "([{":
                depth += 1
                buf.append(ch)
                continue
            if ch in ")]}":
                depth = max(0, depth - 1)
                buf.append(ch)
                continue
            if ch == ";" and depth == 0:
                parts.append("".join(buf))
                buf = []
                continue
            buf.append(ch)
        parts.append("".join(buf))
        statements.extend(p.strip() for p in parts if p.strip())
    return statements


def parse_json_ish(s: str) -> Any:
    """Parse a JSON (or single-quoted JS) object literal."""
    s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return ast.literal_eval(s)


def extract_init_directives(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Pull ``%%{init: {...}}%%`` directives out of the source."""
    configs: list[dict[str, Any]] = []
    for m in _INIT_RE.finditer(text):
        try:
            parsed = parse_json_ish(m.group(1))
        except Exception:
            continue
        if isinstance(parsed, dict):
            configs.append(parsed)
    text = _INIT_RE.sub("", text)
    return text, configs


def extract_front_matter(text: str) -> tuple[str, dict[str, Any]]:
    """Parse an optional YAML-ish front matter block (``---`` fenced)."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return text, {}
    meta: dict[str, Any] = {}
    config: dict[str, Any] = {}
    for line in m.group(1).split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if key == "config":
            try:
                parsed = parse_json_ish(value)
                if isinstance(parsed, dict):
                    config = parsed
            except Exception:
                pass
        elif value:
            meta[key] = value
    if config:
        meta["config"] = config
    return text[m.end() :], meta


_ACC_TITLE_RE = re.compile(r"^\s*accTitle\s*:\s*(.*)$", re.MULTILINE)
_ACC_DESCR_RE = re.compile(r"^\s*accDescr\s*:\s*(.*)$", re.MULTILINE)


def extract_acc(text: str) -> tuple[str, str | None, str | None]:
    """Extract ``accTitle:`` / ``accDescr:`` accessibility metadata."""
    title = None
    desc = None
    m = _ACC_TITLE_RE.search(text)
    if m and m.group(1).strip():
        title = m.group(1).strip()
        text = _ACC_TITLE_RE.sub("", text, count=1)
    m = _ACC_DESCR_RE.search(text)
    if m and m.group(1).strip():
        desc = m.group(1).strip()
        text = _ACC_DESCR_RE.sub("", text, count=1)
    return text, title, desc


def detect_diagram_type(statements: Sequence[str]) -> str:
    """Return the diagram keyword of the first significant statement."""
    for stmt in statements:
        head = stmt.split()[0] if stmt.split() else ""
        if head:
            return head
    return ""


def preprocess_definition(definition: str) -> tuple[list[str], dict[str, Any], str | None, str | None]:
    """Full preprocessing pipeline.

    Returns ``(statements, merged_mermaid_config, title, desc)`` where
    statements have no comments / front matter / init directives / acc
    metadata left in them.
    """
    text = definition.replace("\r\n", "\n").replace("\r", "\n")
    text, front = extract_front_matter(text)
    text, inits = extract_init_directives(text)
    text = strip_comments(text)
    text, acc_title, acc_desc = extract_acc(text)

    config: dict[str, Any] = {}
    if isinstance(front.get("config"), dict):
        config.update(front["config"])
    for init in inits:
        config.update(init)

    title = acc_title or front.get("title")
    desc = acc_desc or front.get("description")

    statements = split_statements(text)
    return statements, config, title, desc


# --------------------------------------------------------------------------
# flowchart / graph parser
# --------------------------------------------------------------------------


@dataclass
class FNode:
    id: str
    label: str = ""
    shape: str = "rect"
    group: str | None = None
    classes: list[str] = field(default_factory=list)
    style: dict[str, str] = field(default_factory=dict)


@dataclass
class FEdge:
    src: str
    dst: str
    style: str = "solid"  # solid | dotted | thick
    head: str = "arrow"  # arrow | circle | cross | none
    tail: str = "none"
    label: str = ""
    weight: int = 2
    style_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class FGroup:
    id: str
    title: str
    parent: str | None = None


@dataclass
class FlowIR:
    direction: str = "TB"
    nodes: dict[str, FNode] = field(default_factory=dict)
    edges: list[FEdge] = field(default_factory=list)
    groups: dict[str, FGroup] = field(default_factory=dict)
    class_defs: dict[str, dict[str, str]] = field(default_factory=dict)
    link_styles: list[tuple[str, dict[str, str]]] = field(default_factory=list)


# ---- edge operator scanner ---------------------------------------------

_RUN_RE = re.compile(r"-{2,}|={2,}|-\.[-.]*")
_CLOSER_FOR_STYLE = {"solid": r"-{2,}", "thick": r"={2,}", "dotted": r"\.[-.]*"}


def _read_head(s: str, i: int) -> tuple[str, int]:
    if i < len(s) and s[i] in ">ox":
        return {">": "arrow", "o": "circle", "x": "cross"}[s[i]], i + 1
    return "none", i


def _read_pipe_label(s: str, i: int) -> tuple[str | None, int]:
    j = i
    n = len(s)
    while j < n and s[j].isspace():
        j += 1
    if j < n and s[j] == "|":
        k = s.find("|", j + 1)
        if k != -1:
            return s[j + 1 : k].strip(), k + 1
    return None, i


def _match_op(s: str, i: int) -> dict[str, Any] | None:
    """Try to match a flowchart edge operator at ``s[i]``.

    Returns a dict with keys style/head/tail/label/weight/end, or None when
    the characters at ``i`` do not form an operator (e.g. a lone hyphen in
    an id like ``node-1``).
    """
    n = len(s)
    tail = "none"
    ch = s[i]

    if ch == "<":
        if not _RUN_RE.match(s, i + 1):
            return None
        i += 1
        tail = "arrow"
    elif ch in "ox":
        if not _RUN_RE.match(s, i + 1):
            return None
        tail = "circle" if ch == "o" else "cross"
        i += 1

    m = _RUN_RE.match(s, i)
    if not m:
        return None
    run = m.group(0)
    if "." in run:
        style = "dotted"
    elif run[0] == "=":
        style = "thick"
    else:
        style = "solid"
    weight = len(run)
    i = m.end()

    label: str | None = None
    closer = _CLOSER_FOR_STYLE[style]
    text_re = re.compile(r"\s+(?P<text>\S.*?)\s+(?:" + closer + r")")
    mt = text_re.match(s, i)
    if mt and mt.group("text"):
        label = mt.group("text").strip()
        i = mt.end()

    head, i = _read_head(s, i)
    if label is None:
        label, i = _read_pipe_label(s, i)

    return {
        "style": style,
        "head": head,
        "tail": tail,
        "label": label or "",
        "weight": weight,
        "end": i,
    }


def _scan_statement(stmt: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Split a flowchart statement into node parts and edge operators."""
    parts: list[str] = []
    ops: list[dict[str, Any]] = []
    buf: list[str] = []
    i = 0
    n = len(stmt)
    depth = 0
    quote: str | None = None
    while i < n:
        ch = stmt[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in "([{":
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            buf.append(ch)
            i += 1
            continue
        if depth == 0 and (ch in "-=.<" or (ch in "ox" and _RUN_RE.match(stmt, i + 1))):
            op = _match_op(stmt, i)
            if op:
                parts.append("".join(buf).strip())
                buf = []
                ops.append(op)
                i = op["end"]
                continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf).strip())
    return parts, ops


def _split_amp(s: str) -> list[str]:
    """Split ``A & B`` on ``&`` outside brackets/quotes."""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in s:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "&" and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return [p.strip() for p in out if p.strip()]


# ---- node reference parsing ---------------------------------------------


_SHAPE_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\(\(\((.*)\)\)\)$", re.S), "double_circle"),
    (re.compile(r"^\(\[(.*)\]\)$", re.S), "stadium"),
    (re.compile(r"^\(\((.*)\)\)$", re.S), "circle"),
    (re.compile(r"^\[\[(.*)\]\]$", re.S), "subroutine"),
    (re.compile(r"^\[\((.*)\)\]$", re.S), "cylinder"),
    (re.compile(r"^\{\{(.*)\}\}$", re.S), "hexagon"),
    (re.compile(r"^\{(.*)\}$", re.S), "rhombus"),
    (re.compile(r"^\[/(.*)\\\]$", re.S), "trapezoid"),
    (re.compile(r"^\[\\(.*)/\]$", re.S), "trapezoid_alt"),
    (re.compile(r"^\[/(.*)/\]$", re.S), "para"),
    (re.compile(r"^\[\\(.*)\\\]$", re.S), "para_alt"),
    (re.compile(r"^\[(.*)\]$", re.S), "rect"),
    (re.compile(r"^\((.*)\)$", re.S), "rounded"),
    (re.compile(r"^>(.*)\]$", re.S), "asym"),
]


def _match_shape(s: str) -> tuple[str, str] | None:
    """Return ``(label, shape)`` when ``s`` is a shape-wrapped label."""
    for rx, shape in _SHAPE_RES:
        m = rx.match(s.strip())
        if m:
            label = m.group(1).strip()
            if len(label) >= 2 and label[0] == '"' and label[-1] == '"':
                label = label[1:-1]
            elif len(label) >= 2 and label[0] == "`" and label[-1] == "`":
                label = label[1:-1]
            return (label, shape)
    return None


_CLASS_SUFFIX_RE = re.compile(r":::([A-Za-z0-9_-]+)\s*$")


def _parse_node_ref(text: str, counter: dict[str, int]) -> tuple[str, str | None, str | None, list[str]] | None:
    """Parse ``A``, ``A[Label]``, ``A{...}``, ``[Box]`` ... into id/label/shape."""
    text = text.strip()
    classes: list[str] = []
    while True:
        m = _CLASS_SUFFIX_RE.search(text)
        if not m:
            break
        classes.insert(0, m.group(1))
        text = text[: m.start()].strip()
    if not text:
        return None

    m = re.match(r"^(\w+)\s*(.*)$", text, re.S)
    if m and m.group(1):
        nid = m.group(1)
        rest = m.group(2).strip()
        if not rest:
            return (nid, None, None, classes)
        sh = _match_shape(rest)
        if sh:
            return (nid, sh[0] or None, sh[1], classes)
        return (nid, None, None, classes)

    sh = _match_shape(text)
    if sh:
        counter["n"] += 1
        return (f"anon{counter['n']}", sh[0] or None, sh[1], classes)

    if re.fullmatch(r"\S+", text):
        return (text, None, None, classes)
    counter["n"] += 1
    safe = re.sub(r"\W+", "_", text).strip("_") or "n"
    return (f"anon{counter['n']}", text, "rect", classes)


# ---- statement-level parsing --------------------------------------------


def _parse_style_dict(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in re.split(r",(?![^()]*\))", s):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        k, _, v = chunk.partition(":")
        out[k.strip()] = v.strip()
    return out


_SUBGRAPH_RE = re.compile(r"^subgraph\s+(.+)$", re.IGNORECASE)
_DIR_RE = re.compile(r"^(TB|TD|BT|RL|LR)$", re.IGNORECASE)


def _group_id_for(title: str, existing: dict[str, FGroup]) -> str:
    gid = "cluster_" + (re.sub(r"\W+", "_", title).strip("_") or "g")
    while gid in existing:
        gid += "_"
    return gid


def parse_flowchart(statements: Sequence[str]) -> FlowIR:
    """Parse preprocessed flowchart statements into a :class:`FlowIR`."""
    ir = FlowIR()
    group_stack: list[str] = []
    counter: dict[str, int] = {"n": 0}

    def ensure_node(nid: str, label: str | None, shape: str | None, classes: list[str]) -> str:
        if nid not in ir.nodes:
            ir.nodes[nid] = FNode(
                id=nid,
                label=label if label is not None else nid,
                shape=shape or "rect",
                group=group_stack[-1] if group_stack else None,
            )
        else:
            node = ir.nodes[nid]
            if label is not None:
                node.label = label
            if shape is not None:
                node.shape = shape
        for c in classes:
            if c not in ir.nodes[nid].classes:
                ir.nodes[nid].classes.append(c)
        return nid

    first = True
    for raw in statements:
        stmt = raw.strip()
        if not stmt:
            continue

        if first:
            m = re.match(r"^(?:flowchart|graph)\s*(.*)$", stmt, re.IGNORECASE)
            if m:
                d = m.group(1).strip().upper()
                if _DIR_RE.match(d or ""):
                    ir.direction = d
                first = False
                continue
            first = False

        low = stmt.lower()

        m = _SUBGRAPH_RE.match(stmt)
        if m:
            rest = m.group(1).strip()
            if rest.startswith('"') and rest.endswith('"') and len(rest) >= 2:
                title = rest[1:-1]
                gid = _group_id_for(title, ir.groups)
            elif "[" in rest and rest.endswith("]"):
                gid_part, _, title_part = rest.partition("[")
                gid = gid_part.strip()
                title = title_part[:-1].strip()
                if len(title) >= 2 and title[0] == '"' and title[-1] == '"':
                    title = title[1:-1]
                if not gid:
                    gid = _group_id_for(title, ir.groups)
                if gid in ir.groups:
                    gid += "_"
            else:
                toks = rest.split()
                if len(toks) == 1:
                    gid, title = toks[0], toks[0]
                else:
                    title = rest
                    gid = _group_id_for(rest, ir.groups)
            ir.groups[gid] = FGroup(
                id=gid,
                title=title or gid,
                parent=group_stack[-1] if group_stack else None,
            )
            group_stack.append(gid)
            continue

        if re.match(r"^end$", stmt, re.IGNORECASE):
            if group_stack:
                group_stack.pop()
            continue

        if low.startswith("direction "):
            continue  # subgraph-local direction is not honoured (see README)

        if low.startswith("classdef "):
            m2 = re.match(r"^classDef\s+(\S+)\s+(.*)$", stmt, re.IGNORECASE)
            if m2:
                ir.class_defs[m2.group(1)] = _parse_style_dict(m2.group(2))
            continue

        if low.startswith("class "):
            m2 = re.match(r"^class\s+(.*)\s+(\S+)$", stmt, re.IGNORECASE)
            if m2:
                for nid in re.split(r"[,\s]+", m2.group(1).strip()):
                    if nid and nid in ir.nodes:
                        if m2.group(2) not in ir.nodes[nid].classes:
                            ir.nodes[nid].classes.append(m2.group(2))
            continue

        if low.startswith("style "):
            m2 = re.match(r"^style\s+(\S+)\s+(.*)$", stmt, re.IGNORECASE)
            if m2 and m2.group(1) in ir.nodes:
                ir.nodes[m2.group(1)].style.update(_parse_style_dict(m2.group(2)))
            continue

        if low.startswith("linkstyle "):
            m2 = re.match(r"^linkStyle\s+(\S+)\s+(.*)$", stmt, re.IGNORECASE)
            if m2:
                ir.link_styles.append((m2.group(1), _parse_style_dict(m2.group(2))))
            continue

        if low.startswith("click "):
            continue  # interaction callbacks have no static rendering

        parts, ops = _scan_statement(stmt)
        if not ops:
            for part in _split_amp(" ".join(p for p in parts if p)):
                if not part:
                    continue
                ref = _parse_node_ref(part, counter)
                if ref:
                    ensure_node(ref[0], ref[1], ref[2], ref[3])
            continue

        for k, op in enumerate(ops):
            if k + 1 >= len(parts):
                break
            left_parts = _split_amp(parts[k])
            right_parts = _split_amp(parts[k + 1])
            for lp in left_parts:
                lref = _parse_node_ref(lp, counter)
                if not lref:
                    continue
                lid = ensure_node(lref[0], lref[1], lref[2], lref[3])
                for rp in right_parts:
                    rref = _parse_node_ref(rp, counter)
                    if not rref:
                        continue
                    rid = ensure_node(rref[0], rref[1], rref[2], rref[3])
                    ir.edges.append(
                        FEdge(
                            src=lid,
                            dst=rid,
                            style=op["style"],
                            head=op["head"],
                            tail=op["tail"],
                            label=op.get("label") or "",
                            weight=op.get("weight", 2),
                        )
                    )

    return ir


# --------------------------------------------------------------------------
# layered (Sugiyama-lite) layout shared by flowchart/class/ER/state
# --------------------------------------------------------------------------


def layered_layout(
    sizes: dict[str, tuple[float, float]],
    edges: list[tuple[str, str]],
    *,
    node_spacing: float = 45.0,
    rank_spacing: float = 70.0,
    group_of: dict[str, str] | None = None,
) -> tuple[dict[str, tuple[float, float]], dict[str, int]]:
    """Compute node center positions for a layered drawing (top-down).

    Returns ``(positions, ranks)``.  Uses longest-path layering, barycenter
    crossing reduction with group contiguity, and simple centered ranks.
    """
    ids = list(sizes)
    if not ids:
        return {}, {}

    succ: dict[str, list[str]] = {i: [] for i in ids}
    pred: dict[str, list[str]] = {i: [] for i in ids}
    for a, b in edges:
        if a == b or a not in sizes or b not in sizes:
            continue
        succ[a].append(b)
        pred[b].append(a)

    # --- cycle detection: mark back edges -------------------------------
    back: set[tuple[str, str]] = set()
    state: dict[str, int] = {}
    for start in ids:
        if state.get(start):
            continue
        state[start] = 1
        stack: list[tuple[str, Any]] = [(start, iter(succ[start]))]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if state.get(nxt) == 1:
                    back.add((node, nxt))
                elif not state.get(nxt):
                    state[nxt] = 1
                    stack.append((nxt, iter(succ[nxt])))
                    advanced = True
                    break
            if not advanced:
                state[node] = 2
                stack.pop()

    # --- ranking (longest path over non-back edges) ----------------------
    indeg = {i: 0 for i in ids}
    for a, b in edges:
        if a in sizes and b in sizes and a != b and (a, b) not in back:
            indeg[b] += 1
    queue = [i for i in ids if indeg[i] == 0]
    rank: dict[str, int] = {i: 0 for i in ids}
    processed: set[str] = set()
    qi = 0
    while qi < len(queue):
        u = queue[qi]
        qi += 1
        processed.add(u)
        for v in succ[u]:
            if (u, v) in back:
                continue
            if rank[v] < rank[u] + 1:
                rank[v] = rank[u] + 1
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    for i in ids:
        if i in processed:
            continue
        ps = [rank[p] for p in pred[i] if p in processed and (p, i) not in back]
        rank[i] = max(ps) + 1 if ps else 0

    max_rank = max(rank.values())
    buckets: list[list[str]] = [[] for _ in range(max_rank + 1)]
    for i in ids:
        buckets[rank[i]].append(i)

    # --- initial ordering with group contiguity -------------------------
    decl_idx = {nid: k for k, nid in enumerate(ids)}
    if group_of:
        group_order: dict[str, int] = {}
        for i in ids:
            g = group_of.get(i)
            if g is not None and g not in group_order:
                group_order[g] = len(group_order)
        for b in buckets:
            b.sort(key=lambda i: (group_order.get(group_of.get(i), 10**9), decl_idx[i]))
    else:
        for b in buckets:
            b.sort(key=lambda i: decl_idx[i])

    # --- barycenter crossing reduction -----------------------------------
    neighbors: dict[str, list[str]] = {i: pred[i] + succ[i] for i in ids}

    def positions() -> dict[str, int]:
        pos: dict[str, int] = {}
        for b in buckets:
            for k, nid in enumerate(b):
                pos[nid] = k
        return pos

    for _sweep in range(4):
        pos = positions()
        for b in buckets:
            bary: dict[str, float] = {}
            for nid in b:
                ns = [pos[x] for x in neighbors[nid] if x in pos]
                bary[nid] = sum(ns) / len(ns) if ns else pos[nid]
            if group_of:
                gsum: dict[str, list[float]] = {}
                for nid in b:
                    g = group_of.get(nid)
                    if g is not None:
                        gsum.setdefault(g, []).append(bary[nid])
                gavg = {g: sum(v) / len(v) for g, v in gsum.items()}

                def key(nid: str, _b=bary, _g=gavg, _p=pos) -> tuple[float, float, int]:
                    g = group_of.get(nid)
                    if g is not None and g in _g:
                        return (_g[g], _b[nid], _p[nid])
                    return (_b[nid], _b[nid], _p[nid])

            else:

                def key(nid: str, _b=bary, _p=pos) -> tuple[float, float, int]:
                    return (_b[nid], _b[nid], _p[nid])

            b.sort(key=key)
        # reverse sweeps help downward pull
        buckets.reverse()
        buckets.reverse()

    # --- coordinates ------------------------------------------------------
    widths = [sum(sizes[i][0] for i in b) + node_spacing * (len(b) - 1) if b else 0.0 for b in buckets]
    total_w = max(widths) if widths else 0.0
    band_h = [max((sizes[i][1] for i in b), default=0.0) for b in buckets]

    positions_out: dict[str, tuple[float, float]] = {}
    y_cursor = 0.0
    for r, b in enumerate(buckets):
        x = (total_w - widths[r]) / 2 if widths[r] else 0.0
        cy = y_cursor + band_h[r] / 2
        for nid in b:
            w, _h = sizes[nid]
            positions_out[nid] = (x + w / 2, cy)
            x += w + node_spacing
        y_cursor += band_h[r] + rank_spacing
    return positions_out, rank


# --------------------------------------------------------------------------
# style resolution helpers shared by diagram renderers
# --------------------------------------------------------------------------


def _css_float(v: str, default: float) -> float:
    try:
        return float(re.sub(r"[^0-9.\-]", "", v) or default)
    except ValueError:
        return default


def _apply_style_dict(
    style: dict[str, str],
    fill: str | None,
    stroke: str | None,
    text: str | None,
    sw: float,
    dash: tuple[float, ...] | None,
) -> tuple[str | None, str | None, str | None, float, tuple[float, ...] | None]:
    for k, v in style.items():
        lk = k.lower()
        if lk == "fill":
            fill = v
        elif lk == "stroke":
            stroke = v
        elif lk == "color":
            text = v
        elif lk in ("stroke-width", "strokewidth"):
            sw = _css_float(v, sw)
        elif lk in ("stroke-dasharray", "strokedasharray", "dasharray"):
            try:
                dash = tuple(float(x) for x in re.split(r"[\s,]+", v) if x)
            except ValueError:
                pass
    return fill, stroke, text, sw, dash


def _group_boxes(
    groups: dict[str, Any],
    node_rects: dict[str, tuple[float, float, float, float]],
    node_group_of: Callable[[str], str | None],
    pad: float = 16.0,
    title_h: float = 30.0,
) -> dict[str, tuple[float, float, float, float]]:
    """Compute bounding boxes ``(x, y, w, h)`` for nested groups."""
    boxes: dict[str, tuple[float, float, float, float]] = {}
    children: dict[str | None, list[str]] = {}
    for gid, grp in groups.items():
        children.setdefault(getattr(grp, "parent", None), []).append(gid)
    direct_members: dict[str, list[str]] = {}
    for nid in node_rects:
        g = node_group_of(nid)
        if g is not None:
            direct_members.setdefault(g, []).append(nid)

    def box_of(gid: str) -> tuple[float, float, float, float]:
        if gid in boxes:
            return boxes[gid]
        boxes[gid] = (0.0, 0.0, 0.0, 0.0)  # cycle guard
        xs: list[float] = []
        ys: list[float] = []
        for nid in direct_members.get(gid, []):
            x, y, w, h = node_rects[nid]
            xs.extend((x, x + w))
            ys.extend((y, y + h))
        for child in children.get(gid, []):
            gx, gy, gw, gh = box_of(child)
            if gw > 0:
                xs.extend((gx, gx + gw))
                ys.extend((gy, gy + gh))
        if not xs:
            return boxes[gid]
        x0, y0 = min(xs) - pad, min(ys) - pad - title_h
        x1, y1 = max(xs) + pad, max(ys) + pad
        boxes[gid] = (x0, y0, x1 - x0, y1 - y0)
        return boxes[gid]

    for gid in groups:
        box_of(gid)
    return boxes


# --------------------------------------------------------------------------
# flowchart renderer
# --------------------------------------------------------------------------


def _flow_node_size(node: FNode, theme: Theme) -> tuple[float, float, list[str]]:
    """Return ``(w, h, label_lines)`` for a flowchart node."""
    size = theme.font_size - 2
    label = node.label or node.id
    lines = fit_label(label, size, 220)
    tw, th = lines_size(lines, size)
    pad_x, pad_y = 16.0, 10.0
    w = max(tw + 2 * pad_x, 46.0)
    h = max(th + 2 * pad_y, 36.0)
    shape = node.shape
    if shape in ("circle", "double_circle"):
        d = max(w, h)
        w = h = d
    elif shape == "rhombus":
        w = max(w * 1.6, 70.0)
        h = max(h * 1.7, 52.0)
    elif shape in ("hexagon", "para", "para_alt", "trapezoid", "trapezoid_alt"):
        w += 2 * min(22.0, w * 0.22)
    elif shape == "asym":
        w += min(24.0, w * 0.3)
    elif shape == "stadium":
        w = max(tw + 2 * max(pad_x, h * 0.55), 46.0)
    elif shape == "cylinder":
        h += 10.0
    return (w, h, lines)


def _flow_node_style(node: FNode, ir: FlowIR, theme: Theme) -> dict[str, Any]:
    fill: str | None = theme.node_fill
    stroke: str | None = theme.node_stroke
    text: str | None = theme.node_text
    sw = theme.node_stroke_width
    dash: tuple[float, ...] | None = None
    for cls in node.classes:
        defs = ir.class_defs.get(cls)
        if defs:
            fill, stroke, text, sw, dash = _apply_style_dict(defs, fill, stroke, text, sw, dash)
    fill, stroke, text, sw, dash = _apply_style_dict(node.style, fill, stroke, text, sw, dash)
    return {"fill": fill, "stroke": stroke, "text": text, "sw": sw, "dash": dash}


def _flow_edge_style(edge: FEdge, ir: FlowIR, theme: Theme, idx: int) -> dict[str, Any]:
    color: str | None = theme.edge_stroke
    text: str | None = theme.edge_text
    sw = 1.5 if edge.style != "thick" else 3.5
    dash: tuple[float, ...] | None = (5.0, 4.0) if edge.style == "dotted" else None
    for target, defs in ir.link_styles:
        if target == "default" or (target.isdigit() and int(target) == idx):
            color, _s, text, sw, dash = _apply_style_dict(defs, color, None, text, sw, dash)
    return {"color": color, "text": text, "sw": sw, "dash": dash}


def _route_flow_edge(
    sc: tuple[float, float],
    ss: tuple[float, float],
    dc: tuple[float, float],
    ds: tuple[float, float],
    r_src: int,
    r_dst: int,
    rank_spacing: float,
    loop_idx: int,
) -> tuple[list[Pt], Pt]:
    """Route one edge in abstract top-down space. Returns (points, label_pt)."""
    sx, sy = sc
    sw_, sh_ = ss
    dx, dy = dc
    dw, dh = ds
    src_bottom = sy + sh_ / 2
    dst_top = dy - dh / 2

    if r_dst > r_src:
        # forward edge: bottom of source -> top of destination
        if abs(sx - dx) < 6:
            pts = [(sx, src_bottom), (sx, dst_top)]
            label_pt = (sx, (src_bottom + dst_top) / 2)
        else:
            mid = (src_bottom + dst_top) / 2
            pts = [(sx, src_bottom), (sx, mid), (dx, mid), (dx, dst_top)]
            label_pt = ((sx + dx) / 2, mid)
        return pts, label_pt

    if r_dst == r_src and abs(sx - dx) > 1e-6:
        # same-rank edge: loop out to the right side
        sxr = sx + sw_ / 2
        dxr = dx + dw / 2
        off = 26.0 + 14.0 * loop_idx
        mx = max(sxr, dxr) + off
        pts = [(sxr, sy), (mx, sy), (mx, dy), (dxr, dy)]
        label_pt = (mx, (sy + dy) / 2)
        return pts, label_pt

    # backward edge (or same-position): loop around the right side
    sxr = sx + sw_ / 2
    side_x = sxr + 30.0 + 16.0 * loop_idx
    top_y = dst_top - max(24.0, rank_spacing * 0.45)
    pts = [(sxr, sy), (side_x, sy), (side_x, top_y), (dx, top_y), (dx, dst_top)]
    label_pt = (side_x, (sy + top_y) / 2)
    return pts, label_pt


def _route_self_loop(sc: tuple[float, float], ss: tuple[float, float]) -> tuple[list[Pt], Pt]:
    sx, sy = sc
    w, h = ss
    bottom = sy + h / 2
    out_x = sx + w / 2 + 26.0
    pts = [
        (sx, bottom),
        (sx, bottom + 20.0),
        (out_x, bottom + 20.0),
        (out_x, sy),
        (sx + w / 2, sy),
    ]
    label_pt = (out_x, sy + h / 2 + 6.0)
    return pts, label_pt


def render_flowchart(statements: Sequence[str], theme: Theme, config: dict[str, Any]) -> Canvas:
    """Lay out and render a flowchart definition into a :class:`Canvas`."""
    ir = parse_flowchart(statements)
    fc_cfg = config.get("flowchart") or {}
    node_spacing = float(fc_cfg.get("nodeSpacing", 45))
    rank_spacing = float(fc_cfg.get("rankSpacing", 70))

    sizes: dict[str, tuple[float, float]] = {}
    label_lines: dict[str, list[str]] = {}
    for nid, node in ir.nodes.items():
        w, h, lines = _flow_node_size(node, theme)
        sizes[nid] = (w, h)
        label_lines[nid] = lines

    group_of = {nid: node.group for nid, node in ir.nodes.items() if node.group}
    positions, ranks = layered_layout(
        sizes,
        [(e.src, e.dst) for e in ir.edges],
        node_spacing=node_spacing,
        rank_spacing=rank_spacing,
        group_of=group_of,
    )

    node_rects = {
        nid: (cx - sizes[nid][0] / 2, cy - sizes[nid][1] / 2, sizes[nid][0], sizes[nid][1])
        for nid, (cx, cy) in positions.items()
    }

    prims: list[Prim] = []
    heads: list[tuple[list[Pt], str, str, str | None]] = []  # pts, head, tail, color

    # --- groups (outer first) --------------------------------------------
    boxes = _group_boxes(ir.groups, node_rects, lambda nid: ir.nodes[nid].group)

    def depth_of(gid: str) -> int:
        seen = set()
        d = 0
        while gid in ir.groups and ir.groups[gid].parent is not None and gid not in seen:
            seen.add(gid)
            gid = ir.groups[gid].parent  # type: ignore[assignment]
            d += 1
        return d

    for gid in sorted(ir.groups, key=lambda g: depth_of(g)):
        gx, gy, gw, gh = boxes[gid]
        if gw <= 0:
            continue
        grp = ir.groups[gid]
        prims.append(
            Prim(
                kind="rect",
                x=gx,
                y=gy,
                w=gw,
                h=gh,
                rx=6.0,
                fill=theme.cluster_fill,
                stroke=theme.cluster_stroke,
                stroke_width=1.0,
                cls="cluster",
            )
        )
        t_lines = fit_label(grp.title, theme.font_size - 3, gw - 12)
        prims.append(left_text(gx + 8, gy + 7, t_lines, theme.font_size - 3, theme.cluster_text, cls="cluster-label"))

    # --- edges -------------------------------------------------------------
    loop_counter: dict[tuple[str, str, str], int] = {}
    for idx, edge in enumerate(ir.edges):
        if edge.src not in positions or edge.dst not in positions:
            continue
        est = _flow_edge_style(edge, ir, theme, idx)
        color = est["color"] or theme.edge_stroke
        key3 = (edge.src, edge.dst, edge.label)
        loop_idx = loop_counter.get(key3, 0)
        loop_counter[key3] = loop_idx + 1

        if edge.src == edge.dst:
            pts, label_pt = _route_self_loop(positions[edge.src], sizes[edge.src])
        else:
            pts, label_pt = _route_flow_edge(
                positions[edge.src],
                sizes[edge.src],
                positions[edge.dst],
                sizes[edge.dst],
                ranks[edge.src],
                ranks[edge.dst],
                rank_spacing,
                loop_idx,
            )

        prims.append(
            Prim(kind="polyline", pts=tuple(pts), stroke=color, stroke_width=est["sw"], dash=est["dash"], cls="edge")
        )
        heads.append((pts, edge.head, edge.tail, color))

        if edge.label:
            lsize = theme.font_size - 3
            l_lines = fit_label(edge.label, lsize, 170)
            lw, lh = lines_size(l_lines, lsize)
            prims.append(
                Prim(
                    kind="rect",
                    x=label_pt[0] - lw / 2 - 4,
                    y=label_pt[1] - lh / 2 - 3,
                    w=lw + 8,
                    h=lh + 6,
                    rx=3.0,
                    fill=theme.edge_label_bg,
                    stroke=None,
                    cls="edgeLabel",
                )
            )
            prims.append(
                center_text(label_pt[0], label_pt[1], l_lines, lsize, est["text"] or theme.edge_text, cls="edgeLabel")
            )

    # --- nodes --------------------------------------------------------------
    for nid, node in ir.nodes.items():
        if nid not in positions:
            continue
        cx, cy = positions[nid]
        w, h = sizes[nid]
        st = _flow_node_style(node, ir, theme)
        prims.extend(shape_prims(node.shape, cx, cy, w, h, st["fill"], st["stroke"], st["sw"], st["dash"], cls="node"))
        prims.append(
            center_text(cx, cy, label_lines[nid], theme.font_size - 2, st["text"] or theme.node_text, cls="label")
        )

    # --- direction transform + arrow heads ---------------------------------
    bx, by, bw, bh = prim_bbox(prims)
    prims = shift_prims(prims, -bx, -by)
    prims = transform_prims(prims, ir.direction, bw, bh)
    fw, fh = transformed_size(ir.direction, bw, bh)

    for pts, head, tail, color in heads:
        pts = [(x - bx, y - by) for (x, y) in pts]
        pts = _transform_pts(pts, ir.direction, bw, bh)
        if head == "cross":
            if len(pts) >= 2:
                prims.extend(crosshead(pts[-2], pts[-1], color or "#333"))
        else:
            hp = arrowhead(pts[-2], pts[-1], head, color or "#333")
            if hp:
                prims.append(hp)
        if tail == "cross":
            if len(pts) >= 2:
                prims.extend(crosshead(pts[1], pts[0], color or "#333"))
        elif tail != "none":
            tp = arrowhead(pts[1], pts[0], tail, color or "#333")
            if tp:
                prims.append(tp)

    return Canvas(w=math.ceil(fw), h=math.ceil(fh), prims=prims)


def _transform_pts(pts: list[Pt], direction: str, w: float, h: float) -> list[Pt]:
    """Transform raw points for the requested direction."""
    direction = (direction or "TB").upper()
    if direction in ("TB", "TD"):
        return pts
    out: list[Pt] = []
    for x, y in pts:
        if direction == "BT":
            out.append((x, h - y))
        elif direction == "LR":
            out.append((y, x))
        else:  # RL
            out.append((h - y, x))
    return out


# --------------------------------------------------------------------------
# sequence diagram parser + renderer
# --------------------------------------------------------------------------


@dataclass
class SeqParticipant:
    id: str
    label: str
    actor: bool = False


@dataclass
class SeqMessage:
    src: str
    dst: str
    arrow: str
    label: str
    src_flag: str = ""
    dst_flag: str = ""


@dataclass
class SeqNote:
    placement: str  # left | right | over
    targets: list[str]
    text: str


@dataclass
class SeqActivation:
    target: str
    action: str  # activate | deactivate


@dataclass
class SeqBlock:
    kind: str
    label: str
    children: list = field(default_factory=list)


@dataclass
class SeqElse:
    label: str


@dataclass
class SeqIR:
    participants: list[SeqParticipant] = field(default_factory=list)
    items: list = field(default_factory=list)
    autonumber: bool = False
    autonumber_start: int = 1
    title: str | None = None


_SEQ_CREATE_RE = re.compile(r"^create\s+(participant|actor)\s+([\w-]+)(?:\s+as\s+(.+))?$", re.I)
_SEQ_PART_RE = re.compile(r"^(participant|actor)\s+([\w-]+)(?:\s+as\s+(.+))?$", re.I)
_SEQ_MSG_RE = re.compile(r"^([\w-]+)\s*(-->>|-->|->>|->|--x|-x|--\)|-\))\s*([+-]*[\w-]+)\s*:\s*(.*)$", re.S)
_SEQ_NOTE_RE = re.compile(r"^note\s+(left of|right of|over)\s+([\w\s,-]+?)\s*:\s*(.*)$", re.I | re.S)
_SEQ_BLOCK_RE = re.compile(r"^(loop|alt|opt|par|and|critical|option|break|rect|box)\s*(.*)$", re.I)
_SEQ_ELSE_RE = re.compile(r"^else\s*(.*)$", re.I)
_SEQ_END_RE = re.compile(r"^end$", re.I)
_SEQ_ACT_RE = re.compile(r"^(activate|deactivate)\s+([\w-]+)$", re.I)
_SEQ_AUTO_RE = re.compile(r"^autonumber\s*(off)?\s*(\d+)?\s*$", re.I)
_SEQ_TITLE_RE = re.compile(r"^title\s+(.*)$", re.I)


def _seq_flags(tok: str) -> tuple[str, str]:
    """Strip leading/trailing ``+``/``-`` activation flags from a participant."""
    flags = ""
    while tok and tok[-1] in "+-":
        flags = tok[-1] + flags
        tok = tok[:-1]
    lead = ""
    while tok and tok[0] in "+-":
        lead = lead + tok[0]
        tok = tok[1:]
    return tok, lead + flags


def parse_sequence(statements: Sequence[str]) -> SeqIR:
    """Parse sequence diagram statements into a :class:`SeqIR`."""
    ir = SeqIR()
    stack: list[SeqBlock] = []

    def cur() -> list:
        return stack[-1].children if stack else ir.items

    def find_part(pid: str) -> SeqParticipant | None:
        for p in ir.participants:
            if p.id == pid:
                return p
        return None

    for raw in statements:
        stmt = raw.strip()
        if not stmt:
            continue
        low = stmt.lower()

        m = _SEQ_CREATE_RE.match(stmt)
        if m:
            pid = m.group(2)
            if not find_part(pid):
                ir.participants.append(
                    SeqParticipant(pid, m.group(3).strip() if m.group(3) else pid, m.group(1).lower() == "actor")
                )
            continue

        if low.startswith(("participant ", "actor ")):
            m = _SEQ_PART_RE.match(stmt)
            if m:
                pid = m.group(2)
                existing = find_part(pid)
                if existing is None:
                    ir.participants.append(
                        SeqParticipant(pid, m.group(3).strip() if m.group(3) else pid, m.group(1).lower() == "actor")
                    )
                elif m.group(3):
                    existing.label = m.group(3).strip()
                continue

        m = _SEQ_AUTO_RE.match(stmt)
        if m:
            if m.group(1) and m.group(1).lower() == "off":
                ir.autonumber = False
            else:
                ir.autonumber = True
                if m.group(2):
                    ir.autonumber_start = int(m.group(2))
            continue

        if low.startswith("title ") and _SEQ_TITLE_RE.match(stmt):
            ir.title = _SEQ_TITLE_RE.match(stmt).group(1).strip()  # type: ignore[union-attr]
            continue

        if _SEQ_END_RE.match(stmt):
            if stack:
                stack.pop()
            continue

        m = _SEQ_ELSE_RE.match(stmt)
        if m:
            cur().append(SeqElse(m.group(1).strip()))
            continue

        m = _SEQ_BLOCK_RE.match(stmt)
        if m:
            blk = SeqBlock(kind=m.group(1).lower(), label=(m.group(2) or "").strip())
            cur().append(blk)
            stack.append(blk)
            continue

        m = _SEQ_ACT_RE.match(stmt)
        if m:
            cur().append(SeqActivation(m.group(2), m.group(1).lower()))
            continue

        m = _SEQ_NOTE_RE.match(stmt)
        if m:
            placement = {"left of": "left", "right of": "right", "over": "over"}[m.group(1).lower()]
            targets = [t.strip() for t in re.split(r"[,\s]+", m.group(2)) if t.strip()]
            cur().append(SeqNote(placement, targets, m.group(3).strip()))
            continue

        m = _SEQ_MSG_RE.match(stmt)
        if m:
            src, sflag = _seq_flags(m.group(1))
            dst, dflag = _seq_flags(m.group(3))
            for pid in (src, dst):
                if pid and not find_part(pid):
                    ir.participants.append(SeqParticipant(pid, pid))
            cur().append(SeqMessage(src, dst, m.group(2), m.group(4).strip(), sflag, dflag))
            continue

        # unknown statement: ignore silently (lenient parsing)

    return ir


# ---- sequence rendering ---------------------------------------------------


class _SeqState:
    """Mutable layout state while walking sequence items."""

    def __init__(self, ir: SeqIR, theme: Theme, config: dict[str, Any]) -> None:
        self.ir = ir
        self.theme = theme
        cfg = config.get("sequence") or {}
        self.actor_margin = float(cfg.get("actorMargin", 50))
        self.message_margin = float(cfg.get("messageMargin", 35))
        self.mirror = bool(cfg.get("mirrorActors", True))
        self.size = theme.font_size - 2
        self.cx: dict[str, float] = {}
        self.pw: dict[str, float] = {}
        self.ph: dict[str, float] = {}
        self.top_y = 0.0
        self.mirror_top = 0.0
        self.bottom = 0.0
        self.y = 0.0
        self.auto_n = (ir.autonumber_start - 1) if ir.autonumber else 0
        self.activations: dict[str, list[float]] = {}
        self.frames: list[Prim] = []
        self.bars: list[Prim] = []
        self.lifelines: list[Prim] = []
        self.notes: list[Prim] = []
        self.messages: list[Prim] = []
        self.actors: list[Prim] = []


def render_sequence(statements: Sequence[str], theme: Theme, config: dict[str, Any]) -> Canvas:
    """Lay out and render a sequence diagram into a :class:`Canvas`."""
    ir = parse_sequence(statements)
    st = _SeqState(ir, theme, config)

    if not ir.participants:
        return Canvas(w=100, h=60, prims=[])

    # ---- participant geometry --------------------------------------------
    title_h = 34 if ir.title else 0
    for p in ir.participants:
        lines = fit_label(p.label, st.size, 150)
        lw, _lh = lines_size(lines, st.size)
        st.pw[p.id] = max(lw + 30, 120)
        min_h = 64 if p.actor else 38  # stick figures need extra room
        st.ph[p.id] = max(len(lines) * st.size * LINE_H + 16, min_h)

    st.cx[ir.participants[0].id] = st.pw[ir.participants[0].id] / 2
    for prev, p in zip(ir.participants, ir.participants[1:]):
        st.cx[p.id] = st.cx[prev.id] + st.pw[prev.id] / 2 + st.actor_margin + st.pw[p.id] / 2

    max_ph = max(st.ph.values())
    st.top_y = title_h + 14
    st.y = st.top_y + max_ph + 26

    _seq_walk(st, ir.items)
    st.bottom = st.y + 8
    st.mirror_top = st.bottom
    if st.mirror:
        st.mirror_top = st.bottom
        st.bottom += max_ph + 8
    for pid, stack in st.activations.items():
        for i, y0 in enumerate(stack):
            _seq_emit_bar(st, pid, y0, st.bottom - max_ph - 12 if st.mirror else st.bottom - 6, i + 1)

    # ---- emit -------------------------------------------------------------
    prims: list[Prim] = []
    if ir.title:
        prims.append(
            center_text(
                (st.cx[ir.participants[0].id] + st.cx[ir.participants[-1].id]) / 2,
                title_h / 2 + 2,
                fit_label(ir.title, st.size + 2, 420),
                st.size + 2,
                theme.text_color,
                bold=True,
            )
        )
    for p in ir.participants:
        st.lifelines.append(
            Prim(
                kind="polyline",
                pts=((st.cx[p.id], st.top_y + st.ph[p.id]), (st.cx[p.id], st.mirror_top)),
                stroke=theme.signal_color,
                stroke_width=1.0,
                dash=(4, 4),
                cls="actor-line",
            )
        )
    for p in ir.participants:
        _seq_actor(st, p, st.top_y)
        if st.mirror:
            _seq_actor(st, p, st.mirror_top)

    prims.extend(st.frames)
    prims.extend(st.lifelines)
    prims.extend(st.bars)
    prims.extend(st.notes)
    prims.extend(st.messages)
    prims.extend(st.actors)

    bx, by, bw, bh = prim_bbox(prims)
    prims = shift_prims(prims, -bx + 4, -by + 4)
    return Canvas(w=math.ceil(bw + 8), h=math.ceil(bh + 8), prims=prims)


def _seq_walk(st: _SeqState, items: list) -> None:
    for item in items:
        if isinstance(item, SeqMessage):
            _seq_message(st, item)
        elif isinstance(item, SeqNote):
            _seq_note(st, item)
        elif isinstance(item, SeqActivation):
            _seq_activation(st, item)
        elif isinstance(item, SeqBlock):
            _seq_block(st, item)
        # SeqElse is handled inside _seq_block


def _seq_targets(item: Any) -> set[str]:
    if isinstance(item, SeqMessage):
        return {item.src, item.dst}
    if isinstance(item, SeqNote):
        return set(item.targets)
    if isinstance(item, SeqBlock):
        out: set[str] = set()
        for child in item.children:
            out |= _seq_targets(child)
        return out
    return set()


def _seq_emit_bar(st: _SeqState, pid: str, y0: float, y1: float, depth: int) -> None:
    x = st.cx[pid] - 5 + (depth - 1) * 4
    st.bars.append(
        Prim(
            kind="rect",
            x=x,
            y=y0,
            w=10,
            h=max(1.0, y1 - y0),
            rx=1,
            fill=st.theme.activation_fill,
            stroke=st.theme.actor_stroke,
            stroke_width=1.0,
            cls="activation",
        )
    )


def _seq_open(st: _SeqState, pid: str, y: float) -> None:
    st.activations.setdefault(pid, []).append(y)


def _seq_close(st: _SeqState, pid: str, y: float) -> None:
    stack = st.activations.get(pid)
    if stack:
        y0 = stack.pop()
        _seq_emit_bar(st, pid, y0, y, len(stack) + 1)


def _seq_activation(st: _SeqState, item: SeqActivation) -> None:
    if item.target not in st.cx:
        return
    if item.action == "activate":
        _seq_open(st, item.target, st.y)
    else:
        _seq_close(st, item.target, st.y)


def _seq_block(st: _SeqState, blk: SeqBlock) -> None:
    targets = {t for t in _seq_targets(blk) if t in st.cx}

    if blk.kind == "box":
        y_start = st.top_y - 8
        _seq_walk(st, blk.children)
        y_end = st.y + 4
        if targets:
            ids = sorted(targets, key=lambda t: st.cx[t])
            x0 = st.cx[ids[0]] - st.pw[ids[0]] / 2 - 10
            x1 = st.cx[ids[-1]] + st.pw[ids[-1]] / 2 + 10
            label = blk.label
            if label and label.lower() != "transparent":
                st.frames.append(
                    Prim(
                        kind="rect",
                        x=x0,
                        y=y_start,
                        w=x1 - x0,
                        h=y_end - y_start,
                        rx=4,
                        fill=st.theme.label_box_fill,
                        stroke=st.theme.label_box_stroke,
                        stroke_width=1.0,
                        opacity=0.3,
                        cls="actor-box",
                    )
                )
                st.frames.append(
                    left_text(
                        x0 + 6,
                        y_start + 4,
                        fit_label(label, st.size - 2, x1 - x0 - 12),
                        st.size - 2,
                        st.theme.actor_text,
                        bold=True,
                    )
                )
        return

    if blk.kind == "rect":
        y_start = st.y
        _seq_walk(st, blk.children)
        y_end = st.y
        if targets:
            ids = sorted(targets, key=lambda t: st.cx[t])
            x0 = st.cx[ids[0]] - st.pw[ids[0]] / 2 - 12
            x1 = st.cx[ids[-1]] + st.pw[ids[-1]] / 2 + 12
            fill = blk.label or "rgba(0, 0, 0, 0.2)"
            st.frames.append(
                Prim(
                    kind="rect",
                    x=x0,
                    y=y_start - 4,
                    w=x1 - x0,
                    h=y_end - y_start + 8,
                    rx=2,
                    fill=fill,
                    stroke=None,
                    opacity=0.3,
                    cls="rect",
                )
            )
        return

    # loop / alt / opt / par / critical / break frames
    y_start = st.y
    x0 = x1 = 0.0
    if targets:
        ids = sorted(targets, key=lambda t: st.cx[t])
        x0 = st.cx[ids[0]] - st.pw[ids[0]] / 2 - 14
        x1 = st.cx[ids[-1]] + st.pw[ids[-1]] / 2 + 14
    else:
        all_ids = sorted(st.cx, key=lambda t: st.cx[t])
        x0 = st.cx[all_ids[0]] - 40
        x1 = st.cx[all_ids[-1]] + 40

    for child in blk.children:
        if isinstance(child, SeqElse):
            if child.label:
                lbl = fit_label(child.label, st.size - 3, x1 - x0 - 12)
                lw, lh = lines_size(lbl, st.size - 3)
                st.frames.append(
                    Prim(
                        kind="rect",
                        x=x0 + 6,
                        y=st.y,
                        w=lw + 10,
                        h=lh + 6,
                        rx=2,
                        fill=st.theme.label_box_fill,
                        stroke=st.theme.label_box_stroke,
                        stroke_width=1.0,
                        cls="labelBox",
                    )
                )
                st.frames.append(left_text(x0 + 11, st.y + 3, lbl, st.size - 3, st.theme.loop_text))
                st.y += lh + 10
            st.frames.append(
                Prim(
                    kind="polyline",
                    pts=((x0, st.y), (x1, st.y)),
                    stroke=st.theme.label_box_stroke,
                    stroke_width=1.0,
                    dash=(4, 3),
                    cls="loopLine",
                )
            )
            st.y += 12
        else:
            _seq_walk(st, [child])
    y_end = st.y

    kind_lbl = blk.kind
    text = blk.label
    lbl_text = f"[{kind_lbl}] {text}".strip() if text else f"[{kind_lbl}]"
    lbl_lines = fit_label(lbl_text, st.size - 3, max(60.0, x1 - x0 - 12))
    lw, lh = lines_size(lbl_lines, st.size - 3)
    st.frames.append(
        Prim(
            kind="rect",
            x=x0,
            y=y_start - 6,
            w=x1 - x0,
            h=y_end - y_start + 12,
            rx=2,
            fill=None,
            stroke=st.theme.label_box_stroke,
            stroke_width=1.2,
            cls="loopLine",
        )
    )
    st.frames.append(
        Prim(
            kind="rect",
            x=x0,
            y=y_start - 6,
            w=lw + 14,
            h=lh + 8,
            rx=2,
            fill=st.theme.label_box_fill,
            stroke=st.theme.label_box_stroke,
            stroke_width=1.0,
            cls="labelBox",
        )
    )
    st.frames.append(left_text(x0 + 7, y_start - 2, lbl_lines, st.size - 3, st.theme.loop_text))
    st.y = y_end + 6


def _seq_actor(st: _SeqState, p: SeqParticipant, y: float) -> None:
    cx = st.cx[p.id]
    w = st.pw[p.id]
    h = st.ph[p.id]
    lines = fit_label(p.label, st.size, w - 12)
    if p.actor:
        head_r = 6.0
        top = y + 2
        st.actors.append(
            Prim(
                kind="ellipse",
                x=cx,
                y=top + head_r,
                rx=head_r,
                ry=head_r,
                fill=st.theme.actor_fill,
                stroke=st.theme.actor_stroke,
                stroke_width=1.2,
                cls="actor",
            )
        )
        body_top = top + 2 * head_r
        body_bot = body_top + 14
        st.actors.append(
            Prim(
                kind="polyline",
                pts=((cx, body_top), (cx, body_bot)),
                stroke=st.theme.actor_stroke,
                stroke_width=1.4,
                cls="actor",
            )
        )
        st.actors.append(
            Prim(
                kind="polyline",
                pts=((cx - 9, body_top + 4), (cx, body_top + 1), (cx + 9, body_top + 4)),
                stroke=st.theme.actor_stroke,
                stroke_width=1.4,
                cls="actor",
            )
        )
        st.actors.append(
            Prim(
                kind="polyline",
                pts=((cx - 8, body_bot + 10), (cx, body_bot), (cx + 8, body_bot + 10)),
                stroke=st.theme.actor_stroke,
                stroke_width=1.4,
                cls="actor",
            )
        )
        st.actors.append(center_text(cx, body_bot + 26, lines, st.size, st.theme.actor_text, cls="actor"))
    else:
        st.actors.append(
            Prim(
                kind="rect",
                x=cx - w / 2,
                y=y,
                w=w,
                h=h,
                rx=2,
                fill=st.theme.actor_fill,
                stroke=st.theme.actor_stroke,
                stroke_width=1.2,
                cls="actor",
            )
        )
        st.actors.append(center_text(cx, y + h / 2, lines, st.size, st.theme.actor_text, cls="actor"))


def _seq_message(st: _SeqState, msg: SeqMessage) -> None:
    theme = st.theme
    label = msg.label or ""
    if st.ir.autonumber:
        st.auto_n += 1
        label = f"{st.auto_n}: {label}"
    dashed = msg.arrow.startswith("--")

    if msg.src == msg.dst:
        cx = st.cx[msg.src]
        lsize = st.size - 1
        lines = fit_label(label, lsize, 200)
        lw, lh = lines_size(lines, lsize)
        y0 = st.y + lh + 4
        pts = ((cx, y0), (cx + 40, y0), (cx + 40, y0 + 22), (cx, y0 + 22))
        st.messages.append(
            Prim(
                kind="polyline",
                pts=pts,
                stroke=theme.signal_color,
                stroke_width=1.5,
                dash=(6, 4) if dashed else None,
                cls="message",
            )
        )
        if msg.arrow.endswith("x"):
            st.messages.extend(crosshead(pts[2], pts[3], theme.signal_color, 9))
        else:
            head = "arrow_open" if msg.arrow.endswith(")") else "arrow"
            hp = arrowhead(pts[2], pts[3], head, theme.signal_color, 9)
            if hp:
                st.messages.append(hp)
        st.messages.append(
            Prim(
                kind="text",
                x=cx + 46,
                y=st.y,
                lines=tuple(lines),
                size=lsize,
                anchor="start",
                color=theme.signal_text,
                cls="messageText",
            )
        )
        st.y = y0 + 22 + st.message_margin
        _seq_flags_apply(st, msg, y0 + 22)
        return

    src_x, dst_x = _seq_endpoints(st, msg)
    lsize = st.size - 1
    lines = fit_label(label, lsize, 260)
    lw, lh = lines_size(lines, lsize)
    arrow_y = st.y + lh + 4
    st.messages.append(
        Prim(
            kind="polyline",
            pts=((src_x, arrow_y), (dst_x, arrow_y)),
            stroke=theme.signal_color,
            stroke_width=1.5,
            dash=(6, 4) if dashed else None,
            cls="message",
        )
    )
    p_from = (src_x, arrow_y)
    p_to = (dst_x, arrow_y)
    if msg.arrow.endswith(")"):
        hp = arrowhead(p_from, p_to, "arrow_open", theme.signal_color, 10)
    elif msg.arrow.endswith("x"):
        st.messages.extend(crosshead(p_from, p_to, theme.signal_color, 10))
        hp = None
    else:
        hp = arrowhead(p_from, p_to, "arrow", theme.signal_color, 10)
    if hp:
        st.messages.append(hp)
    mid = (src_x + dst_x) / 2
    st.messages.append(
        Prim(
            kind="text",
            x=mid,
            y=st.y,
            lines=tuple(lines),
            size=lsize,
            anchor="middle",
            color=theme.signal_text,
            cls="messageText",
        )
    )
    st.y = arrow_y + 10 + st.message_margin
    _seq_flags_apply(st, msg, arrow_y)


def _seq_endpoints(st: _SeqState, msg: SeqMessage) -> tuple[float, float]:
    def edge_x(pid: str, other: str) -> float:
        depth = len(st.activations.get(pid, []))
        base = st.cx[pid]
        if depth == 0:
            return base
        right = st.cx[other] > base if other in st.cx else True
        if right:
            return base + 5 + (depth - 1) * 4
        return base - 5 + (depth - 1) * 4

    src_x = edge_x(msg.src, msg.dst)
    dst_x = edge_x(msg.dst, msg.src)
    if abs(src_x - dst_x) < 2:
        dst_x = src_x + (2 if dst_x >= src_x else -2)
    return src_x, dst_x


def _seq_flags_apply(st: _SeqState, msg: SeqMessage, arrow_y: float) -> None:
    for pid, flag in ((msg.dst, msg.dst_flag), (msg.src, msg.src_flag)):
        if pid not in st.cx:
            continue
        if "+" in flag:
            _seq_open(st, pid, arrow_y)
        elif "-" in flag:
            _seq_close(st, pid, arrow_y)


def _seq_note(st: _SeqState, note: SeqNote) -> None:
    theme = st.theme
    targets = [t for t in note.targets if t in st.cx]
    if not targets:
        return
    lsize = st.size - 2
    lines = fit_label(note.text, lsize, 200)
    lw, lh = lines_size(lines, lsize)
    w = lw + 20
    h = lh + 20
    if note.placement == "over":
        xs = [st.cx[t] for t in targets]
        center = (min(xs) + max(xs)) / 2
        x = center - w / 2
    elif note.placement == "left":
        x = st.cx[targets[0]] - st.pw[targets[0]] / 2 - w - 10
    else:
        x = st.cx[targets[0]] + st.pw[targets[0]] / 2 + 10
    st.notes.append(
        Prim(
            kind="rect",
            x=x,
            y=st.y,
            w=w,
            h=h,
            rx=2,
            fill=theme.note_fill,
            stroke=theme.note_stroke,
            stroke_width=1.0,
            cls="note",
        )
    )
    st.notes.append(center_text(x + w / 2, st.y + h / 2, lines, lsize, theme.note_text, cls="noteText"))
    st.y += h + 28


# --------------------------------------------------------------------------
# class diagram parser + renderer
# --------------------------------------------------------------------------


@dataclass
class ClsClass:
    name: str
    annotations: list[str] = field(default_factory=list)
    members: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class ClsRel:
    left: str
    right: str
    op: str
    left_card: str | None = None
    right_card: str | None = None
    label: str = ""


@dataclass
class ClassIR:
    direction: str = "TB"
    classes: dict[str, ClsClass] = field(default_factory=dict)
    rels: list[ClsRel] = field(default_factory=list)


_CLS_OPS = [
    "<|--",
    "--|>",
    "<|..",
    "..|>",
    "*--",
    "--*",
    "*..",
    "..*",
    "o--",
    "--o",
    "o..",
    "..o",
    "-->",
    "<--",
    "..>",
    "<..",
    "--",
    "..",
]
_CLS_REL_RE = re.compile(
    r'^\s*(?:"([^"]*)")?\s*([\w~$]+)\s*(?:"([^"]*)")?\s*('
    + "|".join(re.escape(op) for op in _CLS_OPS)
    + r')\s*(?:"([^"]*)")?\s*([\w~$]+)\s*(?:"([^"]*)")?\s*(?::\s*(.*))?$'
)
_CLS_NOTE_RE = re.compile(r'^note\s+for\s+([\w~$]+)\s+"(.*)"$', re.I)


def _clean_cls_name(name: str) -> str:
    """Convert mermaid generics ``List~T~`` to ``List<T>``."""
    parts = name.split("~")
    if len(parts) == 1:
        return name
    out = parts[0]
    for i, part in enumerate(parts[1:]):
        out += ("<" if i % 2 == 0 else ">") + part
    return out


def parse_class(statements: Sequence[str]) -> ClassIR:
    """Parse class diagram statements into a :class:`ClassIR`."""
    ir = ClassIR()
    open_class: ClsClass | None = None
    composite_stack: list[tuple[str, ClsClass]] = []

    for raw in statements:
        stmt = raw.strip()
        if not stmt:
            continue
        low = stmt.lower()

        m = re.match(r"^classDiagram(-v2)?$", stmt, re.I)
        if m and not ir.classes:
            continue

        if low.startswith("direction "):
            d = stmt.split(None, 1)[1].strip().upper()
            if d in ("TB", "TD", "BT", "LR", "RL"):
                ir.direction = d
            continue

        if open_class is not None:
            if stmt == "}":
                open_class = None
                continue
            if stmt.endswith("}") and len(stmt) > 1:
                body = stmt[:-1].strip()
                if body:
                    _cls_add_member(open_class, body)
                open_class = None
                continue
            _cls_add_member(open_class, stmt)
            continue

        m = re.match(r"^class\s+([\w~$]+)\s*(?:<<(.+)>>)?\s*(\{)?$", stmt)
        if m:
            name = m.group(1)
            cls = ir.classes.get(name)
            if cls is None:
                cls = ClsClass(name=_clean_cls_name(name))
                ir.classes[name] = cls
            if m.group(2):
                cls.annotations.append(m.group(2).strip())
            if m.group(3):
                open_class = cls
            continue

        m = _CLS_NOTE_RE.match(stmt)
        if m and m.group(1) in ir.classes:
            ir.classes[m.group(1)].note = m.group(2)
            continue

        m = _CLS_REL_RE.match(stmt)
        if m:
            left, right = m.group(2), m.group(6)
            for nm in (left, right):
                if nm not in ir.classes:
                    ir.classes[nm] = ClsClass(name=_clean_cls_name(nm))
            ir.rels.append(
                ClsRel(
                    left=left,
                    right=right,
                    op=m.group(4),
                    left_card=m.group(1) or m.group(3),
                    right_card=m.group(5) or m.group(7),
                    label=(m.group(8) or "").strip(),
                )
            )
            continue

        m = re.match(r"^([\w~$]+)\s*:\s*(.+)$", stmt)
        if m and not re.search(r"[<>*o]|--|\.\.", stmt.split(":")[0]):
            name = m.group(1)
            cls = ir.classes.get(name)
            if cls is None:
                cls = ClsClass(name=_clean_cls_name(name))
                ir.classes[name] = cls
            _cls_add_member(cls, m.group(2).strip())
            continue

    return ir


def _cls_add_member(cls: ClsClass, text: str) -> None:
    m = re.match(r"^<<(.+)>>$", text.strip())
    if m:
        cls.annotations.append(m.group(1).strip())
    else:
        cls.members.append(text.strip())


# ---- class rendering ------------------------------------------------------


def _cls_node_size(cls: ClsClass, theme: Theme) -> tuple[float, float]:
    tsize = theme.font_size - 2
    msize = theme.font_size - 3
    title_w = text_width(cls.name, tsize, bold=True)
    anno_lines = [a for a in cls.annotations]
    anno_w = max((text_width(f"\u00ab{a}\u00bb", msize) for a in anno_lines), default=0.0)
    member_w = max((text_width(mm, msize) for mm in cls.members), default=0.0)
    w = max(title_w, anno_w, member_w) + 20
    title_h = 28
    anno_h = len(anno_lines) * msize * LINE_H
    member_h = len(cls.members) * msize * LINE_H
    if cls.members:
        member_h += 8  # divider padding
    h = title_h + anno_h + member_h + 8
    return (max(w, 70.0), max(h, 44.0))


def _cls_rel_style(rel: ClsRel) -> tuple[str, str, str, bool]:
    """Return ``(head_at_left, head_at_right, kind, dashed)``."""
    op = rel.op
    dashed = ".." in op
    head_left = "none"
    head_right = "none"
    if op in ("<|--", "--|>"):
        head_left, head_right = ("triangle_hollow", "none") if op == "<|--" else ("none", "triangle_hollow")
    elif op in ("<|..", "..|>"):
        head_left, head_right = ("triangle_hollow", "none") if op == "<|.." else ("none", "triangle_hollow")
    elif op in ("*--", "--*", "*..", "..*"):
        head_left, head_right = ("diamond_fill", "none") if op.startswith("*") else ("none", "diamond_fill")
    elif op in ("o--", "--o", "o..", "..o"):
        head_left, head_right = ("diamond_hollow", "none") if op.startswith("o") else ("none", "diamond_hollow")
    elif op in ("-->", "<--"):
        head_left, head_right = ("arrow", "none") if op == "<--" else ("none", "arrow")
    elif op in ("..>", "<.."):
        head_left, head_right = ("arrow", "none") if op == "<.." else ("none", "arrow")
    return head_left, head_right, op, dashed


def render_class(statements: Sequence[str], theme: Theme, config: dict[str, Any]) -> Canvas:
    """Lay out and render a class diagram into a :class:`Canvas`."""
    ir = parse_class(statements)
    if not ir.classes:
        return Canvas(w=100, h=60, prims=[])

    sizes: dict[str, tuple[float, float]] = {}
    for cid, cls in ir.classes.items():
        sizes[cid] = _cls_node_size(cls, theme)

    positions, _ranks = layered_layout(
        sizes,
        [(r.left, r.right) for r in ir.rels],
        node_spacing=60,
        rank_spacing=80,
    )

    node_rects = {
        cid: (cx - sizes[cid][0] / 2, cy - sizes[cid][1] / 2, sizes[cid][0], sizes[cid][1])
        for cid, (cx, cy) in positions.items()
    }

    prims: list[Prim] = []
    heads: list[tuple[list[Pt], str, str]] = []
    end_labels: list[tuple[Pt, str, str]] = []

    # relationships first (nodes drawn over edge ends)
    for rel in ir.rels:
        if rel.left not in positions or rel.right not in positions:
            continue
        head_left, head_right, _op, dashed = _cls_rel_style(rel)
        lx, ly = positions[rel.left]
        rx_, ry_ = positions[rel.right]
        lw, lh = sizes[rel.left]
        rw, rh = sizes[rel.right]
        # connect side-to-side / top-bottom depending on relative position
        if abs(lx - rx_) < 6:
            if ly < ry_:
                pts = [(lx, ly + lh / 2), (rx_, ry_ - rh / 2)]
            else:
                pts = [(lx, ly - lh / 2), (rx_, ry_ + rh / 2)]
        elif ly == ry_:
            if lx < rx_:
                pts = [(lx + lw / 2, ly), (rx_ - rw / 2, ry_)]
            else:
                pts = [(lx - lw / 2, ly), (rx_ + rw / 2, ry_)]
        else:
            midx, midy = (lx + rx_) / 2, (ly + ry_) / 2
            if abs(ly - ry_) > abs(lx - rx_):
                pts = [
                    (lx, ly + (lh / 2 if ry_ > ly else -lh / 2)),
                    (lx, midy),
                    (rx_, midy),
                    (rx_, ry_ - (rh / 2 if ry_ > ly else -rh / 2)),
                ]
            else:
                pts = [
                    (lx + (lw / 2 if rx_ > lx else -lw / 2), ly),
                    (midx, ly),
                    (midx, ry_),
                    (rx_ - (rw / 2 if rx_ > lx else -rw / 2), ry_),
                ]
        prims.append(
            Prim(
                kind="polyline",
                pts=tuple(pts),
                stroke=theme.edge_stroke,
                stroke_width=1.4,
                dash=(6, 4) if dashed else None,
                cls="relation",
            )
        )
        heads.append((pts, head_left, head_right))
        if rel.left_card:
            end_labels.append((pts[0], rel.left_card, "end"))
        if rel.right_card:
            end_labels.append((pts[-1], rel.right_card, "start"))
        if rel.label:
            mid = pts[len(pts) // 2] if len(pts) > 2 else ((pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2)
            lsize = theme.font_size - 3
            lines = fit_label(rel.label, lsize, 140)
            lw2, lh2 = lines_size(lines, lsize)
            prims.append(
                Prim(
                    kind="rect",
                    x=mid[0] - lw2 / 2 - 3,
                    y=mid[1] - lh2 / 2 - 2,
                    w=lw2 + 6,
                    h=lh2 + 4,
                    rx=2,
                    fill=theme.edge_label_bg,
                    stroke=None,
                    cls="edgeLabel",
                )
            )
            prims.append(center_text(mid[0], mid[1], lines, lsize, theme.edge_text, cls="edgeLabel"))

    # class boxes
    for cid, cls in ir.classes.items():
        if cid not in positions:
            continue
        cx, cy = positions[cid]
        w, h = sizes[cid]
        x, y = cx - w / 2, cy - h / 2
        tsize = theme.font_size - 2
        msize = theme.font_size - 3
        prims.append(
            Prim(
                kind="rect",
                x=x,
                y=y,
                w=w,
                h=h,
                rx=0,
                fill=theme.node_fill,
                stroke=theme.node_stroke,
                stroke_width=1.0,
                cls="classBox",
            )
        )
        cursor = y + 6
        for anno in cls.annotations:
            prims.append(
                center_text(
                    cx,
                    cursor + msize * LINE_H / 2,
                    [f"\u00ab{anno}\u00bb"],
                    msize,
                    theme.node_text,
                    cls="classAnnotation",
                )
            )
            cursor += msize * LINE_H
        prims.append(
            center_text(
                cx, cursor + tsize * LINE_H / 2, [cls.name], tsize, theme.node_text, bold=True, cls="classTitle"
            )
        )
        cursor += tsize * LINE_H + 4
        if cls.members:
            prims.append(
                Prim(
                    kind="polyline",
                    pts=((x, cursor), (x + w, cursor)),
                    stroke=theme.node_stroke,
                    stroke_width=1.0,
                    cls="classDivider",
                )
            )
            cursor += 4
            for mm in cls.members:
                prims.append(left_text(x + 8, cursor, [mm], msize, theme.node_text, cls="classMember"))
                cursor += msize * LINE_H
        if cls.note:
            nsize = theme.font_size - 3
            lines = fit_label(cls.note, nsize, 180)
            nw, nh = lines_size(lines, nsize)
            prims.append(
                Prim(
                    kind="rect",
                    x=cx - nw / 2 - 8,
                    y=y - nh - 18,
                    w=nw + 16,
                    h=nh + 10,
                    rx=2,
                    fill=theme.note_fill,
                    stroke=theme.note_stroke,
                    stroke_width=1.0,
                    cls="note",
                )
            )
            prims.append(center_text(cx, y - nh / 2 - 13, lines, nsize, theme.note_text, cls="noteText"))

    # arrow heads + cardinality labels
    for pts, head_left, head_right in heads:
        if head_left != "none" and len(pts) >= 2:
            hp = arrowhead(pts[1], pts[0], head_left, theme.edge_stroke, 11)
            if hp:
                prims.append(hp)
        if head_right != "none" and len(pts) >= 2:
            hp = arrowhead(pts[-2], pts[-1], head_right, theme.edge_stroke, 11)
            if hp:
                prims.append(hp)
    csize = theme.font_size - 4
    for pt, text, anchor in end_labels:
        dx = -10 if anchor == "end" else 10
        prims.append(
            Prim(
                kind="text",
                x=pt[0] + dx,
                y=pt[1] - csize * LINE_H / 2 - 4,
                lines=(text,),
                size=csize,
                anchor=anchor,
                color=theme.edge_text,
                cls="cardinality",
            )
        )

    bx, by, bw, bh = prim_bbox(prims)
    prims = shift_prims(prims, -bx + 4, -by + 4)
    return Canvas(w=math.ceil(bw + 8), h=math.ceil(bh + 8), prims=prims)


# --------------------------------------------------------------------------
# ER diagram parser + renderer
# --------------------------------------------------------------------------


@dataclass
class ErAttr:
    type: str
    name: str
    key: str | None = None
    comment: str | None = None


@dataclass
class ErEntity:
    name: str
    attrs: list[ErAttr] = field(default_factory=list)


@dataclass
class ErRel:
    left: str
    right: str
    ltoken: str
    rtoken: str
    dashed: bool = False
    label: str = ""


@dataclass
class ErIR:
    entities: dict[str, ErEntity] = field(default_factory=dict)
    rels: list[ErRel] = field(default_factory=list)


_ER_ROW_RE = re.compile(r'^(\w+)\s+(\w+)\s*(?:(PK|FK|UK)\s*)?(?:"([^"]*)")?\s*$')
_ER_REL_RE = re.compile(r"^(\w+)\s+([}|o{]{1,2})\s*(--|\.\.)\s*([}|o{]{1,2})\s+(\w+)\s*:\s*(.*)$")


def parse_er(statements: Sequence[str]) -> ErIR:
    """Parse ER diagram statements into an :class:`ErIR`."""
    ir = ErIR()
    open_entity: ErEntity | None = None

    for raw in statements:
        stmt = raw.strip()
        if not stmt:
            continue
        if re.match(r"^erDiagram$", stmt, re.I):
            continue

        if open_entity is not None:
            if stmt == "}":
                open_entity = None
                continue
            if stmt.endswith("}") and len(stmt) > 1:
                body = stmt[:-1].strip()
                if body:
                    _er_add_row(open_entity, body)
                open_entity = None
                continue
            _er_add_row(open_entity, stmt)
            continue

        m = re.match(r"^(\w+)\s*\{$", stmt)
        if m:
            name = m.group(1)
            ent = ir.entities.get(name)
            if ent is None:
                ent = ErEntity(name=name)
                ir.entities[name] = ent
            open_entity = ent
            continue

        m = _ER_REL_RE.match(stmt)
        if m:
            for nm in (m.group(1), m.group(5)):
                if nm not in ir.entities:
                    ir.entities[nm] = ErEntity(name=nm)
            ir.rels.append(
                ErRel(
                    left=m.group(1),
                    right=m.group(5),
                    ltoken=m.group(2),
                    rtoken=m.group(4),
                    dashed=m.group(3) == "..",
                    label=m.group(6).strip(),
                )
            )
            continue

    return ir


def _er_add_row(ent: ErEntity, text: str) -> None:
    m = _ER_ROW_RE.match(text)
    if m:
        ent.attrs.append(ErAttr(m.group(1), m.group(2), m.group(3), m.group(4)))
    else:
        ent.attrs.append(ErAttr("", text))


def render_er(statements: Sequence[str], theme: Theme, config: dict[str, Any]) -> Canvas:
    """Lay out and render an ER diagram into a :class:`Canvas`."""
    ir = parse_er(statements)
    if not ir.entities:
        return Canvas(w=100, h=60, prims=[])

    tsize = theme.font_size - 2
    rsize = theme.font_size - 3
    sizes: dict[str, tuple[float, float]] = {}
    for name, ent in ir.entities.items():
        title_w = text_width(name, tsize, bold=True)
        rows = []
        for a in ent.attrs:
            row = f"{a.type} {a.name}".strip()
            if a.key:
                row += f" {a.key}"
            rows.append((row, a.comment))
        row_w = max((text_width(r, rsize) for r, _c in rows), default=0.0)
        w = max(title_w, row_w) + 20
        h = 30 + len(rows) * rsize * LINE_H + (8 if rows else 0) + 6
        sizes[name] = (max(w, 90.0), max(h, 46.0))

    positions, _ranks = layered_layout(
        sizes,
        [(r.left, r.right) for r in ir.rels],
        node_spacing=70,
        rank_spacing=90,
    )

    prims: list[Prim] = []

    def perp(p0: Pt, p1: Pt, dist: float) -> Pt:
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        n = math.hypot(dx, dy) or 1.0
        return (-dy / n * dist, dx / n * dist)

    for rel in ir.rels:
        if rel.left not in positions or rel.right not in positions:
            continue
        lx, ly = positions[rel.left]
        rx_, ry_ = positions[rel.right]
        lw, lh = sizes[rel.left]
        rw, rh = sizes[rel.right]
        if ly < ry_:
            pts = [(lx, ly + lh / 2), (lx, (ly + ry_) / 2), (rx_, (ly + ry_) / 2), (rx_, ry_ - rh / 2)]
        elif ly > ry_:
            pts = [(lx, ly - lh / 2), (lx, (ly + ry_) / 2), (rx_, (ly + ry_) / 2), (rx_, ry_ + rh / 2)]
        elif lx < rx_:
            pts = [(lx + lw / 2, ly), (rx_ - rw / 2, ry_)]
        else:
            pts = [(lx - lw / 2, ly), (rx_ + rw / 2, ry_)]
        prims.append(
            Prim(
                kind="polyline",
                pts=tuple(pts),
                stroke=theme.edge_stroke,
                stroke_width=1.4,
                dash=(6, 4) if rel.dashed else None,
                cls="relation",
            )
        )
        csize = theme.font_size - 4

        # cardinality tokens near each end, offset off the line
        off_l = perp(pts[0], pts[1], 14)
        off_r = perp(pts[-1], pts[-2], 14)
        lpos = (
            pts[0][0] + (pts[1][0] - pts[0][0]) * 0.30 + off_l[0],
            pts[0][1] + (pts[1][1] - pts[0][1]) * 0.30 + off_l[1],
        )
        rpos = (
            pts[-1][0] + (pts[-2][0] - pts[-1][0]) * 0.30 + off_r[0],
            pts[-1][1] + (pts[-2][1] - pts[-1][1]) * 0.30 + off_r[1],
        )
        for pos, token in ((lpos, rel.ltoken), (rpos, rel.rtoken)):
            prims.append(
                Prim(
                    kind="rect",
                    x=pos[0] - 12,
                    y=pos[1] - csize * 0.7,
                    w=24,
                    h=csize * 1.25,
                    rx=2,
                    fill=theme.background if theme.background not in ("transparent", "none") else "#ffffff",
                    stroke=None,
                    cls="cardinality-bg",
                )
            )
            prims.append(
                Prim(
                    kind="text",
                    x=pos[0],
                    y=pos[1] - csize * 0.6,
                    lines=(token,),
                    size=csize,
                    anchor="middle",
                    color=theme.edge_text,
                    cls="cardinality",
                )
            )
        if rel.label:
            mid = pts[len(pts) // 2] if len(pts) > 2 else ((pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2)
            seg0 = pts[len(pts) // 2 - 1]
            off = perp(seg0, mid, 13)
            lpos2 = (mid[0] + off[0], mid[1] + off[1])
            lines = fit_label(rel.label, csize, 140)
            lw2, lh2 = lines_size(lines, csize)
            prims.append(
                Prim(
                    kind="rect",
                    x=lpos2[0] - lw2 / 2 - 4,
                    y=lpos2[1] - lh2 / 2 - 3,
                    w=lw2 + 8,
                    h=lh2 + 6,
                    rx=3,
                    fill=theme.edge_label_bg,
                    stroke=None,
                    cls="edgeLabel",
                )
            )
            prims.append(center_text(lpos2[0], lpos2[1], lines, csize, theme.edge_text, cls="edgeLabel"))

    for name, ent in ir.entities.items():
        if name not in positions:
            continue
        cx, cy = positions[name]
        w, h = sizes[name]
        x, y = cx - w / 2, cy - h / 2
        prims.append(
            Prim(
                kind="rect",
                x=x,
                y=y,
                w=w,
                h=h,
                rx=0,
                fill=theme.node_fill,
                stroke=theme.node_stroke,
                stroke_width=1.0,
                cls="erBox",
            )
        )
        prims.append(
            Prim(
                kind="rect",
                x=x,
                y=y,
                w=w,
                h=30,
                rx=0,
                fill=theme.node_stroke,
                stroke=theme.node_stroke,
                stroke_width=1.0,
                opacity=0.25,
                cls="erHeader",
            )
        )
        prims.append(center_text(cx, y + 15, [name], tsize, theme.node_text, bold=True, cls="erTitle"))
        if ent.attrs:
            prims.append(
                Prim(kind="polyline", pts=((x, y + 30), (x + w, y + 30)), stroke=theme.node_stroke, stroke_width=1.0)
            )
        cursor = y + 34
        for a in ent.attrs:
            row = f"{a.type} {a.name}".strip()
            if a.key:
                row += f" {a.key}"
            lines = [row] + ([a.comment] if a.comment else [])
            prims.append(left_text(x + 8, cursor, lines, rsize, theme.node_text, cls="erAttr"))
            cursor += len(lines) * rsize * LINE_H

    bx, by, bw, bh = prim_bbox(prims)
    prims = shift_prims(prims, -bx + 4, -by + 4)
    return Canvas(w=math.ceil(bw + 8), h=math.ceil(bh + 8), prims=prims)


# --------------------------------------------------------------------------
# state diagram parser + renderer
# --------------------------------------------------------------------------


@dataclass
class StState:
    id: str
    label: str
    kind: str = "state"  # state | start | end | choice | fork | join
    group: str | None = None
    note: tuple[str, str] | None = None  # (side, text)


@dataclass
class StTrans:
    src: str
    dst: str
    label: str = ""


@dataclass
class StateIR:
    direction: str = "TB"
    states: dict[str, StState] = field(default_factory=dict)
    trans: list[StTrans] = field(default_factory=list)
    groups: dict[str, StState] = field(default_factory=dict)


_ST_TRANS_RE = re.compile(r"^(\S+?)\s*(?:-+>|->)\s*(\[?\S+?\]?)\s*(?::\s*(.*))?$")
_ST_STATE_AS_RE = re.compile(r'^state\s+"(.*)"\s+as\s+(\w+)$')
_ST_STATE_DESC_RE = re.compile(r"^state\s+(\w+)\s*:\s*(.*)$")
_ST_STATE_PLAIN_RE = re.compile(r"^state\s+(\w+)(?:\s+<<(choice|fork|join)>>)?\s*$")
_ST_NOTE_RE = re.compile(r"^note\s+(left of|right of|over)\s+(\w+)\s*:\s*(.*)$", re.I)
_ST_DIR_RE = re.compile(r"^direction\s+(TB|TD|BT|RL|LR)$", re.I)


def parse_state(statements: Sequence[str]) -> StateIR:
    """Parse state diagram statements into a :class:`StateIR`."""
    ir = StateIR()
    group_stack: list[str] = []
    counter = {"n": 0}

    def ensure(sid: str, label: str | None = None, kind: str = "state") -> str:
        if sid == "[*]":
            if "__start__" not in ir.states:
                ir.states["__start__"] = StState(id="__start__", label="", kind="start")
            return "__start__"
        if sid in ir.states:
            if label is not None and kind == "state":
                ir.states[sid].label = label
            return sid
        st = StState(
            id=sid, label=label if label is not None else sid, kind=kind, group=group_stack[-1] if group_stack else None
        )
        ir.states[sid] = st
        return sid

    for raw in statements:
        stmt = raw.strip()
        if not stmt:
            continue

        if re.match(r"^stateDiagram(-v2)?$", stmt, re.I):
            continue

        m = _ST_DIR_RE.match(stmt)
        if m:
            ir.direction = m.group(1).upper()
            continue

        if re.match(r"^end$", stmt, re.I):
            if group_stack:
                group_stack.pop()
            continue

        # composite state:  state composite {
        m = re.match(r"^state\s+(\w+)\s*\{$", stmt)
        if m:
            gid = m.group(1)
            if gid not in ir.groups:
                ir.groups[gid] = StState(id=gid, label=gid, kind="state")
            group_stack.append(gid)
            continue

        m = _ST_STATE_AS_RE.match(stmt)
        if m:
            ensure(m.group(2), m.group(1))
            continue

        m = _ST_STATE_DESC_RE.match(stmt)
        if m:
            ensure(m.group(1), m.group(2))
            continue

        m = _ST_STATE_PLAIN_RE.match(stmt)
        if m:
            kind = m.group(2) or "state"
            ensure(m.group(1), None, kind)
            continue

        m = _ST_NOTE_RE.match(stmt)
        if m:
            sid = ensure(m.group(2))
            side = {"left of": "left", "right of": "right", "over": "over"}[m.group(1).lower()]
            ir.states[sid].note = (side, m.group(3).strip())
            continue

        m = _ST_TRANS_RE.match(stmt)
        if m:
            src_raw, dst_raw = m.group(1), m.group(2)
            src = ensure(src_raw)
            if dst_raw == "[*]":
                counter["n"] += 1
                dst = f"__end{'' if counter['n'] == 1 else counter['n']}__"
                if dst not in ir.states:
                    ir.states[dst] = StState(id=dst, label="", kind="end")
            else:
                dst = ensure(dst_raw)
            ir.trans.append(StTrans(src=src, dst=dst, label=(m.group(3) or "").strip()))
            continue

        if stmt == "{":
            continue

    return ir


def render_state(statements: Sequence[str], theme: Theme, config: dict[str, Any]) -> Canvas:
    """Lay out and render a state diagram into a :class:`Canvas`."""
    ir = parse_state(statements)
    if not ir.states:
        return Canvas(w=100, h=60, prims=[])

    lsize = theme.font_size - 2
    sizes: dict[str, tuple[float, float]] = {}
    lines_of: dict[str, list[str]] = {}
    for sid, st in ir.states.items():
        if st.kind == "start":
            sizes[sid] = (16.0, 16.0)
            lines_of[sid] = []
        elif st.kind == "end":
            sizes[sid] = (22.0, 22.0)
            lines_of[sid] = []
        elif st.kind in ("fork", "join"):
            sizes[sid] = (80.0, 8.0)
            lines_of[sid] = []
        elif st.kind == "choice":
            sizes[sid] = (46.0, 34.0)
            lines_of[sid] = []
        else:
            lines = fit_label(st.label or st.id, lsize, 200)
            tw, th = lines_size(lines, lsize)
            sizes[sid] = (max(tw + 28, 60.0), max(th + 16, 36.0))
            lines_of[sid] = lines

    group_of = {sid: st.group for sid, st in ir.states.items() if st.group}
    positions, ranks = layered_layout(
        sizes,
        [(t.src, t.dst) for t in ir.trans],
        node_spacing=50,
        rank_spacing=65,
        group_of=group_of,
    )
    # composite groups need entries in ir.groups keyed the same as st.group
    for sid, st in ir.states.items():
        if st.group and st.group not in ir.groups:
            ir.groups[st.group] = StState(id=st.group, label=st.group, kind="state")

    node_rects = {
        sid: (cx - sizes[sid][0] / 2, cy - sizes[sid][1] / 2, sizes[sid][0], sizes[sid][1])
        for sid, (cx, cy) in positions.items()
    }

    prims: list[Prim] = []
    heads: list[tuple[list[Pt], str]] = []

    # composite boxes behind everything
    boxes = _group_boxes(
        {gid: g for gid, g in ir.groups.items()},
        node_rects,
        lambda sid: ir.states[sid].group,
    )

    def depth_of(gid: str) -> int:
        d = 0
        while gid in ir.groups and ir.groups[gid].group is not None:
            gid = ir.groups[gid].group  # type: ignore[assignment]
            d += 1
        return d

    for gid in sorted(ir.groups, key=depth_of):
        gx, gy, gw, gh = boxes[gid]
        if gw <= 0:
            continue
        prims.append(
            Prim(
                kind="rect",
                x=gx,
                y=gy,
                w=gw,
                h=gh,
                rx=6,
                fill=theme.cluster_fill,
                stroke=theme.cluster_stroke,
                stroke_width=1.0,
                cls="cluster",
            )
        )
        prims.append(
            left_text(
                gx + 8,
                gy + 7,
                fit_label(ir.groups[gid].label, lsize - 2, gw - 12),
                lsize - 2,
                theme.cluster_text,
                cls="cluster-label",
            )
        )

    # transitions
    loop_counter: dict[tuple[str, str], int] = {}
    for tr in ir.trans:
        if tr.src not in positions or tr.dst not in positions:
            continue
        key = (tr.src, tr.dst)
        loop_idx = loop_counter.get(key, 0)
        loop_counter[key] = loop_idx + 1
        if tr.src == tr.dst:
            sx, sy = positions[tr.src]
            w, h = sizes[tr.src]
            bottom = sy + h / 2
            out_x = sx + w / 2 + 24
            pts = [(sx, bottom), (sx, bottom + 18), (out_x, bottom + 18), (out_x, sy), (sx + w / 2, sy)]
            label_pt = (out_x, sy + h / 2 + 4)
        else:
            pts, label_pt = _route_flow_edge(
                positions[tr.src],
                sizes[tr.src],
                positions[tr.dst],
                sizes[tr.dst],
                ranks[tr.src],
                ranks[tr.dst],
                65,
                loop_idx,
            )
        prims.append(
            Prim(kind="polyline", pts=tuple(pts), stroke=theme.edge_stroke, stroke_width=1.5, cls="transition")
        )
        heads.append((pts, "arrow"))
        if tr.label:
            els = theme.font_size - 3
            lines = fit_label(tr.label, els, 150)
            lw, lh = lines_size(lines, els)
            prims.append(
                Prim(
                    kind="rect",
                    x=label_pt[0] - lw / 2 - 4,
                    y=label_pt[1] - lh / 2 - 3,
                    w=lw + 8,
                    h=lh + 6,
                    rx=3,
                    fill=theme.edge_label_bg,
                    stroke=None,
                    cls="edgeLabel",
                )
            )
            prims.append(center_text(label_pt[0], label_pt[1], lines, els, theme.edge_text, cls="edgeLabel"))

    # states
    for sid, st in ir.states.items():
        if sid not in positions:
            continue
        cx, cy = positions[sid]
        w, h = sizes[sid]
        if st.kind == "start":
            prims.append(
                Prim(
                    kind="ellipse",
                    x=cx,
                    y=cy,
                    rx=w / 2,
                    ry=h / 2,
                    fill=theme.edge_stroke,
                    stroke=theme.edge_stroke,
                    stroke_width=1.5,
                    cls="startState",
                )
            )
        elif st.kind == "end":
            prims.append(
                Prim(
                    kind="ellipse",
                    x=cx,
                    y=cy,
                    rx=w / 2,
                    ry=h / 2,
                    fill=None,
                    stroke=theme.edge_stroke,
                    stroke_width=1.5,
                    cls="endState",
                )
            )
            prims.append(
                Prim(
                    kind="ellipse",
                    x=cx,
                    y=cy,
                    rx=w / 2 - 4,
                    ry=h / 2 - 4,
                    fill=theme.edge_stroke,
                    stroke=theme.edge_stroke,
                    stroke_width=1.0,
                    cls="endState",
                )
            )
        elif st.kind in ("fork", "join"):
            prims.append(
                Prim(
                    kind="rect",
                    x=cx - w / 2,
                    y=cy - h / 2,
                    w=w,
                    h=h,
                    rx=1,
                    fill=theme.edge_stroke,
                    stroke=theme.edge_stroke,
                    stroke_width=1.0,
                    cls="forkJoin",
                )
            )
        elif st.kind == "choice":
            prims.append(
                Prim(
                    kind="polygon",
                    pts=((cx, cy - h / 2), (cx + w / 2, cy), (cx, cy + h / 2), (cx - w / 2, cy)),
                    fill=theme.node_fill,
                    stroke=theme.node_stroke,
                    stroke_width=1.2,
                    cls="choice",
                )
            )
        else:
            prims.extend(
                shape_prims("rounded", cx, cy, w, h, theme.node_fill, theme.node_stroke, 1.2, None, cls="state")
            )
            prims.append(center_text(cx, cy, lines_of[sid], lsize, theme.node_text, cls="label"))
        if st.note:
            side, text = st.note
            nsize = theme.font_size - 3
            lines = fit_label(text, nsize, 170)
            nw, nh = lines_size(lines, nsize)
            if side == "left":
                nx = cx - w / 2 - nw - 24
            elif side == "right":
                nx = cx + w / 2 + 24
            else:
                nx = cx - nw / 2 - 8
            ny = cy - nh / 2 - 6
            prims.append(
                Prim(
                    kind="rect",
                    x=nx,
                    y=ny,
                    w=nw + 16,
                    h=nh + 10,
                    rx=2,
                    fill=theme.note_fill,
                    stroke=theme.note_stroke,
                    stroke_width=1.0,
                    cls="note",
                )
            )
            prims.append(
                center_text(nx + (nw + 16) / 2, ny + (nh + 10) / 2, lines, nsize, theme.note_text, cls="noteText")
            )

    bx, by, bw, bh = prim_bbox(prims)
    prims = shift_prims(prims, -bx, -by)
    prims = transform_prims(prims, ir.direction, bw, bh)
    fw, fh = transformed_size(ir.direction, bw, bh)
    for pts, head in heads:
        pts = _transform_pts([(x - bx, y - by) for (x, y) in pts], ir.direction, bw, bh)
        hp = arrowhead(pts[-2], pts[-1], head, theme.edge_stroke)
        if hp:
            prims.append(hp)

    return Canvas(w=math.ceil(fw), h=math.ceil(fh), prims=prims)


# --------------------------------------------------------------------------
# pie chart parser + renderer
# --------------------------------------------------------------------------


@dataclass
class PieIR:
    title: str | None = None
    rows: list[tuple[str, float]] = field(default_factory=list)
    show_data: bool = False


def parse_pie(statements: Sequence[str]) -> PieIR:
    """Parse pie chart statements into a :class:`PieIR`."""
    ir = PieIR()
    for raw in statements:
        stmt = raw.strip()
        if not stmt:
            continue
        if re.match(r"^pie$", stmt, re.I):
            continue
        m = re.match(r"^pie\s+title\s+(.*)$", stmt, re.I)
        if m:
            ir.title = m.group(1).strip()
            continue
        m = re.match(r"^title\s+(.*)$", stmt, re.I)
        if m and not ir.rows:
            ir.title = m.group(1).strip()
            continue
        if re.match(r"^showData$", stmt, re.I):
            ir.show_data = True
            continue
        m = re.match(r'^"(.*)"\s*:\s*([\d.]+)\s*$', stmt)
        if m:
            ir.rows.append((m.group(1), float(m.group(2))))
    return ir


def render_pie(statements: Sequence[str], theme: Theme, config: dict[str, Any]) -> Canvas:
    """Lay out and render a pie chart into a :class:`Canvas`."""
    ir = parse_pie(statements)
    if not ir.rows:
        return Canvas(w=100, h=60, prims=[])

    total = sum(v for _k, v in ir.rows) or 1.0
    title_h = 34 if ir.title else 0
    cx = 150.0
    cy = title_h + 150.0
    r = 110.0

    # legend
    lsize = theme.font_size - 2
    legend_x = cx + r + 60
    legend_w = max(
        (text_width(k if not ir.show_data else f"{k} ({v:g})", lsize) for k, v in ir.rows),
        default=0.0,
    )
    legend_h = len(ir.rows) * 26
    canvas_w = max(legend_x + legend_w + 40, cx + r + 60)
    canvas_h = max(cy + r + 24, title_h + legend_h + 30)

    prims: list[Prim] = []
    if ir.title:
        prims.append(center_text(canvas_w / 2, 16, [ir.title], theme.font_size + 2, theme.text_color, bold=True))

    angle = -math.pi / 2
    for i, (key, value) in enumerate(ir.rows):
        frac = value / total
        sweep = frac * 2 * math.pi
        color = theme.pie_colors[i % len(theme.pie_colors)]
        pts: list[Pt] = [(cx, cy)]
        steps = max(2, int(sweep / 0.06) + 1)
        for s in range(steps + 1):
            a = angle + sweep * s / steps
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        prims.append(
            Prim(
                kind="polygon",
                pts=tuple(pts),
                fill=color,
                stroke=theme.background if theme.background != "transparent" else "#ffffff",
                stroke_width=2.0,
                cls="pieSlice",
            )
        )
        # percentage label
        pct = frac * 100
        mid_a = angle + sweep / 2
        lr = r * (0.62 if pct >= 8 else 1.16)
        label = f"{pct:.1f}%"
        prims.append(
            Prim(
                kind="text",
                x=cx + lr * math.cos(mid_a),
                y=cy + lr * math.sin(mid_a) - lsize * 0.4,
                lines=(label,),
                size=lsize - 2,
                anchor="middle",
                color=theme.text_color,
                cls="sliceLabel",
            )
        )
        # legend entry
        ly = title_h + 16 + i * 26
        prims.append(Prim(kind="rect", x=legend_x, y=ly, w=14, h=14, rx=2, fill=color, stroke=None, cls="legendSwatch"))
        legend_text = f"{key} ({value:g})" if ir.show_data else key
        prims.append(
            Prim(
                kind="text",
                x=legend_x + 22,
                y=ly - 1,
                lines=(legend_text,),
                size=lsize,
                anchor="start",
                color=theme.text_color,
                cls="legendText",
            )
        )
        angle += sweep

    return Canvas(w=math.ceil(canvas_w), h=math.ceil(canvas_h), prims=prims)


# --------------------------------------------------------------------------
# diagram dispatch
# --------------------------------------------------------------------------


def render_diagram(
    definition: str, mermaid_config: dict[str, Any] | None = None
) -> tuple[Canvas, str | None, str | None, Theme]:
    """Render a mermaid definition to a :class:`Canvas`.

    Returns ``(canvas, title, desc, theme)``.
    """
    statements, init_config, title, desc = preprocess_definition(definition)
    merged: dict[str, Any] = dict(mermaid_config or {})
    merged.update(init_config)

    theme_name = merged.get("theme") or "default"
    theme = get_theme(theme_name, merged.get("themeVariables"))
    if merged.get("fontFamily"):
        theme.font_family = str(merged["fontFamily"])
    if merged.get("fontSize"):
        try:
            theme.font_size = float(merged["fontSize"])
        except (TypeError, ValueError):
            pass

    dtype = detect_diagram_type(statements).lower()
    if dtype in ("flowchart", "graph"):
        canvas = render_flowchart(statements, theme, merged)
    elif dtype == "sequencediagram":
        canvas = render_sequence(statements, theme, merged)
    elif dtype in ("classdiagram", "classdiagram-v2"):
        canvas = render_class(statements, theme, merged)
    elif dtype in ("statediagram", "statediagram-v2"):
        canvas = render_state(statements, theme, merged)
    elif dtype == "erdiagram":
        canvas = render_er(statements, theme, merged)
    elif dtype == "pie":
        canvas = render_pie(statements, theme, merged)
    elif not dtype:
        raise MermaidParseError("Empty diagram definition")
    else:
        raise UnsupportedDiagramError(detect_diagram_type(statements))

    canvas = with_margin(canvas, DIAGRAM_MARGIN)
    return canvas, title, desc, theme


# --------------------------------------------------------------------------
# library API mirroring the JS module exports
#
#   export { run, renderMermaid, cli, error }
# --------------------------------------------------------------------------


def renderMermaid(browser, definition, outputFormat, opt=None) -> dict[str, Any]:
    """Render a mermaid diagram (mirror of the JS ``renderMermaid``).

    ``browser`` is accepted for API compatibility with the JS module but is
    unused -- this implementation needs no browser.  Returns a dict with
    ``title``, ``desc`` and ``data`` (the output file as bytes).
    """
    if outputFormat not in ("svg", "png", "pdf"):
        raise MermaidError('Output format must be one of "svg", "png" or "pdf"')
    opt = opt or {}
    mermaid_config = dict(opt.get("mermaidConfig") or {})
    canvas, title, desc, theme = render_diagram(definition, mermaid_config)

    background = opt.get("backgroundColor", "white")
    viewport = opt.get("viewport") or {}
    try:
        scale = float(viewport.get("deviceScaleFactor", 1) or 1)
    except (TypeError, ValueError):
        scale = 1.0
    size = opt.get("size")

    if outputFormat == "svg":
        data = canvas_to_svg(
            canvas,
            background=background,
            svg_id=opt.get("svgId") or "my-svg",
            css=mermaid_config.get("themeCSS"),
            font_family=theme.font_family,
            size=int(size) if size else None,
            title=title,
            desc=desc,
        ).encode("utf-8")
    elif outputFormat == "png":
        data = canvas_to_png(canvas, background=background, scale=scale, size=size)
    else:
        data = canvas_to_pdf(
            canvas,
            background=background,
            paper=opt.get("pdfPaperFormat"),
            size=size,
        )
    return {"title": title, "desc": desc, "data": data}


def markdownImage(params: dict[str, Any]) -> str:
    """Mirror of the JS ``markdownImage``: ``![alt](url "title")``."""
    alt = str(params.get("alt") or "")
    alt_escaped = re.sub(r"[\[\]\\]", lambda m: "\\" + m.group(0), alt)
    url = str(params.get("url") or "")
    title = params.get("title")
    if title:
        title_escaped = re.sub(r"[\"\\]", lambda m: "\\" + m.group(0), str(title))
        return f'![{alt_escaped}]({url} "{title_escaped}")'
    return f"![{alt_escaped}]({url})"


#: Mirrors upstream's markdown code-block finder (supports ``` and ::: fences).
MERMAID_MD_RE = re.compile(r"^[^\S\n]*[`:]{3}mermaid([^\S\n]*\r?\n([\s\S]*?))[`:]{3}[^\S\n]*$", re.M)


def _render_to_file(
    definition: str, output_file: str, output_format: str, parse_opts: dict[str, Any]
) -> dict[str, Any]:
    result = renderMermaid(None, definition, output_format, parse_opts)
    Path(output_file).write_bytes(result["data"])
    return result


def run(input: str | None, output: str, opts: dict[str, Any] | None = None) -> None:
    """Render a mermaid diagram or mermaid markdown file (mirror of JS ``run``).

    ``opts`` keys mirror the JS options: ``browser``, ``puppeteerConfig``,
    ``quiet``, ``outputFormat``, ``parseMMDOptions``, ``limiter``,
    ``artefacts`` (plus the pythonic ``jobs`` used when no limiter is given).
    """
    o: dict[str, Any] = {
        "browser": None,
        "puppeteerConfig": {},
        "quiet": False,
        "outputFormat": None,
        "parseMMDOptions": None,
        "limiter": None,
        "artefacts": None,
        "jobs": None,
    }
    o.update(opts or {})
    set_quiet(bool(o.get("quiet")))

    parse_opts = o.get("parseMMDOptions") or {}
    outputFormat = o.get("outputFormat")
    if not outputFormat:
        ext = Path(output).suffix.lstrip(".").lower()
        outputFormat = "svg" if ext in ("md", "markdown") else ext
    if outputFormat not in ("svg", "png", "pdf"):
        raise MermaidError('Output format must be one of "svg", "png" or "pdf"')

    if input is None:
        definition = sys.stdin.read()
    else:
        definition = Path(input).read_text(encoding="utf-8")

    limiter = o.get("limiter")
    own_pool: ThreadPoolExecutor | None = None
    if limiter is None:
        jobs = o.get("jobs") or max((os.cpu_count() or 1) // 2, 1)
        own_pool = ThreadPoolExecutor(max_workers=max(1, int(jobs)))
        limiter = _pool_limiter(own_pool)

    try:
        is_markdown = input is not None and Path(input).suffix.lower() in (".md", ".markdown")
        if is_markdown:
            if output == "/dev/stdout":
                raise MermaidError("Cannot use `stdout` with markdown input")

            matches = list(MERMAID_MD_RE.finditer(definition))
            futures: list[tuple[Any, str]] = []
            for i, m in enumerate(matches, start=1):
                mermaid_definition = m.group(2)
                out_file = re.sub(
                    r"(\.(?:md|markdown|png|svg|pdf))$",
                    lambda mm: f"-{i}{mm.group(1)}",
                    output,
                )
                out_file = re.sub(r"\.(?:md|markdown)$", f".{outputFormat}", out_file)
                if o.get("artefacts"):
                    out_file = str(Path(o["artefacts"]).resolve() / Path(out_file).name)
                out_file_rel = "./" + relative_to(out_file, Path(output).parent)
                fut = limiter(
                    lambda d=mermaid_definition, of=out_file: _render_to_file(d, of, outputFormat, parse_opts)
                )
                futures.append((fut, out_file_rel))

            if futures:
                info(f"Found {len(futures)} mermaid charts in Markdown input")
            else:
                info("No mermaid charts found in Markdown input")

            images: list[dict[str, Any]] = []
            for fut, rel in futures:
                res = fut.result() if hasattr(fut, "result") else fut
                info(f" ✅ {rel}")
                images.append({"url": rel, "title": res.get("title"), "alt": res.get("desc")})

            if Path(output).suffix.lower() in (".md", ".markdown"):
                image_iter = iter(images)

                def repl(_m: re.Match[str]) -> str:
                    img = next(image_iter, {"url": "", "title": None, "alt": None})
                    return markdownImage({"url": img["url"], "title": img["title"], "alt": img["alt"] or "diagram"})

                out_definition = MERMAID_MD_RE.sub(repl, definition)
                Path(output).write_text(out_definition, encoding="utf-8")
                info(f" ✅ {output}")
        else:
            info("Generating single mermaid chart")
            result = renderMermaid(None, definition, outputFormat, parse_opts)
            if output == "/dev/stdout":
                sys.stdout.buffer.write(result["data"])
                sys.stdout.buffer.flush()
            else:
                Path(output).write_bytes(result["data"])
    finally:
        if own_pool is not None:
            own_pool.shutdown()


def _pool_limiter(pool: ThreadPoolExecutor) -> Callable[..., Any]:
    """A p-limit style limiter backed by a thread pool."""

    def limiter(fn: Callable[..., Any], *args: Any) -> Any:
        return pool.submit(fn, *args)

    limiter.shutdown = pool.shutdown  # type: ignore[attr-defined]
    return limiter


# --------------------------------------------------------------------------
# CLI (mirrors upstream commander setup)
# --------------------------------------------------------------------------


def _positive_int(value: str) -> int:
    try:
        v = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Not a positive integer.") from None
    if v < 1:
        raise argparse.ArgumentTypeError("Not a positive integer.")
    return v


def _positive_float(value: str) -> float:
    try:
        v = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Not a positive number.") from None
    if v <= 0:
        raise argparse.ArgumentTypeError("Not a positive number.")
    return v


def _output_value(value: str) -> str:
    if value == "-":
        return "/dev/stdout"
    if re.search(r"\.(?:svg|png|pdf|md|markdown)$", value):
        return value
    raise argparse.ArgumentTypeError("Output file must be either md, svg, png, or pdf.")


class _CommanderParser(argparse.ArgumentParser):
    """An argparse parser styled after commander's help/error output."""

    def format_usage(self) -> str:
        return "Usage: mmdc [options]\n"

    def format_help(self) -> str:
        entries: list[tuple[str, str]] = []
        for action in self._actions:
            if not action.option_strings:
                continue
            flag = ", ".join(action.option_strings)
            takes_value = not isinstance(action, (argparse._HelpAction, argparse._VersionAction)) and action.nargs != 0
            if takes_value:
                flag += f" <{action.metavar or action.dest}>"
            entries.append((flag, action.help or ""))
        width = max((len(f) for f, _ in entries), default=20) + 3
        out = ["Usage: mmdc [options]", ""]
        if self.description:
            out.extend([self.description, ""])
        out.append("Options:")
        for flag, help_text in entries:
            for i, line in enumerate(help_text.split("\n")):
                if i == 0:
                    out.append(f"  {flag.ljust(width)}{line}".rstrip())
                else:
                    out.append(f"  {' ' * width}{line}".rstrip())
        return "\n".join(out) + "\n"

    def error(self, message: str) -> None:
        sys.stderr.write(self.format_usage())
        sys.stderr.write(f"error: {message}\n")
        sys.stderr.write("Try 'mmdc --help' for more information.\n")
        self.exit(1)


def _build_parser() -> _CommanderParser:
    jobs_default = max((os.cpu_count() or 1) // 2, 1)
    parser = _CommanderParser(
        prog="mmdc",
        description="Render a mermaid diagram definition into various formats",
        add_help=False,
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=__version__,
        help="output the version number",
    )
    parser.add_argument(
        "-t",
        "--theme",
        choices=list(THEMES),
        default=None,
        help='Theme of the chart\n(default: "Mermaid default (depends on diagram type)")',
    )
    parser.add_argument(
        "--size",
        type=_positive_int,
        default=None,
        help=(
            "Size of the diagram.\n"
            "This will attempt to create a PNG with a maximum height or width equal to the given size.\n"
            "For `.svg`s, this will set the `max-width` attribute.\n"
            "For `.pdf`s, this is in CSS px, which is 1/96 of an inch."
        ),
    )
    parser.add_argument(
        "-i",
        "--input",
        default=None,
        help=(
            "Input mermaid file. Files ending in .md will be treated as Markdown and all charts "
            "(e.g. ```mermaid (...)``` or :::mermaid (...):::) will be extracted and generated. "
            "Use `-` to read from stdin."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=_output_value,
        default=None,
        help="Output file. It should be either md, svg, png, pdf or use `-` to output to stdout. "
        'Optional. Default: input + ".svg"',
    )
    parser.add_argument(
        "-a",
        "--artefacts",
        default=None,
        help="Output artefacts path. Only used with Markdown input file. Optional. Default: output directory",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=_positive_int,
        default=jobs_default,
        help="Number of parallel jobs to run when rendering multiple diagrams. "
        "Defaults to half the available CPUs. "
        f"(default: {jobs_default})",
    )
    parser.add_argument(
        "-e",
        "--outputFormat",
        choices=["svg", "png", "pdf"],
        default=None,
        help='Output format for the generated image.\n(default: "Loaded from the output file extension")',
    )
    parser.add_argument(
        "-b",
        "--backgroundColor",
        default="white",
        help="Background color for pngs/svgs (not pdfs). Example: transparent, red, '#F0F0F0'. (default: \"white\")",
    )
    parser.add_argument(
        "-c",
        "--configFile",
        default=None,
        help="JSON configuration file for mermaid.",
    )
    parser.add_argument(
        "-C",
        "--cssFile",
        default=None,
        help="CSS file for the page, used as Mermaid's `themeCSS`",
    )
    parser.add_argument(
        "-I",
        "--svgId",
        default=None,
        help="The id attribute for the SVG element to be rendered.",
    )
    parser.add_argument(
        "-s",
        "--scale",
        type=_positive_float,
        default=1.0,
        help="Puppeteer scale factor (default: 1)",
    )
    parser.add_argument(
        "--no-font-embed",
        dest="font_embed",
        action="store_false",
        help="Disable embedding fonts into the SVG. This cuts down on SVG size, but would "
        "result in missing fonts if they are not available on the system.",
    )
    parser.add_argument(
        "--pdf-paper-format",
        dest="pdf_paper_format",
        metavar="paperFormat",
        choices=list(PAPER_FORMATS),
        default=None,
        help="If set, output a PDF with the given paper size, instead of fitting the diagram.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress log output",
    )
    parser.add_argument(
        "-p",
        "--puppeteerConfigFile",
        default=None,
        help="JSON configuration file for puppeteer.",
    )
    parser.add_argument(
        "--iconPacks",
        action="append",
        default=None,
        help=(
            "Installed icon packs to use, e.g. @iconify-json/logos.\n"
            "These should be Iconify NPM packages that expose a icons.json file, see "
            "https://iconify.design/docs/icons/json.html.\n"
            "They can be installed via `npm install <icon pack>`."
        ),
    )
    parser.add_argument(
        "--iconPacksNamesAndUrls",
        action="append",
        default=None,
        help=(
            "Icon packs to use, e.g. azure#file:///tmp/icons.json where the name (prefix) of the "
            'icon pack is defined before the "#" and the url of the json definition after the "#".\n'
            "These should be Iconify json file formatted as IconifyJson, see "
            "https://iconify.design/docs/icons/json.html.\n"
            "These will be downloaded when needed.\n"
            "Use `file://` URIs to reference local files."
        ),
    )
    parser.add_argument(
        "-h",
        "--help",
        action="help",
        help="display help for command",
    )
    return parser


def _validate_icon_packs(args: argparse.Namespace) -> dict[str, str]:
    packs: dict[str, str] = {}
    for val in args.iconPacks or []:
        specifier = f"{val}/icons.json"
        path = Path(specifier)
        if not path.is_file():
            raise MermaidError(
                f"Could not find icon pack \"{specifier}\". Have you installed them with 'npm install {val}'?"
            )
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise MermaidError(f'Failed to parse icon pack JSON from "{specifier}".') from None
        if not isinstance(content, dict) or not isinstance(content.get("prefix"), str):
            raise MermaidError(f'Invalid icon pack JSON from "{specifier}". Missing or invalid "prefix" property.')
        packs[content["prefix"]] = str(path.resolve())
    for val in args.iconPacksNamesAndUrls or []:
        name, *url_parts = val.split("#")
        url = "#".join(url_parts)
        if not url.startswith(("http://", "https://", "file://")):
            raise MermaidError(
                "Failed to parse a valid URL. The format should be iconPackName#https://my-example-url.example"
            )
        packs[name] = url
    return packs


def _cli(argv: Sequence[str] | None) -> None:
    if sys.version_info < (3, 12):
        error(f"mmdc requires Python 3.12 or newer (found {sys.version_info.major}.{sys.version_info.minor})")

    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    icon_packs = _validate_icon_packs(args)

    theme = args.theme
    size = args.size
    input_arg = args.input
    output = args.output
    outputFormat = args.outputFormat
    quiet = args.quiet

    # check input file
    if not input_arg:
        warn(
            "No input file specified, reading from stdin. "
            "If you want to specify an input file, please use `-i .` "
            "You can use `-i -` to read from stdin and to suppress this warning."
        )
        input_path: str | None = None
    elif input_arg == "-":
        input_path = None
    elif not Path(input_arg).exists():
        error(f'Input file "{input_arg}" doesn\'t exist')
    else:
        input_path = input_arg

    # check output file
    if not output:
        if outputFormat:
            output = f"{input_path}.{outputFormat}" if input_path else f"out.{outputFormat}"
        else:
            output = f"{input_path}.svg" if input_path else "out.svg"
    elif output == "/dev/stdout":
        quiet = True
        if not outputFormat:
            outputFormat = "svg"
            warn(
                "No output format specified, using svg. "
                "If you want to specify an output format and suppress this warning, "
                "please use `-e .` "
            )

    if args.artefacts:
        if not input_path or Path(input_path).suffix.lower() not in (".md", ".markdown"):
            error("Artefacts [-a|--artefacts] path can only be used with Markdown input file")
        artefacts_dir = Path(args.artefacts)
        if not artefacts_dir.exists():
            artefacts_dir.mkdir(parents=True, exist_ok=True)

    if output != "/dev/stdout":
        output_dir = Path(output).parent
        if not output_dir.exists():
            error(f'Output directory "{output_dir}/" doesn\'t exist')

    # check config files
    mermaid_config: dict[str, Any] = {"theme": theme} if theme else {}
    if args.configFile:
        cfg_path = Path(args.configFile)
        if not cfg_path.exists():
            error(f'Configuration file "{args.configFile}" doesn\'t exist')
        try:
            parsed_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            error(f"JSON syntax error in {args.configFile}: {exc}")
        if not isinstance(parsed_cfg, dict):
            error(f'Configuration file "{args.configFile}" must contain a JSON object')
        mermaid_config.update(parsed_cfg)

    if args.puppeteerConfigFile:
        pptr_path = Path(args.puppeteerConfigFile)
        if not pptr_path.exists():
            error(f'Configuration file "{args.puppeteerConfigFile}" doesn\'t exist')
        try:
            json.loads(pptr_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            error(f"JSON syntax error in {args.puppeteerConfigFile}: {exc}")
        # no browser is launched by this implementation; the config is
        # validated and accepted for interface compatibility only.

    if args.cssFile:
        css_path = Path(args.cssFile)
        if not css_path.exists():
            error(f'CSS file "{args.cssFile}" doesn\'t exist')
        if "themeCSS" in mermaid_config:
            warn(f'Overriding existing themeCSS in mermaidConfig with CSS from "{args.cssFile}"')
        mermaid_config["themeCSS"] = css_path.read_text(encoding="utf-8")

    set_quiet(quiet)

    pool = ThreadPoolExecutor(max_workers=max(1, int(args.jobs)))
    try:
        run(
            input_path,
            output,
            {
                "puppeteerConfig": {},
                "quiet": quiet,
                "outputFormat": outputFormat,
                "limiter": _pool_limiter(pool),
                "jobs": args.jobs,
                "parseMMDOptions": {
                    "mermaidConfig": mermaid_config,
                    "backgroundColor": args.backgroundColor,
                    "fontEmbed": args.font_embed,
                    "pdfPaperFormat": args.pdf_paper_format,
                    "size": size,
                    "viewport": {
                        "width": (size + 16) if size else 0,
                        "height": (size + 16) if size else 0,
                        "deviceScaleFactor": args.scale,
                    },
                    "svgId": args.svgId,
                    "iconPacks": icon_packs,
                },
                "artefacts": args.artefacts,
            },
        )
    finally:
        pool.shutdown()


def cli(argv: Sequence[str] | None = None) -> int:
    """Run the mmdc CLI (mirror of the JS ``cli``).

    Returns 0 on success; failures are printed in red to stderr and the
    process exits with code 1 (via :func:`error`, like upstream).
    """
    try:
        _cli(argv)
        return 0
    except SystemExit:
        raise
    except MermaidError as exc:
        error(str(exc))
    except OSError as exc:
        error(str(exc))
    except Exception as exc:  # pragma: no cover - defensive mirror of cli.js
        error(f"{type(exc).__name__}: {exc}")
    return 1


#: Pythonic alias used by ``python mmdc.py``.
def main(argv: Sequence[str] | None = None) -> int:
    return cli(argv)


if __name__ == "__main__":
    sys.exit(main())
