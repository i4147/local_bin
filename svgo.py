#!/data/data/com.termux/files/usr/bin/env python

import base64
import functools
import json
import math
import os
import re
import signal
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import Future
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from html.entities import html5 as _HTML5_ENTITIES
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from xml.parsers import expat

EXIT_SUCCESS: int = 0
EXIT_FAILURE: int = 1


class SvgoError(Exception):
    """Base class for errors raised by the Python svgo port."""


class SvgoParserError(SvgoError):
    def __init__(self, message: str, line: int, column: int, source: str, from_file: str | None = None) -> None:
        self.reason: str = message
        self.line: int = line
        self.column: int = column
        self.source: str = source
        self.message: str = f"{from_file or '<input>'}:{line}:{column}: {message}"
        super().__init__(self.message)

    def __str__(self) -> str:
        lines = self.source.split("\n")
        if len(lines) == 1 and "\r" in lines[0]:
            lines = self.source.split("\r\n")
        else:
            import re as _re

            lines = _re.split(r"\r\n|\r|\n", self.source)
        start_line = max(self.line - 3, 0)
        end_line = min(self.line + 2, len(lines))
        line_number_width = len(str(end_line))
        start_column = max(self.column - 54, 0)
        end_column = max(self.column + 20, 80)
        rendered: list[str] = []
        for index in range(start_line, end_line):
            line_text = lines[index] if index < len(lines) else ""
            line_slice = line_text[start_column:end_column]
            ellipsis_prefix = ""
            ellipsis_suffix = ""
            if start_column != 0:
                ellipsis_prefix = " " if start_column > len(line_text) - 1 else "…"
            if end_column < len(line_text) - 1:
                ellipsis_suffix = "…"
            number = start_line + 1 + index - start_line
            gutter = f" {number:>{line_number_width}} | "
            if number == self.line:
                gutter_spacing = re.sub(r"[^|]", " ", gutter)
                line_spacing = re.sub(
                    r"[^\t]",
                    " ",
                    ellipsis_prefix + line_text[start_column : self.column - 1],
                )
                spacing = gutter_spacing + line_spacing
                rendered.append(f">{gutter}{ellipsis_prefix}{line_slice}{ellipsis_suffix}\n {spacing}^")
            else:
                rendered.append(f" {gutter}{ellipsis_prefix}{line_slice}{ellipsis_suffix}")
        code = "\n".join(rendered)
        return f"SvgoParserError: {self.message}\n\n{code}\n"


class SvgoConfigError(SvgoError):
    """Raised for malformed configuration (files or inline values)."""


def _js_round(value: float) -> float:
    return math.floor(value + 0.5)


def js_to_fixed_str(value: float, precision: int) -> str:
    quantum = Decimal(1).scaleb(-precision)
    decimal_value = Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)
    if decimal_value == 0:
        decimal_value = abs(decimal_value)
    return f"{decimal_value:f}"


def js_to_fixed(value: float, precision: int) -> float:
    return float(js_to_fixed_str(value, precision))


def tools_to_fixed(value: float, precision: int) -> float:
    power = 10.0**precision
    return _js_round(value * power) / power


def js_number_string(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    if value != value:
        return "NaN"
    if value == math.inf:
        return "Infinity"
    if value == -math.inf:
        return "-Infinity"
    if value == 0.0:
        return "-0" if math.copysign(1.0, value) < 0 else "0"
    if -1e21 < value < 1e21 and value.is_integer():
        return str(int(value))
    text = repr(value)
    if "e" in text or "E" in text:
        mantissa, _, exponent_text = text.replace("E", "e").partition("e")
        exponent = int(exponent_text)
        if -7 < exponent < 21:
            decimal_value = Decimal(mantissa).scaleb(exponent)
            return f"{decimal_value:f}"
        mantissa = mantissa.rstrip("0").rstrip(".")
        sign = "+" if exponent >= 0 else ""
        return f"{mantissa}e{sign}{exponent}"
    if text.endswith(".0"):
        return text[:-2]
    return text


def remove_leading_zero(value: float | int) -> str:
    text = js_number_string(value)
    if 0 < value < 1 and text.startswith("0"):
        return text[1:]
    if -1 < value < 0 and len(text) > 1 and text[1] == "0":
        return text[0] + text[2:]
    return text


def js_parse_float(text: str) -> float | None:
    match = re.match(r"\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text)
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def js_parse_int(text: str) -> float:
    match = re.match(r"\s*[+-]?\d+", text)
    if match is None:
        return float("nan")
    return float(match.group(0))


ELEMS_GROUPS: dict[str, frozenset[str]] = {
    "animation": frozenset(("animate", "animateColor", "animateMotion", "animateTransform", "set")),
    "descriptive": frozenset(("desc", "metadata", "title")),
    "shape": frozenset(("circle", "ellipse", "line", "path", "polygon", "polyline", "rect")),
    "structural": frozenset(("defs", "g", "svg", "symbol", "use")),
    "paintServer": frozenset(("hatch", "linearGradient", "meshGradient", "pattern", "radialGradient", "solidColor")),
    "nonRendering": frozenset((
        "clipPath",
        "filter",
        "linearGradient",
        "marker",
        "mask",
        "pattern",
        "radialGradient",
        "solidColor",
        "symbol",
    )),
    "container": frozenset((
        "a",
        "defs",
        "foreignObject",
        "g",
        "marker",
        "mask",
        "missing-glyph",
        "pattern",
        "svg",
        "switch",
        "symbol",
    )),
    "textContent": frozenset((
        "a",
        "altGlyph",
        "altGlyphDef",
        "altGlyphItem",
        "glyph",
        "glyphRef",
        "text",
        "textPath",
        "tref",
        "tspan",
    )),
    "textContentChild": frozenset(("altGlyph", "textPath", "tref", "tspan")),
    "lightSource": frozenset((
        "feDiffuseLighting",
        "feDistantLight",
        "fePointLight",
        "feSpecularLighting",
        "feSpotLight",
    )),
    "filterPrimitive": frozenset((
        "feBlend",
        "feColorMatrix",
        "feComponentTransfer",
        "feComposite",
        "feConvolveMatrix",
        "feDiffuseLighting",
        "feDisplacementMap",
        "feDropShadow",
        "feFlood",
        "feFuncA",
        "feFuncB",
        "feFuncG",
        "feFuncR",
        "feGaussianBlur",
        "feImage",
        "feMerge",
        "feMergeNode",
        "feMorphology",
        "feOffset",
        "feSpecularLighting",
        "feTile",
        "feTurbulence",
    )),
}

TEXT_ELEMS: frozenset[str] = ELEMS_GROUPS["textContent"] | frozenset(("pre", "title"))

PATH_ELEMS: frozenset[str] = frozenset({"glyph", "missing-glyph", "path"})

ATTRS_GROUPS: dict[str, frozenset[str]] = {
    "animationAddition": frozenset(("additive", "accumulate")),
    "animationAttributeTarget": frozenset(("attributeType", "attributeName")),
    "animationEvent": frozenset(("onbegin", "onend", "onrepeat", "onload")),
    "animationTiming": frozenset(("begin", "dur", "end", "fill", "max", "min", "repeatCount", "repeatDur", "restart")),
    "animationValue": frozenset(("by", "calcMode", "from", "keySplines", "keyTimes", "to", "values")),
    "conditionalProcessing": frozenset(("requiredExtensions", "requiredFeatures", "systemLanguage")),
    "core": frozenset(("id", "tabindex", "xml:base", "xml:lang", "xml:space")),
    "graphicalEvent": frozenset((
        "onactivate",
        "onclick",
        "onfocusin",
        "onfocusout",
        "onload",
        "onmousedown",
        "onmousemove",
        "onmouseout",
        "onmouseover",
        "onmouseup",
    )),
    "presentation": frozenset((
        "alignment-baseline",
        "baseline-shift",
        "clip-path",
        "clip-rule",
        "clip",
        "color-interpolation-filters",
        "color-interpolation",
        "color-profile",
        "color-rendering",
        "color",
        "cursor",
        "direction",
        "display",
        "dominant-baseline",
        "enable-background",
        "fill-opacity",
        "fill-rule",
        "fill",
        "filter",
        "flood-color",
        "flood-opacity",
        "font-family",
        "font-size-adjust",
        "font-size",
        "font-stretch",
        "font-style",
        "font-variant",
        "font-weight",
        "glyph-orientation-horizontal",
        "glyph-orientation-vertical",
        "image-rendering",
        "letter-spacing",
        "lighting-color",
        "marker-end",
        "marker-mid",
        "marker-start",
        "mask",
        "opacity",
        "overflow",
        "paint-order",
        "pointer-events",
        "shape-rendering",
        "stop-color",
        "stop-opacity",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-opacity",
        "stroke-width",
        "stroke",
        "text-anchor",
        "text-decoration",
        "text-overflow",
        "text-rendering",
        "transform-origin",
        "transform",
        "unicode-bidi",
        "vector-effect",
        "visibility",
        "word-spacing",
        "writing-mode",
    )),
    "xlink": frozenset((
        "xlink:actuate",
        "xlink:arcrole",
        "xlink:href",
        "xlink:role",
        "xlink:show",
        "xlink:title",
        "xlink:type",
    )),
    "documentEvent": frozenset(("onabort", "onerror", "onresize", "onscroll", "onunload", "onzoom")),
    "documentElementEvent": frozenset(("oncopy", "oncut", "onpaste")),
    "globalEvent": frozenset((
        "oncancel",
        "oncanplay",
        "oncanplaythrough",
        "onchange",
        "onclick",
        "onclose",
        "oncuechange",
        "ondblclick",
        "ondrag",
        "ondragend",
        "ondragenter",
        "ondragleave",
        "ondragover",
        "ondragstart",
        "ondrop",
        "ondurationchange",
        "onemptied",
        "onended",
        "onerror",
        "onfocus",
        "oninput",
        "oninvalid",
        "onkeydown",
        "onkeypress",
        "onkeyup",
        "onload",
        "onloadeddata",
        "onloadedmetadata",
        "onloadstart",
        "onmousedown",
        "onmouseenter",
        "onmouseleave",
        "onmousemove",
        "onmouseout",
        "onmouseover",
        "onmouseup",
        "onmousewheel",
        "onpause",
        "onplay",
        "onplaying",
        "onprogress",
        "onratechange",
        "onreset",
        "onresize",
        "onscroll",
        "onseeked",
        "onseeking",
        "onselect",
        "onshow",
        "onstalled",
        "onsubmit",
        "onsuspend",
        "ontimeupdate",
        "ontoggle",
        "onvolumechange",
        "onwaiting",
    )),
    "filterPrimitive": frozenset(("x", "y", "width", "height", "result")),
    "transferFunction": frozenset(("amplitude", "exponent", "intercept", "offset", "slope", "tableValues", "type")),
}

ATTRS_GROUPS_DEFAULTS: dict[str, dict[str, str]] = {
    "core": {"xml:space": "default"},
    "presentation": {
        "clip": "auto",
        "clip-path": "none",
        "clip-rule": "nonzero",
        "mask": "none",
        "opacity": "1",
        "stop-color": "#000",
        "stop-opacity": "1",
        "fill-opacity": "1",
        "fill-rule": "nonzero",
        "fill": "#000",
        "stroke": "none",
        "stroke-width": "1",
        "stroke-linecap": "butt",
        "stroke-linejoin": "miter",
        "stroke-miterlimit": "4",
        "stroke-dasharray": "none",
        "stroke-dashoffset": "0",
        "stroke-opacity": "1",
        "paint-order": "normal",
        "vector-effect": "none",
        "display": "inline",
        "visibility": "visible",
        "marker-start": "none",
        "marker-mid": "none",
        "marker-end": "none",
        "color-interpolation": "sRGB",
        "color-interpolation-filters": "linearRGB",
        "color-rendering": "auto",
        "shape-rendering": "auto",
        "text-rendering": "auto",
        "image-rendering": "auto",
        "font-style": "normal",
        "font-variant": "normal",
        "font-weight": "normal",
        "font-stretch": "normal",
        "font-size": "medium",
        "font-size-adjust": "none",
        "kerning": "auto",
        "letter-spacing": "normal",
        "word-spacing": "normal",
        "text-decoration": "none",
        "text-anchor": "start",
        "text-overflow": "clip",
        "writing-mode": "lr-tb",
        "glyph-orientation-vertical": "auto",
        "glyph-orientation-horizontal": "0deg",
        "direction": "ltr",
        "unicode-bidi": "normal",
        "dominant-baseline": "auto",
        "alignment-baseline": "baseline",
        "baseline-shift": "baseline",
    },
    "transferFunction": {"slope": "1", "intercept": "0", "amplitude": "1", "exponent": "1", "offset": "0"},
}

ATTRS_GROUPS_DEPRECATED: dict[str, dict[str, frozenset[str]]] = {
    "animationAttributeTarget": {"unsafe": frozenset(("attributeType",))},
    "conditionalProcessing": {"unsafe": frozenset(("requiredFeatures",))},
    "core": {"unsafe": frozenset(("xml:base", "xml:lang", "xml:space"))},
    "presentation": {
        "unsafe": frozenset((
            "clip",
            "color-profile",
            "enable-background",
            "glyph-orientation-horizontal",
            "glyph-orientation-vertical",
            "kerning",
        ))
    },
}

ELEMS: dict[str, dict[str, Any]] = {
    "a": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation", "xlink")),
        "attrs": frozenset(("class", "externalResourcesRequired", "style", "target", "transform")),
        "defaults": {"target": "_self"},
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
            "tspan",
        )),
    },
    "altGlyph": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation", "xlink")),
        "attrs": frozenset((
            "class",
            "dx",
            "dy",
            "externalResourcesRequired",
            "format",
            "glyphRef",
            "rotate",
            "style",
            "x",
            "y",
        )),
    },
    "altGlyphDef": {"attrsGroups": frozenset(("core",)), "content": frozenset(("glyphRef",))},
    "altGlyphItem": {"attrsGroups": frozenset(("core",)), "content": frozenset(("glyphRef", "altGlyphItem"))},
    "animate": {
        "attrsGroups": frozenset((
            "animationAddition",
            "animationAttributeTarget",
            "animationEvent",
            "animationTiming",
            "animationValue",
            "conditionalProcessing",
            "core",
            "presentation",
            "xlink",
        )),
        "attrs": frozenset(("externalResourcesRequired",)),
        "contentGroups": frozenset(("descriptive",)),
    },
    "animateColor": {
        "attrsGroups": frozenset((
            "animationAddition",
            "animationAttributeTarget",
            "animationEvent",
            "animationTiming",
            "animationValue",
            "conditionalProcessing",
            "core",
            "presentation",
            "xlink",
        )),
        "attrs": frozenset(("externalResourcesRequired",)),
        "contentGroups": frozenset(("descriptive",)),
    },
    "animateMotion": {
        "attrsGroups": frozenset((
            "animationAddition",
            "animationEvent",
            "animationTiming",
            "animationValue",
            "conditionalProcessing",
            "core",
            "xlink",
        )),
        "attrs": frozenset(("externalResourcesRequired", "keyPoints", "origin", "path", "rotate")),
        "defaults": {"rotate": "0"},
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("mpath",)),
    },
    "animateTransform": {
        "attrsGroups": frozenset((
            "animationAddition",
            "animationAttributeTarget",
            "animationEvent",
            "animationTiming",
            "animationValue",
            "conditionalProcessing",
            "core",
            "xlink",
        )),
        "attrs": frozenset(("externalResourcesRequired", "type")),
        "contentGroups": frozenset(("descriptive",)),
    },
    "circle": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset(("class", "cx", "cy", "externalResourcesRequired", "r", "style", "transform")),
        "defaults": {"cx": "0", "cy": "0"},
        "contentGroups": frozenset(("animation", "descriptive")),
    },
    "clipPath": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "presentation")),
        "attrs": frozenset(("class", "clipPathUnits", "externalResourcesRequired", "style", "transform")),
        "defaults": {"clipPathUnits": "userSpaceOnUse"},
        "contentGroups": frozenset(("animation", "descriptive", "shape")),
        "content": frozenset(("text", "use")),
    },
    "color-profile": {
        "attrsGroups": frozenset(("core", "xlink")),
        "attrs": frozenset(("local", "name", "rendering-intent")),
        "defaults": {"name": "sRGB", "rendering-intent": "auto"},
        "deprecated": {"unsafe": frozenset(("name",))},
        "contentGroups": frozenset(("descriptive",)),
    },
    "cursor": {
        "attrsGroups": frozenset(("core", "conditionalProcessing", "xlink")),
        "attrs": frozenset(("externalResourcesRequired", "x", "y")),
        "defaults": {"x": "0", "y": "0"},
        "contentGroups": frozenset(("descriptive",)),
    },
    "defs": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset(("class", "externalResourcesRequired", "style", "transform")),
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
        )),
    },
    "desc": {"attrsGroups": frozenset(("core",)), "attrs": frozenset(("class", "style"))},
    "ellipse": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset(("class", "cx", "cy", "externalResourcesRequired", "rx", "ry", "style", "transform")),
        "defaults": {"cx": "0", "cy": "0"},
        "contentGroups": frozenset(("animation", "descriptive")),
    },
    "feBlend": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "style", "in", "in2", "mode")),
        "defaults": {"mode": "normal"},
        "content": frozenset(("animate", "set")),
    },
    "feColorMatrix": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "style", "in", "type", "values")),
        "defaults": {"type": "matrix"},
        "content": frozenset(("animate", "set")),
    },
    "feComponentTransfer": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "style", "in")),
        "content": frozenset(("feFuncA", "feFuncB", "feFuncG", "feFuncR")),
    },
    "feComposite": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "in", "in2", "k1", "k2", "k3", "k4", "operator", "style")),
        "defaults": {"operator": "over", "k1": "0", "k2": "0", "k3": "0", "k4": "0"},
        "content": frozenset(("animate", "set")),
    },
    "feConvolveMatrix": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset((
            "class",
            "in",
            "kernelMatrix",
            "order",
            "style",
            "bias",
            "divisor",
            "edgeMode",
            "targetX",
            "targetY",
            "kernelUnitLength",
            "preserveAlpha",
        )),
        "defaults": {"order": "3", "bias": "0", "edgeMode": "duplicate", "preserveAlpha": "false"},
        "content": frozenset(("animate", "set")),
    },
    "feDiffuseLighting": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "diffuseConstant", "in", "kernelUnitLength", "style", "surfaceScale")),
        "defaults": {"surfaceScale": "1", "diffuseConstant": "1"},
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("feDistantLight", "fePointLight", "feSpotLight")),
    },
    "feDisplacementMap": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "in", "in2", "scale", "style", "xChannelSelector", "yChannelSelector")),
        "defaults": {"scale": "0", "xChannelSelector": "A", "yChannelSelector": "A"},
        "content": frozenset(("animate", "set")),
    },
    "feDistantLight": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset(("azimuth", "elevation")),
        "defaults": {"azimuth": "0", "elevation": "0"},
        "content": frozenset(("animate", "set")),
    },
    "feFlood": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "style")),
        "content": frozenset(("animate", "animateColor", "set")),
    },
    "feFuncA": {"attrsGroups": frozenset(("core", "transferFunction")), "content": frozenset(("set", "animate"))},
    "feFuncB": {"attrsGroups": frozenset(("core", "transferFunction")), "content": frozenset(("set", "animate"))},
    "feFuncG": {"attrsGroups": frozenset(("core", "transferFunction")), "content": frozenset(("set", "animate"))},
    "feFuncR": {"attrsGroups": frozenset(("core", "transferFunction")), "content": frozenset(("set", "animate"))},
    "feGaussianBlur": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "style", "in", "stdDeviation")),
        "defaults": {"stdDeviation": "0"},
        "content": frozenset(("set", "animate")),
    },
    "feImage": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive", "xlink")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "href",
            "preserveAspectRatio",
            "style",
            "xlink:href",
        )),
        "defaults": {"preserveAspectRatio": "xMidYMid meet"},
        "content": frozenset(("animate", "animateTransform", "set")),
    },
    "feMerge": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "style")),
        "content": frozenset(("feMergeNode",)),
    },
    "feMergeNode": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset(("in",)),
        "content": frozenset(("animate", "set")),
    },
    "feMorphology": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "style", "in", "operator", "radius")),
        "defaults": {"operator": "erode", "radius": "0"},
        "content": frozenset(("animate", "set")),
    },
    "feOffset": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "style", "in", "dx", "dy")),
        "defaults": {"dx": "0", "dy": "0"},
        "content": frozenset(("animate", "set")),
    },
    "fePointLight": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset(("x", "y", "z")),
        "defaults": {"x": "0", "y": "0", "z": "0"},
        "content": frozenset(("animate", "set")),
    },
    "feSpecularLighting": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset((
            "class",
            "in",
            "kernelUnitLength",
            "specularConstant",
            "specularExponent",
            "style",
            "surfaceScale",
        )),
        "defaults": {"surfaceScale": "1", "specularConstant": "1", "specularExponent": "1"},
        "contentGroups": frozenset(("descriptive", "lightSource")),
    },
    "feSpotLight": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset((
            "limitingConeAngle",
            "pointsAtX",
            "pointsAtY",
            "pointsAtZ",
            "specularExponent",
            "x",
            "y",
            "z",
        )),
        "defaults": {
            "x": "0",
            "y": "0",
            "z": "0",
            "pointsAtX": "0",
            "pointsAtY": "0",
            "pointsAtZ": "0",
            "specularExponent": "1",
        },
        "content": frozenset(("animate", "set")),
    },
    "feTile": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("class", "style", "in")),
        "content": frozenset(("animate", "set")),
    },
    "feTurbulence": {
        "attrsGroups": frozenset(("core", "presentation", "filterPrimitive")),
        "attrs": frozenset(("baseFrequency", "class", "numOctaves", "seed", "stitchTiles", "style", "type")),
        "defaults": {
            "baseFrequency": "0",
            "numOctaves": "1",
            "seed": "0",
            "stitchTiles": "noStitch",
            "type": "turbulence",
        },
        "content": frozenset(("animate", "set")),
    },
    "filter": {
        "attrsGroups": frozenset(("core", "presentation", "xlink")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "filterRes",
            "filterUnits",
            "height",
            "href",
            "primitiveUnits",
            "style",
            "width",
            "x",
            "xlink:href",
            "y",
        )),
        "defaults": {"primitiveUnits": "userSpaceOnUse", "x": "-10%", "y": "-10%", "width": "120%", "height": "120%"},
        "deprecated": {"unsafe": frozenset(("filterRes",))},
        "contentGroups": frozenset(("descriptive", "filterPrimitive")),
        "content": frozenset(("animate", "set")),
    },
    "font": {
        "attrsGroups": frozenset(("core", "presentation")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "horiz-adv-x",
            "horiz-origin-x",
            "horiz-origin-y",
            "style",
            "vert-adv-y",
            "vert-origin-x",
            "vert-origin-y",
        )),
        "defaults": {"horiz-origin-x": "0", "horiz-origin-y": "0"},
        "deprecated": {
            "unsafe": frozenset(("horiz-origin-x", "horiz-origin-y", "vert-adv-y", "vert-origin-x", "vert-origin-y"))
        },
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("font-face", "glyph", "hkern", "missing-glyph", "vkern")),
    },
    "font-face": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset((
            "font-family",
            "font-style",
            "font-variant",
            "font-weight",
            "font-stretch",
            "font-size",
            "unicode-range",
            "units-per-em",
            "panose-1",
            "stemv",
            "stemh",
            "slope",
            "cap-height",
            "x-height",
            "accent-height",
            "ascent",
            "descent",
            "widths",
            "bbox",
            "ideographic",
            "alphabetic",
            "mathematical",
            "hanging",
            "v-ideographic",
            "v-alphabetic",
            "v-mathematical",
            "v-hanging",
            "underline-position",
            "underline-thickness",
            "strikethrough-position",
            "strikethrough-thickness",
            "overline-position",
            "overline-thickness",
        )),
        "defaults": {
            "font-style": "all",
            "font-variant": "normal",
            "font-weight": "all",
            "font-stretch": "normal",
            "unicode-range": "U+0-10FFFF",
            "units-per-em": "1000",
            "panose-1": "0 0 0 0 0 0 0 0 0 0",
            "slope": "0",
        },
        "deprecated": {
            "unsafe": frozenset((
                "accent-height",
                "alphabetic",
                "ascent",
                "bbox",
                "cap-height",
                "descent",
                "hanging",
                "ideographic",
                "mathematical",
                "panose-1",
                "slope",
                "stemh",
                "stemv",
                "unicode-range",
                "units-per-em",
                "v-alphabetic",
                "v-hanging",
                "v-ideographic",
                "v-mathematical",
                "widths",
                "x-height",
            ))
        },
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("font-face-src",)),
    },
    "font-face-format": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset(("string",)),
        "deprecated": {"unsafe": frozenset(("string",))},
    },
    "font-face-name": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset(("name",)),
        "deprecated": {"unsafe": frozenset(("name",))},
    },
    "font-face-src": {"attrsGroups": frozenset(("core",)), "content": frozenset(("font-face-name", "font-face-uri"))},
    "font-face-uri": {
        "attrsGroups": frozenset(("core", "xlink")),
        "attrs": frozenset(("href", "xlink:href")),
        "content": frozenset(("font-face-format",)),
    },
    "foreignObject": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset(("class", "externalResourcesRequired", "height", "style", "transform", "width", "x", "y")),
        "defaults": {"x": "0", "y": "0"},
    },
    "g": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset(("class", "externalResourcesRequired", "style", "transform")),
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
        )),
    },
    "glyph": {
        "attrsGroups": frozenset(("core", "presentation")),
        "attrs": frozenset((
            "arabic-form",
            "class",
            "d",
            "glyph-name",
            "horiz-adv-x",
            "lang",
            "orientation",
            "style",
            "unicode",
            "vert-adv-y",
            "vert-origin-x",
            "vert-origin-y",
        )),
        "defaults": {"arabic-form": "initial"},
        "deprecated": {
            "unsafe": frozenset((
                "arabic-form",
                "glyph-name",
                "horiz-adv-x",
                "orientation",
                "unicode",
                "vert-adv-y",
                "vert-origin-x",
                "vert-origin-y",
            ))
        },
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
        )),
    },
    "glyphRef": {
        "attrsGroups": frozenset(("core", "presentation")),
        "attrs": frozenset(("class", "d", "horiz-adv-x", "style", "vert-adv-y", "vert-origin-x", "vert-origin-y")),
        "deprecated": {"unsafe": frozenset(("horiz-adv-x", "vert-adv-y", "vert-origin-x", "vert-origin-y"))},
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
        )),
    },
    "hatch": {
        "attrsGroups": frozenset(("core", "presentation", "xlink")),
        "attrs": frozenset((
            "class",
            "hatchContentUnits",
            "hatchUnits",
            "pitch",
            "rotate",
            "style",
            "transform",
            "x",
            "y",
        )),
        "defaults": {
            "hatchUnits": "objectBoundingBox",
            "hatchContentUnits": "userSpaceOnUse",
            "x": "0",
            "y": "0",
            "pitch": "0",
            "rotate": "0",
        },
        "contentGroups": frozenset(("animation", "descriptive")),
        "content": frozenset(("hatchPath",)),
    },
    "hatchPath": {
        "attrsGroups": frozenset(("core", "presentation", "xlink")),
        "attrs": frozenset(("class", "style", "d", "offset")),
        "defaults": {"offset": "0"},
        "contentGroups": frozenset(("animation", "descriptive")),
    },
    "hkern": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset(("u1", "g1", "u2", "g2", "k")),
        "deprecated": {"unsafe": frozenset(("g1", "g2", "k", "u1", "u2"))},
    },
    "image": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation", "xlink")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "height",
            "href",
            "preserveAspectRatio",
            "style",
            "transform",
            "width",
            "x",
            "xlink:href",
            "y",
        )),
        "defaults": {"x": "0", "y": "0", "preserveAspectRatio": "xMidYMid meet"},
        "contentGroups": frozenset(("animation", "descriptive")),
    },
    "line": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset(("class", "externalResourcesRequired", "style", "transform", "x1", "x2", "y1", "y2")),
        "defaults": {"x1": "0", "y1": "0", "x2": "0", "y2": "0"},
        "contentGroups": frozenset(("animation", "descriptive")),
    },
    "linearGradient": {
        "attrsGroups": frozenset(("core", "presentation", "xlink")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "gradientTransform",
            "gradientUnits",
            "href",
            "spreadMethod",
            "style",
            "x1",
            "x2",
            "xlink:href",
            "y1",
            "y2",
        )),
        "defaults": {"x1": "0", "y1": "0", "x2": "100%", "y2": "0", "spreadMethod": "pad"},
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("animate", "animateTransform", "set", "stop")),
    },
    "marker": {
        "attrsGroups": frozenset(("core", "presentation")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "markerHeight",
            "markerUnits",
            "markerWidth",
            "orient",
            "preserveAspectRatio",
            "refX",
            "refY",
            "style",
            "viewBox",
        )),
        "defaults": {"markerUnits": "strokeWidth", "refX": "0", "refY": "0", "markerWidth": "3", "markerHeight": "3"},
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
        )),
    },
    "mask": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "presentation")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "height",
            "mask-type",
            "maskContentUnits",
            "maskUnits",
            "style",
            "width",
            "x",
            "y",
        )),
        "defaults": {
            "maskUnits": "objectBoundingBox",
            "maskContentUnits": "userSpaceOnUse",
            "x": "-10%",
            "y": "-10%",
            "width": "120%",
            "height": "120%",
        },
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
        )),
    },
    "metadata": {"attrsGroups": frozenset(("core",))},
    "missing-glyph": {
        "attrsGroups": frozenset(("core", "presentation")),
        "attrs": frozenset(("class", "d", "horiz-adv-x", "style", "vert-adv-y", "vert-origin-x", "vert-origin-y")),
        "deprecated": {"unsafe": frozenset(("horiz-adv-x", "vert-adv-y", "vert-origin-x", "vert-origin-y"))},
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
        )),
    },
    "mpath": {
        "attrsGroups": frozenset(("core", "xlink")),
        "attrs": frozenset(("externalResourcesRequired", "href", "xlink:href")),
        "contentGroups": frozenset(("descriptive",)),
    },
    "path": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset(("class", "d", "externalResourcesRequired", "pathLength", "style", "transform")),
        "contentGroups": frozenset(("animation", "descriptive")),
    },
    "pattern": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "presentation", "xlink")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "height",
            "href",
            "patternContentUnits",
            "patternTransform",
            "patternUnits",
            "preserveAspectRatio",
            "style",
            "viewBox",
            "width",
            "x",
            "xlink:href",
            "y",
        )),
        "defaults": {
            "patternUnits": "objectBoundingBox",
            "patternContentUnits": "userSpaceOnUse",
            "x": "0",
            "y": "0",
            "width": "0",
            "height": "0",
            "preserveAspectRatio": "xMidYMid meet",
        },
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
        )),
    },
    "polygon": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset(("class", "externalResourcesRequired", "points", "style", "transform")),
        "contentGroups": frozenset(("animation", "descriptive")),
    },
    "polyline": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset(("class", "externalResourcesRequired", "points", "style", "transform")),
        "contentGroups": frozenset(("animation", "descriptive")),
    },
    "radialGradient": {
        "attrsGroups": frozenset(("core", "presentation", "xlink")),
        "attrs": frozenset((
            "class",
            "cx",
            "cy",
            "externalResourcesRequired",
            "fr",
            "fx",
            "fy",
            "gradientTransform",
            "gradientUnits",
            "href",
            "r",
            "spreadMethod",
            "style",
            "xlink:href",
        )),
        "defaults": {"gradientUnits": "objectBoundingBox", "cx": "50%", "cy": "50%", "r": "50%"},
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("animate", "animateTransform", "set", "stop")),
    },
    "meshGradient": {
        "attrsGroups": frozenset(("core", "presentation", "xlink")),
        "attrs": frozenset(("class", "style", "x", "y", "gradientUnits", "transform")),
        "contentGroups": frozenset(("descriptive", "paintServer", "animation")),
        "content": frozenset(("meshRow",)),
    },
    "meshRow": {
        "attrsGroups": frozenset(("core", "presentation")),
        "attrs": frozenset(("class", "style")),
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("meshPatch",)),
    },
    "meshPatch": {
        "attrsGroups": frozenset(("core", "presentation")),
        "attrs": frozenset(("class", "style")),
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("stop",)),
    },
    "rect": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "height",
            "rx",
            "ry",
            "style",
            "transform",
            "width",
            "x",
            "y",
        )),
        "defaults": {"x": "0", "y": "0"},
        "contentGroups": frozenset(("animation", "descriptive")),
    },
    "script": {
        "attrsGroups": frozenset(("core", "xlink")),
        "attrs": frozenset(("externalResourcesRequired", "type", "href", "xlink:href")),
    },
    "set": {
        "attrsGroups": frozenset((
            "animation",
            "animationAttributeTarget",
            "animationTiming",
            "conditionalProcessing",
            "core",
            "xlink",
        )),
        "attrs": frozenset(("externalResourcesRequired", "to")),
        "contentGroups": frozenset(("descriptive",)),
    },
    "solidColor": {
        "attrsGroups": frozenset(("core", "presentation")),
        "attrs": frozenset(("class", "style")),
        "contentGroups": frozenset(("paintServer",)),
    },
    "stop": {
        "attrsGroups": frozenset(("core", "presentation")),
        "attrs": frozenset(("class", "style", "offset", "path")),
        "content": frozenset(("animate", "animateColor", "set")),
    },
    "style": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset(("type", "media", "title")),
        "defaults": {"type": "text/css"},
    },
    "svg": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "documentEvent", "graphicalEvent", "presentation")),
        "attrs": frozenset((
            "baseProfile",
            "class",
            "contentScriptType",
            "contentStyleType",
            "height",
            "preserveAspectRatio",
            "style",
            "version",
            "viewBox",
            "width",
            "x",
            "y",
            "zoomAndPan",
        )),
        "defaults": {
            "x": "0",
            "y": "0",
            "width": "100%",
            "height": "100%",
            "preserveAspectRatio": "xMidYMid meet",
            "zoomAndPan": "magnify",
            "version": "1.1",
            "baseProfile": "none",
            "contentScriptType": "application/ecmascript",
            "contentStyleType": "text/css",
        },
        "deprecated": {
            "safe": frozenset(("version",)),
            "unsafe": frozenset(("baseProfile", "contentScriptType", "contentStyleType", "zoomAndPan")),
        },
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
        )),
    },
    "switch": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset(("class", "externalResourcesRequired", "style", "transform")),
        "contentGroups": frozenset(("animation", "descriptive", "shape")),
        "content": frozenset(("a", "foreignObject", "g", "image", "svg", "switch", "text", "use")),
    },
    "symbol": {
        "attrsGroups": frozenset(("core", "graphicalEvent", "presentation")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "preserveAspectRatio",
            "refX",
            "refY",
            "style",
            "viewBox",
        )),
        "defaults": {"refX": "0", "refY": "0"},
        "contentGroups": frozenset(("animation", "descriptive", "paintServer", "shape", "structural")),
        "content": frozenset((
            "a",
            "altGlyphDef",
            "clipPath",
            "color-profile",
            "cursor",
            "filter",
            "font-face",
            "font",
            "foreignObject",
            "image",
            "marker",
            "mask",
            "pattern",
            "script",
            "style",
            "switch",
            "text",
            "view",
        )),
    },
    "text": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset((
            "class",
            "dx",
            "dy",
            "externalResourcesRequired",
            "lengthAdjust",
            "rotate",
            "style",
            "textLength",
            "transform",
            "x",
            "y",
        )),
        "defaults": {"x": "0", "y": "0", "lengthAdjust": "spacing"},
        "contentGroups": frozenset(("animation", "descriptive", "textContentChild")),
        "content": frozenset(("a",)),
    },
    "textPath": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation", "xlink")),
        "attrs": frozenset((
            "class",
            "d",
            "externalResourcesRequired",
            "href",
            "method",
            "spacing",
            "startOffset",
            "style",
            "xlink:href",
        )),
        "defaults": {"startOffset": "0", "method": "align", "spacing": "exact"},
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("a", "altGlyph", "animate", "animateColor", "set", "tref", "tspan")),
    },
    "title": {"attrsGroups": frozenset(("core",)), "attrs": frozenset(("class", "style"))},
    "tref": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation", "xlink")),
        "attrs": frozenset(("class", "externalResourcesRequired", "href", "style", "xlink:href")),
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("animate", "animateColor", "set")),
    },
    "tspan": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation")),
        "attrs": frozenset((
            "class",
            "dx",
            "dy",
            "externalResourcesRequired",
            "lengthAdjust",
            "rotate",
            "style",
            "textLength",
            "x",
            "y",
        )),
        "contentGroups": frozenset(("descriptive",)),
        "content": frozenset(("a", "altGlyph", "animate", "animateColor", "set", "tref", "tspan")),
    },
    "use": {
        "attrsGroups": frozenset(("conditionalProcessing", "core", "graphicalEvent", "presentation", "xlink")),
        "attrs": frozenset((
            "class",
            "externalResourcesRequired",
            "height",
            "href",
            "style",
            "transform",
            "width",
            "x",
            "xlink:href",
            "y",
        )),
        "defaults": {"x": "0", "y": "0"},
        "contentGroups": frozenset(("animation", "descriptive")),
    },
    "view": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset(("externalResourcesRequired", "preserveAspectRatio", "viewBox", "viewTarget", "zoomAndPan")),
        "deprecated": {"unsafe": frozenset(("viewTarget", "zoomAndPan"))},
        "contentGroups": frozenset(("descriptive",)),
    },
    "vkern": {
        "attrsGroups": frozenset(("core",)),
        "attrs": frozenset(("u1", "g1", "u2", "g2", "k")),
        "deprecated": {"unsafe": frozenset(("g1", "g2", "k", "u1", "u2"))},
    },
}

