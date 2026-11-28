#!/data/data/com.termux/files/usr/bin/env python
"""Consolidated single-file MHTML -> HTML converter.

This script merges the two uploaded converters into one:

* ``mhtml.py`` — the profile-driven converter built on lxml, tinycss2 and
  lxml_html_clean (``ConverterConfig`` / ``MHTMLParser`` / ``ResourceManager``
  / ``CSSProcessor`` / ``HTMLProcessor`` / ``MHTMLConverter``).
* ``mhtml_to_html.py`` — the Python port of the Node.js ``mhtml-to-html``
  library (robust state-machine parser, WHATWG-compatible URL resolution,
  forgiving charset/base64 decoding, spec-compliant srcset parser, batch
  CLI, ``parse``/``convert`` library API).

How the two were consolidated:

* The conversion engine is the ``mhtml.py`` pipeline: MIME parsing with the
  stdlib email parser, URL rewriting with lxml + tinycss2, optional
  sanitizing with lxml_html_clean, profile presets, SSRF-guarded external
  fetching, the resource cache and the plugin hooks.
* The malformed-archive tolerance of ``mhtml_to_html.py`` lives on as
  :class:`RobustMHTMLParser` (a port of its state-machine parser).  It runs
  automatically whenever the stdlib MIME parser cannot find a usable
  document part — mislabeled plain-HTML files, broken or unused boundaries,
  multipart bodies without delimiters, archives embedding a whole other
  MHTML — and it also powers the compatibility ``parse()`` API.
* The forgiving charset/base64/quoted-printable decoders, the
  WHATWG-compatible URL resolution, the content-type predicates and the
  spec-compliant srcset parser/serializer of ``mhtml_to_html.py`` are shared
  utilities used by both parsers.
* The CLI merges the flags of both scripts: single-file and batch
  conversion (files, directories searched recursively, glob patterns),
  profiles, external fetching with domain rules, script and CSP control,
  minification, logging and progress.

Bugs fixed while consolidating (both scripts were broken as uploaded):

1. ``mhtml.py`` evaluated forward references in class/argument annotations
   (``list[ConverterPlugin]`` in ``ConverterConfig``, ``Resource`` in
   ``resource_to_data_uri``), so importing it raised ``NameError``;
   ``from __future__ import annotations`` defers those evaluations.
2. ``mhtml_to_html.py`` iterated minidom ``NamedNodeMap`` objects directly
   (``for name in attrs:`` / ``for key in attrs:``); minidom maps have no
   ``__iter__`` and the legacy sequence protocol raises ``KeyError``, so
   every conversion of a real page crashed inside its serializer.
3. Its loguru calls used stdlib ``%s`` placeholders, hiding the actual
   errors behind messages like ``Error processing %s: %s``.
4. lxml_html_clean deleted ``<style>`` bodies that merely contained the
   ``data:`` URIs this converter inlines and stripped every ``@import``
   directive; the consolidated cleaner preserves already-URL-sanitized
   inline CSS across the sanitizing pass.
5. base64 parts that fail to decode are now flagged as anomalies and left
   as their original URLs instead of being silently double-encoded into
   data URIs.

Usage (CLI)::

    python3 mhtml_converter.py page.mhtml
    python3 mhtml_converter.py page.mhtml --output out.html
    python3 mhtml_converter.py folder/            # recursive batch conversion
    python3 mhtml_converter.py                    # converts *.mht/*.mhtml in CWD

    python3 mhtml_converter.py page.mhtml --profile secure --no-scripts
    python3 mhtml_converter.py page.mhtml --fetch-external --allow-domain example.com

Usage (library)::

    from mhtml_converter import ConverterConfig, MHTMLConverter, convert, parse

    result = convert("page.mhtml")  # -> PageData; the HTML is result.data
    document = parse("page.mhtml")  # -> Mhtml (headers/resources/index)

Dependencies: click, loguru, lxml, lxml_html_clean, tinycss2, typer.
The html5lib/minidom dependency of ``mhtml_to_html.py`` was dropped: the
consolidated robust parser uses lxml for its DOM needs.

License: MIT (as both sources).
"""

from __future__ import annotations
import argparse
import base64
import binascii
import codecs
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, fields
from email import policy
from email.header import decode_header
from email.message import Message
from email.parser import BytesParser
from enum import Enum, StrEnum
import hashlib
import html as html_stdlib
from http.client import HTTPMessage
import ipaddress
import json
import mimetypes
from pathlib import Path
import quopri
import re
import socket
import sys
import threading
from types import SimpleNamespace
from typing import (
    IO,
    Annotated,
    Any,
    BinaryIO,
    ClassVar,
    NoReturn,
    Self,
    cast,
)
import urllib.error
import urllib.parse
from urllib.parse import urlsplit
import urllib.request

from loguru import logger
from lxml import (
    etree,
    html as lxml_html,
)
from lxml_html_clean import Cleaner
import tinycss2
from tinycss2.ast import StringToken


# ---------------------------------------------------------------------------
# Version, shared CLI plumbing, exceptions and shared constants
# ---------------------------------------------------------------------------

__version__: str = "1.0.0"


class MhtmlError(Exception):
    """Base class for errors raised by the compatibility API."""


class IndexPageNotFoundError(MhtmlError):
    """Raised when an archive holds nothing that can be presented as a page."""


class ConversionError(MhtmlError):
    """Raised when a conversion fails for a reason other than a missing index."""


# Content-Security-Policy injected into converted pages (from mhtml-to-html).
DEFAULT_CSP: str = (
    "default-src 'none'; connect-src 'self' data:; "
    "font-src 'self' data:; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline' data:; frame-src 'self' data:; "
    "media-src 'self' data:; object-src 'self' data:; "
)
JS_ENABLED_CSP: str = "script-src 'self' 'unsafe-inline' data:;"
JS_DISABLED_CSP: str = "script-src 'none';"

# Anomaly type names reported in conversion metadata.
UNDECODABLE_BASE64_ANOMALY: str = "base64-left-encoded"
MISLABELED_DOCUMENT_ANOMALY: str = "document-mislabeled-as-archive"
UNUSED_BOUNDARY_ANOMALY: str = "declared-boundary-unused"
MISSING_DELIMITERS_ANOMALY: str = "multipart-without-delimiters"
SYNTHESIZED_INDEX_ANOMALY: str = "index-synthesized"


# ===== Charset / text / URL utilities (consolidated from mhtml_to_html.py) =====


class UrlError(ValueError):
    """Internal: URL could not be parsed (mirrors JS ``new URL`` throwing)."""


CHARSET_REGEXP = re.compile(r"charset=([^;]+)", re.IGNORECASE)
QUOTED_PARAMETER_REGEXP = re.compile(r'^([^=]+)=\s*"([^"]*)"\s*$')

