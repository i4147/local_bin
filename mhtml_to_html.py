#!/data/data/com.termux/files/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mhtml-to-html — a Python port of the Node.js library ``mhtml-to-html`` (v2.1.0)
by Gildas Lormeau (https://github.com/gildas-lormeau/mhtml-to-html).

Converts MHTML/MHT archives into single, self-contained HTML files: every
resource the archive holds (images, stylesheets, fonts, frames...) is inlined
as a ``data:`` URI and every reference is rewritten accordingly.

The implementation is a single-file, dependency-light CLI and library:

    * Pure standard library except for ``html5lib`` (the only third-party
      dependency — a spec-compliant HTML5 parser, the counterpart of the
      original's ``parse5``).
    * The CSS engine is a from-scratch port of the behavior of the vendored
      ``csstree`` build used by the original (tokenizer, structural parser,
      token serializer with the same whitespace/adjacency rules).

Usage (CLI)::

    python3 mhtml_to_html.py page.mhtml
    python3 mhtml_to_html.py page.mhtml --output out.html
    python3 mhtml_to_html.py folder/            # recursive batch conversion
    python3 mhtml_to_html.py                    # converts *.mht/*.mhtml in CWD

Usage (library)::

    from mhtml_to_html import convert, parse

    result = convert(Path("page.mhtml"))  # -> PageData
    html = result.data

Differences from the Node.js original (documented deliberately):

    * ``--enable-scripts`` and ``--fetch-missing-resources`` are ON by default
      (the original defaults them to off; this port follows the requested
      behavior). Pass ``--no-scripts`` / ``--no-fetch-missing-resources`` to
      restore the original defaults.
    * The network fetcher implements SSRF protections (scheme allow-list,
      private/loopback address blocking, redirect re-validation, timeouts,
      response size limits). These can be relaxed via CLI flags or the
      ``FetchConfig`` dataclass; the original performs none of these checks.
    * ``<noscript>`` content is parsed as markup (scripting-disabled
      semantics, as html5lib does) rather than kept as raw text (parse5's
      scripting-enabled semantics). See COMPATIBILITY notes.

License: MIT (same as the original).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import ipaddress
import json
import logging
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence, Union

__version__ = "2.1.0-py1"

try:  # the single third-party dependency
    import html5lib  # type: ignore
    from xml.dom import minidom  # type: ignore
except ImportError:  # pragma: no cover - reported at use time
    html5lib = None
    minidom = None  # type: ignore

log = logging.getLogger("mhtml_to_html")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MhtmlError(Exception):
    """Base class for errors raised by this library."""


class IndexPageNotFoundError(MhtmlError):
    """Raised when an archive holds nothing that can be presented as a page.

    Mirrors the original library's "Index page not found" error.
    """


class ConversionError(MhtmlError):
    """Raised when a conversion fails for a reason other than a missing index."""


class CssParseError(Exception):
    """Internal: raised by the CSS engine when a stylesheet cannot be parsed."""


class UrlError(ValueError):
    """Internal: URL could not be parsed (mirrors JS ``new URL`` throwing)."""


# ---------------------------------------------------------------------------
# Constants (ported from lib/util.js / lib/convert.js)
# ---------------------------------------------------------------------------

EVENT_HANDLER_ATTRIBUTES = [
    "onafterprint",
    "onbeforeprint",
    "onbeforeunload",
    "onhashchange",
    "onlanguagechange",
    "onmessage",
    "onmessageerror",
    "onoffline",
    "ononline",
    "onpagehide",
    "onpageshow",
    "onpopstate",
    "onrejectionhandled",
    "onstorage",
    "onunhandledrejection",
    "onunload",
    "ongamepadconnected",
    "ongamepaddisconnected",
    "onabort",
    "onblur",
    "onfocus",
    "oncancel",
    "onauxclick",
    "onbeforeinput",
    "onbeforetoggle",
    "oncanplay",
    "oncanplaythrough",
    "onchange",
    "onclick",
    "onclose",
    "oncontentvisibilityautostatechange",
    "oncontextlost",
    "oncontextmenu",
    "oncontextrestored",
    "oncopy",
    "oncuechange",
    "oncut",
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
    "onformdata",
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
    "onwheel",
    "onpaste",
    "onpause",
    "onplay",
    "onplaying",
    "onprogress",
    "onratechange",
    "onreset",
    "onresize",
    "onscroll",
    "onscrollend",
    "onsecuritypolicyviolation",
    "onseeked",
    "onseeking",
    "onselect",
    "onslotchange",
    "onstalled",
    "onsubmit",
    "onsuspend",
    "ontimeupdate",
    "onvolumechange",
    "onwaiting",
    "onselectstart",
    "onselectionchange",
    "ontoggle",
    "onpointercancel",
    "onpointerdown",
    "onpointerup",
    "onpointermove",
    "onpointerout",
    "onpointerover",
    "onpointerenter",
    "onpointerleave",
    "ongotpointercapture",
    "onlostpointercapture",
    "onanimationcancel",
    "onanimationend",
    "onanimationiteration",
    "onanimationstart",
    "ontransitioncancel",
    "ontransitionend",
    "ontransitionrun",
    "ontransitionstart",
    "onerror",
    "onfullscreenchange",
    "onfullscreenerror",
]
_EVENT_HANDLER_ATTRIBUTE_SET = frozenset(EVENT_HANDLER_ATTRIBUTES)

DEFAULT_CSP = (
    "default-src 'none'; connect-src 'self' data:; "
    "font-src 'self' data:; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline' data:; frame-src 'self' data:; "
    "media-src 'self' data:; object-src 'self' data:; "
)
JS_ENABLED_CSP = "script-src 'self' 'unsafe-inline' data:;"
JS_DISABLED_CSP = "script-src 'none';"

ORIGINAL_URL_FUNCTION_NAME = "--mhtml-to-html-url"
ORIGINAL_URL_ATTRIBUTE_PREFIX = "data-original-"
ORIGINAL_URL_FUNCTION_REGEXP = re.compile(r'url\(--mhtml-to-html-url\\\(\\"(.*?)\\"\\\)\\ ')
ORIGINAL_URL_COMMENT = r"/* original URL: \1 */ url("

REL_REMOVED_VALUES_REGEXP = re.compile(
    r"(preconnect|prerender|dns-prefetch|preload|prefetch|manifest|modulepreload)", re.IGNORECASE
)
REL_SEPARATOR_REGEXP = re.compile(r"\s+")
URN_ERROR_REGEXP = re.compile(r"^urn:[^:]+:(.+)$")
CID_REGEXP = re.compile(r"^<.+>$")

CHARSET_REGEXP = re.compile(r"charset=([^;]+)", re.IGNORECASE)
QUOTED_PARAMETER_REGEXP = re.compile(r'^([^=]+)=\s*"([^"]*)"\s*$')

DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_PARALLEL_REQUESTS = 16
RETRY_BASE_DELAY = 0.5
MAX_RETRY_DELAY = 5.0


# ---------------------------------------------------------------------------
# Charset / text utilities  (port of lib/util.js)
# ---------------------------------------------------------------------------

_CHARSET_ALIASES = {
    # WHATWG/IANA label -> Python codec
    "utf8": "utf-8",
    "unicode-1-1-utf-8": "utf-8",
    "utf-8": "utf-8",
    "utf16": "utf-16",
    "utf-16": "utf-16",
    "utf-16le": "utf-16-le",
    "utf-16be": "utf-16-be",
    "us-ascii": "ascii",
    "ascii": "ascii",
    "iso-8859-1": "iso-8859-1",
    "latin1": "latin-1",
    "windows-1252": "cp1252",
    "cp1252": "cp1252",
    "iso-8859-15": "iso-8859-15",
    "macintosh": "mac-roman",
    "mac-roman": "mac-roman",
    "windows-1250": "cp1250",
    "windows-1251": "cp1251",
    "cp1251": "cp1251",
    "koi8-r": "koi8-r",
    "koi8-u": "koi8-u",
    "windows-1253": "cp1253",
    "windows-1254": "cp1254",
    "windows-1255": "cp1255",
    "windows-1256": "cp1256",
    "windows-1257": "cp1257",
    "windows-1258": "cp1258",
    "iso-8859-2": "iso-8859-2",
    "iso-8859-3": "iso-8859-3",
    "iso-8859-4": "iso-8859-4",
    "iso-8859-5": "iso-8859-5",
    "iso-8859-6": "iso-8859-6",
    "iso-8859-7": "iso-8859-7",
    "iso-8859-8": "iso-8859-8",
    "iso-8859-8-i": "iso-8859-8",
    "shift_jis": "shift_jis",
    "shift-jis": "shift_jis",
    "sjis": "shift_jis",
    "csshiftjis": "shift_jis",
    "euc-jp": "euc_jp",
    "iso-2022-jp": "iso2022_jp",
    "gb2312": "gb2312",
    "gbk": "gbk",
    "gb18030": "gb18030",
    "big5": "big5",
    "big5-hkscs": "big5hkscs",
    "euc-kr": "euc_kr",
    "windows-874": "cp874",
    "tis-620": "cp874",
    "ksc5601": "euc_kr",
    "ks_c_5601-1987": "euc_kr",
}


def _lookup_codec(charset: Optional[str]) -> Optional[str]:
    """Map a charset label onto a Python codec, or ``None`` when unknown."""
    if charset is None:
        return None
    label = charset.strip().strip("\"'").lower()
    if not label:
        return None
    codec = _CHARSET_ALIASES.get(label)
    if codec is not None:
        return codec
    try:  # codecs.lookup normalizes many labels itself
        import codecs

        return codecs.lookup(label).name
    except (LookupError, ValueError, TypeError):
        return None


def decode_string(data: bytes, charset: Optional[str] = None) -> str:
    """Decode ``data`` with ``charset``; unknown labels fall back to UTF-8.

    Matches ``new TextDecoder(charset).decode(...)``: invalid sequences are
    replaced with U+FFFD, never raised.
    """
    result = _decode_inner(data, charset)
    # JS TextDecoder removes a leading byte order mark from the output
    if result.startswith("\ufeff"):
        result = result[1:]
    return result


def _decode_inner(data: bytes, charset: Optional[str]) -> str:
    if charset is None:
        return data.decode("utf-8", "replace")
    codec = _lookup_codec(charset)
    if codec == "x-user-defined":
        # WHATWG mapping of the x-user-defined label
        return "".join(chr(b) if b < 0x80 else chr(0xF780 + b - 0x80) for b in data)
    if codec is None:
        return data.decode("utf-8", "replace")
    try:
        return data.decode(codec, "replace")
    except (LookupError, UnicodeDecodeError, ValueError):
        return data.decode("utf-8", "replace")


def encode_string(text: str) -> bytes:
    """UTF-8 encode (the counterpart of the original's ``TextEncoder``)."""
    return text.encode("utf-8", "surrogatepass")


def get_charset(content_type: Optional[str]) -> Optional[str]:
    if not content_type:
        return None
    m = CHARSET_REGEXP.search(content_type)
    if m:
        return _remove_quotes(m.group(1)).lower()
    return None


def _remove_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        value = value[1:-1]
    return value.strip()


def replace_charset(content_type: str, charset: str) -> str:
    return CHARSET_REGEXP.sub(f"charset={charset}", content_type, count=1)


def get_bom_charset(data: bytes) -> Optional[str]:
    if len(data) >= 2 and data[0] == 0xFF and data[1] == 0xFE:
        return "utf-16le"
    if len(data) >= 2 and data[0] == 0xFE and data[1] == 0xFF:
        return "utf-16be"
    if len(data) >= 3 and data[0] == 0xEF and data[1] == 0xBB and data[2] == 0xBF:
        return "utf-8"
    return None


def decode_quoted_printable(data: bytes) -> bytes:
    """Decode ``=XX`` escapes; soft line breaks are handled by the parser."""
    if 0x3D not in data:
        return data
    out = bytearray()
    n = len(data)
    hexdigits = b"0123456789ABCDEFabcdef"
    i = 0
    while i < n:
        b = data[i]
        if b == 0x3D and i + 2 < n and data[i + 1] in hexdigits and data[i + 2] in hexdigits:
            out.append(int(data[i + 1 : i + 3].decode("ascii"), 16))
            i += 3
        else:
            out.append(b)
            i += 1
    return bytes(out)


def decode_binary(data: bytes) -> str:
    """Bytes -> base64 text (the original's ``btoa``-based ``decodeBinary``)."""
    return base64.b64encode(data).decode("ascii")


def decode_base64_bytes(value: str) -> Optional[bytes]:
    """Forgiving base64 decode mirroring JS ``atob``; ``None`` on failure."""
    cleaned = "".join(ch for ch in value if ch not in "\t\n\f\r ")
    if not cleaned:
        return b""
    stripped = cleaned.rstrip("=")
    pad_count = len(cleaned) - len(stripped)
    if pad_count > 2:
        return None
    if "=" in stripped:  # interior padding is invalid
        return None
    if len(stripped) % 4 == 1:
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/]*", stripped):
        return None
    padded = stripped + "=" * ((-len(stripped)) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return None


def decode_base64(value: str, charset: Optional[str]) -> str:
    """Undecodable base64 is returned as it was written (original behavior)."""
    decoded = decode_base64_bytes(value)
    if decoded is None:
        return value
    return decode_string(decoded, charset)


# ---------------------------------------------------------------------------
# RFC 2047 encoded-word decoding  (port of decodeMimeHeader)
# ---------------------------------------------------------------------------


def _decode_encoded_word_qp(value: str) -> bytes:
    # "_" stands for a space inside encoded words
    text = value.replace("_", " ")
    return encode_string(text) if "=" not in text else decode_quoted_printable(encode_string(text))


def _decode_encoded_word(header: str, start: int) -> Optional[tuple[str, bytes, int]]:
    """Parse ``=?charset?enc?value?=`` at ``start``; return (charset, bytes, end)."""
    try:
        end_charset = header.index("?", start + 2)
        end_encoding = header.index("?", end_charset + 1)
        end_value = header.index("?=", end_encoding + 1)
    except ValueError:
        return None
    charset = header[start + 2 : end_charset].lower()
    encoding = header[end_charset + 1 : end_encoding].lower()
    value = header[end_encoding + 1 : end_value]
    end = end_value + 2
    if encoding == "q":
        return charset, _decode_encoded_word_qp(value), end
    if encoding == "b":
        decoded = decode_base64_bytes(value)
        # an unusable value is kept as it was written
        return charset, (encode_string(value) if decoded is None else decoded), end
    return None


def decode_mime_header(header: Optional[str]) -> str:
    """RFC 2047 decoding keeping surrounding text; adjacent words sharing a
    charset are decoded as one so a character split across two words survives."""
    if not header:
        return ""
    parts: list[str] = []
    index = 0
    pending_charset: Optional[str] = None
    pending_chunks: list[bytes] = []

    def flush() -> None:
        nonlocal pending_charset, pending_chunks
        if pending_charset is not None:
            parts.append(decode_string(b"".join(pending_chunks), pending_charset))
            pending_charset = None
            pending_chunks = []

    while index < len(header):
        start = header.find("=?", index)
        if start == -1:
            break
        text = header[index:start]
        encoded = _decode_encoded_word(header, start)
        if encoded is None:
            flush()
            parts.append(text + "=?")
            index = start + 2
        else:
            charset, payload, end = encoded
            if text and (pending_charset is None or text.strip()):
                flush()
                parts.append(text)
            if pending_charset is not None and pending_charset != charset:
                flush()
            if pending_charset is None:
                pending_charset = charset
            pending_chunks.append(payload)
            index = end
    flush()
    parts.append(header[index:])
    return "".join(parts)


# ---------------------------------------------------------------------------
# WHATWG-compatible URL handling (counterpart of ``new URL``)
# ---------------------------------------------------------------------------

_SPECIAL_SCHEMES = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_HEX = "0123456789abcdefABCDEF"


def _pct_encode(text: str, unsafe_extra: set[str]) -> str:
    """Percent-encode: keep valid ``%XX`` escapes verbatim (case preserved),
    encode non-ASCII as UTF-8 escapes, plus controls/DEL/``unsafe_extra``."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        o = ord(ch)
        if ch == "%" and i + 2 < n and text[i + 1] in _HEX and text[i + 2] in _HEX:
            out.append(text[i : i + 3])
            i += 3
            continue
        if o <= 0x20 or o >= 0x7F or ch in unsafe_extra:
            for b in ch.encode("utf-8", "surrogatepass"):
                out.append(f"%{b:02X}")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_PATH_UNSAFE = set('"<>`{}?#')
_QUERY_UNSAFE = set("\"<>#'")
_FRAGMENT_UNSAFE = set('"<>`')
_OPAQUE_UNSAFE = set('"<>`')


def _remove_dot_segments(path: str) -> str:
    if not path:
        return path
    # Fast path: nothing to do (also keeps trailing-slash semantics intact)
    if "." not in path:
        return path
    output: list[str] = []
    segments = path.split("/")
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if seg == ".":
            if last:
                output.append("")
            continue
        if seg == "..":
            if output and output[-1] not in ("", ".."):
                output.pop()
            elif output and output[-1] == "..":
                output.append("..")
            if last:
                output.append("")
            continue
        output.append(seg)
    resolved = "/".join(output)
    if path.startswith("/") and not resolved.startswith("/"):
        resolved = "/" + resolved
    if path.endswith(("/.", "/..")) and not resolved.endswith("/"):
        resolved += "/"
    return resolved


def _normalize_absolute(url: str) -> str:
    """Normalize an absolute URL the way ``new URL(url).href`` does (the
    subset of WHATWG behavior the original relies on). Raises UrlError."""
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if not scheme:
        raise UrlError(f"no scheme in {url!r}")
    netloc = parts.netloc
    if scheme in _SPECIAL_SCHEMES and not netloc:
        # e.g. "http:example.com" — special schemes coerce to authority form
        rest = url[url.index(":") + 1 :]
        while rest.startswith("/"):
            rest = rest[1:]
        split = urllib.parse.urlsplit(f"{scheme}://{rest}")
        parts, netloc = split, split.netloc

    userinfo = ""
    host = ""
    port = ""
    if netloc:
        authority = netloc
        at = authority.rfind("@")
        if at != -1:
            userinfo = authority[: at + 1]
            authority = authority[at + 1 :]
        if authority.startswith("["):  # IPv6 literal
            close = authority.find("]")
            if close == -1:
                raise UrlError("unterminated IPv6 address")
            host = authority[: close + 1]
            tail = authority[close + 1 :]
        else:
            colon = authority.find(":")
            if colon != -1:
                host = authority[:colon]
                tail = authority[colon:]
            else:
                host = authority
                tail = ""
        if tail.startswith(":"):
            port = tail[1:]
            if port and not port.isdigit():
                raise UrlError(f"invalid port {port!r}")
            if port and scheme in _SPECIAL_SCHEMES and int(port) == _SPECIAL_SCHEMES[scheme]:
                port = ""  # default ports are removed
        host = host.lower()

    if scheme in _SPECIAL_SCHEMES:
        if not netloc and scheme != "file":
            raise UrlError(f"special scheme {scheme} requires a host")
        path = _pct_encode(parts.path, _PATH_UNSAFE)
        path = _remove_dot_segments(path)
        if not path and scheme != "file":
            path = "/"
        query = _pct_encode(parts.query, _QUERY_UNSAFE) if parts.query else ""
        fragment = _pct_encode(parts.fragment, _FRAGMENT_UNSAFE) if parts.fragment else ""
        result = f"{scheme}://{userinfo}{host}{':' + port if port else ''}{path}"
    else:
        if netloc:
            path = _pct_encode(parts.path, _PATH_UNSAFE)
            path = _remove_dot_segments(path)
            result = f"{scheme}://{userinfo}{host}{':' + port if port else ''}{path}"
        else:
            # opaque URL ("cid:x", "urn:x", "mailto:x"...)
            opaque = _pct_encode(parts.path, _OPAQUE_UNSAFE)
            result = f"{scheme}:{opaque}"
        query = _pct_encode(parts.query, _QUERY_UNSAFE) if parts.query else ""
        fragment = _pct_encode(parts.fragment, _FRAGMENT_UNSAFE) if parts.fragment else ""
    if query:
        result += "?" + query
    if fragment:
        result += "#" + fragment
    return result


def js_new_url(url: str, base: Optional[str] = None) -> str:
    """Emulate ``new URL(url, base).href``; raises :class:`UrlError` on failure."""
    if base is None:
        return _normalize_absolute(url)

    # Strip leading/trailing C0 controls and spaces (WHATWG "trim")
    url = url.strip(" \t\n\r\f\x0b")
    base_n = _normalize_absolute(base)

    if url == "":
        return re.sub(r"#.*$", "", base_n)
    if _SCHEME_RE.match(url):
        return _normalize_absolute(url)
    if url.startswith("//"):
        base_scheme = urllib.parse.urlsplit(base_n).scheme
        return _normalize_absolute(f"{base_scheme}:{url}")

    base_parts = urllib.parse.urlsplit(base_n)
    base_prefix = f"{base_parts.scheme}://{base_parts.netloc}"
    # split the reference into its path part and its query/fragment
    q_i = _find_unescaped(url, "?")
    f_i = _find_unescaped(url, "#")
    if q_i is not None and (f_i is None or q_i < f_i):
        path_part = url[:q_i]
    elif f_i is not None:
        path_part = url[:f_i]
    else:
        path_part = url
    if url.startswith("/"):
        merged = base_prefix + _remove_dot_segments(_pct_encode(path_part, _PATH_UNSAFE))
        return _with_query_fragment(merged, None, None, url)
    if url.startswith("?"):
        return _with_query_fragment(re.sub(r"[?#].*$", "", base_n), url[1:], None, url)
    if url.startswith("#"):
        return re.sub(r"#.*$", "", base_n) + "#" + _pct_encode(url[1:], _FRAGMENT_UNSAFE)

    # relative path — merge with the base path's directory
    base_path = base_parts.path or "/"
    if not base_path.endswith("/"):
        base_path = base_path[: base_path.rfind("/") + 1]
    merged_path = _remove_dot_segments(_pct_encode(base_path + path_part, _PATH_UNSAFE))
    result = base_prefix + merged_path
    return _with_query_fragment(result, None, None, url)


def _with_query_fragment(candidate: str, query: Optional[str], fragment: Optional[str], original: str) -> str:
    """Attach the query/fragment encoded inside ``original`` to ``candidate``."""
    out = candidate
    q_i = _find_unescaped(original, "?")
    f_i = _find_unescaped(original, "#")
    if q_i is not None and (f_i is None or q_i < f_i):
        end = f_i if (f_i is not None and f_i > q_i) else len(original)
        out += "?" + _pct_encode(original[q_i + 1 : end], _QUERY_UNSAFE)
        if f_i is not None and f_i > q_i:
            out += "#" + _pct_encode(original[f_i + 1 :], _FRAGMENT_UNSAFE)
    elif f_i is not None:
        out += "#" + _pct_encode(original[f_i + 1 :], _FRAGMENT_UNSAFE)
    return out


def _find_unescaped(text: str, ch: str) -> Optional[int]:
    i = 0
    while i < len(text):
        if text[i] == ch:
            return i
        if text[i] == "%" and i + 2 < len(text) and text[i + 1] in _HEX and text[i + 2] in _HEX:
            i += 3
        else:
            i += 1
    return None


def normalize_location(value: str) -> str:
    """``new URL(value).href`` — unparseable values are returned unchanged."""
    try:
        return js_new_url(value)
    except (UrlError, ValueError):
        return value


def resolve_path(path: str, base: Optional[str]) -> str:
    """Port of the original's ``resolvePath`` (URL resolution with fallbacks)."""
    if base and not path.startswith("data:"):
        try:
            return js_new_url(path, base)
        except (UrlError, ValueError):
            if path.startswith("//"):
                m = re.match(r"^[^:]+", base)
                if m:
                    return f"{m.group(0)}:{path}"
                return path
            return path
    return path


# ---------------------------------------------------------------------------
# Content-type predicates (port of util.js)
# ---------------------------------------------------------------------------


def _test_content_type(content_type: Optional[str], prefix: str) -> bool:
    return bool(content_type) and content_type.lower().startswith(prefix)


def is_document(content_type: Optional[str]) -> bool:
    return _test_content_type(content_type, "text/html") or _test_content_type(content_type, "application/xhtml+xml")


def is_stylesheet(content_type: Optional[str]) -> bool:
    return _test_content_type(content_type, "text/css")


def is_text(content_type: Optional[str]) -> bool:
    return _test_content_type(content_type, "text/")


def is_image(content_type: Optional[str]) -> bool:
    return _test_content_type(content_type, "image/")


def is_plain_text(content_type: Optional[str]) -> bool:
    return _test_content_type(content_type, "text/plain")


def is_media(content_type: Optional[str]) -> bool:
    return (
        _test_content_type(content_type, "image/")
        or _test_content_type(content_type, "audio/")
        or _test_content_type(content_type, "video/")
        or _test_content_type(content_type, "font/")
    )


def is_multipart(content_type: Optional[str]) -> bool:
    return _test_content_type(content_type, "multipart/")


def is_multipart_alternative(content_type: Optional[str]) -> bool:
    return _test_content_type(content_type, "multipart/alternative")


def get_boundary(content_type: str) -> Optional[str]:
    params = content_type.split(";")[1:]
    for param in params:
        param = param.strip()
        if param.startswith("boundary="):
            return _remove_quotes(param[9:])
    return None


def get_media_type(content_type: Optional[str]) -> Optional[str]:
    """Media type of a data URI: no whitespace, no quoted parameter values."""
    if not content_type:
        return content_type
    cleaned = []
    for parameter in content_type.split(";"):
        parameter = parameter.strip()
        m = QUOTED_PARAMETER_REGEXP.match(parameter)
        if m:
            parameter = f"{m.group(1)}={m.group(2)}"
        cleaned.append(parameter)
    return ";".join(cleaned)


def get_resource_uri(content_type: Optional[str], transfer_encoding: Optional[str], data: str) -> str:
    media = get_media_type(content_type)
    media = "undefined" if media is None else media  # JS String(undefined) parity
    if transfer_encoding == "base64":
        payload = data
    else:
        payload = decode_binary(encode_string(data))
    return f"data:{media};base64,{payload}"


# ---------------------------------------------------------------------------
# Srcset parser/serializer (direct port of lib/srcset-parser.js)
# ---------------------------------------------------------------------------

_SRCSET_SPACES = " \t\n\r\f"


def _srcset_collect(regexp: "re.Pattern[str]", s: str, pos: list[int]) -> Optional[str]:
    m = regexp.match(s, pos[0])
    if m:
        pos[0] = m.end()
        return m.group(0)
    return None


def parse_srcset(input_text: str) -> list[dict[str, Any]]:
    """Parse a srcset attribute per the HTML algorithm; descriptors of
    invalid candidates are kept verbatim (the original's deviation)."""
    candidates: list[dict[str, Any]] = []
    pos = [0]
    input_length = len(input_text)
    regex_leading_commas_or_spaces = re.compile(r"[, \t\n\r\f]+")
    regex_leading_not_spaces = re.compile(r"[^ \t\n\r\f]+")
    regex_leading_spaces = re.compile(r"[ \t\n\r\f]+")
    regex_trailing_commas = re.compile(r",+$")
    regex_non_negative_integer = re.compile(r"^\d+$")
    regex_floating_point = re.compile(r"^-?(?:[0-9]+|[0-9]*\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")

    def is_space(c: str) -> bool:
        return bool(c) and c in _SRCSET_SPACES

    def parse_descriptors(url: str, descriptors: list[str]) -> None:
        p_error = False
        w = d = h = None
        for desc in descriptors:
            last_char = desc[-1] if desc else ""
            value = desc[:-1]
            try:
                int_val = int(value) if value.isdigit() else None
            except ValueError:
                int_val = None
            try:
                float_val = float(value) if regex_floating_point.match(value or "x") else None
            except ValueError:
                float_val = None
            if regex_non_negative_integer.match(value or "") and last_char == "w":
                if w or d is not None:
                    p_error = True
                if int_val == 0:
                    p_error = True
                else:
                    w = int_val
            elif regex_floating_point.match(value or "x") and last_char == "x":
                if w or d is not None or h:
                    p_error = True
                if float_val is not None and float_val < 0:
                    p_error = True
                else:
                    d = float_val
            elif regex_non_negative_integer.match(value or "") and last_char == "h":
                if h or d is not None:
                    p_error = True
                if int_val == 0:
                    p_error = True
                else:
                    h = int_val
            else:
                p_error = True
        candidate: dict[str, Any] = {"url": url}
        if p_error:
            candidate["descriptors"] = list(descriptors)
        else:
            if w:
                candidate["w"] = w
            if d is not None:
                candidate["d"] = d
            if h:
                candidate["h"] = h
        candidates.append(candidate)

    def tokenize(url: str) -> None:
        _srcset_collect(regex_leading_spaces, input_text, pos)
        state = "in descriptor"
        current_descriptor = ""
        descriptors: list[str] = []
        while True:
            c = input_text[pos[0]] if pos[0] < input_length else ""
            if state == "in descriptor":
                if is_space(c):
                    if current_descriptor:
                        descriptors.append(current_descriptor)
                        current_descriptor = ""
                        state = "after descriptor"
                elif c == ",":
                    pos[0] += 1
                    if current_descriptor:
                        descriptors.append(current_descriptor)
                    parse_descriptors(url, descriptors)
                    return
                elif c == "(":
                    current_descriptor += c
                    state = "in parens"
                elif c == "":
                    if current_descriptor:
                        descriptors.append(current_descriptor)
                    parse_descriptors(url, descriptors)
                    return
                else:
                    current_descriptor += c
            elif state == "in parens":
                if c == ")":
                    current_descriptor += c
                    state = "in descriptor"
                elif c == "":
                    descriptors.append(current_descriptor)
                    parse_descriptors(url, descriptors)
                    return
                else:
                    current_descriptor += c
            elif state == "after descriptor":
                if is_space(c):
                    pass
                elif c == "":
                    parse_descriptors(url, descriptors)
                    return
                else:
                    state = "in descriptor"
                    pos[0] -= 1
            pos[0] += 1

    while True:
        _srcset_collect(regex_leading_commas_or_spaces, input_text, pos)
        if pos[0] >= input_length:
            return candidates
        url = _srcset_collect(regex_leading_not_spaces, input_text, pos)
        if url is None:
            url = ""
        if url.endswith(","):
            url = regex_trailing_commas.sub("", url)
            parse_descriptors(url, [])
        else:
            tokenize(url)


def serialize_srcset(srcset: Sequence[dict[str, Any]]) -> str:
    out = []
    for candidate in srcset:
        descriptors = list(candidate.get("descriptors") or [])
        if candidate.get("w"):
            descriptors.append(f"{candidate['w']}w")
        if candidate.get("h"):
            descriptors.append(f"{candidate['h']}h")
        if candidate.get("d") is not None:
            descriptors.append(_format_srcset_density(candidate["d"]) + "x")
        out.append(" ".join([candidate["url"]] + descriptors))
    return ", ".join(out)


def _format_srcset_density(d: float) -> str:
    # JS String(number) parity for the values that can occur here
    if d == int(d):
        return str(int(d))
    return repr(d)


# ===========================================================================
# CSS engine — a behavioral port of the vendored csstree build
# ===========================================================================
# Token types use csstree's numbering so the adjacency table maps directly.

T_EOF = 0
T_IDENT = 1
T_FUNCTION = 2
T_ATKEYWORD = 3
T_HASH = 4
T_STRING = 5
T_BADSTRING = 6
T_URL = 7
T_BADURL = 8
T_DELIM = 9
T_NUMBER = 10
T_PERCENTAGE = 11
T_DIMENSION = 12
T_WHITESPACE = 13
T_CDO = 14
T_CDC = 15
T_COLON = 16
T_SEMICOLON = 17
T_COMMA = 18
T_LEFT_SQUARE = 19
T_RIGHT_SQUARE = 20
T_LEFT_PAREN = 21
T_RIGHT_PAREN = 22
T_LEFT_CURLY = 23
T_RIGHT_CURLY = 24
T_COMMENT = 25

# The "safe" adjacency table (csstree's default generator mode): pairs of
# (prev, next) token kinds that require a space between them.  Delim tokens
# are keyed by (charCode << 8).
_ADJ_BASE: dict[int, frozenset[int]] = {
    T_IDENT: frozenset({T_IDENT, T_FUNCTION, T_HASH, T_URL, 8, T_NUMBER, T_PERCENTAGE, T_DIMENSION, T_CDC, 21, 0x2D00}),
    T_ATKEYWORD: frozenset({
        T_IDENT,
        T_FUNCTION,
        T_STRING,
        T_URL,
        8,
        T_NUMBER,
        T_PERCENTAGE,
        T_DIMENSION,
        T_CDC,
        T_COLON,
        21,
        0x2D00,
    }),
    T_HASH: frozenset({T_IDENT, T_FUNCTION, T_HASH, T_URL, 8, T_NUMBER, T_PERCENTAGE, T_DIMENSION, T_CDC, 0x2D00}),
    T_NUMBER: frozenset({T_IDENT, T_FUNCTION, T_URL, 8, T_NUMBER, T_PERCENTAGE, T_DIMENSION, T_CDC, 0x2500}),
    T_PERCENTAGE: frozenset({T_FUNCTION, T_PERCENTAGE, T_DIMENSION, 0x2D00}),
    T_DIMENSION: frozenset({T_IDENT, T_FUNCTION, T_HASH, T_URL, 8, T_NUMBER, T_PERCENTAGE, T_DIMENSION, T_CDC, 0x2D00}),
    22: frozenset({T_IDENT, T_FUNCTION, T_HASH, T_PERCENTAGE, T_DIMENSION, 0x2D00}),
    0x2D00: frozenset({T_IDENT, T_FUNCTION, T_URL, 8, T_NUMBER, T_PERCENTAGE, T_DIMENSION, T_CDC, 0x2D00}),
    0x2B00: frozenset({T_NUMBER, T_PERCENTAGE, T_DIMENSION}),
    0x2E00: frozenset({T_NUMBER, T_PERCENTAGE, T_DIMENSION}),
    0x2300: frozenset({T_IDENT, T_FUNCTION, T_URL, 8, T_NUMBER, T_PERCENTAGE, T_DIMENSION, T_CDC, 0x2D00}),
    0x4000: frozenset({T_IDENT, T_FUNCTION, T_URL, 8, T_CDC, 0x2D00}),
    0x2F00: frozenset({0x2A00}),
}
_SAFE_TABLE: set[int] = set()
for _prev, _nexts in _ADJ_BASE.items():
    for _nx in _nexts:
        _SAFE_TABLE.add((_prev << 16) | _nx)


def _css_is_ws_char(ch: str) -> bool:
    return ch in " \t\r\n\f"


def _css_is_hex(ch: str) -> bool:
    return ch in "0123456789abcdefABCDEF"


def _css_is_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _css_is_ident_start(ch: str) -> bool:
    o = ord(ch)
    return ch == "_" or ch.isalpha() or o >= 0x80


def _css_is_ident_char(ch: str) -> bool:
    return _css_is_ident_start(ch) or _css_is_digit(ch) or ch == "-"


def _valid_escape_at(src: str, i: int) -> bool:
    """``src[i] == '\\'`` starts a valid escape?"""
    if i >= len(src) or src[i] != "\\":
        return False
    if i + 1 >= len(src):
        return False
    nxt = src[i + 1]
    return nxt not in "\n\r\f"


def _ident_starts_at(src: str, i: int) -> bool:
    if i >= len(src):
        return False
    ch = src[i]
    if ch == "-":
        if i + 1 >= len(src):
            return False
        nxt = src[i + 1]
        if nxt == "-":
            return i + 2 < len(src) and (_css_is_ident_start(src[i + 2]) or _valid_escape_at(src, i + 2))
        return _css_is_ident_start(nxt) or _valid_escape_at(src, i + 1)
    return _css_is_ident_start(ch) or _valid_escape_at(src, i)


def _number_starts_at(src: str, i: int) -> bool:
    ch = src[i] if i < len(src) else ""
    if ch in "+-":
        i += 1
        ch = src[i] if i < len(src) else ""
        if ch == ".":
            return i + 1 < len(src) and _css_is_digit(src[i + 1])
        return _css_is_digit(ch)
    if ch == ".":
        return i + 1 < len(src) and _css_is_digit(src[i + 1])
    return _css_is_digit(ch)


def _consume_ident(src: str, i: int) -> int:
    while i < len(src):
        ch = src[i]
        if _css_is_ident_char(ch):
            i += 1
        elif ch == "\\" and _valid_escape_at(src, i):
            i = _consume_escape(src, i)
        else:
            break
    return i


def _consume_escape(src: str, start: int) -> int:
    """``start`` points at a backslash; return index after the whole escape."""
    t = start + 2
    if t - 1 < len(src) and _css_is_hex(src[t - 1]):
        limit = min(len(src), t + 5)
        while t < limit and _css_is_hex(src[t]):
            t += 1
        if t < len(src) and _css_is_ws_char(src[t]):
            t += _ws_run_len(src, t)
    return t


def _ws_run_len(src: str, t: int) -> int:
    # one terminating whitespace (CRLF counts as one)
    if src[t] == "\r" and t + 1 < len(src) and src[t + 1] == "\n":
        return 2
    return 1


def _decode_escape_body(body: str) -> str:
    if len(body) == 1 and not _css_is_hex(body):
        return body
    try:
        cp = int(body, 16)
    except ValueError:
        return body
    if cp == 0 or 0xD800 <= cp <= 0xDFFF or cp > 0x10FFFF:
        cp = 0xFFFD
    return chr(cp)


def _consume_bad_url_remnants(src: str, i: int) -> int:
    while i < len(src):
        if src[i] == ")":
            return i + 1
        if src[i] == "\\" and _valid_escape_at(src, i):
            i = _consume_escape(src, i)
        else:
            i += 1
    return i


class CssToken:
    __slots__ = ("kind", "text")

    def __init__(self, kind: int, text: str) -> None:
        self.kind = kind
        self.text = text


def tokenize_css(src: str) -> list[CssToken]:
    tokens: list[CssToken] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if _css_is_ws_char(ch):
            j = i + 1
            while j < n and _css_is_ws_char(src[j]):
                j += 1
            tokens.append(CssToken(T_WHITESPACE, src[i:j]))
            i = j
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            if end == -1:
                tokens.append(CssToken(T_COMMENT, src[i:]))
                i = n
            else:
                tokens.append(CssToken(T_COMMENT, src[i : end + 2]))
                i = end + 2
            continue
        if ch == "<" and src.startswith("<!--", i):
            tokens.append(CssToken(T_CDO, "<!--"))
            i += 4
            continue
        if _ident_starts_at(src, i):
            ident_start = i
            j = _consume_ident(src, i)
            ident = src[ident_start:j]
            if j < n and src[j] == "(":
                if ident.lower() == "url":
                    # url( — function or url token depending on what follows
                    k = j + 1
                    while k < n and _css_is_ws_char(src[k]):
                        k += 1
                    if k < n and src[k] in "\"'":
                        tokens.append(CssToken(T_FUNCTION, src[ident_start : j + 1]))
                        i = j + 1
                        continue
                    # consume a url token
                    k = j + 1
                    while k < n and _css_is_ws_char(src[k]):
                        k += 1
                    end = _consume_url_body(src, k)
                    tokens.append(CssToken(end[0], src[ident_start : end[1]]))
                    i = end[1]
                    continue
                tokens.append(CssToken(T_FUNCTION, src[ident_start : j + 1]))
                i = j + 1
                continue
            tokens.append(CssToken(T_IDENT, ident))
            i = j
            continue
        if _number_starts_at(src, i):
            j = _consume_number(src, i)
            if j < n and src[j] == "%":
                tokens.append(CssToken(T_PERCENTAGE, src[i : j + 1]))
                i = j + 1
                continue
            if j < n and (
                _css_is_ident_start(src[j])
                or (src[j] == "-" and j + 1 < n and _css_is_ident_start(src[j + 1]))
                or (src[j] == "\\" and _valid_escape_at(src, j))
            ):
                k = _consume_ident(src, j)
                tokens.append(CssToken(T_DIMENSION, src[i:k]))
                i = k
                continue
            tokens.append(CssToken(T_NUMBER, src[i:j]))
            i = j
            continue
        if ch == '"' or ch == "'":
            kind, end = _consume_string(src, i)
            tokens.append(CssToken(kind, src[i:end]))
            i = end
            continue
        if ch == "#":
            if i + 1 < n and (
                _css_is_ident_start(src[i + 1]) or _css_is_digit(src[i + 1]) or _valid_escape_at(src, i + 1)
            ):
                j = i + 1
                while j < n:
                    c = src[j]
                    if _css_is_ident_char(c):
                        j += 1
                    elif c == "\\" and _valid_escape_at(src, j):
                        j = _consume_escape(src, j)
                    else:
                        break
                tokens.append(CssToken(T_HASH, src[i:j]))
                i = j
                continue
            tokens.append(CssToken(T_DELIM, ch))
            i += 1
            continue
        if ch == "@":
            if i + 1 < n and (_css_is_ident_start(src[i + 1]) or _valid_escape_at(src, i + 1)):
                j = _consume_ident(src, i + 1)
                tokens.append(CssToken(T_ATKEYWORD, src[i:j]))
                i = j
                continue
            tokens.append(CssToken(T_DELIM, ch))
            i += 1
            continue
        if ch == "-" and src.startswith("-->", i):
            tokens.append(CssToken(T_CDC, "-->"))
            i += 3
            continue
        simple = {
            "(": T_LEFT_PAREN,
            ")": T_RIGHT_PAREN,
            "[": T_LEFT_SQUARE,
            "]": T_RIGHT_SQUARE,
            "{": T_LEFT_CURLY,
            "}": T_RIGHT_CURLY,
            ":": T_COLON,
            ";": T_SEMICOLON,
            ",": T_COMMA,
        }
        if ch in simple:
            tokens.append(CssToken(simple[ch], ch))
            i += 1
            continue
        tokens.append(CssToken(T_DELIM, ch))
        i += 1
    return tokens


def _consume_url_body(src: str, k: int) -> tuple[int, int]:
    """Scan the body of an unquoted ``url(...)`` token starting at ``k``
    (past ``url(`` and any leading whitespace).  Returns (kind, end)."""
    n = len(src)
    i = k
    while i < n:
        ch = src[i]
        if ch == ")":
            return (T_URL, i + 1)
        if _css_is_ws_char(ch):
            j = i + 1
            while j < n and _css_is_ws_char(src[j]):
                j += 1
            if j < n and src[j] == ")":
                return (T_URL, j + 1)
            if j >= n:
                return (T_URL, n)
            return (T_BADURL, _consume_bad_url_remnants(src, j))
        if ch in "\"'(" or ord(ch) < 0x20 or ord(ch) == 0x7F:
            return (T_BADURL, _consume_bad_url_remnants(src, i))
        if ch == "\\":
            if _valid_escape_at(src, i):
                i = _consume_escape(src, i)
                continue
            return (T_BADURL, _consume_bad_url_remnants(src, i))
        i += 1
    return (T_URL, n)


def _consume_number(src: str, i: int) -> int:
    n = len(src)
    j = i
    if j < n and src[j] in "+-":
        j += 1
    while j < n and _css_is_digit(src[j]):
        j += 1
    if j < n and src[j] == ".":
        if j + 1 < n and _css_is_digit(src[j + 1]):
            j += 1
            while j < n and _css_is_digit(src[j]):
                j += 1
        else:
            return j
    if j < n and src[j] in "eE":
        k = j + 1
        if k < n and src[k] in "+-":
            k += 1
        if k < n and _css_is_digit(src[k]):
            j = k
            while j < n and _css_is_digit(src[j]):
                j += 1
    return j


def _consume_string(src: str, i: int) -> tuple[int, int]:
    n = len(src)
    quote = src[i]
    j = i + 1
    while j < n:
        ch = src[j]
        if ch == quote:
            return (T_STRING, j + 1)
        if ch in "\n\r":
            return (T_BADSTRING, j)  # newline terminates a bad string
        if ch == "\\":
            if j + 1 >= n:
                return (T_STRING, n)  # trailing backslash at EOF — lenient
            nxt = src[j + 1]
            if nxt in "\n\r\f":
                j += 2
                if nxt == "\r" and j < n and src[j] == "\n":
                    j += 1
                continue
            j = _consume_escape(src, j)
            continue
        j += 1
    return (T_STRING, n)  # unterminated at EOF — lenient like csstree


def css_decode_string_token(text: str) -> str:
    """Port of csstree's string-token decoder (``vt``)."""
    t = len(text)
    first = text[0] if t else ""
    start = 1 if first in "\"'" else 0
    end = (t - 2) if (start == 1 and t > 1 and text[t - 1] == first) else (t - 1)
    out = ""
    s = start
    while s <= end:
        ch = text[s]
        if ch == "\\":
            if s == end:
                if s != t - 1:
                    out = text[s + 1 :]  # faithful to csstree's odd behavior
                break
            s += 1
            nxt = text[s] if s < t else ""
            if nxt and nxt not in "\n\r\f":
                back = s - 1
                stop = _consume_escape(text, back)
                s = stop - 1
                out += _decode_escape_body(text[back + 1 : stop])
            elif nxt == "\r" and s + 1 < t and text[s + 1] == "\n":
                s += 1
            # an invalid escape (\<newline>) contributes nothing
        else:
            out += ch
        s += 1
    return out


def css_decode_url_token(text: str) -> str:
    """Port of csstree's url-token decoder (``Vi``)."""
    t = len(text)
    r = 4
    n = (t - 2) if (t > 0 and text[t - 1] == ")") else (t - 1)
    while r < n and _css_is_ws_char(text[r]):
        r += 1
    while r < n and _css_is_ws_char(text[n]):
        n -= 1
    out = ""
    o = r
    while o <= n:
        ch = text[o]
        if ch == "\\":
            if o == n:
                if o != t - 1:
                    out = text[o + 1 :]  # faithful to csstree's odd behavior
                break
            o += 1
            nxt = text[o] if o < t else ""
            if nxt and nxt not in "\n\r\f":
                back = o - 1
                stop = _consume_escape(text, back)
                o = stop - 1
                out += _decode_escape_body(text[back + 1 : stop])
            elif nxt == "\r" and o + 1 < t and text[o + 1] == "\n":
                o += 1
        else:
            out += ch
        o += 1
    return out


def css_encode_string(value: str) -> str:
    """Port of csstree's ``encodeString`` (always double quotes)."""
    out: list[str] = []
    after_hex = False
    for ch in value:
        o = ord(ch)
        if o == 0:
            out.append("\ufffd")
            continue
        if o <= 31 or o == 127:
            out.append("\\" + format(o, "x"))
            after_hex = True
            continue
        if ch in ('"', "\\"):
            out.append("\\" + ch)
            after_hex = False
        else:
            if after_hex and (_css_is_hex(ch) or _css_is_ws_char(ch)):
                out.append(" ")
            out.append(ch)
            after_hex = False
    return '"' + "".join(out) + '"'


def css_encode_url(value: str) -> str:
    """Port of csstree's ``encodeUrl``."""
    out: list[str] = []
    after_hex = False
    for ch in value:
        o = ord(ch)
        if o == 0:
            out.append("\ufffd")
            continue
        if o <= 31 or o == 127:
            out.append("\\" + format(o, "x"))
            after_hex = True
            continue
        if ch in " \\\"'()":
            out.append("\\" + ch)
            after_hex = False
        else:
            if after_hex and _css_is_hex(ch):
                out.append(" ")
            out.append(ch)
            after_hex = False
    return "url(" + "".join(out) + ")"


# ---------------------------------------------------------------------------
# CSS AST nodes
# ---------------------------------------------------------------------------


class CssNode:
    __slots__ = ()


class CssTok(CssNode):
    __slots__ = ("kind", "text")

    def __init__(self, kind: int, text: str) -> None:
        self.kind = kind
        self.text = text


class CssStr(CssNode):
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value


class CssUrl(CssNode):
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value


class CssFunc(CssNode):
    __slots__ = ("name", "children")

    def __init__(self, name: str, children: list) -> None:
        self.name = name  # raw text, includes the "("
        self.children = children


class CssParen(CssNode):
    __slots__ = ("children",)

    def __init__(self, children: list) -> None:
        self.children = children


class CssBracket(CssNode):
    __slots__ = ("children",)

    def __init__(self, children: list) -> None:
        self.children = children


class CssOpPM(CssNode):
    """A standalone ``+``/``-`` operator; surrounding whitespace is
    normalized to single spaces exactly the way csstree emits it."""

    __slots__ = ("text", "lead", "trail")

    def __init__(self, text: str, lead: bool, trail: bool) -> None:
        self.text = text
        self.lead = lead
        self.trail = trail


class CssComb(CssNode):
    """A descendant combinator (emits one space)."""

    __slots__ = ()


class CssRaw(CssNode):
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


class CssDecl(CssNode):
    __slots__ = ("property", "value", "important")

    def __init__(self, property_name: str, value, important: bool) -> None:
        self.property = property_name
        self.value = value  # list of components or CssRaw
        self.important = important


class CssAtrule(CssNode):
    __slots__ = ("name", "prelude", "block")

    def __init__(self, name: str, prelude: list, block) -> None:
        self.name = name
        self.prelude = prelude  # list of components
        self.block = block  # list of block items or None


class CssRule(CssNode):
    __slots__ = ("prelude", "block")

    def __init__(self, prelude: list, block) -> None:
        self.prelude = prelude  # list of CssTok/CssComb/CssStr/CssUrl
        self.block = block  # list of block items or None


_RULE_BLOCK_ATRULES = frozenset({
    "media",
    "supports",
    "container",
    "keyframes",
    "layer",
    "nest",
    "scope",
    "starting-style",
    "document",
})

_COMBINATOR_DELIMS = frozenset({">", "+", "~"})


class _Toks:
    """Token list helper with whitespace skipping."""

    def __init__(self, tokens: list[CssToken]) -> None:
        self.tokens = tokens

    def skip_ws(self, i: int) -> int:
        while i < len(self.tokens) and self.tokens[i].kind in (T_WHITESPACE, T_COMMENT):
            i += 1
        return i

    def text(self, start: int, end: int) -> str:
        return "".join(t.text for t in self.tokens[start:end])


def parse_stylesheet(css: str) -> list:
    """Parse a whole stylesheet into a list of block-level items."""
    tk = _Toks(tokenize_css(css))
    items, i = _parse_block_items(tk, 0, "rules", top_level=True)
    return items


def parse_declaration_list(css: str) -> list:
    """Parse the content of a ``style`` attribute."""
    tk = _Toks(tokenize_css(css))
    items, i = _parse_block_items(tk, 0, "declarations", top_level=True)
    return items


def _parse_block_items(tk: _Toks, i: int, mode: str, top_level: bool = False) -> tuple[list, int]:
    items: list = []
    n = len(tk.tokens)
    while True:
        i = tk.skip_ws(i)
        if i >= n:
            break
        kind = tk.tokens[i].kind
        if kind == T_RIGHT_CURLY and not top_level:
            break
        if kind == T_RIGHT_CURLY and top_level:
            # stray closing brace — kept verbatim
            items.append(CssRaw(tk.tokens[i].text))
            i += 1
            continue
        if kind == T_ATKEYWORD:
            item, i = _parse_atrule(tk, i, mode)
            items.append(item)
            continue
        if mode == "rules":
            item, i = _parse_rule(tk, i)
            items.append(item)
        else:
            item, i = _parse_declaration_or_raw(tk, i)
            items.append(item)
    return items, i


def _span_has_depth0_comma(tk: _Toks, start: int, end: int) -> bool:
    depth = 0
    for idx in range(start, end):
        kind = tk.tokens[idx].kind
        if kind in (T_WHITESPACE, T_COMMENT):
            continue
        if kind in (T_FUNCTION, T_LEFT_PAREN, T_LEFT_SQUARE):
            depth += 1
        elif kind in (T_RIGHT_PAREN, T_RIGHT_SQUARE):
            if depth > 0:
                depth -= 1
        elif depth == 0 and kind == T_COMMA:
            return True
    return False


def _parse_atrule(tk: _Toks, i: int, mode: str) -> tuple[CssNode, int]:
    start = i
    at_token = tk.tokens[i]
    name = at_token.text[1:]
    i += 1
    n = len(tk.tokens)
    # prelude: components until ';' or '{' at depth 0
    depth = 0
    prelude_start = i
    j = i
    while j < n:
        kind = tk.tokens[j].kind
        if kind in (T_WHITESPACE, T_COMMENT):
            j += 1
            continue
        if depth == 0 and kind == T_SEMICOLON:
            break
        if depth == 0 and kind == T_LEFT_CURLY:
            break
        if kind in (T_FUNCTION, T_LEFT_PAREN, T_LEFT_SQUARE):
            depth += 1
        elif kind in (T_RIGHT_PAREN, T_RIGHT_SQUARE):
            if depth > 0:
                depth -= 1
        j += 1
    prelude_end = j
    if name.lower() == "import" and _span_has_depth0_comma(tk, prelude_start, prelude_end):
        # csstree's import prelude parser rejects commas: the prelude is kept raw
        raw_text = tk.text(prelude_start, prelude_end)
        prelude = [CssRaw(raw_text)]
        if j < n and tk.tokens[j].kind == T_LEFT_CURLY:
            block, i = _parse_block(tk, j, "declarations")
            return CssAtrule(name, prelude, block), i
        if j < n and tk.tokens[j].kind == T_SEMICOLON:
            return CssAtrule(name, prelude, None), j + 1
        return CssAtrule(name, prelude, None), j
    try:
        prelude = _parse_components(tk, prelude_start, prelude_end, lenient=True)
        if j < n and tk.tokens[j].kind == T_LEFT_CURLY:
            block_mode = "rules" if name.lower() in _RULE_BLOCK_ATRULES else "declarations"
            block, i = _parse_block(tk, j, block_mode)
            return CssAtrule(name, prelude, block), i
        if j < n and tk.tokens[j].kind == T_SEMICOLON:
            return CssAtrule(name, prelude, None), j + 1
        return CssAtrule(name, prelude, None), j
    except CssParseError:
        raw_end = _raw_span_end(tk, start)
        return CssRaw(tk.text(start, raw_end[0])), raw_end[1]


def _parse_block(tk: _Toks, i: int, mode: str) -> tuple[list, int]:
    # tokens[i] is '{'
    i += 1
    n = len(tk.tokens)
    items: list = []
    while True:
        i = tk.skip_ws(i)
        if i >= n:
            break
        if tk.tokens[i].kind == T_RIGHT_CURLY:
            i += 1
            break
        if tk.tokens[i].kind == T_ATKEYWORD:
            item, i = _parse_atrule(tk, i, mode)
            items.append(item)
            continue
        if mode == "rules":
            item, i = _parse_rule(tk, i)
        else:
            item, i = _parse_declaration_or_raw(tk, i)
        items.append(item)
    return items, i


def _parse_rule(tk: _Toks, i: int) -> tuple[CssNode, int]:
    start = i
    n = len(tk.tokens)
    depth = 0
    j = tk.skip_ws(i)
    prelude_start = j
    while j < n:
        kind = tk.tokens[j].kind
        if kind in (T_WHITESPACE, T_COMMENT):
            j += 1
            continue
        if depth == 0 and kind == T_LEFT_CURLY:
            break
        if depth == 0 and kind in (T_SEMICOLON, T_RIGHT_CURLY):
            # not a rule after all
            raw_end = _raw_span_end(tk, start)
            return CssRaw(tk.text(start, raw_end[0])), raw_end[1]
        if kind in (T_FUNCTION, T_LEFT_PAREN, T_LEFT_SQUARE):
            depth += 1
        elif kind in (T_RIGHT_PAREN, T_RIGHT_SQUARE):
            if depth > 0:
                depth -= 1
        j += 1
    prelude = _parse_selector_prelude(tk, prelude_start, j)
    if j < n and tk.tokens[j].kind == T_LEFT_CURLY:
        block, i = _parse_block(tk, j, "declarations")
        return CssRule(prelude, block), i
    return CssRule(prelude, None), j


_SELECTOR_DROP_AROUND = _COMBINATOR_DELIMS | {","}


def _parse_selector_prelude(tk: _Toks, start: int, end: int) -> list:
    out: list = []
    pending_ws = False
    idx = start
    while idx < end:
        t = tk.tokens[idx]
        if t.kind in (T_WHITESPACE, T_COMMENT):
            pending_ws = True
            idx += 1
            continue
        if t.kind == T_FUNCTION:
            # a functional pseudo-class: its arguments are parsed as selector
            # components with the whitespace dropped (e.g. :not( b ))
            closer = _match_nested_end(tk, idx, end)
            children = _parse_selector_prelude(tk, idx + 1, closer)
            node = CssFunc(t.text, children)
            idx = closer + 1
        else:
            node = _selector_token_node(t)
            idx += 1
        if pending_ws and out and not _selector_drop_flag(out[-1]) and not _selector_drop_flag(node):
            out.append(CssComb())
        out.append(node)
        pending_ws = False
    return out


def _match_nested_end(tk: _Toks, opener: int, end: int) -> int:
    """Index of the closer matching the function/paren at ``opener``."""
    depth = 1
    j = opener + 1
    while j < end:
        kind = tk.tokens[j].kind
        if kind in (T_FUNCTION, T_LEFT_PAREN, T_LEFT_SQUARE):
            depth += 1
        elif kind in (T_RIGHT_PAREN, T_RIGHT_SQUARE):
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return end


def _selector_drop_flag(node: CssNode) -> bool:
    if isinstance(node, CssTok):
        if node.kind == T_COMMA:
            return True
        if node.kind == T_DELIM and node.text in _COMBINATOR_DELIMS:
            return True
    return False


def _selector_token_node(t: CssToken) -> CssNode:
    if t.kind == T_STRING:
        return CssStr(css_decode_string_token(t.text))
    if t.kind == T_URL:
        return CssUrl(css_decode_url_token(t.text))
    if t.kind == T_FUNCTION:
        # functional pseudo (e.g. :not(...)) — flatten name + children + ')'
        return CssTok(T_FUNCTION, t.text)
    return CssTok(t.kind, t.text)


def _parse_declaration_or_raw(tk: _Toks, i: int) -> tuple[CssNode, int]:
    start = i
    n = len(tk.tokens)
    i = tk.skip_ws(i)
    if i >= n:
        return CssRaw(""), i
    t = tk.tokens[i]
    property_raw = None
    if t.kind == T_IDENT:
        property_raw = t.text
        i += 1
    elif t.kind == T_DELIM and t.text == "*" and i + 1 < n and tk.tokens[i + 1].kind == T_IDENT:
        property_raw = t.text + tk.tokens[i + 1].text
        i += 2
    if property_raw is not None:
        j = tk.skip_ws(i)
        if j < n and tk.tokens[j].kind == T_COLON:
            return _parse_declaration_value(tk, start, property_raw, j + 1)
    raw_end = _raw_span_end(tk, start)
    return CssRaw(tk.text(start, raw_end[0])), raw_end[1]


def _raw_span_end(tk: _Toks, start: int) -> tuple[int, int]:
    """Consume a balance-aware span for a Raw fallback: through ``;``
    (inclusive) or up to ``}``/EOF (exclusive, trailing whitespace stripped).
    An inner ``{`` opens a balanced region and is part of the raw text."""
    n = len(tk.tokens)
    depth = 0
    j = start
    last_nonspace = start
    while j < n:
        kind = tk.tokens[j].kind
        if kind in (T_WHITESPACE, T_COMMENT):
            j += 1
            continue
        if depth == 0 and kind == T_SEMICOLON:
            return j + 1, j + 1
        if depth == 0 and kind == T_RIGHT_CURLY:
            return last_nonspace, j
        if kind in (T_FUNCTION, T_LEFT_PAREN, T_LEFT_SQUARE, T_LEFT_CURLY):
            depth += 1
        elif kind in (T_RIGHT_PAREN, T_RIGHT_SQUARE, T_RIGHT_CURLY):
            if depth > 0:
                depth -= 1
        last_nonspace = j + 1
        j += 1
    return last_nonspace, n


def _parse_declaration_value(tk: _Toks, decl_start: int, property_raw: str, value_start: int) -> tuple[CssNode, int]:
    """Parse the value of a regular (non custom) declaration."""
    n = len(tk.tokens)
    # balance-scan the value span
    depth = 0
    j = value_start
    value_end = value_start
    terminator = None  # ';' '}' '{' or None (EOF)
    stray_close = False
    while j < n:
        kind = tk.tokens[j].kind
        if kind in (T_WHITESPACE, T_COMMENT):
            j += 1
            continue
        if depth == 0 and kind in (T_SEMICOLON, T_RIGHT_CURLY, T_LEFT_CURLY):
            terminator = kind
            break
        if kind in (T_FUNCTION, T_LEFT_PAREN, T_LEFT_SQUARE):
            depth += 1
        elif kind in (T_RIGHT_PAREN, T_RIGHT_SQUARE):
            if depth > 0:
                depth -= 1
            else:
                stray_close = True
                break
        j += 1
    if terminator == T_SEMICOLON:
        value_end = j
        next_i = j + 1
    elif terminator in (T_RIGHT_CURLY, T_LEFT_CURLY):
        value_end = _strip_trailing_ws(tk, value_start, j)
        next_i = j
    else:
        value_end = _strip_trailing_ws(tk, value_start, n)
        next_i = n
        if stray_close:
            raw_end = _raw_span_end(tk, decl_start)
            return CssRaw(tk.text(decl_start, raw_end[0])), raw_end[1]
    # the structural parse sees the unstripped span: a trailing "+"/"-" absorbs
    # the whitespace that precedes the terminator exactly like csstree does
    components_end = j if terminator == T_SEMICOLON else (n if terminator is None else j)

    is_custom = property_raw.startswith("--")
    if is_custom:
        raw_end = _custom_property_end(tk, value_start)
        raw = tk.text(value_start, raw_end[0]).rstrip() if raw_end[2] else tk.text(value_start, raw_end[0])
        important, _ = _extract_important(tk, raw_end[0], raw_end[1])
        return CssDecl(property_raw, CssRaw(raw), important), raw_end[1]

    # !important extraction (depth-0 '!' pattern at the tail)
    important, excl_ok = _extract_important(tk, value_start, value_end)
    if not excl_ok:
        raw_end = _raw_span_end(tk, decl_start)
        return CssRaw(tk.text(decl_start, raw_end[0])), raw_end[1]
    if _has_depth0_exclamation(tk, value_start, _IMPORTANT_SPAN[0]):
        raw_end = _raw_span_end(tk, decl_start)
        return CssRaw(tk.text(decl_start, raw_end[0])), raw_end[1]
    if important:
        components_end = min(components_end, _IMPORTANT_SPAN[0])

    try:
        components = _parse_components(tk, value_start, components_end, lenient=False)
    except CssParseError:
        raw = tk.text(value_start, value_end)
        if terminator == T_SEMICOLON:
            raw = raw.strip()
        else:
            raw = raw.strip()
        return CssDecl(property_raw, CssRaw(raw), important), next_i
    return CssDecl(property_raw, components, important), next_i


def _strip_trailing_ws(tk: _Toks, start: int, end: int) -> int:
    while end > start and tk.tokens[end - 1].kind in (T_WHITESPACE, T_COMMENT):
        end -= 1
    return end


def _custom_property_end(tk: _Toks, start: int) -> tuple[int, int, bool]:
    """End of a custom property value: stops at ``!`` or ``;`` (depth 0) or
    ``}``/EOF.  Returns (raw_end_index, next_index, strip_trailing)."""
    n = len(tk.tokens)
    depth = 0
    j = start
    while j < n:
        kind = tk.tokens[j].kind
        if kind in (T_WHITESPACE, T_COMMENT):
            j += 1
            continue
        if depth == 0 and kind == T_DELIM and tk.tokens[j].text == "!":
            return j, j, True
        if depth == 0 and kind == T_SEMICOLON:
            return j, j + 1, False
        if depth == 0 and kind in (T_RIGHT_CURLY, T_LEFT_CURLY):
            return _strip_trailing_ws(tk, start, j), j, False
        if kind in (T_FUNCTION, T_LEFT_PAREN, T_LEFT_SQUARE, T_LEFT_CURLY):
            depth += 1
        elif kind in (T_RIGHT_PAREN, T_RIGHT_SQUARE, T_RIGHT_CURLY):
            if depth > 0:
                depth -= 1
        j += 1
    return _strip_trailing_ws(tk, start, n), n, False


def _extract_important(tk: _Toks, start: int, end: int) -> tuple[bool, bool]:
    """Strip a trailing ``!important`` from the span [start, end).
    Returns (important, pattern_ok)."""
    j = end
    # trailing ws
    while j > start and tk.tokens[j - 1].kind in (T_WHITESPACE, T_COMMENT):
        j -= 1
    if j > start and tk.tokens[j - 1].kind == T_IDENT and tk.tokens[j - 1].text.lower() == "important":
        k = j - 1
        while k > start and tk.tokens[k - 1].kind in (T_WHITESPACE, T_COMMENT):
            k -= 1
        if k > start and tk.tokens[k - 1].kind == T_DELIM and tk.tokens[k - 1].text == "!":
            _IMPORTANT_SPAN[0] = k - 1
            _IMPORTANT_SPAN[1] = j
            return True, True
    _IMPORTANT_SPAN[0] = end
    _IMPORTANT_SPAN[1] = end
    return False, True


_IMPORTANT_SPAN = [0, 0]


def _has_depth0_exclamation(tk: _Toks, start: int, end: int) -> bool:
    depth = 0
    for idx in range(start, end):
        kind = tk.tokens[idx].kind
        if kind in (T_FUNCTION, T_LEFT_PAREN, T_LEFT_SQUARE):
            depth += 1
        elif kind in (T_RIGHT_PAREN, T_RIGHT_SQUARE):
            if depth > 0:
                depth -= 1
        elif depth == 0 and kind == T_DELIM and tk.tokens[idx].text == "!":
            return True
    return False


def _parse_components(tk: _Toks, start: int, end: int, lenient: bool) -> list:
    """Parse value/prelude components from the token span [start, end)."""
    comps: list = []
    pending_ws = False
    i = start
    while i < end:
        t = tk.tokens[i]
        if t.kind in (T_WHITESPACE, T_COMMENT):
            pending_ws = True
            i += 1
            continue
        kind = t.kind
        if kind in (T_IDENT, T_HASH, T_NUMBER, T_PERCENTAGE, T_DIMENSION, T_ATKEYWORD, T_CDO, T_CDC):
            if kind in (T_ATKEYWORD, T_CDO, T_CDC) and not lenient:
                raise CssParseError(f"unexpected token {t.text!r}")
            comps.append(CssTok(kind, t.text))
            i += 1
        elif kind == T_STRING:
            comps.append(CssStr(css_decode_string_token(t.text)))
            i += 1
        elif kind == T_URL:
            comps.append(CssUrl(css_decode_url_token(t.text)))
            i += 1
        elif kind == T_FUNCTION:
            if t.text.lower() == "url(":
                # url( "string" ) form
                j = tk.skip_ws(i + 1)
                if j < end and tk.tokens[j].kind == T_STRING:
                    value = css_decode_string_token(tk.tokens[j].text)
                    k = tk.skip_ws(j + 1)
                    if k < end and tk.tokens[k].kind == T_RIGHT_PAREN:
                        comps.append(CssUrl(value))
                        i = k + 1
                        continue
                if lenient:
                    comps.append(CssTok(T_FUNCTION, t.text))
                    i += 1
                    continue
                raise CssParseError("url( must be followed by a string")
            children, i = _parse_nested(tk, i, end, T_RIGHT_PAREN, lenient)
            comps.append(CssFunc(t.text, children))
        elif kind == T_LEFT_PAREN:
            children, i = _parse_nested(tk, i, end, T_RIGHT_PAREN, lenient)
            comps.append(CssParen(children))
        elif kind == T_LEFT_SQUARE:
            children, i = _parse_nested(tk, i, end, T_RIGHT_SQUARE, lenient)
            comps.append(CssBracket(children))
        elif kind == T_COMMA:
            comps.append(CssTok(T_COMMA, ","))
            i += 1
        elif kind == T_DELIM:
            if t.text in ("+", "-"):
                lead = pending_ws and bool(comps)
                j = i + 1
                trail = j < end and tk.tokens[j].kind in (T_WHITESPACE, T_COMMENT)
                comps.append(CssOpPM(t.text, lead, trail))
                i += 1
            elif t.text in ("/", "*"):
                comps.append(CssTok(T_DELIM, t.text))
                i += 1
            elif lenient:
                comps.append(CssTok(T_DELIM, t.text))
                i += 1
            else:
                raise CssParseError(f"unexpected delimiter {t.text!r}")
        elif kind == T_BADSTRING or kind == T_BADURL:
            if lenient:
                comps.append(CssTok(kind, t.text))
                i += 1
            else:
                raise CssParseError("bad string/url token in value")
        elif lenient:
            comps.append(CssTok(kind, t.text))
            i += 1
        else:
            raise CssParseError(f"unexpected token kind {kind}")
        pending_ws = False
    return comps


def _parse_nested(tk: _Toks, i: int, end: int, closer: int, lenient: bool) -> tuple[list, int]:
    """Parse the children of a function/parentheses/brackets starting at the
    opener token ``i``; returns (children, index_after_closer)."""
    opener = tk.tokens[i].kind
    i += 1
    # find the matching closer within [i, end)
    depth = 1
    j = i
    while j < end:
        kind = tk.tokens[j].kind
        if kind in (T_FUNCTION, T_LEFT_PAREN, T_LEFT_SQUARE):
            depth += 1
        elif kind in (T_RIGHT_PAREN, T_RIGHT_SQUARE):
            if kind == closer:
                depth -= 1
                if depth == 0:
                    children = _parse_components(tk, i, j, lenient)
                    return children, j + 1
            else:
                depth -= 1
        j += 1
    if lenient:
        return _parse_components(tk, i, end, True), end
    raise CssParseError("unbalanced function/parentheses")


# ---------------------------------------------------------------------------
# CSS serializer (port of csstree's generator)
# ---------------------------------------------------------------------------


class _CssEmitter:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.prev = 0

    def token(self, kind: int, text: str) -> None:
        if text:
            first = ord(text[0])
            norm = _norm_kind(kind, text)
            if (first == 0x2D and kind not in (T_IDENT, T_FUNCTION, T_CDC)) or first == 0x2B:
                key = (self.prev << 16) | (first << 8)
            else:
                key = (self.prev << 16) | norm
            if key in _SAFE_TABLE:
                self.parts.append(" ")
            self.parts.append(text)
            self.prev = norm
            if kind == T_DELIM and text.startswith("\\"):
                self.parts.append("\n")  # csstree emits a newline after a lone backslash
        elif kind:
            self.prev = _norm_kind(kind, text)

    def ws(self) -> None:
        self.token(T_WHITESPACE, " ")

    def result(self) -> str:
        return "".join(self.parts)


def _norm_kind(kind: int, text: str) -> int:
    if kind == T_DELIM and text:
        o = ord(text[0])
        return 0x8000 if o > 127 else (o << 8)
    return kind


def _emit_components(em: _CssEmitter, comps: Iterable) -> None:
    for node in comps:
        if isinstance(node, CssTok):
            em.token(node.kind, node.text)
        elif isinstance(node, CssStr):
            em.token(T_STRING, css_encode_string(node.value))
        elif isinstance(node, CssUrl):
            em.token(T_URL, css_encode_url(node.value))
        elif isinstance(node, CssFunc):
            em.token(T_FUNCTION, node.name)
            _emit_components(em, node.children)
            em.token(T_RIGHT_PAREN, ")")
        elif isinstance(node, CssParen):
            em.token(T_LEFT_PAREN, "(")
            _emit_components(em, node.children)
            em.token(T_RIGHT_PAREN, ")")
        elif isinstance(node, CssBracket):
            em.token(T_LEFT_SQUARE, "[")
            _emit_components(em, node.children)
            em.token(T_RIGHT_SQUARE, "]")
        elif isinstance(node, CssOpPM):
            if node.lead:
                em.ws()
            em.token(T_DELIM, node.text)
            if node.trail:
                em.ws()
        elif isinstance(node, CssComb):
            em.ws()
        elif isinstance(node, CssRaw):
            _emit_raw(em, node.text)


def _emit_raw(em: _CssEmitter, text: str) -> None:
    # csstree re-tokenizes Raw values and emits every token verbatim
    for t in tokenize_css(text):
        em.token(t.kind, t.text)


def _emit_block_items(em: _CssEmitter, items: Sequence, braces: bool = True) -> None:
    if braces:
        em.token(T_LEFT_CURLY, "{")
    for idx, item in enumerate(items):
        if idx > 0 and isinstance(items[idx - 1], CssDecl):
            em.token(T_SEMICOLON, ";")
        _emit_item(em, item)
    if braces:
        em.token(T_RIGHT_CURLY, "}")


def _emit_item(em: _CssEmitter, item: CssNode) -> None:
    if isinstance(item, CssRule):
        _emit_components(em, item.prelude)
        if item.block is not None:
            _emit_block_items(em, item.block)
    elif isinstance(item, CssAtrule):
        em.token(T_ATKEYWORD, "@" + item.name)
        if item.prelude:
            _emit_components(em, item.prelude)
        if item.block is not None:
            _emit_block_items(em, item.block)
        else:
            em.token(T_SEMICOLON, ";")
    elif isinstance(item, CssDecl):
        em.token(T_IDENT, item.property)
        em.token(T_COLON, ":")
        if isinstance(item.value, CssRaw):
            _emit_raw(em, item.value.text)
        else:
            _emit_components(em, item.value)
        if item.important:
            em.token(T_DELIM, "!")
            em.token(T_IDENT, "important")
    elif isinstance(item, CssRaw):
        _emit_raw(em, item.text)


def generate_stylesheet(items: Sequence) -> str:
    em = _CssEmitter()
    for item in items:
        _emit_item(em, item)
    return em.result()


def generate_declaration_list(items: Sequence) -> str:
    em = _CssEmitter()
    _emit_block_items(em, items, braces=False)
    return em.result()


# ---------------------------------------------------------------------------
# CSS tree walking (used by the converter for url()/@import rewriting)
# ---------------------------------------------------------------------------


def walk_css_nodes(items: Iterable, visit: Callable[[CssNode], None]) -> None:
    def walk_list(nodes: Iterable) -> None:
        for node in nodes:
            visit(node)
            if isinstance(node, CssFunc):
                walk_list(node.children)
            elif isinstance(node, CssParen):
                walk_list(node.children)
            elif isinstance(node, CssBracket):
                walk_list(node.children)

    for item in items:
        visit(item)
        if isinstance(item, CssRule):
            walk_list(item.prelude)
            if item.block is not None:
                walk_css_nodes(item.block, visit)
        elif isinstance(item, CssAtrule):
            walk_list(item.prelude)
            if item.block is not None:
                walk_css_nodes(item.block, visit)
        elif isinstance(item, CssDecl):
            if not isinstance(item.value, CssRaw):
                walk_list(item.value)


def first_stylesheet_item_is_charset(items: Sequence) -> Optional[tuple[str, str]]:
    """Return (charset_value, ) for a leading ``@charset`` rule, else None."""
    if not items:
        return None
    first = items[0]
    if isinstance(first, CssAtrule) and first.name.lower() == "charset" and first.prelude:
        node = first.prelude[0]
        if isinstance(node, CssStr):
            return (node.value,)
    return None


def remove_first_stylesheet_item(items: list) -> None:
    if items:
        items.pop(0)


# ===========================================================================
# DOM layer — html5lib parsing + a faithful port of the original serializer
# ===========================================================================

VOID_TAG_NAMES = frozenset({
    "area",
    "base",
    "basefont",
    "bgsound",
    "br",
    "col",
    "command",
    "embed",
    "frame",
    "hr",
    "img",
    "input",
    "keygen",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
})
RAW_TEXT_NODE_TAG_NAMES = frozenset({
    "style",
    "script",
    "xmp",
    "iframe",
    "noembed",
    "noframes",
    "plaintext",
    "noscript",
})
_TEXT_ESCAPE_TABLE = str.maketrans({
    "&": "&amp;",
    "\u00a0": "&nbsp;",
    "<": "&lt;",
    ">": "&gt;",
})
_ATTRIBUTE_ESCAPE_TABLE = str.maketrans({
    "&": "&amp;",
    "\u00a0": "&nbsp;",
    '"': "&quot;",
})
INVALID_TAG_NAME_REGEXP = re.compile(r"""["'>/=]""")
JAVASCRIPT_MIME_TYPE = "text/javascript"


class DomDocument:
    """Thin wrapper over a minidom document produced by html5lib."""

    def __init__(self, document) -> None:
        self.document = document

    # -- lookups ------------------------------------------------------------

    @property
    def document_element(self):
        for child in self.document.childNodes:
            if _tag_name(child) is not None:
                return child
        return None

    @property
    def doctype(self):
        for child in self.document.childNodes:
            if getattr(child, "nodeType", None) == 10:  # DOCUMENT_TYPE_NODE
                return child
        return None

    @property
    def head(self):
        root = self.document_element
        if root is None:
            return None
        for child in root.childNodes:
            if _tag_name(child) == "head":
                return child
        return None

    def get_elements_by_tag_name(self, tag_name: str) -> list:
        return list(self.document.getElementsByTagName(tag_name))

    def create_element(self, tag_name: str):
        return self.document.createElement(tag_name)

    def create_text_node(self, data: str):
        return self.document.createTextNode(data)

    # -- serialization (port of dom-parser-node.js) -------------------------

    def serialize(self) -> str:
        result = ""
        doctype = self.doctype
        if doctype is not None:
            result += _serialize_doctype(doctype) + "\n"
        root = self.document_element
        if root is not None:
            result += serialize_html_node(root)
        return result


def _tag_name(node) -> Optional[str]:
    name = getattr(node, "tagName", None)
    return name.lower() if isinstance(name, str) else None


def parse_dom(asset: str, content_type: str = "text/html") -> DomDocument:
    if html5lib is None:
        raise ConversionError("html5lib is required for HTML parsing: pip install html5lib")
    document = html5lib.parse(asset, treebuilder="dom")
    return DomDocument(document)


def _serialize_doctype(doctype) -> str:
    name = getattr(doctype, "name", "") or ""
    public_id = getattr(doctype, "publicId", "") or ""
    system_id = getattr(doctype, "systemId", "") or ""
    out = f"<!DOCTYPE {name}"
    if public_id:
        out += f' PUBLIC "{public_id}"'
    if system_id:
        out += f' "{system_id}"'
    return out + ">"


def serialize_html_node(node) -> str:
    node_type = getattr(node, "nodeType", None)
    if node_type == 3:  # TEXT_NODE
        return _serialize_text_node(node)
    if node_type == 8:  # COMMENT_NODE
        return f"<!--{node.data}-->"
    return _serialize_element(node)


def _serialize_text_node(text_node) -> str:
    parent = getattr(text_node, "parentNode", None)
    parent_tag = _tag_name(parent) if parent is not None else None
    if parent_tag and parent_tag not in RAW_TEXT_NODE_TAG_NAMES:
        return _escape_entities_text(text_node.data)
    if _should_escape_raw_text(parent):
        return _escape_raw_text(text_node.data)
    return text_node.data


def _should_escape_raw_text(parent) -> bool:
    if parent is None:
        return False
    parent_tag = _tag_name(parent)
    if parent_tag == "style":
        return True
    if parent_tag == "script":
        attrs = getattr(parent, "attributes", None)
        type_value = None
        if attrs is not None and "type" in attrs.keys():
            type_value = attrs["type"].value
        return not type_value or type_value == JAVASCRIPT_MIME_TYPE
    return False


def _escape_entities_text(value: str) -> str:
    return value.translate(_TEXT_ESCAPE_TABLE)


def _escape_raw_text(value: str) -> str:
    value = value.replace("</", "<\\/")
    # a marker already carrying its backslash is left alone
    out: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        if value[i] == "/" and value[i : i + 2] == "/>" and not (i > 0 and value[i - 1] == "\\"):
            out.append("\\/>")
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def _serialize_element(element) -> str:
    tag_name = _tag_name(element)
    html = "<" + tag_name
    attributes = _serialize_attributes(element)
    if attributes:
        html += " " + attributes
    html += ">"
    html += _serialize_children(element)
    if tag_name not in VOID_TAG_NAMES:
        html += f"</{tag_name}>"
    return html


def _serialize_children(element) -> str:
    # parse5 keeps template children in a detached fragment; html5lib keeps
    # them as regular child nodes — serializing childNodes is equivalent here.
    return "".join(serialize_html_node(child) for child in element.childNodes)


def _serialize_attributes(element) -> str:
    attrs = getattr(element, "attributes", None)
    if attrs is None:
        return ""
    out: list[str] = []
    for name in attrs.keys():
        if INVALID_TAG_NAME_REGEXP.search(name):
            continue  # an attribute whose name cannot be written is dropped
        value = attrs[name].value
        value = value.translate(_ATTRIBUTE_ESCAPE_TABLE)
        out.append(f'{name}="{value}"')
    return " ".join(out)


# -- element helpers (the DOMParserLike subset the converter relies on) ----


def element_get_attribute(element, name: str) -> Optional[str]:
    attrs = getattr(element, "attributes", None)
    if attrs is None:
        return None
    if name in attrs.keys():
        return attrs[name].value
    lowered = name.lower()
    for key in attrs.keys():
        if key.lower() == lowered:
            return attrs[key].value
    return None


def element_get_attribute_names(element) -> list[str]:
    attrs = getattr(element, "attributes", None)
    return list(attrs.keys()) if attrs is not None else []


def element_set_attribute(element, name: str, value: str) -> None:
    element.setAttribute(name, value)


def element_remove_attribute(element, name: str) -> None:
    try:
        element.removeAttribute(name)
    except Exception:
        # removeAttribute on minidom raises NotFoundError for unknown names
        lowered = name.lower()
        for key in list(element.attributes.keys()):
            if key.lower() == lowered:
                element.removeAttribute(key)
                return


def element_append_child(parent, child) -> None:
    parent.childNodes.append(child)
    child.parentNode = parent


def element_remove(node) -> None:
    parent = getattr(node, "parentNode", None)
    if parent is not None:
        try:
            parent.childNodes.remove(node)
        except ValueError:
            pass
        node.parentNode = None


def element_replace_with(node, *new_nodes) -> None:
    parent = getattr(node, "parentNode", None)
    if parent is None:
        return
    try:
        index = parent.childNodes.index(node)
    except ValueError:
        return
    parent.childNodes[index : index + 1] = list(new_nodes)
    for n in new_nodes:
        n.parentNode = parent
    node.parentNode = None


def element_prepend(parent, *new_nodes) -> None:
    parent.childNodes[0:0] = list(new_nodes)
    for n in new_nodes:
        n.parentNode = parent


def element_after(node, *new_nodes) -> None:
    parent = getattr(node, "parentNode", None)
    if parent is None:
        return
    try:
        index = parent.childNodes.index(node)
    except ValueError:
        return
    parent.childNodes[index + 1 : index + 1] = list(new_nodes)
    for n in new_nodes:
        n.parentNode = parent


def get_meta_charset_elements(document_element) -> list:
    out = []
    if document_element is None:
        return out
    for meta in document_element.getElementsByTagName("meta"):
        if element_get_attribute(meta, "charset"):
            out.append(meta)
    return out


def get_meta_content_type_elements(document_element) -> list:
    out = []
    if document_element is None:
        return out
    for meta in document_element.getElementsByTagName("meta"):
        http_equiv = element_get_attribute(meta, "http-equiv")
        if http_equiv and http_equiv.lower() == "content-type":
            out.append(meta)
    return out


# ===========================================================================
# MHTML parsing (port of lib/parse.js)
# ===========================================================================

MHTML_HEADERS = 0
MHTML_CONTENT = 1
MHTML_DATA = 2
MHTML_END = 3

HEADER_NAME_REGEXP = re.compile(r"^[!-9;-~]+:")
HEADER_CONTINUATION_REGEXP = re.compile(r"^[ \t]")

MISLABELED_DOCUMENT_ANOMALY = "document-mislabeled-as-archive"
UNUSED_BOUNDARY_ANOMALY = "declared-boundary-unused"
MISSING_DELIMITERS_ANOMALY = "multipart-without-delimiters"
UNDECODABLE_BASE64_ANOMALY = "base64-left-encoded"
SYNTHESIZED_INDEX_ANOMALY = "index-synthesized"

UTF8_CHARSET = "utf-8"
QUOTED_PRINTABLE_ENCODING = "quoted-printable"
BINARY_ENCODING = "binary"
BASE64_ENCODING = "base64"


@dataclass
class Anomaly:
    type: str
    id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.id is None:
            # match the JS shape (no `id` key when absent)
            object.__setattr__(self, "_dict", {"type": self.type})
        else:
            object.__setattr__(self, "_dict", {"type": self.type, "id": self.id})

    def to_dict(self) -> dict:
        return dict(getattr(self, "_dict", {"type": self.type}))


@dataclass
class Resource:
    id: str
    content_type: Optional[str] = None
    transfer_encoding: Optional[str] = None
    data: Union[str, bytes] = ""
    raw_data: Optional[bytes] = None
    used: bool = False
    not_found: bool = False
    dom: Any = None
    dom_data: Any = None


@dataclass
class Mhtml:
    headers: dict[str, str] = field(default_factory=dict)
    frames: dict[str, Resource] = field(default_factory=dict)
    resources: dict[str, Resource] = field(default_factory=dict)
    index: Optional[str] = None
    anomalies: list[Anomaly] = field(default_factory=list)


def _starts_with_markup(data: bytes) -> bool:
    bom = get_bom_charset(data)
    index = 0
    skip_nul = False
    if bom == UTF8_CHARSET:
        index = 3
    elif bom is not None:
        index = 2
        skip_nul = True
    n = len(data)
    while index < n:
        b = data[index]
        if b == 0x3C:  # '<'
            return True
        if skip_nul and b == 0:
            index += 1
            continue
        if b not in (0x20, 0x09, 0x0D, 0x0A):
            return False
        index += 1
    return False


def _is_line_feed(line: bytes) -> bool:
    if len(line) == 2:
        return line[0] == 0x0D and line[1] == 0x0A
    if len(line) == 1:
        return line[0] == 0x0A
    return False


def _ends_with_crlf(line: bytes) -> bool:
    if len(line) >= 2:
        return line[-2] == 0x0D and line[-1] == 0x0A
    if len(line) >= 1:
        return line[-1] == 0x0D
    return False


def _ends_with_lf(line: bytes) -> bool:
    return len(line) >= 1 and line[-1] == 0x0A


def _index_of_subarray(haystack: bytes, needle: Optional[bytes], start: int = 0) -> int:
    if not needle:
        return -1
    return haystack.find(needle, start)


def _find_boundary_delimiter(line: bytes, boundary_bytes: Optional[bytes]) -> int:
    """Index of the boundary string inside a delimiter line, or -1."""
    if boundary_bytes is None:
        return -1
    index_boundary = _index_of_subarray(line, boundary_bytes)
    if index_boundary >= 2 and line[index_boundary - 2] == 0x2D and line[index_boundary - 1] == 0x2D:
        index = index_boundary + len(boundary_bytes)
        if line[index : index + 2] == b"--":
            index += 2
        n = len(line)
        while index < n and line[index] in (0x20, 0x09):
            index += 1
        if index >= n or line[index] in (0x0D, 0x0A):
            return index_boundary
    return -1


def _parse_header_line(line_string: str, obj: dict, header_key: Optional[str]) -> Optional[str]:
    index_column = line_string.find(":")
    if index_column > -1:
        header_key = line_string[:index_column].strip().lower()
        obj[header_key] = line_string[index_column + 1 :].strip()
    elif header_key is not None and header_key in obj:
        obj[header_key] += line_string.strip()
    return header_key


def parse_mhtml(
    mhtml: Union[str, bytes, bytearray], config: Optional[dict] = None, context: Optional[dict] = None
) -> Mhtml:
    """Parse MHTML data into the structured :class:`Mhtml` representation."""
    if isinstance(mhtml, str):
        mhtml = encode_string(mhtml)
    elif isinstance(mhtml, bytearray):
        mhtml = bytes(mhtml)
    config = config or {}
    if context is None:
        context = {"resources": {}, "frames": {}}
    if "anomalies" not in context or context.get("anomalies") is None:
        context["anomalies"] = []
    anomalies: list[Anomaly] = context["anomalies"]
    resources: dict[str, Resource] = context["resources"]
    frames: dict[str, Resource] = context["frames"]
    headers: dict[str, str] = {}
    raw_header_lines: list[bytes] = []

    state = MHTML_HEADERS
    index_mhtml = 0
    index_generated_id = 0
    index_start_embedded_mhtml: Optional[int] = None
    boundary: Optional[str] = None
    boundary_bytes: Optional[bytes] = None
    declared_boundary_unused = False
    header_key: Optional[str] = None
    transfer_encoding: Optional[str] = None
    content_id: Optional[str] = None
    resource: Optional[Resource] = None
    content: dict[str, str] = {}
    data_chunks: list[bytes] = []
    data_length = 0

    n = len(mhtml)

    def get_line(encoding: Optional[str] = None) -> bytes:
        nonlocal index_mhtml
        index_start = index_mhtml
        index_line_feed = mhtml.find(b"\n", index_start)
        index_mhtml = (n + 1) if index_line_feed == -1 else index_line_feed + 1
        line = mhtml[index_start:index_mhtml]
        if encoding == QUOTED_PRINTABLE_ENCODING:
            return decode_quoted_printable(line)
        return line

    def split_headers(line: bytes, obj: dict) -> None:
        nonlocal header_key
        header_key = _parse_header_line(decode_string(line), obj, header_key)

    def decode_raw_headers(charset: Optional[str]) -> None:
        if charset is None or charset == UTF8_CHARSET:
            return
        if not any("\ufffd" in value for value in headers.values()):
            return
        decoded_headers: dict[str, str] = {}
        decoded_key: Optional[str] = None
        for line in raw_header_lines:
            decoded_key = _parse_header_line(decode_string(line, charset), decoded_headers, decoded_key)
        for name, value in list(headers.items()):
            if "\ufffd" in value and name in decoded_headers:
                headers[name] = decoded_headers[name]

    def read_body_headers() -> dict[str, str]:
        nonlocal index_mhtml, header_key
        index_start_body = index_mhtml
        if _starts_with_markup(mhtml[index_mhtml:]):
            return {"content-type": "text/html"}
        body_headers: dict[str, str] = {}
        header_key = None
        index_before_line = index_mhtml
        next_line = get_line()
        while next_line and not _is_line_feed(next_line) and _is_header_line(next_line, len(body_headers)):
            split_headers(next_line, body_headers)
            index_before_line = index_mhtml
            next_line = get_line()
        if "content-type" not in body_headers:
            index_mhtml = index_start_body
            return headers
        if not _is_line_feed(next_line):
            index_mhtml = index_before_line
        return body_headers

    def _is_header_line(line: bytes, count_headers: int) -> bool:
        value = decode_string(line)
        return bool(HEADER_NAME_REGEXP.match(value)) or (
            count_headers > 0 and bool(HEADER_CONTINUATION_REGEXP.match(value))
        )

    def init_resource(resource_data: dict[str, str]) -> None:
        nonlocal transfer_encoding, content_id, resource, content, header_key
        nonlocal data_chunks, data_length, index_generated_id
        transfer_encoding = resource_data.get("content-transfer-encoding")
        content_type = resource_data.get("content-type")
        content_id = resource_data.get("content-id")
        id_value = resource_data.get("content-location")
        if transfer_encoding:
            transfer_encoding = transfer_encoding.lower()
        data_chunks.clear()
        data_length = 0
        resource = Resource(
            id=id_value or "",
            content_type=content_type,
            transfer_encoding=transfer_encoding,
            data="",
        )
        if id_value is None:
            if content_id is not None:
                id_value = content_id
            else:
                while True:
                    id_value = f"_{index_generated_id}"
                    index_generated_id += 1
                    if id_value not in resources:
                        break
        written_id = id_value
        id_value = normalize_location(id_value)
        resource.id = id_value
        if context.get("index") is None and is_document(content_type):
            context["index"] = id_value
        if content_id is not None:
            frames[content_id] = resource
            if content_id not in resources:
                resources[content_id] = resource
        if id_value not in resources:
            resources[id_value] = resource
        if written_id != id_value and written_id not in resources:
            resources[written_id] = resource
        content = {}
        header_key = None

    def append_data(chunk: bytes) -> None:
        nonlocal data_length
        if chunk:
            data_chunks.append(chunk)
            data_length += len(chunk)

    def get_data_byte(offset_from_end: int) -> Optional[int]:
        offset = offset_from_end
        for chunk in reversed(data_chunks):
            if len(chunk) >= offset:
                return chunk[-offset]
            offset -= len(chunk)
        return None

    def truncate_data(count: int) -> None:
        nonlocal data_length
        data_length -= count
        remaining = count
        while remaining:
            if not data_chunks:
                break
            chunk = data_chunks[-1]
            if len(chunk) > remaining:
                data_chunks[-1] = chunk[: len(chunk) - remaining]
                remaining = 0
            else:
                data_chunks.pop()
                remaining -= len(chunk)

    def truncate_data_line_terminator() -> None:
        b2 = get_data_byte(2)
        b1 = get_data_byte(1)
        if data_length > 1 and b2 == 0x0D and b1 == 0x0A:
            truncate_data(2)
        elif data_length > 0 and b1 == 0x0A:
            truncate_data(1)

    def flatten_data() -> bytes:
        return b"".join(data_chunks)

    def parse_resource_data() -> Optional[int]:
        nonlocal data_chunks, data_length
        next_line = get_line(transfer_encoding)
        index_end_data: Optional[int] = None
        boundary_found = False
        while not boundary_found and next_line:
            index_end_data = index_mhtml
            index_boundary = _find_boundary_delimiter(next_line, boundary_bytes)
            if index_boundary != -1:
                index_end_data = index_mhtml - len(next_line) + index_boundary - 2
                if index_boundary > 2:
                    next_line = next_line[: index_boundary - 2]
                else:
                    next_line = b""
                boundary_found = True
            if resource is not None and resource.transfer_encoding == QUOTED_PRINTABLE_ENCODING:
                if data_length > 2 and get_data_byte(3) == 0x3D and _ends_with_crlf(next_line):
                    truncate_data(3)
                elif data_length > 1 and get_data_byte(2) == 0x3D and _ends_with_lf(next_line):
                    truncate_data(2)
            elif resource is not None and resource.transfer_encoding == BASE64_ENCODING:
                if _ends_with_crlf(next_line):
                    next_line = next_line[:-2]
                elif _ends_with_lf(next_line):
                    next_line = next_line[:-1]
            append_data(next_line)
            if not boundary_found:
                next_line = get_line(transfer_encoding)
        truncate_data_line_terminator()
        if not boundary_found and boundary is not None:
            index_end_data = index_mhtml
        return index_end_data

    def convert_embedded_mhtml(index_end: int) -> Optional[str]:
        nonlocal index_mhtml
        inner_context = {"resources": resources, "frames": frames, "anomalies": anomalies}
        embedded = mhtml[index_start_embedded_mhtml:index_end]
        if _ends_with_crlf(embedded):
            index_end -= 2
        elif _ends_with_lf(embedded):
            index_end -= 1
        parse_mhtml(mhtml[index_start_embedded_mhtml:index_end], config, inner_context)
        return inner_context.get("index")

    def process_resource() -> None:
        nonlocal resource
        assert resource is not None
        raw = resource.data if isinstance(resource.data, bytes) else encode_string(str(resource.data))
        resource.raw_data = raw
        declared_charset = get_charset(resource.content_type)
        if (
            resource.transfer_encoding == BASE64_ENCODING
            and resource.content_type
            and (
                resource.id == context.get("index")
                or (content_id is not None and is_document(resource.content_type))
                or is_stylesheet(resource.content_type)
            )
        ):
            decoded = decode_base64_bytes(decode_string(raw))
            if decoded is not None:
                resource.transfer_encoding = None
                resource.data = resource.raw_data = decoded
            else:
                anomalies.append(Anomaly(UNDECODABLE_BASE64_ANOMALY, resource.id))
        data = resource.raw_data
        bom_charset = get_bom_charset(data)
        charset = bom_charset or declared_charset
        if resource.transfer_encoding == BINARY_ENCODING and (
            not resource.content_type or not is_text(resource.content_type)
        ):
            resource.transfer_encoding = BASE64_ENCODING
            resource.data = decode_binary(data)
        else:
            resource.data = decode_string(data, charset)
        if resource.content_type:
            if resource.transfer_encoding != BASE64_ENCODING:
                resource.content_type = replace_charset(resource.content_type, UTF8_CHARSET)
            if is_stylesheet(resource.content_type):
                process_stylesheet_charset(charset, bom_charset)
            elif is_document(resource.content_type):
                document_charset = process_document_charset(charset, bom_charset)
                if resource.id == context.get("index"):
                    decode_raw_headers(document_charset)
        resource.raw_data = None

    def process_stylesheet_charset(charset: Optional[str], bom_charset: Optional[str]) -> None:
        assert resource is not None
        try:
            items = parse_stylesheet(resource.data)
            found = first_stylesheet_item_is_charset(items)
            if found is not None:
                css_charset = found[0].lower()
                if bom_charset is None and css_charset != UTF8_CHARSET and css_charset != charset:
                    resource.data = decode_string(resource.raw_data, css_charset)
                    items = parse_stylesheet(resource.data)
                remove_first_stylesheet_item(items)
                resource.data = generate_stylesheet(items)
        except Exception:
            pass  # a broken sheet is left as it is

    def process_document_charset(charset: Optional[str], bom_charset: Optional[str]) -> Optional[str]:
        nonlocal resource
        assert resource is not None
        document_charset = charset
        modified = False
        content_type = (resource.content_type or "").split(";")[0]
        dom = parse_dom(resource.data, content_type)
        charset_meta_elements = get_meta_charset_elements(dom.document_element)
        if charset_meta_elements:
            html_charset = element_get_attribute(charset_meta_elements[0], "charset")
            if html_charset:
                html_charset = html_charset.lower()
                if bom_charset is None and html_charset != UTF8_CHARSET and html_charset != charset:
                    resource.data = decode_string(resource.raw_data, html_charset)
                    document_charset = html_charset
                    dom = parse_dom(resource.data, content_type)
                    charset_meta_elements = get_meta_charset_elements(dom.document_element)
            for meta_element in charset_meta_elements:
                element_remove(meta_element)
            modified = True
        meta_elements = get_meta_content_type_elements(dom.document_element)
        if meta_elements:
            content_attr = element_get_attribute(meta_elements[0], "content")
            html_charset = get_charset(content_attr)
            if (
                bom_charset is None
                and html_charset
                and html_charset != UTF8_CHARSET
                and html_charset != document_charset
            ):
                resource.data = decode_string(resource.raw_data, html_charset)
                document_charset = html_charset
                dom = parse_dom(resource.data, content_type)
                meta_elements = get_meta_content_type_elements(dom.document_element)
                for meta_element in get_meta_charset_elements(dom.document_element):
                    element_remove(meta_element)
            for meta_element in meta_elements:
                element_remove(meta_element)
            modified = True
        if modified:
            resource.data = dom.serialize()
            # serializing is not idempotent — the tree has to be read back
            dom = parse_dom(resource.data, content_type)
        cache_resource_dom(resource, dom)
        return document_charset

    # -- mislabeled plain-HTML detection -------------------------------------
    if _starts_with_markup(mhtml):
        anomalies.append(Anomaly(MISLABELED_DOCUMENT_ANOMALY))
        headers["content-type"] = "text/html"
        init_resource(headers)
        append_data(mhtml)
        resource.data = flatten_data()
        process_resource()
        return Mhtml(
            headers=headers, frames=frames, resources=resources, index=context.get("index"), anomalies=anomalies
        )

    # -- the main state machine ------------------------------------------------
    while state != MHTML_END and (index_mhtml < n or state == MHTML_DATA):
        if state == MHTML_HEADERS:
            next_line = get_line()
            if not _is_line_feed(next_line):
                raw_header_lines.append(next_line)
                split_headers(next_line, headers)
            else:
                if "content-type" in headers:
                    boundary = get_boundary(headers["content-type"])
                    boundary_bytes = encode_string(boundary) if boundary is not None else None
                if boundary is not None:
                    index_start_body = index_mhtml
                    while _find_boundary_delimiter(next_line, boundary_bytes) == -1 and index_mhtml < n - 1:
                        next_line = get_line()
                    if _find_boundary_delimiter(next_line, boundary_bytes) == -1:
                        index_mhtml = index_start_body
                        boundary = None
                        boundary_bytes = None
                        declared_boundary_unused = True
                if boundary is None:
                    previous_index = index_mhtml
                    next_line = get_line(transfer_encoding)
                    if next_line[:2] == b"--":
                        boundary = decode_string(next_line)[2:].rstrip()
                        boundary_bytes = encode_string(boundary)
                        if declared_boundary_unused:
                            anomalies.append(Anomaly(UNUSED_BOUNDARY_ANOMALY))
                    else:
                        index_mhtml = previous_index
                content = {}
                state = MHTML_CONTENT
        elif state == MHTML_CONTENT:
            if boundary is not None:
                if index_start_embedded_mhtml is None:
                    index_start_embedded_mhtml = index_mhtml
                next_line = get_line()
                if not _is_line_feed(next_line):
                    split_headers(next_line, content)
                else:
                    init_resource(content)
                    if not resource.content_type or not is_multipart_alternative(resource.content_type):
                        index_start_embedded_mhtml = None
                    state = MHTML_DATA
            else:
                missing_delimiters = is_multipart(headers.get("content-type"))
                if missing_delimiters:
                    anomalies.append(Anomaly(MISSING_DELIMITERS_ANOMALY))
                init_resource(read_body_headers() if missing_delimiters else headers)
                state = MHTML_DATA
        elif state == MHTML_DATA:
            index_end_data = parse_resource_data()
            resource.data = flatten_data()
            if index_start_embedded_mhtml is not None and index_end_data is not None:
                resource.used = True
                context["index"] = convert_embedded_mhtml(index_end_data)
            else:
                process_resource()
            state = MHTML_END if index_mhtml >= n - 1 else MHTML_CONTENT

    return Mhtml(headers=headers, frames=frames, resources=resources, index=context.get("index"), anomalies=anomalies)


# ===========================================================================
# Resource DOM cache (port of util.js cacheResourceDOM machinery)
# ===========================================================================


def cache_resource_dom(resource: Resource, dom: DomDocument) -> None:
    resource.dom = dom
    resource.dom_data = resource.data


def get_resource_dom(resource: Resource, content_type: Optional[str]) -> DomDocument:
    if resource.dom is None or resource.dom_data != resource.data:
        cache_resource_dom(resource, parse_dom(resource.data, content_type or "text/html"))
    return resource.dom


def release_resource_dom(resource: Resource) -> None:
    resource.dom = None
    resource.dom_data = None


# ===========================================================================
# Conversion walk (port of lib/convert.js)
# ===========================================================================

CID_PROTOCOL = "cid:"
DATA_PROTOCOL = "data:"
INDEX_PAGE_NOT_FOUND_ERROR = "Index page not found"
JSON_LD_CONTENT_TYPE = "application/ld+json"

BASE_TAG, LINK_TAG, STYLE_TAG, IMG_TAG = "BASE", "LINK", "STYLE", "IMG"
AUDIO_TAG, VIDEO_TAG, SOURCE_TAG, SCRIPT_TAG = "AUDIO", "VIDEO", "SOURCE", "SCRIPT"
BODY_TAG, TABLE_TAG, TD_TAG, TH_TAG = "BODY", "TABLE", "TD", "TH"
INPUT_TAG, IFRAME_TAG, FRAME_TAG = "INPUT", "IFRAME", "FRAME"
EMBED_TAG, OBJECT_TAG, A_TAG, AREA_TAG = "EMBED", "OBJECT", "A", "AREA"
META_TAG, TEMPLATE_TAG, HEAD_TAG, TITLE_TAG = "META", "TEMPLATE", "HEAD", "TITLE"

_JSON_LD_PAGE_INFO = {
    "@context": "https://schema.org",
    "@type": "WebPage",
    "additionalProperty": {"@type": "PropertyValue", "name": "savedBy"},
}


def _js_json_stringify(value: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _get_original_url_function(id_value: str, resource_uri: Optional[str] = None) -> str:
    if resource_uri is None:
        resource_uri = id_value
    return f"{ORIGINAL_URL_FUNCTION_NAME}({_js_json_stringify(id_value)}) {resource_uri}"


def _get_resource(resources: dict[str, Resource], id_value: Optional[str], raw_id: Optional[str]) -> Optional[Resource]:
    resource = resources.get(id_value) if id_value else None
    if resource is None:
        resource = resources.get(raw_id) if raw_id else None
    if resource is None and id_value and id_value.startswith(CID_PROTOCOL):
        resource = resources.get(f"<{id_value[len(CID_PROTOCOL) :]}>")
    return resource


def _add_missing_resource(missing_resources: list, id_value: str, transfer_encoding: Optional[str]) -> None:
    if id_value.startswith("http:") or id_value.startswith("https:") or id_value.startswith("urn:"):
        if not any(r["id"] == id_value for r in missing_resources):
            missing_resources.append({"id": id_value, "transferEncoding": transfer_encoding})


def _resolve_reference(resources: dict[str, Resource], missing_resources, unfound: dict, reference: dict) -> None:
    """The one place where the collecting walk and the rewriting walk differ.

    ``missing_resources`` is a list during collection (references are recorded
    and left alone) and ``None`` during rewriting (references are resolved)."""
    if "resource" in reference:
        resource: Optional[Resource] = reference["resource"]
    else:
        resource = _get_resource(resources, reference.get("id"), reference.get("rawId"))
    if resource is not None:
        if reference.get("descend") is not None:
            reference["descend"](resource)
        if missing_resources is None:  # rewriting walk
            resource.used = True
            reference["found"](resource)
    elif missing_resources is not None:  # collecting walk
        _add_missing_resource(missing_resources, reference["id"], reference.get("encoding"))
    else:
        unfound[reference["id"]] = True
        reference["notFound"]()


def _set_attribute(element, attribute: str, new_value: Optional[str]) -> None:
    value = element_get_attribute(element, attribute)
    if value and not value.startswith(DATA_PROTOCOL) and value != new_value:
        element_set_attribute(element, ORIGINAL_URL_ATTRIBUTE_PREFIX + attribute, value)
    if new_value is not None:
        element_set_attribute(element, attribute, new_value)


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;")


def _replace_stylesheet_urls(
    resources: dict[str, Resource],
    base: Optional[str],
    resource_obj: dict,
    context_kind: str,
    stylesheets: dict,
    missing_resources,
    unfound: dict,
) -> str:
    """Rewrite every ``url()``/``@import`` of a stylesheet (or declaration list)."""
    resource_id = resource_obj.get("id")
    if resource_id is not None:
        cached = stylesheets.get(resource_id)
        if cached is not None:
            return cached.get("data", resource_obj.get("data", ""))
        stylesheets[resource_id] = {}
    try:
        if context_kind == "stylesheet":
            items = parse_stylesheet(resource_obj.get("data", ""))
        else:
            items = parse_declaration_list(resource_obj.get("data", ""))
    except Exception:
        return resource_obj.get("data", "")

    def visit(node) -> None:
        if isinstance(node, CssUrl):
            path = node.value
            if path and not path.startswith(DATA_PROTOCOL) and not path.startswith(ORIGINAL_URL_FUNCTION_NAME):
                id_value = resolve_path(path, base)

                def found(resource: Resource) -> None:
                    if is_stylesheet(resource.content_type):
                        resource.data = _replace_stylesheet_urls(
                            resources,
                            resource.id,
                            {"id": resource.id, "data": resource.data},
                            "stylesheet",
                            stylesheets,
                            missing_resources,
                            unfound,
                        )
                    node.value = _get_original_url_function(
                        id_value,
                        get_resource_uri(resource.content_type, resource.transfer_encoding, str(resource.data)),
                    )

                def not_found() -> None:
                    node.value = _get_original_url_function(id_value)

                _resolve_reference(
                    resources,
                    missing_resources,
                    unfound,
                    {
                        "id": id_value,
                        "rawId": path,
                        "encoding": BASE64_ENCODING,
                        "found": found,
                        "notFound": not_found,
                    },
                )
        elif isinstance(node, CssAtrule) and node.name.lower() == "import":
            if node.prelude:
                import_node = node.prelude[0]
                if isinstance(import_node, (CssStr, CssUrl)):
                    path = import_node.value
                    if path and not path.startswith(DATA_PROTOCOL) and not path.startswith(ORIGINAL_URL_FUNCTION_NAME):
                        id_value = resolve_path(path, base)

                        def descend(resource: Resource) -> None:
                            resource.data = _replace_stylesheet_urls(
                                resources,
                                resource.id,
                                {"id": resource.id, "data": resource.data},
                                "stylesheet",
                                stylesheets,
                                missing_resources,
                                unfound,
                            )

                        def found(resource: Resource) -> None:
                            node.prelude[0] = CssUrl(
                                _get_original_url_function(
                                    id_value,
                                    get_resource_uri(
                                        resource.content_type, resource.transfer_encoding, str(resource.data)
                                    ),
                                )
                            )

                        def not_found() -> None:
                            node.prelude[0] = CssUrl(_get_original_url_function(id_value))

                        _resolve_reference(
                            resources,
                            missing_resources,
                            unfound,
                            {
                                "id": id_value,
                                "rawId": path,
                                "descend": descend,
                                "found": found,
                                "notFound": not_found,
                            },
                        )

    walk_css_nodes(items, visit)
    try:
        if context_kind == "stylesheet":
            generated = generate_stylesheet(items)
        else:
            generated = generate_declaration_list(items)
        result = ORIGINAL_URL_FUNCTION_REGEXP.sub(ORIGINAL_URL_COMMENT, generated)
        if resource_id is not None:
            stylesheets[resource_id]["data"] = result
        return result
    except Exception:
        return resource_obj.get("data", "")


def _create_document(resources: dict[str, Resource]) -> Optional[Resource]:
    """Build the page an archive without a document is presented as."""
    for resource in resources.values():
        content_type = resource.content_type
        if is_image(content_type):
            uri = get_resource_uri(content_type, resource.transfer_encoding, str(resource.data))
            return Resource(
                id=resource.id,
                content_type="text/html",
                transfer_encoding=None,
                data=f'<html><body><img src="{uri}"></body></html>',
            )
        if is_plain_text(content_type):
            if resource.transfer_encoding == BASE64_ENCODING:
                text = decode_base64(str(resource.data), get_charset(content_type))
            else:
                text = str(resource.data)
            return Resource(
                id=resource.id,
                content_type="text/html",
                transfer_encoding=None,
                data=f"<html><body><pre>{_escape_text(text)}</pre></body></html>",
            )
    return None


def _get_page_info(headers: dict[str, str], index: str) -> dict:
    info = {
        "@context": _JSON_LD_PAGE_INFO["@context"],
        "@type": _JSON_LD_PAGE_INFO["@type"],
        "additionalProperty": {
            **_JSON_LD_PAGE_INFO["additionalProperty"],
            "value": decode_mime_header(headers.get("from")),
        },
        "url": index,
        "name": decode_mime_header(headers.get("subject")),
    }
    if headers.get("date") is not None:
        info["dateCreated"] = headers.get("date")
    return info


def _convert_impl(data: dict, config: "ConvertConfig"):
    """The conversion walk.  Returns a list of missing resources when
    ``config.fetch_missing_resources`` (collecting walk) or a result dict
    (rewriting walk)."""
    resources: dict[str, Resource] = data["resources"]
    frames: dict[str, Resource] = data["frames"]
    headers: Optional[dict[str, str]] = data.get("headers")
    unfound: dict = data.setdefault("unfoundResources", {})
    index = data.get("index")
    frame_id_param = data.get("id")
    anomalies: list = data.get("anomalies") or []
    fetch_missing = config.fetch_missing_resources
    enable_scripts = config.enable_scripts

    resource = resources.get(index) if index else None
    if resource is None:
        resource = _create_document(resources)
        if resource is None:
            raise IndexPageNotFoundError(INDEX_PAGE_NOT_FOUND_ERROR)
        index = resource.id
        if not fetch_missing:
            # reported on a copy: converting again reports it once more
            anomalies = list(anomalies) + [Anomaly(SYNTHESIZED_INDEX_ANOMALY, index)]

    base = resource.id
    if resource.transfer_encoding == BASE64_ENCODING:
        resource.transfer_encoding = None
        resource.data = decode_base64(str(resource.data), get_charset(resource.content_type))
    content_type = (resource.content_type or "").split(";")[0] if resource.content_type else None
    dom = get_resource_dom(resource, content_type)
    if not fetch_missing:
        release_resource_dom(resource)
    document = dom.document

    nodes: list = [document]
    base_element = None
    for candidate in document.getElementsByTagName("base"):
        base_element = candidate
        break
    if base_element is not None:
        href = element_get_attribute(base_element, "href")
        if href:
            base = resolve_path(href, base)
        if not fetch_missing:
            element_remove(base_element)
    if not fetch_missing:
        resource.used = True

    canonical_link_element = None
    stylesheets: dict = {}
    missing_resources = [] if fetch_missing else None
    removed_nodes: list = []
    favicons: list = []
    title: Optional[str] = None
    index_node = 0

    while index_node < len(nodes):
        child_node = nodes[index_node]
        index_node += 1
        child_nodes = getattr(child_node, "childNodes", None)
        if not child_nodes:
            continue
        parent_tag_name = _tag_name(child_node)
        parent_tag_name = parent_tag_name.upper() if parent_tag_name else None
        for child in list(child_nodes):
            tag_name = _tag_name(child)
            tag_name = tag_name.upper() if tag_name else None
            href = None
            src = None
            is_element = hasattr(child, "attributes")
            if is_element:
                href = element_get_attribute(child, "href")
                if href:
                    href = resolve_path(href, base)
                src = element_get_attribute(child, "src")
                if src:
                    src = resolve_path(src, base)
                style = element_get_attribute(child, "style")
                if style:
                    declarations = _replace_stylesheet_urls(
                        resources, base, {"data": style}, "declarationList", stylesheets, missing_resources, unfound
                    )
                    if not fetch_missing:
                        element_set_attribute(child, "style", declarations)
                integrity = element_get_attribute(child, "integrity")
                if integrity:
                    element_remove_attribute(child, "integrity")
            if not enable_scripts and is_element:
                for attribute_name in element_get_attribute_names(child):
                    if attribute_name.lower() in _EVENT_HANDLER_ATTRIBUTE_SET:
                        element_remove_attribute(child, attribute_name)

            if tag_name == LINK_TAG and href:
                rel = element_get_attribute(child, "rel")
                if rel:
                    rel = rel.lower()
                    rel_tokens = REL_SEPARATOR_REGEXP.split(rel.strip())
                    if "stylesheet" in rel_tokens:
                        stylesheet_result: list = []

                        def descend(resource_ref: Resource) -> None:
                            style_base = resource_ref.id
                            if style_base.startswith(CID_PROTOCOL):
                                if index and CID_REGEXP.match(index):
                                    style_base = frame_id_param
                                else:
                                    style_base = index
                            stylesheet_result.append(
                                _replace_stylesheet_urls(
                                    resources,
                                    style_base,
                                    {"id": resource_ref.id, "data": resource_ref.data},
                                    "stylesheet",
                                    stylesheets,
                                    missing_resources,
                                    unfound,
                                )
                            )

                        def found(resource_ref: Resource) -> None:
                            if stylesheet_result:
                                resource_ref.data = stylesheet_result[0]
                            media = element_get_attribute(child, "media")
                            if element_get_attribute(child, "title") or "alternate" in rel_tokens:
                                _set_attribute(
                                    child,
                                    "href",
                                    get_resource_uri(
                                        resource_ref.content_type,
                                        resource_ref.transfer_encoding,
                                        str(resource_ref.data),
                                    ),
                                )
                            else:
                                style_element = dom.create_element(STYLE_TAG.lower())
                                if media:
                                    element_set_attribute(style_element, "media", media)
                                if not href.startswith(DATA_PROTOCOL):
                                    element_set_attribute(style_element, ORIGINAL_URL_ATTRIBUTE_PREFIX + "href", href)
                                element_append_child(style_element, dom.create_text_node(str(resource_ref.data)))
                                element_replace_with(child, style_element)

                        def not_found() -> None:
                            _set_attribute(child, "href", href)

                        _resolve_reference(
                            resources,
                            missing_resources,
                            unfound,
                            {
                                "id": href,
                                "rawId": element_get_attribute(child, "href"),
                                "descend": descend,
                                "found": found,
                                "notFound": not_found,
                            },
                        )
                    elif "icon" in rel:
                        media = element_get_attribute(child, "media")
                        type_attr = element_get_attribute(child, "type")
                        sizes = element_get_attribute(child, "sizes")

                        def found_icon(resource_ref: Resource) -> None:
                            resource_uri = get_resource_uri(
                                resource_ref.content_type, resource_ref.transfer_encoding, str(resource_ref.data)
                            )
                            _set_attribute(child, "href", resource_uri)
                            favicons.append({
                                "href": resource_uri,
                                "media": media,
                                "type": type_attr,
                                "sizes": sizes,
                                "originalHref": href,
                            })

                        def not_found_icon() -> None:
                            _set_attribute(child, "href", href)
                            favicons.append({
                                "href": href,
                                "media": media,
                                "type": type_attr,
                                "sizes": sizes,
                            })

                        _resolve_reference(
                            resources,
                            missing_resources,
                            unfound,
                            {
                                "id": href,
                                "rawId": element_get_attribute(child, "href"),
                                "encoding": BASE64_ENCODING,
                                "found": found_icon,
                                "notFound": not_found_icon,
                            },
                        )
                    elif rel == "canonical" and not fetch_missing:
                        canonical_link_element = child
                    if not fetch_missing:
                        rel_value = REL_REMOVED_VALUES_REGEXP.sub("", rel).strip()
                        if rel_value:
                            element_set_attribute(child, "rel", rel_value)
                        else:
                            removed_nodes.append(child)
            elif tag_name == STYLE_TAG:
                style = _replace_stylesheet_urls(
                    resources,
                    base,
                    {"data": _element_text_content(child)},
                    "stylesheet",
                    stylesheets,
                    missing_resources,
                    unfound,
                )
                if not fetch_missing:
                    style_element = dom.create_element(STYLE_TAG.lower())
                    media = element_get_attribute(child, "media")
                    if media:
                        element_set_attribute(style_element, "media", media)
                    title_attr = element_get_attribute(child, "title")
                    if title_attr:
                        element_set_attribute(style_element, "title", title_attr)
                    element_append_child(style_element, dom.create_text_node(style))
                    element_replace_with(child, style_element)
            elif tag_name in (IMG_TAG, AUDIO_TAG, VIDEO_TAG, SOURCE_TAG, SCRIPT_TAG):
                if src:

                    def found_src(resource_ref: Resource) -> None:
                        _set_attribute(
                            child,
                            "src",
                            get_resource_uri(
                                resource_ref.content_type, resource_ref.transfer_encoding, str(resource_ref.data)
                            ),
                        )

                    def not_found_src() -> None:
                        _set_attribute(child, "src", src)

                    _resolve_reference(
                        resources,
                        missing_resources,
                        unfound,
                        {
                            "id": src,
                            "rawId": element_get_attribute(child, "src"),
                            "encoding": BASE64_ENCODING,
                            "found": found_src,
                            "notFound": not_found_src,
                        },
                    )
                if tag_name in (IMG_TAG, SOURCE_TAG):
                    srcset = element_get_attribute(child, "srcset")
                    if srcset:
                        srcset_data = parse_srcset(srcset)
                        for candidate in srcset_data:
                            candidate_url = resolve_path(candidate["url"], base)

                            def found_srcset(resource_ref: Resource, _c=candidate) -> None:
                                _c["url"] = get_resource_uri(
                                    resource_ref.content_type, resource_ref.transfer_encoding, str(resource_ref.data)
                                )

                            def not_found_srcset(_u=candidate_url, _c=candidate) -> None:
                                _c["url"] = _u

                            _resolve_reference(
                                resources,
                                missing_resources,
                                unfound,
                                {
                                    "id": candidate_url,
                                    "rawId": candidate["url"],
                                    "encoding": BASE64_ENCODING,
                                    "found": found_srcset,
                                    "notFound": not_found_srcset,
                                },
                            )
                        if not fetch_missing:
                            _set_attribute(child, "srcset", serialize_srcset(srcset_data))
                elif tag_name == SCRIPT_TAG and not fetch_missing:
                    type_attr = element_get_attribute(child, "type")
                    if type_attr:
                        type_attr = type_attr.lower()
                    if not enable_scripts and (not type_attr or type_attr != JSON_LD_CONTENT_TYPE):
                        removed_nodes.append(child)
            elif tag_name in (BODY_TAG, TABLE_TAG, TD_TAG, TH_TAG):
                background = element_get_attribute(child, "background")
                if background and not background.startswith(DATA_PROTOCOL):
                    resolved_background = resolve_path(background, base)

                    def found_background(resource_ref: Resource) -> None:
                        _set_attribute(
                            child,
                            "background",
                            get_resource_uri(
                                resource_ref.content_type, resource_ref.transfer_encoding, str(resource_ref.data)
                            ),
                        )

                    def not_found_background() -> None:
                        _set_attribute(child, "background", resolved_background)

                    _resolve_reference(
                        resources,
                        missing_resources,
                        unfound,
                        {
                            "id": resolved_background,
                            "rawId": background,
                            "encoding": BASE64_ENCODING,
                            "found": found_background,
                            "notFound": not_found_background,
                        },
                    )
            elif tag_name == INPUT_TAG:
                type_attr = element_get_attribute(child, "type")
                if type_attr and type_attr.lower() == "image" and src:

                    def found_input(resource_ref: Resource) -> None:
                        _set_attribute(
                            child,
                            "src",
                            get_resource_uri(
                                resource_ref.content_type, resource_ref.transfer_encoding, str(resource_ref.data)
                            ),
                        )

                    def not_found_input() -> None:
                        _set_attribute(child, "src", src)

                    _resolve_reference(
                        resources,
                        missing_resources,
                        unfound,
                        {
                            "id": src,
                            "rawId": element_get_attribute(child, "src"),
                            "encoding": BASE64_ENCODING,
                            "found": found_input,
                            "notFound": not_found_input,
                        },
                    )
            elif tag_name in (IFRAME_TAG, FRAME_TAG, EMBED_TAG, OBJECT_TAG):
                if tag_name == OBJECT_TAG:
                    attribute = "data"
                    src = element_get_attribute(child, "data")
                    if src:
                        src = resolve_path(src, base)
                else:
                    attribute = "src"
                if src:
                    frame_id = None
                    frame_resource = None
                    if src.startswith(CID_PROTOCOL):
                        frame_id = f"<{src.split(CID_PROTOCOL, 1)[1]}>"
                        frame_resource = frames.get(frame_id)
                    else:
                        frame_id = src
                        frame_resource = _get_resource(resources, src, element_get_attribute(child, attribute))
                    if frame_resource is not None and not (
                        tag_name in (EMBED_TAG, OBJECT_TAG) or is_media(frame_resource.content_type)
                    ):
                        result = _convert_impl(
                            {
                                "resources": {**resources, frame_id: frame_resource},
                                "unfoundResources": unfound,
                                "frames": frames,
                                "index": frame_id,
                                "id": frame_resource.id,
                            },
                            config,
                        )
                        if fetch_missing:
                            for missing_resource in result:
                                if not any(r["id"] == missing_resource["id"] for r in missing_resources):
                                    missing_resources.append(missing_resource)
                        else:
                            frame_resource.used = True
                            if tag_name == IFRAME_TAG:
                                _set_attribute(child, "src", None)
                                element_remove_attribute(child, "src")
                                element_set_attribute(child, "srcdoc", result["data"])
                            else:
                                element_set_attribute(
                                    child,
                                    attribute,
                                    DATA_PROTOCOL + "text/html," + urllib.parse.quote(result["data"], safe=""),
                                )
                    else:
                        reference_resource = frame_resource

                        def found_frame(resource_ref: Resource) -> None:
                            _set_attribute(
                                child,
                                attribute,
                                get_resource_uri(
                                    resource_ref.content_type, resource_ref.transfer_encoding, str(resource_ref.data)
                                ),
                            )

                        def not_found_frame() -> None:
                            _set_attribute(child, attribute, src)

                        _resolve_reference(
                            resources,
                            missing_resources,
                            unfound,
                            {
                                "id": src,
                                "resource": reference_resource,
                                "found": found_frame,
                                "notFound": not_found_frame,
                            },
                        )
            elif tag_name in (A_TAG, AREA_TAG) and not fetch_missing:
                if href:
                    try:
                        raw_href = element_get_attribute(child, "href")
                        url_href = js_new_url(raw_href, base)
                        hash_part = _url_fragment(url_href)
                        url_no_frag = url_href[: len(url_href) - len(hash_part)] if hash_part else url_href
                        if url_no_frag == base and hash_part:
                            element_set_attribute(child, "href", hash_part)
                        else:
                            element_set_attribute(child, "href", href)
                    except (UrlError, ValueError):
                        element_set_attribute(child, "href", href)
                element_remove_attribute(child, "ping")
            elif tag_name == META_TAG and not fetch_missing:
                http_equiv = element_get_attribute(child, "http-equiv")
                if http_equiv and http_equiv.lower() in ("refresh", "content-security-policy"):
                    removed_nodes.append(child)
            elif tag_name == TEMPLATE_TAG and not fetch_missing:
                shadow_mode = element_get_attribute(child, "shadowmode")
                if shadow_mode:
                    element_remove_attribute(child, "shadowmode")
                    element_set_attribute(child, "shadowrootmode", shadow_mode)
            elif tag_name == TITLE_TAG and not fetch_missing and parent_tag_name == HEAD_TAG and title is None:
                text_content = _element_text_content(child)
                if text_content:
                    title = text_content
            nodes.append(child)

    if fetch_missing:
        return missing_resources

    for node in removed_nodes:
        element_remove(node)
    head = dom.head
    if head is not None:
        if canonical_link_element is None:
            link_element = dom.create_element(LINK_TAG.lower())
            element_set_attribute(link_element, "rel", "canonical")
            element_set_attribute(link_element, "href", index)
            element_append_child(head, link_element)
        meta_element = dom.create_element(META_TAG.lower())
        element_set_attribute(meta_element, "http-equiv", "content-security-policy")
        csp = DEFAULT_CSP + (JS_ENABLED_CSP if enable_scripts else JS_DISABLED_CSP)
        element_set_attribute(meta_element, "content", csp)
        if head.firstChild:
            element_prepend(head, meta_element)
        else:
            element_append_child(head, meta_element)
        meta_element = dom.create_element(META_TAG.lower())
        element_set_attribute(meta_element, "charset", "utf-8")
        element_prepend(head, meta_element)
        if headers:
            page_info_element = dom.create_element(SCRIPT_TAG.lower())
            element_set_attribute(page_info_element, "type", JSON_LD_CONTENT_TYPE)
            element_append_child(
                page_info_element,
                dom.create_text_node(json.dumps(_get_page_info(headers, index), indent=2, ensure_ascii=False)),
            )
            if head.firstChild:
                element_after(head.firstChild, page_info_element)
            else:
                element_append_child(head, page_info_element)

    unfound_resource_ids: list[str] = []
    if unfound:
        for id_value in unfound:
            if not id_value.startswith(DATA_PROTOCOL):
                resources[id_value] = Resource(id=id_value, not_found=True, used=True)
                unfound_resource_ids.append(id_value)

    return {
        "title": title,
        "favicons": favicons,
        "unfoundResources": unfound_resource_ids,
        "anomalies": anomalies,
        "data": dom.serialize(),
    }


def _element_text_content(element) -> str:
    parts: list[str] = []
    for node in element.childNodes:
        if getattr(node, "nodeType", None) == 3:
            parts.append(node.data)
        else:
            parts.append(_element_text_content(node))
    return "".join(parts)


def _url_fragment(url: str) -> str:
    idx = _find_unescaped(url, "#")
    return url[idx:] if idx is not None else ""


# ===========================================================================
# Network fetching (retries + SSRF protections)
# ===========================================================================


@dataclass
class FetchConfig:
    """Controls for fetching missing resources.

    These protections are a deliberate addition over the Node.js original
    (which fetches without any restriction); every one of them is optional.
    """

    max_retries: int = DEFAULT_MAX_RETRIES
    max_parallel_requests: int = DEFAULT_MAX_PARALLEL_REQUESTS
    connect_timeout: float = 15.0
    read_timeout: float = 30.0
    max_response_size: int = 64 * 1024 * 1024
    max_redirects: int = 10
    allowed_schemes: tuple[str, ...] = ("http", "https")
    allow_private_hosts: bool = False
    user_agent: str = f"mhtml-to-html-python/{__version__}"


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def _validate_url_for_fetch(url: str, fetch_config: FetchConfig) -> None:
    """Scheme + DNS validation; raises :class:`MhtmlError` when blocked."""
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in fetch_config.allowed_schemes:
        raise MhtmlError(f"scheme not allowed for fetching: {scheme!r}")
    host = parts.hostname
    if not host:
        raise MhtmlError("URL has no host")
    if fetch_config.allow_private_hosts:
        return
    try:
        addrinfos = socket.getaddrinfo(host, parts.port or (443 if scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise MhtmlError(f"cannot resolve {host!r}: {exc}") from exc
    for addrinfo in addrinfos:
        ip = ipaddress.ip_address(addrinfo[4][0])
        if _is_blocked_address(ip):
            raise MhtmlError(
                f"refusing to fetch from non-public address {ip} (use "
                "allow_private_hosts/FetchConfig or --allow-private-network to override)"
            )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _read_capped(response, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = response.read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    else:
        raise MhtmlError(f"response exceeds the size limit ({limit} bytes)")
    return b"".join(chunks)


def _fetch_once(url: str, fetch_config: FetchConfig) -> tuple[int, Any, bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": fetch_config.user_agent,
            "Accept": "*/*",
        },
    )
    timeout = max(fetch_config.connect_timeout, fetch_config.read_timeout)
    try:
        response = _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # read the (small) error body so the connection is reused cleanly
        try:
            exc.read(1024)
        except Exception:
            pass
        headers = exc.headers
        return exc.code, headers, b""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise MhtmlError(str(exc)) from exc
    with response:
        headers = response.headers
        try:
            payload = _read_capped(response, fetch_config.max_response_size)
        except MhtmlError:
            raise
        return response.status if hasattr(response, "status") else 200, headers, payload


def _parse_retry_after(retry_after: Optional[str]) -> Optional[float]:
    if not retry_after:
        return None
    try:
        seconds = float(retry_after)
        return max(0.0, seconds)
    except ValueError:
        pass
    try:
        import email.utils

        parsed = email.utils.parsedate_to_datetime(retry_after)
        delta = parsed.timestamp() - time.time()
        return max(0.0, delta)
    except (ValueError, TypeError, OverflowError):
        return None


def _is_transient_status(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


def _backoff_delay(attempt: int) -> float:
    return min(RETRY_BASE_DELAY * (2**attempt), MAX_RETRY_DELAY)


def fetch_resource(
    resource: dict, fetch_config: FetchConfig, fetch_fn: Optional[Callable[[str], tuple[int, Any, bytes]]] = None
) -> bool:
    """Fetch one missing resource.  Returns True on success (the resource
    dict gains ``contentType``/``data``), False when it could not be fetched."""
    url = resource["id"]
    match = URN_ERROR_REGEXP.match(url)
    if match:
        url = match.group(1)
    max_retries = fetch_config.max_retries
    attempt = 0
    while True:
        delay: Optional[float] = None
        try:
            if fetch_fn is not None:
                status, headers, payload = fetch_fn(url)
            else:
                _validate_url_for_fetch(url, fetch_config)
                status, headers, payload = _fetch_with_redirects(url, fetch_config)
            if 200 <= status < 300:
                content_type = None
                if headers is not None:
                    content_type = headers.get("Content-Type")
                resource["contentType"] = content_type or "application/octet-stream"
                if resource.get("transferEncoding") == BASE64_ENCODING:
                    resource["data"] = decode_binary(payload)
                else:
                    resource["data"] = decode_string(payload, get_charset(content_type))
                return True
            if not _is_transient_status(status):
                log.debug("fetch %s failed with status %s", url, status)
                return False
            retry_after = headers.get("Retry-After") if headers is not None else None
            parsed = _parse_retry_after(retry_after)
            delay = None if (parsed is None or parsed > MAX_RETRY_DELAY) else parsed
        except MhtmlError as exc:
            log.debug("fetch %s failed: %s", url, exc)
            delay = _backoff_delay(attempt)
        if delay is None or attempt >= max_retries:
            return False
        time.sleep(delay)
        attempt += 1


def _fetch_with_redirects(url: str, fetch_config: FetchConfig) -> tuple[int, Any, bytes]:
    current = url
    for _ in range(fetch_config.max_redirects + 1):
        status, headers, payload = _fetch_once(current, fetch_config)
        if status in (301, 302, 303, 307, 308):
            location = headers.get("Location") if headers is not None else None
            if not location:
                return status, headers, payload
            current = urllib.parse.urljoin(current, location)
            _validate_url_for_fetch(current, fetch_config)
            continue
        return status, headers, payload
    raise MhtmlError(f"too many redirects fetching {url}")


def _fetch_resources(
    missing_resources: list, mhtml_obj, fetch_config: FetchConfig, config: "ConvertConfig", failed_resources: list
) -> None:
    count_workers = max(1, min(fetch_config.max_parallel_requests, len(missing_resources)))
    failed_lock = threading.Lock()

    def work(entry: dict) -> None:
        if fetch_resource(entry, fetch_config, config.fetch_fn):
            mhtml_obj.resources[entry["id"]] = Resource(
                id=entry["id"],
                content_type=entry.get("contentType"),
                transfer_encoding=entry.get("transferEncoding"),
                data=entry.get("data", ""),
            )
        else:
            with failed_lock:
                if entry["id"] not in failed_resources:
                    failed_resources.append(entry["id"])

    with ThreadPoolExecutor(max_workers=count_workers) as executor:
        list(executor.map(work, missing_resources))


# ===========================================================================
# Public API
# ===========================================================================


@dataclass
class ConvertConfig:
    """Configuration for :func:`convert`.

    Note: ``enable_scripts`` and ``fetch_missing_resources`` default to
    ``True`` — a deliberate deviation from the Node.js library (both default
    to ``False`` there) applied per this port's requirements.
    """

    enable_scripts: bool = True
    fetch_missing_resources: bool = True
    fetch_config: FetchConfig = field(default_factory=FetchConfig)
    fetch_fn: Optional[Callable[[str], tuple[int, Any, bytes]]] = None

    def _with(self, **changes) -> "ConvertConfig":
        data = {
            "enable_scripts": self.enable_scripts,
            "fetch_missing_resources": self.fetch_missing_resources,
            "fetch_config": self.fetch_config,
            "fetch_fn": self.fetch_fn,
        }
        data.update(changes)
        return ConvertConfig(**data)


def parse(data: Union[str, bytes, bytearray, Path]) -> Mhtml:
    """Parse MHTML data (bytes, string or file path) into an :class:`Mhtml`."""
    if isinstance(data, Path):
        data = data.read_bytes()
    return parse_mhtml(data)


def convert(mhtml: Union[str, bytes, bytearray, Path, Mhtml], config: Optional[ConvertConfig] = None) -> "PageData":
    """Convert an MHTML archive into a single HTML page.

    Returns a :class:`PageData` with the HTML, title, favicons, the
    references that resolved nowhere and the anomalies recovered from.
    Raises :class:`IndexPageNotFoundError` when the archive holds nothing
    that can be presented as a page.
    """
    if config is None:
        config = ConvertConfig()
    if isinstance(mhtml, (str, bytes, bytearray, Path)):
        mhtml = parse(mhtml)
    result = _fetch_and_convert(mhtml, config, None)
    return PageData(
        data=result["data"],
        title=result.get("title"),
        unfound_resources=list(result.get("unfoundResources", [])),
        anomalies=[a if isinstance(a, Anomaly) else Anomaly(**a) for a in result.get("anomalies", [])],
        favicons=[_favicon_from_dict(f) for f in result.get("favicons", [])],
    )


def _favicon_from_dict(f: dict) -> "Favicon":
    return Favicon(
        href=f["href"],
        media=f.get("media"),
        type=f.get("type"),
        sizes=f.get("sizes"),
        original_href=f.get("originalHref"),
    )


@dataclass
class Favicon:
    href: str
    media: Optional[str] = None
    type: Optional[str] = None
    sizes: Optional[str] = None
    original_href: Optional[str] = None


@dataclass
class PageData:
    data: str
    title: Optional[str] = None
    unfound_resources: list[str] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)
    favicons: list[Favicon] = field(default_factory=list)


def _fetch_and_convert(mhtml: Mhtml, config: ConvertConfig, failed_resources: Optional[list]) -> dict:
    if config.fetch_missing_resources:
        if failed_resources is None:
            failed_resources = []
        data = {
            "headers": mhtml.headers,
            "frames": mhtml.frames,
            "resources": mhtml.resources,
            "index": mhtml.index,
            "anomalies": mhtml.anomalies,
        }
        missing = _convert_impl(data, config)
        missing = [r for r in missing if r["id"] not in failed_resources]
        if missing:
            _fetch_resources(missing, mhtml, config.fetch_config, config, failed_resources)
            return _fetch_and_convert(mhtml, config, failed_resources)
        return _convert_impl(data, config._with(fetch_missing_resources=False))
    data = {
        "headers": mhtml.headers,
        "frames": mhtml.frames,
        "resources": mhtml.resources,
        "index": mhtml.index,
        "anomalies": mhtml.anomalies,
    }
    return _convert_impl(data, config)


# ===========================================================================
# CLI
# ===========================================================================

MHTML_EXTENSIONS = (".mht", ".mhtml")
USAGE_EXIT = 1
CONVERSION_FAILURE_EXIT = 2


def _discover_inputs(paths: Sequence[str]) -> list[Path]:
    """Expand files, directories, and glob patterns into a list of archives."""
    import glob as glob_module

    result: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        if path not in seen:
            seen.add(path)
            result.append(path)

    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for pattern in ("**/*.mht", "**/*.mhtml", "**/*.MHT", "**/*.MHTML"):
                for found in sorted(path.glob(pattern)):
                    add(found.resolve())
            continue
        if path.exists():
            add(path.resolve())
            continue
        # shell-unexpanded glob pattern?
        if any(ch in raw for ch in "*?["):
            matches = sorted(glob_module.glob(raw, recursive=True))
            if matches:
                for found in matches:
                    add(Path(found).resolve())
                continue
            log.error("no matches for pattern: %s", raw)
            continue
        log.error("input not found: %s", raw)
    return result


def _default_output_path(input_path: Path) -> Path:
    name = input_path.name
    if "." in name:
        stem = name.rsplit(".", 1)[0]
        candidate = input_path.with_name(stem + ".html")
    else:
        candidate = input_path.with_name(name + ".html")
    if candidate.suffix != ".html":
        candidate = candidate.with_name(candidate.name + ".html")
    return candidate


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mhtml-to-html",
        description="Convert MHTML/MHT archives into single self-contained HTML files "
        "(Python port of the mhtml-to-html library).",
        epilog="examples:\n"
        "  mhtml-to-html page.mht\n"
        "  mhtml-to-html page.mht --output out.html\n"
        "  mhtml-to-html folder/            # recursive batch conversion\n"
        "  mhtml-to-html *.mht *.mhtml\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        metavar="INPUT",
        help="input .mht/.mhtml files, directories (searched recursively) "
        "or glob patterns; defaults to a recursive search of the "
        "current directory",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="OUTPUT",
        help="output HTML file (single input only); default: the input name with a .html extension",
    )
    parser.add_argument(
        "--enable-scripts",
        action="store_true",
        default=True,
        help="keep scripts enabled (default: enabled in this port; the original defaults to disabled)",
    )
    parser.add_argument(
        "--no-scripts",
        dest="enable_scripts",
        action="store_false",
        help="remove scripts and event handlers (the original's default)",
    )
    parser.add_argument(
        "--fetch-missing-resources",
        action="store_true",
        default=True,
        help="fetch resources missing from the archive over the network "
        "(default: enabled in this port; the original defaults to "
        "disabled)",
    )
    parser.add_argument(
        "--no-fetch-missing-resources",
        dest="fetch_missing_resources",
        action="store_false",
        help="do not fetch missing resources (the original's default)",
    )
    parser.add_argument(
        "--allow-private-network",
        action="store_true",
        help="allow fetching from loopback/private/link-local addresses (blocked by default as an SSRF protection)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="connect/read timeout for network fetches (default: 15/30s)",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=None,
        metavar="BYTES",
        help="maximum response size for network fetches (default: 64 MiB)",
    )
    parser.add_argument("--fail-fast", action="store_true", help="abort the batch on the first conversion failure")
    parser.add_argument("--no-clobber", action="store_true", help="never overwrite an existing output file")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress per-file logs")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}", help="show the version number and exit"
    )
    return parser


def _convert_file(input_path: Path, output_path: Path, config: ConvertConfig) -> None:
    mhtml = parse(input_path.read_bytes())
    result = convert(mhtml, config)
    output_path.write_text(result.data, encoding="utf-8", newline="")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else (logging.ERROR if args.quiet else logging.INFO),
        format="%(levelname)s: %(message)s",
        force=True,
    )

    fetch_config = FetchConfig()
    if args.allow_private_network:
        fetch_config.allow_private_hosts = True
    if args.timeout is not None:
        fetch_config.connect_timeout = args.timeout
        fetch_config.read_timeout = args.timeout
    if args.max_size is not None:
        fetch_config.max_response_size = args.max_size
    config = ConvertConfig(
        enable_scripts=args.enable_scripts,
        fetch_missing_resources=args.fetch_missing_resources,
        fetch_config=fetch_config,
    )

    if args.inputs:
        inputs = _discover_inputs(args.inputs)
    else:
        inputs = _discover_inputs(["."])
    if not inputs:
        if args.inputs:
            log.error("no MHTML files found")
            return USAGE_EXIT
        log.info("no .mht/.mhtml files found in the current directory")
        return 0

    if args.output and len(inputs) > 1:
        log.error("--output can only be used with a single input file")
        return USAGE_EXIT

    failures = 0
    successes = 0
    for index, input_path in enumerate(inputs):
        if args.output and len(inputs) == 1:
            output_path = Path(args.output)
            if output_path.suffix != ".html":
                output_path = output_path.with_name(output_path.name + ".html")
        else:
            output_path = _default_output_path(input_path)
        try:
            if output_path.resolve() == input_path.resolve():
                raise MhtmlError(f"output path would overwrite the input archive: {output_path}")
            if args.no_clobber and output_path.exists():
                raise MhtmlError(f"output file already exists: {output_path}")
            _convert_file(input_path, output_path, config)
        except Exception as exc:
            failures += 1
            log.error("Error processing %s: %s", input_path, exc)
            if args.verbose:
                import traceback

                traceback.print_exc()
            if args.fail_fast:
                break
        else:
            successes += 1
            if not args.quiet:
                log.info("converted %s -> %s", input_path, output_path)

    if not args.quiet:
        log.info("%d file(s) converted, %d failure(s)", successes, failures)
    if failures:
        return CONVERSION_FAILURE_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