EDITOR_NAMESPACES: frozenset[str] = frozenset((
    "http://creativecommons.org/ns#",
    "http://inkscape.sourceforge.net/DTD/sodipodi-0.dtd",
    "http://krita.org/namespaces/svg/krita",
    "http://ns.adobe.com/AdobeIllustrator/10.0/",
    "http://ns.adobe.com/AdobeSVGViewerExtensions/3.0/",
    "http://ns.adobe.com/Extensibility/1.0/",
    "http://ns.adobe.com/Flows/1.0/",
    "http://ns.adobe.com/GenericCustomNamespace/1.0/",
    "http://ns.adobe.com/Graphs/1.0/",
    "http://ns.adobe.com/ImageReplacement/1.0/",
    "http://ns.adobe.com/SaveForWeb/1.0/",
    "http://ns.adobe.com/Variables/1.0/",
    "http://ns.adobe.com/XPath/1.0/",
    "http://purl.org/dc/elements/1.1/",
    "http://schemas.microsoft.com/visio/2003/SVGExtensions/",
    "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    "http://taptrix.com/vectorillustrator/svg_extensions",
    "http://www.bohemiancoding.com/sketch/ns",
    "http://www.figma.com/figma/ns",
    "http://www.inkscape.org/namespaces/inkscape",
    "http://www.serif.com/",
    "http://www.vector.evaxdesign.sk",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "https://boxy-svg.com",
))

REFERENCES_PROPS: frozenset[str] = frozenset((
    "clip-path",
    "color-profile",
    "fill",
    "filter",
    "marker-end",
    "marker-mid",
    "marker-start",
    "mask",
    "stroke",
    "style",
))

INHERITABLE_ATTRS: frozenset[str] = frozenset((
    "clip-rule",
    "color-interpolation-filters",
    "color-interpolation",
    "color-profile",
    "color-rendering",
    "color",
    "cursor",
    "direction",
    "dominant-baseline",
    "fill-opacity",
    "fill-rule",
    "fill",
    "font-family",
    "font-size-adjust",
    "font-size",
    "font-stretch",
    "font-style",
    "font-variant",
    "font-weight",
    "font",
    "glyph-orientation-horizontal",
    "glyph-orientation-vertical",
    "image-rendering",
    "letter-spacing",
    "marker-end",
    "marker-mid",
    "marker-start",
    "marker",
    "paint-order",
    "pointer-events",
    "shape-rendering",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-opacity",
    "stroke-width",
    "stroke",
    "text-anchor",
    "text-rendering",
    "transform",
    "visibility",
    "word-spacing",
    "writing-mode",
))

PRESENTATION_NON_INHERITABLE_GROUP_ATTRS: frozenset[str] = frozenset((
    "clip-path",
    "display",
    "filter",
    "mask",
    "opacity",
    "text-decoration",
    "transform",
    "unicode-bidi",
))

COLORS_NAMES: dict[str, str] = {
    "aliceblue": "#f0f8ff",
    "antiquewhite": "#faebd7",
    "aqua": "#0ff",
    "aquamarine": "#7fffd4",
    "azure": "#f0ffff",
    "beige": "#f5f5dc",
    "bisque": "#ffe4c4",
    "black": "#000",
    "blanchedalmond": "#ffebcd",
    "blue": "#00f",
    "blueviolet": "#8a2be2",
    "brown": "#a52a2a",
    "burlywood": "#deb887",
    "cadetblue": "#5f9ea0",
    "chartreuse": "#7fff00",
    "chocolate": "#d2691e",
    "coral": "#ff7f50",
    "cornflowerblue": "#6495ed",
    "cornsilk": "#fff8dc",
    "crimson": "#dc143c",
    "cyan": "#0ff",
    "darkblue": "#00008b",
    "darkcyan": "#008b8b",
    "darkgoldenrod": "#b8860b",
    "darkgray": "#a9a9a9",
    "darkgreen": "#006400",
    "darkgrey": "#a9a9a9",
    "darkkhaki": "#bdb76b",
    "darkmagenta": "#8b008b",
    "darkolivegreen": "#556b2f",
    "darkorange": "#ff8c00",
    "darkorchid": "#9932cc",
    "darkred": "#8b0000",
    "darksalmon": "#e9967a",
    "darkseagreen": "#8fbc8f",
    "darkslateblue": "#483d8b",
    "darkslategray": "#2f4f4f",
    "darkslategrey": "#2f4f4f",
    "darkturquoise": "#00ced1",
    "darkviolet": "#9400d3",
    "deeppink": "#ff1493",
    "deepskyblue": "#00bfff",
    "dimgray": "#696969",
    "dimgrey": "#696969",
    "dodgerblue": "#1e90ff",
    "firebrick": "#b22222",
    "floralwhite": "#fffaf0",
    "forestgreen": "#228b22",
    "fuchsia": "#f0f",
    "gainsboro": "#dcdcdc",
    "ghostwhite": "#f8f8ff",
    "gold": "#ffd700",
    "goldenrod": "#daa520",
    "gray": "#808080",
    "green": "#008000",
    "greenyellow": "#adff2f",
    "grey": "#808080",
    "honeydew": "#f0fff0",
    "hotpink": "#ff69b4",
    "indianred": "#cd5c5c",
    "indigo": "#4b0082",
    "ivory": "#fffff0",
    "khaki": "#f0e68c",
    "lavender": "#e6e6fa",
    "lavenderblush": "#fff0f5",
    "lawngreen": "#7cfc00",
    "lemonchiffon": "#fffacd",
    "lightblue": "#add8e6",
    "lightcoral": "#f08080",
    "lightcyan": "#e0ffff",
    "lightgoldenrodyellow": "#fafad2",
    "lightgray": "#d3d3d3",
    "lightgreen": "#90ee90",
    "lightgrey": "#d3d3d3",
    "lightpink": "#ffb6c1",
    "lightsalmon": "#ffa07a",
    "lightseagreen": "#20b2aa",
    "lightskyblue": "#87cefa",
    "lightslategray": "#789",
    "lightslategrey": "#789",
    "lightsteelblue": "#b0c4de",
    "lightyellow": "#ffffe0",
    "lime": "#0f0",
    "limegreen": "#32cd32",
    "linen": "#faf0e6",
    "magenta": "#f0f",
    "maroon": "#800000",
    "mediumaquamarine": "#66cdaa",
    "mediumblue": "#0000cd",
    "mediumorchid": "#ba55d3",
    "mediumpurple": "#9370db",
    "mediumseagreen": "#3cb371",
    "mediumslateblue": "#7b68ee",
    "mediumspringgreen": "#00fa9a",
    "mediumturquoise": "#48d1cc",
    "mediumvioletred": "#c71585",
    "midnightblue": "#191970",
    "mintcream": "#f5fffa",
    "mistyrose": "#ffe4e1",
    "moccasin": "#ffe4b5",
    "navajowhite": "#ffdead",
    "navy": "#000080",
    "oldlace": "#fdf5e6",
    "olive": "#808000",
    "olivedrab": "#6b8e23",
    "orange": "#ffa500",
    "orangered": "#ff4500",
    "orchid": "#da70d6",
    "palegoldenrod": "#eee8aa",
    "palegreen": "#98fb98",
    "paleturquoise": "#afeeee",
    "palevioletred": "#db7093",
    "papayawhip": "#ffefd5",
    "peachpuff": "#ffdab9",
    "peru": "#cd853f",
    "pink": "#ffc0cb",
    "plum": "#dda0dd",
    "powderblue": "#b0e0e6",
    "purple": "#800080",
    "rebeccapurple": "#639",
    "red": "#f00",
    "rosybrown": "#bc8f8f",
    "royalblue": "#4169e1",
    "saddlebrown": "#8b4513",
    "salmon": "#fa8072",
    "sandybrown": "#f4a460",
    "seagreen": "#2e8b57",
    "seashell": "#fff5ee",
    "sienna": "#a0522d",
    "silver": "#c0c0c0",
    "skyblue": "#87ceeb",
    "slateblue": "#6a5acd",
    "slategray": "#708090",
    "slategrey": "#708090",
    "snow": "#fffafa",
    "springgreen": "#00ff7f",
    "steelblue": "#4682b4",
    "tan": "#d2b48c",
    "teal": "#008080",
    "thistle": "#d8bfd8",
    "tomato": "#ff6347",
    "turquoise": "#40e0d0",
    "violet": "#ee82ee",
    "wheat": "#f5deb3",
    "white": "#fff",
    "whitesmoke": "#f5f5f5",
    "yellow": "#ff0",
    "yellowgreen": "#9acd32",
}

COLORS_SHORT_NAMES: dict[str, str] = {
    "#f0ffff": "azure",
    "#f5f5dc": "beige",
    "#ffe4c4": "bisque",
    "#a52a2a": "brown",
    "#ff7f50": "coral",
    "#ffd700": "gold",
    "#808080": "gray",
    "#008000": "green",
    "#4b0082": "indigo",
    "#fffff0": "ivory",
    "#f0e68c": "khaki",
    "#faf0e6": "linen",
    "#800000": "maroon",
    "#000080": "navy",
    "#808000": "olive",
    "#ffa500": "orange",
    "#da70d6": "orchid",
    "#cd853f": "peru",
    "#ffc0cb": "pink",
    "#dda0dd": "plum",
    "#800080": "purple",
    "#f00": "red",
    "#ff0000": "red",
    "#fa8072": "salmon",
    "#a0522d": "sienna",
    "#c0c0c0": "silver",
    "#fffafa": "snow",
    "#d2b48c": "tan",
    "#008080": "teal",
    "#ff6347": "tomato",
    "#ee82ee": "violet",
    "#f5deb3": "wheat",
}

COLORS_PROPS: frozenset[str] = frozenset((
    "color",
    "fill",
    "flood-color",
    "lighting-color",
    "stop-color",
    "stroke",
))

PSEUDO_CLASSES: dict[str, frozenset[str]] = {
    "displayState": frozenset(("fullscreen", "modal", "picture-in-picture")),
    "input": frozenset((
        "autofill",
        "blank",
        "checked",
        "default",
        "disabled",
        "enabled",
        "in-range",
        "indeterminate",
        "invalid",
        "optional",
        "out-of-range",
        "placeholder-shown",
        "read-only",
        "read-write",
        "required",
        "user-invalid",
        "valid",
    )),
    "linguistic": frozenset(("dir", "lang")),
    "location": frozenset(("any-link", "link", "local-link", "scope", "target-within", "target", "visited")),
    "resourceState": frozenset(("playing", "paused")),
    "timeDimensional": frozenset(("current", "past", "future")),
    "treeStructural": frozenset((
        "empty",
        "first-child",
        "first-of-type",
        "last-child",
        "last-of-type",
        "nth-child",
        "nth-last-child",
        "nth-last-of-type",
        "nth-of-type",
        "only-child",
        "only-of-type",
        "root",
    )),
    "userAction": frozenset(("active", "focus-visible", "focus-within", "focus", "hover")),
    "functional": frozenset(("is", "not", "where", "has")),
}


ARGS_COUNT_PER_COMMAND: dict[str, int] = {
    "M": 2,
    "m": 2,
    "Z": 0,
    "z": 0,
    "L": 2,
    "l": 2,
    "H": 1,
    "h": 1,
    "V": 1,
    "v": 1,
    "C": 6,
    "c": 6,
    "S": 4,
    "s": 4,
    "Q": 4,
    "q": 4,
    "T": 2,
    "t": 2,
    "A": 7,
    "a": 7,
}

_WHITESPACE: frozenset[str] = frozenset(" \t\r\n")
_DIGITS: frozenset[str] = frozenset("0123456789")


def _read_number(string: str, cursor: int) -> tuple[int, float | None]:
    i = cursor
    value = ""
    state = "none"
    while i < len(string):
        c = string[i]
        if c == "+" or c == "-":
            if state == "none":
                state = "sign"
                value += c
                i += 1
                continue
            if state == "e":
                state = "exponent_sign"
                value += c
                i += 1
                continue
        if c in _DIGITS:
            if state in ("none", "sign", "whole"):
                state = "whole"
                value += c
                i += 1
                continue
            if state in ("decimal_point", "decimal"):
                state = "decimal"
                value += c
                i += 1
                continue
            if state in ("e", "exponent_sign", "exponent"):
                state = "exponent"
                value += c
                i += 1
                continue
        if c == ".":
            if state in ("none", "sign", "whole"):
                state = "decimal_point"
                value += c
                i += 1
                continue
        if c == "E" or c == "e":
            if state in ("whole", "decimal_point", "decimal"):
                state = "e"
                value += c
                i += 1
                continue
        break
    try:
        number = float(value)
    except ValueError:
        return cursor, None
    return i - 1, number


@dataclass
class PathDataItem:
    command: str
    args: list[float] = field(default_factory=list)
    base: list[float] = field(default_factory=lambda: [0.0, 0.0])
    coords: list[float] = field(default_factory=lambda: [0.0, 0.0])


def parse_path_data(string: str) -> list[PathDataItem]:
    path_data: list[PathDataItem] = []
    command: str | None = None
    args: list[float] = []
    args_count = 0
    can_have_comma = False
    had_comma = False
    i = 0
    length = len(string)
    while i < length:
        c = string[i]
        if c in _WHITESPACE:
            i += 1
            continue
        if can_have_comma and c == ",":
            if had_comma:
                break
            had_comma = True
            i += 1
            continue
        if c in ARGS_COUNT_PER_COMMAND:
            if had_comma:
                return path_data
            if command is None:
                if c != "M" and c != "m":
                    return path_data
            elif args:
                return path_data
            command = c
            args = []
            args_count = ARGS_COUNT_PER_COMMAND[command]
            can_have_comma = False
            if args_count == 0:
                path_data.append(PathDataItem(command=command, args=[]))
            i += 1
            continue
        if command is None:
            return path_data
        new_cursor = i
        number: float | None = None
        if command in ("A", "a"):
            position = len(args)
            if position in (0, 1, 2, 5, 6):
                new_cursor, number = _read_number(string, i)
            if position in (3, 4):
                if c == "0":
                    number = 0.0
                if c == "1":
                    number = 1.0
        else:
            new_cursor, number = _read_number(string, i)
        if number is None:
            return path_data
        args.append(number)
        can_have_comma = True
        had_comma = False
        i = new_cursor + 1
        if len(args) == args_count:
            if command in ("A", "a"):
                args[0] = abs(args[0])
                args[1] = abs(args[1])
            path_data.append(PathDataItem(command=command, args=args))
            if command == "M":
                command = "L"
            if command == "m":
                command = "l"
            args = []
    return path_data


def _round_and_stringify(number: float, precision: int | None) -> tuple[str, float]:
    if precision is not None:
        number = tools_to_fixed(number, precision)
    return remove_leading_zero(number), number


def _stringify_args(command: str, args: Sequence[float], precision: int | None, disable_space_after_flags: bool) -> str:
    result = ""
    previous: float | None = None
    for i, raw in enumerate(args):
        rounded_str, rounded = _round_and_stringify(raw, precision)
        if disable_space_after_flags and command in ("A", "a") and i % 7 in (4, 5):
            result += rounded_str
        elif i == 0 or rounded < 0:
            result += rounded_str
        elif previous is not None and previous != math.trunc(previous) and rounded_str[:1] == ".":
            result += rounded_str
        else:
            result += f" {rounded_str}"
        previous = rounded
    return result


def stringify_path_data(
    path_data: Sequence[PathDataItem], precision: int | None = None, disable_space_after_flags: bool = False
) -> str:
    if not path_data:
        return ""
    if len(path_data) == 1:
        item = path_data[0]
        return item.command + _stringify_args(item.command, item.args, precision, disable_space_after_flags)
    result = ""
    prev = PathDataItem(command=path_data[0].command, args=list(path_data[0].args))
    if path_data[1].command == "L":
        prev.command = "M"
    elif path_data[1].command == "l":
        prev.command = "m"
    last = len(path_data) - 1
    for i in range(1, len(path_data)):
        item = path_data[i]
        command = item.command
        args = item.args
        if (
            (prev.command == command and prev.command not in ("M", "m"))
            or (prev.command == "M" and command == "L")
            or (prev.command == "m" and command == "l")
        ):
            prev.args.extend(args)
            if i == last:
                result += prev.command + _stringify_args(prev.command, prev.args, precision, disable_space_after_flags)
        else:
            result += prev.command + _stringify_args(prev.command, prev.args, precision, disable_space_after_flags)
            if i == last:
                result += command + _stringify_args(command, args, precision, disable_space_after_flags)
            else:
                prev = PathDataItem(command=command, args=list(args))
    return result


def cleanup_out_data(
    data: Sequence[float],
    no_space_after_flags: bool = False,
    leading_zero: bool = True,
    negative_extra_space: bool = True,
    command: str | None = None,
) -> str:
    text = ""
    previous: float | None = None
    for i, item in enumerate(data):
        delimiter = " "
        if i == 0:
            delimiter = ""
        if no_space_after_flags and command in ("A", "a"):
            pos = i % 7
            if pos in (4, 5):
                delimiter = ""
        item_str = remove_leading_zero(item) if leading_zero else js_number_string(item)
        if (
            negative_extra_space
            and delimiter != ""
            and (item < 0 or (item_str[:1] == "." and previous is not None and previous % 1 != 0))
        ):
            delimiter = ""
        previous = item
        text += delimiter + item_str
    return text


def path2js(node: "Element") -> list[PathDataItem]:
    cached = getattr(node, "_path_js", None)
    if cached is not None:
        return cached
    path_data = parse_path_data(node.attributes.get("d", ""))
    if path_data and path_data[0].command == "m":
        path_data[0].command = "M"
    node._path_js = path_data
    return path_data


def js2path(
    node: "Element", data: Sequence[PathDataItem], float_precision: int | None = 3, no_space_after_flags: bool = False
) -> None:
    path_data: list[PathDataItem] = []
    for item in data:
        if path_data and item.command in ("M", "m"):
            last = path_data[-1]
            if last.command in ("M", "m"):
                path_data.pop()
        path_data.append(PathDataItem(command=item.command, args=list(item.args)))
    node._path_js = list(data)
    node.attributes["d"] = stringify_path_data(
        path_data,
        precision=float_precision,
        disable_space_after_flags=no_space_after_flags,
    )


def a2c(
    x1: float,
    y1: float,
    rx: float,
    ry: float,
    angle: float,
    large_arc_flag: float,
    sweep_flag: float,
    x2: float,
    y2: float,
    recursive: list[float] | None = None,
) -> list[float]:
    _120 = math.pi * 120 / 180
    rad = math.pi / 180 * (angle or 0)
    res: list[float] = []
    rotate_x = lambda x, y, r: x * math.cos(r) - y * math.sin(r)
    rotate_y = lambda x, y, r: x * math.sin(r) + y * math.cos(r)
    if not recursive:
        x1, y1 = rotate_x(x1, y1, -rad), rotate_y(x1, y1, -rad)
        x2, y2 = rotate_x(x2, y2, -rad), rotate_y(x2, y2, -rad)
        x = (x1 - x2) / 2
        y = (y1 - y2) / 2
        h = x * x / (rx * rx) + y * y / (ry * ry)
        if h > 1:
            h = math.sqrt(h)
            rx, ry = h * rx, h * ry
        rx2 = rx * rx
        ry2 = ry * ry
        k = (-1 if large_arc_flag == sweep_flag else 1) * math.sqrt(
            abs((rx2 * ry2 - rx2 * y * y - ry2 * x * x) / (rx2 * y * y + ry2 * x * x))
        )
        cx = k * rx * y / ry + (x1 + x2) / 2
        cy = k * -ry * x / rx + (y1 + y2) / 2
        f1 = math.asin(js_to_fixed((y1 - cy) / ry, 9))
        f2 = math.asin(js_to_fixed((y2 - cy) / ry, 9))
        f1 = math.pi - f1 if x1 < cx else f1
        f2 = math.pi - f2 if x2 < cx else f2
        if f1 < 0:
            f1 = math.pi * 2 + f1
        if f2 < 0:
            f2 = math.pi * 2 + f2
        if sweep_flag and f1 > f2:
            f1 = f1 - math.pi * 2
        if not sweep_flag and f2 > f1:
            f2 = f2 - math.pi * 2
    else:
        f1, f2, cx, cy = recursive[0], recursive[1], recursive[2], recursive[3]
    df = f2 - f1
    if abs(df) > _120:
        f2old, x2old, y2old = f2, x2, y2
        f2 = f1 + _120 * (1 if sweep_flag and f2 > f1 else -1)
        x2 = cx + rx * math.cos(f2)
        y2 = cy + ry * math.sin(f2)
        res = a2c(x2, y2, rx, ry, angle, 0, sweep_flag, x2old, y2old, [f2, f2old, cx, cy])
    df = f2 - f1
    c1 = math.cos(f1)
    s1 = math.sin(f1)
    c2 = math.cos(f2)
    s2 = math.sin(f2)
    t = math.tan(df / 4)
    hx = 4 / 3 * rx * t
    hy = 4 / 3 * ry * t
    m = [
        -hx * s1,
        hy * c1,
        x2 + hx * s2 - x1,
        y2 - hy * c2 - y1,
        x2 - x1,
        y2 - y1,
    ]
    if recursive:
        return m + res
    res = m + res
    newres: list[float] = []
    for i in range(len(res)):
        if i % 2:
            newres.append(rotate_y(res[i - 1], res[i], rad))
        else:
            newres.append(rotate_x(res[i], res[i + 1], rad))
    return newres


def _convert_relative_to_absolute(
    data: Sequence[PathDataItem],
) -> list[PathDataItem]:
    new_data: list[PathDataItem] = []
    start = [0.0, 0.0]
    cursor = [0.0, 0.0]
    for item in data:
        command = item.command
        args = list(item.args)
        if command == "m":
            args[0] += cursor[0]
            args[1] += cursor[1]
            command = "M"
        if command == "M":
            cursor[0], cursor[1] = args[0], args[1]
            start[0], start[1] = cursor[0], cursor[1]
        if command == "h":
            args[0] += cursor[0]
            command = "H"
        if command == "H":
            cursor[0] = args[0]
        if command == "v":
            args[0] += cursor[1]
            command = "V"
        if command == "V":
            cursor[1] = args[0]
        if command == "l":
            args[0] += cursor[0]
            args[1] += cursor[1]
            command = "L"
        if command == "L":
            cursor[0], cursor[1] = args[0], args[1]
        if command == "c":
            for k in (0, 2, 4):
                args[k] += cursor[0]
            for k in (1, 3, 5):
                args[k] += cursor[1]
            command = "C"
        if command == "C":
            cursor[0], cursor[1] = args[4], args[5]
        if command == "s":
            args[0] += cursor[0]
            args[1] += cursor[1]
            args[2] += cursor[0]
            args[3] += cursor[1]
            command = "S"
        if command == "S":
            cursor[0], cursor[1] = args[2], args[3]
        if command == "q":
            args[0] += cursor[0]
            args[1] += cursor[1]
            args[2] += cursor[0]
            args[3] += cursor[1]
            command = "Q"
        if command == "Q":
            cursor[0], cursor[1] = args[2], args[3]
        if command == "t":
            args[0] += cursor[0]
            args[1] += cursor[1]
            command = "T"
        if command == "T":
            cursor[0], cursor[1] = args[0], args[1]
        if command == "a":
            args[5] += cursor[0]
            args[6] += cursor[1]
            command = "A"
        if command == "A":
            cursor[0], cursor[1] = args[5], args[6]
        if command in ("z", "Z"):
            cursor[0], cursor[1] = start[0], start[1]
            command = "z"
        new_data.append(PathDataItem(command=command, args=args))
    return new_data


@dataclass
class _SubPathPoints:
    points: list[list[float]]
    min_x: int
    min_y: int
    max_x: int
    max_y: int


@dataclass
class _Points:
    sets: list[_SubPathPoints]
    min_x: float
    min_y: float
    max_x: float
    max_y: float


def _gather_points(absolute_data: Sequence[PathDataItem]) -> _Points:
    sets: list[_SubPathPoints] = []
    global_min_x = global_min_y = math.inf
    global_max_x = global_max_y = -math.inf

    def add_point(sub: _SubPathPoints, point: list[float]) -> None:
        nonlocal global_min_x, global_min_y, global_max_x, global_max_y
        if not sub.points or point[1] > sub.points[sub.max_y][1]:
            sub.max_y = len(sub.points)
            global_max_y = max(point[1], global_max_y) if sets or sub.points else point[1]
        if not sub.points or point[0] > sub.points[sub.max_x][0]:
            sub.max_x = len(sub.points)
            global_max_x = max(point[0], global_max_x) if sets or sub.points else point[0]
        if not sub.points or point[1] < sub.points[sub.min_y][1]:
            sub.min_y = len(sub.points)
            global_min_y = min(point[1], global_min_y) if sets or sub.points else point[1]
        if not sub.points or point[0] < sub.points[sub.min_x][0]:
            sub.min_x = len(sub.points)
            global_min_x = min(point[0], global_min_x) if sets or sub.points else point[0]
        sub.points.append(point)

    prev_ctrl_point: list[float] | None = None
    current: _SubPathPoints | None = None

    for index, item in enumerate(absolute_data):
        if item.command == "M":
            current = _SubPathPoints(points=[], min_x=0, min_y=0, max_x=0, max_y=0)
            sets.append(current)
        if current is None:
            current = _SubPathPoints(points=[], min_x=0, min_y=0, max_x=0, max_y=0)
            sets.append(current)
        prev = absolute_data[index - 1] if index else None
        base_point = current.points[-1] if current.points else None
        data = item.args
        ctrl_point = base_point

        def to_absolute(n: float, i: int, _base: list[float] | None = base_point) -> float:
            return n + (_base[i % 2] if _base is not None else 0)

        command = item.command
        if command == "H" and base_point is not None:
            add_point(current, [data[0], base_point[1]])
        elif command == "V" and base_point is not None:
            add_point(current, [base_point[0], data[0]])
        elif command == "Q":
            add_point(current, data[:2])
            prev_ctrl_point = [data[2] - data[0], data[3] - data[1]]
        elif command == "T":
            if base_point is not None and prev is not None and prev.command in ("Q", "T"):
                ctrl_point = [
                    base_point[0] + (prev_ctrl_point or [0, 0])[0],
                    base_point[1] + (prev_ctrl_point or [0, 0])[1],
                ]
                add_point(current, ctrl_point)
                prev_ctrl_point = [data[0] - ctrl_point[0], data[1] - ctrl_point[1]]
        elif command == "C":
            if base_point is not None:
                add_point(
                    current,
                    [
                        0.5 * (base_point[0] + data[0]),
                        0.5 * (base_point[1] + data[1]),
                    ],
                )
            add_point(current, [0.5 * (data[0] + data[2]), 0.5 * (data[1] + data[3])])
            add_point(current, [0.5 * (data[2] + data[4]), 0.5 * (data[3] + data[5])])
            prev_ctrl_point = [data[4] - data[2], data[5] - data[3]]
        elif command == "S":
            if base_point is not None and prev is not None and prev.command in ("C", "S"):
                add_point(
                    current,
                    [
                        base_point[0] + 0.5 * (prev_ctrl_point or [0, 0])[0],
                        base_point[1] + 0.5 * (prev_ctrl_point or [0, 0])[1],
                    ],
                )
                ctrl_point = [
                    base_point[0] + (prev_ctrl_point or [0, 0])[0],
                    base_point[1] + (prev_ctrl_point or [0, 0])[1],
                ]
            if ctrl_point is not None:
                add_point(
                    current,
                    [
                        0.5 * (ctrl_point[0] + data[0]),
                        0.5 * (ctrl_point[1] + data[1]),
                    ],
                )
            add_point(current, [0.5 * (data[0] + data[2]), 0.5 * (data[1] + data[3])])
            prev_ctrl_point = [data[2] - data[0], data[3] - data[1]]
        elif command == "A" and base_point is not None:
            curves = a2c(base_point[0], base_point[1], data[0], data[1], data[2], data[3], data[4], data[5], data[6])
            while curves:
                chunk = curves[0:6]
                del curves[0:6]
                if len(chunk) < 6:
                    break
                c_data = [to_absolute(v, k) for k, v in enumerate(chunk)]
                add_point(
                    current,
                    [
                        0.5 * (base_point[0] + c_data[0]),
                        0.5 * (base_point[1] + c_data[1]),
                    ],
                )
                add_point(current, [0.5 * (c_data[0] + c_data[2]), 0.5 * (c_data[1] + c_data[3])])
                add_point(current, [0.5 * (c_data[2] + c_data[4]), 0.5 * (c_data[3] + c_data[5])])
                if curves:
                    base_point = c_data[-2:]
                    add_point(current, list(base_point))
        if len(data) >= 2:
            add_point(current, data[-2:])

    if not sets:
        return _Points(sets=[], min_x=math.inf, min_y=math.inf, max_x=-math.inf, max_y=-math.inf)
    return _Points(sets=sets, min_x=global_min_x, min_y=global_min_y, max_x=global_max_x, max_y=global_max_y)