_CHARSET_ALIASES: dict[str, str] = {
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


def _lookup_codec(charset: str | None) -> str | None:
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


def decode_string(data: bytes, charset: str | None = None) -> str:
    """Decode ``data`` with ``charset``; unknown labels fall back to UTF-8.

    Matches ``new TextDecoder(charset).decode(...)``: invalid sequences are
    replaced with U+FFFD, never raised.
    """
    result = _decode_inner(data, charset)
    # JS TextDecoder removes a leading byte order mark from the output
    result = result.removeprefix("\ufeff")
    return result


def _decode_inner(data: bytes, charset: str | None) -> str:
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


def get_charset(content_type: str | None) -> str | None:
    "Return charset."
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


def get_bom_charset(data: bytes) -> str | None:
    "Return bom charset."
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


def decode_base64_bytes(value: str) -> bytes | None:
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


def decode_base64(value: str, charset: str | None) -> str:
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


def _decode_encoded_word(header: str, start: int) -> tuple[str, bytes, int] | None:
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


def decode_mime_header(header: str | None) -> str:
    """RFC 2047 decoding keeping surrounding text; adjacent words sharing a
    charset are decoded as one so a character split across two words survives."""
    if not header:
        return ""
    parts: list[str] = []
    index = 0
    pending_charset: str | None = None
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

_SPECIAL_SCHEMES: dict[str, int] = {
    "http": 80,
    "https": 443,
    "ws": 80,
    "wss": 443,
    "ftp": 21,
}
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_HEX: str = "0123456789abcdefABCDEF"


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
        msg = f"no scheme in {url!r}"
        raise UrlError(msg)
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
                msg = "unterminated IPv6 address"
                raise UrlError(msg)
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
                msg = f"invalid port {port!r}"
                raise UrlError(msg)
            if port and scheme in _SPECIAL_SCHEMES and int(port) == _SPECIAL_SCHEMES[scheme]:
                port = ""  # default ports are removed
        host = host.lower()

    if scheme in _SPECIAL_SCHEMES:
        if not netloc and scheme != "file":
            msg = f"special scheme {scheme} requires a host"
            raise UrlError(msg)
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


def js_new_url(url: str, base: str | None = None) -> str:
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


def _with_query_fragment(candidate: str, query: str | None, fragment: str | None, original: str) -> str:
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


def _find_unescaped(text: str, ch: str) -> int | None:
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


def resolve_path(path: str, base: str | None) -> str:
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


def _test_content_type(content_type: str | None, prefix: str) -> bool:
    return bool(content_type) and content_type.lower().startswith(prefix)


def is_document(content_type: str | None) -> bool:
    "Return whether document."
    return _test_content_type(content_type, "text/html") or _test_content_type(content_type, "application/xhtml+xml")


def is_stylesheet(content_type: str | None) -> bool:
    "Return whether stylesheet."
    return _test_content_type(content_type, "text/css")


def is_text(content_type: str | None) -> bool:
    "Return whether text."
    return _test_content_type(content_type, "text/")


def is_image(content_type: str | None) -> bool:
    "Return whether image."
    return _test_content_type(content_type, "image/")


def is_plain_text(content_type: str | None) -> bool:
    "Return whether plain text."
    return _test_content_type(content_type, "text/plain")


def is_media(content_type: str | None) -> bool:
    "Return whether media."
    return (
        _test_content_type(content_type, "image/")
        or _test_content_type(content_type, "audio/")
        or _test_content_type(content_type, "video/")
        or _test_content_type(content_type, "font/")
    )


def is_multipart(content_type: str | None) -> bool:
    "Return whether multipart."
    return _test_content_type(content_type, "multipart/")


def is_multipart_alternative(content_type: str | None) -> bool:
    "Return whether multipart alternative."
    return _test_content_type(content_type, "multipart/alternative")


def get_boundary(content_type: str) -> str | None:
    "Return boundary."
    params = content_type.split(";")[1:]
    for param in params:
        param = param.strip()
        if param.startswith("boundary="):
            return _remove_quotes(param[9:])
    return None


def get_media_type(content_type: str | None) -> str | None:
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


# Srcset parser/serializer (direct port of lib/srcset-parser.js)
# ---------------------------------------------------------------------------

_SRCSET_SPACES: str = " \t\n\r\f"


def _srcset_collect(regexp: re.Pattern[str], s: str, pos: list[int]) -> str | None:
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
        "Return whether space."
        return bool(c) and c in _SRCSET_SPACES

    def parse_descriptors(url: str, descriptors: list[str]) -> None:
        "Parse descriptors."
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


class ConversionProfile(str, Enum):
    """Built-in conversion presets."""

    MINIMAL = "minimal"
    STANDARD = "standard"
    FULL = "full"
    SECURE = "secure"
    OPTIMIZED = "optimized"


ResourceHandler = Callable[["Resource"], "Resource | bytes | str | None"]
ProgressCallback = Callable[[str, float], None]


@dataclass(slots=True)
class ConverterConfig:
    """Options controlling parsing, rewriting, fetching, and output.

    External fetching is disabled by default. Enable it only when converting
    trusted documents; even with it enabled, only public HTTP(S) resources are
    fetched and optional domain allow/deny lists are enforced.
    """

    profile: ConversionProfile = ConversionProfile.STANDARD
    output_format: str = "html5"
    encoding: str = "utf-8"
    resource_handling: str = "inline"
    max_resource_size: int = 10 * 1024 * 1024
    max_input_size: int = 128 * 1024 * 1024
    fetch_external: bool = False
    fetch_timeout: float = 10.0
    max_fetch_retries: int = 2
    allow_private_hosts: bool = False
    inject_csp: bool = True
    include_scripts: bool = True
    include_styles: bool = True
    minify_html: bool = False
    minify_css: bool = False
    compress_output: bool = False
    sanitize_html: bool = True
    remove_scripts: bool = False
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    parallel_processing: bool = True
    max_workers: int = 4
    streaming_output: bool = False
    chunk_size: int = 64 * 1024
    custom_handlers: dict[str, ResourceHandler] = field(default_factory=dict)
    plugins: list[ConverterPlugin] = field(default_factory=list)
    metadata_extraction: bool = True
    progress_callback: ProgressCallback | None = None
    error_recovery: bool = True
    validation: bool = True
    strip_css_comments: bool = True
    max_import_depth: int = 10
    cache_enabled: bool = True
    cache_size: int = 100
    cache_dir: Path | None = None
    debug_mode: bool = False
    log_level: str = "INFO"
    log_file: Path | None = None

    def __post_init__(self) -> None:
        """Normalize user-friendly values, apply presets, and validate options."""
        if isinstance(self.profile, str):
            self.profile = ConversionProfile(self.profile.lower())
        if self.profile is not ConversionProfile.STANDARD:
            self.apply_profile(self.profile)
        if self.cache_dir is not None:
            self.cache_dir = Path(self.cache_dir)
        if self.log_file is not None:
            self.log_file = Path(self.log_file)
        self.allowed_domains = [domain.strip().lower().strip(".") for domain in self.allowed_domains]
        self.blocked_domains = [domain.strip().lower().strip(".") for domain in self.blocked_domains]
        self.validate()

    def apply_profile(self, profile: ConversionProfile) -> None:
        """Apply the named preset; callers may customize fields afterward."""
        if isinstance(profile, str):
            profile = ConversionProfile(profile.lower())
        self.profile = profile

        shared = {
            "remove_scripts": False,
            "include_scripts": True,
            "include_styles": True,
            "sanitize_html": True,
            "resource_handling": "inline",
            "parallel_processing": True,
            "debug_mode": False,
        }
        presets: dict[ConversionProfile, dict[str, Any]] = {
            ConversionProfile.MINIMAL: {
                **shared,
                "minify_html": True,
                "minify_css": True,
                "compress_output": True,
                "fetch_external": False,
                "include_scripts": False,
                "include_styles": False,
                "max_workers": 1,
                "log_level": "WARNING",
            },
            ConversionProfile.STANDARD: {
                **shared,
                "minify_html": False,
                "minify_css": False,
                "compress_output": False,
                "fetch_external": False,
                "max_workers": 4,
                "log_level": "INFO",
            },
            ConversionProfile.FULL: {
                **shared,
                "minify_html": False,
                "minify_css": False,
                "compress_output": False,
                "fetch_external": True,
                "resource_handling": "mixed",
                "max_workers": 8,
                "debug_mode": True,
                "log_level": "DEBUG",
            },
            ConversionProfile.SECURE: {
                **shared,
                "minify_html": True,
                "minify_css": True,
                "compress_output": True,
                "fetch_external": False,
                "remove_scripts": True,
                "include_scripts": False,
                "include_styles": False,
                "parallel_processing": False,
                "max_workers": 1,
                "log_level": "WARNING",
            },
            ConversionProfile.OPTIMIZED: {
                **shared,
                "minify_html": True,
                "minify_css": True,
                "compress_output": True,
                "fetch_external": False,
                "max_workers": 8,
                "log_level": "INFO",
            },
        }
        for name, value in presets[profile].items():
            setattr(self, name, value)

    def validate(self) -> None:
        """Raise ``ValueError`` when a setting is invalid or unsafe."""
        if self.output_format not in {"html5", "xhtml"}:
            msg = "output_format must be 'html5' or 'xhtml'"
            raise ValueError(msg)
        if self.resource_handling not in {"inline", "external", "mixed"}:
            msg = "resource_handling must be 'inline', 'external', or 'mixed'"
            raise ValueError(msg)
        try:
            codecs.lookup(self.encoding)
        except LookupError as error:
            msg = f"Unknown output encoding: {self.encoding}"
            raise ValueError(msg) from error
        if self.max_resource_size <= 0 or self.max_input_size <= 0:
            msg = "max_resource_size and max_input_size must be positive"
            raise ValueError(msg)
        if self.fetch_timeout <= 0:
            msg = "fetch_timeout must be positive"
            raise ValueError(msg)
        if self.max_fetch_retries < 0:
            msg = "max_fetch_retries cannot be negative"
            raise ValueError(msg)
        if self.max_workers < 1:
            msg = "max_workers must be at least 1"
            raise ValueError(msg)
        if self.chunk_size < 1:
            msg = "chunk_size must be at least 1"
            raise ValueError(msg)
        if self.max_import_depth < 0:
            msg = "max_import_depth cannot be negative"
            raise ValueError(msg)
        if self.cache_size < 0:
            msg = "cache_size cannot be negative"
            raise ValueError(msg)
        if self.log_level.upper() not in {
            "TRACE",
            "DEBUG",
            "INFO",
            "SUCCESS",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }:
            msg = f"Unsupported log level: {self.log_level}"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of configuration settings."""
        values: dict[str, Any] = {config_field.name: getattr(self, config_field.name) for config_field in fields(self)}
        values["profile"] = self.profile.value
        values["allowed_domains"] = list(self.allowed_domains)
        values["blocked_domains"] = list(self.blocked_domains)
        values["cache_dir"] = str(self.cache_dir) if self.cache_dir else None
        values["log_file"] = str(self.log_file) if self.log_file else None
        values["custom_handlers"] = sorted(self.custom_handlers)
        values["plugins"] = [type(plugin).__name__ for plugin in self.plugins]
        values["progress_callback"] = self.progress_callback is not None
        return values


# ===== Consolidated from converter/logger.py =====


class ConverterLogger:
    """Scoped logging facade that does not reset the application's Loguru sinks."""

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Bind a component logger and optionally add a rotating file sink."""
        self.config = config or ConverterConfig()
        self.progress_callback: Callable[[str, float], None] | None = self.config.progress_callback
        self._logger = logger.bind(component="mhtml.converter")
        self._minimum_level = logger.level(self.config.log_level.upper()).no
        self._file_sink: int | None = None
        if self.config.log_file is not None:
            self.config.log_file.parent.mkdir(parents=True, exist_ok=True)
            self._file_sink = logger.add(
                self.config.log_file,
                level=self.config.log_level.upper(),
                rotation="10 MB",
                retention=3,
                enqueue=True,
                format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
            )

    def set_progress_callback(
        self,
        callback: Callable[[str, float], None] | None,
    ) -> None:
        """Set or clear the callback invoked at progress milestones."""
        self.progress_callback = callback

    def log_progress(self, message: str, progress: float) -> None:
        """Log progress and invoke the optional callback with a clamped value."""
        clamped_progress = min(1.0, max(0.0, progress))
        self.info("Progress: {:.1f}% - {}", clamped_progress * 100, message)
        if self.progress_callback is not None:
            try:
                self.progress_callback(message, clamped_progress)
            # User callback exceptions must not abort the conversion pipeline.
            except Exception:  # pylint: disable=broad-exception-caught
                self._logger.exception("Progress callback failed")

    def trace(self, message: str, *args: object) -> None:
        """Log a trace-level message when enabled by the configured threshold."""
        self._log("TRACE", message, *args)

    def debug(self, message: str, *args: object) -> None:
        """Log a debug message when enabled by the configured threshold."""
        self._log("DEBUG", message, *args)

    def info(self, message: str, *args: object) -> None:
        """Log an informational message."""
        self._log("INFO", message, *args)

    def warning(self, message: str, *args: object) -> None:
        """Log a warning message."""
        self._log("WARNING", message, *args)

    def error(self, message: str, *args: object) -> None:
        """Log an error message."""
        self._log("ERROR", message, *args)

    def exception(self, message: str, *args: object) -> None:
        """Log an error with the active exception traceback."""
        if self._minimum_level <= logger.level("ERROR").no:
            self._logger.error(message, *args)

    def close(self) -> None:
        """Remove this instance's optional Loguru file sink."""
        if self._file_sink is not None:
            logger.remove(self._file_sink)
            self._file_sink = None

    def _log(self, level: str, message: str, *args: object) -> None:
        if logger.level(level).no >= self._minimum_level:
            self._logger.log(level, message, *args)


# ===== Consolidated from converter/parser.py =====


class ResourceType(str, Enum):
    """High-level category of an extracted MIME resource."""

    HTML = "html"
    CSS = "css"
    IMAGE = "image"
    SCRIPT = "script"
    FONT = "font"
    OTHER = "other"


def classify_resource_type(content_type: str) -> ResourceType:
    """Map a MIME content type to the converter's resource categories."""
    media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    if media_type in {"text/html", "text/htm", "application/xhtml+xml"}:
        return ResourceType.HTML
    if media_type == "text/css":
        return ResourceType.CSS
    if media_type in {
        "application/javascript",
        "application/x-javascript",
        "text/javascript",
        "text/ecmascript",
        "application/ecmascript",
    }:
        return ResourceType.SCRIPT
    if media_type.startswith("image/"):
        return ResourceType.IMAGE
    if media_type.startswith("font/") or media_type in {
        "application/font-woff",
        "application/font-woff2",
        "application/vnd.ms-fontobject",
    }:
        return ResourceType.FONT
    return ResourceType.OTHER


def resource_to_data_uri(resource: Resource) -> str:
    """Encode a resource as a base64 data URI using its MIME media type."""
    content = resource.content if isinstance(resource.content, bytes) else resource.content.encode("utf-8")
    media_type = resource.content_type.split(";", maxsplit=1)[0].strip()
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


# A resource carries MIME, URL, content, and lookup metadata by design.
# pylint: disable=too-many-instance-attributes
@dataclass(slots=True)
class Resource:
    """A MIME part and the URL by which HTML/CSS refers to it."""

    url: str
    content_type: str
    content: bytes | str
    resource_type: ResourceType
    encoding: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    original_url: str | None = None
    size: int = 0
    decode_failed: bool = False

    def __post_init__(self) -> None:
        """Compute byte size and ensure each resource owns its header mapping."""
        if isinstance(self.content, bytes):
            self.size = len(self.content)
        else:
            self.size = len(self.content.encode("utf-8"))
        if self.original_url is None:
            self.original_url = self.url


def resolve_resource_url(
    url: str,
    base_url: str,
    resource_map: dict[str, Resource],
    config: ConverterConfig,
) -> str:
    """Normalize an asset URL and inline a mapped resource when configured."""
    value = url.strip()
    if not value or value.lower().startswith("data:"):
        return value
    normalized = urllib.parse.urldefrag(urllib.parse.urljoin(base_url, value))[0]
    resource = resource_map.get(normalized) or resource_map.get(value)
    if (
        resource is not None
        and not resource.decode_failed
        and config.resource_handling in {"inline", "mixed"}
        and resource.size <= config.max_resource_size
    ):
        return resource_to_data_uri(resource)
    return normalized


class MHTMLParser:
    """Parse MHTML data using Python's MIME parser plus compatibility helpers."""

    BOUNDARY_RE = re.compile(
        r"boundary\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s;]+))",
        re.IGNORECASE,
    )
    CHARSET_RE = re.compile(r"charset\s*=\s*['\"]?([^\s;'\"]+)", re.IGNORECASE)
    HTML_URL_RE = re.compile(
        r"\b(?:src|href|poster|background|action|data|xlink:href)\s*=\s*"
        r"(?P<quote>[\"'])(?P<url>.*?)(?P=quote)",
        re.IGNORECASE | re.DOTALL,
    )
    CSS_URL_RE = re.compile(
        r"url\(\s*(?:(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)|(?P<plain>[^)]*?))\s*\)",
        re.IGNORECASE | re.DOTALL,
    )
    CSS_IMPORT_RE = re.compile(
        r"@import\s+(?:url\(\s*)?(?:\"([^\"]*)\"|'([^']*)'|([^\s)]+))",
        re.IGNORECASE,
    )
    SRCSET_RE = re.compile(r"(?:^|,)\s*([^\s,]+)(?:\s+[^,]+)?")

    BOM_UTF8 = b"\xef\xbb\xbf"
    BOM_UTF16BE = b"\xfe\xff"
    BOM_UTF16LE = b"\xff\xfe"

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Create a parser using the supplied configuration."""
        self.config = config or ConverterConfig()
        self.parser = BytesParser(policy=policy.default)
        self._anomalies: list[Anomaly] = []
        self._last_decode_failed = False

    def _detect_encoding(self, content: bytes, content_type: str = "") -> tuple[str, bytes]:
        """Detect a BOM or MIME charset and return the charset and BOM-free bytes."""
        if content.startswith(self.BOM_UTF8):
            return "utf-8", content[len(self.BOM_UTF8) :]
        if content.startswith(self.BOM_UTF16BE):
            return "utf-16-be", content[len(self.BOM_UTF16BE) :]
        if content.startswith(self.BOM_UTF16LE):
            return "utf-16-le", content[len(self.BOM_UTF16LE) :]

        charset_match = self.CHARSET_RE.search(content_type)
        charset = charset_match.group(1) if charset_match else self.config.encoding
        try:
            content.decode(charset)
        except (LookupError, UnicodeDecodeError):
            return "utf-8", content
        return charset, content

    def decode_text(self, content: bytes | str, content_type: str = "") -> str:
        """Decode text with BOM/charset detection and a replacement fallback."""
        if isinstance(content, str):
            return content.removeprefix("\ufeff")
        charset, data = self._detect_encoding(content, content_type)
        try:
            return data.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            return data.decode(self.config.encoding, errors="replace")

    def _decode_content(
        self,
        content: bytes,
        content_type: str,
        encoding: str,
    ) -> bytes:
        """Decode MIME transfer encodings while preserving text as raw bytes."""
        if not content:
            return b""
        transfer_encoding = encoding.strip().lower()
        self._last_decode_failed = False
        if transfer_encoding in {"base64", "b64"}:
            compact = re.sub(rb"\s+", b"", content)
            decoded = decode_base64_bytes(compact.decode("ascii", errors="replace"))
            if decoded is not None:
                return decoded
            # undecodable base64 is kept verbatim and flagged so the resource
            # is not silently double-encoded into a data URI
            self._last_decode_failed = True
            return content
        if transfer_encoding in {"quoted-printable", "qp"}:
            return quopri.decodestring(content)
        # 7bit/8bit/binary parts already contain their transfer-decoded bytes.
        del content_type
        return content

    @staticmethod
    def _get_resource_type(content_type: str) -> ResourceType:
        """Compatibility wrapper for MIME resource classification."""
        return classify_resource_type(content_type)

    def parse_mhtml(
        self,
        raw_data: bytes,
    ) -> tuple[Resource, dict[str, Resource], dict[str, Any]]:
        """Parse a full MHTML message into its main part, assets, and metadata.

        Raises:
            ValueError: If the input is empty or has no MIME content-type header.
        """
        if not raw_data.strip() or b"content-type:" not in raw_data.lower():
            msg = "Invalid MHTML data: missing MIME Content-Type header"
            raise ValueError(msg)
        self._anomalies.clear()
        if len(raw_data) > self.config.max_input_size:
            msg = f"MHTML input exceeds the {self.config.max_input_size}-byte limit"
            raise ValueError(msg)

        message = self.parser.parsebytes(raw_data)
        main_part = self._find_main_part(message)
        main_resource = self._resource_from_part(main_part)
        resources = self._extract_resources(message, main_part)
        metadata = self._extract_metadata(message, main_resource, resources)
        return main_resource, resources, metadata

    @staticmethod
    def _find_main_part(message: Message) -> Message:
        """Select the first HTML part, or the first leaf if no HTML part exists."""
        if not message.is_multipart():
            return message
        parts = list(message.walk())
        for part in parts:
            if part.get_content_type().lower() in {
                "text/html",
                "text/htm",
                "application/xhtml+xml",
            }:
                return part
        return next((part for part in parts if not part.is_multipart()), message)

    def _resource_from_part(self, part: Message) -> Resource:
        """Build a resource from one MIME part, honoring its own MIME headers."""
        payload = part.get_payload(decode=False)
        if isinstance(payload, bytes):
            raw_content = payload
        elif isinstance(payload, str):
            raw_content = payload.encode("utf-8", errors="replace")
        else:
            raw_content = b""

        transfer_encoding = str(part.get("Content-Transfer-Encoding", "7bit"))
        content = self._decode_content(
            raw_content,
            str(part.get("Content-Type", part.get_content_type())),
            transfer_encoding,
        )
        decode_failed = self._last_decode_failed
        if decode_failed:
            location = part.get("Content-Location")
            self._anomalies.append(Anomaly(UNDECODABLE_BASE64_ANOMALY, str(location) if location else None))
        url = str(part.get("Content-Location", "") or part.get("Content-ID", ""))
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]

        return Resource(
            url=url,
            content_type=part.get_content_type(),
            content=content,
            resource_type=self._get_resource_type(part.get_content_type()),
            encoding=transfer_encoding,
            headers={str(key): str(value) for key, value in part.items()},
            original_url=url,
            decode_failed=decode_failed,
        )

    def _extract_resources(
        self,
        message: Message,
        main_part: Message,
    ) -> dict[str, Resource]:
        """Extract all non-main MIME parts and index Content-ID aliases."""
        resources: dict[str, Resource] = {}
        if not message.is_multipart():
            return resources

        for part in message.walk():
            if part is message or part is main_part or part.is_multipart():
                continue
            resource = self._resource_from_part(part)
            if not resource.url:
                continue
            resources[resource.url] = resource
            content_id = str(part.get("Content-ID", "")).strip()
            if content_id.startswith("<") and content_id.endswith(">"):
                content_id = content_id[1:-1]
            if content_id:
                resources[f"cid:{content_id}"] = resource
        return resources

    def _extract_metadata(
        self,
        message: Message,
        main_resource: Resource,
        resources: dict[str, Resource],
    ) -> dict[str, Any]:
        """Extract MIME headers, structural details, and lightweight asset metadata."""
        metadata: dict[str, Any] = {
            "content_type": message.get_content_type(),
            "boundary": self._extract_boundary(message),
            "part_count": sum(1 for part in message.walk() if not part.is_multipart()),
            "main_resource_type": main_resource.resource_type.value,
            "resource_count": len({id(resource) for resource in resources.values()}),
            "anomalies": [anomaly.to_dict() for anomaly in self._anomalies],
        }
        if self.config.metadata_extraction:
            metadata.update({
                "subject": self._decode_header_value(message.get("Subject", "")),
                "from": self._decode_header_value(message.get("From", "")),
                "to": self._decode_header_value(message.get("To", "")),
                "date": self._decode_header_value(message.get("Date", "")),
                "content_location": main_resource.url,
                "resources": [
                    {
                        "url": resource.url,
                        "content_type": resource.content_type,
                        "resource_type": resource.resource_type.value,
                        "size": resource.size,
                    }
                    for resource in self._unique_resources(resources.values())
                ],
            })
        return metadata

    @staticmethod
    def _unique_resources(resources: Any) -> list[Resource]:
        """Return a stable unique list, accounting for CID aliases."""
        result: list[Resource] = []
        seen: set[int] = set()
        for resource in resources:
            identity = id(resource)
            if identity not in seen:
                result.append(resource)
                seen.add(identity)
        return result

    @staticmethod
    def _decode_header_value(value: Any) -> str:
        """Decode RFC 2047 encoded MIME headers into Unicode text."""
        if not value:
            return ""
        pieces: list[str] = []
        for chunk, charset in decode_header(str(value)):
            if isinstance(chunk, bytes):
                pieces.append(chunk.decode(charset or "utf-8", errors="replace"))
            else:
                pieces.append(chunk)
        return "".join(pieces)

    def _extract_boundary(self, message: Message) -> str | None:
        """Extract the MIME boundary from a real or compatible message object."""
        content_type = str(message.get("Content-Type", ""))
        match = self.BOUNDARY_RE.search(content_type)
        if match is None:
            return None
        return next((value for value in match.groups() if value), None)

    def extract_urls_from_content(
        self,
        content: bytes | str,
        content_type: str,
    ) -> set[str]:
        """Find URL references in HTML and CSS, including srcset and CSS imports."""
        text = self.decode_text(content, content_type)
        urls: set[str] = set()
        media_type = content_type.split(";", maxsplit=1)[0].lower()
        if "html" in media_type or media_type.endswith("xhtml+xml"):
            urls.update(
                match.group("url").strip() for match in self.HTML_URL_RE.finditer(text) if match.group("url").strip()
            )
            for match in re.finditer(
                r"\bsrcset\s*=\s*([\"'])(.*?)\1",
                text,
                re.IGNORECASE | re.DOTALL,
            ):
                urls.update(
                    item.group(1).strip() for item in self.SRCSET_RE.finditer(match.group(2)) if item.group(1).strip()
                )
        if "css" in media_type or "html" in media_type:
            for match in self.CSS_URL_RE.finditer(text):
                url = (match.group("quoted") or match.group("plain") or "").strip().strip("\"'")
                if url:
                    urls.add(url)
        if "css" in media_type:
            for match in self.CSS_IMPORT_RE.finditer(text):
                url = next((value for value in match.groups() if value), "").strip("\"'")
                if url:
                    urls.add(url)
        return urls


# ===== Robust state-machine parser (consolidated from mhtml_to_html.py) =====
#
# The stdlib MIME parser handles well-formed archives; this parser (a port of
# the mhtml-to-html library's parser) additionally survives mislabeled plain
# HTML, unused or missing boundaries, multipart bodies without delimiters and
# archives that embed a whole MHTML inside a multipart/alternative part.  It
# is used as the fallback whenever the stdlib parser cannot find a usable
# document part, and it powers the compatibility ``parse()`` API.

MHTML_HEADERS: int = 0
MHTML_CONTENT: int = 1
MHTML_DATA: int = 2
MHTML_END: int = 3

HEADER_NAME_REGEXP = re.compile(r"^[!-9;-~]+:")
HEADER_CONTINUATION_REGEXP = re.compile(r"^[ \t]")

UTF8_CHARSET: str = "utf-8"
QUOTED_PRINTABLE_ENCODING: str = "quoted-printable"
BINARY_ENCODING: str = "binary"
BASE64_ENCODING: str = "base64"

CID_PROTOCOL: str = "cid:"
CID_REGEXP = re.compile(r"^<.+>$")
CSS_CHARSET_REGEXP = re.compile(r"""@charset\s+(?:"([^"]*)"|'([^']*)')\s*;""", re.IGNORECASE)


