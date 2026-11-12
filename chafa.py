#!/data/data/com.termux/files/usr/bin/env python

import argparse
import base64
import io
import math
import os
import re
import shutil
import sys
import unicodedata
import zlib
from dataclasses import dataclass
from enum import Enum

import numpy as np

from PIL import Image, ImageOps

SYMP_W = 8
SYMP_H = 8
SYMP_N = SYMP_W * SYMP_H

TRANSPARENT = 256
FG_PEN = 257
BG_PEN = 258


class Mode(Enum):
    FGBG = "none"
    FGBG_BGFG = "2"
    INDEXED_8 = "8"
    INDEXED_16_8 = "16/8"
    INDEXED_16 = "16"
    INDEXED_240 = "240"
    INDEXED_256 = "256"
    TRUECOLOR = "full"


PALETTE_MODES = (
    Mode.INDEXED_8,
    Mode.INDEXED_16_8,
    Mode.INDEXED_16,
    Mode.INDEXED_240,
    Mode.INDEXED_256,
)
FGBG_MODES = (Mode.FGBG, Mode.FGBG_BGFG)

DITHER_BASE_INTENSITY = {
    Mode.FGBG: 1.0,
    Mode.FGBG_BGFG: 1.0,
    Mode.INDEXED_8: 0.5,
    Mode.INDEXED_16_8: 0.25,
    Mode.INDEXED_16: 0.25,
    Mode.INDEXED_240: 0.1,
    Mode.INDEXED_256: 0.1,
    Mode.TRUECOLOR: 0.1,
}

CROP_PCT_16 = 5
CROP_PCT_8 = 10
CROP_PCT_2 = 20

INTENSITY_MAX = 256 * 8
FIXED_MULT = 4096

ESC = "\x1b"

SEQ_RESET_ATTRS = ESC + "[0m"
SEQ_RESET_FG = ESC + "[39m"
SEQ_BOLD = ESC + "[1m"
SEQ_INVERT = ESC + "[7m"


@dataclass
class Config:
    width: int = 80
    height: int = 24
    mode: Mode = Mode.TRUECOLOR
    color_space: str = "din99d"
    format: str = "symbols"
    symbols_spec: str = "block+border+space"
    fill_spec: str = ""
    work_factor: float = 0.5
    alpha_threshold: int = 128
    fg_color: tuple[int, int, int] = (255, 255, 255)
    bg_color: tuple[int, int, int] = (0, 0, 0)
    preprocessing: bool = True
    dither_mode: str = "none"
    dither_intensity: float = 1.0
    dither_grain_w: int = 4
    dither_grain_h: int = 4
    fg_only: bool = False
    invert: bool = False
    stretch: bool = False
    animate: bool = True
    align: str = "start"

    @property
    def extract_colors(self) -> bool:
        return self.mode not in FGBG_MODES

    @property
    def work_factor_int(self) -> int:
        return int(self.work_factor * 10 + 0.5)


_XYZ_EPSILON = 216.0 / 24389.0
_XYZ_KAPPA = 24389.0 / 27.0
_D99_XN_INV = 1.0 / (1.12 * 0.95047 - 0.12 * 1.08883)
_D99_ZN_INV = 1.0 / 1.08883
_D99_SIN40 = 0.6427876096865393
_D99_COS40 = 0.766044443118978
_TWO_PI = 6.283185


def rgb_to_din99d(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) * (1.0 / 255.0)
    lin = np.where(c <= 0.04045, c * (1.0 / 12.92), ((c + 0.055) * (1.0 / 1.055)) ** 2.4)
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    x = 1.12 * x - 0.12 * z
    fx = np.where(
        x * _D99_XN_INV > _XYZ_EPSILON, np.cbrt(x * _D99_XN_INV), (_XYZ_KAPPA * x * _D99_XN_INV + 16.0) * (1.0 / 116.0)
    )
    fy = np.where(y > _XYZ_EPSILON, np.cbrt(y), (_XYZ_KAPPA * y + 16.0) * (1.0 / 116.0))
    fz = np.where(
        z * _D99_ZN_INV > _XYZ_EPSILON, np.cbrt(z * _D99_ZN_INV), (_XYZ_KAPPA * z * _D99_ZN_INV + 16.0) * (1.0 / 116.0)
    )
    l = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b2 = 200.0 * (fy - fz)
    adj_l = 325.22 * np.log1p(0.0036 * l)
    ee = _D99_SIN40 * a + _D99_COS40 * b2
    f = 1.14 * (_D99_SIN40 * b2 - _D99_COS40 * a)
    g_chroma = np.sqrt(ee * ee + f * f)
    chroma = 22.5 * np.log1p(0.06 * g_chroma)
    h = np.mod(np.arctan2(f, ee) + 0.8726646, _TWO_PI)
    out_l = np.floor(adj_l * 2.5 + 0.5)
    out_a = np.floor(chroma * np.cos(h) * 2.5 + 128.0 + 0.5)
    out_b = np.floor(chroma * np.sin(h) * 2.5 + 128.0 + 0.5)
    out = np.stack([out_l, out_a, out_b], axis=-1)
    return np.clip(out, 0, 255).astype(np.uint8)


def brightness(col: np.ndarray, color_space: str) -> np.ndarray:
    if color_space == "din99d":
        return col[..., 0].astype(np.int64) * 255 // 250
    return col.sum(axis=-1) // 3


_TERM_256_FIRST16 = [
    0x000000,
    0x800000,
    0x007000,
    0x707000,
    0x000070,
    0x700070,
    0x007070,
    0xC0C0C0,
    0x404040,
    0xFF0000,
    0x00FF00,
    0xFFFF00,
    0x0000FF,
    0xFF00FF,
    0x00FFFF,
    0xFFFFFF,
]
_CUBE_STEPS = [0x00, 0x5F, 0x87, 0xAF, 0xD7, 0xFF]
_GRAY_RAMP = [0x08 + 10 * i for i in range(24)]


def _build_term_256() -> list[tuple[int, int, int]]:
    cols = [(v >> 16, (v >> 8) & 0xFF, v & 0xFF) for v in _TERM_256_FIRST16]
    for r in _CUBE_STEPS:
        for g in _CUBE_STEPS:
            for b in _CUBE_STEPS:
                cols.append((r, g, b))
    cols += [(v, v, v) for v in _GRAY_RAMP]
    assert len(cols) == 256
    cols += [(0x80, 0x80, 0x80)]
    cols += [(0xFF, 0xFF, 0xFF)]
    cols += [(0x00, 0x00, 0x00)]
    return cols


TERM_256_RGB = np.array(_build_term_256(), dtype=np.uint8)