def _cross(o: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _convex_hull(sub: _SubPathPoints) -> _SubPathPoints:
    points = sorted(sub.points, key=lambda p: (p[0], p[1]))
    if len(points) <= 2:
        return _SubPathPoints(points=points, min_x=0, min_y=0, max_x=len(points) - 1, max_y=0)
    lower: list[list[float]] = []
    min_y = 0
    bottom = 0
    for i, point in enumerate(points):
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        if point[1] < points[min_y][1]:
            min_y = i
            bottom = len(lower)
        lower.append(point)
    upper: list[list[float]] = []
    max_y = len(points) - 1
    top = 0
    for i in range(len(points) - 1, -1, -1):
        point = points[i]
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        if point[1] > points[max_y][1]:
            max_y = i
            top = len(upper)
        upper.append(point)
    upper.pop()
    lower.pop()
    hull_list = lower + upper
    return _SubPathPoints(
        points=hull_list,
        min_x=0,
        max_x=len(lower),
        min_y=bottom,
        max_y=(len(lower) + top) % len(hull_list) if hull_list else 0,
    )


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _sub(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [a[0] - b[0], a[1] - b[1]]


def _minus(a: Sequence[float]) -> list[float]:
    return [-a[0], -a[1]]


def _orth(v: Sequence[float], from_point: Sequence[float]) -> list[float]:
    o = [-v[1], v[0]]
    return _minus(o) if _dot(o, _minus(from_point)) < 0 else o


def _support_point(sub: _SubPathPoints, direction: Sequence[float]) -> list[float]:
    if direction[1] >= 0:
        index = sub.max_y if direction[0] < 0 else sub.max_x
    else:
        index = sub.min_x if direction[0] < 0 else sub.min_y
    count = len(sub.points)
    if count == 0:
        return [0.0, 0.0]
    maximum = -math.inf
    while True:
        value = _dot(sub.points[index], direction)
        if value <= maximum:
            break
        maximum = value
        index = (index + 1) % count
    return sub.points[(index or count) - 1]


def _process_simplex(simplex: list[list[float]], direction: list[float]) -> bool:
    if len(simplex) == 2:
        a = simplex[1]
        b = simplex[0]
        ao = _minus(a)
        ab = _sub(b, a)
        if _dot(ao, ab) > 0:
            direction[:] = _orth(ab, a)
        else:
            direction[:] = ao
            simplex.pop(0)
    else:
        a = simplex[2]
        b = simplex[1]
        c = simplex[0]
        ab = _sub(b, a)
        ac = _sub(c, a)
        ao = _minus(a)
        acb = _orth(ab, ac)
        abc = _orth(ac, ab)
        if _dot(acb, ao) > 0:
            if _dot(ab, ao) > 0:
                direction[:] = acb
                simplex.pop(0)
            else:
                direction[:] = ao
                del simplex[0:2]
        elif _dot(abc, ao) > 0:
            if _dot(ac, ao) > 0:
                direction[:] = abc
                del simplex[1:2]
            else:
                direction[:] = ao
                del simplex[0:2]
        else:
            return True
    return False


def paths_intersect(path1: Sequence[PathDataItem], path2: Sequence[PathDataItem]) -> bool:
    points1 = _gather_points(_convert_relative_to_absolute(list(path1)))
    points2 = _gather_points(_convert_relative_to_absolute(list(path2)))
    if not points1.sets or not points2.sets:
        return False

    def separated(set1: _SubPathPoints, set2: _SubPathPoints) -> bool:
        return (
            set1.points[set1.max_x][0] <= set2.points[set2.min_x][0]
            or set2.points[set2.max_x][0] <= set1.points[set1.min_x][0]
            or set1.points[set1.max_y][1] <= set2.points[set2.min_y][1]
            or set2.points[set2.max_y][1] <= set1.points[set1.min_y][1]
        )

    if (
        points1.max_x <= points2.min_x
        or points2.max_x <= points1.min_x
        or points1.max_y <= points2.min_y
        or points2.max_y <= points1.min_y
        or all(separated(set1, set2) for set1 in points1.sets for set2 in points2.sets)
    ):
        return False

    hull_nest1 = [_convex_hull(s) for s in points1.sets]
    hull_nest2 = [_convex_hull(s) for s in points2.sets]

    for hull1 in hull_nest1:
        if len(hull1.points) < 3:
            continue
        for hull2 in hull_nest2:
            if len(hull2.points) < 3:
                continue
            simplex = [_sub(_support_point(hull1, [1, 0]), _support_point(hull2, [-1, 0]))]
            direction = _minus(simplex[0])
            iterations = 10000
            while True:
                iterations -= 1
                if iterations == 0:
                    return True
                simplex.append(_sub(_support_point(hull1, direction), _support_point(hull2, _minus(direction))))
                if _dot(direction, simplex[-1]) <= 0:
                    return False
                if _process_simplex(simplex, direction):
                    return True
    return False


_TRANSFORM_TYPES: frozenset[str] = frozenset({"matrix", "rotate", "scale", "skewX", "skewY", "translate"})

_REG_TRANSFORM_SPLIT = re.compile(r"\s*(matrix|translate|scale|rotate|skewX|skewY)\s*\(\s*(.+?)\s*\)[\s,]*")
_REG_NUMERIC_VALUES = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


@dataclass
class TransformItem:
    name: str
    data: list[float]


def transform2js(transform_string: str) -> list[TransformItem]:
    transforms: list[TransformItem] = []
    current_transform: TransformItem | None = None
    for item in _REG_TRANSFORM_SPLIT.split(transform_string):
        if not item:
            continue
        if item in _TRANSFORM_TYPES:
            current_transform = TransformItem(name=item, data=[])
            transforms.append(current_transform)
        else:
            for match in _REG_NUMERIC_VALUES.finditer(item):
                if current_transform is not None:
                    current_transform.data.append(float(match.group()))
    if current_transform is None or not current_transform.data:
        return []
    return transforms


def _mth_rad(deg: float) -> float:
    return deg * math.pi / 180


def _mth_deg(rad: float) -> float:
    return rad * 180 / math.pi


def _mth_cos(deg: float) -> float:
    return math.cos(_mth_rad(deg))


def _mth_sin(deg: float) -> float:
    return math.sin(_mth_rad(deg))


def _mth_tan(deg: float) -> float:
    return math.tan(_mth_rad(deg))


def _mth_acos(value: float, float_precision: int) -> float:
    return tools_to_fixed(_mth_deg(math.acos(value)), float_precision)


def _mth_asin(value: float, float_precision: int) -> float:
    return tools_to_fixed(_mth_deg(math.asin(value)), float_precision)


def _mth_atan(value: float, float_precision: int) -> float:
    return tools_to_fixed(_mth_deg(math.atan(value)), float_precision)


def _multiply_transform_matrices(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [
        a[0] * b[0] + a[2] * b[1],
        a[1] * b[0] + a[3] * b[1],
        a[0] * b[2] + a[2] * b[3],
        a[1] * b[2] + a[3] * b[3],
        a[0] * b[4] + a[2] * b[5] + a[4],
        a[1] * b[4] + a[3] * b[5] + a[5],
    ]


def transform_to_matrix(transform: TransformItem) -> list[float]:
    if transform.name == "matrix":
        return list(transform.data)
    if transform.name == "translate":
        return [1.0, 0.0, 0.0, 1.0, transform.data[0], transform.data[1] if len(transform.data) > 1 else 0.0]
    if transform.name == "scale":
        sx = transform.data[0]
        sy = transform.data[1] if len(transform.data) > 1 else sx
        return [sx, 0.0, 0.0, sy, 0.0, 0.0]
    if transform.name == "rotate":
        cos = _mth_cos(transform.data[0])
        sin = _mth_sin(transform.data[0])
        cx = transform.data[1] if len(transform.data) > 1 else 0.0
        cy = transform.data[2] if len(transform.data) > 2 else 0.0
        return [cos, sin, -sin, cos, (1 - cos) * cx + sin * cy, (1 - cos) * cy - sin * cx]
    if transform.name == "skewX":
        return [1.0, 0.0, _mth_tan(transform.data[0]), 1.0, 0.0, 0.0]
    if transform.name == "skewY":
        return [1.0, _mth_tan(transform.data[0]), 0.0, 1.0, 0.0, 0.0]
    raise SvgoError(f"Unknown transform {transform.name}")


def transforms_multiply(transforms: Sequence[TransformItem]) -> TransformItem:
    matrix_data = [list(t.data) if t.name == "matrix" else transform_to_matrix(t) for t in transforms]
    data: list[float] = []
    if matrix_data:
        data = list(matrix_data[0])
        for other in matrix_data[1:]:
            data = _multiply_transform_matrices(data, other)
    return TransformItem(name="matrix", data=data)


def transform_arc(cursor: Sequence[float], arc: list[float], transform_matrix: Sequence[float]) -> list[float]:
    x = arc[5] - cursor[0]
    y = arc[6] - cursor[1]
    a = arc[0]
    b = arc[1]
    rot = arc[2] * math.pi / 180
    cos = math.cos(rot)
    sin = math.sin(rot)
    if a > 0 and b > 0:
        h = (x * cos + y * sin) ** 2 / (4 * a * a) + (y * cos - x * sin) ** 2 / (4 * b * b)
        if h > 1:
            h = math.sqrt(h)
            a *= h
            b *= h
    ellipse = [a * cos, a * sin, -b * sin, b * cos, 0.0, 0.0]
    m = _multiply_transform_matrices(list(transform_matrix), ellipse)
    last_col = m[2] * m[2] + m[3] * m[3]
    square_sum = m[0] * m[0] + m[1] * m[1] + last_col
    root = math.hypot(m[0] - m[3], m[1] + m[2]) * math.hypot(m[0] + m[3], m[1] - m[2])
    if not root:
        arc[0] = arc[1] = math.sqrt(square_sum / 2)
        arc[2] = 0.0
    else:
        major_axis_sqr = (square_sum + root) / 2
        minor_axis_sqr = (square_sum - root) / 2
        major = abs(major_axis_sqr - last_col) > 1e-6
        sub = (major_axis_sqr if major else minor_axis_sqr) - last_col
        rows_sum = m[0] * m[2] + m[1] * m[3]
        term1 = m[0] * sub + m[2] * rows_sum
        term2 = m[1] * sub + m[3] * rows_sum
        arc[0] = math.sqrt(major_axis_sqr)
        arc[1] = math.sqrt(minor_axis_sqr)
        arc[2] = (
            (-1 if (term2 < 0 if major else term1 > 0) else 1)
            * math.acos((term1 if major else term2) / math.hypot(term1, term2))
            * 180
            / math.pi
        )
    if (transform_matrix[0] < 0) != (transform_matrix[3] < 0):
        arc[4] = 1 - arc[4]
    return arc


@dataclass
class _TransformParams:
    convert_to_shorts: bool = True
    deg_precision: int | None = None
    float_precision: int = 3
    transform_precision: int = 5
    matrix_to_transform: bool = True
    short_translate: bool = True
    short_scale: bool = True
    short_rotate: bool = True
    remove_useless: bool = True
    collapse_into_one: bool = True
    leading_zero: bool = True
    negative_extra_space: bool = False


def _plain_round(data: Sequence[float]) -> list[float]:
    return [_js_round(v) for v in data]


def _smart_round(precision: int, data: list[float]) -> list[float]:
    tolerance = js_to_fixed(0.1**precision, precision)
    for i in range(len(data) - 1, -1, -1):
        if tools_to_fixed(data[i], precision) != data[i]:
            rounded = js_to_fixed(data[i], precision - 1)
            if js_to_fixed(abs(rounded - data[i]), precision + 1) >= tolerance:
                data[i] = js_to_fixed(data[i], precision)
            else:
                data[i] = rounded
    return data


def _deg_round(data: list[float], params: _TransformParams) -> list[float]:
    if params.deg_precision is not None and params.deg_precision >= 1 and params.float_precision < 20:
        return _smart_round(params.deg_precision, data)
    return _plain_round(data)


def _float_round(data: list[float], params: _TransformParams) -> list[float]:
    if 1 <= params.float_precision < 20:
        return _smart_round(params.float_precision, data)
    return _plain_round(data)


def _transform_round(data: list[float], params: _TransformParams) -> list[float]:
    if params.transform_precision >= 1 and params.float_precision < 20:
        return _smart_round(params.transform_precision, data)
    return _plain_round(data)


def round_transform(transform: TransformItem, params: _TransformParams) -> TransformItem:
    if transform.name == "translate":
        transform.data = _float_round(transform.data, params)
    elif transform.name == "rotate":
        transform.data = _deg_round(transform.data[:1], params) + _float_round(transform.data[1:], params)
    elif transform.name in ("skewX", "skewY"):
        transform.data = _deg_round(transform.data, params)
    elif transform.name == "scale":
        transform.data = _transform_round(transform.data, params)
    elif transform.name == "matrix":
        transform.data = _transform_round(transform.data[:4], params) + _float_round(transform.data[4:], params)
    return transform


def _is_identity_transform(transform: TransformItem) -> bool:
    if transform.name in ("rotate", "skewX", "skewY"):
        return transform.data[0] == 0
    if transform.name == "scale":
        return transform.data[0] == 1 and (len(transform.data) < 2 or transform.data[1] == 1)
    if transform.name == "translate":
        return transform.data[0] == 0 and (len(transform.data) < 2 or transform.data[1] == 0)
    return False


def _create_scale_transform(data: Sequence[float]) -> TransformItem:
    scale_data = data[:1] if (len(data) < 2 or data[0] == data[1]) else data[:2]
    return TransformItem(name="scale", data=list(scale_data))


def _merge_translate_and_rotate(tx: float, ty: float, angle: float) -> TransformItem:
    rotation_angle_rads = _mth_rad(angle)
    d = 1 - math.cos(rotation_angle_rads)
    e = math.sin(rotation_angle_rads)
    cy = (d * ty + e * tx) / (d * d + e * e)
    cx = (tx - e * cy) / d
    return TransformItem(name="rotate", data=[angle, cx, cy])


def _optimize(rounded: list[TransformItem], raw: list[TransformItem]) -> list[TransformItem]:
    optimized: list[TransformItem] = []
    index = 0
    while index < len(rounded):
        transform = rounded[index]
        if _is_identity_transform(transform):
            index += 1
            continue
        data = transform.data
        if transform.name == "rotate":
            if data and data[0] in (180, -180):
                nxt = rounded[index + 1] if index + 1 < len(rounded) else None
                if nxt is not None and nxt.name == "scale":
                    optimized.append(_create_scale_transform([-v for v in nxt.data]))
                    index += 2
                else:
                    optimized.append(TransformItem(name="scale", data=[-1.0]))
                    index += 1
                continue
            count = 3 if (len(data) > 1 and data[1] or len(data) > 2 and data[2]) else 1
            optimized.append(TransformItem(name="rotate", data=data[:count]))
        elif transform.name == "scale":
            optimized.append(_create_scale_transform(data))
        elif transform.name in ("skewX", "skewY"):
            optimized.append(TransformItem(name=transform.name, data=data[:1]))
        elif transform.name == "translate":
            nxt = rounded[index + 1] if index + 1 < len(rounded) else None
            if (
                nxt is not None
                and nxt.name == "rotate"
                and nxt.data[0] not in (180, -180, 0)
                and len(nxt.data) > 2
                and nxt.data[1] == 0
                and nxt.data[2] == 0
            ):
                raw_data = raw[index].data
                raw_next = raw[index + 1].data
                optimized.append(
                    _merge_translate_and_rotate(raw_data[0], raw_data[1] if len(raw_data) > 1 else 0.0, raw_next[0])
                )
                index += 2
                continue
            count = 2 if (len(data) > 1 and data[1]) else 1
            optimized.append(TransformItem(name="translate", data=data[:count]))
        index += 1
    if not optimized:
        return [TransformItem(name="scale", data=[1.0])]
    return optimized


def _decompose_qrab(matrix: TransformItem) -> list[TransformItem] | None:
    data = matrix.data
    a, b, c, d, e, f = data
    delta = a * d - b * c
    if delta == 0:
        return None
    r = math.hypot(a, b)
    if r == 0:
        return None
    decomposition: list[TransformItem] = []
    if e or f:
        decomposition.append(TransformItem(name="translate", data=[e, f]))
    cos_of_rotation_angle = a / r
    if cos_of_rotation_angle != 1:
        rotation_angle_rads = math.acos(cos_of_rotation_angle)
        decomposition.append(
            TransformItem(
                name="rotate",
                data=[_mth_deg(-rotation_angle_rads if b < 0 else rotation_angle_rads), 0.0, 0.0],
            )
        )
    sx = r
    sy = delta / sx
    if sx != 1 or sy != 1:
        decomposition.append(TransformItem(name="scale", data=[sx, sy]))
    ac_plus_bd = a * c + b * d
    if ac_plus_bd:
        decomposition.append(
            TransformItem(
                name="skewX",
                data=[_mth_deg(math.atan(ac_plus_bd / (a * a + b * b)))],
            )
        )
    return decomposition


def _decompose_qrcd(matrix: TransformItem) -> list[TransformItem] | None:
    data = matrix.data
    a, b, c, d, e, f = data
    delta = a * d - b * c
    if delta == 0:
        return None
    s = math.hypot(c, d)
    if s == 0:
        return None
    decomposition: list[TransformItem] = []
    if e or f:
        decomposition.append(TransformItem(name="translate", data=[e, f]))
    rotation_angle_rads = math.pi / 2 - (1 if d < 0 else -1) * math.acos(-c / s)
    decomposition.append(TransformItem(name="rotate", data=[_mth_deg(rotation_angle_rads), 0.0, 0.0]))
    sx = delta / s
    sy = s
    if sx != 1 or sy != 1:
        decomposition.append(TransformItem(name="scale", data=[sx, sy]))
    ac_plus_bd = a * c + b * d
    if ac_plus_bd:
        decomposition.append(
            TransformItem(
                name="skewY",
                data=[_mth_deg(math.atan(ac_plus_bd / (c * c + d * d)))],
            )
        )
    return decomposition


def matrix_to_transform(orig_matrix: TransformItem, params: _TransformParams) -> list[TransformItem]:
    decompositions: list[list[TransformItem]] = []
    qrab = _decompose_qrab(orig_matrix)
    qrcd = _decompose_qrcd(orig_matrix)
    if qrab:
        decompositions.append(qrab)
    if qrcd:
        decompositions.append(qrcd)
    shortest: list[TransformItem] | None = None
    shortest_len = math.inf
    for decomposition in decompositions:
        rounded_transforms = [
            round_transform(TransformItem(name=t.name, data=list(t.data)), params) for t in decomposition
        ]
        optimized = _optimize(rounded_transforms, decomposition)
        length = len(js2transform(optimized, params))
        if length < shortest_len:
            shortest = optimized
            shortest_len = length
    return shortest if shortest is not None else [orig_matrix]


def js2transform(transform_js: Sequence[TransformItem], params: _TransformParams) -> str:
    parts = []
    for transform in transform_js:
        round_transform(transform, params)
        parts.append(
            "%s(%s)"
            % (
                transform.name,
                cleanup_out_data(
                    transform.data,
                    no_space_after_flags=False,
                    leading_zero=params.leading_zero,
                    negative_extra_space=params.negative_extra_space,
                ),
            )
        )
    return "".join(parts)


def apply_matrix_to_path_data(path_data: Sequence[PathDataItem], matrix: Sequence[float]) -> None:
    start = [0.0, 0.0]
    cursor = [0.0, 0.0]

    def transform_absolute_point(x: float, y: float) -> tuple[float, float]:
        return (matrix[0] * x + matrix[2] * y + matrix[4], matrix[1] * x + matrix[3] * y + matrix[5])

    def transform_relative_point(x: float, y: float) -> tuple[float, float]:
        return (matrix[0] * x + matrix[2] * y, matrix[1] * x + matrix[3] * y)

    for path_item in path_data:
        command = path_item.command
        args = path_item.args

        if command == "M":
            cursor[0], cursor[1] = args[0], args[1]
            start[0], start[1] = cursor[0], cursor[1]
            args[0], args[1] = transform_absolute_point(args[0], args[1])
        if command == "m":
            cursor[0] += args[0]
            cursor[1] += args[1]
            start[0], start[1] = cursor[0], cursor[1]
            args[0], args[1] = transform_relative_point(args[0], args[1])
        if command == "H":
            command = "L"
            args[:] = [args[0], cursor[1]]
        if command == "h":
            command = "l"
            args[:] = [args[0], 0.0]
        if command == "V":
            command = "L"
            args[:] = [cursor[0], args[0]]
        if command == "v":
            command = "l"
            args[:] = [0.0, args[0]]
        if command == "L":
            cursor[0], cursor[1] = args[0], args[1]
            args[0], args[1] = transform_absolute_point(args[0], args[1])
        if command == "l":
            cursor[0] += args[0]
            cursor[1] += args[1]
            args[0], args[1] = transform_relative_point(args[0], args[1])
        if command == "C":
            cursor[0], cursor[1] = args[4], args[5]
            args[0], args[1] = transform_absolute_point(args[0], args[1])
            args[2], args[3] = transform_absolute_point(args[2], args[3])
            args[4], args[5] = transform_absolute_point(args[4], args[5])
        if command == "c":
            cursor[0] += args[4]
            cursor[1] += args[5]
            args[0], args[1] = transform_relative_point(args[0], args[1])
            args[2], args[3] = transform_relative_point(args[2], args[3])
            args[4], args[5] = transform_relative_point(args[4], args[5])
        if command == "S":
            cursor[0], cursor[1] = args[2], args[3]
            args[0], args[1] = transform_absolute_point(args[0], args[1])
            args[2], args[3] = transform_absolute_point(args[2], args[3])
        if command == "s":
            cursor[0] += args[2]
            cursor[1] += args[3]
            args[0], args[1] = transform_relative_point(args[0], args[1])
            args[2], args[3] = transform_relative_point(args[2], args[3])
        if command == "Q":
            cursor[0], cursor[1] = args[2], args[3]
            args[0], args[1] = transform_absolute_point(args[0], args[1])
            args[2], args[3] = transform_absolute_point(args[2], args[3])
        if command == "q":
            cursor[0] += args[2]
            cursor[1] += args[3]
            args[0], args[1] = transform_relative_point(args[0], args[1])
            args[2], args[3] = transform_relative_point(args[2], args[3])
        if command == "T":
            cursor[0], cursor[1] = args[0], args[1]
            args[0], args[1] = transform_absolute_point(args[0], args[1])
        if command == "t":
            cursor[0] += args[0]
            cursor[1] += args[1]
            args[0], args[1] = transform_relative_point(args[0], args[1])
        if command == "A":
            transform_arc(cursor, args, list(matrix))
            cursor[0], cursor[1] = args[5], args[6]
            if abs(args[2]) > 80:
                a, rotation = args[0], args[2]
                args[0] = args[1]
                args[1] = a
                args[2] = rotation + (-90 if rotation > 0 else 90)
            args[5], args[6] = transform_absolute_point(args[5], args[6])
        if command == "a":
            transform_arc([0, 0], args, list(matrix))
            cursor[0] += args[5]
            cursor[1] += args[6]
            if abs(args[2]) > 80:
                a, rotation = args[0], args[2]
                args[0] = args[1]
                args[1] = a
                args[2] = rotation + (-90 if rotation > 0 else 90)
            args[5], args[6] = transform_relative_point(args[5], args[6])
        if command in ("z", "Z"):
            cursor[0], cursor[1] = start[0], start[1]

        path_item.command = command
        path_item.args = args


VISIT_SKIP = object()


class Node:
    type: str = "node"


class Document(Node):
    type = "root"

    def __init__(self) -> None:
        self.children: list[Node] = []

    def __repr__(self) -> str:
        return "Document()"


class Element(Node):
    type = "element"

    def __init__(self, name: str, attributes: dict[str, str] | None = None) -> None:
        self.name: str = name
        self.attributes: dict[str, str] = attributes if attributes is not None else {}
        self.children: list[Node] = []

    def __repr__(self) -> str:
        return f"Element({self.name!r}, attrs={list(self.attributes)!r})"


class TextNode(Node):
    type = "text"

    def __init__(self, value: str) -> None:
        self.value: str = value

    def __repr__(self) -> str:
        return f"TextNode({self.value!r})"


class CdataNode(Node):
    type = "cdata"

    def __init__(self, value: str) -> None:
        self.value: str = value


class CommentNode(Node):
    type = "comment"

    def __init__(self, value: str) -> None:
        self.value: str = value


class DoctypeNode(Node):
    type = "doctype"

    def __init__(self, data: str) -> None:
        self.data: dict[str, str] = {"doctype": data}


class InstructionNode(Node):
    type = "instruction"

    def __init__(self, name: str, value: str) -> None:
        self.name: str = name
        self.value: str = value


_REG_ENTITY_REF = re.compile(r"&([A-Za-z_:][\w.:-]*);")


def _expat_error_reason(exc: expat.ExpatError, source: str, line: int, column: int) -> tuple[str, int, int]:
    text = expat.ErrorString(exc.code or 0).lower() if exc.code else ""
    offset = _line_offset(source, line)
    if text == expat.errors.XML_ERROR_UNDEFINED_ENTITY or "entity" in text:
        match = _REG_ENTITY_REF.search(source, offset + column - 1)
        end = match.end() if match else offset + column
        return "Invalid character entity", line, end - offset
    if text == expat.errors.XML_ERROR_TAG_MISMATCH or "mismatched" in text:
        close = source.find(">", offset + column - 1)
        end = close + 1 if close != -1 else column
        return "Unexpected close tag", line, end - offset
    if text == expat.errors.XML_ERROR_UNBOUND_PREFIX or "prefix" in text:
        match = re.compile(r"([A-Za-z_][\w.-]*):").search(source, offset + column - 1)
        prefix = match.group(1) if match else ""
        close = source.find(">", offset + column - 1)
        end = close + 1 if close != -1 else column
        return f'Unbound namespace prefix: "{prefix}"', line, end - offset
    if text == expat.errors.XML_ERROR_SYNTAX and "<" not in source[: max(_line_offset(source, line) + column - 2, 0)]:
        return "Non-whitespace before first tag.", line, column
    if text == expat.errors.XML_ERROR_DUPLICATE_ATTRIBUTE:
        return "Duplicate attribute", line, column
    return text or "Syntax error", line, column


def _line_offset(source: str, line: int) -> int:
    if line <= 1:
        return 0
    offset = 0
    for _ in range(line - 1):
        position = source.find("\n", offset)
        if position == -1:
            break
        offset = position + 1
    return offset


_REG_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*(?:\[[\s\S]*?\])?\s*>")
_REG_XMLDECL = re.compile(r"^\s*<\?xml\s+(.*?)\?>", re.S)


def parse_svg(data: str, from_file: str | None = None) -> Document:
    root = Document()
    stack: list[Document | Element] = [root]
    current = stack[-1]
    in_cdata = False
    in_doctype = False
    text_buffer: list[str] = []

    parser = expat.ParserCreate()
    parser.buffer_text = True
    parser.XmlDeclHandler = lambda *args: None

    def flush_text() -> None:
        nonlocal text_buffer
        if not text_buffer:
            return
        text = "".join(text_buffer)
        text_buffer = []
        parent = stack[-1]
        if isinstance(parent, Element) and parent.name in TEXT_ELEMS:
            if text:
                parent.children.append(TextNode(text))
        else:
            value = text.strip()
            if value:
                parent.children.append(TextNode(value))

    def on_start(name: str, attrs: dict[str, str]) -> None:
        nonlocal current
        flush_text()
        _check_namespace_prefixes(name, attrs)
        element = Element(name, dict(attrs))
        stack[-1].children.append(element)
        stack.append(element)
        current = element
        _namespace_stack.append({key[len("xmlns:") :] for key in attrs if key.startswith("xmlns:")})

    def on_end(name: str) -> None:
        nonlocal current
        flush_text()
        if _namespace_stack:
            _namespace_stack.pop()
        if len(stack) > 1:
            stack.pop()
        current = stack[-1]

    def _check_namespace_prefixes(name: str, attrs: dict[str, str]) -> None:
        in_scope: set[str] = set()
        for frame in _namespace_stack:
            in_scope.update(frame)
        candidates = []
        if ":" in name:
            candidates.append(name.split(":", 1)[0])
        for attr_name in attrs:
            if attr_name.startswith("xmlns:"):
                continue
            if ":" in attr_name:
                candidates.append(attr_name.split(":", 1)[0])
        for prefix in candidates:
            if prefix == "xml":
                continue
            if prefix not in in_scope:
                byte_index = parser.CurrentByteIndex
                close = data.find(">", byte_index)
                end = close if close != -1 else len(data) - 1
                line = data.count("\n", 0, byte_index) + 1
                last_newline = data.rfind("\n", 0, byte_index)
                column = end - last_newline
                raise SvgoParserError(
                    f'Unbound namespace prefix: "{prefix}"',
                    line,
                    column,
                    data,
                    from_file,
                )

    _namespace_stack: list[set[str]] = []

    def on_text(text: str) -> None:
        if in_cdata:
            return
        text_buffer.append(text)

    def on_comment(text: str) -> None:
        flush_text()
        stack[-1].children.append(CommentNode(text.strip()))

    def on_pi(target: str, data: str) -> None:
        flush_text()
        stack[-1].children.append(InstructionNode(target.lower(), data))

    def on_cdata_start() -> None:
        nonlocal in_cdata, _cdata_buffer
        flush_text()
        in_cdata = True
        _cdata_buffer = []

    def on_cdata_end() -> None:
        nonlocal in_cdata
        node = CdataNode("".join(_cdata_buffer))
        stack[-1].children.append(node)
        in_cdata = False

    def on_start_doctype(*args: Any) -> None:
        nonlocal in_doctype
        in_doctype = True

    def on_end_doctype() -> None:
        nonlocal in_doctype
        in_doctype = False

    def on_default(text: str) -> None:
        if in_cdata:
            _cdata_buffer.append(text)
            return
        if in_doctype:
            return
        match = _REG_ENTITY_REF.fullmatch(text.strip())
        if match:
            name = match.group(1)
            decoded = _HTML5_ENTITIES.get(name + ";")
            if decoded is None:
                decoded = _HTML5_ENTITIES.get(name)
            if decoded is not None:
                text_buffer.append(decoded)
                return
            line, column = parser.CurrentLineNumber, parser.CurrentColumnNumber
            reason, line, column = _expat_error_reason(expat.error("undefined entity"), data, line, column + 1)
            raise SvgoParserError(reason, line, column, data, from_file)
        text_buffer.append(text)

    _cdata_buffer: list[str] = []

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    parser.CharacterDataHandler = on_text
    parser.CommentHandler = on_comment
    parser.ProcessingInstructionHandler = on_pi
    parser.StartCdataSectionHandler = on_cdata_start
    parser.EndCdataSectionHandler = on_cdata_end
    parser.DefaultHandler = on_default
    parser.StartDoctypeDeclHandler = on_start_doctype
    parser.EndDoctypeDeclHandler = on_end_doctype
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.ExternalEntityRefHandler = lambda *args: 1

    xml_decl = _REG_XMLDECL.match(data)
    if xml_decl:
        root.children.append(InstructionNode("xml", xml_decl.group(1)))
        doctype_search = data[xml_decl.end() :]
    else:
        doctype_search = data
    doctype = _REG_DOCTYPE.search(doctype_search[:65536] if doctype_search else "")
    if doctype:
        root.children.append(DoctypeNode(doctype.group(0)))

    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        line = parser.CurrentLineNumber or exc.lineno or 1
        column = parser.CurrentColumnNumber or 0
        text = expat.ErrorString(exc.code or 0)
        if text in (expat.errors.XML_ERROR_NO_ELEMENTS, expat.errors.XML_ERROR_UNCLOSED_TOKEN):
            if len(stack) > 1:
                if text == expat.errors.XML_ERROR_UNCLOSED_TOKEN:
                    line = data.count("\n") + 1
                    column = len(data) - (data.rfind("\n") + 1)
                raise SvgoParserError("Unclosed root tag", line, column, data, from_file) from None
            flush_text()
            return root
        reason, line, column = _expat_error_reason(exc, data, line, column + 1)
        raise SvgoParserError(reason, line, column, data, from_file) from None
    flush_text()
    return root


_TEXT_ENTITIES = {"&": "&amp;", "'": "&apos;", '"': "&quot;", ">": "&gt;", "<": "&lt;"}
_VALUE_ENTITIES = {"&": "&amp;", '"': "&quot;", ">": "&gt;", "<": "&lt;"}
_REG_TEXT_ENTITIES = re.compile("[&'\"<>]")
_REG_VALUE_ENTITIES = re.compile('[&"<>]')


@dataclass
class StringifyOptions:
    pretty: bool = False
    indent: int | str = 4
    eol: str = "lf"
    final_newline: bool = False
    use_short_tags: bool = True


def _encode_text_entities(value: str) -> str:
    return _REG_TEXT_ENTITIES.sub(lambda m: _TEXT_ENTITIES[m.group()], value)


def _encode_value_entities(value: str) -> str:
    return _REG_VALUE_ENTITIES.sub(lambda m: _VALUE_ENTITIES[m.group()], value)


def stringify_svg(root: Document, user_options: Mapping[str, Any] | None = None) -> str:
    options = StringifyOptions()
    if user_options:
        key_map = {"finalNewline": "final_newline", "useShortTags": "use_short_tags"}
        for key, value in user_options.items():
            attribute = key_map.get(key, key)
            if hasattr(options, attribute) and value is not None:
                setattr(options, attribute, value)
    indent = options.indent
    if isinstance(indent, int) and not math.isnan(indent):
        new_indent = "\t" if indent < 0 else " " * indent
    elif isinstance(indent, str):
        new_indent = indent
    else:
        new_indent = "    "
    eol = "\r\n" if options.eol == "crlf" else "\n"
    state: dict[str, Any] = {
        "indent": new_indent,
        "textContext": None,
        "indentLevel": 0,
        "pretty": options.pretty,
        "useShortTags": options.use_short_tags,
    }
    suffix = eol if options.pretty else ""
    suffixes = {
        "doctype_end": ">" + suffix,
        "proc_inst_end": "?>" + suffix,
        "comment_end": "-->" + suffix,
        "cdata_end": "]]>" + suffix,
        "tag_short_end": "/>" + suffix,
        "tag_open_end": ">" + suffix,
        "tag_close_end": ">" + suffix,
        "text_end": suffix,
    }
    svg = _stringify_node(root, state, suffixes)
    if options.final_newline and svg and not svg.endswith("\n"):
        svg += eol
    return svg


def _create_indent(state: dict[str, Any]) -> str:
    if state["pretty"] and state["textContext"] is None:
        return state["indent"] * (state["indentLevel"] - 1)
    return ""


def _stringify_node(data: Document | Element, state: dict[str, Any], suffixes: dict[str, str]) -> str:
    svg = ""
    state["indentLevel"] += 1
    for item in data.children:
        if item.type == "element":
            svg += _stringify_element(item, state, suffixes)
        elif item.type == "text":
            svg += _stringify_text(item, state, suffixes)
        elif item.type == "doctype":
            svg += "<!DOCTYPE" + item.data["doctype"] + suffixes["doctype_end"]
        elif item.type == "instruction":
            svg += "<?" + item.name + " " + item.value + suffixes["proc_inst_end"]
        elif item.type == "comment":
            svg += "<!--" + item.value + suffixes["comment_end"] + ""
        elif item.type == "cdata":
            svg += _create_indent(state) + "<![CDATA[" + item.value + suffixes["cdata_end"] + ""
    state["indentLevel"] -= 1
    return svg


def _stringify_element(node: Element, state: dict[str, Any], suffixes: dict[str, str]) -> str:
    if not node.children:
        if state["useShortTags"]:
            return _create_indent(state) + "<" + node.name + _stringify_attributes(node) + suffixes["tag_short_end"]
        return (
            _create_indent(state)
            + "<"
            + node.name
            + _stringify_attributes(node)
            + suffixes["tag_open_end"]
            + "</"
            + node.name
            + suffixes["tag_close_end"]
        )
    tag_open_end = suffixes["tag_open_end"]
    tag_close_start = "</"
    open_indent = _create_indent(state)
    close_indent = _create_indent(state)
    if state["textContext"]:
        tag_open_end = ">"
        open_indent = ""
    elif node.name in TEXT_ELEMS:
        tag_open_end = ">"
        close_indent = ""
        state["textContext"] = node
    children = _stringify_node(node, state, suffixes)
    if state["textContext"] is node:
        state["textContext"] = None
    return (
        open_indent
        + "<"
        + node.name
        + _stringify_attributes(node)
        + tag_open_end
        + children
        + close_indent
        + tag_close_start
        + node.name
        + suffixes["tag_close_end"]
    )


def _stringify_attributes(node: Element) -> str:
    attrs = ""
    for name, value in node.attributes.items():
        attrs += " " + name
        if value is not None:
            attrs += '="' + _encode_value_entities(str(value)) + '"'
    return attrs


def _stringify_text(node: TextNode, state: dict[str, Any], suffixes: dict[str, str]) -> str:
    return (
        _create_indent(state)
        + _encode_text_entities(node.value)
        + ("" if state["textContext"] else suffixes["text_end"])
    )


EnterCallback = Callable[[Node, Node], Any]
ExitCallback = Callable[[Node, Node], Any]
Visitor = Mapping[str, Any]


def visit(node: Node, visitor: Visitor, parent_node: Node | None = None) -> None:
    callbacks = visitor.get(node.type)
    enter = callbacks.get("enter") if isinstance(callbacks, Mapping) else None
    exit_ = callbacks.get("exit") if isinstance(callbacks, Mapping) else None
    if enter is not None:
        symbol = enter(node, parent_node)
        if symbol is VISIT_SKIP:
            return
    if node.type == "root" and isinstance(node, Document):
        for child in list(node.children):
            visit(child, visitor, node)
    elif node.type == "element":
        parent_children = parent_node.children if parent_node is not None else []
        if node in parent_children:
            for child in list(node.children):
                visit(child, visitor, node)
    if exit_ is not None:
        exit_(node, parent_node)


def detach_node_from_parent(node: Node, parent_node: Node) -> None:
    parent_node.children = [c for c in parent_node.children if c is not node]


def map_nodes_to_parents(root: Document) -> dict[Node, Document | Element]:
    parents: dict[Node, Document | Element] = {}
    visit(root, {"element": {"enter": lambda node, parent: parents.__setitem__(node, parent)}})
    return parents


@dataclass
class CssDeclaration:
    name: str
    value: str
    important: bool = False


@dataclass
class CssRule:
    selector: str
    specificity: tuple[int, int, int, int]
    dynamic: bool
    declarations: list[CssDeclaration]
    media: str = ""


def _strip_css_comments(css: str) -> str:
    result: list[str] = []
    in_string = ""
    i = 0
    while i < len(css):
        c = css[i]
        if in_string:
            result.append(c)
            if c == "\\" and i + 1 < len(css):
                result.append(css[i + 1])
                i += 2
                continue
            if c == in_string:
                in_string = ""
            i += 1
            continue
        if c in ("'", '"'):
            in_string = c
            result.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < len(css) and css[i + 1] == "*":
            end = css.find("*/", i + 2)
            i = len(css) if end == -1 else end + 2
            continue
        result.append(c)
        i += 1
    return "".join(result)


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    parts: list[str] = []
    depth = 0
    in_string = ""
    current: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if in_string:
            current.append(c)
            if c == "\\" and i + 1 < len(text):
                current.append(text[i + 1])
                i += 2
                continue
            if c == in_string:
                in_string = ""
            i += 1
            continue
        if c in ("'", '"'):
            in_string = c
            current.append(c)
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if depth == 0 and c == separator:
            parts.append("".join(current))
            current = []
            i += 1
            continue
        current.append(c)
        i += 1
    parts.append("".join(current))
    return parts


def parse_declarations(css: str) -> list[CssDeclaration]:
    declarations: list[CssDeclaration] = []
    css = _strip_css_comments(css)
    for chunk in _split_top_level(css, ";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        name, _, value = chunk.partition(":")
        value = value.strip()
        important = False
        if value.lower().endswith("!important"):
            important = True
            value = value[: -len("!important")].strip()
        if value.lower().endswith("! important"):
            important = True
            value = value[: -len("! important")].strip()
        declarations.append(CssDeclaration(name.strip().lower(), value, important))
    return declarations


_COMBINATOR_CHARS = ">+~"


@dataclass
class SelectorPart:
    kind: str
    value: str
    operator: str = ""
    attr_value: str = ""


@dataclass
class CompoundSelector:
    combinator: str
    parts: list[SelectorPart]

    @property
    def unmatchable(self) -> bool:
        return any(p.kind == "pseudo" for p in self.parts)


def _parse_compound(text: str) -> CompoundSelector:
    parts: list[SelectorPart] = []
    combinator = ""
    stripped = text.strip()
    if stripped.startswith(">") or stripped.startswith("+") or stripped.startswith("~"):
        combinator = stripped[0]
        stripped = stripped[1:].strip()
    elif stripped == ">" or stripped == "+" or stripped == "~":
        combinator = stripped
    i = 0
    while i < len(stripped):
        c = stripped[i]
        if c == "*":
            parts.append(SelectorPart(kind="universal", value="*"))
            i += 1
        elif c == "#":
            j = i + 1
            while j < len(stripped) and stripped[j] not in ".#[\\[: >+~":
                j += 1
            parts.append(SelectorPart(kind="id", value=stripped[i + 1 : j]))
            i = j
        elif c == ".":
            j = i + 1
            while j < len(stripped) and stripped[j] not in ".#[\\[: >+~":
                j += 1
            parts.append(SelectorPart(kind="class", value=stripped[i + 1 : j]))
            i = j
        elif c == "[":
            j = stripped.find("]", i + 1)
            if j == -1:
                j = len(stripped)
            inner = stripped[i + 1 : j].strip()
            operator = ""
            attr_name = inner
            attr_value = ""
            for op in ("~=", "^=", "$=", "*=", "|=", "="):
                position = inner.find(op)
                if position != -1:
                    attr_name = inner[:position].strip()
                    operator = op
                    attr_value = inner[position + len(op) :].strip().strip("'\"")
                    break
            parts.append(SelectorPart(kind="attr", value=attr_name, operator=operator, attr_value=attr_value))
            i = j + 1
        elif c == ":":
            is_element = stripped.startswith("::", i)
            j = i + (2 if is_element else 1)
            depth = 0
            while j < len(stripped):
                if stripped[j] == "(":
                    depth += 1
                elif stripped[j] == ")":
                    depth -= 1
                elif depth == 0 and stripped[j] in ".#[\\[: >+~":
                    break
                j += 1
            parts.append(SelectorPart(kind="pseudo", value=stripped[i:j]))
            i = j
        else:
            j = i
            while j < len(stripped) and stripped[j] not in ".#[\\[: >+~":
                j += 1
            parts.append(SelectorPart(kind="type", value=stripped[i:j]))
            i = j
    return CompoundSelector(combinator=combinator, parts=parts)


def parse_selector(selector: str) -> list[CompoundSelector] | None:
    compounds: list[CompoundSelector] = []
    remainder = selector.strip()
    if not remainder:
        return None
    tokens: list[str] = []
    buffer: list[str] = []
    depth = 0
    in_string = ""
    i = 0
    while i < len(remainder):
        c = remainder[i]
        if in_string:
            buffer.append(c)
            if c == in_string:
                in_string = ""
            i += 1
            continue
        if c in ("'", '"'):
            in_string = c
            buffer.append(c)
            i += 1
            continue
        if c == "[" or c == "(":
            depth += 1
            buffer.append(c)
            i += 1
            continue
        if c == "]" or c == ")":
            depth -= 1
            buffer.append(c)
            i += 1
            continue
        if depth == 0 and c in _COMBINATOR_CHARS:
            if buffer:
                tokens.append("".join(buffer).strip())
                buffer = []
            tokens.append(c)
            i += 1
            continue
        if depth == 0 and c.isspace():
            if buffer:
                tokens.append("".join(buffer).strip())
                buffer = []
            i += 1
            continue
        buffer.append(c)
        i += 1
    if buffer:
        tokens.append("".join(buffer).strip())
    sequence: list[CompoundSelector] = []
    pending_combinator = ""
    expect_combinator = False
    for token in tokens:
        if token in (">", "+", "~"):
            if not sequence:
                return None
            pending_combinator = token
            expect_combinator = False
            continue
        if not token:
            continue
        if expect_combinator and not pending_combinator:
            pending_combinator = " "
        compound = _parse_compound((pending_combinator + " " + token) if pending_combinator else token)
        compound.combinator = pending_combinator or (" " if sequence else "")
        sequence.append(compound)
        pending_combinator = ""
        expect_combinator = True
    if not sequence:
        return None
    return sequence


def _selector_specificity(selector: str) -> tuple[int, int, int, int]:
    ids = classes = types = 0
    compounds = parse_selector(selector) or []
    for compound in compounds:
        for part in compound.parts:
            if part.kind == "id":
                ids += 1
            elif part.kind in ("class", "attr", "pseudo"):
                classes += 1
            elif part.kind == "type":
                types += 1
    return (0, ids, classes, types)


def compare_specificity(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    for x, y in zip(a, b):
        if x < y:
            return -1
        if x > y:
            return 1
    return 0


def _part_matches(part: SelectorPart, node: Element) -> bool:
    if part.kind == "universal":
        return True
    if part.kind == "type":
        return node.name == part.value.lower()
    if part.kind == "id":
        return node.attributes.get("id") == part.value
    if part.kind == "class":
        classes = (node.attributes.get("class") or "").split()
        return part.value in classes
    if part.kind == "attr":
        value = node.attributes.get(part.value)
        if value is None:
            return False
        if not part.operator:
            return True
        if part.operator == "=":
            return value == part.attr_value
        if part.operator == "~=":
            return part.attr_value in value.split()
        if part.operator == "^=":
            return bool(part.attr_value) and value.startswith(part.attr_value)
        if part.operator == "$=":
            return bool(part.attr_value) and value.endswith(part.attr_value)
        if part.operator == "*=":
            return bool(part.attr_value) and part.attr_value in value
        if part.operator == "|=":
            return value == part.attr_value or value.startswith(part.attr_value + "-")
    return False


def _compound_matches(compound: CompoundSelector, node: Element) -> bool:
    if compound.unmatchable:
        return False
    return all(_part_matches(part, node) for part in compound.parts)


def _matches_sequence(
    sequence: list[CompoundSelector], node: Element, parents: Mapping[Node, Document | Element]
) -> bool:
    if not _compound_matches(sequence[-1], node):
        return False
    if len(sequence) == 1:
        return True
    return _match_rest(sequence[:-1], node, parents)


def _match_rest(sequence: list[CompoundSelector], node: Node, parents: Mapping[Node, Document | Element]) -> bool:
    compound = sequence[-1]
    combinator = compound.combinator
    if combinator == ">":
        parent = parents.get(node)
        if not isinstance(parent, Element):
            return False
        if _compound_matches(compound, parent):
            if len(sequence) == 1:
                return True
            return _match_rest(sequence[:-1], parent, parents)
        return False
    if combinator in ("+", "~"):
        if not isinstance(node, Element):
            return False
        parent = parents.get(node)
        if not isinstance(parent, (Element, Document)):
            return False
        siblings = parent.children
        try:
            index = siblings.index(node)
        except ValueError:
            return False
        start = index - 1 if combinator == "+" else 0
        for sibling in siblings[start:index]:
            if isinstance(sibling, Element) and _compound_matches(compound, sibling):
                if len(sequence) == 1:
                    return True
                return _match_rest(sequence[:-1], sibling, parents)
        return False
    ancestor = parents.get(node)
    while isinstance(ancestor, Element):
        if _compound_matches(compound, ancestor):
            if len(sequence) == 1:
                return True
            if _match_rest(sequence[:-1], ancestor, parents):
                return True
        ancestor = parents.get(ancestor)
    return False


def matches(node: Element, selector: str, parents: Mapping[Node, Document | Element]) -> bool:
    if not selector:
        return False
    sequence = parse_selector(selector)
    if sequence is None:
        return False
    return _matches_sequence(sequence, node, parents)


def query_selector_all(node: Document | Element, selector: str) -> list[Element]:
    results: list[Element] = []
    if not selector:
        return results
    for subselector in _split_top_level(selector):
        subselector = subselector.strip()
        if not subselector:
            continue
        sequence = parse_selector(subselector)
        if sequence is None:
            continue
        parents = map_nodes_to_parents(node if isinstance(node, Document) else _as_document(node))
        _collect_matches(node, sequence, parents, results)
    return results


def _as_document(element: Element) -> Document:
    document = Document()
    document.children.append(element)
    return document


def _collect_matches(
    node: Document | Element,
    sequence: list[CompoundSelector],
    parents: Mapping[Node, Document | Element],
    results: list[Element],
) -> None:
    for child in list(node.children):
        if isinstance(child, Element):
            if _matches_sequence(sequence, child, parents):
                results.append(child)
            _collect_matches(child, sequence, parents, results)


def query_selector(node: Document | Element, selector: str) -> Element | None:
    results = query_selector_all(node, selector)
    return results[0] if results else None


def includes_attr_selector(selector: str, name: str, value: str | None = None, traversed: bool = False) -> bool:
    for subselector in _split_top_level(selector):
        compounds = parse_selector(subselector.strip()) or []
        flat = [p for compound in compounds for p in compound.parts]
        for index, part in enumerate(flat):
            if traversed and index == len(flat) - 1:
                continue
            if part.kind == "class" and name == "class":
                if value is None or part.value == value:
                    return True
            elif part.kind == "id" and name == "id":
                if value is None or part.value == value:
                    return True
            elif part.kind == "attr":
                if part.value != name:
                    continue
                if value is None or part.attr_value == value:
                    return True
                if part.operator == "=" and part.attr_value.strip("'\"") == value:
                    return True
    return False


def parse_stylesheet(css: str, dynamic: bool = False) -> list[CssRule]:
    rules: list[CssRule] = []
    css = _strip_css_comments(css)
    i = 0
    length = len(css)
    while i < length:
        brace = css.find("{", i)
        if brace == -1:
            break
        prelude = css[i:brace].strip()
        depth = 1
        j = brace + 1
        while j < length and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[brace + 1 : j - 1] if depth == 0 else css[brace + 1 : j]
        i = j
        if not prelude:
            continue
        if prelude.startswith("@"):
            at_name = prelude[1:].split(None, 1)[0] if len(prelude) > 1 else ""
            if at_name in ("keyframes", "-webkit-keyframes", "-o-keyframes", "-moz-keyframes"):
                continue
            if at_name == "media":
                media_prelude = prelude[len("@media") :].strip()
                inner_rules = parse_stylesheet(body, dynamic=True)
                for rule in inner_rules:
                    rule.media = media_prelude
                rules.extend(inner_rules)
            else:
                rules.extend(parse_stylesheet(body, dynamic=True))
            continue
        declarations = parse_declarations(body)
        for selector in _split_top_level(prelude):
            selector = selector.strip()
            if not selector:
                continue
            cleaned, has_pseudo = _strip_pseudo_classes(selector)
            specificity = _selector_specificity(selector)
            rules.append(
                CssRule(
                    selector=cleaned,
                    specificity=specificity,
                    dynamic=dynamic or has_pseudo,
                    declarations=declarations,
                )
            )
    return rules


def _strip_pseudo_classes(selector: str) -> tuple[str, bool]:
    result: list[str] = []
    has_pseudo = False
    i = 0
    length = len(selector)
    while i < length:
        c = selector[i]
        if c == ":":
            if i + 1 < length and selector[i + 1] == ":":
                j = i + 2
                while j < length and selector[j] not in ".#[\\[: >+~":
                    j += 1
                result.append(selector[i:j])
                i = j
                continue
            has_pseudo = True
            j = i + 1
            depth = 0
            while j < length:
                if selector[j] == "(":
                    depth += 1
                elif selector[j] == ")":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                elif depth == 0 and (selector[j].isspace() or selector[j] in ">+~,"):
                    break
                j += 1
            i = j
            continue
        result.append(c)
        i += 1
    return "".join(result).strip(), has_pseudo


_REG_REFERENCES_URL = re.compile(r"\burl\((['\"]?)#(.+?)\1\)")
_REG_REFERENCES_HREF = re.compile(r"^#(.+?)$")
_REG_REFERENCES_BEGIN = re.compile(r"(\w+)\.[a-zA-Z]")


def _decode_uri(value: str) -> str:
    import urllib.parse

    try:
        return urllib.parse.unquote(
            value,
            errors="surrogateescape",
            safe="#$&+,;:=?@/'!*()-~",
        )
    except Exception:
        return value


def encode_svg_datauri(data: str, kind: str | None) -> str:
    prefix = "data:image/svg+xml"
    if not kind or kind == "base64":
        return prefix + ";base64," + base64.b64encode(data.encode("utf-8")).decode("ascii")
    if kind == "enc":
        import urllib.parse

        return prefix + "," + urllib.parse.quote(data, safe="~()*!.'")
    return prefix + "," + data


def decode_svg_datauri(data: str) -> str:
    match = re.match(r"data:image/svg\+xml(;charset=[^;,]*)?(;base64)?,(.*)", data, re.S)
    if match is None:
        return data
    payload = match.group(3)
    if match.group(2):
        try:
            return base64.b64decode(payload).decode("utf-8")
        except Exception:
            return data
    if payload[:1] == "%":
        import urllib.parse

        try:
            return urllib.parse.unquote(payload)
        except Exception:
            return data
    if payload[:1] == "<":
        return payload
    return data


def find_references(attribute: str, value: str) -> list[str]:
    results: list[str] = []
    if attribute in REFERENCES_PROPS:
        for match in _REG_REFERENCES_URL.finditer(value):
            results.append(match.group(2))
    if attribute == "href" or attribute.endswith(":href"):
        match = _REG_REFERENCES_HREF.match(value)
        if match is not None:
            results.append(match.group(1))
    if attribute == "begin":
        match = _REG_REFERENCES_BEGIN.search(value)
        if match is not None:
            results.append(match.group(1))
    return [_decode_uri(body) for body in results]


def includes_url_reference(body: str) -> bool:
    return _REG_REFERENCES_URL.search(body) is not None


def includes_css_var_reference(body: str) -> bool:
    return re.search(r"var\s*\(\s*--", body) is not None


def _script_event_attrs() -> list[str]:
    groups = ("animationEvent", "documentEvent", "documentElementEvent", "globalEvent", "graphicalEvent")
    attrs: list[str] = []
    for group in groups:
        attrs.extend(sorted(ATTRS_GROUPS.get(group, ())))
    return attrs


_HAS_SCRIPTS_EVENT_ATTRS: tuple[str, ...] = tuple(_script_event_attrs())
_EXECUTABLE_DATA_MEDIA_TYPES: frozenset[str] = frozenset({"application/xhtml+xml", "image/svg+xml", "text/html"})


def is_executable_url(value: str) -> bool:
    import urllib.parse

    normalized = re.sub(r"[\t\n\r]", "", value).lstrip().lower()
    if normalized.startswith("javascript:") or normalized.startswith("vbscript:"):
        return True
    if not normalized.startswith("data:"):
        return False
    media_type_end = re.search(r"[;,]", normalized[5:])
    if media_type_end is None:
        return False
    media_type = normalized[5 : 5 + media_type_end.start()].strip()
    return media_type in _EXECUTABLE_DATA_MEDIA_TYPES


def has_scripts(node: Element) -> bool:
    if node.name == "script" and node.children:
        return True
    if node.name == "a":
        for key, value in node.attributes.items():
            if (key == "href" or key.endswith(":href")) and value is not None:
                if is_executable_url(value):
                    return True
    return any(attr in node.attributes for attr in _HAS_SCRIPTS_EVENT_ATTRS)


@dataclass
class Stylesheet:
    rules: list[CssRule]
    parents: dict[Node, Document | Element]


def collect_stylesheet(root: Document) -> Stylesheet:
    rules: list[CssRule] = []
    parents: dict[Node, Document | Element] = {}
    visit(root, {"element": {"enter": lambda node, parent: _collect_style_element(node, parent, rules, parents)}})
    rules.sort(key=lambda rule: rule.specificity)
    return Stylesheet(rules=rules, parents=parents)


def _collect_style_element(
    node: Element, parent: Node, rules: list[CssRule], parents: dict[Node, Document | Element]
) -> None:
    parents[node] = parent
    if node.name != "style":
        return
    style_type = node.attributes.get("type")
    if style_type not in (None, "", "text/css"):
        return
    dynamic = node.attributes.get("media") not in (None, "all")
    for child in node.children:
        if child.type in ("text", "cdata"):
            rules.extend(parse_stylesheet(child.value, dynamic))


def compute_own_style(
    stylesheet: Stylesheet, node: Element, parents: Mapping[Node, Document | Element]
) -> dict[str, dict[str, Any]]:
    computed: dict[str, dict[str, Any]] = {}
    important_styles: dict[str, bool] = {}
    for name, value in node.attributes.items():
        if name in ATTRS_GROUPS["presentation"]:
            computed[name] = {"type": "static", "inherited": False, "value": value}
            important_styles[name] = False
    for rule in stylesheet.rules:
        if matches(node, rule.selector, parents):
            for declaration in rule.declarations:
                existing = computed.get(declaration.name)
                if existing is not None and existing["type"] == "dynamic":
                    continue
                if rule.dynamic:
                    computed[declaration.name] = {"type": "dynamic", "inherited": False}
                    continue
                if existing is None or declaration.important or important_styles.get(declaration.name) is False:
                    computed[declaration.name] = {
                        "type": "static",
                        "inherited": False,
                        "value": declaration.value,
                    }
                    important_styles[declaration.name] = declaration.important
    inline_style = node.attributes.get("style")
    if inline_style is not None:
        for declaration in parse_declarations(inline_style):
            existing = computed.get(declaration.name)
            if existing is not None and existing["type"] == "dynamic":
                continue
            if existing is None or declaration.important or important_styles.get(declaration.name) is False:
                computed[declaration.name] = {
                    "type": "static",
                    "inherited": False,
                    "value": declaration.value,
                }
                important_styles[declaration.name] = declaration.important
    return computed


def compute_style(stylesheet: Stylesheet, node: Element) -> dict[str, dict[str, Any]]:
    parents = stylesheet.parents
    computed = compute_own_style(stylesheet, node, parents)
    parent = parents.get(node)
    while parent is not None and parent.type != "root":
        inherited_styles = compute_own_style(stylesheet, parent, parents)
        for name, computed_style in inherited_styles.items():
            if (
                name not in computed
                and name in INHERITABLE_ATTRS
                and name not in PRESENTATION_NON_INHERITABLE_GROUP_ATTRS
            ):
                computed[name] = {**computed_style, "inherited": True}
        parent = parents.get(parent)
    return computed


PluginFn = Callable[[Document, dict[str, Any], dict[str, Any]], Any]


@dataclass
class PluginConfig:
    name: str
    params: dict[str, Any]
    fn: PluginFn


@dataclass
class BuiltinPlugin:
    name: str
    description: str
    fn: PluginFn
    is_preset: bool = False
    plugins: tuple["BuiltinPlugin", ...] = ()


def _remove_doctype(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type == "doctype":
            detach_node_from_parent(node, parent)

    return {"doctype": {"enter": enter}}


def _remove_xml_proc_inst(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type == "instruction" and node.name == "xml":
            detach_node_from_parent(node, parent)

    return {"instruction": {"enter": enter}}


def _remove_comments(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    preserve_patterns: Any = params.get("preservePatterns", [re.compile(r"^!")])

    def enter(node: Node, parent: Node) -> None:
        if node.type != "comment":
            return
        if preserve_patterns:
            if not isinstance(preserve_patterns, (list, tuple)):
                raise SvgoError(
                    f"Expected array in removeComments preservePatterns parameter but received {preserve_patterns}"
                )
            for pattern in preserve_patterns:
                if isinstance(pattern, str):
                    if re.search(pattern, node.value):
                        return
                elif re.search(pattern, node.value):
                    return
        detach_node_from_parent(node, parent)

    return {"comment": {"enter": enter}}


def _extract_attributes_in_stylesheet(stylesheet: Stylesheet) -> set[str]:
    attributes: set[str] = set()
    for rule in stylesheet.rules:
        for subselector in _split_top_level(rule.selector):
            for compound in parse_selector(subselector.strip()) or []:
                for part in compound.parts:
                    if part.kind == "attr":
                        attributes.add(part.value)
                    elif part.kind == "class":
                        attributes.add("class")
                    elif part.kind == "id":
                        attributes.add("id")
    return attributes


def _remove_deprecated_attrs(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    stylesheet = collect_stylesheet(root)
    attributes_in_stylesheet = _extract_attributes_in_stylesheet(stylesheet)
    remove_unsafe = bool(params.get("removeUnsafe", False))

    def process_attributes(node: Element, deprecated: dict[str, frozenset[str]] | None) -> None:
        if not deprecated:
            return
        safe = deprecated.get("safe")
        if safe:
            for name in safe:
                if name in attributes_in_stylesheet:
                    continue
                node.attributes.pop(name, None)
        if remove_unsafe:
            unsafe = deprecated.get("unsafe")
            if unsafe:
                for name in unsafe:
                    if name in attributes_in_stylesheet:
                        continue
                    node.attributes.pop(name, None)

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        elem_config = ELEMS.get(element.name)
        if not elem_config:
            return
        attrs_groups = elem_config["attrsGroups"]
        if (
            "core" in attrs_groups
            and element.attributes.get("xml:lang")
            and "xml:lang" not in attributes_in_stylesheet
            and element.attributes.get("lang")
        ):
            del element.attributes["xml:lang"]
        for group in attrs_groups:
            process_attributes(element, ATTRS_GROUPS_DEPRECATED.get(group))
        process_attributes(element, elem_config.get("deprecated"))

    return {"element": {"enter": enter}}


def _remove_metadata(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type == "element" and node.name == "metadata":
            detach_node_from_parent(node, parent)

    return {"element": {"enter": enter}}


def _remove_editors_ns_data(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    namespaces = list(EDITOR_NAMESPACES)
    additional = params.get("additionalNamespaces")
    if isinstance(additional, (list, tuple)):
        namespaces.extend(additional)
    prefixes: list[str] = []

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        if element.name == "svg":
            for name, value in list(element.attributes.items()):
                if name.startswith("xmlns:") and value in namespaces:
                    prefixes.append(name[len("xmlns:") :])
                    del element.attributes[name]
        for name in list(element.attributes):
            if ":" in name:
                prefix = name.split(":", 1)[0]
                if prefix in prefixes:
                    del element.attributes[name]
        if ":" in element.name:
            prefix = element.name.split(":", 1)[0]
            if prefix in prefixes:
                detach_node_from_parent(element, parent)

    return {"element": {"enter": enter}}


_REG_NEWLINES_NEED_SPACE = re.compile(r"(\S)\r?\n(\S)")
_REG_NEWLINES = re.compile(r"\r?\n")
_REG_SPACES = re.compile(r"\s{2,}")


def _cleanup_attrs(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    newlines = params.get("newlines", True)
    trim = params.get("trim", True)
    spaces = params.get("spaces", True)

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        for name in element.attributes:
            value = element.attributes[name]
            if newlines:
                value = _REG_NEWLINES_NEED_SPACE.sub(lambda m: m.group(1) + " " + m.group(2), value)
                value = _REG_NEWLINES.sub("", value)
            if trim:
                value = value.strip()
            if spaces:
                value = _REG_SPACES.sub(" ", value)
            element.attributes[name] = value

    return {"element": {"enter": enter}}


def _merge_styles(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    state = {"first": None, "collected": "", "content_type": "text"}

    def enter(node: Node, parent: Node) -> Any:
        if node.type != "element":
            return None
        if node.name == "foreignObject":
            return VISIT_SKIP
        if node.name != "style":
            return None
        style_type = node.attributes.get("type")
        if style_type not in (None, "", "text/css"):
            return None
        css = ""
        for child in node.children:
            if child.type == "text":
                css += child.value
            elif child.type == "cdata":
                state["content_type"] = "cdata"
                css += child.value
        if not css.strip():
            detach_node_from_parent(node, parent)
            return None
        if node.attributes.get("media") is None:
            state["collected"] += css
        else:
            state["collected"] += f"@media {node.attributes['media']}{{{css}}}"
            del node.attributes["media"]
        if state["first"] is None:
            state["first"] = node
        else:
            detach_node_from_parent(node, parent)
            if state["content_type"] == "cdata":
                state["first"].children = [CdataNode(state["collected"])]
            else:
                state["first"].children = [TextNode(state["collected"])]
        return None

    return {"element": {"enter": enter}}


def _inline_styles(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    only_matched_once = params.get("onlyMatchedOnce", True)
    remove_matched_selectors = params.get("removeMatchedSelectors", True)
    use_mqs = list(params.get("useMqs", ["", "screen"]))
    use_pseudos = list(params.get("usePseudos", [""]))

    preserved_pseudos: set[str] = set()
    for group in ("functional", "treeStructural"):
        preserved_pseudos.update(PSEUDO_CLASSES.get(group, ()))

    styles: list[tuple[Element, Node, list[CssRule]]] = []
    selectors: list[dict[str, Any]] = []

    def enter(node: Node, parent: Node) -> Any:
        if node.type != "element":
            return None
        if node.name == "foreignObject":
            return VISIT_SKIP
        if node.name != "style" or not node.children:
            return None
        style_type = node.attributes.get("type")
        if style_type not in (None, "", "text/css"):
            return None
        css_text = "".join(child.value for child in node.children if child.type in ("text", "cdata"))
        rules = parse_stylesheet(css_text)
        styles.append((node, parent, rules))
        return None

    def exit_root() -> None:
        if not styles:
            return
        for _, _, rules in styles:
            for rule in rules:
                if rule.media not in use_mqs:
                    continue
                cleaned, has_pseudo = _strip_pseudo_classes(rule.selector)
                if has_pseudo:
                    if "" not in use_pseudos:
                        continue
                    pseudo_part = _extract_pseudo_string(rule.selector)
                    if pseudo_part not in use_pseudos:
                        continue
                selectors.append({
                    "node": cleaned,
                    "rule": rule,
                    "matched": None,
                    "removed": False,
                })
        ordered = sorted(
            selectors,
            key=lambda item: _selector_specificity(item["node"]),
            reverse=True,
        )
        for selector in ordered:
            matched_elements = query_selector_all(root, selector["node"])
            if not matched_elements:
                continue
            if only_matched_once and len(matched_elements) > 1:
                continue
            selector["matched"] = matched_elements
            for selected in matched_elements:
                inline = parse_declarations(selected.attributes.get("style", ""))
                style_items: dict[str, CssDeclaration] = dict((d.name, d) for d in inline)
                for rule_declaration in selector["rule"].declarations:
                    property_name = rule_declaration.name
                    if property_name in ATTRS_GROUPS["presentation"] and not any(
                        includes_attr_selector(other["node"], property_name) for other in selectors
                    ):
                        selected.attributes.pop(property_name, None)
                    existing = style_items.get(property_name)
                    if existing is None:
                        style_items[property_name] = rule_declaration
                    elif not existing.important and rule_declaration.important:
                        style_items[property_name] = rule_declaration
                new_styles = ";".join(
                    f"{d.name}:{d.value}{' !important' if d.important else ''}" for d in style_items.values()
                )
                if new_styles:
                    selected.attributes["style"] = new_styles
            if remove_matched_selectors and matched_elements:
                selector["removed"] = True
        if remove_matched_selectors:
            for selector in ordered:
                if not selector["removed"] or not selector["matched"]:
                    continue
                for selected in selector["matched"]:
                    class_list = [c for c in (selected.attributes.get("class") or "").split(" ") if c]
                    for class_name in re.findall(r"\.([\w-]+)", selector["node"]):
                        if class_name in class_list and not any(
                            includes_attr_selector(other["node"], "class", class_name, True) for other in selectors
                        ):
                            class_list = [c for c in class_list if c != class_name]
                    if not class_list:
                        selected.attributes.pop("class", None)
                    else:
                        selected.attributes["class"] = " ".join(class_list)
                    id_match = re.match(r"^#([\w-]+)", selector["node"])
                    if (
                        id_match
                        and selected.attributes.get("id") == id_match.group(1)
                        and not any(
                            includes_attr_selector(other["node"], "id", id_match.group(1), True) for other in selectors
                        )
                    ):
                        selected.attributes.pop("id", None)
        for style_node, style_parent, rules in styles:
            kept = [rule for rule in rules if not _is_rule_consumed(rule, selectors)]
            if not kept:
                detach_node_from_parent(style_node, style_parent)
            else:
                css = _render_rules(kept)
                if style_node.children and style_node.children[0].type in ("text", "cdata"):
                    style_node.children[0].value = css

    return {
        "element": {"enter": enter},
        "root": {"exit": lambda node, parent: exit_root()},
    }


def _extract_pseudo_string(selector: str) -> str:
    parts: list[str] = []
    preserved = set()
    for group in ("functional", "treeStructural"):
        preserved.update(PSEUDO_CLASSES.get(group, ()))
    for match in re.finditer(r"(:[\w-]+(?:\([^)]*\))?)", selector):
        name = match.group(1).split("(", 1)[0][1:]
        if name not in preserved and not match.group(1).startswith("::"):
            parts.append(match.group(1))
    return "".join(parts)


def _is_rule_consumed(rule: CssRule, selectors: list[dict[str, Any]]) -> bool:
    related = [s for s in selectors if s["rule"] is rule]
    return bool(related) and all(s.get("removed") for s in related)


def _render_rules(rules: list[CssRule]) -> str:
    parts: list[str] = []
    for rule in rules:
        declarations = ";".join(f"{d.name}:{d.value}{' !important' if d.important else ''}" for d in rule.declarations)
        if rule.media:
            parts.append(f"@media {rule.media}{{{rule.selector}{{{declarations}}}}}")
        else:
            parts.append(f"{rule.selector}{{{declarations}}}")
    return "".join(parts)


_COLOR_VALUE_RE = re.compile(r"^([A-Za-z]+|#[0-9a-fA-F]{3,8}|rgb\([^)]*\))$")
_REG_HEX_SHORTEN = re.compile(r"^#(([a-fA-F0-9])\2){3}$")


def _minify_color_value(value: str) -> str:
    if not _COLOR_VALUE_RE.match(value):
        return value
    if includes_url_reference(value) or includes_css_var_reference(value):
        return value
    lowered = value.lower()
    if lowered in COLORS_NAMES:
        candidate = COLORS_NAMES[lowered]
        if len(candidate) < len(value):
            value = candidate
    match = re.match(r"^rgb\(\s*([^)]*)\)$", value, re.I)
    if match is not None:
        numbers: list[int] = []
        parts = re.split(r"\s*,\s*|\s+", match.group(1).strip())
        if len(parts) == 3:
            for part in parts:
                if part.endswith("%"):
                    parsed = js_parse_float(part)
                    if parsed is None:
                        break
                    numbers.append(max(0, min(_js_round(parsed * 2.55), 255)))
                else:
                    parsed = js_parse_float(part)
                    if parsed is None:
                        break
                    numbers.append(max(0, min(int(parsed), 255)))
            if len(numbers) == 3:
                value = "#%02x%02x%02x" % tuple(numbers)
    hex_match = _REG_HEX_SHORTEN.match(value)
    if hex_match is not None:
        value = "#" + value[1] + value[3] + value[5]
    lowered = value.lower()
    if lowered in COLORS_SHORT_NAMES:
        candidate = COLORS_SHORT_NAMES[lowered]
        if len(candidate) < len(value):
            value = candidate
    return value.lower() if re.match(r"^#[0-9a-fA-F]{3,8}$", value) else value


def _minify_number_token(token: str) -> str:
    parsed = js_parse_float(token)
    if parsed is None:
        return token
    if parsed == 0:
        return "0"
    return remove_leading_zero(parsed)


def _minify_declaration_value(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    if _COLOR_VALUE_RE.match(value):
        return _minify_color_value(value)
    if re.fullmatch(r"[-+0-9.eE]+(px|pt|pc|mm|cm|in|ft|em|ex|%)?", value):
        parsed = js_parse_float(value)
        if parsed is not None:
            if parsed == 0:
                return "0"
            return remove_leading_zero(parsed) + re.sub(r"[-+0-9.eE]+", "", value)
    return re.sub(r"\s+", " ", value)


def _minify_declarations(declarations: list[CssDeclaration]) -> list[CssDeclaration]:
    result: list[CssDeclaration] = []
    for declaration in declarations:
        value = _minify_declaration_value(declaration.value)
        if value == "":
            continue
        declaration.value = value
        result.append(declaration)
    return result


def minify_css(css: str, usage: dict[str, Any] | None = None) -> str:
    css = _strip_css_comments(css)
    rules = parse_stylesheet(css)
    usage = usage or {}
    tags = usage.get("tags")
    ids = usage.get("ids")
    classes = usage.get("classes")
    rendered: list[str] = []
    for rule in rules:
        selector = rule.selector
        if tags is not None or ids is not None or classes is not None:
            if _selector_cannot_match(selector, tags, ids, classes):
                continue
        declarations = _minify_declarations(rule.declarations)
        if not declarations:
            continue
        body = ";".join(f"{d.name}:{d.value}{'!important' if d.important else ''}" for d in declarations)
        rendered.append(f"{rule.selector}{{{body}}}")
    return "".join(rendered)


def _selector_cannot_match(
    selector: str, tags: set[str] | None, ids: set[str] | None, classes: set[str] | None
) -> bool:
    for compound in parse_selector(selector) or []:
        for part in compound.parts:
            if part.kind == "type" and tags is not None:
                if part.value not in tags and part.value != "*":
                    return True
            if part.kind == "id" and ids is not None:
                if part.value not in ids:
                    return True
            if part.kind == "class" and classes is not None:
                if part.value not in classes:
                    return True
    return False


def minify_css_block(css: str) -> str:
    declarations = _minify_declarations(parse_declarations(css))
    return ";".join(f"{d.name}:{d.value}{'!important' if d.important else ''}" for d in declarations)


def _minify_styles(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    usage = params.get("usage")
    minify_params = {k: v for k, v in params.items() if k != "usage"}

    style_elements: list[tuple[Element, Node]] = []
    elements_with_style: list[Element] = []
    tags_usage: set[str] = set()
    ids_usage: set[str] = set()
    classes_usage: set[str] = set()
    enable_tags = enable_ids = enable_classes = True
    force_usage = False
    if isinstance(usage, bool):
        enable_tags = enable_ids = enable_classes = usage
    elif isinstance(usage, Mapping):
        enable_tags = usage.get("tags", True)
        enable_ids = usage.get("ids", True)
        enable_classes = usage.get("classes", True)
        force_usage = usage.get("force", False)
    deoptimized = False

    def enter(node: Node, parent: Node) -> None:
        nonlocal deoptimized
        if node.type != "element":
            return
        element = node
        if has_scripts(element):
            deoptimized = True
        tags_usage.add(element.name)
        if element.attributes.get("id") is not None:
            ids_usage.add(element.attributes["id"])
        class_value = element.attributes.get("class")
        if class_value:
            classes_usage.update(class_value.split())
        if element.name == "style" and element.children:
            style_elements.append((element, parent))
        elif element.attributes.get("style") is not None:
            elements_with_style.append(element)

    def exit_root() -> None:
        csso_usage: dict[str, Any] = {}
        if not deoptimized or force_usage:
            if enable_tags:
                csso_usage["tags"] = tags_usage
            if enable_ids:
                csso_usage["ids"] = ids_usage
            if enable_classes:
                csso_usage["classes"] = classes_usage
        for style_node, style_parent in style_elements:
            if not style_node.children:
                continue
            first = style_node.children[0]
            if first.type not in ("text", "cdata"):
                continue
            minified = minify_css(first.value, csso_usage)
            if not minified:
                detach_node_from_parent(style_node, style_parent)
                continue
            if ">" in minified or "<" in minified:
                replacement: Node = CdataNode(minified)
            else:
                replacement = TextNode(minified)
            style_node.children = [replacement]
        for node in elements_with_style:
            node.attributes["style"] = minify_css_block(node.attributes["style"])

    return {
        "element": {"enter": enter},
        "root": {"exit": lambda node, parent: exit_root()},
    }


_GENERATE_ID_CHARS: tuple[str, ...] = tuple("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_MAX_ID_INDEX = len(_GENERATE_ID_CHARS) - 1


def _has_string_prefix(value: str, prefixes: Sequence[str]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def _generate_id(current: list[int] | None) -> list[int]:
    if current is None:
        return [0]
    current[-1] += 1
    for i in range(len(current) - 1, 0, -1):
        if current[i] > _MAX_ID_INDEX:
            current[i] = 0
            if i - 1 >= 0:
                current[i - 1] += 1
    if current[0] > _MAX_ID_INDEX:
        current[0] = 0
        current.insert(0, 0)
    return current


def _get_id_string(arr: Sequence[int]) -> str:
    return "".join(_GENERATE_ID_CHARS[i] for i in arr)


def _cleanup_ids(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    remove = params.get("remove", True)
    minify = params.get("minify", True)
    preserve = params.get("preserve", [])
    preserve_prefixes = params.get("preservePrefixes", [])
    force = params.get("force", False)

    if isinstance(preserve, str):
        preserve_ids: set[str] = {preserve}
    else:
        preserve_ids = set(preserve or [])
    if isinstance(preserve_prefixes, str):
        prefixes: list[str] = [preserve_prefixes]
    else:
        prefixes = list(preserve_prefixes or [])

    node_by_id: dict[str, Element] = {}
    references_by_id: dict[str, list[tuple[Element, str]]] = {}
    deoptimized = False

    def is_id_preserved(id_value: str) -> bool:
        return id_value in preserve_ids or _has_string_prefix(id_value, prefixes)

    def enter(node: Node, parent: Node) -> Any:
        nonlocal deoptimized
        if node.type != "element":
            return None
        element = node
        if not force:
            if (element.name == "style" and element.children) or has_scripts(element):
                deoptimized = True
                return None
            if element.name == "svg":
                has_defs_only = all(
                    child.type == "element" and child.name == "defs" for child in element.children
                ) and bool(element.children)
                if has_defs_only:
                    return VISIT_SKIP
        for name, value in list(element.attributes.items()):
            if name == "id":
                if value in node_by_id:
                    del element.attributes["id"]
                else:
                    node_by_id[value] = element
            else:
                for id_value in find_references(name, value):
                    references_by_id.setdefault(id_value, []).append((element, name))
        return None

    def exit_root() -> None:
        if deoptimized:
            return
        current_id: list[int] | None = None
        for id_value, refs in list(references_by_id.items()):
            node = node_by_id.get(id_value)
            if node is not None:
                if minify and not is_id_preserved(id_value):
                    while True:
                        current_id = _generate_id(current_id)
                        current_id_string = _get_id_string(current_id)
                        if not (
                            is_id_preserved(current_id_string)
                            or (current_id_string in references_by_id and current_id_string not in node_by_id)
                        ):
                            break
                    node.attributes["id"] = current_id_string
                    for ref_element, name in refs:
                        value = ref_element.attributes.get(name, "")
                        if "#" in value:
                            ref_element.attributes[name] = value.replace(f"#{id_value}", f"#{current_id_string}")
                        else:
                            ref_element.attributes[name] = value.replace(f"{id_value}.", f"{current_id_string}.")
                node_by_id.pop(id_value, None)
        if remove:
            for id_value, node in list(node_by_id.items()):
                if not is_id_preserved(id_value):
                    del node.attributes["id"]

    return {
        "element": {"enter": enter},
        "root": {"exit": lambda node, parent: exit_root()},
    }


def _collect_useful_nodes(node: Element, useful: list[Element]) -> None:
    for child in node.children:
        if child.type == "element":
            if child.attributes.get("id") is not None or child.name == "style":
                useful.append(child)
            else:
                _collect_useful_nodes(child, useful)


def _remove_useless_defs(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        if element.name == "defs" or (
            element.name in ELEMS_GROUPS["nonRendering"] and element.attributes.get("id") is None
        ):
            useful: list[Element] = []
            _collect_useful_nodes(element, useful)
            if not useful:
                detach_node_from_parent(element, parent)
            element.children = useful

    return {"element": {"enter": enter}}


_REG_NUMERIC_VALUE = re.compile(r"^([-+]?\d*\.?\d+([eE][-+]?\d+)?)(px|pt|pc|mm|cm|m|in|ft|em|ex|%)?$")
_ABSOLUTE_LENGTHS: dict[str, float] = {
    "cm": 96 / 2.54,
    "mm": 96 / 25.4,
    "in": 96.0,
    "pt": 4 / 3,
    "pc": 16.0,
    "px": 1.0,
}


def _round_value(
    match: re.Match, float_precision: int, leading_zero: bool, default_px: bool, convert_to_px: bool
) -> str:
    number_text = match.group(1)
    unit = match.group(3) or ""
    num = js_to_fixed(float(number_text), float_precision)
    units: str = unit
    if convert_to_px and units and units in _ABSOLUTE_LENGTHS:
        px_num = js_to_fixed(_ABSOLUTE_LENGTHS[units] * float(number_text), float_precision)
        if len(js_number_string(px_num)) < len(match.group(0)):
            num = px_num
            units = "px"
    if leading_zero:
        text = remove_leading_zero(num)
    else:
        text = js_number_string(num)
    if default_px and units == "px":
        units = ""
    return text + units


def _cleanup_numeric_values(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    float_precision = params.get("floatPrecision", 3)
    leading_zero = params.get("leadingZero", True)
    default_px = params.get("defaultPx", True)
    convert_to_px = params.get("convertToPx", True)

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        view_box = element.attributes.get("viewBox")
        if view_box is not None:
            numbers = re.split(r"(?:\s,?|,)\s*", view_box.strip())
            element.attributes["viewBox"] = " ".join(
                _round_view_box_number(value, float_precision) for value in numbers
            )
        for name, value in list(element.attributes.items()):
            if name == "version":
                continue
            match = _REG_NUMERIC_VALUE.match(value)
            if match is not None:
                element.attributes[name] = _round_value(match, float_precision, leading_zero, default_px, convert_to_px)

    return {"element": {"enter": enter}}


def _round_view_box_number(value: str, precision: int) -> str:
    parsed = js_parse_float(value)
    if parsed is None or parsed != parsed:
        return value
    return js_number_string(js_to_fixed(parsed, precision))


_R_NUMBER_COLOR = r"([+-]?(?:\d*\.?\d+|\d+\.?)%?)"
_R_COMMA_COLOR = r"(?:\s*,\s*|\s+)"
_REG_RGB_COLOR = re.compile(
    r"^rgb\(\s*" + _R_NUMBER_COLOR + _R_COMMA_COLOR + _R_NUMBER_COLOR + _R_COMMA_COLOR + _R_NUMBER_COLOR + r"\s*\)$"
)
_REG_HEX_COLOR = re.compile(r"^#(([a-fA-F0-9])\2){3}$")


def _convert_rgb_to_hex(numbers: Sequence[int]) -> str:
    hex_number = (((256 + numbers[0]) << 8 | numbers[1]) << 8) | numbers[2]
    return "#" + format(hex_number, "x")[1:].upper()


def _convert_colors(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    current_color: Any = params.get("currentColor", False)
    names2hex = params.get("names2hex", True)
    rgb2hex = params.get("rgb2hex", True)
    convert_case: Any = params.get("convertCase", "lower")
    shorthex = params.get("shorthex", True)
    shortname = params.get("shortname", True)
    mask_counter = 0

    def enter(node: Node, parent: Node) -> None:
        nonlocal mask_counter
        if node.type != "element":
            return
        element = node
        if element.name == "mask":
            mask_counter += 1
        for name, value in list(element.attributes.items()):
            if name not in COLORS_PROPS:
                continue
            val = value
            if current_color and mask_counter == 0:
                if isinstance(current_color, str):
                    matched = val == current_color
                elif current_color is True:
                    matched = val != "none"
                else:
                    matched = False
                if matched:
                    val = "currentColor"
            if names2hex:
                color_name = val.lower()
                if color_name in COLORS_NAMES:
                    val = COLORS_NAMES[color_name]
            if rgb2hex:
                match = _REG_RGB_COLOR.match(val)
                if match is not None:
                    numbers: list[int] = []
                    for group in match.groups():
                        if group is None:
                            continue
                        if group.endswith("%"):
                            parsed = js_parse_float(group)
                            if parsed is None:
                                numbers = []
                                break
                            numbers.append(max(0, min(_js_round(parsed * 2.55), 255)))
                        else:
                            parsed = js_parse_float(group)
                            if parsed is None:
                                numbers = []
                                break
                            numbers.append(max(0, min(int(parsed), 255)))
                    if len(numbers) == 3:
                        val = _convert_rgb_to_hex(numbers)
            if (
                convert_case
                and not includes_url_reference(val)
                and not includes_css_var_reference(val)
                and val != "currentColor"
            ):
                if convert_case == "lower":
                    val = val.lower()
                elif convert_case == "upper":
                    val = val.upper()
            if shorthex:
                match = _REG_HEX_COLOR.match(val)
                if match is not None:
                    val = "#" + val[1] + val[3] + val[5]
            if shortname:
                color_name = val.lower()
                if color_name in COLORS_SHORT_NAMES:
                    val = COLORS_SHORT_NAMES[color_name]
            element.attributes[name] = val

    def exit(node: Node, parent: Node) -> None:
        nonlocal mask_counter
        if node.type == "element" and node.name == "mask":
            mask_counter -= 1

    return {"element": {"enter": enter, "exit": exit}}


_REG_NUMERIC_IN_ATTR = re.compile(r"[-+]?(\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _apply_transforms_plugin(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    transform_precision = params.get("transformPrecision", 5)
    apply_transforms_stroked = params.get("applyTransformsStroked", True)
    stylesheet = collect_stylesheet(root)

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        if element.attributes.get("d") is None:
            return
        if element.attributes.get("id") is not None:
            return
        transform = element.attributes.get("transform")
        if transform is None or transform == "":
            return
        if element.attributes.get("style") is not None:
            return
        for name, value in element.attributes.items():
            if name in REFERENCES_PROPS and includes_url_reference(value):
                return
        computed_style = compute_style(stylesheet, element)
        transform_style = computed_style.get("transform")
        if (
            transform_style is not None
            and transform_style["type"] == "static"
            and transform_style.get("value") != transform
        ):
            return
        transforms = transform2js(transform)
        if not transforms:
            return
        matrix = transforms_multiply(transforms)
        stroke_style = computed_style.get("stroke")
        stroke_width_style = computed_style.get("stroke-width")
        if (stroke_style is not None and stroke_style["type"] == "dynamic") or (
            stroke_width_style is not None and stroke_width_style["type"] == "dynamic"
        ):
            return
        stroke = stroke_style.get("value") if stroke_style is not None and stroke_style["type"] == "static" else None
        stroke_width = (
            stroke_width_style.get("value")
            if stroke_width_style is not None and stroke_width_style["type"] == "static"
            else None
        )
        scale = js_to_fixed(math.hypot(matrix.data[0], matrix.data[1]), transform_precision)
        if stroke is not None and stroke != "none":
            if not apply_transforms_stroked:
                return
            data = matrix.data
            if (data[0] != data[3] or data[1] != -data[2]) and (data[0] != -data[3] or data[1] != data[2]):
                return
            if scale != 1:
                if element.attributes.get("vector-effect") != "non-scaling-stroke":
                    base_stroke_width = stroke_width or ATTRS_GROUPS_DEFAULTS["presentation"]["stroke-width"]
                    element.attributes["stroke-width"] = _scale_numeric_attr(base_stroke_width, scale)
                    if element.attributes.get("stroke-dashoffset") is not None:
                        element.attributes["stroke-dashoffset"] = _scale_numeric_attr(
                            element.attributes["stroke-dashoffset"], scale
                        )
                    if element.attributes.get("stroke-dasharray") is not None:
                        element.attributes["stroke-dasharray"] = _scale_numeric_attr(
                            element.attributes["stroke-dasharray"], scale
                        )
        path_data = path2js(element)
        apply_matrix_to_path_data(path_data, matrix.data)
        del element.attributes["transform"]

    return {"element": {"enter": enter}}


def _scale_numeric_attr(value: str, scale: float) -> str:
    return _REG_NUMERIC_IN_ATTR.sub(
        lambda m: remove_leading_zero(
            js_parse_float(m.group()) * scale if js_parse_float(m.group()) is not None else js_parse_float(m.group())
        ),
        value.strip(),
    )


def _get_intersection(coords: Sequence[float]) -> list[float] | None:
    a1 = coords[1] - coords[3]
    b1 = coords[2] - coords[0]
    c1 = coords[0] * coords[3] - coords[2] * coords[1]
    a2 = coords[5] - coords[7]
    b2 = coords[6] - coords[4]
    c2 = coords[4] * coords[7] - coords[5] * coords[6]
    denom = a1 * b2 - a2 * b1
    if not denom:
        return None
    cross = [(b1 * c2 - b2 * c1) / denom, (a1 * c2 - a2 * c1) / -denom]
    if cross[0] == cross[0] and cross[1] == cross[1] and math.isfinite(cross[0]) and math.isfinite(cross[1]):
        return cross
    return None


def _is_convex(data: Sequence[float]) -> bool:
    center = _get_intersection([
        0,
        0,
        data[2],
        data[3],
        data[0],
        data[1],
        data[4],
        data[5],
    ])
    if center is None:
        return False
    return (
        data[2] < center[0] == center[0] < 0
        and data[3] < center[1] == center[1] < 0
        and data[4] < center[0] == center[0] < data[0]
        and data[5] < center[1] == center[1] < data[1]
    )


def _get_cubic_bezier_point(curve: Sequence[float], t: float) -> list[float]:
    sqr_t = t * t
    cub_t = sqr_t * t
    mt = 1 - t
    sqr_mt = mt * mt
    return [
        3 * sqr_mt * t * curve[0] + 3 * mt * sqr_t * curve[2] + cub_t * curve[4],
        3 * sqr_mt * t * curve[1] + 3 * mt * sqr_t * curve[3] + cub_t * curve[5],
    ]


def _get_distance(point1: Sequence[float], point2: Sequence[float]) -> float:
    return math.hypot(point1[0] - point2[0], point1[1] - point2[1])


def _find_circle(
    curve: Sequence[float], arc_threshold: float, arc_tolerance: float, error: float
) -> tuple[list[float], float] | None:
    mid_point = _get_cubic_bezier_point(curve, 1 / 2)
    m1 = [mid_point[0] / 2, mid_point[1] / 2]
    m2 = [(mid_point[0] + curve[4]) / 2, (mid_point[1] + curve[5]) / 2]
    center = _get_intersection([
        m1[0],
        m1[1],
        m1[0] + m1[1],
        m1[1] - m1[0],
        m2[0],
        m2[1],
        m2[0] + (m2[1] - mid_point[1]),
        m2[1] - (m2[0] - mid_point[0]),
    ])
    radius = _get_distance([0, 0], center) if center is not None else None
    if center is None or radius is None:
        return None
    tolerance = min(arc_threshold * error, (arc_tolerance * radius) / 100)
    if radius < 1e15 and all(
        abs(_get_distance(_get_cubic_bezier_point(curve, point), center) - radius) <= tolerance
        for point in (1 / 4, 3 / 4)
    ):
        return center, radius
    return None


def _is_arc(
    curve: Sequence[float],
    center: Sequence[float],
    radius: float,
    arc_threshold: float,
    arc_tolerance: float,
    error: float,
) -> bool:
    tolerance = min(arc_threshold * error, (arc_tolerance * radius) / 100)
    return all(
        abs(_get_distance(_get_cubic_bezier_point(curve, point), center) - radius) <= tolerance
        for point in (0, 1 / 4, 1 / 2, 3 / 4, 1)
    )


def _find_arc_angle(curve: Sequence[float], rel_center: Sequence[float]) -> float:
    x1 = -rel_center[0]
    y1 = -rel_center[1]
    x2 = curve[4] - rel_center[0]
    y2 = curve[5] - rel_center[1]
    return math.acos((x1 * x2 + y1 * y2) / math.sqrt((x1 * x1 + y1 * y1) * (x2 * x2 + y2 * y2)))


def _is_curve_straight_line(data: Sequence[float], error: float) -> bool:
    i = len(data) - 2
    a = -data[i + 1]
    b = data[i]
    d = 1 / (a * a + b * b)
    if i <= 1 or not math.isfinite(d):
        return False
    i -= 2
    while i >= 0:
        if math.sqrt((a * data[i] + b * data[i + 1]) ** 2 * d) > error:
            return False
        i -= 2
    return True


def _calculate_sagitta(data: Sequence[float], error: float) -> float | None:
    if data[3] == 1:
        return None
    rx, ry = data[0], data[1]
    if abs(rx - ry) > error:
        return None
    chord = math.hypot(data[5], data[6])
    if chord > rx * 2:
        return None
    return rx - math.sqrt(rx**2 - 0.25 * chord**2)


def _make_longhand(item: PathDataItem, data: Sequence[float]) -> PathDataItem:
    if item.command == "s":
        item.command = "c"
    elif item.command == "t":
        item.command = "q"
    item.args.insert(0, data[len(data) - 2] - data[len(data) - 4])
    item.args.insert(1, data[len(data) - 1] - data[len(data) - 3])
    return item


def _reflect_point(control_point: Sequence[float], base: Sequence[float]) -> list[float]:
    return [2 * base[0] - control_point[0], 2 * base[1] - control_point[1]]


@dataclass
class _ConvertPathState:
    precision: int | float | None
    error: float
    round_data: Callable[[list[float]], list[float]]


def _strong_round(state: _ConvertPathState) -> Callable[[list[float]], list[float]]:
    def round_fn(data: list[float]) -> list[float]:
        precision = state.precision if isinstance(state.precision, int) else 0
        for i in range(len(data) - 1, -1, -1):
            fixed = tools_to_fixed(data[i], precision)
            if fixed != data[i]:
                rounded = tools_to_fixed(data[i], precision - 1)
                if js_to_fixed(abs(rounded - data[i]), precision + 1) >= state.error:
                    data[i] = fixed
                else:
                    data[i] = rounded
        return data

    return round_fn


def _plain_round_fn(data: list[float]) -> list[float]:
    for i in range(len(data) - 1, -1, -1):
        data[i] = _js_round(data[i])
    return data


def _data2path(state: _ConvertPathState, params: dict[str, Any]) -> Callable[[Sequence[PathDataItem]], str]:
    def stringify(path_data: Sequence[PathDataItem]) -> str:
        parts: list[str] = []
        for item in path_data:
            if item.args:
                parts.append(
                    item.command
                    + cleanup_out_data(
                        state.round_data(list(item.args)),
                        no_space_after_flags=params.get("noSpaceAfterFlags", False),
                        leading_zero=params.get("leadingZero", True),
                        negative_extra_space=params.get("negativeExtraSpace", True),
                    )
                )
            else:
                parts.append(item.command)
        return "".join(parts)

    return stringify


def _convert_to_relative(path_data: list[PathDataItem]) -> list[PathDataItem]:
    start = [0.0, 0.0]
    cursor = [0.0, 0.0]
    prev_coords = [0.0, 0.0]
    for i, path_item in enumerate(path_data):
        command = path_item.command
        args = path_item.args
        if command == "m":
            cursor[0] += args[0]
            cursor[1] += args[1]
            start[0], start[1] = cursor[0], cursor[1]
        elif command == "M":
            if i != 0:
                command = "m"
            args[0] -= cursor[0]
            args[1] -= cursor[1]
            cursor[0] += args[0]
            cursor[1] += args[1]
            start[0], start[1] = cursor[0], cursor[1]
        elif command == "l":
            cursor[0] += args[0]
            cursor[1] += args[1]
        elif command == "L":
            command = "l"
            args[0] -= cursor[0]
            args[1] -= cursor[1]
            cursor[0] += args[0]
            cursor[1] += args[1]
        elif command == "h":
            cursor[0] += args[0]
        elif command == "H":
            command = "h"
            args[0] -= cursor[0]
            cursor[0] += args[0]
        elif command == "v":
            cursor[1] += args[0]
        elif command == "V":
            command = "v"
            args[0] -= cursor[1]
            cursor[1] += args[0]
        elif command == "c":
            cursor[0] += args[4]
            cursor[1] += args[5]
        elif command == "C":
            command = "c"
            for k in (0, 2, 4):
                args[k] -= cursor[0]
            for k in (1, 3, 5):
                args[k] -= cursor[1]
            cursor[0] += args[4]
            cursor[1] += args[5]
        elif command == "s":
            cursor[0] += args[2]
            cursor[1] += args[3]
        elif command == "S":
            command = "s"
            args[0] -= cursor[0]
            args[1] -= cursor[1]
            args[2] -= cursor[0]
            args[3] -= cursor[1]
            cursor[0] += args[2]
            cursor[1] += args[3]
        elif command == "q":
            cursor[0] += args[2]
            cursor[1] += args[3]
        elif command == "Q":
            command = "q"
            args[0] -= cursor[0]
            args[1] -= cursor[1]
            args[2] -= cursor[0]
            args[3] -= cursor[1]
            cursor[0] += args[2]
            cursor[1] += args[3]
        elif command == "t":
            cursor[0] += args[0]
            cursor[1] += args[1]
        elif command == "T":
            command = "t"
            args[0] -= cursor[0]
            args[1] -= cursor[1]
            cursor[0] += args[0]
            cursor[1] += args[1]
        elif command == "a":
            cursor[0] += args[5]
            cursor[1] += args[6]
        elif command == "A":
            command = "a"
            args[5] -= cursor[0]
            args[6] -= cursor[1]
            cursor[0] += args[5]
            cursor[1] += args[6]
        elif command in ("Z", "z"):
            cursor[0], cursor[1] = start[0], start[1]
        path_item.command = command
        path_item.base = list(prev_coords)
        path_item.coords = [cursor[0], cursor[1]]
        prev_coords = path_item.coords
    return path_data


def _filters(
    path: list[PathDataItem],
    params: dict[str, Any],
    state: _ConvertPathState,
    is_safe_to_use_z: bool,
    is_safe_to_remove: Callable[[bool, bool], bool],
    has_marker_mid: bool,
) -> list[PathDataItem]:
    stringify = _data2path(state, params)
    make_arcs = params.get("makeArcs")
    arc_threshold = params.get("makeArcs", {}).get("threshold", 2.5)
    arc_tolerance = params.get("makeArcs", {}).get("tolerance", 0.5)
    rel_subpoint = [0.0, 0.0]
    path_base = [0.0, 0.0]
    prev: PathDataItem = PathDataItem(command="", args=[])
    prev_q_control_point: list[float] | None = None
    precision_active = state.precision is not False and state.precision is not None

    source = list(path)
    result: list[PathDataItem] = []
    index = 0
    while index < len(source):
        item = source[index]
        q_control_point = prev_q_control_point
        command = item.command
        data = item.args
        nxt = source[index + 1] if index + 1 < len(source) else None

        if command not in ("Z", "z"):
            sdata = data
            if command == "s":
                pdata = prev.args
                n = len(pdata)
                sdata = [pdata[n - 2] - pdata[n - 4], pdata[n - 1] - pdata[n - 3]] + list(data)

            circle = None
            if make_arcs and command in ("c", "s") and _is_convex(sdata):
                circle = _find_circle(sdata, arc_threshold, arc_tolerance, state.error)
            if circle is not None:
                center, radius = circle
                r = state.round_data([radius])[0]
                angle = _find_arc_angle(sdata, center)
                sweep = 1 if sdata[5] * sdata[0] - sdata[4] * sdata[1] > 0 else 0
                arc = PathDataItem(
                    command="a",
                    args=[r, r, 0.0, 0.0, float(sweep), sdata[4], sdata[5]],
                    coords=list(item.coords),
                    base=list(item.base),
                )
                output: list[PathDataItem] = [arc]
                rel_center = [center[0] - sdata[4], center[1] - sdata[5]]
                rel_circle_center = list(rel_center)
                rel_circle_radius = radius
                arc_curves = [item]
                has_prev = 0
                suffix = ""
                if (
                    prev.command == "c"
                    and _is_convex(prev.args)
                    and _is_arc(
                        prev.args,
                        [center[0] + prev.args[4], center[1] + prev.args[5]],
                        radius,
                        arc_threshold,
                        arc_tolerance,
                        state.error,
                    )
                ) or (
                    prev.command == "a"
                    and _arc_prev_fits(prev, center, radius, arc_threshold, arc_tolerance, state.error)
                ):
                    arc_curves.insert(0, prev)
                    arc.base = list(prev.base)
                    arc.args[5] = arc.coords[0] - arc.base[0]
                    arc.args[6] = arc.coords[1] - arc.base[1]
                    prev_data = prev.args
                    prev_angle = _find_arc_angle(
                        prev_data,
                        [prev_data[4] + center[0], prev_data[5] + center[1]],
                    )
                    angle += prev_angle
                    if angle > math.pi:
                        arc.args[3] = 1.0
                    has_prev = 1

                j = index
                while True:
                    j += 1
                    if j >= len(source):
                        break
                    next_item = source[j]
                    if next_item.command not in ("c", "s"):
                        break
                    next_data = next_item.args
                    if next_item.command == "s":
                        longhand = _make_longhand(
                            PathDataItem(command="s", args=list(next_item.args)),
                            source[j - 1].args,
                        )
                        next_data = longhand.args
                        suffix = (
                            stringify([PathDataItem(command="q", args=next_data[:2])])
                            if longhand.command == "q"
                            else stringify([PathDataItem(command="c", args=next_data[:2])])
                        )
                    if _is_convex(next_data) and _is_arc(
                        next_data, rel_circle_center, rel_circle_radius, arc_threshold, arc_tolerance, state.error
                    ):
                        angle += _find_arc_angle(next_data, rel_circle_center)
                        if angle - 2 * math.pi > 1e-3:
                            break
                        if angle > math.pi:
                            arc.args[3] = 1.0
                        arc_curves.append(next_item)
                        if 2 * math.pi - angle > 1e-3:
                            arc.coords = list(next_item.coords)
                            arc.args[5] = arc.coords[0] - arc.base[0]
                            arc.args[6] = arc.coords[1] - arc.base[1]
                        else:
                            arc.args[5] = 2 * (rel_circle_center[0] - next_data[4])
                            arc.args[6] = 2 * (rel_circle_center[1] - next_data[5])
                            arc.coords = [arc.base[0] + arc.args[5], arc.base[1] + arc.args[6]]
                            arc = PathDataItem(
                                command="a",
                                args=[
                                    r,
                                    r,
                                    0.0,
                                    0.0,
                                    float(sweep),
                                    next_item.coords[0] - arc.coords[0],
                                    next_item.coords[1] - arc.coords[1],
                                ],
                                coords=list(next_item.coords),
                                base=list(arc.coords),
                            )
                            output.append(arc)
                            j += 1
                            break
                        rel_circle_center[0] -= next_data[4]
                        rel_circle_center[1] -= next_data[5]
                    else:
                        break

                if len(stringify(output)) + len(suffix) < len(stringify(arc_curves)):
                    if j < len(source) and source[j].command == "s":
                        _make_longhand(source[j], source[j - 1].args)
                    if has_prev:
                        prev_arc = output.pop(0)
                        state.round_data(prev_arc.args)
                        rel_subpoint[0] += prev_arc.args[5] - prev.args[len(prev.args) - 2]
                        rel_subpoint[1] += prev_arc.args[6] - prev.args[len(prev.args) - 1]
                        prev.command = "a"
                        prev.args = prev_arc.args
                        item.base = list(prev.coords)
                        prev.coords = list(prev_arc.coords)
                    arc = output.pop(0)
                    consumed = len(arc_curves) - 1 - has_prev
                    if consumed > 0:
                        source[index + 1 : index + 1 + consumed] = output
                    if arc_curves and len(arc_curves) - 1 - has_prev == 0:
                        item._sdata = list(sdata)
                    command = "a"
                    data = arc.args
                    item.coords = list(arc.coords)

            if precision_active:
                if command in ("m", "l", "t", "q", "s", "c"):
                    for i in range(len(data)):
                        data[i] += item.base[i % 2] - rel_subpoint[i % 2]
                elif command == "h":
                    data[0] += item.base[0] - rel_subpoint[0]
                elif command == "v":
                    data[0] += item.base[1] - rel_subpoint[1]
                elif command == "a":
                    data[5] += item.base[0] - rel_subpoint[0]
                    data[6] += item.base[1] - rel_subpoint[1]
                state.round_data(data)
                if command == "h":
                    rel_subpoint[0] += data[0]
                elif command == "v":
                    rel_subpoint[1] += data[0]
                else:
                    rel_subpoint[0] += data[len(data) - 2]
                    rel_subpoint[1] += data[len(data) - 1]
                state.round_data(rel_subpoint)
                if command in ("M", "m"):
                    path_base[0] = rel_subpoint[0]
                    path_base[1] = rel_subpoint[1]

            sagitta = _calculate_sagitta(data, state.error) if command == "a" else None
            if params.get("smartArcRounding", True) and sagitta is not None and isinstance(state.precision, int):
                for precision_new in range(int(state.precision), -1, -1):
                    radius = tools_to_fixed(data[0], precision_new)
                    sagitta_new = _calculate_sagitta([radius, radius] + list(data[2:]), state.error)
                    if sagitta_new is not None and abs(sagitta - sagitta_new) < state.error:
                        data[0] = radius
                        data[1] = radius
                    else:
                        break

            if params.get("straightCurves", True):
                if (command == "c" and _is_curve_straight_line(data, state.error)) or (
                    command == "s" and _is_curve_straight_line(sdata, state.error)
                ):
                    if nxt is not None and nxt.command == "s":
                        _make_longhand(nxt, data)
                    command = "l"
                    data = list(data[-2:])
                elif (command == "q" and _is_curve_straight_line(data, state.error)) or (
                    command == "t" and prev.command not in ("q", "t")
                ):
                    if command == "q" and nxt is not None and nxt.command == "t":
                        _make_longhand(nxt, data)
                    if command == "t" and nxt is not None and nxt.command == "t":
                        nxt.command = "q"
                        nxt.args.insert(0, (2 * item.coords[0] - item.base[0]) - item.coords[0])
                        nxt.args.insert(1, (2 * item.coords[1] - item.base[1]) - item.coords[1])
                    command = "l"
                    data = list(data[-2:])
                elif command == "a" and (
                    data[0] == 0 or data[1] == 0 or (sagitta is not None and sagitta < state.error)
                ):
                    command = "l"
                    data = list(data[-2:])

            if params.get("convertToQ", True) and command == "c":
                x1 = 0.75 * (item.base[0] + data[0]) - 0.25 * item.base[0]
                x2 = 0.75 * (item.base[0] + data[2]) - 0.25 * (item.base[0] + data[4])
                if abs(x1 - x2) < state.error * 2:
                    y1 = 0.75 * (item.base[1] + data[1]) - 0.25 * item.base[1]
                    y2 = 0.75 * (item.base[1] + data[3]) - 0.25 * (item.base[1] + data[5])
                    if abs(y1 - y2) < state.error * 2:
                        new_data = list(data)
                        new_data[0:4] = [x1 + x2 - item.base[0], y1 + y2 - item.base[1]]
                        state.round_data(new_data)
                        original_length = len(
                            cleanup_out_data(
                                data,
                                params.get("noSpaceAfterFlags", False),
                                params.get("leadingZero", True),
                                params.get("negativeExtraSpace", True),
                            )
                        )
                        new_length = len(
                            cleanup_out_data(
                                new_data,
                                params.get("noSpaceAfterFlags", False),
                                params.get("leadingZero", True),
                                params.get("negativeExtraSpace", True),
                            )
                        )
                        if new_length < original_length:
                            command = "q"
                            data = new_data
                            if nxt is not None and nxt.command == "s":
                                _make_longhand(nxt, data)

            if params.get("lineShorthands", True) and command == "l":
                if data[1] == 0:
                    command = "h"
                    data = data[:-1]
                elif data[0] == 0:
                    command = "v"
                    data = data[1:]

            if (
                params.get("collapseRepeated", True)
                and not has_marker_mid
                and command in ("m", "h", "v")
                and prev.command
                and command == prev.command.lower()
                and (command not in ("h", "v") or (data and (prev.args[0] >= 0) == (data[0] >= 0)))
            ):
                prev.args[0] += data[0]
                if command not in ("h", "v"):
                    prev.args[1] += data[1]
                prev.coords = list(item.coords)
                index += 1
                continue

            if params.get("curveSmoothShorthands", True) and prev.command:
                if command == "c":
                    if (
                        prev.command == "c"
                        and abs(data[0] - -(prev.args[2] - prev.args[4])) < state.error
                        and abs(data[1] - -(prev.args[3] - prev.args[5])) < state.error
                    ):
                        command = "s"
                        data = data[2:]
                    elif (
                        prev.command == "s"
                        and abs(data[0] - -(prev.args[0] - prev.args[2])) < state.error
                        and abs(data[1] - -(prev.args[1] - prev.args[3])) < state.error
                    ):
                        command = "s"
                        data = data[2:]
                    elif prev.command not in ("c", "s") and abs(data[0]) < state.error and abs(data[1]) < state.error:
                        command = "s"
                        data = data[2:]
                elif command == "q":
                    if (
                        prev.command == "q"
                        and abs(data[0] - (prev.args[2] - prev.args[0])) < state.error
                        and abs(data[1] - (prev.args[3] - prev.args[1])) < state.error
                    ):
                        command = "t"
                        data = data[2:]
                    elif prev.command == "t" and q_control_point is not None:
                        predicted = _reflect_point(q_control_point, item.base)
                        real = [data[0] + item.base[0], data[1] + item.base[1]]
                        if abs(predicted[0] - real[0]) < state.error and abs(predicted[1] - real[1]) < state.error:
                            command = "t"
                            data = data[2:]

            if params.get("removeUseless", True) and is_safe_to_remove(prev.command in ("m", "M"), True):
                if command in ("l", "h", "v", "q", "t", "c", "s") and all(v == 0 for v in data):
                    index += 1
                    continue
                if command == "a" and data[5] == 0 and data[6] == 0:
                    index += 1
                    continue

            if (
                params.get("convertToZ", True)
                and (is_safe_to_use_z or (nxt is not None and nxt.command in ("Z", "z")))
                and command in ("l", "h", "v")
            ):
                if (
                    abs(path_base[0] - item.coords[0]) < state.error
                    and abs(path_base[1] - item.coords[1]) < state.error
                ):
                    command = "z"
                    data = []
        else:
            rel_subpoint[0] = path_base[0]
            rel_subpoint[1] = path_base[1]
            if prev.command in ("Z", "z"):
                index += 1
                continue

        if (
            command in ("Z", "z")
            and params.get("removeUseless", True)
            and is_safe_to_remove(prev.command in ("m", "M"), is_safe_to_use_z)
            and abs(item.base[0] - item.coords[0]) < state.error / 10
            and abs(item.base[1] - item.coords[1]) < state.error / 10
        ):
            index += 1
            continue

        if command == "q":
            prev_q_control_point = [data[0] + item.base[0], data[1] + item.base[1]]
        elif command == "t":
            if q_control_point is not None:
                prev_q_control_point = _reflect_point(q_control_point, item.base)
            else:
                prev_q_control_point = list(item.coords)
        else:
            prev_q_control_point = None

        item.command = command
        item.args = data
        result.append(item)
        prev = item
        index += 1

    return result


def _arc_prev_fits(
    prev: PathDataItem, center: Sequence[float], radius: float, arc_threshold: float, arc_tolerance: float, error: float
) -> bool:
    sdata = getattr(prev, "_sdata", None)
    if sdata is None:
        return False
    return _is_arc(sdata, [center[0] + sdata[4], center[1] + sdata[5]], radius, arc_threshold, arc_tolerance, error)


def _convert_to_mixed(path: list[PathDataItem], state: _ConvertPathState, params: dict[str, Any]) -> list[PathDataItem]:
    if not path:
        return path
    prev = path[0]
    result = [path[0]]
    leading_zero = params.get("leadingZero", True)
    negative_extra_space = params.get("negativeExtraSpace", True)
    no_space_after_flags = params.get("noSpaceAfterFlags", False)
    for index in range(1, len(path)):
        item = path[index]
        if item.command in ("Z", "z"):
            result.append(item)
            prev = item
            continue
        command = item.command
        data = item.args
        adata = list(data)
        if command in ("m", "l", "t", "q", "s", "c"):
            for i in range(len(adata)):
                adata[i] += item.base[i % 2]
        elif command == "h":
            adata[0] += item.base[0]
        elif command == "v":
            adata[0] += item.base[1]
        elif command == "a":
            adata[5] += item.base[0]
            adata[6] += item.base[1]
        state.round_data(adata)
        rdata = list(data)
        state.round_data(rdata)
        absolute_str = cleanup_out_data(adata, no_space_after_flags, leading_zero, negative_extra_space)
        relative_str = cleanup_out_data(rdata, no_space_after_flags, leading_zero, negative_extra_space)
        use_absolute = False
        if params.get("forceAbsolutePath", False):
            use_absolute = True
        elif len(absolute_str) < len(relative_str):
            continuity_exception = (
                params.get("negativeExtraSpace", True)
                and command == prev.command
                and len(prev.command) == 1
                and prev.command.islower()
                and len(absolute_str) == len(relative_str) - 1
                and (
                    data[0] < 0
                    or (math.floor(data[0]) == 0 and data[0] != int(data[0]) and prev.args and prev.args[-1] % 1 != 0)
                )
            )
            use_absolute = not continuity_exception
        if use_absolute:
            item.command = command.upper()
            item.args = adata
        result.append(item)
        prev = item
    return result


def _convert_path_data(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    new_params = {
        "applyTransforms": params.get("applyTransforms", True),
        "applyTransformsStroked": params.get("applyTransformsStroked", True),
        "makeArcs": params.get("makeArcs", {"threshold": 2.5, "tolerance": 0.5}),
        "straightCurves": params.get("straightCurves", True),
        "convertToQ": params.get("convertToQ", True),
        "lineShorthands": params.get("lineShorthands", True),
        "convertToZ": params.get("convertToZ", True),
        "curveSmoothShorthands": params.get("curveSmoothShorthands", True),
        "floatPrecision": params.get("floatPrecision", 3),
        "transformPrecision": params.get("transformPrecision", 5),
        "smartArcRounding": params.get("smartArcRounding", True),
        "removeUseless": params.get("removeUseless", True),
        "collapseRepeated": params.get("collapseRepeated", True),
        "utilizeAbsolute": params.get("utilizeAbsolute", True),
        "leadingZero": params.get("leadingZero", True),
        "negativeExtraSpace": params.get("negativeExtraSpace", True),
        "noSpaceAfterFlags": params.get("noSpaceAfterFlags", False),
        "forceAbsolutePath": params.get("forceAbsolutePath", False),
    }

    if new_params["applyTransforms"]:
        visitor = _apply_transforms_plugin(
            root,
            {
                "transformPrecision": new_params["transformPrecision"],
                "applyTransformsStroked": new_params["applyTransformsStroked"],
            },
            info,
        )
        if visitor is not None:
            visit(root, visitor)

    stylesheet = collect_stylesheet(root)

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        if element.name not in PATH_ELEMS or element.attributes.get("d") is None:
            return
        computed_style = compute_style(stylesheet, element)
        precision = new_params["floatPrecision"]
        if precision is False or precision is None:
            error = 1e-2
        else:
            error = js_to_fixed(0.1**precision, int(precision))
        if isinstance(precision, int) and 0 < precision < 20:
            state = _ConvertPathState(
                precision=precision,
                error=error,
                round_data=lambda data: data,
            )
            state.round_data = _strong_round(state)
        else:
            state = _ConvertPathState(
                precision=precision,
                error=error,
                round_data=_plain_round_fn,
            )
        has_marker_mid = computed_style.get("marker-mid") is not None
        stroke_style = computed_style.get("stroke")
        maybe_has_stroke = bool(stroke_style) and (
            stroke_style["type"] == "dynamic" or stroke_style.get("value") != "none"
        )
        linecap_style = computed_style.get("stroke-linecap")
        maybe_has_linecap = bool(linecap_style) and (
            linecap_style["type"] == "dynamic" or linecap_style.get("value") != "butt"
        )
        if maybe_has_stroke:
            cap = computed_style.get("stroke-linecap")
            join = computed_style.get("stroke-linejoin")
            is_safe_to_use_z = bool(
                cap
                and cap["type"] == "static"
                and cap.get("value") == "round"
                and join
                and join["type"] == "static"
                and join.get("value") == "round"
            )
        else:
            is_safe_to_use_z = True

        def is_safe_to_remove(is_first_draw: bool, safe_if_not_first_draw: bool) -> bool:
            if not maybe_has_stroke:
                return True
            if is_first_draw:
                return not maybe_has_linecap
            return safe_if_not_first_draw

        data = path2js(element)
        if not data:
            return
        includes_vertices = any(item.command not in ("m", "M") for item in data)
        data = _convert_to_relative(data)
        data = _filters(
            data,
            new_params,
            state,
            is_safe_to_use_z,
            is_safe_to_remove,
            has_marker_mid,
        )
        if new_params["utilizeAbsolute"]:
            data = _convert_to_mixed(data, state, new_params)
        has_marker = (
            element.attributes.get("marker-start") is not None or element.attributes.get("marker-end") is not None
        )
        is_markers_only_path = has_marker and includes_vertices and all(item.command in ("m", "M") for item in data)
        if is_markers_only_path:
            data.append(PathDataItem(command="z", args=[]))
        js2path(
            element,
            data,
            float_precision=(None if precision is False else precision),
            no_space_after_flags=new_params["noSpaceAfterFlags"],
        )

    return {"element": {"enter": enter}}


_ALLOWED_CHILDREN_PER_ELEMENT: dict[str, set[str]] = {}
_ALLOWED_ATTRIBUTES_PER_ELEMENT: dict[str, set[str]] = {}
_ATTRIBUTES_DEFAULTS_PER_ELEMENT: dict[str, dict[str, str]] = {}


def _build_element_tables() -> None:
    for name, config in ELEMS.items():
        allowed_children: set[str] = set()
        for element_name in config.get("content", ()):
            allowed_children.add(element_name)
        for group_name in config.get("contentGroups", ()):
            allowed_children.update(ELEMS_GROUPS.get(group_name, ()))
        allowed_attributes: set[str] = set()
        for attr_name in config.get("attrs", ()):
            allowed_attributes.add(attr_name)
        defaults: dict[str, str] = dict(config.get("defaults", {}))
        for group_name in config["attrsGroups"]:
            allowed_attributes.update(ATTRS_GROUPS.get(group_name, ()))
            group_defaults = ATTRS_GROUPS_DEFAULTS.get(group_name)
            if group_defaults:
                defaults.update(group_defaults)
        _ALLOWED_CHILDREN_PER_ELEMENT[name] = allowed_children
        _ALLOWED_ATTRIBUTES_PER_ELEMENT[name] = allowed_attributes
        _ATTRIBUTES_DEFAULTS_PER_ELEMENT[name] = defaults


_build_element_tables()


def _remove_unknowns_and_defaults(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    unknown_content = params.get("unknownContent", True)
    unknown_attrs = params.get("unknownAttrs", True)
    default_attrs = params.get("defaultAttrs", True)
    default_markup_declarations = params.get("defaultMarkupDeclarations", True)
    useless_overrides = params.get("uselessOverrides", True)
    keep_data_attrs = params.get("keepDataAttrs", True)
    keep_aria_attrs = params.get("keepAriaAttrs", True)
    keep_role_attr = params.get("keepRoleAttr", False)
    stylesheet = collect_stylesheet(root)

    def attribute_used_in_stylesheet(name: str) -> bool:
        return any(includes_attr_selector(rule.selector, name) for rule in stylesheet.rules)

    def enter(node: Node, parent: Node) -> Any:
        if node.type == "instruction":
            if default_markup_declarations and node.name == "xml":
                node.value = re.sub(r"\s*standalone\s*=\s*([\"'])no\1", "", node.value)
            return None
        if node.type != "element":
            return None
        element = node
        if ":" in element.name:
            return None
        if element.name == "foreignObject":
            return VISIT_SKIP
        if unknown_content and parent.type == "element":
            allowed_children = _ALLOWED_CHILDREN_PER_ELEMENT.get(parent.name)
            if not allowed_children:
                if element.name not in _ALLOWED_CHILDREN_PER_ELEMENT:
                    detach_node_from_parent(element, parent)
                    return None
            elif element.name not in allowed_children:
                detach_node_from_parent(element, parent)
                return None
        allowed_attributes = _ALLOWED_ATTRIBUTES_PER_ELEMENT.get(element.name)
        attributes_defaults = _ATTRIBUTES_DEFAULTS_PER_ELEMENT.get(element.name)
        computed_parent_style = compute_style(stylesheet, parent) if parent.type == "element" else None
        for name, value in list(element.attributes.items()):
            if keep_data_attrs and name.startswith("data-"):
                continue
            if keep_aria_attrs and name.startswith("aria-"):
                continue
            if keep_role_attr and name == "role":
                continue
            if name == "xmlns":
                continue
            if ":" in name:
                prefix = name.split(":", 1)[0]
                if prefix not in ("xml", "xlink"):
                    continue
            if unknown_attrs and allowed_attributes is not None and name not in allowed_attributes:
                del element.attributes[name]
            if (
                default_attrs
                and element.attributes.get("id") is None
                and attributes_defaults is not None
                and attributes_defaults.get(name) == value
                and (computed_parent_style is None or computed_parent_style.get(name) is None)
                and not attribute_used_in_stylesheet(name)
            ):
                del element.attributes[name]
            if useless_overrides and element.attributes.get("id") is None:
                style = computed_parent_style.get(name) if computed_parent_style else None
                if (
                    name not in PRESENTATION_NON_INHERITABLE_GROUP_ATTRS
                    and style is not None
                    and style["type"] == "static"
                    and style.get("value") == value
                ):
                    del element.attributes[name]
        return None

    return {
        "instruction": {"enter": lambda node, parent: enter(node, parent)},
        "element": {"enter": enter},
    }


def _remove_non_inheritable_group_attrs(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element" or node.name != "g":
            return
        for name in list(node.attributes):
            if (
                name in ATTRS_GROUPS["presentation"]
                and name not in INHERITABLE_ATTRS
                and name not in PRESENTATION_NON_INHERITABLE_GROUP_ATTRS
            ):
                del node.attributes[name]

    return {"element": {"enter": enter}}


def _remove_useless_stroke_and_fill(
    root: Document, params: dict[str, Any], info: dict[str, Any]
) -> dict[str, Any] | None:
    remove_stroke = params.get("stroke", True)
    remove_fill = params.get("fill", True)
    remove_none = params.get("removeNone", False)

    has_style_or_script = False

    def detect(node: Node, parent: Node) -> None:
        nonlocal has_style_or_script
        if node.type == "element" and (node.name == "style" or has_scripts(node)):
            has_style_or_script = True

    visit(root, {"element": {"enter": detect}})
    if has_style_or_script:
        return None
    stylesheet = collect_stylesheet(root)

    def enter(node: Node, parent: Node) -> Any:
        if node.type != "element":
            return None
        element = node
        if element.attributes.get("id") is not None:
            return VISIT_SKIP
        if element.name not in ELEMS_GROUPS["shape"]:
            return None
        computed_style = compute_style(stylesheet, element)
        stroke = computed_style.get("stroke")
        stroke_opacity = computed_style.get("stroke-opacity")
        stroke_width = computed_style.get("stroke-width")
        marker_end = computed_style.get("marker-end")
        fill = computed_style.get("fill")
        fill_opacity = computed_style.get("fill-opacity")
        computed_parent_style = compute_style(stylesheet, parent) if parent.type == "element" else None
        parent_stroke = computed_parent_style.get("stroke") if computed_parent_style else None
        if remove_stroke:
            if (
                stroke is None
                or (stroke["type"] == "static" and stroke.get("value") == "none")
                or (
                    stroke_opacity is not None
                    and stroke_opacity["type"] == "static"
                    and stroke_opacity.get("value") == "0"
                )
                or (stroke_width is not None and stroke_width["type"] == "static" and stroke_width.get("value") == "0")
            ):
                if (
                    stroke_width is not None and stroke_width["type"] == "static" and stroke_width.get("value") == "0"
                ) or marker_end is None:
                    for name in list(element.attributes):
                        if name.startswith("stroke"):
                            del element.attributes[name]
                    if (
                        parent_stroke is not None
                        and parent_stroke["type"] == "static"
                        and parent_stroke.get("value") != "none"
                    ):
                        element.attributes["stroke"] = "none"
        if remove_fill:
            if (fill is not None and fill["type"] == "static" and fill.get("value") == "none") or (
                fill_opacity is not None and fill_opacity["type"] == "static" and fill_opacity.get("value") == "0"
            ):
                for name in list(element.attributes):
                    if name.startswith("fill-"):
                        del element.attributes[name]
                if fill is None or (fill["type"] == "static" and fill.get("value") != "none"):
                    element.attributes["fill"] = "none"
        if remove_none:
            if (stroke is None or element.attributes.get("stroke") == "none") and (
                (fill is not None and fill["type"] == "static" and fill.get("value") == "none")
                or element.attributes.get("fill") == "none"
            ):
                detach_node_from_parent(element, parent)
        return None

    return {"element": {"enter": enter}}


_REG_ENABLE_BACKGROUND = re.compile(
    r"^new\s0\s0\s([-+]?\d*\.?\d+([eE][-+]?\d+)?)\s" r"([-+]?\d*\.?\d+([eE][-+]?\d+)?)$"
)


def _cleanup_enable_background(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    has_filter = False

    def detect(node: Node, parent: Node) -> None:
        nonlocal has_filter
        if node.type == "element" and node.name == "filter":
            has_filter = True

    visit(root, {"element": {"enter": detect}})

    def cleanup_value(value: str, node_name: str, width: str, height: str) -> str | None:
        match = _REG_ENABLE_BACKGROUND.match(value)
        if match is not None and width == match.group(1) and height == match.group(3):
            return None if node_name == "svg" else "new"
        return value

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        if not has_filter:
            element.attributes.pop("enable-background", None)
            style_value = element.attributes.get("style")
            if style_value is not None:
                declarations = parse_declarations(style_value)
                kept = [d for d in declarations if d.name != "enable-background"]
                if not kept:
                    del element.attributes["style"]
                else:
                    element.attributes["style"] = ";".join(f"{d.name}:{d.value}" for d in kept)
            return
        has_dimensions = element.attributes.get("width") is not None and element.attributes.get("height") is not None
        if element.name in ("svg", "mask", "pattern") and has_dimensions:
            attr_value = element.attributes.get("enable-background")
            if attr_value is not None:
                cleaned = cleanup_value(
                    attr_value,
                    element.name,
                    element.attributes["width"],
                    element.attributes["height"],
                )
                if cleaned is None:
                    del element.attributes["enable-background"]
                else:
                    element.attributes["enable-background"] = cleaned
            style_value = element.attributes.get("style")
            if style_value is not None:
                declarations = parse_declarations(style_value)
                kept: list[CssDeclaration] = []
                for declaration in declarations:
                    if declaration.name == "enable-background":
                        cleaned = cleanup_value(
                            declaration.value,
                            element.name,
                            element.attributes["width"],
                            element.attributes["height"],
                        )
                        if cleaned is not None:
                            kept.append(CssDeclaration(declaration.name, cleaned, declaration.important))
                    else:
                        kept.append(declaration)
                if not kept:
                    del element.attributes["style"]
                else:
                    element.attributes["style"] = ";".join(f"{d.name}:{d.value}" for d in kept)

    return {"element": {"enter": enter}}


def _remove_hidden_elems(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    display_none = params.get("displayNone", True)
    opacity0 = params.get("opacity0", True)
    circle_r0 = params.get("circleR0", True)
    ellipse_rx0 = params.get("ellipseRX0", True)
    ellipse_ry0 = params.get("ellipseRY0", True)
    rect_width0 = params.get("rectWidth0", True)
    rect_height0 = params.get("rectHeight0", True)
    pattern_width0 = params.get("patternWidth0", True)
    pattern_height0 = params.get("patternHeight0", True)
    image_width0 = params.get("imageWidth0", True)
    image_height0 = params.get("imageHeight0", True)
    path_empty_d = params.get("pathEmptyD", True)
    polyline_empty_points = params.get("polylineEmptyPoints", True)
    polygon_empty_points = params.get("polygonEmptyPoints", True)
    is_hidden = params.get("isHidden", True)
    stylesheet = collect_stylesheet(root)

    non_rendered_nodes: list[tuple[Element, Node]] = []
    removed_def_ids: set[str] = set()
    all_defs: list[tuple[Element, Node]] = []
    all_references: set[str] = set()
    references_by_id: dict[str, list[tuple[Element, Node]]] = {}
    deoptimized = False

    def can_remove_non_rendering_node(node: Element) -> bool:
        if node.attributes.get("id") in all_references:
            return False
        for child in node.children:
            if isinstance(child, Element) and not can_remove_non_rendering_node(child):
                return False
        return True

    def remove_element(node: Element, parent: Node) -> None:
        if node.attributes.get("id") is not None and isinstance(parent, Element) and parent.name == "defs":
            removed_def_ids.add(node.attributes["id"])
        detach_node_from_parent(node, parent)

    def pre_pass(node: Node, parent: Node) -> Any:
        if node.type != "element":
            return None
        element = node
        if element.name in ELEMS_GROUPS["nonRendering"]:
            non_rendered_nodes.append((element, parent))
            return VISIT_SKIP
        computed_style = compute_style(stylesheet, element)
        opacity = computed_style.get("opacity")
        if opacity0 and opacity is not None and opacity["type"] == "static" and opacity.get("value") == "0":
            if element.name == "path":
                non_rendered_nodes.append((element, parent))
                return VISIT_SKIP
            remove_element(element, parent)
        return None

    visit(root, {"element": {"enter": pre_pass}})

    def enter(node: Node, parent: Node) -> Any:
        nonlocal deoptimized
        if node.type != "element":
            return None
        element = node
        if (element.name == "style" and element.children) or has_scripts(element):
            deoptimized = True
            return None
        if element.name == "defs":
            all_defs.append((element, parent))
        if element.name == "use":
            for attr, value in element.attributes.items():
                if attr != "href" and not attr.endswith(":href"):
                    continue
                references_by_id.setdefault(value[1:], []).append((element, parent))
        if circle_r0 and element.name == "circle" and not element.children and element.attributes.get("r") == "0":
            remove_element(element, parent)
            return None
        if ellipse_rx0 and element.name == "ellipse" and not element.children and element.attributes.get("rx") == "0":
            remove_element(element, parent)
            return None
        if ellipse_ry0 and element.name == "ellipse" and not element.children and element.attributes.get("ry") == "0":
            remove_element(element, parent)
            return None
        if rect_width0 and element.name == "rect" and not element.children and element.attributes.get("width") == "0":
            remove_element(element, parent)
            return None
        if (
            rect_height0
            and rect_width0
            and element.name == "rect"
            and not element.children
            and element.attributes.get("height") == "0"
        ):
            remove_element(element, parent)
            return None
        if pattern_width0 and element.name == "pattern" and element.attributes.get("width") == "0":
            remove_element(element, parent)
            return None
        if pattern_height0 and element.name == "pattern" and element.attributes.get("height") == "0":
            remove_element(element, parent)
            return None
        if image_width0 and element.name == "image" and element.attributes.get("width") == "0":
            remove_element(element, parent)
            return None
        if image_height0 and element.name == "image" and element.attributes.get("height") == "0":
            remove_element(element, parent)
            return None
        if polyline_empty_points and element.name == "polyline" and element.attributes.get("points") is None:
            remove_element(element, parent)
            return None
        if polygon_empty_points and element.name == "polygon" and element.attributes.get("points") is None:
            remove_element(element, parent)
            return None
        computed_style = compute_style(stylesheet, element)
        visibility = computed_style.get("visibility")
        if (
            is_hidden
            and visibility is not None
            and visibility["type"] == "static"
            and visibility.get("value") == "hidden"
            and query_selector(element, "[visibility=visible]") is None
        ):
            remove_element(element, parent)
            return None
        display = computed_style.get("display")
        if (
            display_none
            and display is not None
            and display["type"] == "static"
            and display.get("value") == "none"
            and element.name != "marker"
        ):
            remove_element(element, parent)
            return None
        if path_empty_d and element.name == "path":
            if element.attributes.get("d") is None:
                remove_element(element, parent)
                return None
            path_data = parse_path_data(element.attributes["d"])
            if not path_data:
                remove_element(element, parent)
                return None
            if (
                len(path_data) == 1
                and computed_style.get("marker-start") is None
                and computed_style.get("marker-end") is None
            ):
                remove_element(element, parent)
                return None
        for name, value in element.attributes.items():
            for id_value in find_references(name, value):
                all_references.add(id_value)
        return None

    def exit_root() -> None:
        for id_value in removed_def_ids:
            for node, parent in references_by_id.get(id_value, []):
                detach_node_from_parent(node, parent)
        if not deoptimized:
            for node, parent in non_rendered_nodes:
                if can_remove_non_rendering_node(node):
                    detach_node_from_parent(node, parent)
        for node, parent in all_defs:
            if not node.children:
                detach_node_from_parent(node, parent)

    return {
        "element": {"enter": enter},
        "root": {"exit": lambda node, parent: exit_root()},
    }


def _remove_empty_text(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    text = params.get("text", True)
    tspan = params.get("tspan", True)
    tref = params.get("tref", True)

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        if text and element.name == "text" and not element.children:
            detach_node_from_parent(element, parent)
        if tspan and element.name == "tspan" and not element.children:
            detach_node_from_parent(element, parent)
        if tref and element.name == "tref" and element.attributes.get("xlink:href") is None:
            detach_node_from_parent(element, parent)

    return {"element": {"enter": enter}}


_REG_SHAPE_NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _convert_shape_to_path(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    convert_arcs = params.get("convertArcs", False)
    precision = params.get("floatPrecision")

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        attrs = element.attributes
        if (
            element.name == "rect"
            and attrs.get("width") is not None
            and attrs.get("height") is not None
            and attrs.get("rx") is None
            and attrs.get("ry") is None
        ):
            x = _js_number_or_nan(attrs.get("x") or "0")
            y = _js_number_or_nan(attrs.get("y") or "0")
            width = _js_number_or_nan(attrs["width"])
            height = _js_number_or_nan(attrs["height"])
            if _any_nan(x, y, width, height):
                return
            path_data = [
                PathDataItem(command="M", args=[x, y]),
                PathDataItem(command="H", args=[x + width]),
                PathDataItem(command="V", args=[y + height]),
                PathDataItem(command="H", args=[x]),
                PathDataItem(command="z", args=[]),
            ]
            element.name = "path"
            attrs["d"] = stringify_path_data(path_data, precision=precision)
            for name in ("x", "y", "width", "height"):
                attrs.pop(name, None)
        if element.name == "line":
            x1 = _js_number_or_nan(attrs.get("x1") or "0")
            y1 = _js_number_or_nan(attrs.get("y1") or "0")
            x2 = _js_number_or_nan(attrs.get("x2") or "0")
            y2 = _js_number_or_nan(attrs.get("y2") or "0")
            if _any_nan(x1, y1, x2, y2):
                return
            path_data = [
                PathDataItem(command="M", args=[x1, y1]),
                PathDataItem(command="L", args=[x2, y2]),
            ]
            element.name = "path"
            attrs["d"] = stringify_path_data(path_data, precision=precision)
            for name in ("x1", "y1", "x2", "y2"):
                attrs.pop(name, None)
        if element.name in ("polyline", "polygon") and attrs.get("points") is not None:
            coords = [float(m.group()) for m in _REG_SHAPE_NUMBER.finditer(attrs["points"])]
            if len(coords) < 4:
                detach_node_from_parent(element, parent)
                return
            path_data: list[PathDataItem] = []
            for i in range(0, len(coords), 2):
                path_data.append(
                    PathDataItem(
                        command="M" if i == 0 else "L",
                        args=coords[i : i + 2],
                    )
                )
            if element.name == "polygon":
                path_data.append(PathDataItem(command="z", args=[]))
            element.name = "path"
            attrs["d"] = stringify_path_data(path_data, precision=precision)
            attrs.pop("points", None)
        if element.name == "circle" and convert_arcs:
            cx = _js_number_or_nan(attrs.get("cx") or "0")
            cy = _js_number_or_nan(attrs.get("cy") or "0")
            r = _js_number_or_nan(attrs.get("r") or "0")
            if _any_nan(cx, cy, r):
                return
            path_data = [
                PathDataItem(command="M", args=[cx, cy - r]),
                PathDataItem(command="A", args=[r, r, 0, 1, 0, cx, cy + r]),
                PathDataItem(command="A", args=[r, r, 0, 1, 0, cx, cy - r]),
                PathDataItem(command="z", args=[]),
            ]
            element.name = "path"
            attrs["d"] = stringify_path_data(path_data, precision=precision)
            for name in ("cx", "cy", "r"):
                attrs.pop(name, None)
        if element.name == "ellipse" and convert_arcs:
            ecx = _js_number_or_nan(attrs.get("cx") or "0")
            ecy = _js_number_or_nan(attrs.get("cy") or "0")
            rx = _js_number_or_nan(attrs.get("rx") or "0")
            ry = _js_number_or_nan(attrs.get("ry") or "0")
            if _any_nan(ecx, ecy, rx, ry):
                return
            path_data = [
                PathDataItem(command="M", args=[ecx, ecy - ry]),
                PathDataItem(command="A", args=[rx, ry, 0, 1, 0, ecx, ecy + ry]),
                PathDataItem(command="A", args=[rx, ry, 0, 1, 0, ecx, ecy - ry]),
                PathDataItem(command="z", args=[]),
            ]
            element.name = "path"
            attrs["d"] = stringify_path_data(path_data, precision=precision)
            for name in ("cx", "cy", "rx", "ry"):
                attrs.pop(name, None)

    return {"element": {"enter": enter}}


def _js_number_or_nan(value: str) -> float:
    parsed = js_parse_float(value)
    return float("nan") if parsed is None else parsed


def _any_nan(*values: float) -> bool:
    return any(v != v for v in values)


def _convert_ellipse_to_circle(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element" or node.name != "ellipse":
            return
        rx = node.attributes.get("rx", "0")
        ry = node.attributes.get("ry", "0")
        if rx == ry or rx == "auto" or ry == "auto":
            node.name = "circle"
            radius = ry if rx == "auto" else rx
            node.attributes.pop("rx", None)
            node.attributes.pop("ry", None)
            node.attributes["r"] = radius

    return {"element": {"enter": enter}}


def _move_elems_attrs_to_group(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    deoptimized_with_styles = False

    def detect(node: Node, parent: Node) -> None:
        nonlocal deoptimized_with_styles
        if node.type == "element" and node.name == "style":
            deoptimized_with_styles = True

    visit(root, {"element": {"enter": detect}})

    def exit(node: Node, parent: Node) -> None:
        if node.type != "element" or node.name != "g":
            return
        if len(node.children) <= 1 or deoptimized_with_styles:
            return
        common_attributes: dict[str, str] = {}
        initial = True
        every_child_is_path = True
        for child in node.children:
            if isinstance(child, Element):
                if child.name not in PATH_ELEMS:
                    every_child_is_path = False
                if initial:
                    initial = False
                    for name, value in child.attributes.items():
                        if name in INHERITABLE_ATTRS:
                            common_attributes[name] = value
                else:
                    for name, value in list(common_attributes.items()):
                        if child.attributes.get(name) != value:
                            del common_attributes[name]
        if (
            node.attributes.get("filter") is not None
            or node.attributes.get("clip-path") is not None
            or node.attributes.get("mask") is not None
        ):
            common_attributes.pop("transform", None)
        if every_child_is_path:
            common_attributes.pop("transform", None)
        for name, value in common_attributes.items():
            if name == "transform":
                if node.attributes.get("transform") is not None:
                    node.attributes["transform"] = f"{node.attributes['transform']} {value}"
                else:
                    node.attributes["transform"] = value
            else:
                node.attributes[name] = value
        for child in node.children:
            if isinstance(child, Element):
                for name in common_attributes:
                    child.attributes.pop(name, None)

    return {"element": {"exit": exit}}


def _move_group_attrs_to_elems(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    allowed_children = set(PATH_ELEMS) | {"g", "text"}

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element" or node.name != "g":
            return
        element = node
        if not element.children:
            return
        if element.attributes.get("transform") is None:
            return
        has_reference = any(
            name in REFERENCES_PROPS and includes_url_reference(value) for name, value in element.attributes.items()
        )
        if has_reference:
            return
        if not all(
            isinstance(child, Element) and child.name in allowed_children and child.attributes.get("id") is None
            for child in element.children
        ):
            return
        value = element.attributes["transform"]
        for child in element.children:
            if isinstance(child, Element):
                if child.attributes.get("transform") is not None:
                    child.attributes["transform"] = f"{value} {child.attributes['transform']}"
                else:
                    child.attributes["transform"] = value
        del element.attributes["transform"]

    return {"element": {"enter": enter}}


def _has_animated_attr(node: Node, name: str) -> bool:
    if isinstance(node, Element):
        if node.name in ELEMS_GROUPS["animation"] and node.attributes.get("attributeName") == name:
            return True
        for child in node.children:
            if _has_animated_attr(child, name):
                return True
    return False


def _collapse_groups(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    stylesheet = collect_stylesheet(root)

    def exit(node: Node, parent: Node) -> None:
        if parent is None or parent.type == "root":
            return
        if parent.type == "element" and parent.name == "switch":
            return
        if node.type != "element" or node.name != "g" or not node.children:
            return
        element = node
        if element.attributes and len(element.children) == 1:
            first_child = element.children[0]
            node_has_filter = bool(element.attributes.get("filter") or compute_style(stylesheet, element).get("filter"))
            if (
                isinstance(first_child, Element)
                and first_child.attributes.get("id") is None
                and not node_has_filter
                and (element.attributes.get("class") is None or first_child.attributes.get("class") is None)
                and (
                    element.attributes.get("clip-path") is None
                    and element.attributes.get("mask") is None
                    or (
                        first_child.name == "g"
                        and element.attributes.get("transform") is None
                        and first_child.attributes.get("transform") is None
                    )
                )
            ):
                new_child_attrs = dict(first_child.attributes)
                aborted = False
                for name, value in element.attributes.items():
                    if _has_animated_attr(first_child, name):
                        aborted = True
                        break
                    if name not in new_child_attrs:
                        new_child_attrs[name] = value
                    elif name == "transform":
                        new_child_attrs[name] = value + " " + new_child_attrs[name]
                    elif new_child_attrs[name] == "inherit":
                        new_child_attrs[name] = value
                    elif name not in INHERITABLE_ATTRS and new_child_attrs[name] != value:
                        aborted = True
                        break
                if not aborted:
                    element.attributes = {}
                    first_child.attributes = new_child_attrs
        if not element.attributes:
            for child in element.children:
                if isinstance(child, Element) and child.name in ELEMS_GROUPS["animation"]:
                    return
            children = list(parent.children)
            try:
                position = children.index(element)
            except ValueError:
                return
            parent.children[position : position + 1] = list(element.children)

    return {"element": {"exit": exit}}


def _convert_transform(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    transform_params = _TransformParams(
        convert_to_shorts=params.get("convertToShorts", True),
        deg_precision=params.get("degPrecision"),
        float_precision=params.get("floatPrecision", 3),
        transform_precision=params.get("transformPrecision", 5),
        matrix_to_transform=params.get("matrixToTransform", True),
        short_translate=params.get("shortTranslate", True),
        short_scale=params.get("shortScale", True),
        short_rotate=params.get("shortRotate", True),
        remove_useless=params.get("removeUseless", True),
        collapse_into_one=params.get("collapseIntoOne", True),
        leading_zero=params.get("leadingZero", True),
        negative_extra_space=params.get("negativeExtraSpace", False),
    )

    def _float_digits(n: float) -> int:
        text = js_number_string(n)
        dot = text.find(".")
        if dot == -1:
            return 0
        return len(text) - dot - 1

    def _define_precision(data: list[TransformItem], base: _TransformParams) -> _TransformParams:
        matrix_data: list[float] = []
        for item in data:
            if item.name == "matrix":
                matrix_data.extend(item.data[:4])
        params_local = _TransformParams(
            convert_to_shorts=base.convert_to_shorts,
            deg_precision=base.deg_precision,
            float_precision=base.float_precision,
            transform_precision=base.transform_precision,
            matrix_to_transform=base.matrix_to_transform,
            short_translate=base.short_translate,
            short_scale=base.short_scale,
            short_rotate=base.short_rotate,
            remove_useless=base.remove_useless,
            collapse_into_one=base.collapse_into_one,
            leading_zero=base.leading_zero,
            negative_extra_space=base.negative_extra_space,
        )
        number_of_digits = params_local.transform_precision
        if matrix_data:
            digits = [_float_digits(n) for n in matrix_data]
            params_local.transform_precision = min(
                params_local.transform_precision,
                max(digits) if digits else params_local.transform_precision,
            )
            number_of_digits = max(len(re.sub(r"\D+", "", js_number_string(n))) for n in matrix_data)
        if params_local.deg_precision is None:
            params_local.deg_precision = max(0, min(params_local.float_precision, number_of_digits - 2))
        return params_local

    def _remove_useless(transforms: list[TransformItem]) -> list[TransformItem]:
        kept: list[TransformItem] = []
        for transform in transforms:
            data = transform.data
            if (
                transform.name in ("translate", "rotate", "skewX", "skewY")
                and (len(data) == 1 or transform.name == "rotate")
                and not data[0]
            ):
                continue
            if transform.name == "translate" and not data[0] and (len(data) < 2 or not data[1]):
                continue
            if transform.name == "scale" and data[0] == 1 and (len(data) < 2 or data[1] == 1):
                continue
            if (
                transform.name == "matrix"
                and data[0] == 1
                and data[3] == 1
                and not (data[1] or data[2] or data[4] or data[5])
            ):
                continue
            kept.append(transform)
        return kept

    def _convert_to_shorts(transforms: list[TransformItem], tp: _TransformParams) -> list[TransformItem]:
        i = 0
        while i < len(transforms):
            transform = transforms[i]
            if tp.matrix_to_transform and transform.name == "matrix":
                decomposed = matrix_to_transform(transform, tp)
                if len(js2transform(decomposed, tp)) <= len(js2transform([transform], tp)):
                    transforms[i : i + 1] = decomposed
                transform = transforms[i]
            round_transform(transform, tp)
            if (
                tp.short_translate
                and transform.name == "translate"
                and len(transform.data) == 2
                and not transform.data[1]
            ):
                transform.data.pop()
            if (
                tp.short_scale
                and transform.name == "scale"
                and len(transform.data) == 2
                and transform.data[0] == transform.data[1]
            ):
                transform.data.pop()
            if (
                tp.short_rotate
                and i >= 2
                and transforms[i - 2].name == "translate"
                and transforms[i - 1].name == "rotate"
                and transforms[i].name == "translate"
                and transforms[i - 2].data[0] == -transforms[i].data[0]
                and transforms[i - 2].data[1] == -transforms[i].data[1]
            ):
                transforms[i - 2 : i + 1] = [
                    TransformItem(
                        name="rotate",
                        data=[transforms[i - 1].data[0], transforms[i - 2].data[0], transforms[i - 2].data[1]],
                    )
                ]
                i -= 2
            i += 1
        return transforms

    def convert(item: Element, attr_name: str) -> None:
        data = transform2js(item.attributes[attr_name])
        tp = _define_precision(data, transform_params)
        if tp.collapse_into_one and len(data) > 1:
            data = [transforms_multiply(data)]
        if tp.convert_to_shorts:
            data = _convert_to_shorts(data, tp)
        else:
            for transform in data:
                round_transform(transform, tp)
        if tp.remove_useless:
            data = _remove_useless(data)
        if data:
            item.attributes[attr_name] = js2transform(data, tp)
        else:
            item.attributes.pop(attr_name, None)

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        for attr_name in ("transform", "gradientTransform", "patternTransform"):
            if element.attributes.get(attr_name) is not None:
                convert(element, attr_name)

    return {"element": {"enter": enter}}


def _remove_empty_attrs(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        for name, value in list(node.attributes.items()):
            if value == "" and name not in ATTRS_GROUPS["conditionalProcessing"]:
                del node.attributes[name]

    return {"element": {"enter": enter}}


def _remove_empty_containers(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    stylesheet = collect_stylesheet(root)
    removed_ids: set[str] = set()
    uses_by_id: dict[str, list[tuple[Element, Node]]] = {}

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        if node.name == "use":
            for name, value in node.attributes.items():
                for id_value in find_references(name, value):
                    uses_by_id.setdefault(id_value, []).append((node, parent))

    def exit(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        if element.name == "svg":
            return
        if element.name not in ELEMS_GROUPS["container"]:
            return
        if element.children:
            return
        if element.name == "pattern" and element.attributes:
            return
        if element.name == "mask" and element.attributes.get("id") is not None:
            return
        if parent.type == "element" and parent.name == "switch":
            return
        if element.name == "g" and (
            element.attributes.get("filter") is not None or compute_style(stylesheet, element).get("filter")
        ):
            return
        detach_node_from_parent(element, parent)
        if element.attributes.get("id"):
            removed_ids.add(element.attributes["id"])

    def exit_root() -> None:
        for id_value in removed_ids:
            for node, parent in uses_by_id.get(id_value, []):
                detach_node_from_parent(node, parent)

    return {
        "element": {"enter": enter, "exit": exit},
        "root": {"exit": lambda node, parent: exit_root()},
    }


def _merge_paths(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    force = params.get("force", False)
    float_precision = params.get("floatPrecision", 3)
    no_space_after_flags = params.get("noSpaceAfterFlags", False)
    stylesheet = collect_stylesheet(root)

    def element_has_url(computed_style: dict[str, dict[str, Any]], attr_name: str) -> bool:
        style = computed_style.get(attr_name)
        if style is not None and style["type"] == "static":
            return includes_url_reference(style.get("value", ""))
        return False

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element" or len(node.children) <= 1:
            return
        elements_to_remove: list[Node] = []
        prev_child: Node = node.children[0]
        prev_path_data: list[PathDataItem] | None = None

        def update_previous_path(child: Element, path_data: list[PathDataItem]) -> None:
            nonlocal prev_path_data
            js2path(child, path_data, float_precision=float_precision, no_space_after_flags=no_space_after_flags)
            prev_path_data = None

        for i in range(1, len(node.children)):
            child = node.children[i]
            if (
                not isinstance(prev_child, Element)
                or prev_child.name != "path"
                or prev_child.children
                or prev_child.attributes.get("d") is None
            ):
                if prev_path_data is not None and isinstance(prev_child, Element):
                    update_previous_path(prev_child, prev_path_data)
                prev_child = child
                continue
            if (
                not isinstance(child, Element)
                or child.name != "path"
                or child.children
                or child.attributes.get("d") is None
            ):
                if prev_path_data is not None:
                    update_previous_path(prev_child, prev_path_data)
                prev_child = child
                continue
            computed_style = compute_style(stylesheet, child)
            if (
                computed_style.get("marker-start")
                or computed_style.get("marker-mid")
                or computed_style.get("marker-end")
                or computed_style.get("clip-path")
                or computed_style.get("mask")
                or computed_style.get("mask-image")
                or any(element_has_url(computed_style, name) for name in ("fill", "filter", "stroke"))
            ):
                if prev_path_data is not None:
                    update_previous_path(prev_child, prev_path_data)
                prev_child = child
                continue
            child_attrs = list(child.attributes)
            if len(child_attrs) != len(prev_child.attributes):
                if prev_path_data is not None:
                    update_previous_path(prev_child, prev_path_data)
                prev_child = child
                continue
            attrs_equal = any(
                attr != "d" and prev_child.attributes.get(attr) != child.attributes.get(attr) for attr in child_attrs
            )
            if attrs_equal:
                if prev_path_data is not None:
                    update_previous_path(prev_child, prev_path_data)
                prev_child = child
                continue
            has_prev_path = prev_path_data is not None
            current_path_data = path2js(child)
            if prev_path_data is None:
                prev_path_data = path2js(prev_child)
            if force or not paths_intersect(prev_path_data, current_path_data):
                prev_path_data.extend(current_path_data)
                elements_to_remove.append(child)
                continue
            if has_prev_path:
                update_previous_path(prev_child, prev_path_data)
            prev_child = child
            prev_path_data = None
        if prev_path_data is not None and isinstance(prev_child, Element):
            update_previous_path(prev_child, prev_path_data)
        node.children = [c for c in node.children if c not in elements_to_remove]

    return {"element": {"enter": enter}}


def _remove_unused_ns(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    unused_namespaces: set[str] = set()

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        if node.name == "svg" and parent.type == "root":
            for name in node.attributes:
                if name.startswith("xmlns:"):
                    unused_namespaces.add(name[len("xmlns:") :])
        if unused_namespaces:
            if ":" in node.name:
                prefix = node.name.split(":", 1)[0]
                unused_namespaces.discard(prefix)
            for name in node.attributes:
                if ":" in name:
                    unused_namespaces.discard(name.split(":", 1)[0])

    def exit(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        if node.name == "svg" and parent.type == "root":
            for name in list(unused_namespaces):
                node.attributes.pop(f"xmlns:{name}", None)

    return {"element": {"enter": enter, "exit": exit}}


def _sort_attrs(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    order = list(
        params.get("order")
        or [
            "id",
            "width",
            "height",
            "x",
            "x1",
            "x2",
            "y",
            "y1",
            "y2",
            "cx",
            "cy",
            "r",
            "fill",
            "stroke",
            "marker",
            "d",
            "points",
        ]
    )
    xmlns_order = params.get("xmlnsOrder", "front")

    def get_ns_priority(name: str) -> int:
        if xmlns_order == "front":
            if name == "xmlns":
                return 3
            if name.startswith("xmlns:"):
                return 2
        if ":" in name:
            return 1
        return 0

    def compare(a: tuple[str, str], b: tuple[str, str]) -> int:
        a_name, b_name = a[0], b[0]
        priority_ns = get_ns_priority(b_name) - get_ns_priority(a_name)
        if priority_ns != 0:
            return priority_ns
        a_part = a_name.split("-", 1)[0]
        b_part = b_name.split("-", 1)[0]
        if a_part != b_part:
            a_in_order = a_part in order
            b_in_order = b_part in order
            if a_in_order and b_in_order:
                return order.index(a_part) - order.index(b_part)
            priority_order = (1 if b_in_order else 0) - (1 if a_in_order else 0)
            if priority_order != 0:
                return priority_order
        if a_name < b_name:
            return -1
        return 1

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        attrs = sorted(node.attributes.items(), key=functools.cmp_to_key(compare))
        node.attributes = dict(attrs)

    return {"element": {"enter": enter}}


def _sort_defs_children(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def compare(a: Node, b: Node) -> int:
        if not isinstance(a, Element) or not isinstance(b, Element):
            return 0
        a_frequency = frequencies.get(a.name)
        b_frequency = frequencies.get(b.name)
        if a_frequency is not None and b_frequency is not None:
            frequency_comparison = b_frequency - a_frequency
            if frequency_comparison != 0:
                return frequency_comparison
        length_comparison = len(b.name) - len(a.name)
        if length_comparison != 0:
            return length_comparison
        if a.name != b.name:
            return -1 if a.name > b.name else 1
        return 0

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element" or node.name != "defs":
            return
        frequencies: dict[str, int] = {}
        for child in node.children:
            if isinstance(child, Element):
                frequencies[child.name] = frequencies.get(child.name, 0) + 1
        node.children.sort(key=functools.cmp_to_key(compare))

    return {"element": {"enter": enter}}


_REG_STANDARD_DESCS = re.compile(r"^(Created with|Created using)")


def _remove_desc(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    remove_any = params.get("removeAny", False)

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element" or node.name != "desc":
            return
        if (
            remove_any
            or not node.children
            or (node.children[0].type == "text" and _REG_STANDARD_DESCS.match(node.children[0].value))
        ):
            detach_node_from_parent(node, parent)

    return {"element": {"enter": enter}}


def _remove_title(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type == "element" and node.name == "title":
            detach_node_from_parent(node, parent)

    return {"element": {"enter": enter}}


def _remove_style_element(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type == "element" and node.name == "style":
            detach_node_from_parent(node, parent)

    return {"element": {"enter": enter}}


def _remove_raster_images(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element" or node.name != "image":
            return
        href = node.attributes.get("xlink:href")
        if href is not None and re.search(r"(\.|image/)(jpe?g|png|gif)", href):
            detach_node_from_parent(node, parent)

    return {"element": {"enter": enter}}


def _remove_scripts(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    event_attrs = set(_HAS_SCRIPTS_EVENT_ATTRS)
    html_url_attrs = {"action", "data", "formaction", "href", "src"}
    prefixes: dict[str, list[str]] = {}
    foreign_object_depth = 0

    def is_namespace_aware_elem(elem: str, target: str, target_namespaces: Sequence[str]) -> bool:
        if elem == target:
            return True
        if ":" in elem:
            prefix, effective = elem.split(":", 1)
            if target == effective:
                namespaces = prefixes.get(prefix)
                namespace = namespaces[-1] if namespaces else None
                return namespace in target_namespaces
        return False

    def enter(node: Node, parent: Node) -> None:
        nonlocal foreign_object_depth
        if node.type != "element":
            return
        for key, value in node.attributes.items():
            if key.startswith("xmlns:"):
                prefix = key[6:]
                prefixes.setdefault(prefix, []).append(value)
        if is_namespace_aware_elem(node.name, "foreignObject", ("http://www.w3.org/2000/svg",)):
            foreign_object_depth += 1
        if is_namespace_aware_elem(
            node.name,
            "script",
            ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xhtml"),
        ):
            detach_node_from_parent(node, parent)
            return
        for attr, value in list(node.attributes.items()):
            local_attr = attr[attr.rfind(":") + 1 :].lower()
            is_event_attr = attr in event_attrs or (foreign_object_depth > 0 and local_attr.startswith("on"))
            is_embedded_document_attr = foreign_object_depth > 0 and local_attr == "srcdoc"
            is_executable_html_url = (
                foreign_object_depth > 0 and local_attr in html_url_attrs and is_executable_url(value)
            )
            if is_event_attr or is_embedded_document_attr or is_executable_html_url:
                del node.attributes[attr]

    def exit(node: Node, parent: Node) -> None:
        nonlocal foreign_object_depth
        if node.type != "element":
            return
        is_foreign_object = is_namespace_aware_elem(node.name, "foreignObject", ("http://www.w3.org/2000/svg",))
        is_anchor = is_namespace_aware_elem(node.name, "a", ("http://www.w3.org/2000/svg",))
        for key in list(node.attributes):
            if key.startswith("xmlns:"):
                prefix = key[6:]
                stack = prefixes.get(prefix)
                if stack:
                    stack.pop()
        if is_anchor:
            for attr in list(node.attributes):
                if attr == "href" or attr.endswith(":href"):
                    value = node.attributes.get(attr)
                    if value is None or not is_executable_url(value):
                        continue
                    index = parent.children.index(node)
                    useful_children = [c for c in node.children if c.type != "text"]
                    parent.children[index : index + 1] = useful_children
        if is_foreign_object:
            foreign_object_depth -= 1

    return {"element": {"enter": enter, "exit": exit}}


def _remove_view_box(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        if node.name not in ("pattern", "svg", "symbol"):
            return
        if (
            node.attributes.get("viewBox") is None
            or node.attributes.get("width") is None
            or node.attributes.get("height") is None
        ):
            return
        if node.name == "svg" and parent.type != "root":
            return
        numbers = re.split(r"[ ,]+", node.attributes["viewBox"])
        if (
            len(numbers) >= 4
            and numbers[0] == "0"
            and numbers[1] == "0"
            and re.sub(r"px$", "", node.attributes["width"]) == numbers[2]
            and re.sub(r"px$", "", node.attributes["height"]) == numbers[3]
        ):
            del node.attributes["viewBox"]

    return {"element": {"enter": enter}}


def _remove_dimensions(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element" or node.name != "svg":
            return
        if node.attributes.get("viewBox") is not None:
            node.attributes.pop("width", None)
            node.attributes.pop("height", None)
        elif node.attributes.get("width") is not None and node.attributes.get("height") is not None:
            width = js_parse_float(node.attributes["width"])
            height = js_parse_float(node.attributes["height"])
            if width is None or height is None or width != width or height != height:
                return
            node.attributes["viewBox"] = f"0 0 {js_number_string(width)} {js_number_string(height)}"
            node.attributes.pop("width", None)
            node.attributes.pop("height", None)

    return {"element": {"enter": enter}}


def _remove_xmlns(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:

    def enter(node: Node, parent: Node) -> None:
        if node.type == "element" and node.name == "svg":
            node.attributes.pop("xmlns", None)

    return {"element": {"enter": enter}}


_ADD_ATTRIBUTES_ENOCLS = (
    'Error in plugin "addAttributesToSVGElement": absent parameters.\n'
    'It should have a list of "attributes" or one "attribute".\n'
    "Config example:\n"
    "\n"
    "plugins: [\n"
    "  {\n"
    '    name: "addAttributesToSVGElement",\n'
    "    params: {\n"
    '      attribute: "mySvg"\n'
    "    }\n"
    "  }\n"
    "]\n"
    "\n"
    "plugins: [\n"
    "  {\n"
    '    name: "addAttributesToSVGElement",\n'
    "    params: {\n"
    '      attributes: ["mySvg", "size-big"]\n'
    "    }\n"
    "  }\n"
    "]\n"
    "\n"
    "plugins: [\n"
    "  {\n"
    '    name: "addAttributesToSVGElement",\n'
    "    params: {\n"
    "      attributes: [\n"
    "        {\n"
    "          focusable: false\n"
    "        },\n"
    "        {\n"
    "          'data-image': icon\n"
    "        }\n"
    "      ]\n"
    "    }\n"
    "  }\n"
    "]\n"
)


def _add_attributes_to_svg_element(
    root: Document, params: dict[str, Any], info: dict[str, Any]
) -> dict[str, Any] | None:
    attributes = params.get("attributes")
    attribute = params.get("attribute")
    if not isinstance(attributes, list) and not attribute:
        sys.stderr.write(_ADD_ATTRIBUTES_ENOCLS)
        return None
    attribute_list = attributes if isinstance(attributes, list) else [attribute]

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element" or node.name != "svg":
            return
        if parent.type != "root":
            return
        for item in attribute_list:
            if isinstance(item, str):
                if item not in node.attributes:
                    node.attributes[item] = ""
            elif isinstance(item, Mapping):
                for key, value in item.items():
                    if key not in node.attributes:
                        node.attributes[key] = "" if value is None else str(value)

    return {"element": {"enter": enter}}


_ADD_CLASSES_ENOCLS = (
    'Error in plugin "addClassesToSVGElement": absent parameters.\n'
    'It should have a list of classes in "classNames" or one "className".\n'
    "Config example:\n"
    "\n"
    "plugins: [\n"
    "  {\n"
    '    name: "addClassesToSVGElement",\n'
    "    params: {\n"
    '      className: "mySvg"\n'
    "    }\n"
    "  }\n"
    "]\n"
    "\n"
    "plugins: [\n"
    "  {\n"
    '    name: "addClassesToSVGElement",\n'
    "    params: {\n"
    '      classNames: ["mySvg", "size-big"]\n'
    "    }\n"
    "  }\n"
    "]\n"
)


def _add_classes_to_svg_element(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any] | None:
    class_names = params.get("classNames")
    class_name = params.get("className")
    if not (isinstance(class_names, list) and class_names) and not class_name:
        sys.stderr.write(_ADD_CLASSES_ENOCLS)
        return None
    names = class_names if isinstance(class_names, list) else [class_name]

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element" or node.name != "svg":
            return
        if parent.type != "root":
            return
        class_list = [c for c in (node.attributes.get("class") or "").split(" ") if c]
        for name in names:
            if name is not None and name not in class_list:
                class_list.append(str(name))
        node.attributes["class"] = " ".join(class_list)

    return {"element": {"enter": enter}}


_REG_NUMERIC_LIST_VALUE = re.compile(r"^([-+]?\d*\.?\d+([eE][-+]?\d+)?)(px|pt|pc|mm|cm|m|in|ft|em|ex|%)?$")
_REG_LIST_SEPARATOR = re.compile(r"\s+,?\s*|,\s*")


def _cleanup_list_of_values(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    float_precision = params.get("floatPrecision", 3)
    leading_zero = params.get("leadingZero", True)
    default_px = params.get("defaultPx", True)
    convert_to_px = params.get("convertToPx", True)

    def round_values(value_list: str) -> str:
        rounded: list[str] = []
        for elem in _REG_LIST_SEPARATOR.split(value_list):
            if not elem:
                continue
            match = _REG_NUMERIC_LIST_VALUE.match(elem)
            if match is not None:
                num = js_to_fixed(float(match.group(1)), float_precision)
                units = match.group(3) or ""
                if convert_to_px and units and units in _ABSOLUTE_LENGTHS:
                    px_num = js_to_fixed(
                        _ABSOLUTE_LENGTHS[units] * float(match.group(1)),
                        float_precision,
                    )
                    if len(js_number_string(px_num)) < len(elem):
                        num = px_num
                        units = "px"
                text = remove_leading_zero(num) if leading_zero else js_number_string(num)
                if default_px and units == "px":
                    units = ""
                rounded.append(text + units)
            elif "new" in elem:
                rounded.append("new")
            else:
                rounded.append(elem)
        return " ".join(rounded)

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        for name in ("points", "enable-background", "viewBox", "stroke-dasharray", "dx", "dy", "x", "y"):
            if node.attributes.get(name) is not None:
                node.attributes[name] = round_values(node.attributes[name])

    return {"element": {"enter": enter}}


def _convert_one_stop_gradients(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    stylesheet = collect_stylesheet(root)
    effected_defs: set[Element] = set()
    all_defs: list[tuple[Element, Node]] = []
    gradients_to_detach: list[tuple[Element, Node]] = []
    xlink_href_count = 0

    def enter(node: Node, parent: Node) -> None:
        nonlocal xlink_href_count
        if node.type != "element":
            return
        element = node
        if element.attributes.get("xlink:href") is not None:
            xlink_href_count += 1
        if element.name == "defs":
            all_defs.append((element, parent))
            return
        if element.name not in ("linearGradient", "radialGradient"):
            return
        stops = [c for c in element.children if isinstance(c, Element) and c.name == "stop"]
        href = element.attributes.get("xlink:href") or element.attributes.get("href")
        effective_node = element
        if not stops and href is not None and href.startswith("#"):
            found = query_selector(root, href)
            if found is not None:
                effective_node = found
        if effective_node is element and not stops:
            gradients_to_detach.append((element, parent))
            return
        effective_stops = [c for c in effective_node.children if isinstance(c, Element) and c.name == "stop"]
        if len(effective_stops) != 1:
            return
        if isinstance(parent, Element) and parent.name == "defs":
            effected_defs.add(parent)
        gradients_to_detach.append((element, parent))
        stop_style = compute_style(stylesheet, effective_stops[0]).get("stop-color")
        color = None
        if stop_style is not None and stop_style["type"] == "static":
            color = stop_style.get("value")
        selector_val = f"url(#{element.attributes.get('id')})"
        for attr in COLORS_PROPS:
            selector = f'[{attr}="{selector_val}"]'
            for matched in query_selector_all(root, selector):
                if matched.attributes.get(attr) == selector_val:
                    if color is not None:
                        matched.attributes[attr] = color
                    else:
                        matched.attributes.pop(attr, None)
        for matched in query_selector_all(root, f"[style*={selector_val}]"):
            style = matched.attributes.get("style") or ""
            matched.attributes["style"] = style.replace(
                selector_val,
                color or ATTRS_GROUPS_DEFAULTS["presentation"]["stop-color"],
            )

    def exit(node: Node, parent: Node) -> None:
        nonlocal xlink_href_count
        if node.type != "element" or node.name != "svg":
            return
        for gradient, gradient_parent in gradients_to_detach:
            if gradient.attributes.get("xlink:href") is not None:
                xlink_href_count -= 1
            detach_node_from_parent(gradient, gradient_parent)
        if xlink_href_count == 0:
            node.attributes.pop("xmlns:xlink", None)
        for defs, defs_parent in all_defs:
            if defs in effected_defs and not defs.children:
                detach_node_from_parent(defs, defs_parent)

    return {"element": {"enter": enter, "exit": exit}}


def _convert_style_to_attrs(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    keep_important = params.get("keepImportant", False)
    styling_props = ATTRS_GROUPS["presentation"]

    reg_declaration = re.compile(r"\s*([A-Za-z_:][-A-Za-z0-9_:.]*)\s*:\s*" r"([^;]*?)\s*(\s*!important\s*)?(;|$)")

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        style = element.attributes.get("style")
        if style is None:
            return
        styles: list[tuple[str, str]] = []
        new_attributes: dict[str, str] = {}
        for match in reg_declaration.finditer(style):
            name, value, important = (match.group(1), match.group(2), match.group(3))
            if not keep_important or not important:
                styles.append((name, value))
        if styles:
            remaining: list[tuple[str, str]] = []
            for name, value in styles:
                prop = name.lower()
                val = value.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if prop in styling_props:
                    new_attributes[prop] = val
                else:
                    remaining.append((name, value))
            element.attributes.update(new_attributes)
            if remaining:
                element.attributes["style"] = ";".join(f"{name}:{value}" for name, value in remaining)
            else:
                del element.attributes["style"]

    return {"element": {"enter": enter}}


def _prefix_ids(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    delim = params.get("delim", "__")
    prefix = params.get("prefix")
    prefix_ids_enabled = params.get("prefixIds", True)
    prefix_class_names = params.get("prefixClassNames", True)
    prefix_map: dict[str, str] = {}

    def get_basename(path: str) -> str:
        matched = re.search(r"[/\\]?([^/\\]+)$", path)
        return matched.group(1) if matched else ""

    def escape_identifier_name(value: str) -> str:
        return re.sub(r"[. ]", "_", value)

    def generate_prefix(body: str) -> str:
        if isinstance(prefix, str):
            return prefix + delim
        if prefix is False:
            return ""
        if prefix is True:
            path = info.get("path")
            if path:
                return escape_identifier_name(get_basename(path)) + delim
            return "prefix" + delim
        return "prefix" + delim

    def prefix_id(body: str) -> str:
        generated = prefix_map.get(body)
        if generated is None:
            generated = generate_prefix(body)
            prefix_map[body] = generated
        if body.startswith(generated):
            return body
        return generated + body

    def prefix_reference(reference: str) -> str | None:
        if reference.startswith("#"):
            return "#" + prefix_id(reference[1:])
        return None

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        if element.name == "style" and element.children:
            for child in element.children:
                if child.type not in ("text", "cdata"):
                    continue
                text = child.value
                if prefix_ids_enabled:
                    text = re.sub(r"#([\w-]+)", lambda m: "#" + prefix_id(m.group(1)), text)
                if prefix_class_names:
                    text = re.sub(r"\.([\w-]+)", lambda m: "." + prefix_id(m.group(1)), text)
                child.value = text
        if prefix_ids_enabled and element.attributes.get("id") and element.attributes["id"]:
            element.attributes["id"] = prefix_id(element.attributes["id"])
        if prefix_class_names and element.attributes.get("class") and element.attributes["class"]:
            element.attributes["class"] = " ".join(
                prefix_id(name) for name in re.split(r"\s+", element.attributes["class"])
            )
        for name in ("href", "xlink:href"):
            value = element.attributes.get(name)
            if value:
                prefixed = prefix_reference(value)
                if prefixed is not None:
                    element.attributes[name] = prefixed
        for name in REFERENCES_PROPS:
            value = element.attributes.get(name)
            if value:
                element.attributes[name] = re.sub(
                    r"\burl\((['\"]?)(#.+?)\1\)",
                    lambda m: "url(" + (prefix_reference(m.group(2)) or m.group(2)) + ")",
                    value,
                    flags=re.I,
                )
        for name in ("begin", "end"):
            value = element.attributes.get(name)
            if value:
                parts = re.split(r"\s*;\s+", value)
                rebuilt = []
                for part in parts:
                    if part.endswith(".end") or part.endswith(".start"):
                        identifier, _, postfix = part.partition(".")
                        rebuilt.append(f"{prefix_id(identifier)}.{postfix}")
                    else:
                        rebuilt.append(part)
                element.attributes[name] = "; ".join(rebuilt)

    return {"element": {"enter": enter}}


def _remove_attributes_by_selector(
    root: Document, params: dict[str, Any], info: dict[str, Any]
) -> dict[str, Any] | None:
    selectors = params.get("selectors")
    single_selector = params.get("selector")
    attributes = params.get("attributes")
    if not isinstance(selectors, list) and (not single_selector or not attributes):
        sys.stderr.write(
            'Warning: The plugin "removeAttributesBySelector" is missing'
            ' parameters.\nIt should have a list of "selectors", or one'
            ' "selector" and one "attributes".\nWithout either, the plugin'
            " is a noop.\n"
        )
        return None
    selector_list = (
        selectors if isinstance(selectors, list) else [{"selector": single_selector, "attributes": attributes}]
    )
    for item in selector_list:
        nodes = query_selector_all(root, item["selector"])
        attribute_list = item["attributes"]
        if isinstance(attribute_list, str):
            attribute_list = [attribute_list]
        for node in nodes:
            for name in attribute_list:
                node.attributes.pop(name, None)
    return {}


def _remove_attrs(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any] | None:
    if "attrs" not in params:
        sys.stderr.write(
            'Warning: The plugin "removeAttrs" requires the "attrs"'
            " parameter.\nIt should have a pattern to remove, otherwise the"
            " plugin is a noop.\nConfig example:\n\nplugins: [\n  {\n"
            '    name: "removeAttrs",\n    params: {\n'
            '      attrs: "(fill|stroke)"\n    }\n  }\n]\n'
        )
        return None
    elem_separator = params.get("elemSeparator")
    if not isinstance(elem_separator, str):
        elem_separator = ":"
    preserve_current_color = bool(params.get("preserveCurrentColor", False))
    attrs = params["attrs"]
    if not isinstance(attrs, list):
        attrs = [attrs]

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        for pattern in attrs:
            if not isinstance(pattern, str):
                continue
            if elem_separator not in pattern:
                pattern = elem_separator.join([".*", pattern, ".*"])
            elif len(pattern.split(elem_separator)) < 3:
                pattern = elem_separator.join([pattern, ".*"])
            pieces = pattern.split(elem_separator)
            regexes = []
            for value in pieces:
                if value == "*":
                    value = ".*"
                regexes.append(re.compile(f"^{value}$", re.I))
            if len(regexes) < 3:
                continue
            if regexes[0].search(node.name):
                for name, value in list(node.attributes.items()):
                    is_current_color = value.lower() == "currentcolor"
                    is_fill_current_color = preserve_current_color and name == "fill" and is_current_color
                    is_stroke_current_color = preserve_current_color and name == "stroke" and is_current_color
                    if (
                        not is_fill_current_color
                        and not is_stroke_current_color
                        and regexes[1].search(name)
                        and regexes[2].search(value)
                    ):
                        del node.attributes[name]

    return {"element": {"enter": enter}}


def _remove_elements_by_attr(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    ids = params.get("id")
    classes = params.get("class")
    id_list = ids if isinstance(ids, list) else ([ids] if ids else [])
    class_list = classes if isinstance(classes, list) else ([classes] if classes else [])

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        if node.attributes.get("id") is not None and id_list:
            if node.attributes["id"] in id_list:
                detach_node_from_parent(node, parent)
        if node.attributes.get("class") and class_list:
            node_classes = node.attributes["class"].split(" ")
            for item in class_list:
                if item in node_classes:
                    detach_node_from_parent(node, parent)
                    break

    return {"element": {"enter": enter}}


def _remove_off_canvas_paths(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    view_box_data: dict[str, float] | None = None

    def enter(node: Node, parent: Node) -> Any:
        nonlocal view_box_data
        if node.type != "element":
            return None
        element = node
        if element.name == "svg" and parent.type == "root":
            view_box = element.attributes.get("viewBox")
            if view_box is None:
                if element.attributes.get("height") is not None and element.attributes.get("width") is not None:
                    view_box = f"0 0 {element.attributes['width']} {element.attributes['height']}"
            if view_box is None:
                view_box = ""
            view_box = re.sub(r"[,+]|px", " ", view_box)
            view_box = re.sub(r"\s+", " ", view_box).strip()
            match = re.match(
                r"(-?\d*\.?\d+) (-?\d*\.?\d+) (\d*\.?\d+) (\d*\.?\d+)",
                view_box,
            )
            if match is None:
                return None
            left = float(match.group(1))
            top = float(match.group(2))
            width = float(match.group(3))
            height = float(match.group(4))
            view_box_data = {
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "width": width,
                "height": height,
            }
        if element.attributes.get("transform") is not None:
            return VISIT_SKIP
        if element.name == "path" and element.attributes.get("d") is not None and view_box_data is not None:
            path_data = parse_path_data(element.attributes["d"])
            visible = False
            for path_data_item in path_data:
                if path_data_item.command == "M":
                    x, y = path_data_item.args[0], path_data_item.args[1]
                    if (
                        view_box_data["left"] <= x <= view_box_data["right"]
                        and view_box_data["top"] <= y <= view_box_data["bottom"]
                    ):
                        visible = True
            if visible:
                return None
            if len(path_data) == 2:
                path_data.append(PathDataItem(command="z", args=[]))
            left = view_box_data["left"]
            top = view_box_data["top"]
            view_box_path_data = [
                PathDataItem(command="M", args=[left, top]),
                PathDataItem(command="h", args=[view_box_data["width"]]),
                PathDataItem(command="v", args=[view_box_data["height"]]),
                PathDataItem(command="H", args=[left]),
                PathDataItem(command="z", args=[]),
            ]
            if not paths_intersect(view_box_path_data, path_data):
                detach_node_from_parent(element, parent)
        return None

    return {"element": {"enter": enter}}


def _remove_xlink(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    include_legacy = params.get("includeLegacy")
    xlink_namespace = "http://www.w3.org/1999/xlink"
    show_to_target = {"new": "_blank", "replace": "_self"}
    legacy_elements = {"cursor", "filter", "font-face-uri", "glyphRef", "tref"}
    xlink_prefixes: list[str] = []
    overridden_prefixes: list[str] = []
    used_in_legacy_element: list[str] = []

    def find_prefixed_attrs(element: Element, attr: str) -> list[str]:
        return [f"{prefix}:{attr}" for prefix in xlink_prefixes if f"{prefix}:{attr}" in element.attributes]

    def enter(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        for key, value in element.attributes.items():
            if key.startswith("xmlns:"):
                prefix = key.split(":", 2)[1]
                if value == xlink_namespace:
                    if prefix not in xlink_prefixes:
                        xlink_prefixes.append(prefix)
                elif prefix in xlink_prefixes:
                    overridden_prefixes.append(prefix)
        if any(prefix in xlink_prefixes for prefix in overridden_prefixes):
            return
        show_attrs = find_prefixed_attrs(element, "show")
        show_handled = "target" in element.attributes
        for attr in reversed(show_attrs):
            value = element.attributes.get(attr)
            mapping = show_to_target.get(value or "")
            if show_handled or mapping is None:
                element.attributes.pop(attr, None)
                continue
            elem_config = ELEMS.get(element.name, {})
            default_target = elem_config.get("defaults", {}).get("target")
            if mapping != default_target:
                element.attributes["target"] = mapping
            element.attributes.pop(attr, None)
            show_handled = True
        title_attrs = find_prefixed_attrs(element, "title")
        for attr in reversed(title_attrs):
            value = element.attributes.get(attr)
            has_title = any(isinstance(child, Element) and child.name == "title" for child in element.children)
            if has_title:
                element.attributes.pop(attr, None)
                continue
            title_tag = Element("title")
            title_tag.children.append(TextNode(value or ""))
            element.children.insert(0, title_tag)
            element.attributes.pop(attr, None)
        href_attrs = find_prefixed_attrs(element, "href")
        if href_attrs and element.name in legacy_elements and not include_legacy:
            for attr in href_attrs:
                prefix = attr.split(":", 1)[0]
                if prefix not in used_in_legacy_element:
                    used_in_legacy_element.append(prefix)
            return
        for attr in reversed(href_attrs):
            value = element.attributes.get(attr)
            if "href" in element.attributes:
                element.attributes.pop(attr, None)
                continue
            element.attributes["href"] = value or ""
            element.attributes.pop(attr, None)

    def exit(node: Node, parent: Node) -> None:
        if node.type != "element":
            return
        element = node
        for key, value in list(element.attributes.items()):
            parts = key.split(":", 2)
            if len(parts) == 2:
                prefix, attr = parts
            else:
                prefix, attr = key, ""
            if (
                prefix in xlink_prefixes
                and prefix not in overridden_prefixes
                and prefix not in used_in_legacy_element
                and not include_legacy
            ):
                element.attributes.pop(key, None)
                continue
            if key.startswith("xmlns:") and attr not in used_in_legacy_element:
                if value == xlink_namespace:
                    if attr in xlink_prefixes:
                        xlink_prefixes.remove(attr)
                    element.attributes.pop(key, None)
                    continue
                if prefix in overridden_prefixes:
                    if attr in overridden_prefixes:
                        overridden_prefixes.remove(attr)

    return {"element": {"enter": enter, "exit": exit}}


def _reuse_paths(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    stylesheet = collect_stylesheet(root)
    paths: dict[str, list[Element]] = {}
    svg_defs: Element | None = None
    hrefs: set[str] = set()

    def enter(node: Node, parent: Node) -> None:
        nonlocal svg_defs
        if node.type != "element":
            return
        element = node
        if element.name == "path" and element.attributes.get("d") is not None:
            d = element.attributes["d"]
            fill = element.attributes.get("fill", "")
            stroke = element.attributes.get("stroke", "")
            key = d + ";s:" + stroke + ";f:" + fill
            paths.setdefault(key, []).append(element)
        if svg_defs is None and element.name == "defs" and isinstance(parent, Element) and parent.name == "svg":
            svg_defs = element
        if element.name == "use":
            for name in ("href", "xlink:href"):
                href = element.attributes.get(name)
                if href and href.startswith("#") and len(href) > 1:
                    hrefs.add(href[1:])

    def exit(node: Node, parent: Node) -> None:
        nonlocal svg_defs
        if node.type != "element" or node.name != "svg":
            return
        if parent is None or parent.type != "root":
            return
        defs_tag = svg_defs
        if defs_tag is None:
            defs_tag = Element("defs")
        index = 0
        for path_list in paths.values():
            if len(path_list) <= 1:
                continue
            reusable_path = Element("path")
            for attr in ("fill", "stroke", "d"):
                if path_list[0].attributes.get(attr) is not None:
                    reusable_path.attributes[attr] = path_list[0].attributes[attr]
            original_id = path_list[0].attributes.get("id")
            if (
                original_id is None
                or original_id in hrefs
                or any(rule.selector == f"#{original_id}" for rule in stylesheet.rules)
            ):
                reusable_path.attributes["id"] = f"reuse-{index}"
                index += 1
            else:
                reusable_path.attributes["id"] = original_id
                del path_list[0].attributes["id"]
            defs_tag.children.append(reusable_path)
            for path_node in path_list:
                path_node.attributes.pop("d", None)
                path_node.attributes.pop("stroke", None)
                path_node.attributes.pop("fill", None)
                if path_node in defs_tag.children and not path_node.children:
                    if not path_node.attributes:
                        detach_node_from_parent(path_node, defs_tag)
                        continue
                    if len(path_node.attributes) == 1 and "id" in path_node.attributes:
                        detach_node_from_parent(path_node, defs_tag)
                        old_id = path_node.attributes["id"]
                        for child in query_selector_all(node, f"[xlink\\:href=#{old_id}], [href=#{old_id}]"):
                            for name in ("href", "xlink:href"):
                                if name in child.attributes:
                                    child.attributes[name] = "#" + reusable_path.attributes["id"]
                        continue
                path_node.name = "use"
                path_node.attributes["xlink:href"] = "#" + reusable_path.attributes["id"]
        if defs_tag.children:
            if node.attributes.get("xmlns:xlink") is None:
                node.attributes["xmlns:xlink"] = "http://www.w3.org/1999/xlink"
            if svg_defs is None:
                node.children.insert(0, defs_tag)

    return {"element": {"enter": enter, "exit": exit}}


def _noop_plugin(root: Document, params: dict[str, Any], info: dict[str, Any]) -> dict[str, Any] | None:
    return None


def _invoke_plugins(
    ast: Document,
    info: dict[str, Any],
    plugins: Sequence[PluginConfig],
    overrides: Mapping[str, Any] | None,
    global_overrides: Mapping[str, Any] | None,
) -> None:
    for plugin in plugins:
        override = (overrides or {}).get(plugin.name)
        if override is False:
            continue
        params: dict[str, Any] = dict(plugin.params)
        if global_overrides:
            params.update(global_overrides)
        if isinstance(override, Mapping):
            params.update(override)
        visitor = plugin.fn(ast, params, info)
        if visitor is not None:
            visit(ast, visitor)


def _make_preset(name: str, plugins: Sequence[BuiltinPlugin]) -> BuiltinPlugin:

    def preset_fn(root: Document, params: dict[str, Any], info: dict[str, Any]) -> None:
        float_precision = params.get("floatPrecision")
        overrides = params.get("overrides")
        global_overrides: dict[str, Any] = {}
        if float_precision is not None:
            global_overrides["floatPrecision"] = float_precision
        if isinstance(overrides, Mapping):
            plugin_names = [plugin.name for plugin in plugins]
            for plugin_name in overrides:
                if plugin_name not in plugin_names:
                    sys.stderr.write(
                        f"You are trying to configure {plugin_name} which is"
                        f" not part of {name}.\n"
                        "Try to put it before or after, for example\n\n"
                        "plugins: [\n"
                        "  {\n"
                        f"    name: '{name}',\n"
                        "  },\n"
                        f"  '{plugin_name}'\n"
                        "]\n"
                    )
        plugin_configs = [PluginConfig(name=plugin.name, params={}, fn=plugin.fn) for plugin in plugins]
        _invoke_plugins(ast := root, info, plugin_configs, overrides, global_overrides)

    return BuiltinPlugin(name=name, description="", fn=preset_fn, is_preset=True, plugins=tuple(plugins))


_PRESET_DEFAULT_PLUGINS: tuple[BuiltinPlugin, ...] = (
    BuiltinPlugin(name="removeDoctype", description="removes doctype declaration", fn=_remove_doctype),
    BuiltinPlugin(
        name="removeXMLProcInst", description="removes XML processing instructions", fn=_remove_xml_proc_inst
    ),
    BuiltinPlugin(name="removeComments", description="removes comments", fn=_remove_comments),
    BuiltinPlugin(
        name="removeDeprecatedAttrs", description="removes deprecated attributes", fn=_remove_deprecated_attrs
    ),
    BuiltinPlugin(name="removeMetadata", description="removes <metadata>", fn=_remove_metadata),
    BuiltinPlugin(
        name="removeEditorsNSData",
        description="removes editors namespaces, elements and attributes",
        fn=_remove_editors_ns_data,
    ),
    BuiltinPlugin(
        name="cleanupAttrs",
        description="cleanups attributes from newlines, trailing and repeating spaces",
        fn=_cleanup_attrs,
    ),
    BuiltinPlugin(name="mergeStyles", description="merge multiple style elements into one", fn=_merge_styles),
    BuiltinPlugin(name="inlineStyles", description="inline styles (additional options)", fn=_inline_styles),
    BuiltinPlugin(name="minifyStyles", description="minifies styles and removes unused styles", fn=_minify_styles),
    BuiltinPlugin(name="cleanupIds", description="removes unused IDs and minifies used", fn=_cleanup_ids),
    BuiltinPlugin(
        name="removeUselessDefs", description="removes elements in <defs> without id", fn=_remove_useless_defs
    ),
    BuiltinPlugin(
        name="cleanupNumericValues",
        description='rounds numeric values to the fixed precision, removes default "px" units',
        fn=_cleanup_numeric_values,
    ),
    BuiltinPlugin(
        name="convertColors", description="converts colors: rgb() to #rrggbb and #rrggbb to #rgb", fn=_convert_colors
    ),
    BuiltinPlugin(
        name="removeUnknownsAndDefaults",
        description="removes unknown elements content and attributes, removes attrs with default values",
        fn=_remove_unknowns_and_defaults,
    ),
    BuiltinPlugin(
        name="removeNonInheritableGroupAttrs",
        description="removes non-inheritable group's presentational attributes",
        fn=_remove_non_inheritable_group_attrs,
    ),
    BuiltinPlugin(
        name="removeUselessStrokeAndFill",
        description="removes useless stroke and fill attributes",
        fn=_remove_useless_stroke_and_fill,
    ),
    BuiltinPlugin(
        name="cleanupEnableBackground",
        description="remove or cleanup enable-background attribute when possible",
        fn=_cleanup_enable_background,
    ),
    BuiltinPlugin(
        name="removeHiddenElems",
        description="removes hidden elements (zero sized, with absent attributes)",
        fn=_remove_hidden_elems,
    ),
    BuiltinPlugin(name="removeEmptyText", description="removes empty <text> elements", fn=_remove_empty_text),
    BuiltinPlugin(
        name="convertShapeToPath",
        description="converts basic shapes to more compact path form",
        fn=_convert_shape_to_path,
    ),
    BuiltinPlugin(
        name="convertEllipseToCircle",
        description="converts non-eccentric <ellipse>s to <circle>s",
        fn=_convert_ellipse_to_circle,
    ),
    BuiltinPlugin(
        name="moveElemsAttrsToGroup",
        description="Move common attributes of group children to the group",
        fn=_move_elems_attrs_to_group,
    ),
    BuiltinPlugin(
        name="moveGroupAttrsToElems",
        description="moves some group attributes to the content elements",
        fn=_move_group_attrs_to_elems,
    ),
    BuiltinPlugin(name="collapseGroups", description="collapses useless groups", fn=_collapse_groups),
    BuiltinPlugin(
        name="convertPathData",
        description="optimizes path data: writes in shorter form, applies transformations",
        fn=_convert_path_data,
    ),
    BuiltinPlugin(
        name="convertTransform",
        description="collapses multiple transformations and optimizes it",
        fn=_convert_transform,
    ),
    BuiltinPlugin(name="removeEmptyAttrs", description="removes empty attributes", fn=_remove_empty_attrs),
    BuiltinPlugin(
        name="removeEmptyContainers", description="removes empty container elements", fn=_remove_empty_containers
    ),
    BuiltinPlugin(name="mergePaths", description="merges multiple paths in one if possible", fn=_merge_paths),
    BuiltinPlugin(name="removeUnusedNS", description="removes unused namespaces declaration", fn=_remove_unused_ns),
    BuiltinPlugin(name="sortAttrs", description="Sort element attributes for better compression", fn=_sort_attrs),
    BuiltinPlugin(
        name="sortDefsChildren", description="Sorts children of <defs> to improve compression", fn=_sort_defs_children
    ),
    BuiltinPlugin(name="removeDesc", description="removes <desc>", fn=_remove_desc),
)

_PRESET_DEFAULT: BuiltinPlugin = _make_preset("preset-default", _PRESET_DEFAULT_PLUGINS)

#: All builtin plugins in the reference registration order (used by
#: ``--show-plugins`` and by name lookup during config resolution).
BUILTIN_PLUGINS: tuple[BuiltinPlugin, ...] = (
    _PRESET_DEFAULT,
    BuiltinPlugin(
        name="addAttributesToSVGElement",
        description="adds attributes to an outer <svg> element",
        fn=_add_attributes_to_svg_element,
    ),
    BuiltinPlugin(
        name="addClassesToSVGElement",
        description="adds classnames to an outer <svg> element",
        fn=_add_classes_to_svg_element,
    ),
    BuiltinPlugin(
        name="cleanupAttrs",
        description="cleanups attributes from newlines, trailing and repeating spaces",
        fn=_cleanup_attrs,
    ),
    BuiltinPlugin(
        name="cleanupEnableBackground",
        description="remove or cleanup enable-background attribute when possible",
        fn=_cleanup_enable_background,
    ),
    BuiltinPlugin(name="cleanupIds", description="removes unused IDs and minifies used", fn=_cleanup_ids),
    BuiltinPlugin(
        name="cleanupListOfValues",
        description="rounds list of values to the fixed precision",
        fn=_cleanup_list_of_values,
    ),
    BuiltinPlugin(
        name="cleanupNumericValues",
        description='rounds numeric values to the fixed precision, removes default "px" units',
        fn=_cleanup_numeric_values,
    ),
    BuiltinPlugin(name="collapseGroups", description="collapses useless groups", fn=_collapse_groups),
    BuiltinPlugin(
        name="convertColors", description="converts colors: rgb() to #rrggbb and #rrggbb to #rgb", fn=_convert_colors
    ),
    BuiltinPlugin(
        name="convertEllipseToCircle",
        description="converts non-eccentric <ellipse>s to <circle>s",
        fn=_convert_ellipse_to_circle,
    ),
    BuiltinPlugin(
        name="convertOneStopGradients",
        description="converts one-stop (single color) gradients to a plain color",
        fn=_convert_one_stop_gradients,
    ),
    BuiltinPlugin(
        name="convertPathData",
        description="optimizes path data: writes in shorter form, applies transformations",
        fn=_convert_path_data,
    ),
    BuiltinPlugin(
        name="convertShapeToPath",
        description="converts basic shapes to more compact path form",
        fn=_convert_shape_to_path,
    ),
    BuiltinPlugin(name="convertStyleToAttrs", description="converts style to attributes", fn=_convert_style_to_attrs),
    BuiltinPlugin(
        name="convertTransform",
        description="collapses multiple transformations and optimizes it",
        fn=_convert_transform,
    ),
    BuiltinPlugin(name="inlineStyles", description="inline styles (additional options)", fn=_inline_styles),
    BuiltinPlugin(name="mergePaths", description="merges multiple paths in one if possible", fn=_merge_paths),
    BuiltinPlugin(name="mergeStyles", description="merge multiple style elements into one", fn=_merge_styles),
    BuiltinPlugin(name="minifyStyles", description="minifies styles and removes unused styles", fn=_minify_styles),
    BuiltinPlugin(
        name="moveElemsAttrsToGroup",
        description="Move common attributes of group children to the group",
        fn=_move_elems_attrs_to_group,
    ),
    BuiltinPlugin(
        name="moveGroupAttrsToElems",
        description="moves some group attributes to the content elements",
        fn=_move_group_attrs_to_elems,
    ),
    BuiltinPlugin(name="prefixIds", description="prefix IDs", fn=_prefix_ids),
    BuiltinPlugin(
        name="removeAttributesBySelector",
        description="removes attributes of elements that match a css selector",
        fn=_remove_attributes_by_selector,
    ),
    BuiltinPlugin(name="removeAttrs", description="removes specified attributes", fn=_remove_attrs),
    BuiltinPlugin(name="removeComments", description="removes comments", fn=_remove_comments),
    BuiltinPlugin(
        name="removeDeprecatedAttrs", description="removes deprecated attributes", fn=_remove_deprecated_attrs
    ),
    BuiltinPlugin(name="removeDesc", description="removes <desc>", fn=_remove_desc),
    BuiltinPlugin(
        name="removeDimensions",
        description="removes width and height in presence of viewBox (opposite to removeViewBox)",
        fn=_remove_dimensions,
    ),
    BuiltinPlugin(name="removeDoctype", description="removes doctype declaration", fn=_remove_doctype),
    BuiltinPlugin(
        name="removeEditorsNSData",
        description="removes editors namespaces, elements and attributes",
        fn=_remove_editors_ns_data,
    ),
    BuiltinPlugin(
        name="removeElementsByAttr",
        description="removes arbitrary elements by ID or className",
        fn=_remove_elements_by_attr,
    ),
    BuiltinPlugin(name="removeEmptyAttrs", description="removes empty attributes", fn=_remove_empty_attrs),
    BuiltinPlugin(
        name="removeEmptyContainers", description="removes empty container elements", fn=_remove_empty_containers
    ),
    BuiltinPlugin(name="removeEmptyText", description="removes empty <text> elements", fn=_remove_empty_text),
    BuiltinPlugin(
        name="removeHiddenElems",
        description="removes hidden elements (zero sized, with absent attributes)",
        fn=_remove_hidden_elems,
    ),
    BuiltinPlugin(name="removeMetadata", description="removes <metadata>", fn=_remove_metadata),
    BuiltinPlugin(
        name="removeNonInheritableGroupAttrs",
        description="removes non-inheritable group's presentational attributes",
        fn=_remove_non_inheritable_group_attrs,
    ),
    BuiltinPlugin(
        name="removeOffCanvasPaths",
        description="removes elements that are drawn outside of the viewBox",
        fn=_remove_off_canvas_paths,
    ),
    BuiltinPlugin(name="removeRasterImages", description="removes raster images", fn=_remove_raster_images),
    BuiltinPlugin(name="removeScripts", description="removes scripts", fn=_remove_scripts),
    BuiltinPlugin(name="removeStyleElement", description="removes <style> element", fn=_remove_style_element),
    BuiltinPlugin(name="removeTitle", description="removes <title>", fn=_remove_title),
    BuiltinPlugin(
        name="removeUnknownsAndDefaults",
        description="removes unknown elements content and attributes, removes attrs with default values",
        fn=_remove_unknowns_and_defaults,
    ),
    BuiltinPlugin(name="removeUnusedNS", description="removes unused namespaces declaration", fn=_remove_unused_ns),
    BuiltinPlugin(
        name="removeUselessDefs", description="removes elements in <defs> without id", fn=_remove_useless_defs
    ),
    BuiltinPlugin(
        name="removeUselessStrokeAndFill",
        description="removes useless stroke and fill attributes",
        fn=_remove_useless_stroke_and_fill,
    ),
    BuiltinPlugin(name="removeViewBox", description="removes viewBox attribute when possible", fn=_remove_view_box),
    BuiltinPlugin(
        name="removeXlink",
        description="remove xlink namespace and replaces attributes with the SVG 2 equivalent where applicable",
        fn=_remove_xlink,
    ),
    BuiltinPlugin(name="removeXMLNS", description="removes xmlns attribute (for inline svg)", fn=_remove_xmlns),
    BuiltinPlugin(
        name="removeXMLProcInst", description="removes XML processing instructions", fn=_remove_xml_proc_inst
    ),
    BuiltinPlugin(
        name="reusePaths",
        description="Finds <path> elements with the same d, fill, and stroke, and converts them to <use> elements referencing a single <path> def.",
        fn=_reuse_paths,
    ),
    BuiltinPlugin(name="sortAttrs", description="Sort element attributes for better compression", fn=_sort_attrs),
    BuiltinPlugin(
        name="sortDefsChildren", description="Sorts children of <defs> to improve compression", fn=_sort_defs_children
    ),
)

_PLUGINS_MAP: dict[str, BuiltinPlugin] = {plugin.name: plugin for plugin in BUILTIN_PLUGINS}


def _get_plugin(name: str) -> BuiltinPlugin | None:
    if name == "removeScriptElement":
        sys.stderr.write(
            "Warning: removeScriptElement has been renamed to removeScripts, please update your SVGO config"
        )
        return _PLUGINS_MAP.get("removeScripts")
    return _PLUGINS_MAP.get(name)


def _resolve_plugin_config(plugin: Any) -> PluginConfig | None:
    if isinstance(plugin, str):
        builtin_plugin = _get_plugin(plugin)
        if builtin_plugin is None:
            raise SvgoConfigError(f'Unknown builtin plugin "{plugin}" specified.')
        return PluginConfig(name=plugin, params={}, fn=builtin_plugin.fn)
    if isinstance(plugin, Mapping):
        name = plugin.get("name")
        if name is None:
            raise SvgoConfigError("Plugin name must be specified")
        fn = plugin.get("fn")
        if fn is None:
            builtin_plugin = _get_plugin(str(name))
            if builtin_plugin is None:
                raise SvgoConfigError(f'Unknown builtin plugin "{name}" specified.')
            fn = builtin_plugin.fn
        params = plugin.get("params") or {}
        if not isinstance(params, Mapping):
            params = {}
        return PluginConfig(name=str(name), params=dict(params), fn=fn)
    return None


def _import_config(config_file: str) -> dict[str, Any]:
    resolved = Path(config_file)
    if not resolved.is_file():
        raise SvgoConfigError(f"Cannot find module '{resolved.resolve()}'")
    try:
        config = json.loads(resolved.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise SvgoConfigError(f'Invalid config file "{config_file}"') from exc
    if not isinstance(config, dict):
        raise SvgoConfigError(f'Invalid config file "{config_file}"')
    return config


def load_config(config_file: str | None = None, cwd: str | None = None) -> dict[str, Any] | None:
    if config_file is not None:
        base = Path(cwd) if cwd else Path.cwd()
        target = Path(config_file) if Path(config_file).is_absolute() else base / config_file
        return _import_config(str(target))
    directory = Path(cwd) if cwd else Path.cwd()
    while True:
        json_config = directory / "svgo.config.json"
        if json_config.is_file():
            return _import_config(str(json_config))
        for legacy_name in ("svgo.config.js", "svgo.config.mjs", "svgo.config.cjs"):
            legacy = directory / legacy_name
            if legacy.is_file():
                sys.stderr.write(
                    f"Warning: found {legacy_name} but JavaScript config"
                    " files are not supported by the Python port;"
                    " use svgo.config.json\n"
                )
        parent = directory.parent
        if directory == parent:
            return None
        directory = parent


def optimize(input_svg: str, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if config is None:
        config = {}
    if not isinstance(config, Mapping):
        raise SvgoError("Config should be an object")
    config = dict(config)
    js2svg = dict(config.get("js2svg") or {})
    js2svg.setdefault("eol", "lf")
    config["js2svg"] = js2svg

    max_pass_count = 10 if config.get("multipass") else 1
    prev_result_size = math.inf
    output = ""
    info: dict[str, Any] = {}
    if config.get("path") is not None:
        info["path"] = config["path"]
    for pass_index in range(max_pass_count):
        info["multipassCount"] = pass_index
        ast = parse_svg(input_svg, config.get("path"))
        plugins = config.get("plugins")
        if plugins is None:
            plugins = ["preset-default"]
        if not isinstance(plugins, (list, tuple)):
            raise SvgoConfigError(
                "malformed config, `plugins` property must be an array.\n"
                "See more info here:"
                " https://github.com/svg/svgo#configuration"
            )
        resolved_plugins = [
            resolved for resolved in (_resolve_plugin_config(plugin) for plugin in plugins) if resolved is not None
        ]
        if len(resolved_plugins) < len(plugins):
            sys.stderr.write("Warning: plugins list includes null or undefined elements, these will be ignored.\n")
        global_overrides: dict[str, Any] = {}
        if config.get("floatPrecision") is not None:
            global_overrides["floatPrecision"] = config["floatPrecision"]
        _invoke_plugins(ast, info, resolved_plugins, None, global_overrides)
        output = stringify_svg(ast, config.get("js2svg"))
        if len(output) < prev_result_size:
            input_svg = output
            prev_result_size = len(output)
        else:
            break
    if config.get("datauri"):
        output = encode_svg_datauri(output, str(config["datauri"]))
    return {"data": output}


HELP_TEXT = (
    "Usage: svgo [options] [INPUT...]\n"
    "\n"
    "SVGO is a Node.js library and command-line application for optimizing vector\n"
    "images.\n"
    "\n"
    "Arguments:\n"
    "  INPUT                      Alias to --input\n"
    "\n"
    "Options:\n"
    "  -v, --version              output the version number\n"
    '  -i, --input <INPUT...>     Input files, "-" for STDIN\n'
    "  -s, --string <STRING>      Input SVG data string\n"
    "  -f, --folder <FOLDER>      Input folder, optimize and rewrite all *.svg files\n"
    "  -o, --output <OUTPUT...>   Output file or folder (by default the same as the\n"
    '                             input), "-" for STDOUT\n'
    "  -p, --precision <INTEGER>  Set number of digits in the fractional part,\n"
    "                             overrides plugins params\n"
    "  --config <CONFIG>          Custom config file, only .js, .mjs, and .cjs is\n"
    "                             supported\n"
    "  --datauri <FORMAT>         Output as Data URI string (base64), URI encoded\n"
    "                             (enc) or unencoded (unenc)\n"
    "  --multipass                Pass over SVGs multiple times to ensure all\n"
    "                             optimizations are applied\n"
    "  --pretty                   Make SVG pretty printed\n"
    "  --indent <INTEGER>         Indent number when pretty printing SVGs\n"
    "  --eol <EOL>                Line break to use when outputting SVG: lf, crlf.\n"
    "                             If unspecified, uses platform default.\n"
    "  --final-newline            Ensure SVG ends with a line break\n"
    "  -r, --recursive            Use with '--folder'. Optimizes *.svg files in\n"
    "                             folders recursively.\n"
    "  --exclude <PATTERN...>     Use with '--folder'. Exclude files matching\n"
    "                             regular expression pattern.\n"
    "  -q, --quiet                Only output error messages, not regular status\n"
    "                             messages\n"
    "  --show-plugins             Show available plugins and exit\n"
    "  --no-color                 Output plain text without color\n"
    "  -h, --help                 display help for command\n"
)

VERSION_TEXT = "4.1.0\n"


import errno
import time

#: Number of parallel workers (per the port specification).
_WORKERS: int = 4

#: Files handed to each worker task (keeps memory O(1) in input size).
_TASK_CHUNK: int = 32


class _CliUsageError(Exception):
    """Commander-style usage error (message printed verbatim)."""


class _HelpRequested(Exception):
    """Raised when ``-h``/``--help`` was requested."""


class _VersionRequested(Exception):
    """Raised when ``-v``/``--version`` was requested."""


class _CliExit(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _CliOptions:
    inputs: list[str] = field(default_factory=list)
    string: str | None = None
    folder: str | None = None
    outputs: list[str] = field(default_factory=list)
    precision: str | None = None
    config: str | None = None
    datauri: str | None = None
    multipass: bool = False
    pretty: bool = False
    indent: str | None = None
    eol: str | None = None
    final_newline: bool = False
    recursive: bool = False
    exclude: list[str] = field(default_factory=list)
    quiet: bool = False
    show_plugins: bool = False
    color: bool = True
    positionals: list[str] = field(default_factory=list)

    @property
    def finalnewline(self) -> bool:
        return self.final_newline


_VALUE_OPTIONS: dict[str, str] = {
    "input": "INPUT",
    "string": "STRING",
    "folder": "FOLDER",
    "output": "OUTPUT",
    "precision": "INTEGER",
    "config": "CONFIG",
    "datauri": "FORMAT",
    "indent": "INTEGER",
    "eol": "EOL",
    "exclude": "PATTERN",
}
_BOOL_OPTIONS: tuple[str, ...] = (
    "multipass",
    "pretty",
    "final-newline",
    "recursive",
    "quiet",
    "show-plugins",
)
_SHORT_TO_LONG: dict[str, str] = {
    "i": "input",
    "s": "string",
    "f": "folder",
    "o": "output",
    "p": "precision",
    "r": "recursive",
    "q": "quiet",
}
_LONG_FLAG_NAMES: tuple[str, ...] = tuple(list(_VALUE_OPTIONS) + list(_BOOL_OPTIONS) + ["version", "help", "color"])


def _edit_distance(a: str, b: str) -> int:
    max_distance = 3
    if abs(len(a) - len(b)) > max_distance:
        return max(len(a), len(b))
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for j in range(1, len(b) + 1):
        for i in range(1, len(a) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _suggest_similar(word: str, candidates: Sequence[str]) -> str:
    if not candidates:
        return ""
    unique = list(dict.fromkeys(candidates))
    searching_options = word.startswith("--")
    if searching_options:
        word = word[2:]
        unique = [candidate[2:] for candidate in unique]
    similar: list[str] = []
    best_distance = 3
    for candidate in unique:
        if len(candidate) <= 1:
            continue
        distance = _edit_distance(word, candidate)
        length = max(len(word), len(candidate))
        similarity = (length - distance) / length
        if similarity > 0.4:
            if distance < best_distance:
                best_distance = distance
                similar = [candidate]
            elif distance == best_distance:
                similar.append(candidate)
    similar.sort()
    if searching_options:
        similar = [f"--{candidate}" for candidate in similar]
    if len(similar) > 1:
        return f"\n(Did you mean one of {', '.join(similar)}?)"
    if len(similar) == 1:
        return f"\n(Did you mean {similar[0]}?)"
    return ""


def _unknown_option(token: str) -> None:
    suggestion = ""
    if token.startswith("--"):
        suggestion = _suggest_similar(token, [f"--{name}" for name in _LONG_FLAG_NAMES])
    raise _CliUsageError(f"error: unknown option '{token}'{suggestion}")


def _option_display(long_name: str) -> str:
    short = next((s for s, l in _SHORT_TO_LONG.items() if l == long_name), None)
    placeholder = _VALUE_OPTIONS[long_name]
    if long_name in ("input", "output", "exclude"):
        body = f"{long_name} <{placeholder}...>"
    else:
        body = f"{long_name} <{placeholder}>"
    if short:
        return f"-{short}, --{body}"
    return f"--{body}"


def _plural(long_name: str) -> str:
    return {"input": "inputs", "output": "outputs"}.get(long_name, long_name)


def parse_cli_args(argv: Sequence[str]) -> _CliOptions:
    options = _CliOptions()
    index = 0
    count = len(argv)

    def consume_variadic(value: str) -> list[str]:
        nonlocal index
        collected = [value]
        while index < count:
            nxt = argv[index]
            if nxt.startswith("-") and nxt != "-":
                break
            collected.append(nxt)
            index += 1
        return collected

    def assign_value(long_name: str, value: str) -> None:
        if long_name in ("input", "output", "exclude"):
            target = _plural(long_name)
            getattr(options, target).extend(consume_variadic(value))
        else:
            setattr(options, long_name, value)

    while index < count:
        arg = argv[index]
        index += 1
        if arg == "--":
            options.positionals.extend(argv[index:])
            break
        if arg.startswith("--"):
            body = arg[2:]
            value: str | None = None
            if "=" in body:
                body, value = body.split("=", 1)
            if value is None and body == "help":
                raise _HelpRequested()
            if value is None and body == "version":
                raise _VersionRequested()
            if body == "no-color":
                if value is not None:
                    _unknown_option(arg)
                options.color = False
                continue
            if body in _BOOL_OPTIONS:
                if value is not None:
                    _unknown_option(arg)
                setattr(options, body.replace("-", "_"), True)
                continue
            if body in _VALUE_OPTIONS:
                if value is None:
                    if index >= count:
                        raise _CliUsageError(f"error: option '{_option_display(body)}' argument missing")
                    value = argv[index]
                    index += 1
                assign_value(body, value)
                continue
            _unknown_option(arg)
        elif arg.startswith("-") and len(arg) > 1 and arg != "-":
            body = arg[1:]
            position = 0
            while position < len(body):
                short = body[position]
                if short == "h":
                    raise _HelpRequested()
                if short == "v":
                    raise _VersionRequested()
                long_name = _SHORT_TO_LONG.get(short)
                if long_name is None:
                    _unknown_option(f"-{short}")
                if long_name in _BOOL_OPTIONS:
                    setattr(options, long_name.replace("-", "_"), True)
                    position += 1
                    continue
                attached = body[position + 1 :]
                if attached.startswith("="):
                    attached = attached[1:]
                if attached:
                    value = attached
                else:
                    if index >= count:
                        raise _CliUsageError(f"error: option '{_option_display(long_name)}' argument missing")
                    value = argv[index]
                    index += 1
                assign_value(long_name, value)
                break
            continue
        else:
            options.positionals.append(arg)
    return options


def _colors_enabled(options: _CliOptions) -> bool:
    if not options.color:
        return False
    if "NO_COLOR" in os.environ:
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _red(text: str, enabled: bool) -> str:
    return f"\033[31m{text}\033[0m" if enabled else text


def _green(text: str, enabled: bool) -> str:
    return f"\033[32m{text}\033[0m" if enabled else text


def _print_error(text: str, color: bool) -> None:
    payload = (_red(text, color) + "\n").encode("utf-8", "surrogateescape")
    sys.stderr.buffer.write(payload)
    sys.stderr.buffer.flush()


def _print_line(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _write_stdout(data: str) -> None:
    sys.stdout.buffer.write(data.encode("utf-8"))
    sys.stdout.buffer.flush()


def _show_available_plugins(color: bool) -> None:
    plugin_to_presets: dict[str, list[str]] = {}
    for plugin in BUILTIN_PLUGINS:
        if plugin.is_preset:
            for preset_plugin in plugin.plugins:
                plugin_to_presets.setdefault(preset_plugin.name, []).append(plugin.name)
    lines = ["Currently available plugins:"]
    for plugin in BUILTIN_PLUGINS:
        description = plugin.description
        presets = plugin_to_presets.get(plugin.name) or []
        if presets:
            description += f" ({', '.join(presets)})"
        lines.append(f" [ {_green(plugin.name, color)} ] {description}")
    _print_line("\n".join(lines))


def _check_is_dir(path: str) -> bool:
    try:
        return Path(path).lstat().is_dir()
    except OSError:
        return path.endswith(os.sep)


def _iter_svg_file_descriptions(
    config: Mapping[str, Any],
    directory: Path,
    output: Path,
) -> Iterator[tuple[Path, Path]]:
    exclude: list[re.Pattern[str]] = list(config.get("exclude") or [])
    entries = list(os.scandir(directory))
    for entry in entries:
        name = entry.name
        if name[-4:].lower() == ".svg" and not any(pattern.search(name) for pattern in exclude):
            yield directory / name, output / name
    if config.get("recursive"):
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            yield from _iter_svg_file_descriptions(config, directory / entry.name, output / entry.name)


def _format_os_error(path: str, exc: OSError) -> str:
    if exc.errno == errno.ENOENT:
        return f"Error: Error: no such file or directory '{path}'."
    code_name = errno.errorcode.get(exc.errno, "EIO")
    return f"Error: {code_name}: {exc.strerror or str(exc)}, open '{path}'"


def _optimize_file_task(
    payload: tuple[str, str, str],
) -> tuple[str, str, int, int, str | None, int, str | None]:
    input_path, output_path, config_json = payload
    start_time = time.monotonic()
    try:
        config = json.loads(config_json)
    except ValueError:
        return input_path, output_path, 0, 0, "Error: internal worker config error", 0, None
    try:
        data = Path(input_path).read_text(encoding="utf-8")
    except IsADirectoryError:
        return input_path, output_path, 0, 0, "EISDIR", 0, None
    except OSError as exc:
        return input_path, output_path, 0, 0, _format_os_error(input_path, exc), 0, None
    except UnicodeDecodeError as exc:
        return input_path, output_path, 0, 0, f"Error: {exc}", 0
    try:
        config["path"] = input_path
        result = optimize(data, config)
    except SvgoParserError as exc:
        return input_path, output_path, 0, 0, f"PARSE:{exc}", 0, None
    except SvgoError as exc:
        return input_path, output_path, 0, 0, f"Error: {exc}", 0, None
    output = result["data"]
    try:
        if output_path != "-":
            target = Path(output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_text(output, encoding="utf-8")
            except IsADirectoryError:
                target = target / Path(input_path).name
                target.write_text(output, encoding="utf-8")
            except NotADirectoryError:
                return input_path, output_path, 0, 0, (f"Error: output '{output_path}' is not a directory."), 0, None
    except OSError as exc:
        return input_path, output_path, 0, 0, _format_os_error(output_path, exc), 0, None
    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    return (
        input_path,
        output_path,
        len(data.encode("utf-8")),
        len(output.encode("utf-8")),
        None,
        elapsed_ms,
        output if output_path == "-" else None,
    )


_EXECUTOR: ProcessPoolExecutor | None = None


def _get_executor() -> ProcessPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ProcessPoolExecutor(max_workers=_WORKERS)
    return _EXECUTOR


def _shutdown_executor() -> None:
    global _EXECUTOR
    if _EXECUTOR is not None:
        _EXECUTOR.shutdown(wait=True)
        _EXECUTOR = None


@dataclass
class _RunState:
    color: bool = False


def _handle_task_result(
    task: tuple[str, str, int, int, str | None, int, str | None],
    config: Mapping[str, Any],
    state: _RunState,
) -> None:
    input_path, output_path, input_size, output_size, error, elapsed_ms, stdout_data = task
    if error is not None:
        if error == "EISDIR":
            _optimize_folder(config, input_path, input_path, state)
            return
        if error.startswith("PARSE:"):
            _print_error(error[len("PARSE:") :].rstrip("\n") + "\n", state.color)
            raise _CliExit(EXIT_FAILURE)
        _print_error(error, state.color)
        raise _CliExit(EXIT_FAILURE)
    if stdout_data is not None:
        _write_stdout(stdout_data)
    if not config.get("quiet") and output_path != "-":
        _print_line()
        _print_line(f"{os.path.basename(input_path)}:")
        _print_line(f"Done in {elapsed_ms} ms!")
        _print_profit(input_size, output_size, state.color)


def _print_profit(input_size: int, output_size: int, color: bool) -> None:
    if input_size == 0:
        return
    profit_percent = 100 - (output_size * 100) / input_size
    if profit_percent < 0:
        sign, percent_text = "+", js_number_string(abs(_js_round(profit_percent * 10) / 10)) + "%"
        styled = _red(percent_text, color)
    else:
        sign, percent_text = "-", js_number_string(abs(_js_round(profit_percent * 10) / 10)) + "%"
        styled = _green(percent_text, color)
    in_kib = _js_round(input_size / 1024 * 1000) / 1000
    out_kib = _js_round(output_size / 1024 * 1000) / 1000
    _print_line(f"{js_number_string(in_kib)} KiB {sign} {styled} = {js_number_string(out_kib)} KiB")


def _process_file_descriptions(
    config: dict[str, Any],
    descriptions: Iterator[tuple[Path, Path]],
    state: _RunState,
) -> None:
    pool_config = {key: value for key, value in config.items() if key not in ("quiet", "recursive", "exclude")}
    config_json = json.dumps(pool_config)

    def run_task(input_path: Path, output_path: Path) -> None:
        _handle_task_result(
            _optimize_file_task((str(input_path), str(output_path), config_json)),
            config,
            state,
        )

    buffered: list[tuple[Path, Path]] = []

    def flush(first_only: bool) -> None:
        nonlocal buffered
        if not buffered:
            return
        if first_only or len(buffered) == 1:
            for input_path, output_path in buffered:
                run_task(input_path, output_path)
        else:
            executor = _get_executor()
            batch = buffered[:]
            futures = [
                executor.submit(
                    _optimize_file_task,
                    (str(input_path), str(output_path), config_json),
                )
                for input_path, output_path in batch
            ]
            for future in futures:
                _handle_task_result(future.result(), config, state)
        buffered = []

    for description in descriptions:
        buffered.append(description)
        if len(buffered) >= _TASK_CHUNK:
            flush(first_only=False)
    flush(first_only=False)


def _optimize_folder(config: Mapping[str, Any], folder: str, output: str, state: _RunState) -> None:
    if not config.get("quiet"):
        _print_line(f"Processing directory '{folder}':")
        _print_line()
    directory = Path(folder)
    output_directory = Path(output)
    descriptions = list(_iter_svg_file_descriptions(config, directory, output_directory))
    if not descriptions:
        message = f"No SVG files have been found in '{folder}' directory."
        _print_error(f"Error: {message}", state.color)
        raise _CliExit(EXIT_FAILURE)
    _process_file_descriptions(dict(config), iter(descriptions), state)


def _process_svg_data(
    config: Mapping[str, Any],
    info: dict[str, Any] | None,
    data: str,
    output: str,
    state: _RunState,
    input_name: str | None = None,
) -> None:
    start_time = time.monotonic()
    prev_file_size = len(data.encode("utf-8"))
    run_config = dict(config)
    if info:
        run_config.update(info)
    try:
        result = optimize(data, run_config)
    except SvgoParserError as exc:
        _print_error(str(exc).rstrip("\n") + "\n", state.color)
        raise _CliExit(EXIT_FAILURE) from None
    except SvgoError as exc:
        _print_error(f"Error: {exc}", state.color)
        raise _CliExit(EXIT_FAILURE) from None
    result_file_size = len(result["data"].encode("utf-8"))
    processing_time = int((time.monotonic() - start_time) * 1000)
    _write_output(input_name, output, result["data"], state)
    if not config.get("quiet") and output != "-":
        if input_name:
            _print_line()
            _print_line(f"{os.path.basename(input_name)}:")
        _print_line(f"Done in {processing_time} ms!")
        _print_profit(prev_file_size, result_file_size, state.color)


def _write_output(input_name: str | None, output: str, data: str, state: _RunState) -> None:
    if output == "-":
        _write_stdout(data)
        return
    target = Path(output)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8")
    except IsADirectoryError:
        if input_name:
            (target / os.path.basename(input_name)).write_text(data, encoding="utf-8")
        else:
            _print_error(f"Error: output '{output}' is not a directory.", state.color)
            raise _CliExit(EXIT_FAILURE)
    except NotADirectoryError:
        _print_error(f"Error: output '{output}' is not a directory.", state.color)
        raise _CliExit(EXIT_FAILURE)
    except OSError as exc:
        _print_error(_format_os_error(output, exc), state.color)
        raise _CliExit(EXIT_FAILURE)


def _run_cli(argv: Sequence[str]) -> int:
    options = parse_cli_args(list(argv))
    input_list = list(options.inputs) if options.inputs else list(options.positionals)
    output = list(options.outputs)
    config: dict[str, Any] = {}

    if options.datauri is not None and options.datauri not in ("base64", "enc", "unenc"):
        _print_error(
            "error: option '--datauri' must have one of the following values: 'base64', 'enc' or 'unenc'", False
        )
        return EXIT_FAILURE

    if options.indent is not None:
        number = js_parse_int(options.indent)
        if number != number:
            _print_error(
                "error: option '--indent' argument must be an integer number",
                False,
            )
            return EXIT_FAILURE
        options.indent = str(int(number))

    if options.eol is not None and options.eol not in ("lf", "crlf"):
        _print_error("error: option '--eol' must have one of the following values: 'lf' or 'crlf'", False)
        return EXIT_FAILURE

    if options.show_plugins:
        _show_available_plugins(_colors_enabled(options))
        return EXIT_SUCCESS

    loaded = load_config(options.config)
    if loaded is not None:
        config = dict(loaded)

    if options.quiet:
        config["quiet"] = True
    if options.recursive:
        config["recursive"] = True
    config["exclude"] = [re.compile(pattern) for pattern in options.exclude] if options.exclude else []

    if options.precision is not None:
        number = js_parse_int(options.precision)
        if number != number:
            _print_error(
                "error: option '-p, --precision' argument must be an integer number",
                False,
            )
            return EXIT_FAILURE
        config["floatPrecision"] = min(max(0, int(number)), 20)

    if options.multipass:
        config["multipass"] = True

    if options.pretty:
        js2svg = dict(config.get("js2svg") or {})
        js2svg["pretty"] = True
        if options.indent is not None:
            js2svg["indent"] = int(options.indent)
        config["js2svg"] = js2svg

    if options.eol is not None:
        js2svg = dict(config.get("js2svg") or {})
        js2svg["eol"] = options.eol
        config["js2svg"] = js2svg

    if options.final_newline:
        js2svg = dict(config.get("js2svg") or {})
        js2svg["finalNewline"] = True
        config["js2svg"] = js2svg

    if output:
        if input_list and input_list[0] != "-":
            if len(output) == 1 and _check_is_dir(output[0]):
                directory = output[0]
                for i in range(len(input_list)):
                    output[i] = (
                        input_list[i]
                        if _check_is_dir(input_list[i])
                        else os.path.join(directory, os.path.basename(input_list[i]))
                    )
            elif len(output) < len(input_list):
                output = output + input_list[len(output) :]
    elif input_list:
        output = list(input_list)
    elif options.string is not None:
        output = ["-"]

    if options.datauri:
        config["datauri"] = options.datauri

    state = _RunState(color=_colors_enabled(options))
    inputs_specified = options.folder is not None or bool(input_list) or options.string is not None

    try:
        if options.folder is not None:
            output_folder = (output[0] if output else None) or options.folder
            _optimize_folder(config, options.folder, output_folder, state)
        if input_list:
            if input_list[0] == "-":
                data = sys.stdin.read()
                _process_svg_data(config, None, data, output[0] if output else "-", state)
            else:
                descriptions = iter([
                    (
                        Path(input_list[i]),
                        Path(output[i] if i < len(output) else input_list[i]),
                    )
                    for i in range(len(input_list))
                ])
                _process_file_descriptions(config, descriptions, state)
        elif options.string is not None:
            data = decode_svg_datauri(options.string)
            _process_svg_data(config, None, data, output[0] if output else "-", state)
        elif not inputs_specified:
            _optimize_folder(config, ".", ".", state)
    except _CliExit as exc:
        _flush_stdout_safe()
        return exc.code
    _flush_stdout_safe()
    return EXIT_SUCCESS


def _flush_stdout_safe() -> None:
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        raise
    except Exception:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        try:
            return _run_cli(list(argv))
        except _HelpRequested:
            sys.stdout.write(HELP_TEXT)
            sys.stdout.flush()
            return EXIT_SUCCESS
        except _VersionRequested:
            sys.stdout.write(VERSION_TEXT)
            sys.stdout.flush()
            return EXIT_SUCCESS
        except _CliUsageError as exc:
            _print_error(str(exc), False)
            return EXIT_FAILURE
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return EXIT_FAILURE
    finally:
        _shutdown_executor()


if __name__ == "__main__":
    raise SystemExit(main())