@dataclass
class Anomaly:
    """A recovered-from condition worth reporting in conversion metadata."""

    type: str
    id: str | None = None

    def __post_init__(self) -> None:
        if self.id is None:
            # match the JS shape (no `id` key when absent)
            object.__setattr__(self, "_dict", {"type": self.type})
        else:
            object.__setattr__(self, "_dict", {"type": self.type, "id": self.id})

    def to_dict(self) -> dict:
        "Return a JSON-friendly copy of this anomaly."
        return dict(getattr(self, "_dict", {"type": self.type}))


@dataclass
class MhtmlResource:
    """One MIME part in the mhtml-to-html compatibility model.

    ``data`` holds decoded text for textual parts and raw bytes for binary
    parts (the original kept base64 text and re-emitted it verbatim; the
    consolidated engine always inlines from decoded bytes).
    """

    id: str
    content_type: str | None = None
    transfer_encoding: str | None = None
    data: str | bytes = ""
    raw_data: bytes | None = None
    used: bool = False
    not_found: bool = False
    decode_failed: bool = False


@dataclass
class Mhtml:
    """A parsed archive: headers, Content-ID frames, resources, index page."""

    headers: dict[str, str] = field(default_factory=dict)
    frames: dict[str, MhtmlResource] = field(default_factory=dict)
    resources: dict[str, MhtmlResource] = field(default_factory=dict)
    index: str | None = None
    anomalies: list[Anomaly] = field(default_factory=list)


def _escape_text(value: str) -> str:
    "Escape text interpolated into synthesized HTML."
    return value.replace("&", "&amp;").replace("<", "&lt;")


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


def _index_of_subarray(haystack: bytes, needle: bytes | None, start: int = 0) -> int:
    if not needle:
        return -1
    return haystack.find(needle, start)


def _find_boundary_delimiter(line: bytes, boundary_bytes: bytes | None) -> int:
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


def _parse_header_line(line_string: str, obj: dict, header_key: str | None) -> str | None:
    index_column = line_string.find(":")
    if index_column > -1:
        header_key = line_string[:index_column].strip().lower()
        obj[header_key] = line_string[index_column + 1 :].strip()
    elif header_key is not None and header_key in obj:
        obj[header_key] += line_string.strip()
    return header_key


def _create_document(document: Mhtml) -> MhtmlResource | None:
    """Build the page an archive without a document is presented as."""
    for mres in document.resources.values():
        content_type = mres.content_type
        if is_image(content_type):
            return MhtmlResource(
                id=mres.id,
                content_type="text/html",
                transfer_encoding=None,
                data=f'<html><body><img src="{_escape_text(mres.id)}"></body></html>',
            )
        if is_plain_text(content_type):
            text = mres.data if isinstance(mres.data, str) else decode_string(mres.data)
            return MhtmlResource(
                id=mres.id,
                content_type="text/html",
                transfer_encoding=None,
                data=f"<html><body><pre>{_escape_text(text)}</pre></body></html>",
            )
    return None