class Palette:
    def __init__(
        self,
        kind: str,
        color_space: str,
        mode: Mode,
        fg_rgb: tuple[int, int, int],
        bg_rgb: tuple[int, int, int],
        alpha_threshold: int,
        colors: np.ndarray | None = None,
    ):
        self.kind = kind
        self.cs = color_space
        self.mode = mode
        self.alpha_threshold = alpha_threshold
        if colors is None:
            base = TERM_256_RGB.copy()
            base[FG_PEN] = fg_rgb
            base[BG_PEN] = bg_rgb
        else:
            base = colors
        self.rgb = base
        tab = base if color_space == "rgb" else rgb_to_din99d(base)
        self.colors = tab.astype(np.int32)
        if kind == "fgbg":
            self.order = np.array([FG_PEN, BG_PEN], dtype=np.int32)
        elif kind == "8":
            self.order = np.arange(0, 8, dtype=np.int32)
        elif kind == "16":
            self.order = np.arange(0, 16, dtype=np.int32)
        elif kind == "240":
            self.order = np.arange(16, 256, dtype=np.int32)
        elif kind == "256" and color_space == "rgb":
            self.order = np.concatenate([np.arange(16, 256, dtype=np.int32), np.arange(0, 16, dtype=np.int32)])
        else:
            self.order = np.arange(0, min(len(base), 256), dtype=np.int32)

    def _distances(self, qq: np.ndarray) -> np.ndarray:
        pal = self.colors[self.order].astype(np.float32)
        qf = qq.astype(np.float32)
        q2 = (qf * qf).sum(axis=1, keepdims=True)
        p2 = (pal * pal).sum(axis=1)[None, :]
        cross = qf @ pal.T
        return q2 - 2.0 * cross + p2

    def lookup(self, q: np.ndarray, q_alpha: np.ndarray) -> np.ndarray:
        out = np.full(q.shape[0], TRANSPARENT, dtype=np.int32)
        opaque = q_alpha >= self.alpha_threshold
        if not opaque.any():
            return out
        d = self._distances(q[opaque])
        out[opaque] = self.order[np.argmin(d, axis=1)]
        return out

    def lookup2(self, q: np.ndarray, q_alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n = q.shape[0]
        best = np.full(n, TRANSPARENT, dtype=np.int32)
        second = np.full(n, TRANSPARENT, dtype=np.int32)
        opaque = q_alpha >= self.alpha_threshold
        if not opaque.any():
            return best, second
        d = self._distances(q[opaque])
        if d.shape[1] >= 2:
            idx = np.argsort(d, axis=1, kind="stable")[:, :2]
            b1 = self.order[idx[:, 0]]
            b2 = self.order[idx[:, 1]]
        else:
            b1 = np.full(d.shape[0], self.order[0], dtype=np.int32)
            b2 = b1
        best[opaque] = b1
        second[opaque] = b2
        return best, second

    def color_of(self, pens: np.ndarray) -> np.ndarray:
        return self.colors[np.clip(pens, 0, len(self.colors) - 1)]


TAG_NONE = 0
TAG_SPACE = 1 << 0
TAG_SOLID = 1 << 1
TAG_STIPPLE = 1 << 2
TAG_BLOCK = 1 << 3
TAG_BORDER = 1 << 4
TAG_DIAGONAL = 1 << 5
TAG_DOT = 1 << 6
TAG_QUAD = 1 << 7
TAG_HHALF = 1 << 8
TAG_VHALF = 1 << 9
TAG_HALF = TAG_HHALF | TAG_VHALF
TAG_INVERTED = 1 << 10
TAG_BRAILLE = 1 << 11
TAG_TECHNICAL = 1 << 12
TAG_GEOMETRIC = 1 << 13
TAG_ASCII = 1 << 14
TAG_ALPHA = 1 << 15
TAG_DIGIT = 1 << 16
TAG_ALNUM = TAG_ALPHA | TAG_DIGIT
TAG_NARROW = 1 << 17
TAG_WIDE = 1 << 18
TAG_AMBIGUOUS = 1 << 19
TAG_UGLY = 1 << 20
TAG_LEGACY = 1 << 21
TAG_SEXTANT = 1 << 22
TAG_WEDGE = 1 << 23
TAG_LATIN = 1 << 24
TAG_IMPORTED = 1 << 25
TAG_OCTANT = 1 << 26
TAG_EXTRA = 1 << 30
TAG_BAD = TAG_AMBIGUOUS | TAG_UGLY
TAG_ALL = ~(TAG_EXTRA | TAG_BAD) & 0xFFFFFFFF

TAG_NAMES = {
    "all": TAG_ALL,
    "none": TAG_NONE,
    "space": TAG_SPACE,
    "solid": TAG_SOLID,
    "stipple": TAG_STIPPLE,
    "block": TAG_BLOCK,
    "border": TAG_BORDER,
    "diagonal": TAG_DIAGONAL,
    "dot": TAG_DOT,
    "quad": TAG_QUAD,
    "half": TAG_HALF,
    "hhalf": TAG_HHALF,
    "vhalf": TAG_VHALF,
    "inverted": TAG_INVERTED,
    "braille": TAG_BRAILLE,
    "sextant": TAG_SEXTANT,
    "wedge": TAG_WEDGE,
    "technical": TAG_TECHNICAL,
    "geometric": TAG_GEOMETRIC,
    "ascii": TAG_ASCII,
    "alpha": TAG_ALPHA,
    "digit": TAG_DIGIT,
    "narrow": TAG_NARROW,
    "wide": TAG_WIDE,
    "ambiguous": TAG_AMBIGUOUS,
    "ugly": TAG_UGLY,
    "extra": TAG_EXTRA,
    "alnum": TAG_ALNUM,
    "bad": TAG_BAD,
    "legacy": TAG_LEGACY,
    "latin": TAG_LATIN,
    "import": TAG_IMPORTED,
    "imported": TAG_IMPORTED,
    "octant": TAG_OCTANT,
}

_AMBIGUOUS_RANGES = (
    (0x00AD, 0x00AD),
    (0x2196, 0x21FF),
    (0x222C, 0x2237),
    (0x2245, 0x2269),
    (0x226D, 0x2279),
    (0x2295, 0x22AF),
    (0x22BF, 0x22BF),
    (0x22C8, 0x22FF),
    (0x2300, 0x23FF),
    (0x2460, 0x24FF),
    (0x25A0, 0x25FF),
    (0x2700, 0x27BF),
    (0x27C0, 0x27E5),
    (0x27F0, 0x27FF),
    (0x2900, 0x297F),
    (0x2980, 0x29FF),
    (0x2B00, 0x2BFF),
    (0x1F100, 0x1F1FF),
)
_UGLY_RANGES = (
    (0x2600, 0x26FF),
    (0x1F000, 0x1FB3B),
    (0x1FBCB, 0x1FFFF),
    (0x534D, 0x534D),
)
_META_RANGES = ((0x0640, 0x0640), (0x2FF0, 0x2FFF))


def _in_ranges(c: int, ranges) -> bool:
    return any(lo <= c <= hi for lo, hi in ranges)


def _default_tags(c: int) -> int:
    tags = 0
    ch = chr(c)
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        tags |= TAG_WIDE
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Mc", "Me", "Cf") or _in_ranges(c, _AMBIGUOUS_RANGES):
        tags |= TAG_AMBIGUOUS
    if _in_ranges(c, _UGLY_RANGES) or _in_ranges(c, _META_RANGES):
        tags |= TAG_UGLY
    if c <= 0x7F:
        tags |= TAG_ASCII
    elif 0x2300 <= c <= 0x23FF:
        tags |= TAG_TECHNICAL
    elif 0x25A0 <= c <= 0x25FF:
        tags |= TAG_GEOMETRIC
    elif 0x2800 <= c <= 0x28FF:
        tags |= TAG_BRAILLE
    elif 0x1FB00 <= c <= 0x1FB3B:
        tags |= TAG_SEXTANT
    if ch.isalpha():
        tags |= TAG_ALPHA
    if ch.isdigit():
        tags |= TAG_DIGIT
    if not tags & TAG_WIDE:
        tags |= TAG_NARROW
    return tags


class Symbol:
    __slots__ = ("c", "tags", "bitmap", "cov", "popcount", "fg_weight", "bg_weight")

    def __init__(self, c: int, tags: int, cov: np.ndarray):
        self.c = c
        self.tags = tags
        self.cov = cov
        self.bitmap = 0
        for p in cov.flat:
            self.bitmap = (self.bitmap << 1) | int(p)
        self.popcount = int(cov.sum())
        self.fg_weight = self.popcount
        self.bg_weight = SYMP_N - self.popcount


def _cov_from_bitmap(bitmap: int) -> np.ndarray:
    bits = np.array([(bitmap >> (SYMP_N - 1 - i)) & 1 for i in range(SYMP_N)], dtype=np.uint8)
    return bits.reshape(SYMP_H, SYMP_W)


def _gen_braille(c: int) -> np.ndarray:
    cov = np.zeros((SYMP_H, SYMP_W), dtype=np.uint8)
    val = c - 0x2800
    left_bits = (0, 1, 2, 6)
    right_bits = (3, 4, 5, 7)
    for row in range(4):
        cov[row * 2, 1] = cov[row * 2, 2] = (val >> left_bits[row]) & 1
        cov[row * 2, 5] = cov[row * 2, 6] = (val >> right_bits[row]) & 1
    return cov


def _gen_sextant(c: int) -> np.ndarray:
    b = c - 0x1FB00 + 1
    if b > 20:
        b += 1
    if b > 41:
        b += 1
    cov = np.zeros((SYMP_H, SYMP_W), dtype=np.uint8)
    for y in range(3):
        for x in range(2):
            bit = y * 2 + x
            if b & (1 << bit):
                for v in range(3):
                    for u in range(4):
                        row = y * 3 + v
                        if row > 3:
                            row -= 1
                        cov[row, x * 4 + u] = 1
    return cov


_OCTANT_MAP = {
    0x00: 0x00A0,
    0x01: 0x1CEA8,
    0x02: 0x1CEAB,
    0x03: 0x1FB82,
    0x05: 0x2598,
    0x0A: 0x259D,
    0x0F: 0x2580,
    0x14: 0x1FBE6,
    0x28: 0x1FBE7,
    0x3F: 0x1FB85,
    0x40: 0x1CEA3,
    0x50: 0x2596,
    0x55: 0x258C,
    0x5A: 0x259E,
    0x5F: 0x259B,
    0x80: 0x1CEA0,
    0xA0: 0x2597,
    0xA5: 0x259A,
    0xAA: 0x2590,
    0xAF: 0x259C,
    0xC0: 0x2582,
    0xF0: 0x2584,
    0xF5: 0x2599,
    0xFA: 0x259F,
    0xFC: 0x2586,
    0xFF: 0x2588,
}
_OCTANT_KEYS = sorted(_OCTANT_MAP)


def _octant_bits_to_cp(bits: int) -> int:
    if bits in _OCTANT_MAP:
        return _OCTANT_MAP[bits]
    lo, hi = 0, len(_OCTANT_KEYS)
    while lo < hi:
        mid = (lo + hi) // 2
        if _OCTANT_KEYS[mid] < bits:
            lo = mid + 1
        else:
            hi = mid
    return ((bits - lo) & 0xFFFF) | 0x1CD00


def _gen_octant(bits: int) -> np.ndarray:
    cov = np.zeros((SYMP_H, SYMP_W), dtype=np.uint8)
    for y in range(SYMP_H):
        for x in range(SYMP_W):
            bit = (y & ~1) + ((x >> 2) & 1)
            cov[y, x] = (bits >> bit) & 1
    return cov


SYM_DATA_B64 = (
    "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"
    "pa7yMpqzdvdJT8H7oWzfA="
)

_SYMBOLS: dict[int, Symbol] | None = None


def all_symbols() -> dict[int, Symbol]:
    global _SYMBOLS
    if _SYMBOLS is not None:
        return _SYMBOLS
    syms: dict[int, Symbol] = {}
    raw = zlib.decompress(base64.b64decode(SYM_DATA_B64)).decode()
    for line in raw.splitlines():
        cp_s, tags_s, bm_s = line.split()
        cp, tags, bm = int(cp_s, 16), int(tags_s, 16), int(bm_s, 16)
        syms[cp] = Symbol(cp, tags, _cov_from_bitmap(bm))
    for c in range(0x2800, 0x2900):
        syms[c] = Symbol(c, TAG_BRAILLE, _gen_braille(c))
    for c in range(0x1FB00, 0x1FB3B):
        syms[c] = Symbol(c, TAG_LEGACY | TAG_SEXTANT, _gen_sextant(c))
    for bits in range(256):
        c = _octant_bits_to_cp(bits)
        if 0x1CD00 <= c <= 0x1D000:
            syms[c] = Symbol(c, TAG_LEGACY | TAG_OCTANT, _gen_octant(bits))
    _SYMBOLS = syms
    return syms


@dataclass
class _Selector:
    kind: str
    additive: bool
    tags: int = 0
    first: int = 0
    last: int = 0


def _parse_code_point(s: str) -> tuple[int, int]:
    i = 0
    if s[:1] in ("u", "U"):
        i = 1
    if len(s) >= 2 and s[0] == "0" and s[1] == "x":
        i = 2
    start = i
    val = 0
    while i < len(s):
        ch = s[i]
        if ch.isdigit() or ch in "abcdefABCDEF":
            val = val * 16 + int(ch, 16)
            i += 1
        else:
            break
    return (val, i) if i > start else (0, 0)


def _parse_selectors_new(spec: str) -> tuple[list[_Selector], bool]:
    sels: list[_Selector] = []
    p = 0
    is_add = is_remove = False
    do_clear = False
    n = len(spec)
    while p < n:
        while p < n and spec[p] in " ,":
            p += 1
        if p >= n:
            break
        if spec[p] == "-":
            is_add, is_remove = False, True
            p += 1
        elif spec[p] == "+":
            is_add, is_remove = True, False
            p += 1
        while p < n and spec[p] == " ":
            p += 1
        if p >= n:
            break
        if not is_add and not is_remove:
            do_clear = True
            is_add = True
        if spec[p] == "[":
            p += 1
            escaped = False
            closed = False
            while p < n:
                ch = spec[p]
                if ch == "\\" and not escaped:
                    escaped = True
                    p += 1
                    continue
                if ch == "]" and not escaped:
                    closed = True
                    p += 1
                    break
                sels.append(_Selector("range", is_add, first=ord(ch), last=ord(ch)))
                escaped = False
                p += 1
            if not closed:
                raise ValueError("Syntax error in symbol selector set.")
            continue
        m = re.match(r"[a-zA-Z0-9.]+", spec[p:])
        if not m:
            raise ValueError("Syntax error in symbol tag selectors.")
        token = m.group(0)
        p += len(token)
        low = token.lower()
        hit = next((name for name in TAG_NAMES if name.startswith(low)), None)
        if hit is not None:
            sels.append(_Selector("tag", is_add, tags=TAG_NAMES[hit]))
            continue
        val, consumed = _parse_code_point(token)
        if not consumed:
            raise ValueError(f"Unrecognized symbol tag '{token}'.")
        rest = token[consumed:]
        if rest:
            if len(rest) < 3 or not rest.startswith(".."):
                raise ValueError(f"Unrecognized symbol tag '{token}'.")
            val2, consumed2 = _parse_code_point(rest[2:])
            if not consumed2 or consumed2 + 2 != len(rest):
                raise ValueError(f"Unrecognized symbol tag '{token}'.")
            last = val2
        else:
            last = val
        sels.append(_Selector("range", is_add, first=val, last=last))
    return sels, do_clear


def char_is_selected(c: int, tags: int, selectors: list[_Selector]) -> bool:
    ch = chr(c)
    if c == 0x09 or not ch.isprintable() or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return False
    if unicodedata.bidirectional(ch) in ("R", "AL"):
        return False
    auto_exclude = TAG_BAD
    selected = False
    for sel in selectors:
        if sel.kind == "tag":
            if tags & sel.tags:
                selected = sel.additive
                auto_exclude &= ~sel.tags
        elif sel.first <= c <= sel.last:
            selected = sel.additive
    if tags & auto_exclude:
        selected = False
    return selected


def apply_selectors(existing: list[_Selector], spec: str) -> list[_Selector]:
    new, do_clear = _parse_selectors_new(spec)
    return new if do_clear else existing + new


def build_symbol_map(selectors: list[_Selector]) -> list[Symbol]:
    out = [s for cp, s in sorted(all_symbols().items()) if char_is_selected(cp, s.tags, selectors)]
    out.sort(key=lambda s: (s.popcount, s.c))
    return out


# Dithering (chafa-dither.c / chafa-util.c)

BLUE_NOISE_B64 = (
    "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"
    "VC7OfP6U0="
)

_TEXTURE_DIM = 16


def _fill_bayer(matrix: list[list[int]], size: int, sub: int, x: int, y: int, value: int, step: int) -> None:
    if sub == 1:
        matrix[y][x] = value
        return
    half = sub // 2
    _fill_bayer(matrix, size, half, x, y, value, step * 4)
    _fill_bayer(matrix, size, half, x + half, y + half, value + step, step * 4)
    _fill_bayer(matrix, size, half, x + half, y, value + step * 2, step * 4)
    _fill_bayer(matrix, size, half, x, y + half, value + step * 3, step * 4)


def gen_bayer_matrix(size: int, magnitude: float) -> np.ndarray:
    matrix = [[0] * size for _ in range(size)]
    _fill_bayer(matrix, size, size, 0, 0, 0, 1)
    maxval = float(size * size)
    out = np.empty((size, size), dtype=np.int32)
    for y in range(size):
        for x in range(size):
            out[y, x] = int((matrix[y][x] - maxval / 2.0) * (256.0 / maxval) * magnitude + 0.5)
    return out


def _load_blue_noise() -> np.ndarray:
    raw = zlib.decompress(base64.b64decode(BLUE_NOISE_B64))
    return np.frombuffer(raw, dtype=np.uint8).reshape(64, 64, 3).astype(np.int32)


@dataclass
class Dither:
    mode: str = "none"
    intensity: float = 1.0
    grain_w: int = 1
    grain_h: int = 1

    def __post_init__(self) -> None:
        self.gw_shift = {1: 0, 2: 1, 4: 2, 8: 3}[self.grain_w]
        self.gh_shift = {1: 0, 2: 1, 4: 2, 8: 3}[self.grain_h]
        if self.mode == "ordered":
            self.texture = gen_bayer_matrix(_TEXTURE_DIM, self.intensity)
        elif self.mode == "noise":
            mag = self.intensity * 0.1
            src = _load_blue_noise()
            self.texture = np.floor((src - 128.0) * mag + 0.5).astype(np.int32)
        else:
            self.texture = None


def apply_texture_dither(pix: np.ndarray, dither: Dither) -> None:
    h, w = pix.shape[:2]
    n = dither.texture.shape[0]
    mask = n - 1
    ys = (np.arange(h) >> dither.gh_shift) & mask
    xs = (np.arange(w) >> dither.gw_shift) & mask
    if dither.mode == "noise":
        mod = dither.texture[np.ix_(ys, xs)]
    else:
        mod = dither.texture[np.ix_(ys, xs)][..., None]
    pos = np.clip(mod, 0, 255)
    neg = np.clip(-mod, 0, 255)
    rgb = pix[..., :3].astype(np.int32)
    out = np.minimum(rgb + pos, 255)
    out = np.maximum(out - neg, 0)
    pix[..., :3] = out.astype(np.uint8)


def fs_dither(pix: np.ndarray, palette: Palette, dither: Dither) -> None:
    h, w = pix.shape[:2]
    gw, gh = dither.grain_w, dither.grain_h
    gshift = dither.gw_shift + dither.gh_shift
    wg = w // gw
    if wg * gw != w:
        wg = (w + gw - 1) // gw
    intensity = dither.intensity
    rgb = pix[..., :3].astype(np.int64)
    alpha = pix[..., 3].astype(np.int64)
    err_rows = [np.zeros((wg + 2, 3), dtype=np.int64), np.zeros((wg + 2, 3), dtype=np.int64)]
    cur = 0
    for gy in range(0, h, gh):
        nxt = 1 - cur
        err_rows[nxt][:] = 0
        forward = (gy // gh) % 2 == 0
        cols = range(wg) if forward else range(wg - 1, -1, -1)
        for gx in cols:
            x0, y0 = gx * gw, gy
            x1, y1 = min(x0 + gw, w), min(y0 + gh, h)
            if alpha[y0:y1, x0:x1].sum() == 0:
                continue
            err_in = err_rows[cur][gx + 1]
            next_err = np.zeros(3, dtype=np.int64)
            block = rgb[y0:y1, x0:x1]
            npx = block.shape[0] * block.shape[1]
            acc = np.zeros(3, dtype=np.int64)
            adj = block.astype(np.int64) + err_in
            lo = adj < 0
            hi = adj > 255
            next_err += np.where(lo, adj, 0).reshape(-1, 3).sum(0)
            next_err += np.where(hi, adj - 255, 0).reshape(-1, 3).sum(0)
            adj = np.clip(adj, 0, 255)
            block[...] = adj
            acc = adj.reshape(-1, 3).sum(0)
            mean = acc // (npx if npx else 1)
            q = mean.reshape(1, 3).astype(np.uint8)
            pens = palette.lookup(q, np.array([255], dtype=np.uint8))
            col = palette.colors[pens[0]]
            next_err = ((next_err >> gshift) + np.trunc((acc - col) * intensity)).astype(np.int64)
            e7 = next_err * 7 // 16
            e1 = next_err * 1 // 16
            e5 = next_err * 5 // 16
            e3 = next_err - e7 - e1 - e5
            if forward:
                err_rows[cur][gx + 2] += e7
                err_rows[nxt][gx + 2] += e1
                err_rows[nxt][gx + 1] += e5
                err_rows[nxt][gx] += e3
            else:
                err_rows[cur][gx] += e7
                err_rows[nxt][gx] += e1
                err_rows[nxt][gx + 1] += e5
                err_rows[nxt][gx + 2] += e3
        cur = nxt
    pix[..., :3] = rgb.astype(np.uint8)


CELL_W = 10
CELL_H = 20


def _align_dim(align: str, size: int, dest: int) -> int:
    if size >= dest:
        return 0
    if align == "center":
        return (dest - size) // 2
    if align == "end":
        return dest - size
    return 0


def tuck_and_align(
    src_w: int, src_h: int, dest_w: int, dest_h: int, stretch: bool, align: str = "start"
) -> tuple[int, int, int, int]:
    if stretch:
        return 0, 0, dest_w, dest_h
    ratio = min(dest_w / src_w, dest_h / src_h)
    pw = min(math.ceil(src_w * ratio), dest_w)
    ph = min(math.ceil(src_h * ratio), dest_h)
    return _align_dim(align, pw, dest_w), _align_dim(align, ph, dest_h), pw, ph


def _round_up_mult(v: int, m: int) -> int:
    return ((v + m - 1) // m) * m


def boost_saturation(rgb: np.ndarray) -> None:
    f = rgb.astype(np.float32)
    p = np.sqrt(f[..., 0] ** 2 * 0.299 + f[..., 1] ** 2 * 0.587 + f[..., 2] ** 2 * 0.144)
    out = p[..., None] + (f - p[..., None]) * 2.0
    rgb[...] = np.clip(out, 0, 255).astype(np.uint8)


def _histogram_bounds(rgb: np.ndarray, alpha: np.ndarray, crop_pct: int) -> tuple[int, int]:
    op = alpha > 127
    if not op.any():
        return 0, INTENSITY_MAX - 1
    r = rgb[op, 0].astype(np.int64)
    g = rgb[op, 1].astype(np.int64)
    b = rgb[op, 2].astype(np.int64)
    v = r * 3 + g * 4 + b
    hist = np.bincount(v, minlength=INTENSITY_MAX)
    n_samples = hist.sum()
    crop = int(n_samples * crop_pct // 100)
    lo = 0
    t = crop
    for i in range(INTENSITY_MAX):
        t -= hist[i]
        if t <= 0:
            lo = i
            break
    hi = INTENSITY_MAX - 1
    t = crop
    for i in range(INTENSITY_MAX - 1, -1, -1):
        t -= hist[i]
        if t <= 0:
            hi = i
            break
    return lo, hi


def _normalize_rgb(rgb: np.ndarray, lo: int, hi: int) -> None:
    if lo == hi:
        return
    factor = ((INTENSITY_MAX - 1) * FIXED_MULT) // (hi - lo)
    lo8 = lo // 8
    f = rgb.astype(np.int64)
    out = (f - lo8) * factor // FIXED_MULT
    rgb[...] = np.clip(out, 0, 255).astype(np.uint8)


def prepare_work_pixels(img: Image.Image, cfg: Config, palette: Palette | None) -> np.ndarray:
    src = np.asarray(img)
    if src.shape[2] == 3:
        src = np.concatenate([src, np.full(src.shape[:2] + (1,), 255, dtype=np.uint8)], axis=2)
    src_h, src_w = src.shape[:2]
    dest_w, dest_h = cfg.width * SYMP_W, cfg.height * SYMP_H

    px, py, pw, ph = tuck_and_align(src_w, src_h, cfg.width * CELL_W, cfg.height * CELL_H, cfg.stretch, cfg.align)
    px -= px % CELL_W
    py -= py % CELL_H
    pw = _round_up_mult(pw, CELL_W)
    ph = _round_up_mult(ph, CELL_H)
    px = px // CELL_W * SYMP_W
    py = py // CELL_H * SYMP_H
    pw = min(pw // CELL_W * SYMP_W, dest_w - px)
    ph = min(ph // CELL_H * SYMP_H, dest_h - py)

    resample = Image.Resampling.NEAREST if cfg.work_factor_int < 3 else Image.Resampling.BOX
    work = np.empty((dest_h, dest_w, 4), dtype=np.uint8)
    work[..., :3] = cfg.bg_color
    work[..., 3] = 0
    if pw > 0 and ph > 0:
        scaled = img.resize((pw, ph), resample)
        a = np.asarray(scaled)
        if a.shape[2] == 3:
            a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, dtype=np.uint8)], axis=2)
        work[py : py + ph, px : px + pw] = a

    alpha = work[..., 3:4].astype(np.float32)
    rgbf = work[..., :3].astype(np.float32)
    bgf = np.array(cfg.bg_color, dtype=np.float32)
    flat = rgbf * (alpha / 255.0) + bgf * ((255.0 - alpha) / 255.0)
    flat = np.rint(flat)
    flat[alpha[..., 0] == 0] = cfg.bg_color
    work[..., :3] = np.clip(flat, 0, 255).astype(np.uint8)

    mode = cfg.mode
    boost = cfg.preprocessing and mode in (Mode.INDEXED_16, Mode.INDEXED_16_8, Mode.INDEXED_8)
    if boost:
        boost_saturation(work[..., :3])

    lo = hi = 0
    if cfg.preprocessing:
        if mode in (Mode.INDEXED_16, Mode.INDEXED_16_8):
            crop = CROP_PCT_16
        elif mode == Mode.INDEXED_8:
            crop = CROP_PCT_8
        else:
            crop = CROP_PCT_2
        lo, hi = _histogram_bounds(work[..., :3], work[..., 3], crop)

    normalize = cfg.preprocessing and mode in (
        Mode.INDEXED_16,
        Mode.INDEXED_16_8,
        Mode.INDEXED_8,
        Mode.FGBG,
        Mode.FGBG_BGFG,
    )
    if normalize:
        _normalize_rgb(work[..., :3], lo, hi)

    dither = Dither(cfg.dither_mode, cfg.dither_intensity, cfg.dither_grain_w, cfg.dither_grain_h)
    if dither.mode in ("ordered", "noise"):
        apply_texture_dither(work, dither)

    if palette is not None and cfg.color_space == "din99d":
        work[..., :3] = rgb_to_din99d(work[..., :3])

    if dither.mode == "diffusion":
        if palette is not None:
            fs_dither(work, palette, dither)
    return work


def _pack_colors(rgb: np.ndarray, a: np.ndarray) -> np.ndarray:
    r = rgb[:, 0].astype(np.int64) & 0xFF
    g = rgb[:, 1].astype(np.int64) & 0xFF
    b = rgb[:, 2].astype(np.int64) & 0xFF
    return ((r << 16) | (g << 8) | b | ((a.astype(np.int64) & 0xFF) << 24)).astype(np.uint32)


def _closest_popcount(sorted_syms: list[Symbol], target: int) -> int:
    pops = [s.popcount for s in sorted_syms]
    if not pops:
        return -1
    i = 0
    j = len(pops) - 1
    while i < j:
        k = (i + j + 1) // 2
        if target < pops[k]:
            j = k - 1
        else:
            i = k
    if i + 1 < len(pops) and abs(target - pops[i + 1]) < abs(target - pops[i]):
        i += 1
    return i


def _find_blank_char(symbols: list[Symbol], fill_symbols: list[Symbol]) -> int:
    if any(s.c == 0x20 for s in symbols) or any(s.c == 0x20 for s in fill_symbols):
        return 0x20
    for m in (fill_symbols, symbols):
        if m:
            return m[_closest_popcount(m, 0)].c
    return 0x20


def _find_solid_char(symbols: list[Symbol], fill_symbols: list[Symbol]) -> int:
    if any(s.c == 0x2588 for s in symbols) or any(s.c == 0x2588 for s in fill_symbols):
        return 0x2588
    if fill_symbols:
        i = _closest_popcount(fill_symbols, 64)
        if abs(64 - fill_symbols[i].popcount) <= 32:
            return fill_symbols[i].c
    if symbols:
        best = max(range(len(symbols)), key=lambda i: (symbols[i].popcount, -symbols[i].c))
        if 64 - symbols[best].popcount <= 32:
            return symbols[best].c
    return 0


class CellGrid:
    def __init__(self, chars: np.ndarray, fg: np.ndarray, bg: np.ndarray):
        self.chars = chars
        self.fg = fg
        self.bg = bg


def render_symbol_cells(
    work: np.ndarray,
    symbols: list[Symbol],
    fill_symbols: list[Symbol],
    cfg: Config,
    fg_palette: Palette | None,
    bg_palette: Palette | None,
) -> CellGrid:
    R, C = cfg.height, cfg.width
    N = R * C
    mode = cfg.mode
    blank_char = _find_blank_char(symbols, fill_symbols)
    solid_char = _find_solid_char(symbols, fill_symbols)
    consider_inverted = not (cfg.fg_only or mode == Mode.FGBG)
    use_quant_err = mode == Mode.INDEXED_16_8 and not cfg.fg_only
    fixed_colors = cfg.fg_only or mode == Mode.FGBG

    cells = work.reshape(R, SYMP_H, C, SYMP_W, 4).transpose(0, 2, 1, 3, 4).reshape(N, SYMP_N, 4)
    P = cells[:, :, :3].astype(np.float64)
    A = cells[:, :, 3].astype(np.float64)
    P2 = P * P
    Q = P2.sum(axis=1)
    Stot = P.sum(axis=1)
    Atot = A.sum(axis=1)

    fg_rgb = np.array(cfg.fg_color, dtype=np.uint8).reshape(1, 3)
    bg_rgb = np.array(cfg.bg_color, dtype=np.uint8).reshape(1, 3)
    if fg_palette is not None and cfg.color_space == "din99d":
        fg_def = rgb_to_din99d(fg_rgb)[0].astype(np.int64)
        bg_def = rgb_to_din99d(bg_rgb)[0].astype(np.int64)
    else:
        fg_def = fg_rgb[0].astype(np.int64)
        bg_def = bg_rgb[0].astype(np.int64)
    if cfg.extract_colors and cfg.fg_only:
        fg_def = np.array([127, 127, 127], dtype=np.int64)
        for ch in range(3):
            diff = bg_def[ch] - fg_def[ch]
            if -5 <= diff <= 0:
                bg_def[ch] = max(fg_def[ch] - 5, 0)
            elif 0 <= diff <= 5:
                bg_def[ch] = min(fg_def[ch] + 5, 255)

    best_err = np.full(N, np.inf)
    best_sym = np.zeros(N, dtype=np.int64)
    S = len(symbols)

    if S:
        masks = np.stack([s.cov.reshape(-1) for s in symbols]).astype(np.float64)
        w = np.array([s.fg_weight for s in symbols], dtype=np.int64)
        bw = np.array([s.bg_weight for s in symbols], dtype=np.int64)
        CHUNK = 64
        for c0 in range(0, S, CHUNK):
            M = masks[c0 : c0 + CHUNK]
            s_n = M.shape[0]
            ww = w[c0 : c0 + CHUNK]
            bww = bw[c0 : c0 + CHUNK]
            Son = np.tensordot(P, M, axes=([1], [1]))
            Qon = np.tensordot(P2, M, axes=([1], [1]))
            Aon = A @ M.T

            ww_safe = np.where(ww > 1, ww, 1)
            bww_safe = np.where(bww > 1, bww, 1)
            fg_mean = np.where(ww > 1, Son // ww_safe, Son)
            bg_mean = np.where(bww > 1, (Stot[:, :, None] - Son) // bww_safe, Stot[:, :, None] - Son)
            fg_amean = np.where(ww > 1, Aon // ww_safe, Aon)
            bg_amean = np.where(bww > 1, (Atot[:, None] - Aon) // bww_safe, Atot[:, None] - Aon)

            if fixed_colors:
                fgcol = np.broadcast_to(fg_def, (N, s_n, 3))
                bgcol = np.broadcast_to(bg_def, (N, s_n, 3))
            elif use_quant_err:
                pf = fg_palette.lookup(
                    fg_mean.reshape(-1, 3).astype(np.uint8),
                    fg_amean.reshape(-1).astype(np.uint8),
                ).reshape(N, s_n)
                pb = bg_palette.lookup(
                    bg_mean.reshape(-1, 3).astype(np.uint8),
                    bg_amean.reshape(-1).astype(np.uint8),
                ).reshape(N, s_n)
                fgcol = fg_palette.color_of(pf)
                bgcol = bg_palette.color_of(pb)
            else:
                fgcol = np.swapaxes(fg_mean, 1, 2)
                bgcol = np.swapaxes(bg_mean, 1, 2)

            q_on = Qon.sum(axis=1)
            fg_dot = np.einsum("nsc,ncs->ns", fgcol, Son)
            fg_sq = (fgcol * fgcol).sum(axis=2)
            err_fg = q_on - 2 * fg_dot + ww * fg_sq
            q_off = Q.sum(axis=1)[:, None] - q_on
            Soff = Stot[:, :, None] - Son
            bg_dot = np.einsum("nsc,ncs->ns", bgcol, Soff)
            bg_sq = (bgcol * bgcol).sum(axis=2)
            err = err_fg + (q_off - 2 * bg_dot + bww * bg_sq)

            chunk_min = err.min(axis=1)
            take = chunk_min < best_err
            if take.any():
                rows = np.nonzero(take)[0]
                pos = err[rows].argmin(axis=1)
                best_err[rows] = chunk_min[rows]
                best_sym[rows] = c0 + pos

    if S:
        win_masks = masks[best_sym]
        Son_w = np.einsum("np,npc->nc", win_masks, P)
        Aon_w = (A * win_masks).sum(axis=1)
        ww_w = w[best_sym]
        bww_w = bw[best_sym]
        ww_w_safe = np.where(ww_w > 1, ww_w, 1)
        bww_w_safe = np.where(bww_w > 1, bww_w, 1)
        fg_mean_w = np.where(ww_w[:, None] > 1, Son_w // ww_w_safe[:, None], Son_w)
        bg_sum_w = Stot - Son_w
        bg_mean_w = np.where(bww_w[:, None] > 1, bg_sum_w // bww_w_safe[:, None], bg_sum_w)
        fg_a_w = np.where(ww_w > 1, Aon_w // ww_w_safe, Aon_w)
        bg_a_w = np.where(bww_w > 1, (Atot - Aon_w) // bww_w_safe, Atot - Aon_w)
        chars = np.array([s.c for s in symbols], dtype=np.int64)[best_sym]
    else:
        fg_mean_w = np.zeros((N, 3))
        bg_mean_w = np.zeros((N, 3))
        fg_a_w = np.zeros(N)
        bg_a_w = np.zeros(N)
        chars = np.full(N, blank_char, dtype=np.int64)
        best_sym = None

    truecolor = mode == Mode.TRUECOLOR
    if truecolor:
        fg = _pack_colors(fg_mean_w, fg_a_w)
        bg = _pack_colors(bg_mean_w, bg_a_w)
    elif mode == Mode.FGBG:
        fg = np.full(N, FG_PEN, dtype=np.int32)
        bg = np.full(N, BG_PEN, dtype=np.int32)
    else:
        fg = fg_palette.lookup(fg_mean_w.astype(np.uint8), fg_a_w.astype(np.uint8))
        bg = fg_palette.lookup(bg_mean_w.astype(np.uint8), bg_a_w.astype(np.uint8))
        if mode == Mode.INDEXED_16_8:
            fg16 = fg
            bg16 = bg
            same = (fg16 == bg16) & (fg16 >= 8) & (fg16 <= 15)
            bg8_fg = bg_palette.lookup(fg_mean_w.astype(np.uint8), fg_a_w.astype(np.uint8))
            bg8_bg = bg_palette.lookup(bg_mean_w.astype(np.uint8), bg_a_w.astype(np.uint8))
            bg = np.where(same, bg8_bg, bg8_bg)
            if solid_char and same.any():
                chars[same] = solid_char
                bg[same] = bg8_fg[same]
    if cfg.fg_only:
        bg = np.full(N, 0x00808080, dtype=np.uint32) if truecolor else np.full(N, TRANSPARENT, dtype=np.int32)

    if S:
        if truecolor:
            featureless = (chars == 0x20) | (chars == 0x2588) | (fg == bg)
        else:
            featureless = (chars == 0x20) | (chars == 0x2588) | (fg == bg)
        fidx = np.nonzero(featureless)[0]
        if fidx.size and fill_symbols:
            if cfg.fg_only:
                _apply_fill_fg_only(
                    fidx,
                    chars,
                    fg,
                    bg,
                    fill_symbols,
                    cfg,
                    fg_palette,
                    Stot,
                    Atot,
                    fg_def,
                    bg_def,
                )
            else:
                _apply_fill(
                    fidx,
                    chars,
                    fg,
                    bg,
                    fill_symbols,
                    cfg,
                    fg_palette,
                    bg_palette,
                    Stot,
                    Atot,
                    consider_inverted,
                    mode,
                    truecolor,
                    solid_char,
                )
        if truecolor:
            still = (chars == 0x20) | (fg == bg)
        else:
            still = (chars == 0x20) | (fg == bg)
        chars[still] = blank_char
        if blank_char == 0x20:
            fg2 = fg.reshape(R, C)
            still2 = still.reshape(R, C)
            for cx in range(1, C):
                col = still2[:, cx]
                if col.any():
                    fg2[col, cx] = fg2[col, cx - 1]
                    if truecolor:
                        f = fg2[col, cx]
                        fg2[col, cx] = f | np.uint32(0xFF000000)
                    else:
                        f = fg2[col, cx]
                        trans = f == TRANSPARENT
                        if trans.any():
                            fg2[col, cx] = np.where(trans, FG_PEN, f)

    return CellGrid(chars, fg, bg)


def _apply_fill(
    fidx,
    chars,
    fg,
    bg,
    fill_symbols,
    cfg,
    fg_palette,
    bg_palette,
    Stot,
    Atot,
    consider_inverted,
    mode,
    truecolor,
    solid_char,
) -> None:
    mean_rgb = (Stot[fidx] // 64).astype(np.int64)
    mean_a = Atot[fidx] // 64
    M = fidx.size
    if truecolor:
        fg[fidx] = _pack_colors(mean_rgb, mean_a)
        bg[fidx] = fg[fidx]
        idx = _closest_popcount(fill_symbols, 0)
        chars[fidx] = fill_symbols[idx].c
        return
    if mode in PALETTE_MODES:
        cand0, cand1 = fg_palette.lookup2(mean_rgb.astype(np.uint8), mean_a.astype(np.uint8))
        if mode == Mode.INDEXED_16_8:
            b0, b1 = bg_palette.lookup2(mean_rgb.astype(np.uint8), mean_a.astype(np.uint8))
            diff = cand0 != b0
            cand1 = np.where(diff & (cand1 == b0), b1, cand1)
            cand0 = np.where(diff, b0, cand0)
    else:
        cand0 = np.full(M, FG_PEN, dtype=np.int32)
        cand1 = np.full(M, BG_PEN, dtype=np.int32)

    col0 = fg_palette.color_of(cand0).astype(np.int64)
    col1 = fg_palette.color_of(cand1).astype(np.int64)
    a0 = np.where(cand0 == TRANSPARENT, 0, 255)
    a1 = np.where(cand1 == TRANSPARENT, 0, 255)
    if mode in FGBG_MODES:
        a1 = np.full(M, 255)
    steps = np.arange(65, dtype=np.int64)
    c0_4 = np.concatenate([col0, a0[:, None]], axis=1)
    c1_4 = np.concatenate([col1, a1[:, None]], axis=1)
    mix = (c0_4[:, None, :] * (64 - steps)[None, :, None] + c1_4[:, None, :] * steps[None, :, None]) // 64
    err = ((mean_rgb[:, None, :] - mix[..., :3]) ** 2).sum(axis=2)
    best_i = err.argmin(axis=1)
    if mode == Mode.FGBG:
        best_i = 64 - best_i

    idx = np.empty(M, dtype=np.int64)
    inverted = np.zeros(M, dtype=bool)
    for j in range(M):
        i0 = _closest_popcount(fill_symbols, int(best_i[j]))
        d0 = abs(int(best_i[j]) - fill_symbols[i0].popcount)
        k = i0
        inv = False
        if consider_inverted and mode != Mode.INDEXED_16_8 and d0 != 0:
            i1 = _closest_popcount(fill_symbols, 64 - int(best_i[j]))
            d1 = abs(64 - int(best_i[j]) - fill_symbols[i1].popcount)
            if d1 < d0:
                k, inv = i1, True
        idx[j], inverted[j] = k, inv
    chars[fidx] = [fill_symbols[i].c for i in idx]
    if mode != Mode.FGBG and mode != Mode.INDEXED_16_8:
        cand1 = np.where(best_i == 0, cand0, cand1)
        cand0 = np.where(best_i == 64, cand1, cand0)
    f_pen = np.where(inverted, cand0, cand1)
    b_pen = np.where(inverted, cand1, cand0)
    fg[fidx] = f_pen
    bg[fidx] = b_pen


def _apply_fill_fg_only(
    fidx,
    chars,
    fg,
    bg,
    fill_symbols,
    cfg,
    fg_palette,
    Stot,
    Atot,
    fg_def,
    bg_def,
) -> None:
    mean_rgb = (Stot[fidx] // 64).astype(np.int64)
    mean_a = Atot[fidx] // 64
    if cfg.mode == Mode.TRUECOLOR:
        fg[fidx] = _pack_colors(mean_rgb, mean_a)
    else:
        fg[fidx] = fg_palette.lookup(mean_rgb.astype(np.uint8), mean_a.astype(np.uint8))
    bg[fidx] = np.uint32(0x00808080) if cfg.mode == Mode.TRUECOLOR else TRANSPARENT
    fg_val = int(brightness(fg_def.reshape(1, 3), cfg.color_space)[0])
    bg_val = int(brightness(bg_def.reshape(1, 3), cfg.color_space)[0])
    mean_val = brightness(mean_rgb.astype(np.uint8), cfg.color_space)
    n_bits = (mean_val * 64 + 128) // 255
    if fg_val < bg_val:
        n_bits = 64 - n_bits
    for j, cell in enumerate(fidx):
        k = _closest_popcount(fill_symbols, int(n_bits[j]))
        chars[cell] = fill_symbols[k].c


def _aix16fg(pen: int) -> int:
    return pen + (30 if pen < 8 else 90 - 8)


def _aix16bg(pen: int) -> int:
    return pen + (40 if pen < 8 else 100 - 8)


class _PrintCtx:
    __slots__ = ("cur_char", "n_reps", "cur_inverted", "cur_bold", "cur_fg", "cur_bg", "parts")

    def __init__(self) -> None:
        self.cur_char = 0
        self.n_reps = 0
        self.cur_inverted = False
        self.cur_bold = False
        self.cur_fg = TRANSPARENT
        self.cur_bg = TRANSPARENT
        self.parts: list[str] = []

    def queue(self, c: int) -> None:
        if self.cur_char == c:
            self.n_reps += 1
        else:
            self.flush()
            self.cur_char = c
            self.n_reps = 1

    def flush(self) -> None:
        if self.cur_char:
            self.parts.append(chr(self.cur_char) * self.n_reps)
            self.cur_char = 0
            self.n_reps = 0

    def reset_attrs(self) -> None:
        self.parts.append(SEQ_RESET_ATTRS)
        self.cur_inverted = False
        self.cur_bold = False
        self.cur_fg = TRANSPARENT
        self.cur_bg = TRANSPARENT

    def reset_fg(self) -> None:
        self.parts.append(SEQ_RESET_FG)
        self.cur_fg = TRANSPARENT


def _emit_attrs(ctx: _PrintCtx, fg, bg, inverted, bold, mode: Mode, fg_only: bool):
    if not fg_only:
        if (
            (ctx.cur_inverted and not inverted)
            or (ctx.cur_bold and not bold)
            or (ctx.cur_fg != TRANSPARENT and fg == TRANSPARENT)
            or (ctx.cur_bg != TRANSPARENT and bg == TRANSPARENT)
        ):
            ctx.flush()
            ctx.reset_attrs()
        if not ctx.cur_inverted and inverted:
            ctx.flush()
            ctx.parts.append(SEQ_INVERT)
        if not ctx.cur_bold and bold:
            ctx.flush()
            ctx.parts.append(SEQ_BOLD)

    if fg != ctx.cur_fg:
        if bg != ctx.cur_bg and bg != TRANSPARENT:
            ctx.flush()
            ctx.parts.append(_sgr_fgbg(mode, fg, bg))
        elif fg != TRANSPARENT:
            ctx.flush()
            ctx.parts.append(_sgr_fg(mode, fg))
    elif bg != ctx.cur_bg and bg != TRANSPARENT:
        ctx.flush()
        ctx.parts.append(_sgr_bg(mode, bg))
    ctx.cur_fg = fg
    ctx.cur_bg = bg
    ctx.cur_inverted = inverted
    ctx.cur_bold = bold


def _unpack(pen):
    pen = int(pen)
    return (pen >> 16) & 0xFF, (pen >> 8) & 0xFF, pen & 0xFF, (pen >> 24) & 0xFF


def _pen_transparent(mode: Mode, pen) -> bool:
    if mode == Mode.TRUECOLOR:
        return (int(pen) >> 24) == 0
    return int(pen) == TRANSPARENT


def _sgr_fg(mode: Mode, pen) -> str:
    if mode == Mode.TRUECOLOR:
        r, g, b, _ = _unpack(pen)
        return f"{ESC}[38;2;{r};{g};{b}m"
    if mode in (Mode.INDEXED_256, Mode.INDEXED_240):
        return f"{ESC}[38;5;{int(pen)}m"
    if mode == Mode.INDEXED_16_8:
        return f"{ESC}[{(int(pen) & 7) + 30}m"
    return f"{ESC}[{_aix16fg(int(pen))}m"


def _sgr_bg(mode: Mode, pen) -> str:
    if mode == Mode.TRUECOLOR:
        r, g, b, _ = _unpack(pen)
        return f"{ESC}[48;2;{r};{g};{b}m"
    if mode in (Mode.INDEXED_256, Mode.INDEXED_240):
        return f"{ESC}[48;5;{int(pen)}m"
    if mode == Mode.INDEXED_16_8:
        return f"{ESC}[{int(pen) + 40}m"
    return f"{ESC}[{_aix16bg(int(pen))}m"


def _sgr_fgbg(mode: Mode, fg, bg) -> str:
    if mode == Mode.TRUECOLOR:
        fr, fgc, fb, _ = _unpack(fg)
        br, bgc, bb, _ = _unpack(bg)
        return f"{ESC}[38;2;{fr};{fgc};{fb};48;2;{br};{bgc};{bb}m"
    if mode in (Mode.INDEXED_256, Mode.INDEXED_240):
        return f"{ESC}[38;5;{int(fg)};48;5;{int(bg)}m"
    if mode == Mode.INDEXED_16_8:
        return f"{ESC}[{(int(fg) & 7) + 30};{int(bg) + 40}m"
    return f"{ESC}[{_aix16fg(int(fg))};{_aix16bg(int(bg))}m"


def _emit_attrs(ctx: _PrintCtx, fg, bg, inverted, bold, mode: Mode, fg_only: bool) -> None:
    trans = _pen_transparent(mode, fg)
    trans_bg = _pen_transparent(mode, bg)
    if not fg_only:
        if (
            (ctx.cur_inverted and not inverted)
            or (ctx.cur_bold and not bold)
            or (not _pen_transparent(mode, ctx.cur_fg) and trans)
            or (not _pen_transparent(mode, ctx.cur_bg) and trans_bg)
        ):
            ctx.flush()
            ctx.reset_attrs()
        if not ctx.cur_inverted and inverted:
            ctx.flush()
            ctx.parts.append(SEQ_INVERT)
        if not ctx.cur_bold and bold:
            ctx.flush()
            ctx.parts.append(SEQ_BOLD)
    if fg != ctx.cur_fg:
        if bg != ctx.cur_bg and not trans_bg:
            ctx.flush()
            ctx.parts.append(_sgr_fgbg(mode, fg, bg))
        elif not trans:
            ctx.flush()
            ctx.parts.append(_sgr_fg(mode, fg))
    elif bg != ctx.cur_bg and not trans_bg:
        ctx.flush()
        ctx.parts.append(_sgr_bg(mode, bg))
    ctx.cur_fg = fg
    ctx.cur_bg = bg
    ctx.cur_inverted = inverted
    ctx.cur_bold = bold


def print_symbol_rows(grid: CellGrid, cfg: Config, symbols: list[Symbol], fill_symbols: list[Symbol]) -> str:
    R, C = cfg.height, cfg.width
    mode = cfg.mode
    chars = grid.chars.reshape(R, C)
    fg = grid.fg.reshape(R, C)
    bg = grid.bg.reshape(R, C)
    fg_only = cfg.fg_only
    have_space = any(s.c == 0x20 for s in symbols) or any(s.c == 0x20 for s in fill_symbols)
    have_solid = any(s.c == 0x2588 for s in symbols) or any(s.c == 0x2588 for s in fill_symbols)
    blank_symbol = 0x20 if have_space else (0x2588 if have_solid else 0)

    ctx = _PrintCtx()
    lines: list[str] = []
    for row in range(R):
        if row == 0 and mode != Mode.FGBG:
            if fg_only:
                ctx.reset_fg()
            else:
                ctx.reset_attrs()
        for col in range(C):
            c = int(chars[row, col])
            f, b = fg[row, col], bg[row, col]
            if mode == Mode.TRUECOLOR:
                fa = (int(f) >> 24) & 0xFF
                ba = (int(b) >> 24) & 0xFF
                fa = 0 if fa < cfg.alpha_threshold else 255
                ba = 0 if ba < cfg.alpha_threshold else 255
                f = (int(f) & 0x00FFFFFF) | (fa << 24)
                b = (int(b) & 0x00FFFFFF) | (ba << 24)
                if fa == 0 and ba != 0:
                    _emit_attrs(ctx, b, f, True, False, mode, fg_only)
                else:
                    _emit_attrs(ctx, f, b, False, False, mode, fg_only)
                if fa == 0 and ba == 0:
                    ctx.queue(0x20)
                else:
                    ctx.queue(c)
            elif mode in (Mode.INDEXED_256, Mode.INDEXED_240, Mode.INDEXED_16, Mode.INDEXED_8):
                f, b = int(f), int(b)
                if f == TRANSPARENT and b != TRANSPARENT:
                    _emit_attrs(ctx, b, f, True, False, mode, fg_only)
                else:
                    _emit_attrs(ctx, f, b, False, False, mode, fg_only)
                if f == TRANSPARENT and b == TRANSPARENT:
                    ctx.queue(0x20)
                else:
                    ctx.queue(c)
            elif mode == Mode.INDEXED_16_8:
                f, b = int(f), int(b)
                bold = 8 <= f < 256
                if f == TRANSPARENT and b != TRANSPARENT:
                    _emit_attrs(ctx, b, f, True, bold, mode, fg_only)
                else:
                    _emit_attrs(ctx, f, b, False, bold, mode, fg_only)
                if f == TRANSPARENT and b == TRANSPARENT:
                    ctx.queue(0x20)
                else:
                    ctx.queue(c)
            elif mode == Mode.FGBG_BGFG:
                invert = False
                cc = c
                if f == b and blank_symbol and (col == C - 1 or int(chars[row, col + 1]) != 0):
                    cc = blank_symbol
                    if blank_symbol == 0x2588:
                        invert = True
                if int(b) == FG_PEN:
                    invert = not invert
                if not ctx.cur_inverted and invert:
                    ctx.flush()
                    ctx.parts.append(SEQ_INVERT)
                elif ctx.cur_inverted and not invert:
                    ctx.flush()
                    ctx.reset_attrs()
                ctx.cur_inverted = invert
                ctx.queue(cc)
            else:
                ctx.queue(c)
        ctx.flush()
        if mode != Mode.FGBG:
            if fg_only:
                ctx.reset_fg()
            else:
                ctx.reset_attrs()
        lines.append("".join(ctx.parts))
        ctx.parts = []
    return "\n".join(lines)


def _scaled_placement(img: Image.Image, cfg: Config, W: int, H: int):
    src = np.asarray(img)
    src_h, src_w = src.shape[:2]
    px, py, pw, ph = tuck_and_align(src_w, src_h, W, H, cfg.stretch, cfg.align)
    resample = Image.Resampling.NEAREST if cfg.work_factor_int < 3 else Image.Resampling.BOX
    canvas = np.zeros((H, W, 4), dtype=np.uint8)
    if pw > 0 and ph > 0:
        scaled = img.resize((pw, ph), resample)
        a = np.asarray(scaled)
        if a.shape[2] == 3:
            a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, dtype=np.uint8)], axis=2)
        canvas[py : py + ph, px : px + pw] = a
    if cfg.alpha_threshold < 1:
        alpha = canvas[..., 3:4].astype(np.float32)
        rgbf = canvas[..., :3].astype(np.float32)
        bgf = np.array(cfg.bg_color, dtype=np.float32)
        flat = rgbf * (alpha / 255.0) + bgf * ((255.0 - alpha) / 255.0)
        canvas[..., :3] = np.clip(np.rint(flat), 0, 255).astype(np.uint8)
        canvas[..., 3] = 255
    return canvas


def _rle_sixels(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        ch = data[i]
        run = 1
        while i + run < n and data[i + run] == ch:
            run += 1
        i += run
        while run > 0:
            if run < 4:
                out += bytes([ch]) * run
                run = 0
            else:
                k = min(run, 255)
                out += b"!" + str(k).encode() + bytes([ch])
                run -= k
    return bytes(out)


def _sixel_level(v: int) -> int:
    return (int(v) * 100 + 127) // 255


def render_sixel(img: Image.Image, cfg: Config) -> str:
    W = cfg.width * CELL_W
    H = cfg.height * CELL_H
    canvas = _scaled_placement(img, cfg, W, H)
    transparent = canvas[..., 3] < cfg.alpha_threshold
    rgb_img = Image.fromarray(canvas[..., :3])
    dither_flag = Image.Dither.FLOYDSTEINBERG if cfg.dither_mode != "none" else Image.Dither.NONE
    n_colors = {
        Mode.FGBG: 2,
        Mode.FGBG_BGFG: 2,
        Mode.INDEXED_8: 8,
        Mode.INDEXED_16_8: 16,
        Mode.INDEXED_16: 16,
        Mode.INDEXED_240: 240,
        Mode.INDEXED_256: 256,
        Mode.TRUECOLOR: 256,
    }[cfg.mode]
    q = rgb_img.quantize(colors=n_colors, method=Image.Quantize.MEDIANCUT, dither=dither_flag)
    indices = np.asarray(q).astype(np.int32)
    flat_pal = q.getpalette() or []
    n_pal = len(flat_pal) // 3
    pal = np.zeros((256, 3), dtype=np.uint8)
    for i in range(min(256, n_pal)):
        pal[i] = flat_pal[i * 3 : i * 3 + 3]

    H6 = _round_up_mult(H, 6)
    pad = H6 - H
    if pad:
        indices = np.concatenate([indices, np.zeros((pad, W), dtype=np.int32)])
        transparent = np.concatenate([transparent, np.ones((pad, W), dtype=bool)])
    row_bits = (1 << np.arange(6, dtype=np.int32)).reshape(6, 1)

    used = np.unique(indices[~transparent])
    out = [f"{ESC}P0;1;0q", f'"1;1;{W};{H}']
    for pen in used:
        r, g, b = pal[pen]
        out.append(f"#{pen};2;{_sixel_level(r)};{_sixel_level(g)};{_sixel_level(b)}")

    n_bands = H6 // 6
    for band in range(n_bands):
        y0 = band * 6
        bidx = indices[y0 : y0 + 6]
        btrans = transparent[y0 : y0 + 6]
        force_full = band == 0 or band == n_bands - 1
        first_flush = True
        runs: list[str] = []
        for pen in used:
            sel = (bidx == pen) & ~btrans
            colbits = (sel * row_bits).sum(axis=0)
            nz = np.nonzero(colbits)[0]
            if nz.size == 0:
                continue
            last_col = int(nz[-1])
            width = W if (force_full and first_flush) else last_col + 1
            data = (0x3F + colbits[:width]).astype(np.uint8).tobytes()
            runs.append(f"#{pen}" + _rle_sixels(data).decode("ascii"))
            first_flush = False
        if runs:
            out.append("$".join(runs))
        if band != n_bands - 1:
            out.append("-")
    out.append(ESC + "\\")
    return "".join(out)


def render_kitty(img: Image.Image, cfg: Config) -> str:
    W = cfg.width * CELL_W
    H = cfg.height * CELL_H
    canvas = _scaled_placement(img, cfg, W, H)
    payload = canvas.tobytes()
    parts = [f"{ESC}_Ga=T,f=32,s={W},v={H},c={cfg.width},r={cfg.height},m=1,q=2{ESC}\\"]
    CH = 510
    for i in range(0, len(payload), CH):
        chunk = base64.b64encode(payload[i : i + CH]).decode("ascii")
        parts.append(f"{ESC}_Gm=1;{chunk}{ESC}\\")
    parts.append(f"{ESC}_Gm=0{ESC}\\")
    return "".join(parts)


def render_iterm(img: Image.Image, cfg: Config) -> str:
    W = cfg.width * CELL_W
    H = cfg.height * CELL_H
    canvas = _scaled_placement(img, cfg, W, H)
    pil = Image.fromarray(canvas)
    buf = io.BytesIO()
    pil.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"{ESC}]1337;File=inline=1;width={cfg.width};height={cfg.height};preserveAspectRatio=0:{b64}\a"


def load_image(path: str) -> Image.Image:
    from pathlib import Path

    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        data = Path(path).read_bytes()
    img = Image.open(io.BytesIO(data))
    img.load()
    if getattr(img, "n_frames", 1) <= 1:
        img = ImageOps.exif_transpose(img)
    return img


def iter_frames(img: Image.Image, animate: bool):
    n = getattr(img, "n_frames", 1)
    if n <= 1 or not animate:
        frame = img.copy()
        if frame.mode != "RGBA":
            frame = frame.convert("RGBA")
        yield frame
        return
    for i in range(n):
        img.seek(i)
        frame = img.convert("RGBA")
        yield frame


_BRIEF = """usage: chafa.py [OPTION...] [FILE...]

Show images in your terminal. Try `chafa.py --help' for more information.
With no FILE, or when FILE is -, read standard input."""


def _parse_size(val: str, term_w: int, term_h: int) -> tuple[int, int]:
    val = val.strip().lower()
    if val == "auto":
        return term_w, term_h
    m = re.match(r"^(\d*)x(\d*)$", val)
    if not m or (not m.group(1) and not m.group(2)):
        raise ValueError("Size must be [width]x[height], e.g. 80x25, 80x or x25.")
    w = int(m.group(1)) if m.group(1) else 1 << 30
    h = int(m.group(2)) if m.group(2) else 1 << 30
    return w, h


def _parse_color(val: str, what: str) -> tuple[int, int, int]:
    v = val.strip()
    if re.match(r"^[0-9a-fA-F]{6}$", v):
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    try:
        from PIL import ImageColor

        r, g, b = ImageColor.getrgb(v)[:3]
        return int(r), int(g), int(b)
    except Exception:
        raise ValueError(f"Unrecognized {what} color '{val}'.") from None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="chafa.py",
        description="Show images in your terminal (single-file chafa clone).",
        epilog=(
            "examples:\n"
            "  chafa.py photo.jpg\n"
            "  chafa.py -c none --symbols ascii in.png      # old-school art\n"
            "  chafa.py -f sixels -c full in.jpg               # graphics terminal\n"
            "\n"
            "symbol classes for --symbols/--fill:\n"
            "  all none space solid stipple block border diagonal dot quad half\n"
            "  hhalf vhalf inverted braille sextant wedge technical geometric\n"
            "  ascii alpha digit narrow wide ambiguous ugly extra alnum bad\n"
            "  legacy latin import octant\n"
            "combine with + and -, e.g. block+border-diagonal or all-wide;\n"
            "raw characters in [..], code point ranges like 2500..25ff\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("files", nargs="*", metavar="FILE", help="image file, or - for stdin")
    p.add_argument("-s", "--size", metavar="WxH", help="output size in cells")
    p.add_argument("-c", "--colors", metavar="MODE", help=("none|2|8|16/8|16|240|256|full (default: auto)"))
    p.add_argument(
        "-f", "--format", metavar="FMT", help=("symbols|sixels|kitty|iterm|auto (default: symbols or auto-detected)")
    )
    p.add_argument("--symbols", metavar="SPEC", help="output symbols (see below)")
    p.add_argument("--fill", metavar="SPEC", help="fill symbols for gradients")
    p.add_argument("--color-space", choices=["rgb", "din99d"], help=("color distance metric (default: din99d)"))
    p.add_argument(
        "-w", "--work", type=int, metavar="1..9", help=("work factor; 1-2 uses nearest-neighbor scaling (default: 5)")
    )
    p.add_argument("--dither", choices=["none", "ordered", "diffusion", "noise"], help="dither mode (default: none)")
    p.add_argument("--dither-intensity", type=float, metavar="0..1", help="dither intensity (default: 1.0)")
    p.add_argument("--dither-grain", metavar="WxH", help="dither grain size, 1/2/4/8 (default: 4x4)")
    p.add_argument(
        "--no-preprocess",
        dest="preprocess",
        action="store_false",
        help="disable auto-contrast/saturation preprocessing",
    )
    p.add_argument(
        "-t", "--threshold", type=float, metavar="0..1", help="transparency threshold (default: 0.5; 1.0 = opaque)"
    )
    p.add_argument("--fg", metavar="COLOR", help="default foreground color")
    p.add_argument("--bg", metavar="COLOR", help="default background color")
    p.add_argument("--fg-only", action="store_true", help="never emit background colors")
    p.add_argument("--invert", action="store_true", help="swap foreground and background colors")
    p.add_argument("--stretch", action="store_true", help="stretch image to fill output dimensions")
    p.add_argument("--align", choices=["start", "center", "end"], help="horizontal alignment (default: start)")
    p.add_argument(
        "--animate",
        dest="animate",
        action="store_true",
        default=None,
        help="dump all frames of animated input (default)",
    )
    p.add_argument("--still", dest="animate", action="store_false", help="render only the first frame")
    p.add_argument("-V", "--version", action="version", version="chafa.py 1.0 (chafa clone)")
    return p


def _detect_format() -> str:
    term = os.environ.get("TERM", "")
    if os.environ.get("KITTY_WINDOW_ID") or term == "kitty":
        return "kitty"
    if os.environ.get("ITERM_SESSION_ID") or os.environ.get("TERM_PROGRAM", "") in ("iTerm.app", "WezTerm", "vscode"):
        return "iterm"
    return "symbols"


def _detect_colors() -> Mode:
    term = os.environ.get("TERM", "")
    if term in ("", "dumb", "unknown"):
        return Mode.FGBG
    return Mode.TRUECOLOR


def run(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    files = list(args.files)
    if not files and not sys.stdin.isatty():
        files = ["-"]
    if not files:
        print(_BRIEF, file=sys.stderr)
        return 1

    term = shutil.get_terminal_size(fallback=(80, 25))
    cfg = Config()
    cfg.width, cfg.height = term.columns, max(1, term.lines - 1)

    if args.size:
        try:
            cfg.width, cfg.height = _parse_size(args.size, term.columns, max(1, term.lines - 1))
        except ValueError as e:
            print(f"chafa.py: {e}", file=sys.stderr)
            return 1
    if cfg.width > (1 << 29):
        cfg.width = 1 << 29
    if cfg.height > (1 << 29):
        cfg.height = 1 << 29

    if args.colors:
        cmap = {
            "none": Mode.FGBG,
            "2": Mode.FGBG_BGFG,
            "8": Mode.INDEXED_8,
            "16/8": Mode.INDEXED_16_8,
            "16-8": Mode.INDEXED_16_8,
            "16": Mode.INDEXED_16,
            "240": Mode.INDEXED_240,
            "256": Mode.INDEXED_256,
            "full": Mode.TRUECOLOR,
            "rgb": Mode.TRUECOLOR,
            "tc": Mode.TRUECOLOR,
            "direct": Mode.TRUECOLOR,
            "directcolor": Mode.TRUECOLOR,
            "truecolor": Mode.TRUECOLOR,
        }
        try:
            cfg.mode = cmap[args.colors.lower()]
        except KeyError:
            print(f"chafa.py: Colors must be one of [none, 2, 8, 16/8, 16, 240, 256, full].", file=sys.stderr)
            return 1
    else:
        cfg.mode = _detect_colors()

    cfg.format = (args.format or _detect_format()).lower()
    if cfg.format in ("symbol", "ansi"):
        cfg.format = "symbols"
    if cfg.format in ("sixel", "sixels"):
        cfg.format = "sixels"
    if cfg.format in ("iterm", "iterm2", "iter"):
        cfg.format = "iterm"
    if cfg.format not in ("symbols", "sixels", "kitty", "iterm"):
        print(f"chafa.py: Format must be one of [symbols, sixels, kitty, iterm].", file=sys.stderr)
        return 1

    if args.color_space:
        cfg.color_space = args.color_space
    if args.work is not None:
        if not 1 <= args.work <= 9:
            print("chafa.py: Work factor must be in the range [1-9].", file=sys.stderr)
            return 1
        cfg.work_factor = args.work / 10.0
    if args.dither:
        cfg.dither_mode = args.dither
    if args.dither_intensity is not None:
        cfg.dither_intensity = args.dither_intensity
    if args.dither_grain:
        m = re.match(r"^([1248])x([1248])$", args.dither_grain.strip())
        if not m:
            print("chafa.py: Dither grain must be WxH with W, H in [1, 2, 4, 8].", file=sys.stderr)
            return 1
        cfg.dither_grain_w, cfg.dither_grain_h = int(m.group(1)), int(m.group(2))
    if not args.preprocess:
        cfg.preprocessing = False
    if args.threshold is not None:
        if not 0.0 <= args.threshold <= 1.0:
            print("chafa.py: Transparency threshold must be in [0.0, 1.0].", file=sys.stderr)
            return 1
        cfg.alpha_threshold = int(256 * (1.0 - args.threshold))
    else:
        cfg.alpha_threshold = 128
    try:
        if args.fg:
            cfg.fg_color = _parse_color(args.fg, "foreground")
        if args.bg:
            cfg.bg_color = _parse_color(args.bg, "background")
    except ValueError as e:
        print(f"chafa.py: {e}", file=sys.stderr)
        return 1
    if args.invert:
        cfg.fg_color, cfg.bg_color = cfg.bg_color, cfg.fg_color
    cfg.fg_only = args.fg_only or cfg.mode == Mode.FGBG
    if args.stretch:
        cfg.stretch = True
    if args.align:
        cfg.align = args.align
    if args.animate is not None:
        cfg.animate = args.animate

    if cfg.format != "symbols" or cfg.mode == Mode.TRUECOLOR:
        if cfg.format in ("kitty", "iterm") or cfg.mode == Mode.TRUECOLOR:
            cfg.color_space = "rgb"
            cfg.dither_mode = "none"

    fg_pal = bg_pal = None
    if cfg.mode in PALETTE_MODES or cfg.mode in FGBG_MODES:
        kind_map = {
            Mode.INDEXED_8: ("8", "8"),
            Mode.INDEXED_16_8: ("16", "8"),
            Mode.INDEXED_16: ("16", "16"),
            Mode.INDEXED_240: ("240", "240"),
            Mode.INDEXED_256: ("256", "256"),
            Mode.FGBG: ("fgbg", "fgbg"),
            Mode.FGBG_BGFG: ("fgbg", "fgbg"),
        }
        kf, kb = kind_map[cfg.mode]
        fg_pal = Palette(kf, cfg.color_space, cfg.mode, cfg.fg_color, cfg.bg_color, cfg.alpha_threshold)
        if kb == kf:
            bg_pal = fg_pal
        else:
            bg_pal = Palette(kb, cfg.color_space, cfg.mode, cfg.fg_color, cfg.bg_color, cfg.alpha_threshold)
        if cfg.dither_mode == "diffusion":
            cfg.dither_intensity = DITHER_BASE_INTENSITY[cfg.mode] * (
                args.dither_intensity if args.dither_intensity is not None else 1.0
            )

    selectors = apply_selectors([], "block+border")
    if args.symbols is not None:
        try:
            selectors = apply_selectors(selectors, args.symbols)
        except ValueError as e:
            print(f"chafa.py: {e}", file=sys.stderr)
            return 1
    else:
        if cfg.mode != Mode.FGBG:
            selectors = apply_selectors(selectors, "-inverted")
    try:
        if args.fill is not None:
            fill_selectors = apply_selectors([], args.fill)
            fill_symbols = build_symbol_map(fill_selectors)
        else:
            fill_symbols = []
        symbols = build_symbol_map(selectors)
    except ValueError as e:
        print(f"chafa.py: {e}", file=sys.stderr)
        return 1

    out_parts: list[str] = []
    for path in files:
        try:
            img = load_image(path)
        except Exception as e:
            print(f"chafa.py: Failed to load '{path}': {e}", file=sys.stderr)
            return 1
        frames = list(iter_frames(img, cfg.animate))
        for fi, frame in enumerate(frames):
            if cfg.format == "symbols":
                work = prepare_work_pixels(frame, cfg, fg_pal)
                grid = render_symbol_cells(work, symbols, fill_symbols, cfg, fg_pal, bg_pal)
                text = print_symbol_rows(grid, cfg, symbols, fill_symbols)
            elif cfg.format == "sixels":
                text = render_sixel(frame, cfg)
            elif cfg.format == "kitty":
                text = render_kitty(frame, cfg)
            else:
                text = render_iterm(frame, cfg)
            if fi > 0:
                out_parts.append("\f")
            out_parts.append(text)
        if len(files) > 1:
            out_parts.append("\n")
    out = "".join(out_parts)
    sys.stdout.write(out)
    if out and not out.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def main() -> None:
    try:
        sys.exit(run())
    except BrokenPipeError:
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