class RobustMHTMLParser:
    """State-machine MHTML parser tolerant of malformed archives."""

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Create a parser using the supplied configuration."""
        self.config = config or ConverterConfig()

    def parse_mhtml(
        self,
        raw_data: bytes,
    ) -> tuple[Resource, dict[str, Resource], dict[str, Any]]:
        """Parse raw MHTML bytes into the engine's parts, resources, metadata."""
        document = self.parse_to_mhtml(raw_data)
        return self.mhtml_to_parts(document)

    def mhtml_to_parts(
        self,
        document: Mhtml,
    ) -> tuple[Resource, dict[str, Resource], dict[str, Any]]:
        """Convert a parsed :class:`Mhtml` into the engine's resource model."""
        converted: dict[int, Resource] = {}

        def as_resource(mres: MhtmlResource) -> Resource:
            if id(mres) not in converted:
                converted[id(mres)] = self._as_resource(mres)
            return converted[id(mres)]

        resource_map: dict[str, Resource] = {}
        for key, mres in document.resources.items():
            resource_map[key] = as_resource(mres)
        for content_id, mres in document.frames.items():
            # register Content-ID aliases the way the email parser does
            bare = content_id.removeprefix("<").removesuffix(">")
            resource_map.setdefault(f"cid:{bare}", as_resource(mres))

        main_resource = resource_map.get(document.index) if document.index else None
        anomalies = list(document.anomalies)
        if main_resource is None:
            synthesized = _create_document(document)
            if synthesized is None:
                msg = "Index page not found"
                raise ValueError(msg)
            main_resource = as_resource(synthesized)
            resource_map[main_resource.url] = main_resource
            anomalies.append(Anomaly(SYNTHESIZED_INDEX_ANOMALY, main_resource.url))

        unique_parts = {id(mres): mres for mres in document.resources.values()}
        metadata: dict[str, Any] = {
            "content_type": document.headers.get("content-type", "multipart/related"),
            "boundary": get_boundary(document.headers.get("content-type", "")),
            "part_count": len(unique_parts),
            "main_resource_type": main_resource.resource_type.value,
            "resource_count": len(unique_parts),
            "anomalies": [anomaly.to_dict() for anomaly in anomalies],
        }
        if self.config.metadata_extraction:
            metadata.update({
                "subject": decode_mime_header(document.headers.get("subject", "")),
                "from": decode_mime_header(document.headers.get("from", "")),
                "to": decode_mime_header(document.headers.get("to", "")),
                "date": decode_mime_header(document.headers.get("date", "")),
                "content_location": main_resource.url,
                "resources": [
                    {
                        "url": mres.id,
                        "content_type": mres.content_type,
                        "resource_type": classify_resource_type(mres.content_type or "").value,
                        "size": len(mres.data) if isinstance(mres.data, (str, bytes)) else 0,
                    }
                    for mres in unique_parts.values()
                ],
            })
        return main_resource, resource_map, metadata

    def _as_resource(self, mres: MhtmlResource) -> Resource:
        """Translate one compatibility resource into the engine's model."""
        content = mres.data
        if isinstance(content, bytearray):
            content = bytes(content)
        content_type = mres.content_type or "application/octet-stream"
        return Resource(
            url=mres.id or "",
            content_type=content_type,
            content=content,
            resource_type=classify_resource_type(content_type),
            encoding=mres.transfer_encoding,
            headers={"Content-Type": mres.content_type} if mres.content_type else {},
            original_url=mres.id,
            decode_failed=mres.decode_failed,
        )

    def parse_to_mhtml(self, mhtml: str | bytes | bytearray) -> Mhtml:
        """Parse MHTML data into the structured :class:`Mhtml` representation."""
        if isinstance(mhtml, str):
            mhtml = encode_string(mhtml)
        elif isinstance(mhtml, bytearray):
            mhtml = bytes(mhtml)
        context: dict[str, Any] = {"resources": {}, "frames": {}, "anomalies": []}
        return self._parse_state_machine(mhtml, context)

    def _parse_state_machine(self, mhtml: bytes, context: dict[str, Any]) -> Mhtml:
        """Run the line-oriented state machine over raw MHTML bytes."""
        anomalies: list[Anomaly] = context["anomalies"]
        resources: dict[str, MhtmlResource] = context["resources"]
        frames: dict[str, MhtmlResource] = context["frames"]
        headers: dict[str, str] = {}
        raw_header_lines: list[bytes] = []

        state = MHTML_HEADERS
        index_mhtml = 0
        index_generated_id = 0
        index_start_embedded_mhtml: int | None = None
        boundary: str | None = None
        boundary_bytes: bytes | None = None
        declared_boundary_unused = False
        header_key: str | None = None
        transfer_encoding: str | None = None
        content_id: str | None = None
        resource: MhtmlResource | None = None
        content: dict[str, str] = {}
        data_chunks: list[bytes] = []
        data_length = 0

        n = len(mhtml)

        def get_line(encoding: str | None = None) -> bytes:
            "Return line."
            nonlocal index_mhtml
            index_start = index_mhtml
            index_line_feed = mhtml.find(b"\n", index_start)
            index_mhtml = (n + 1) if index_line_feed == -1 else index_line_feed + 1
            line = mhtml[index_start:index_mhtml]
            if encoding == QUOTED_PRINTABLE_ENCODING:
                return decode_quoted_printable(line)
            return line

        def split_headers(line: bytes, obj: dict) -> None:
            "Split headers."
            nonlocal header_key
            header_key = _parse_header_line(decode_string(line), obj, header_key)

        def decode_raw_headers(charset: str | None) -> None:
            "Decode raw headers."
            if charset is None or charset == UTF8_CHARSET:
                return
            if not any("\ufffd" in value for value in headers.values()):
                return
            decoded_headers: dict[str, str] = {}
            decoded_key: str | None = None
            for line in raw_header_lines:
                decoded_key = _parse_header_line(decode_string(line, charset), decoded_headers, decoded_key)
            for name, value in list(headers.items()):
                if "\ufffd" in value and name in decoded_headers:
                    headers[name] = decoded_headers[name]

        def read_body_headers() -> dict[str, str]:
            "Read body headers."
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
            "Initialize resource."
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
            resource = MhtmlResource(
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
            "Append data."
            nonlocal data_length
            if chunk:
                data_chunks.append(chunk)
                data_length += len(chunk)

        def get_data_byte(offset_from_end: int) -> int | None:
            "Return data byte."
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

        def parse_resource_data() -> int | None:
            "Parse resource data."
            nonlocal data_chunks, data_length
            next_line = get_line(transfer_encoding)
            index_end_data: int | None = None
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

        def convert_embedded_mhtml(index_end: int) -> str | None:
            "Convert embedded mhtml."
            nonlocal index_mhtml
            inner_context: dict[str, Any] = {
                "resources": resources,
                "frames": frames,
                "anomalies": anomalies,
            }
            embedded = mhtml[index_start_embedded_mhtml:index_end]
            if _ends_with_crlf(embedded):
                index_end -= 2
            elif _ends_with_lf(embedded):
                index_end -= 1
            self._parse_state_machine(mhtml[index_start_embedded_mhtml:index_end], inner_context)
            return inner_context.get("index")

        def process_resource() -> None:
            "Process resource."
            nonlocal resource
            assert resource is not None
            raw = resource.data if isinstance(resource.data, bytes) else encode_string(str(resource.data))
            resource.raw_data = raw
            declared_charset = get_charset(resource.content_type)
            if resource.transfer_encoding == BASE64_ENCODING and resource.content_type:
                # Unlike the mhtml-to-html original (which decodes only document
                # and stylesheet parts early), every base64 part is decoded
                # here: the engine inlines resources from decoded bytes.
                decoded = decode_base64_bytes(decode_string(raw))
                if decoded is not None:
                    resource.transfer_encoding = None
                    resource.data = raw = decoded
                    resource.raw_data = decoded
                else:
                    anomalies.append(Anomaly(UNDECODABLE_BASE64_ANOMALY, resource.id))
                    resource.decode_failed = True
                    resource.transfer_encoding = None
            bom_charset = get_bom_charset(raw)
            charset = bom_charset or declared_charset
            textual = bool(resource.content_type) and (
                is_text(resource.content_type)
                or is_document(resource.content_type)
                or is_stylesheet(resource.content_type)
            )
            if textual:
                resource.data = decode_string(raw, charset)
                resource.content_type = replace_charset(resource.content_type, UTF8_CHARSET)
                if is_stylesheet(resource.content_type):
                    process_stylesheet_charset(charset, bom_charset)
                elif is_document(resource.content_type):
                    document_charset = process_document_charset(charset, bom_charset)
                    if resource.id == context.get("index"):
                        decode_raw_headers(document_charset)
            else:
                # binary payloads stay as raw bytes (lossless for data URIs)
                resource.data = raw
            resource.raw_data = None

        def process_stylesheet_charset(charset: str | None, bom_charset: str | None) -> None:
            "Process stylesheet charset."
            assert resource is not None
            assert isinstance(resource.data, str)
            assert resource.raw_data is not None
            match = CSS_CHARSET_REGEXP.search(resource.data)
            if match is None:
                return
            css_charset = (match.group(1) or match.group(2) or "").lower()
            if not css_charset:
                return
            if bom_charset is None and css_charset not in (UTF8_CHARSET, charset or ""):
                # an @charset declaration wins over the MIME-declared charset
                resource.data = decode_string(resource.raw_data, css_charset)
            # the directive describes the original bytes, not the decoded text
            resource.data = CSS_CHARSET_REGEXP.sub("", resource.data, count=1)

        def process_document_charset(charset: str | None, bom_charset: str | None) -> str | None:
            "Process document charset."
            assert resource is not None
            assert resource.raw_data is not None
            document_charset = charset
            try:
                dom = lxml_html.fromstring(resource.data)
            except (ValueError, etree.LxmlError):
                return document_charset
            charset_metas = [element for element in dom.iter("meta") if element.get("charset")]
            if charset_metas:
                html_charset = (charset_metas[0].get("charset") or "").lower()
                if html_charset and bom_charset is None and html_charset not in (UTF8_CHARSET, charset or ""):
                    resource.data = decode_string(resource.raw_data, html_charset)
                    document_charset = html_charset
                return document_charset
            for meta in dom.iter("meta"):
                if (meta.get("http-equiv") or "").lower() != "content-type":
                    continue
                html_charset = get_charset(meta.get("content"))
                if html_charset and bom_charset is None and html_charset not in (UTF8_CHARSET, document_charset or ""):
                    resource.data = decode_string(resource.raw_data, html_charset)
                    document_charset = html_charset
                break
            return document_charset

        # -- mislabeled plain-HTML detection -------------------------------------
        if _starts_with_markup(mhtml):
            anomalies.append(Anomaly(MISLABELED_DOCUMENT_ANOMALY))
            headers["content-type"] = "text/html"
            init_resource(headers)
            assert resource is not None
            append_data(mhtml)
            resource.data = flatten_data()
            process_resource()
            return Mhtml(
                headers=headers,
                frames=frames,
                resources=resources,
                index=context.get("index"),
                anomalies=anomalies,
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
                        assert resource is not None
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
                assert resource is not None
                index_end_data = parse_resource_data()
                resource.data = flatten_data()
                if index_start_embedded_mhtml is not None and index_end_data is not None:
                    resource.used = True
                    context["index"] = convert_embedded_mhtml(index_end_data)
                else:
                    process_resource()
                state = MHTML_END if index_mhtml >= n - 1 else MHTML_CONTENT

        return Mhtml(
            headers=headers,
            frames=frames,
            resources=resources,
            index=context.get("index"),
            anomalies=anomalies,
        )


# ===== Consolidated from converter/resource_manager.py =====


class ResourceCache:
    """Thread-safe, bounded LRU cache with optional persistent disk storage."""

    def __init__(self, max_size: int = 100, cache_dir: Path | None = None) -> None:
        """Create a cache holding at most ``max_size`` in-memory resources."""
        self.max_size = max_size
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._entries: OrderedDict[str, Resource] = OrderedDict()
        self._lock = threading.RLock()

    def put(self, key: str, resource: Resource) -> None:
        """Insert or replace a resource, persisting it when configured."""
        if self.max_size == 0:
            return
        with self._lock:
            self._entries.pop(key, None)
            self._entries[key] = resource
            while len(self._entries) > self.max_size:
                self._entries.popitem(last=False)
        self._write_disk(key, resource)

    def get(self, key: str) -> Resource | None:
        """Retrieve a resource and update its LRU position."""
        with self._lock:
            resource = self._entries.pop(key, None)
            if resource is not None:
                self._entries[key] = resource
                return resource
        resource = self._read_disk(key)
        if resource is not None:
            with self._lock:
                self._entries[key] = resource
                while len(self._entries) > self.max_size:
                    self._entries.popitem(last=False)
        return resource

    def clear(self, *, clear_disk: bool = False) -> None:
        """Clear memory entries and optionally remove persisted cache files."""
        with self._lock:
            self._entries.clear()
        if clear_disk and self.cache_dir is not None and self.cache_dir.exists():
            for cache_file in self.cache_dir.glob("*.json"):
                cache_file.unlink(missing_ok=True)
                cache_file.with_suffix(".bin").unlink(missing_ok=True)

    def _cache_paths(self, key: str) -> tuple[Path, Path] | None:
        if self.cache_dir is None:
            return None
        filename = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{filename}.json", self.cache_dir / f"{filename}.bin"

    def _write_disk(self, key: str, resource: Resource) -> None:
        """Write one resource atomically to the optional disk cache."""
        paths = self._cache_paths(key)
        cache_dir = self.cache_dir
        if paths is None or cache_dir is None:
            return
        metadata_path, content_path = paths
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            is_text = isinstance(resource.content, str)
            content = resource.content.encode("utf-8") if isinstance(resource.content, str) else resource.content
            metadata = {
                "url": resource.url,
                "content_type": resource.content_type,
                "resource_type": resource.resource_type.value,
                "encoding": resource.encoding,
                "headers": resource.headers,
                "original_url": resource.original_url,
                "is_text": is_text,
            }
            content_tmp = content_path.with_suffix(".tmp")
            metadata_tmp = metadata_path.with_suffix(".tmp")
            content_tmp.write_bytes(content)
            metadata_tmp.write_text(json.dumps(metadata), encoding="utf-8")
            content_tmp.replace(content_path)
            metadata_tmp.replace(metadata_path)
        except OSError as error:
            logger.warning("Unable to persist resource cache entry {}: {}", key, error)

    def _read_disk(self, key: str) -> Resource | None:
        """Read and validate one resource from the optional disk cache."""
        paths = self._cache_paths(key)
        if paths is None:
            return None
        metadata_path, content_path = paths
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            raw_content = content_path.read_bytes()
            content: bytes | str = raw_content
            if metadata.get("is_text"):
                content = raw_content.decode("utf-8", errors="replace")
            return Resource(
                url=metadata["url"],
                content_type=metadata["content_type"],
                content=content,
                resource_type=ResourceType(metadata["resource_type"]),
                encoding=metadata.get("encoding"),
                headers=metadata.get("headers", {}),
                original_url=metadata.get("original_url"),
            )
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            return None


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects to URLs outside the resource manager's policy."""

    def __init__(self, is_allowed: Callable[[str], bool]) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__()
        self._is_allowed = is_allowed

    # Signature is fixed by urllib.request.HTTPRedirectHandler.
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not self._is_allowed(newurl):
            raise urllib.error.HTTPError(
                newurl,
                code,
                "Redirect target rejected by resource security policy",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ResourceManager:
    """Resolve URLs against MHTML resources and optionally fetch public assets."""

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Set up cache and fetch settings from the converter configuration."""
        self.config = config or ConverterConfig()
        cache_dir = self.config.cache_dir if self.config.cache_enabled else None
        cache_size = self.config.cache_size if self.config.cache_enabled else 0
        self.cache = ResourceCache(cache_size, cache_dir)
        self._opener = urllib.request.build_opener(_SafeRedirectHandler(self._is_safe_external_url))

    @staticmethod
    def normalize_url(url: str, base_url: str) -> str:
        """Resolve relative URLs and discard fragments for asset lookup."""
        value = url.strip()
        if value.lower().startswith(("data:", "cid:")):
            return value
        normalized = urllib.parse.urljoin(base_url, value)
        return urllib.parse.urldefrag(normalized)[0]

    @staticmethod
    def _normalize_url(url: str, base_url: str) -> str:
        """Backwards-compatible alias for :meth:`normalize_url`."""
        return ResourceManager.normalize_url(url, base_url)

    @staticmethod
    def _convert_to_data_uri(resource: Resource) -> str:
        """Encode a resource as an RFC 2397 base64 data URI."""
        return resource_to_data_uri(resource)

    @staticmethod
    # Ordered signature checks make the MIME fallback straightforward to review.
    # pylint: disable=too-many-return-statements
    def _guess_content_type(url: str, content: bytes) -> str:
        """Guess MIME type from URL extension or a few common file signatures."""
        path = urllib.parse.urlsplit(url).path
        content_type, _ = mimetypes.guess_type(path)
        if content_type:
            return content_type
        if content.lstrip().lower().startswith((b"<html", b"<!doctype html")):
            return "text/html"
        if content.lstrip().startswith((b"body {", b"@import", b"@media")):
            return "text/css"
        if content.lstrip().startswith((b"function", b"console", b"const ", b"let ")):
            return "application/javascript"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
        return "application/octet-stream"

    def process_resources_parallel(
        self,
        urls: list[str],
        base_url: str,
    ) -> dict[str, Resource | None]:
        """Resolve the unique URLs in parallel where enabled, using the cache."""
        normalized_urls = list(dict.fromkeys(self.normalize_url(url, base_url) for url in urls))
        result: dict[str, Resource | None] = {}
        pending: list[str] = []
        for url in normalized_urls:
            cached = self.cache.get(url)
            if cached is None:
                pending.append(url)
            else:
                result[url] = cached

        if self.config.parallel_processing and len(pending) > 1:
            with ThreadPoolExecutor(max_workers=min(self.config.max_workers, len(pending))) as pool:
                futures = {pool.submit(self._fetch_resource, url, base_url): url for url in pending}
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        result[url] = future.result()
                    except (OSError, ValueError, urllib.error.URLError) as error:
                        logger.debug("Resource fetch failed for {}: {}", url, error)
                        result[url] = None
        else:
            for url in pending:
                try:
                    result[url] = self._fetch_resource(url, base_url)
                except (OSError, ValueError, urllib.error.URLError) as error:
                    logger.debug("Resource fetch failed for {}: {}", url, error)
                    result[url] = None
        return result

    # Explicit guard clauses keep network limits and security policy obvious.
    # pylint: disable=too-many-return-statements
    def _fetch_resource(self, url: str, base_url: str) -> Resource | None:
        """Fetch a data URI or permitted public HTTP(S) asset within size limits."""
        normalized_url = self.normalize_url(url, base_url)
        if normalized_url.lower().startswith("data:"):
            return self._parse_data_uri(normalized_url)
        if normalized_url.lower().startswith("cid:") or not self.config.fetch_external:
            return None
        if not self._is_safe_external_url(normalized_url):
            logger.warning("Refusing unsafe external resource URL: {}", normalized_url)
            return None

        request = urllib.request.Request(
            normalized_url,
            headers={"User-Agent": "MHTML-Converter/1.0"},
        )
        for attempt in range(self.config.max_fetch_retries + 1):
            try:
                with self._opener.open(request, timeout=self.config.fetch_timeout) as response:
                    final_url = response.geturl()
                    if not self._is_safe_external_url(final_url):
                        logger.warning("Refusing unsafe redirect target: {}", final_url)
                        return None
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self.config.max_resource_size:
                        logger.warning("Resource exceeds configured size limit: {}", final_url)
                        return None
                    content = response.read(self.config.max_resource_size + 1)
                    if len(content) > self.config.max_resource_size:
                        logger.warning("Resource exceeds configured size limit: {}", final_url)
                        return None
                    content_type = response.headers.get_content_type()
                    resource = Resource(
                        url=final_url,
                        content_type=content_type or self._guess_content_type(final_url, content),
                        content=content,
                        resource_type=self._get_resource_type(content_type),
                        encoding=response.headers.get("Content-Transfer-Encoding"),
                        headers=dict(response.headers.items()),
                        original_url=url,
                    )
                    transformed = self.transform_resource(resource)
                    self.cache.put(normalized_url, transformed)
                    return transformed
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                if attempt >= self.config.max_fetch_retries:
                    logger.debug("Could not fetch {}: {}", normalized_url, error)
                    return None
        return None

    # Early returns make the URL policy easy to audit as a sequence of checks.
    # pylint: disable=too-many-return-statements
    def _is_safe_external_url(self, url: str) -> bool:
        """Allow public HTTP(S) hosts that satisfy the configured domain policy."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return False
        if parts.username is not None or parts.password is not None:
            return False
        host = parts.hostname.rstrip(".").lower()
        if not self.config.allow_private_hosts and (
            host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal"))
        ):
            return False
        if self._domain_matches(host, self.config.blocked_domains):
            return False
        if self.config.allowed_domains and not self._domain_matches(host, self.config.allowed_domains):
            return False

        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                addresses = [
                    ipaddress.ip_address(result[4][0])
                    for result in socket.getaddrinfo(host, parts.port or 80, type=socket.SOCK_STREAM)
                ]
            except (OSError, ValueError):
                return False
        if self.config.allow_private_hosts:
            return bool(addresses)
        return bool(addresses) and all(address.is_global for address in addresses)

    @staticmethod
    def _domain_matches(host: str, domains: list[str]) -> bool:
        """Match an exact hostname or a subdomain of a configured domain."""
        return any(host == domain or host.endswith(f".{domain}") for domain in domains)

    # Each malformed data-URI condition returns safely without extra nesting.
    # pylint: disable=too-many-return-statements
    def _parse_data_uri(self, data_uri: str) -> Resource | None:
        """Decode a data URI, rejecting malformed or oversized payloads."""
        if not data_uri.lower().startswith("data:"):
            return None
        header, separator, data = data_uri[5:].partition(",")
        if not separator:
            return None
        header_parts = header.split(";")
        content_type = header_parts[0] or "text/plain"
        is_base64 = any(part.lower() == "base64" for part in header_parts[1:])
        try:
            if is_base64:
                content = base64.b64decode(re.sub(r"\s+", "", data), validate=False)
            else:
                content = urllib.parse.unquote_to_bytes(data)
        except (ValueError, binascii.Error):
            return None
        if len(content) > self.config.max_resource_size:
            return None
        resource = Resource(
            url=data_uri,
            content_type=content_type,
            content=content,
            resource_type=self._get_resource_type(content_type),
            encoding="base64" if is_base64 else "7bit",
            original_url=data_uri,
        )
        return self.transform_resource(resource)

    @staticmethod
    def _get_resource_type(content_type: str) -> ResourceType:
        """Compatibility wrapper for shared MIME resource classification."""
        return classify_resource_type(content_type)

    def transform_resource(self, resource: Resource) -> Resource:
        """Apply the first matching user handler to a resource."""
        media_type = resource.content_type.split(";", maxsplit=1)[0].lower()
        handler = next(
            (
                candidate
                for key, candidate in self.config.custom_handlers.items()
                if key.lower()
                in {
                    media_type,
                    resource.resource_type.value,
                    f"{media_type.split('/')[0]}/*",
                }
            ),
            None,
        )
        if handler is None:
            return resource
        transformed = handler(resource)
        if transformed is None:
            return resource
        if isinstance(transformed, Resource):
            return transformed
        return Resource(
            url=resource.url,
            content_type=resource.content_type,
            content=transformed,
            resource_type=resource.resource_type,
            encoding=resource.encoding,
            headers=resource.headers.copy(),
            original_url=resource.original_url,
        )


# ===== Consolidated from converter/css_processor.py =====


class CSSProcessor:
    """Rewrite CSS asset references with tinycss2 while preserving valid syntax."""

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Create a CSS processor bound to a converter configuration."""
        self.config = config or ConverterConfig()

    def rewrite_css(
        self,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
        depth: int = 0,
    ) -> str:
        """Resolve CSS ``url()`` and ``@import`` references against resources.

        Comments are removed when ``strip_css_comments`` is enabled. At or past
        ``max_import_depth``, imports are left as normalized references and are
        not expanded further.
        """
        if depth > self.config.max_import_depth:
            return content
        rules = tinycss2.parse_stylesheet(
            content,
            skip_comments=self.config.strip_css_comments,
            skip_whitespace=False,
        )
        self._rewrite_tokens(rules, base_url, resource_map, depth)
        result = cast("str", tinycss2.serialize(rules))
        if self.config.minify_css:
            result = self._conservative_minify(result)
        return result

    def extract_urls(self, content: str) -> set[str]:
        """Return URL tokens and string-form ``@import`` references in CSS."""
        rules = tinycss2.parse_stylesheet(content, skip_comments=True, skip_whitespace=False)
        urls: set[str] = set()
        self._collect_urls(rules, urls)
        return urls

    @staticmethod
    def parse_css(content: str) -> list[Any]:
        """Parse a stylesheet and expose tinycss2 error tokens for validation."""
        return cast(
            "list[Any]",
            tinycss2.parse_stylesheet(content, skip_comments=False, skip_whitespace=False),
        )

    def _rewrite_tokens(
        self,
        tokens: list[Any],
        base_url: str,
        resource_map: dict[str, Resource],
        depth: int,
    ) -> None:
        """Mutate tinycss2 tokens recursively, rewriting nested URL values."""
        for token in tokens:
            token_type = getattr(token, "type", "")
            if token_type == "url":
                token.value = self._rewrite_url(token.value, base_url, resource_map)
                token.representation = self._url_representation(token.value)
            elif token_type == "function" and token.name.lower() == "url":
                url = self._function_url_value(token)
                rewritten = self._rewrite_url(url, base_url, resource_map)
                token.arguments = [
                    StringToken(
                        token.source_line,
                        token.source_column,
                        rewritten,
                        self._string_representation(rewritten),
                    )
                ]
            elif token_type == "at-rule":
                if token.lower_at_keyword == "import":
                    self._rewrite_import(token, base_url, resource_map, depth)
                else:
                    self._rewrite_tokens(token.prelude, base_url, resource_map, depth)
                if token.content is not None:
                    self._rewrite_tokens(token.content, base_url, resource_map, depth)
            else:
                nested = getattr(token, "content", None)
                if isinstance(nested, list):
                    self._rewrite_tokens(nested, base_url, resource_map, depth)
                prelude = getattr(token, "prelude", None)
                if isinstance(prelude, list):
                    self._rewrite_tokens(prelude, base_url, resource_map, depth)
                arguments = getattr(token, "arguments", None)
                if isinstance(arguments, list) and token_type != "function":
                    self._rewrite_tokens(arguments, base_url, resource_map, depth)

    def _rewrite_import(
        self,
        token: Any,
        base_url: str,
        resource_map: dict[str, Resource],
        depth: int,
    ) -> None:
        """Rewrite the first URL in an ``@import`` prelude."""
        if depth >= self.config.max_import_depth:
            return
        for item in token.prelude:
            if item.type in {"whitespace", "comment"}:
                continue
            if item.type == "string":
                rewritten = self._rewrite_url(item.value, base_url, resource_map)
                item.value = rewritten
                item.representation = self._string_representation(rewritten)
            elif item.type == "url":
                item.value = self._rewrite_url(item.value, base_url, resource_map)
                item.representation = self._url_representation(item.value)
            elif item.type == "function" and item.name.lower() == "url":
                url = self._function_url_value(item)
                rewritten = self._rewrite_url(url, base_url, resource_map)
                item.arguments = [
                    StringToken(
                        item.source_line,
                        item.source_column,
                        rewritten,
                        self._string_representation(rewritten),
                    )
                ]
            break

    def _rewrite_url(
        self,
        url: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Return an inlined URI or normalized safe URL for one asset reference."""
        value = url.strip().strip("\"'")
        if not value or value.lower().startswith("data:"):
            return value
        scheme = urllib.parse.urlsplit(value).scheme.lower()
        if scheme in {"javascript", "vbscript"}:
            return ""
        return resolve_resource_url(value, base_url, resource_map, self.config)

    @staticmethod
    def _function_url_value(token: Any) -> str:
        """Extract the unquoted URL from a parsed ``url(...)`` function token."""
        if len(token.arguments) == 1 and hasattr(token.arguments[0], "value"):
            return str(token.arguments[0].value)
        serialized = cast("str", tinycss2.serialize(token.arguments))
        return serialized.strip().strip("\"'")

    @staticmethod
    def _string_representation(value: str) -> str:
        """Quote a CSS string, escaping delimiters and backslashes."""
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\a ")
        return f'"{escaped}"'

    @classmethod
    def _url_representation(cls, value: str) -> str:
        """Serialize a URL token without allowing its value to break CSS syntax."""
        return f"url({cls._string_representation(value)})"

    def _collect_urls(self, tokens: list[Any], urls: set[str]) -> None:
        """Walk a tinycss2 token tree and collect URLs and import strings."""
        for token in tokens:
            self._collect_token_urls(token, urls)

    def _collect_token_urls(self, token: Any, urls: set[str]) -> None:
        """Collect direct URL values and recursively visit a token's children."""
        token_type = getattr(token, "type", "")
        if token_type == "url" and token.value:
            urls.add(token.value)
        elif token_type == "function" and token.name.lower() == "url":
            url = self._function_url_value(token)
            if url:
                urls.add(url)
        elif token_type == "at-rule" and token.lower_at_keyword == "import":
            self._collect_import_url(token.prelude, urls)
        self._collect_nested_urls(token, urls)

    def _collect_import_url(self, prelude: list[Any], urls: set[str]) -> None:
        """Collect the URL-like first item of an ``@import`` prelude."""
        for item in prelude:
            if item.type in {"whitespace", "comment"}:
                continue
            if item.type in {"string", "url"} and item.value:
                urls.add(item.value)
            elif item.type == "function" and item.name.lower() == "url":
                url = self._function_url_value(item)
                if url:
                    urls.add(url)
            return

    def _collect_nested_urls(self, token: Any, urls: set[str]) -> None:
        """Recursively inspect nested component values, rules, and function args."""
        for name in ("content", "prelude", "arguments"):
            nested = getattr(token, name, None)
            if isinstance(nested, list):
                self._collect_urls(nested, urls)

    def _conservative_minify(self, content: str) -> str:
        """Reduce redundant spaces only around punctuation safe in CSS syntax."""
        result = re.sub(r"\s*([{}:;,>])\s*", r"\1", content)
        return result.strip()

    def _rewrite_with_regex_fallback(
        self,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Compatibility helper for consumers that need a regex-only fallback."""
        url_pattern = re.compile(
            r"url\(\s*(?P<quote>[\"']?)(?P<url>[^)\"']+)(?P=quote)\s*\)",
            re.IGNORECASE,
        )

        def replace(match: re.Match[str]) -> str:
            original = match.group(0)
            rewritten = self._rewrite_url(match.group("url"), base_url, resource_map)
            return f"url({self._string_representation(rewritten)})" if rewritten else original

        stripped = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL) if self.config.strip_css_comments else content
        return url_pattern.sub(replace, stripped)


# ===== Consolidated from converter/html_processor.py =====

_VOID_TAGS = frozenset({
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
})
_SAFE_EXTRA_ATTRIBUTES = frozenset({
    "style",
    "srcset",
    "sizes",
    "loading",
    "decoding",
    "fetchpriority",
    "crossorigin",
    "integrity",
    "referrerpolicy",
    "download",
    "poster",
    "background",
    "data",
    "form",
    "formaction",
    "formenctype",
    "formmethod",
    "formnovalidate",
    "formtarget",
    "placeholder",
    "pattern",
    "minlength",
    "maxlength",
    "autocomplete",
    "autofocus",
    "required",
    "multiple",
    "novalidate",
    "step",
    "min",
    "max",
    "controls",
    "loop",
    "muted",
    "autoplay",
    "preload",
    "sandbox",
    "allowfullscreen",
    "frameborder",
    "scrolling",
    "marginwidth",
    "marginheight",
    "xlink:href",
})


# This compatibility model mirrors the legacy public node constructor.
# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments
class HTMLNode:
    """Small mutable HTML node model retained for the legacy tree API."""

    def __init__(
        self,
        tag: str = "",
        is_text: bool = False,
        text: str = "",
        is_comment: bool = False,
        comment: str = "",
        is_doctype: bool = False,
        attributes: dict[str, str] | None = None,
    ) -> None:
        """Initialize a node and its relationship fields."""
        self.tag = tag
        self.is_text = is_text
        self.text = text
        self.is_comment = is_comment
        self.comment = comment
        self.is_doctype = is_doctype
        self.attributes = attributes.copy() if attributes else {}
        self.children: list[HTMLNode] = []
        self.parent: HTMLNode | None = None

    def add_child(self, child: HTMLNode) -> None:
        """Append a child and update its parent pointer."""
        child.parent = self
        self.children.append(child)

    def to_string(self) -> str:
        """Serialize this node with text/attribute escaping."""
        if self.is_doctype:
            return ""
        if self.is_comment:
            safe_comment = self.comment.replace("--", "- -")
            return f"<!--{safe_comment}-->"
        if self.is_text:
            return html_stdlib.escape(self.text, quote=False)
        if not self.tag:
            return "".join(child.to_string() for child in self.children)

        attributes = "".join(
            f' {name}="{html_stdlib.escape(value, quote=True)}"' for name, value in self.attributes.items()
        )
        children = "".join(child.to_string() for child in self.children)
        if self.tag.lower() in _VOID_TAGS:
            return f"<{self.tag}{attributes} />"
        return f"<{self.tag}{attributes}>{children}</{self.tag}>"


# Maintains the old API shape while exposing one public ``feed`` operation.
# pylint: disable=too-few-public-methods
class HTMLTreeParser:
    """Compatibility tree parser that exposes :class:`HTMLNode` instances."""

    def __init__(self) -> None:
        """Initialize an empty document root."""
        self.root = HTMLNode()
        self.current = self.root

    def feed(self, html_content: str) -> None:
        """Parse an HTML document, retaining comments and meaningful text."""
        parser = lxml_html.HTMLParser(recover=True, remove_comments=False)
        try:
            tree = lxml_html.fromstring(html_content, parser=parser)
        except (ValueError, etree.LxmlError):
            self.root = HTMLNode()
            self.root.add_child(HTMLNode(tag="html"))
            self.current = self.root
            return
        self.root = HTMLNode()
        self.root.add_child(self._convert_lxml_to_node(tree))
        self.current = self.root

    def _convert_lxml_to_node(self, element: Any) -> HTMLNode:
        """Copy an lxml element and its mixed text content into HTMLNode form."""
        if isinstance(element, lxml_html.HtmlComment):
            comment_text = element.text or ""
            return HTMLNode(is_comment=True, comment=comment_text, text=comment_text)

        node = HTMLNode(tag=str(element.tag), attributes=dict(element.attrib))
        if element.text and element.text.strip():
            node.add_child(HTMLNode(is_text=True, text=element.text))
        for child in element:
            node.add_child(self._convert_lxml_to_node(child))
            if child.tail and child.tail.strip():
                node.add_child(HTMLNode(is_text=True, text=child.tail))
        return node


_INLINED_DATA_URI_REGEXP = re.compile(
    r"data:(?!image/)[a-z0-9.+-]+/[a-z0-9.+-]+;base64,",
    re.IGNORECASE,
)
_CSS_EXPRESSION_REGEXP = re.compile(r"expression\s*\(.{0,1024}?\)", re.DOTALL | re.IGNORECASE)


class _MHTMLCleaner(Cleaner):
    """Preserve opted-in inlined assets while filtering active URL schemes."""

    safe_attrs: frozenset[str]
    allow_frame_documents: bool = False

    def __init__(
        self,
        allow_inline_scripts: bool,
        allow_inline_styles: bool,
        allow_external_stylesheets: bool,
        **options: Any,
    ) -> None:
        "Initialize the instance state from the supplied arguments."
        self.allow_inline_scripts = allow_inline_scripts
        self.allow_inline_styles = allow_inline_styles
        self.allow_external_stylesheets = allow_external_stylesheets
        super().__init__(**options)

    @staticmethod
    def _data_uri_media_type(url: str) -> str:
        """Return a data URI media type, ignoring parameters."""
        if not url.lower().startswith("data:"):
            return ""
        return url[5:].split(",", maxsplit=1)[0].split(";", maxsplit=1)[0].lower()

    def sanitize_url(self, link: str) -> str:
        """Reject scriptable URL schemes and allow intended data resources."""
        compact = re.sub(r"[\s\x00-\x20]+", "", urllib.parse.unquote(link).strip()).lower()
        media_type = self._data_uri_media_type(compact)
        if compact.startswith("data:"):
            allowed_media_type = (
                (
                    self.allow_inline_scripts
                    and media_type
                    in {
                        "application/javascript",
                        "application/x-javascript",
                        "text/javascript",
                    }
                )
                or (self.allow_inline_styles and media_type == "text/css")
                or (media_type.startswith("image/") and media_type != "image/svg+xml")
                or media_type in {"font/woff", "font/woff2", "application/font-woff"}
                or (self.allow_frame_documents and media_type in {"text/html", "application/xhtml+xml"})
            )
            return link if allowed_media_type else ""
        scheme = urllib.parse.urlsplit(compact).scheme
        return "" if scheme in {"javascript", "vbscript", "file", "filesystem", "blob"} else link

    def _remove_javascript_link(self, link: str) -> str:
        """Adapt lxml's callback to the public URL sanitizer."""
        return self.sanitize_url(link)

    def _has_sneaky_javascript(self, style: Any):
        """Mask inlined data: URIs before the stock sneaky-content checks.

        lxml_html_clean counts every ``data:`` scheme as malicious (image
        data URLs only balance the count); plain-image URIs balance
        themselves, but stylesheet/font/script URIs this converter inlines
        would be flagged.  Non-image URIs — already URL-sanitized during
        rewriting — are masked with a placeholder containing no colon so no
        scheme-like substring can match.
        """
        masked = _INLINED_DATA_URI_REGEXP.sub("mhtml-inlined-asset", style)
        return cast(Any, super())._has_sneaky_javascript(masked)

    def allow_element(self, el: Any) -> bool:
        """Keep only enabled stylesheets when lxml checks embedded links."""
        if el.tag == "link" and "stylesheet" in el.get("rel", "").lower():
            href = el.get("href", "")
            media_type = self._data_uri_media_type(href)
            if self.allow_inline_styles and media_type == "text/css":
                return True
            if self.allow_external_stylesheets:
                return urllib.parse.urlsplit(href).scheme.lower() in {"http", "https"}
        return False


class HTMLProcessor:
    """Parse, validate, rewrite, and sanitize HTML content."""

    URL_ATTRIBUTES: ClassVar[tuple[str, ...]] = (
        "src",
        "href",
        "poster",
        "background",
        "action",
        "formaction",
        "data",
        "xlink:href",
    )
    RESOURCE_TAGS: ClassVar[dict[str, frozenset[str]]] = {
        "img": frozenset({"src", "srcset"}),
        "script": frozenset({"src"}),
        "link": frozenset({"href"}),
        "source": frozenset({"src", "srcset"}),
        "video": frozenset({"src", "poster"}),
        "audio": frozenset({"src"}),
        "track": frozenset({"src"}),
        "iframe": frozenset({"src"}),
        "embed": frozenset({"src"}),
        "object": frozenset({"data"}),
        "body": frozenset({"background"}),
        "table": frozenset({"background"}),
        "td": frozenset({"background"}),
        "th": frozenset({"background"}),
        "input": frozenset({"src"}),
    }

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Create an HTML processor and its CSS rewriter."""
        self.config = config or ConverterConfig()
        self.css_processor = CSSProcessor(self.config)
        self.cleaner = _MHTMLCleaner(
            allow_inline_scripts=self.config.include_scripts and not self.config.remove_scripts,
            allow_inline_styles=self.config.include_styles,
            allow_external_stylesheets=(self.config.resource_handling in {"external", "mixed"}),
            safe_attrs_only=True,
            safe_attrs=frozenset(getattr(Cleaner, "safe_attrs", frozenset())) | _SAFE_EXTRA_ATTRIBUTES,
            scripts=not self.config.include_scripts or self.config.remove_scripts,
            style=not self.config.include_styles,
            links=False,
            meta=False,
            page_structure=False,
            forms=False,
            frames=False,
            embedded=False,
            kill_tags=("base",),
            remove_unknown_tags=False,
        )

    def parse_basic_html(self, content: str) -> Any:
        """Parse an HTML fragment or document with error recovery enabled."""
        parser = lxml_html.HTMLParser(recover=True, remove_comments=False)
        if not content.strip():
            return lxml_html.fromstring("<html><body></body></html>", parser=parser)
        return lxml_html.fromstring(content, parser=parser)

    def to_string(self, element: Any, doctype: str | None = None) -> str:
        """Serialize an lxml element, preserving the source doctype."""
        method = "xml" if self.config.output_format == "xhtml" else "html"
        serialized = lxml_html.tostring(element, encoding="unicode", method=method)
        if doctype:
            return f"{doctype}\n{serialized}"
        return serialized

    def extract_resource_urls(self, content: str) -> set[str]:
        """Find fetchable assets in HTML, excluding ordinary anchor navigation."""
        try:
            tree = self.parse_basic_html(content)
        except (ValueError, etree.LxmlError):
            return set()
        urls: set[str] = set()
        for element in tree.iter():
            tag = str(element.tag).lower()
            attributes: set[str] = set(self.RESOURCE_TAGS.get(tag, frozenset()))
            if tag == "script" and (not self.config.include_scripts or self.config.remove_scripts):
                attributes = set()
            if tag == "link":
                rel = element.get("rel", "").lower().split()
                allowed_rel = {"icon", "preload", "modulepreload", "manifest"}
                if self.config.include_styles:
                    allowed_rel.add("stylesheet")
                if not allowed_rel.intersection(rel):
                    attributes = set()
            for name in attributes:
                value = element.get(name)
                if not value:
                    continue
                if name == "srcset":
                    urls.update(candidate["url"] for candidate in parse_srcset(value) if candidate.get("url"))
                else:
                    urls.add(value.strip())
            style = element.get("style")
            if style:
                urls.update(self.css_processor.extract_urls(f"x{{{style}}}"))
            if tag == "style" and element.text:
                urls.update(self.css_processor.extract_urls(element.text))
        return {url for url in urls if url and not url.lower().startswith("data:")}

    def rewrite_html(
        self,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Rewrite assets in HTML and sanitize the result when configured."""
        try:
            tree = self.parse_basic_html(content)
        except (ValueError, etree.LxmlError):
            return self._rewrite_with_regex_fallback(content, base_url, resource_map)
        doctype = self._document_doctype(tree)

        for element in tree.iter():
            for attribute in self.URL_ATTRIBUTES:
                if attribute in element.attrib:
                    element.attrib[attribute] = self._rewrite_url(element.attrib[attribute], base_url, resource_map)
            if "srcset" in element.attrib:
                candidates = parse_srcset(element.attrib["srcset"])
                for candidate in candidates:
                    candidate["url"] = self._rewrite_url(candidate["url"], base_url, resource_map)
                element.attrib["srcset"] = serialize_srcset(candidates)
            if self.config.include_styles and "style" in element.attrib:
                declaration = element.attrib["style"]
                rewritten = self.css_processor.rewrite_css(f"mhtml-inline{{{declaration}}}", base_url, resource_map)
                element.attrib["style"] = self._extract_rule_body(rewritten)
            if self.config.include_styles and str(element.tag).lower() == "style" and element.text:
                element.text = self.css_processor.rewrite_css(element.text, base_url, resource_map)

        self._normalize_charset_meta(tree)
        if self.config.sanitize_html:
            self._extend_safe_attributes(tree)
            tree = self._clean_preserving_inlined_styles(tree)
        self._inject_csp(tree)
        return self.to_string(tree, doctype)

    def validate_html(self, content: str) -> list[str]:
        """Return parser diagnostics for an HTML document or fragment."""
        parser = lxml_html.HTMLParser(recover=True, remove_comments=False)
        try:
            lxml_html.fromstring(content or "<html></html>", parser=parser)
        except (ValueError, etree.LxmlError) as error:
            return [str(error)]
        return [str(entry) for entry in parser.error_log]

    def minify_html(self, content: str) -> str:
        """Remove comments and serialize compactly while preserving text spacing."""
        parser = lxml_html.HTMLParser(recover=True, remove_comments=True)
        try:
            tree = lxml_html.fromstring(content or "<html></html>", parser=parser)
        except (ValueError, etree.LxmlError):
            return content
        method = "xml" if self.config.output_format == "xhtml" else "html"
        serialized = lxml_html.tostring(tree, encoding="unicode", method=method)
        doctype = self._document_doctype(tree)
        return f"{doctype}\n{serialized}" if doctype else serialized

    def _extend_safe_attributes(self, tree: Any) -> None:
        """Preserve data-/ARIA attributes while dropping event-handler attributes."""
        safe_attributes = set(getattr(self.cleaner, "safe_attrs", frozenset()))
        for element in tree.iter():
            for name in tuple(element.attrib):
                normalized = name.lower()
                if normalized.startswith(("data-", "aria-")):
                    safe_attributes.add(name)
        self.cleaner.safe_attrs = frozenset(safe_attributes)

    @staticmethod
    def _extract_rule_body(css: str) -> str:
        """Extract the declaration block from a temporary selector wrapper."""
        opening = css.find("{")
        closing = css.rfind("}")
        if opening < 0 or closing < opening:
            return css
        return css[opening + 1 : closing].strip()

    def _document_doctype(self, tree: Any) -> str:
        """Return the source document's doctype, if any."""
        try:
            doctype = tree.getroottree().docinfo.doctype
        except AttributeError:
            return ""
        return doctype or ""

    def _clean_preserving_inlined_styles(self, tree: Any) -> Any:
        """Clean the tree without destroying the inlined CSS it now carries.

        lxml_html_clean strips ``@import`` directives and deletes ``<style>``
        bodies that merely contain ``data:`` URIs — the very output this
        converter produces.  The rewritten CSS already passed URL
        sanitization in the CSS processor, so the style texts are restored;
        IE-era ``expression(...)`` is still stripped.
        """
        style_texts = [element.text for element in tree.iter("style")]
        # Frame documents were rewritten and sanitized as sub-documents; their
        # data: URIs may pass the URL filter during this pass only.
        self.cleaner.allow_frame_documents = True
        try:
            cleaned = self.cleaner.clean_html(tree)
        finally:
            self.cleaner.allow_frame_documents = False
        cleaned_styles = list(cleaned.iter("style"))
        if len(cleaned_styles) == len(style_texts):
            for element, original in zip(cleaned_styles, style_texts):
                if original is not None and element.text != original:
                    element.text = _CSS_EXPRESSION_REGEXP.sub("", original)
        return cleaned

    def _normalize_charset_meta(self, tree: Any) -> None:
        """Declare UTF-8 output and drop conflicting legacy charset claims."""
        has_charset = False
        for element in tree.iter("meta"):
            if element.get("charset"):
                element.set("charset", "utf-8")
                has_charset = True
            elif (element.get("http-equiv") or "").lower() == "content-type":
                content = element.get("content") or ""
                if get_charset(content):
                    element.set("content", replace_charset(content, "utf-8"))
                    has_charset = True
        head = tree.find("head") if str(tree.tag).lower() == "html" else None
        if head is not None and not has_charset:
            meta = etree.Element("meta")
            meta.set("charset", "utf-8")
            head.insert(0, meta)

    def _inject_csp(self, tree: Any) -> None:
        """Restrict the self-contained page with a Content-Security-Policy."""
        if not self.config.inject_csp:
            return
        head = tree.find("head") if str(tree.tag).lower() == "html" else None
        if head is None:
            return
        scripts_enabled = self.config.include_scripts and not self.config.remove_scripts
        meta = etree.Element("meta")
        meta.set("http-equiv", "content-security-policy")
        meta.set(
            "content",
            DEFAULT_CSP + (JS_ENABLED_CSP if scripts_enabled else JS_DISABLED_CSP),
        )
        head.insert(0, meta)

    def _rewrite_url(
        self,
        url: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Return an inlined data URI or a normalized URL for an HTML attribute."""
        value = url.strip()
        if not value:
            return value
        if self.config.sanitize_html and not self.cleaner.sanitize_url(value):
            return ""
        return resolve_resource_url(value, base_url, resource_map, self.config)

    def _rewrite_with_regex(
        self,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Regex-only URL rewrite retained for compatibility and parse recovery."""
        pattern = re.compile(
            r"(?P<prefix>\b(?:src|href|poster|background|action|data)\s*=\s*)"
            r"(?P<quote>[\"'])(?P<url>.*?)(?P=quote)",
            re.IGNORECASE | re.DOTALL,
        )
        return pattern.sub(
            lambda match: (
                f"{match.group('prefix')}{match.group('quote')}"
                f"{self._rewrite_url(match.group('url'), base_url, resource_map)}"
                f"{match.group('quote')}"
            ),
            content,
        )

    def _rewrite_with_regex_fallback(
        self,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> str:
        """Alias kept for backwards compatibility with the original scripts."""
        rewritten = self._rewrite_with_regex(content, base_url, resource_map)
        if not self.config.sanitize_html:
            return rewritten
        rewritten = re.sub(
            r"\s+on[a-z]+\s*=\s*([\"']).*?\1",
            "",
            rewritten,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return re.sub(
            r"\b(href|src|action)\s*=\s*([\"'])\s*(?:javascript|vbscript):.*?\2",
            r'\1=""',
            rewritten,
            flags=re.IGNORECASE | re.DOTALL,
        )


# ===== Consolidated from converter/plugins.py =====


class ConverterPlugin:
    """Base class for optional converter extensions.

    Subclasses can override any hook. Hooks run in registration order. The
    default implementations leave data untouched.
    """

    def before_parse(self, data: bytes, config: ConverterConfig) -> bytes:
        """Transform MHTML bytes before parsing."""
        del config
        return data

    def process_resource(self, resource: Resource) -> Resource:
        """Transform an extracted resource before it is rewritten into HTML."""
        return resource

    def after_convert(self, output: bytes, metadata: dict[str, Any]) -> bytes:
        """Transform final output bytes after conversion and validation."""
        del metadata
        return output


# ===== Consolidated from converter/converter.py =====


@dataclass(slots=True)
class _ParsedDocument:
    """Internal aggregate of the selected MIME part and its companion assets."""

    main_resource: Resource
    resources: dict[str, Resource]
    metadata: dict[str, Any]


@dataclass(slots=True)
class _CSSRewriteState:
    """Mutable traversal state for recursive CSS rewriting and cycle detection."""

    resource_map: dict[str, Resource]
    fallback_base: str
    visiting: set[int] = field(default_factory=set)
    processed: set[int] = field(default_factory=set)


class MHTMLConverter:
    """Coordinate MIME parsing, resource processing, rewriting, and output."""

    def __init__(self, config: ConverterConfig | None = None) -> None:
        """Create a converter and its stateful cache/resource services."""
        self.config = config or ConverterConfig()
        self.logger = ConverterLogger(self.config)
        self.parser = MHTMLParser(self.config)
        self.resource_manager = ResourceManager(self.config)
        self.html_processor = HTMLProcessor(self.config)
        self.css_processor = CSSProcessor(self.config)
        self.robust_parser = RobustMHTMLParser(self.config)

    def __enter__(self) -> Self:
        """Allow use as a context manager for timely logger cleanup."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Release this converter's optional Loguru file sink."""
        self.close()

    def close(self) -> None:
        """Close this converter's optional logging sink."""
        self.logger.close()

    def convert(
        self,
        mhtml_data: bytes | str,
        base_url: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        """Convert in-memory MHTML data to HTML bytes and a metadata dictionary.

        The parser retains MIME part payloads in memory. ``max_input_size`` and
        ``max_resource_size`` provide predictable bounds; use ``convert_stream``
        or ``convert_file`` to write the final output incrementally.
        """
        callback = progress_callback if progress_callback is not None else self.config.progress_callback
        previous_callback = self.logger.progress_callback
        self.logger.set_progress_callback(callback)
        try:
            return self._convert(mhtml_data, base_url)
        finally:
            self.logger.set_progress_callback(previous_callback)

    def _convert(
        self,
        mhtml_data: bytes | str,
        base_url: str | None,
    ) -> tuple[bytes, dict[str, Any]]:
        raw_data = mhtml_data.encode(self.config.encoding) if isinstance(mhtml_data, str) else mhtml_data
        if len(raw_data) > self.config.max_input_size:
            msg = f"MHTML input exceeds the {self.config.max_input_size}-byte limit"
            raise ValueError(msg)

        self.logger.info("Starting MHTML conversion")
        self.logger.debug(f"Configuration: {self.config.to_dict()}")
        self.logger.log_progress("Parsing MHTML", 0.1)
        prepared_data = raw_data
        parse_error: Exception | None = None
        main_resource: Resource | None = None
        resources: dict[str, Resource] = {}
        metadata: dict[str, Any] = {}
        try:
            prepared_data = self._run_before_parse_plugins(raw_data)
            main_resource, resources, metadata = self.parser.parse_mhtml(prepared_data)
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.logger.exception("MHTML parsing or input plugin failed")
            parse_error = error

        if main_resource is None or main_resource.resource_type not in {
            ResourceType.HTML,
            ResourceType.CSS,
        }:
            # The stdlib MIME parser found nothing presentable; the
            # state-machine parser survives mislabeled or broken archives.
            try:
                robust_main, robust_resources, robust_metadata = self.robust_parser.parse_mhtml(prepared_data)
            except Exception as robust_error:  # pylint: disable=broad-exception-caught
                if parse_error is None:
                    parse_error = robust_error
            else:
                if robust_main.resource_type == ResourceType.HTML:
                    main_resource, resources, metadata = (
                        robust_main,
                        robust_resources,
                        robust_metadata,
                    )
                    parse_error = None

        if main_resource is None:
            if not self.config.error_recovery:
                raise (parse_error if parse_error is not None else ValueError("Invalid MHTML data"))
            return self._fallback_conversion(
                raw_data,
                parse_error if parse_error is not None else ValueError("Invalid MHTML data"),
            )

        parsed = _ParsedDocument(main_resource, resources, metadata)
        return self._convert_parsed(len(raw_data), parsed, base_url)

    def _convert_parsed(
        self,
        input_size: int,
        parsed: _ParsedDocument,
        base_url: str | None,
    ) -> tuple[bytes, dict[str, Any]]:
        main_resource = parsed.main_resource
        resolved_base = self._resolve_base_url(base_url, main_resource.url)
        resource_map = self._normalize_resource_map(parsed.resources, resolved_base)
        self.logger.log_progress("Processing resources", 0.3)

        main_content = self._resource_text(main_resource)
        assets = self._extract_main_asset_urls(main_resource, main_content)
        self._fetch_missing_resources(assets, resolved_base, resource_map)
        self._fetch_nested_css_resources(main_resource, resource_map, resolved_base)
        self._apply_resource_plugins(resource_map)
        self._rewrite_css_resources(resource_map, resolved_base)
        self._rewrite_frame_resources(assets, resolved_base, resource_map, {id(main_resource)})
        unfound_resources = self._collect_unfound_resources(assets, resolved_base, resource_map)
        conversion_anomalies = parsed.metadata.get("anomalies", [])
        if conversion_anomalies:
            self.logger.warning("Anomalies recovered from: {}", conversion_anomalies)

        self.logger.log_progress("Rewriting main content", 0.6)
        output = self._render_main_resource(
            main_resource,
            main_content,
            resolved_base,
            resource_map,
        )
        output = self._run_after_convert_plugins(output, parsed.metadata)
        validation = self._validate_output(output, main_resource.resource_type)
        page_title, page_favicons = self._extract_page_details(output, main_resource.resource_type)
        self.logger.log_progress("Validating and finalizing output", 0.9)

        metadata: dict[str, Any] = {
            "original_size": input_size,
            "output_size": len(output),
            "resource_count": len(self._unique_resources(resource_map.values())),
            "main_resource_type": main_resource.resource_type.value,
            "content_type": main_resource.content_type,
            "profile": self.config.profile.value,
            "metadata": parsed.metadata,
            "anomalies": conversion_anomalies,
            "unfound_resources": unfound_resources,
            "title": page_title,
            "favicons": page_favicons,
            "config": self.config.to_dict(),
            "validation": validation,
            "output_format": self.config.output_format,
        }
        self.logger.log_progress("Conversion complete", 1.0)
        self.logger.info("MHTML conversion completed successfully")
        return output, metadata

    def _resolve_base_url(self, base_url: str | None, resource_url: str) -> str:
        """Use a caller URL or the main part's URL when it is a valid HTTP base."""
        if base_url:
            return base_url
        if resource_url and urlsplit(resource_url).scheme.lower() in {"http", "https"}:
            return resource_url
        return "http://localhost/"

    def _render_main_resource(
        self,
        main_resource: Resource,
        content: str,
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> bytes:
        """Rewrite a main HTML/CSS part and encode its output."""
        if main_resource.resource_type == ResourceType.HTML:
            rewritten = self.html_processor.rewrite_html(content, base_url, resource_map)
            if self.config.minify_html or self.config.compress_output:
                rewritten = self.html_processor.minify_html(rewritten)
            return rewritten.encode(self.config.encoding, errors="replace")
        if main_resource.resource_type == ResourceType.CSS:
            rewritten = self.css_processor.rewrite_css(content, base_url, resource_map)
            return rewritten.encode(self.config.encoding, errors="replace")
        if isinstance(main_resource.content, bytes):
            return main_resource.content
        return main_resource.content.encode(self.config.encoding, errors="replace")

    def convert_file(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        base_url: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """Convert a file, writing ``<input-stem>.html`` beside it by default."""
        source = Path(input_path)
        if not source.is_file():
            msg = f"Input MHTML file does not exist: {source}"
            raise FileNotFoundError(msg)
        if source.stat().st_size > self.config.max_input_size:
            msg = f"MHTML input exceeds the {self.config.max_input_size}-byte limit"
            raise ValueError(msg)
        destination = Path(output_path) if output_path is not None else source.with_suffix(".html")
        if source.resolve() == destination.resolve():
            msg = "Output path must not overwrite the input MHTML file"
            raise ValueError(msg)

        with source.open("rb") as input_file:
            result, metadata = self.convert(
                input_file.read(self.config.max_input_size + 1),
                base_url=base_url,
                progress_callback=progress_callback,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output_file:
            for chunk in self._chunks(result):
                output_file.write(chunk)
        metadata["output_path"] = str(destination)
        return destination, metadata

    def convert_stream(
        self,
        source: BinaryIO,
        destination: BinaryIO,
        base_url: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Read an input stream within limits and write output in bounded chunks.

        The MIME parser needs a complete message for reliable related-part
        lookup, so input is buffered up to ``max_input_size``; output is written
        incrementally according to ``chunk_size``.
        """
        input_buffer = bytearray()
        while True:
            next_chunk = source.read(self.config.chunk_size)
            if not next_chunk:
                break
            input_buffer.extend(next_chunk)
            if len(input_buffer) > self.config.max_input_size:
                msg = f"MHTML input exceeds the {self.config.max_input_size}-byte limit"
                raise ValueError(msg)
        result, metadata = self.convert(
            bytes(input_buffer),
            base_url=base_url,
            progress_callback=progress_callback,
        )
        destination.writelines(self._chunks(result))
        return metadata

    def iter_convert(
        self,
        mhtml_data: bytes | str,
        base_url: str | None = None,
    ) -> Iterator[bytes]:
        """Yield the converted output in ``chunk_size`` byte pieces."""
        output, _metadata = self.convert(mhtml_data, base_url=base_url)
        yield from self._chunks(output)

    def convert_parts(
        self,
        main_resource: Resource,
        resources: dict[str, Resource],
        base_url: str | None = None,
        metadata: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        """Convert already-parsed parts (see :func:`parse`) to HTML bytes."""
        callback = progress_callback if progress_callback is not None else self.config.progress_callback
        previous_callback = self.logger.progress_callback
        self.logger.set_progress_callback(callback)
        try:
            parsed = _ParsedDocument(
                main_resource,
                resources,
                metadata or {"content_type": main_resource.content_type},
            )
            return self._convert_parsed(0, parsed, base_url)
        finally:
            self.logger.set_progress_callback(previous_callback)

    def _collect_unfound_resources(
        self,
        assets: Iterable[str],
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> list[str]:
        """Report referenced assets neither the archive nor fetches provided."""
        unfound = sorted(
            url
            for url in (
                self.resource_manager.normalize_url(asset, base_url)
                for asset in assets
                if not asset.lower().startswith(("data:", "cid:"))
            )
            if url not in resource_map
        )
        if unfound:
            self.logger.warning(
                "References not found in the archive (left as-is): {}",
                ", ".join(unfound),
            )
        return unfound

    def _rewrite_frame_resources(
        self,
        assets: Iterable[str],
        base_url: str,
        resource_map: dict[str, Resource],
        seen: set[int],
    ) -> None:
        """Recursively rewrite embedded HTML documents (iframes, objects).

        Each frame resource is rewritten as a full sub-document — its own
        assets inlined, its own CSP and charset metadata — so the main
        document can embed it as a self-contained ``data:`` URI, mirroring
        the mhtml-to-html library's frame handling.
        """
        if self.config.resource_handling not in {"inline", "mixed"}:
            return
        for asset in assets:
            normalized = self.resource_manager.normalize_url(asset, base_url)
            resource = resource_map.get(normalized)
            if (
                resource is None
                or resource.resource_type != ResourceType.HTML
                or id(resource) in seen
                or resource.decode_failed
            ):
                continue
            seen.add(id(resource))
            frame_base = resource.url or base_url
            content = self._resource_text(resource)
            sub_assets = self.html_processor.extract_resource_urls(content)
            self._rewrite_frame_resources(sub_assets, frame_base, resource_map, seen)
            rewritten = self.html_processor.rewrite_html(content, frame_base, resource_map)
            resource.content = rewritten.encode(self.config.encoding, errors="replace")
            resource.size = len(resource.content)

    def _extract_page_details(
        self,
        output: bytes,
        resource_type: ResourceType,
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Extract the document title and favicon links from rendered output."""
        if resource_type != ResourceType.HTML or not self.config.metadata_extraction:
            return None, []
        text = self.parser.decode_text(output, self.config.encoding)
        try:
            tree = self.html_processor.parse_basic_html(text)
        except (ValueError, etree.LxmlError):
            return None, []
        title = tree.findtext(".//title") or None
        favicons: list[dict[str, Any]] = []
        for element in tree.iter("link"):
            rel_tokens = (element.get("rel") or "").lower().split()
            if "icon" not in rel_tokens:
                continue
            favicons.append({
                "href": element.get("href", ""),
                "rel": " ".join(rel_tokens),
                "type": element.get("type"),
                "sizes": element.get("sizes"),
            })
        return title, favicons

    def _chunks(self, content: bytes) -> Iterator[bytes]:
        for offset in range(0, len(content), self.config.chunk_size):
            yield content[offset : offset + self.config.chunk_size]

    def _run_before_parse_plugins(self, data: bytes) -> bytes:
        """Run each plugin's input hook in the order it was registered."""
        for plugin in self.config.plugins:
            data = plugin.before_parse(data, self.config)
            if not isinstance(data, bytes):
                msg = "plugin before_parse hooks must return bytes"
                raise TypeError(msg)
        return data

    def _run_after_convert_plugins(
        self,
        output: bytes,
        metadata: dict[str, Any],
    ) -> bytes:
        """Run each plugin's output hook in registration order."""
        for plugin in self.config.plugins:
            output = plugin.after_convert(output, metadata)
            if not isinstance(output, bytes):
                msg = "plugin after_convert hooks must return bytes"
                raise TypeError(msg)
        return output

    def _normalize_resource_map(
        self,
        resources: dict[str, Resource],
        base_url: str,
    ) -> dict[str, Resource]:
        """Canonicalize MIME resource keys and apply custom resource handlers."""
        normalized: dict[str, Resource] = {}
        transformed_by_id: dict[int, Resource] = {}
        for key, resource in resources.items():
            transformed = transformed_by_id.get(id(resource))
            if transformed is None:
                transformed = self.resource_manager.transform_resource(resource)
                transformed_by_id[id(resource)] = transformed
            if key.lower().startswith(("cid:", "data:")):
                normalized[key] = transformed
            else:
                normalized[self.resource_manager.normalize_url(key, base_url)] = transformed
            if resource.url:
                if resource.url.lower().startswith(("cid:", "data:")):
                    normalized[resource.url] = transformed
                else:
                    normalized[self.resource_manager.normalize_url(resource.url, base_url)] = transformed
        return normalized

    def _extract_main_asset_urls(
        self,
        main_resource: Resource,
        content: str,
    ) -> set[str]:
        """Discover fetchable HTML/CSS assets, excluding ordinary anchor links."""
        if main_resource.resource_type == ResourceType.HTML:
            return self.html_processor.extract_resource_urls(content)
        if main_resource.resource_type == ResourceType.CSS:
            return self.css_processor.extract_urls(content)
        return set()

    def _fetch_missing_resources(
        self,
        urls: set[str],
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> None:
        """Fetch external assets only when not already present in the MIME map."""
        missing = [
            self.resource_manager.normalize_url(url, base_url)
            for url in urls
            if self.resource_manager.normalize_url(url, base_url) not in resource_map
        ]
        fetched = self.resource_manager.process_resources_parallel(missing, base_url)
        resource_map.update((url, resource) for url, resource in fetched.items() if resource is not None)

    def _fetch_nested_css_resources(
        self,
        main_resource: Resource,
        resource_map: dict[str, Resource],
        base_url: str,
    ) -> None:
        """Resolve nested CSS URLs/imports iteratively up to the configured depth."""
        css_resources = [
            resource
            for resource in self._unique_resources(resource_map.values())
            if resource.resource_type == ResourceType.CSS
        ]
        if main_resource.resource_type == ResourceType.CSS:
            css_resources.append(main_resource)

        for _ in range(self.config.max_import_depth):
            pending = self._find_missing_css_urls(css_resources, resource_map, base_url)
            if not pending:
                return
            added = self._fetch_resource_batch(pending, base_url, resource_map)
            if not added:
                return
            css_resources.extend(resource for resource in added if resource.resource_type == ResourceType.CSS)

    def _find_missing_css_urls(
        self,
        css_resources: Iterable[Resource],
        resource_map: dict[str, Resource],
        fallback_base: str,
    ) -> set[str]:
        """Collect uncached CSS URLs with each stylesheet's own base URL."""
        pending: set[str] = set()
        for resource in css_resources:
            css_base = resource.url or fallback_base
            for raw_url in self.css_processor.extract_urls(self._resource_text(resource)):
                normalized = self.resource_manager.normalize_url(raw_url, css_base)
                if normalized not in resource_map and not normalized.lower().startswith(("data:", "cid:")):
                    pending.add(normalized)
        return pending

    def _fetch_resource_batch(
        self,
        urls: set[str],
        base_url: str,
        resource_map: dict[str, Resource],
    ) -> list[Resource]:
        """Fetch unresolved CSS dependencies and add successful responses to the map."""
        fetched = self.resource_manager.process_resources_parallel(sorted(urls), base_url)
        added = [resource for resource in fetched.values() if resource is not None]
        resource_map.update((url, resource) for url, resource in fetched.items() if resource is not None)
        return added

    def _apply_resource_plugins(self, resource_map: dict[str, Resource]) -> None:
        """Apply resource hooks once per unique MIME resource and preserve aliases."""
        transformed: dict[int, Resource] = {}
        for key, resource in list(resource_map.items()):
            replacement = transformed.get(id(resource))
            if replacement is None:
                replacement = resource
                for plugin in self.config.plugins:
                    replacement = plugin.process_resource(replacement)
                    if not isinstance(replacement, Resource):
                        msg = "plugin process_resource hooks must return Resource"
                        raise TypeError(msg)
                transformed[id(resource)] = replacement
            resource_map[key] = replacement

    def _rewrite_css_resources(
        self,
        resource_map: dict[str, Resource],
        base_url: str,
    ) -> None:
        """Rewrite CSS assets recursively, guarding cycles and import depth."""
        state = _CSSRewriteState(resource_map, base_url)
        for resource in self._unique_resources(resource_map.values()):
            if resource.resource_type == ResourceType.CSS:
                self._rewrite_css_resource(resource, state, 0)

    def _rewrite_css_resource(
        self,
        resource: Resource,
        state: _CSSRewriteState,
        depth: int,
    ) -> None:
        """Rewrite one stylesheet after rewriting mapped CSS dependencies."""
        identity = id(resource)
        if identity in state.processed or identity in state.visiting:
            return
        state.visiting.add(identity)
        css_text = self._resource_text(resource)
        css_base = resource.url or state.fallback_base
        if depth < self.config.max_import_depth:
            self._rewrite_css_dependencies(css_text, state, css_base, depth)

        safe_map = {key: value for key, value in state.resource_map.items() if id(value) not in state.visiting}
        rewritten = self.css_processor.rewrite_css(
            css_text,
            css_base,
            safe_map,
            depth=depth,
        )
        resource.content = rewritten.encode(self.config.encoding, errors="replace")
        resource.size = len(resource.content)
        state.visiting.remove(identity)
        state.processed.add(identity)

    def _rewrite_css_dependencies(
        self,
        css_text: str,
        state: _CSSRewriteState,
        css_base: str,
        depth: int,
    ) -> None:
        """Rewrite all mapped stylesheet dependencies before their parent."""
        for raw_url in self.css_processor.extract_urls(css_text):
            normalized = self.resource_manager.normalize_url(raw_url, css_base)
            dependency = state.resource_map.get(normalized)
            if dependency and dependency.resource_type == ResourceType.CSS:
                self._rewrite_css_resource(dependency, state, depth + 1)

    def _resource_text(self, resource: Resource) -> str:
        """Decode textual MIME content using its part-level charset and BOM."""
        content_type = resource.headers.get("Content-Type", resource.content_type)
        return self.parser.decode_text(resource.content, content_type)

    def _validate_output(
        self,
        output: bytes,
        resource_type: ResourceType,
    ) -> dict[str, Any]:
        """Validate generated HTML/CSS and return actionable diagnostics."""
        if not self.config.validation:
            return {"enabled": False, "valid": True, "errors": []}
        if resource_type == ResourceType.HTML:
            text = self.parser.decode_text(output, self.config.encoding)
            errors = self.html_processor.validate_html(text)
        elif resource_type == ResourceType.CSS:
            text = self.parser.decode_text(output, self.config.encoding)
            rules = self.css_processor.parse_css(text)
            errors = [str(rule) for rule in rules if getattr(rule, "type", "") == "error"]
        else:
            errors = []
        return {"enabled": True, "valid": not errors, "errors": errors}

    def _fallback_conversion(
        self,
        data: bytes,
        error: Exception,
    ) -> tuple[bytes, dict[str, Any]]:
        """Recover invalid MHTML into safe HTML rather than returning a broken MIME file."""
        text = data.decode(self.config.encoding, errors="replace")
        start = text.lower().find("<html")
        end = text.lower().rfind("</html>")
        if 0 <= start <= end:
            recovered = text[start : end + len("</html>")]
            safe_html = self.html_processor.rewrite_html(
                recovered,
                "http://localhost/",
                {},
            )
            output = safe_html.encode(self.config.encoding, errors="replace")
        else:
            safe_text = html_stdlib.escape(text, quote=False)
            output = (
                f'<!doctype html><html><head><meta charset="utf-8"></head><body><pre>{safe_text}</pre></body></html>'
            ).encode()
        validation = self._validate_output(output, ResourceType.HTML)
        metadata = {
            "original_size": len(data),
            "output_size": len(output),
            "resource_count": 0,
            "main_resource_type": "other",
            "content_type": "text/html",
            "profile": self.config.profile.value,
            "metadata": {},
            "config": self.config.to_dict(),
            "validation": validation,
            "output_format": self.config.output_format,
            "error": str(error),
        }
        self.logger.warning(f"Returning recovered HTML after parse error: {error}")
        self.logger.log_progress("Conversion recovered", 1.0)
        return output, metadata

    @staticmethod
    def _unique_resources(resources: Iterable[Resource]) -> list[Resource]:
        """Return resources once even when the map has URL and CID aliases."""
        unique: list[Resource] = []
        seen: set[int] = set()
        for resource in resources:
            identity = id(resource)
            if identity not in seen:
                unique.append(resource)
                seen.add(identity)
        return unique


# ------------------------------ mhtml-to-html compatible API ------------------------------

MHTML_EXTENSIONS: tuple[str, str] = (".mht", ".mhtml")


@dataclass
class Favicon:
    """A favicon link collected from the converted document."""

    href: str
    rel: str | None = None
    media: str | None = None
    type: str | None = None
    sizes: str | None = None


@dataclass
class PageData:
    """The result of :func:`convert` (the mhtml-to-html library shape)."""

    data: str
    title: str | None = None
    unfound_resources: list[str] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)
    favicons: list[Favicon] = field(default_factory=list)


def parse(data: str | bytes | bytearray | Path) -> Mhtml:
    """Parse MHTML data (bytes, string or file path) into an :class:`Mhtml`.

    This is the ``mhtml_to_html``-style entry point: it always uses the
    state-machine parser, which tolerates mislabeled or malformed archives.
    """
    if isinstance(data, Path):
        data = data.read_bytes()
    return RobustMHTMLParser().parse_to_mhtml(data)


def convert(
    mhtml: str | bytes | bytearray | Path | Mhtml,
    config: ConverterConfig | None = None,
) -> PageData:
    """Convert an MHTML archive (or a parsed :class:`Mhtml`) into one HTML page.

    Returns a :class:`PageData` with the HTML, the title, the references that
    resolved nowhere and the anomalies recovered from.  Raises
    :class:`IndexPageNotFoundError`-equivalent ``ValueError`` when the archive
    holds nothing that can be presented as a page.
    """
    config = config or ConverterConfig()
    with MHTMLConverter(config) as converter:
        if isinstance(mhtml, Mhtml):
            main_resource, resources, parsed_metadata = converter.robust_parser.mhtml_to_parts(mhtml)
            output, metadata = converter.convert_parts(main_resource, resources, metadata=parsed_metadata)
        else:
            if isinstance(mhtml, Path):
                mhtml = mhtml.read_bytes()
            elif isinstance(mhtml, bytearray):
                mhtml = bytes(mhtml)
            output, metadata = converter.convert(mhtml)
    return PageData(
        data=decode_string(output, config.encoding),
        title=metadata.get("title"),
        unfound_resources=list(metadata.get("unfound_resources", [])),
        anomalies=[Anomaly(**item) for item in metadata.get("anomalies", [])],
        favicons=[
            Favicon(
                href=item.get("href", ""),
                rel=item.get("rel"),
                media=item.get("media"),
                type=item.get("type"),
                sizes=item.get("sizes"),
            )
            for item in metadata.get("favicons", [])
        ],
    )


def convert_file(
    input_path: str | Path,
    output_path: str | Path | None = None,
    config: ConverterConfig | None = None,
) -> Path:
    """Convert one archive and return the path of the written HTML file."""
    config = config or ConverterConfig()
    with MHTMLConverter(config) as converter:
        destination, _metadata = converter.convert_file(input_path, output_path=output_path)
    return destination


# ------------------------------ Command-line interface ------------------------------

USAGE_EXIT: int = 1
CONVERSION_FAILURE_EXIT: int = 2


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
            logger.error("no matches for pattern: {}", raw)
            continue
        logger.error("input not found: {}", raw)
    return result


def _default_output_path(input_path: Path) -> Path:
    """Return the sibling ``.html`` path for an input archive."""
    name = input_path.name
    if "." in name:
        stem = name.rsplit(".", 1)[0]
        candidate = input_path.with_name(stem + ".html")
    else:
        candidate = input_path.with_name(name + ".html")
    if candidate.suffix != ".html":
        candidate = candidate.with_name(candidate.name + ".html")
    return candidate


def _resolve_log_level(arguments: argparse.Namespace) -> str:
    """Pick the log level from --log-level, -v and -q."""
    if arguments.log_level is not None:
        return str(arguments.log_level).upper()
    if arguments.verbose:
        return "DEBUG"
    if arguments.quiet:
        return "ERROR"
    return "INFO"


def _build_argument_parser() -> argparse.ArgumentParser:
    """Create the standalone script's command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Convert MHTML/MHT archives into single self-contained HTML files (consolidation of mhtml.py and mhtml_to_html.py)."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        metavar="INPUT",
        help="input .mht/.mhtml files, directories (searched recursively) or glob patterns; defaults to a recursive search of the current directory",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output HTML path (single input only; default: beside the input with a .html suffix)",
    )
    parser.add_argument(
        "--profile",
        default=ConversionProfile.STANDARD.value,
        help="Conversion preset (minimal/standard/full/secure/optimized)",
    )
    parser.add_argument(
        "--fetch-external",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Fetch referenced public HTTP(S) assets missing from the archive (SSRF-guarded)",
    )
    parser.add_argument(
        "--enable-scripts",
        dest="enable_scripts",
        action="store_true",
        default=True,
        help="Keep scripts and event handlers (--no-scripts strips them)",
    )
    parser.add_argument("--no-scripts", dest="enable_scripts", action="store_false")
    parser.add_argument(
        "--csp",
        action=argparse.BooleanOptionalAction,
        default=True,
        dest="inject_csp",
        help="Inject a Content-Security-Policy meta tag into the output",
    )
    parser.add_argument(
        "--allow-domain",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="Restrict external fetches to this domain; may be repeated",
    )
    parser.add_argument(
        "--block-domain",
        action="append",
        default=[],
        metavar="DOMAIN",
        help="Block external fetches to this domain; may be repeated",
    )
    parser.add_argument(
        "--allow-private-network",
        action="store_true",
        default=False,
        help="Allow external fetches from loopback/private addresses (blocked by default)",
    )
    parser.add_argument(
        "--resource-handling",
        choices=["inline", "external", "mixed"],
        default=None,
        help="Override the profile's resource handling strategy",
    )
    parser.add_argument("--minify", action="store_true", default=False, help="Minify HTML and CSS output")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Connect/read timeout for external fetches (seconds)",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=None,
        metavar="BYTES",
        help="Maximum size of externally fetched resources (bytes)",
    )
    parser.add_argument(
        "--no-clobber", action="store_true", default=False, help="Never overwrite an existing output file"
    )
    parser.add_argument(
        "--fail-fast", action="store_true", default=False, help="Abort the batch on the first conversion failure"
    )
    parser.add_argument(
        "--progress", action="store_true", default=False, help="Print conversion progress to standard error"
    )
    parser.add_argument(
        "--log-level",
        choices=["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="Set the minimum log level (overrides -v/-q)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", default=False, help="Verbose (DEBUG) logging")
    parser.add_argument("-q", "--quiet", action="store_true", default=False, help="Suppress per-file logs")
    parser.add_argument(
        "--version", action="version", version=f"mhtml_converter {__version__}", help="Show the version number and exit"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI: convert every input archive to a self-contained HTML file."""
    parser = _build_argument_parser()
    arguments = parser.parse_args(argv)

    log_level = _resolve_log_level(arguments)
    logger.remove()
    logger.add(sys.stderr, level=log_level, format="<level>{level}</level>: {message}")

    try:
        config = ConverterConfig(
            profile=ConversionProfile(arguments.profile or ConversionProfile.STANDARD.value),
            allowed_domains=list(arguments.allow_domain or []),
            blocked_domains=list(arguments.block_domain or []),
            log_level=log_level,
        )
    except ValueError as error:
        logger.error("invalid configuration: {}", error)
        return USAGE_EXIT

    config.fetch_external = bool(arguments.fetch_external)
    if not arguments.enable_scripts:
        config.include_scripts = False
        config.remove_scripts = True
    config.inject_csp = bool(arguments.inject_csp)
    if arguments.allow_private_network:
        config.allow_private_hosts = True
    if arguments.timeout is not None:
        config.fetch_timeout = float(arguments.timeout)
    if arguments.max_size is not None:
        config.max_resource_size = int(arguments.max_size)
    if arguments.resource_handling is not None:
        config.resource_handling = str(arguments.resource_handling)
    if arguments.minify:
        config.minify_html = True
        config.minify_css = True
    if arguments.progress:
        config.progress_callback = lambda message, value: print(f"{value:6.1%} {message}", file=sys.stderr)

    if arguments.inputs:
        inputs = _discover_inputs(list(arguments.inputs))
    else:
        inputs = _discover_inputs(["."])
    if not inputs:
        if arguments.inputs:
            logger.error("no MHTML files found")
            return USAGE_EXIT
        logger.info("no .mht/.mhtml files found in the current directory")
        return 0

    if arguments.output and len(inputs) > 1:
        logger.error("--output can only be used with a single input file")
        return USAGE_EXIT

    failures = 0
    successes = 0
    with MHTMLConverter(config) as converter:
        for input_path in inputs:
            if arguments.output and len(inputs) == 1:
                output_path = Path(arguments.output)
                if output_path.suffix != ".html":
                    output_path = output_path.with_name(output_path.name + ".html")
            else:
                output_path = _default_output_path(input_path)
            try:
                if output_path.resolve() == input_path.resolve():
                    msg = f"output path would overwrite the input archive: {output_path}"
                    raise ValueError(msg)
                if arguments.no_clobber and output_path.exists():
                    msg = f"output file already exists: {output_path}"
                    raise ValueError(msg)
                _destination, metadata = converter.convert_file(input_path, output_path=output_path)
            except Exception as error:  # pylint: disable=broad-exception-caught
                # one bad archive must not abort a batch
                failures += 1
                logger.error("error processing {}: {}", input_path, error)
                if arguments.verbose:
                    import traceback

                    traceback.print_exc()
                if arguments.fail_fast:
                    break
            else:
                successes += 1
                logger.info(
                    "converted {} -> {} ({} bytes)",
                    input_path,
                    output_path,
                    metadata["output_size"],
                )

    if not arguments.quiet:
        logger.info("{} file(s) converted, {} failure(s)", successes, failures)
    if failures:
        return CONVERSION_FAILURE_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
