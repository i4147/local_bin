#!/data/data/com.termux/files/usr/bin/env python
"""
extract_assets.py — Recursively extract CSS/JS/data-URIs and other referenced
assets from HTML files, with optional HTML rewriting.
Core (stdlib-only):
  - Inline <style>/<script> extraction
  - Remote <script src> / <link rel=stylesheet> download (-r)
  - data: URI decoding (-b)
  - srcset parsing, @import, CSS url(), @font-face
  - HTML rewriting with asset path substitution (--rewrite)
  - Relative-reference resolution (--resolve-relative)
  - Content-addressing, dedupe, global remote cache, flat/mirror layouts
  - SSRF protection, retries, rate limiting, per-host concurrency, max-bytes
  - Subresource-Integrity (SRI) verification, charset auto-detection
  - JSON-LD / importmap / noscript / template / service-worker capture
  - Manifest (JSON), checksums, JSON/CSV reports, zip archive
Optional 3rd-party (auto-detected, silently skipped if absent):
  - charset_normalizer / chardet  → robust encoding detection
  - tomli / tomllib / PyYAML      → config files
  - tqdm                          → progress bar
  - brotli / zstandard            → br/zstd content-encoding
  - rjsmin / rcssmin              → --minify
  - jsbeautifier / cssbeautifier  → --beautify
  - pathspec                      → --respect-gitignore
Run `python extract_assets.py --help-optional` for the 3rd-party status.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import csv
import gzip
import hashlib
import ipaddress
import json
import mimetypes
import re
import shutil
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from dh import atomic_write
from loguru import logger

try:
    import tomllib as _toml
except ImportError:  # pragma: no cover
    try:
        import tomli as _toml
    except ImportError:
        _toml = None
try:
    import yaml as _yaml
except ImportError:
    _yaml = None
try:
    import charset_normalizer as _cn

    _CN_API = "charset_normalizer"
except ImportError:  # pragma: no cover
    _cn = None
    try:
        import chardet as _cn

        _CN_API = "chardet"
    except ImportError:
        _CN_API = None
try:
    from tqdm import tqdm as _tqdm
except ImportError:
    _tqdm = None
try:
    import brotli as _brotli
except ImportError:
    _brotli = None
try:
    import zstandard as _zstd
except ImportError:
    _zstd = None
try:
    from rcssmin import cssmin as _cssmin
    from rjsmin import jsmin as _jsmin
except ImportError:
    _jsmin = _cssmin = None
try:
    import cssbeautifier as _cssbeautifier
    import jsbeautifier as _jsbeautifier
except ImportError:
    _jsbeautifier = _cssbeautifier = None
try:
    import pathspec as _pathspec
except ImportError:
    _pathspec = None
__version__: str = "2.0.0"
log = logger
_DATA_URI_RE = re.compile(r"^data:(?P<meta>[^,]*),(?P<body>.*)$", re.DOTALL)
_CSS_URL_RE = re.compile(r"""url\(\s*(?P<q>['"]?)(?P<url>[^)'"]+?)(?P=q)\s*\)""", re.IGNORECASE | re.DOTALL)
_CSS_IMPORT_RE = re.compile(r"""@import\s+(?:url\(\s*)?(?P<q>['"]?)(?P<url>[^)'";]+)(?P=q)""", re.IGNORECASE)
_SW_REGISTER_RE = re.compile(r"""navigator\s*\.\s*serviceWorker\s*\.\s*register\s*\(\s*['"](?P<url>[^'"]+)['"]""")
_SOURCEMAP_RE = re.compile(r"""//[#@]\s*sourceMappingURL=(?P<url>\S+)""")
_SRCSET_SPLIT_RE = re.compile(r",\s*")
_HTML_EXTS: tuple[str, str, str, str] = (".html", ".htm", ".xhtml", ".shtml")
_JS_LIKE_TYPES: set[str] = {
    "",
    "text/javascript",
    "application/javascript",
    "module",
    "text/ecmascript",
    "application/ecmascript",
    "text/jscript",
}
_NON_JS_TYPES: set[str] = {
    "application/ld+json",
    "application/json",
    "importmap",
    "text/template",
    "text/x-template",
    "text/html",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _short_hash(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return _sha256(data)[:16]


def _ext_for_mime(mime: str | None, fallback: str = ".bin") -> str:
    if not mime:
        return fallback
    manual = {
        "text/javascript": ".js",
        "application/javascript": ".js",
        "text/css": ".css",
        "image/svg+xml": ".svg",
        "image/jpeg": ".jpg",
        "font/woff2": ".woff2",
        "font/woff": ".woff",
        "application/font-woff": ".woff",
        "application/font-woff2": ".woff2",
    }
    mime = mime.split(";")[0].strip().lower()
    return manual.get(mime) or mimetypes.guess_extension(mime) or fallback


def parse_data_uri(uri: str) -> tuple[str, bytes] | None:
    """Return (mime, bytes) for a valid data: URI, else None."""
    m = _DATA_URI_RE.match(uri)
    if not m:
        return None
    meta, body = m.group("meta"), m.group("body")
    parts = [p.strip() for p in meta.split(";") if p.strip()]
    is_b64 = any(p.lower() == "base64" for p in parts)
    mime = parts[0] if parts and "/" in parts[0] else "text/plain"
    try:
        data = base64.b64decode(body, validate=False) if is_b64 else urllib.parse.unquote_to_bytes(body)
    except Exception:
        return None
    return mime, data


def find_attr_value_span(tag_text: str, attr_name: str) -> tuple[int, int, str] | None:
    """Return (start, end, quote) of attr_name's value inside tag_text."""
    pat = re.compile(
        rf"(?<![A-Za-z0-9_-]){re.escape(attr_name)}\s*=\s*"
        r'(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
        re.IGNORECASE,
    )
    m = pat.search(tag_text)
    if not m:
        return None
    for g, q in ((1, '"'), (2, "'"), (3, "")):
        if m.group(g) is not None:
            return m.start(g), m.end(g), q
    return None


def parse_srcset(value: str) -> list[tuple[str, str]]:
    """Split srcset value into (url, descriptor) pairs."""
    out: list[tuple[str, str]] = []
    for chunk in _SRCSET_SPLIT_RE.split(value):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(None, 1)
        out.append((parts[0], parts[1].strip() if len(parts) > 1 else ""))
    return out


def apply_edits(source: str, edits: Sequence[Edit]) -> str:
    """Splice text edits into source, non-overlapping, by offset."""
    ordered = sorted(edits, key=lambda e: e.start)
    parts: list[str] = []
    cursor = 0
    for e in ordered:
        if e.start < cursor:
            continue
        parts.append(source[cursor : e.start])
        parts.append(e.text)
        cursor = e.end
    parts.append(source[cursor:])
    return "".join(parts)


def rel_href(from_file: Path, to_file: Path) -> str:
    """POSIX-style relative path from one file to another."""
    src_parts = from_file.parent.resolve().parts
    dst_parts = to_file.resolve().parts
    common = 0
    for a, b in zip(src_parts, dst_parts):
        if a != b:
            break
        common += 1
    up = ("..",) * (len(src_parts) - common)
    rel_parts = up + dst_parts[common:]
    return Path(*rel_parts).as_posix() if rel_parts else "."


def decode_bytes(raw: bytes, declared: str | None) -> tuple[str, str]:
    """Best-effort HTML decoding. Returns (text, encoding)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace"), "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", "replace"), "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace"), "utf-16-be"
    if declared:
        try:
            return raw.decode(declared, "replace"), declared
        except LookupError:
            pass
    m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:8192], re.IGNORECASE)
    if m:
        cs = m.group(1).decode("ascii", "replace")
        try:
            return raw.decode(cs, "replace"), cs
        except LookupError:
            pass
    if _cn is not None:
        cn: Any = _cn
        try:
            if _CN_API == "charset_normalizer":
                r = cn.from_bytes(raw).best()
                if r and r.encoding:
                    return raw.decode(r.encoding, "replace"), r.encoding
            else:
                r = cn.detect(raw)
                enc = r.get("encoding") if isinstance(r, dict) else None
                if isinstance(enc, str) and enc:
                    return raw.decode(enc, "replace"), enc
        except Exception:
            pass
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def verify_sri(data: bytes, integrity: str) -> bool:
    """Return True if data matches any hash in an SRI integrity string."""
    for token in integrity.split():
        algo, _, b64 = token.partition("-")
        if algo not in ("sha256", "sha384", "sha512"):
            continue
        digest = hashlib.new(algo, data).digest()
        if base64.b64encode(digest).decode() == b64:
            return True
    return False


def _maybe_minify(kind: str, text: str) -> str:
    if kind == "css" and _cssmin:
        return _cssmin(text)  # type: ignore[misc]
    if kind == "js" and _jsmin:
        return _jsmin(text)  # type: ignore[misc]
    return text


def _maybe_beautify(kind: str, text: str) -> str:
    if kind == "css" and _cssbeautifier:
        return _cssbeautifier.beautify(text)
    if kind == "js" and _jsbeautifier:
        return _jsbeautifier.beautify(text)
    return text


@dataclass(slots=True)
class Edit:
    """Store start, end, text."""

    start: int
    end: int
    text: str


@dataclass(slots=True)
class InlineBlock:
    """Store kind, text, start, end, index and related state."""

    kind: str
    text: str
    start: int
    end: int
    index: int
    name_hint: str | None = None


@dataclass(slots=True)
class Ref:
    """Any outbound reference we might rewrite (remote or local)."""

    url: str
    kind: str
    tag: str
    start: int
    end: int
    quote: str
    attr: str
    integrity: str | None = None
    cross_origin: str | None = None
    referer: str | None = None
    saved_path: Path | None = None
    error: str | None = None
    sha256: str | None = None
    size: int | None = None
    mime: str | None = None


@dataclass(slots=True)
class DataUriHit:
    """Store uri, mime, payload, sha256, start and related state."""

    uri: str
    mime: str
    payload: bytes
    sha256: str
    start: int
    end: int
    quote: str
    saved_path: Path | None = None


@dataclass(slots=True)
class Options:
    """Store root, out, clean, dry_run, flat and related state."""

    root: Path
    out: Path
    clean: bool = False
    dry_run: bool = False
    flat: bool = False
    content_addressed: bool = False
    mirror_host: bool = False
    global_remote_cache: bool = False
    overwrite: bool = True
    skip_existing: bool = False
    incremental: bool = False
    atomic: bool = True
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    max_depth: int = 0
    follow_symlinks: bool = True
    respect_gitignore: bool = False
    exts: tuple[str, ...] = _HTML_EXTS
    no_inline_styles: bool = False
    no_inline_scripts: bool = False
    concat_style: bool = False
    concat_script: bool = False
    name_by_id: bool = False
    strip_comments: bool = False
    minify: bool = False
    beautify: bool = False
    extract_noscript: bool = False
    extract_template: bool = False
    extract_jsonld: bool = False
    skip_jsonld: bool = True
    extract_importmap: bool = False
    script_types: str = ""
    remote: bool = False
    allow_host: str = ""
    deny_private_ips: bool = False
    max_redirects: int = 10
    retries: int = 2
    rate: float = 0.0
    per_host: int = 0
    jobs: int = 8
    timeout: float = 15.0
    max_bytes: int = 50 * 1024 * 1024
    user_agent: str = f"html-extract/{__version__}"
    headers: list[str] = field(default_factory=list)
    cookie_file: Path | None = None
    auth: str = ""
    proxy: str = ""
    insecure: bool = False
    ca_bundle: str = ""
    sri_check: bool = False
    strip_integrity: bool = False
    referer_from_page: bool = True
    quiet_404: bool = True
    favicon: bool = False
    preload: bool = False
    media: bool = False
    webfonts: bool = False
    manifest_webmanifest: bool = False
    serviceworkers: bool = False
    iframes: bool = False
    iframes_follow: bool = False
    meta_images: bool = False
    all_refs: bool = False
    sourcemaps: bool = False
    css_imports: bool = False
    css_urls: bool = False
    extract_b64: bool = False
    data_min: int = 0
    data_max: int = 0
    data_keep_small: bool = False
    data_map: bool = False
    rewrite: bool = False
    rewrite_inplace: bool = False
    rewrite_suffix: str = ".extracted"
    resolve_relative: bool = False
    defer_scripts: bool = False
    manifest_file: Path | None = None
    checksums: bool = False
    report: str = ""
    zip_output: bool = False
    exit_partial: int = 2
    fail_on_warn: bool = False
    charset: str = "auto"
    quiet: bool = False
    verbose: int = 0
    color: str = "auto"
    log_file: Path | None = None
    config_file: Path | None = None


@dataclass
class PageResult:
    """Store html_path, html_rel, out_path, charset, inline_css and related state."""

    html_path: Path
    html_rel: str
    out_path: Path | None = None
    charset: str = "utf-8"
    inline_css: list[InlineBlock] = field(default_factory=list)
    inline_js: list[InlineBlock] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)
    data_uris: list[DataUriHit] = field(default_factory=list)
    jsonld: list[str] = field(default_factory=list)
    importmaps: list[str] = field(default_factory=list)
    noscripts: list[str] = field(default_factory=list)
    templates: list[str] = field(default_factory=list)
    service_workers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class AssetStore:
    """Computes output paths and writes files atomically / deduped."""

    def __init__(self, opts: Options) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.opts = opts
        self.root = opts.out
        self._sha_cache: dict[str, Path] = {}
        self._url_cache: dict[str, Path] = {}
        self._lock = threading.Lock()
        self.manifest_pages: list[dict[str, Any]] = []

    def page_dir(self, html_rel: str) -> Path:
        if self.opts.flat or self.opts.content_addressed:
            return self.root
        stem = Path(html_rel).with_suffix("")
        return self.root / stem

    def allocate(
        self,
        page_dir: Path,
        bucket: str,
        name: str,
        content: bytes | None = None,
        *,
        url: str | None = None,
    ) -> Path:
        if url and self.opts.global_remote_cache:
            with self._lock:
                cached = self._url_cache.get(url)
            if cached is not None:
                return cached
        if self.opts.content_addressed and content is not None:
            h = _sha256(content)
            with self._lock:
                hit = self._sha_cache.get(h)
            if hit is not None:
                return hit
            ext = Path(name).suffix or ".bin"
            p = self.root / "_sha" / h[:2] / h[2:4] / (h + ext)
            with self._lock:
                self._sha_cache[h] = p
            return p
        if url and self.opts.global_remote_cache:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.netloc or "unknown").replace(":", "_")
            tail = Path(parsed.path).name or f"asset{Path(name).suffix}"
            p = self.root / "_remote" / host / f"{_short_hash(url)}_{tail}"
            with self._lock:
                self._url_cache[url] = p
            return p
        if self.opts.flat:
            return self.root / name
        if bucket == "remote" and self.opts.mirror_host and url:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.netloc or "unknown").replace(":", "_")
            rel = parsed.path.lstrip("/")
            if not rel:
                rel = Path(name).name
            return self.root / "_remote" / host / rel
        return page_dir / bucket / name

    def write(self, path: Path, data: bytes) -> None:
        if self.opts.dry_run:
            log.info("[dry-run] would write {} ({} bytes)", path, len(data))
            return
        if self.opts.skip_existing and path.exists():
            return
        if self.opts.incremental and path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.opts.atomic:
            tmp = path.with_name(path.name + ".part")
            atomic_write(tmp, data)
            tmp.replace(path)
        else:
            atomic_write(path, data)
        log.info("wrote {} ({} bytes)", path, len(data))


class Fetcher:
    """Concurrent, retried, rate-limited HTTP fetcher with SSRF guard."""

    def __init__(self, opts: Options) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.opts = opts
        self._ssl_ctx: ssl.SSLContext | None = None
        if opts.insecure:
            self._ssl_ctx = ssl._create_unverified_context()
        elif opts.ca_bundle:
            self._ssl_ctx = ssl.create_default_context(cafile=opts.ca_bundle)
        self._host_sems: dict[str, threading.Semaphore] = {}
        self._host_lock = threading.Lock()
        self._global_lock = threading.Lock()
        self._last_request_at: float = 0.0
        handlers: list[Any] = []
        try:
            import http.cookiejar as _cj

            if opts.cookie_file and opts.cookie_file.exists():
                jar = _cj.MozillaCookieJar(str(opts.cookie_file))
                try:
                    jar.load(ignore_discard=True, ignore_expires=True)
                except Exception:
                    jar = _cj.CookieJar()
                handlers.append(urllib.request.HTTPCookieProcessor(jar))
        except Exception:
            pass
        if opts.proxy:
            handlers.append(urllib.request.ProxyHandler({"http": opts.proxy, "https": opts.proxy}))
        if self._ssl_ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=self._ssl_ctx))
        self.opener = urllib.request.build_opener(*handlers)
        self.opener.addheaders = [("User-Agent", opts.user_agent)]
        for h in opts.headers:
            k, _, v = h.partition(":")
            if k and v:
                self.opener.addheaders.append((k.strip(), v.strip()))

    def is_allowed(self, url: str) -> bool:
        """Return whether allowed."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if self.opts.allow_host:
            allowed = {h.strip().lower() for h in self.opts.allow_host.split(",") if h.strip()}
            host = (parsed.hostname or "").lower()
            if parsed.netloc.lower() not in allowed and host not in allowed:
                return False
        if self.opts.deny_private_ips and parsed.hostname:
            try:
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
            except (socket.gaierror, OSError):
                return False
            for _fam, _t, _p, _c, sock in infos:
                ip = ipaddress.ip_address(sock[0])
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                    or ip.is_unspecified
                ):
                    return False
        return True

    def _host_sem(self, host: str) -> threading.Semaphore:
        limit = self.opts.per_host or self.opts.jobs
        with self._host_lock:
            s = self._host_sems.get(host)
            if s is None:
                s = threading.Semaphore(limit)
                self._host_sems[host] = s
            return s

    def _apply_global_rate(self) -> None:
        if self.opts.rate <= 0:
            return
        with self._global_lock:
            now = time.monotonic()
            min_gap = 1.0 / self.opts.rate
            wait = max(0.0, (self._last_request_at + min_gap) - now)
            if wait:
                time.sleep(wait)
            self._last_request_at = time.monotonic()

    def fetch(self, url: str, referer: str | None = None) -> tuple[bytes, str | None]:
        if not self.is_allowed(url):
            raise RuntimeError(f"URL blocked by policy: {url}")
        host = urllib.parse.urlparse(url).netloc
        with self._host_sem(host):
            last: Exception | None = None
            for attempt in range(self.opts.retries + 1):
                self._apply_global_rate()
                try:
                    return self._do_fetch(url, referer)
                except urllib.error.HTTPError as e:
                    last = e
                    if 400 <= e.code < 500 and e.code != 429:
                        raise
                except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                    last = e
                if attempt < self.opts.retries:
                    time.sleep(1.0 * (2**attempt))
            raise RuntimeError(f"fetch failed after {self.opts.retries + 1} attempt(s): {url}: {last}")

    def _do_fetch(self, url: str, referer: str | None) -> tuple[bytes, str | None]:
        req = urllib.request.Request(url)
        if referer:
            req.add_header("Referer", referer)
        if self.opts.auth:
            token = base64.b64encode(self.opts.auth.encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        with self.opener.open(req, timeout=self.opts.timeout) as resp:
            data = self._read_limited(resp)
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc in ("gzip", "x-gzip"):
                data = gzip.decompress(data)
            elif enc == "deflate":
                data = zlib.decompress(data)
            elif enc == "br" and _brotli is not None:
                data = _brotli.decompress(data)
            elif enc == "zstd" and _zstd is not None:
                data = _zstd.ZstdDecompressor().decompress(data)
            return data, resp.headers.get_content_type()

    def _read_limited(self, resp: Any) -> bytes:
        buf = bytearray()
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if self.opts.max_bytes and len(buf) > self.opts.max_bytes:
                raise RuntimeError(f"response exceeds max-bytes={self.opts.max_bytes}")
        return bytes(buf)


def scan_css_refs(css: str) -> tuple[list[str], list[str]]:
    """Return (imports, other_urls)."""
    imports = [m.group("url") for m in _CSS_IMPORT_RE.finditer(css)]
    urls: list[str] = []
    for m in _CSS_URL_RE.finditer(css):
        u = m.group("url").strip()
        if not u or u.startswith(("data:", "#")):
            continue
        urls.append(u)
    return imports, urls


def strip_css_comments(css: str) -> str:
    """Strip css comments."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def strip_js_line_comments(js: str) -> str:
    """Strip js line comments."""
    return re.sub(r"(?m)^\s*//(?!\s*[#@]).*$", "", js)


class HtmlExtractor(HTMLParser):
    """Single-pass extractor collecting inline blocks, refs, data URIs, etc."""

    def __init__(self, opts: Options, page_url: str | None, source: str) -> None:
        """Initialize the instance state from the supplied arguments."""
        super().__init__(convert_charrefs=True)
        self.opts = opts
        self.page_url = page_url
        self.source = source
        self._line_offsets: list[int] = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                self._line_offsets.append(i + 1)
        self.inline_css: list[InlineBlock] = []
        self.inline_js: list[InlineBlock] = []
        self.refs: list[Ref] = []
        self.data_uris: list[DataUriHit] = []
        self.jsonld: list[str] = []
        self.importmaps: list[str] = []
        self.noscripts: list[str] = []
        self.templates: list[str] = []
        self._mode: str | None = None
        self._buf: list[str] = []
        self._content_start = 0
        self._block_start = 0
        self._style_idx = 0
        self._script_idx = 0
        self._cur_id: str | None = None
        self._template_depth = 0
        self._template_start = 0
        self._noscript_depth = 0
        self._noscript_start = 0

    def _abs(self) -> int:
        ln, off = self.getpos()
        return self._line_offsets[ln - 1] + off

    def _want_script_type(self, typ: str) -> bool:
        t = typ.strip().lower()
        if t in _NON_JS_TYPES:
            return False
        allow = {x.strip().lower() for x in self.opts.script_types.split(",") if x.strip()}
        if allow:
            return t in allow or (t == "" and "" in allow)
        return t in _JS_LIKE_TYPES or t.startswith("text/") or "/" in t

    def _record_ref(
        self,
        tag: str,
        tag_text: str,
        tag_start: int,
        attrs: dict[str, str],
        attr: str,
        kind: str,
    ) -> None:
        val = attrs.get(attr)
        if not val:
            return
        span = find_attr_value_span(tag_text, attr)
        if span is None:
            return
        rel_start, rel_end, quote = span
        self.refs.append(
            Ref(
                url=val,
                kind=kind,
                tag=tag,
                start=tag_start + rel_start,
                end=tag_start + rel_end,
                quote=quote,
                attr=attr,
                integrity=attrs.get("integrity"),
                cross_origin=attrs.get("crossorigin"),
                referer=attrs.get("referrerpolicy"),
            )
        )

    def _scan_data_uris(self, tag_text: str, tag_start: int) -> None:
        for m in re.finditer(
            r"""(?:src|href|poster|data-src)\s*=\s*(?P<q>["'])(?P<v>data:[^"']+)(?P=q)""",
            tag_text,
            re.IGNORECASE,
        ):
            uri = m.group("v")
            parsed = parse_data_uri(uri)
            if not parsed:
                continue
            mime, payload = parsed
            if self.opts.data_min and len(payload) < self.opts.data_min:
                continue
            if self.opts.data_max and len(payload) > self.opts.data_max:
                continue
            self.data_uris.append(
                DataUriHit(
                    uri=uri,
                    mime=mime,
                    payload=payload,
                    sha256=_sha256(payload),
                    start=tag_start + m.start("v"),
                    end=tag_start + m.end("v"),
                    quote=m.group("q"),
                )
            )
        for m in re.finditer(
            r"""srcset\s*=\s*(?P<q>["'])(?P<v>[^"']*data:[^"']*)(?P=q)""",
            tag_text,
            re.IGNORECASE,
        ):
            srcset = m.group("v")
            base = tag_start + m.start("v")
            cursor = 0
            for url, _desc in parse_srcset(srcset):
                if url.startswith("data:"):
                    parsed = parse_data_uri(url)
                    if parsed:
                        mime, payload = parsed
                        if self.opts.data_min and len(payload) < self.opts.data_min:
                            continue
                        if self.opts.data_max and len(payload) > self.opts.data_max:
                            continue
                        idx = srcset.find(url, cursor)
                        if idx < 0:
                            continue
                        self.data_uris.append(
                            DataUriHit(
                                uri=url,
                                mime=mime,
                                payload=payload,
                                sha256=_sha256(payload),
                                start=base + idx,
                                end=base + idx + len(url),
                                quote='"',
                            )
                        )
                        cursor = idx + len(url)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle starttag."""
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        start = self._abs()
        tag_text = self.get_starttag_text() or ""
        tag_end = start + len(tag_text)
        if tag == "style":
            if self.opts.no_inline_styles:
                return
            self._mode = "style"
            self._buf = []
            self._content_start = tag_end
            self._block_start = start
            self._cur_id = a.get("id")
            return
        if tag == "script":
            typ = (a.get("type") or "").strip().lower()
            if a.get("src"):
                self._record_ref(tag, tag_text, start, a, "src", "js")
                return
            if self.opts.no_inline_scripts:
                return
            if typ in ("application/ld+json", "application/json"):
                if self.opts.extract_jsonld:
                    self._mode = "jsonld"
                    self._buf = []
                    self._content_start = tag_end
                return
            if typ == "importmap" and self.opts.extract_importmap:
                self._mode = "importmap"
                self._buf = []
                self._content_start = tag_end
                return
            if not self._want_script_type(typ):
                return
            self._mode = "script"
            self._buf = []
            self._content_start = tag_end
            self._block_start = start
            self._cur_id = a.get("id")
            return
        if tag == "link":
            rel = (a.get("rel") or "").lower()
            href = a.get("href") or ""
            if "stylesheet" in rel:
                self._record_ref(tag, tag_text, start, a, "href", "css")
            elif self.opts.preload and any(r in rel for r in ("preload", "modulepreload", "prefetch")):
                kind = {
                    "script": "js",
                    "style": "css",
                    "font": "font",
                    "image": "media",
                }.get((a.get("as") or "").lower(), "other")
                self._record_ref(tag, tag_text, start, a, "href", kind)
            elif self.opts.favicon and any(r in rel for r in ("icon", "shortcut icon", "apple-touch-icon")):
                self._record_ref(tag, tag_text, start, a, "href", "favicon")
            elif self.opts.manifest_webmanifest and "manifest" in rel:
                self._record_ref(tag, tag_text, start, a, "href", "other")
            elif (href.startswith(("data:", "http"))) and self.opts.extract_b64:
                pass
        if self.opts.meta_images and tag == "meta":
            prop = (a.get("property") or a.get("name") or "").lower()
            content = a.get("content") or ""
            if prop in ("og:image", "og:image:url", "twitter:image") and content:
                span = find_attr_value_span(tag_text, "content")
                if span:
                    rs, re_, q = span
                    self.refs.append(
                        Ref(
                            url=content,
                            kind="media",
                            tag="meta",
                            attr="content",
                            start=start + rs,
                            end=start + re_,
                            quote=q,
                        )
                    )
        if self.opts.media:
            for attr in ("src", "poster"):
                if attr in a:
                    self._record_ref(tag, tag_text, start, a, attr, "media")
            for attr in ("srcset", "imagesrcset"):
                if attr in a:
                    self._record_srcset(tag, tag_text, start, a[attr], attr)
        if self.opts.iframes and tag == "iframe" and a.get("src"):
            self._record_ref(tag, tag_text, start, a, "src", "other")
        if self.opts.extract_b64:
            self._scan_data_uris(tag_text, start)
        if tag == "template":
            if self._template_depth == 0:
                self._template_start = tag_end
            self._template_depth += 1
        elif tag == "noscript":
            if self._noscript_depth == 0:
                self._noscript_start = tag_end
            self._noscript_depth += 1

    def _record_srcset(self, tag: str, tag_text: str, start: int, srcset: str, attr: str) -> None:
        cursor = 0
        for url, _desc in parse_srcset(srcset):
            if url.startswith("data:"):
                continue
            idx = tag_text.find(url, cursor)
            if idx < 0:
                continue
            cursor = idx + len(url)
            self.refs.append(
                Ref(
                    url=url,
                    kind="media",
                    tag=tag,
                    attr=attr,
                    start=start + idx,
                    end=start + idx + len(url),
                    quote='"',
                )
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle startendtag."""
        if tag.lower() in ("script", "style"):
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)
        else:
            self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        """Handle data."""
        if self._mode is not None:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        """Handle endtag."""
        tag = tag.lower()
        if tag == "style" and self._mode == "style":
            self._finalize_inline("css")
            self._mode = None
        elif tag == "script":
            if self._mode == "script":
                self._finalize_inline("js")
            elif self._mode == "jsonld":
                self.jsonld.append("".join(self._buf))
            elif self._mode == "importmap":
                self.importmaps.append("".join(self._buf))
            self._mode = None
        elif tag == "template":
            self._template_depth -= 1
            if self._template_depth == 0 and self.opts.extract_template:
                end = self._abs()
                self.templates.append(self.source[self._template_start : end])
        elif tag == "noscript":
            self._noscript_depth -= 1
            if self._noscript_depth == 0 and self.opts.extract_noscript:
                end = self._abs()
                self.noscripts.append(self.source[self._noscript_start : end])

    def _finalize_inline(self, kind: str) -> None:
        content = "".join(self._buf)
        if not content.strip():
            return
        if self.opts.strip_comments:
            content = strip_css_comments(content) if kind == "css" else strip_js_line_comments(content)
        if self.opts.minify:
            content = _maybe_minify(kind, content)
        elif self.opts.beautify:
            content = _maybe_beautify(kind, content)
        name_hint: str | None = None
        if self.opts.name_by_id and self._cur_id:
            name_hint = re.sub(r"[^A-Za-z0-9_-]", "_", self._cur_id)
        if kind == "css":
            self._style_idx += 1
            self.inline_css.append(
                InlineBlock(
                    kind="css",
                    text=content,
                    start=self._block_start,
                    end=self._content_start + len("".join(self._buf)) + len("</style>"),
                    index=self._style_idx,
                    name_hint=name_hint,
                )
            )
            if self.opts.css_imports or self.opts.css_urls or self.opts.webfonts:
                imports, urls = scan_css_refs(content)
                if self.opts.css_imports:
                    for u in imports:
                        self.refs.append(
                            Ref(
                                url=u,
                                kind="css",
                                tag="style",
                                attr="url",
                                start=-1,
                                end=-1,
                                quote='"',
                            )
                        )
                if self.opts.css_urls or self.opts.webfonts:
                    for u in urls:
                        k = "font" if self.opts.webfonts else "media"
                        self.refs.append(
                            Ref(
                                url=u,
                                kind=k,
                                tag="style",
                                attr="url",
                                start=-1,
                                end=-1,
                                quote='"',
                            )
                        )
        else:
            self._script_idx += 1
            self.inline_js.append(
                InlineBlock(
                    kind="js",
                    text=content,
                    start=self._block_start,
                    end=self._content_start + len("".join(self._buf)) + len("</script>"),
                    index=self._script_idx,
                    name_hint=name_hint,
                )
            )
            if self.opts.serviceworkers:
                for m in _SW_REGISTER_RE.finditer(content):
                    self.refs.append(
                        Ref(
                            url=m.group("url"),
                            kind="other",
                            tag="script",
                            attr="sw",
                            start=-1,
                            end=-1,
                            quote='"',
                        )
                    )
            if self.opts.sourcemaps:
                for m in _SOURCEMAP_RE.finditer(content):
                    self.refs.append(
                        Ref(
                            url=m.group("url"),
                            kind="other",
                            tag="script",
                            attr="sourcemap",
                            start=-1,
                            end=-1,
                            quote='"',
                        )
                    )


class PageProcessor:
    def __init__(self, opts: Options, store: AssetStore, fetcher: Fetcher | None, root: Path) -> None:
        """Initialize the instance state from the supplied arguments."""
        self.opts = opts
        self.store = store
        self.fetcher = fetcher
        self.root = root

    def process(self, html_path: Path) -> PageResult:
        rel = str(html_path.relative_to(self.root))
        result = PageResult(html_path=html_path, html_rel=rel)
        raw = html_path.read_bytes()
        declared = None if self.opts.charset == "auto" else self.opts.charset
        source, charset = decode_bytes(raw, declared)
        result.charset = charset
        page_url = None
        if html_path.is_absolute():
            page_url = html_path.as_uri()
        parser = HtmlExtractor(self.opts, page_url, source)
        try:
            parser.feed(source)
            parser.close()
        except Exception as e:
            result.errors.append(f"parse: {e}")
            return result
        result.inline_css = parser.inline_css
        result.inline_js = parser.inline_js
        result.refs = parser.refs
        result.data_uris = parser.data_uris
        result.jsonld = parser.jsonld
        result.importmaps = parser.importmaps
        result.noscripts = parser.noscripts
        result.templates = parser.templates
        page_dir = self.store.page_dir(rel)
        page_stem = Path(rel).stem
        edits: list[Edit] = []
        if self.opts.concat_style and result.inline_css:
            merged = "\n\n".join(b.text for b in result.inline_css)
            p = self.store.allocate(page_dir, "css", f"{page_stem}.inline.css", merged.encode("utf-8"))
            self.store.write(p, merged.encode("utf-8"))
            first = result.inline_css[0]
            _fill_inline_paths(
                result.inline_css,
                p,
                source,
                self._out_path_for(html_path),
                self.opts,
                edits,
                concat_anchor=first,
            )
            for blk in result.inline_css[1:]:
                edits.append(Edit(blk.start, blk.end, ""))
        else:
            for blk in result.inline_css:
                name = (blk.name_hint + ".css") if blk.name_hint else f"inline_{blk.index}.css"
                p = self.store.allocate(page_dir, "css", name, blk.text.encode("utf-8"))
                self.store.write(p, blk.text.encode("utf-8"))
                blk_path = p
                if self.opts.rewrite:
                    href = rel_href(self._out_path_for(html_path), blk_path)
                    edits.append(Edit(blk.start, blk.end, f'<link rel="stylesheet" href="{href}">'))
        if self.opts.concat_script and result.inline_js:
            merged = "\n\n".join(b.text for b in result.inline_js)
            p = self.store.allocate(page_dir, "js", f"{page_stem}.inline.js", merged.encode("utf-8"))
            self.store.write(p, merged.encode("utf-8"))
            first = result.inline_js[0]
            if self.opts.rewrite:
                href = rel_href(self._out_path_for(html_path), p)
                defer = " defer" if self.opts.defer_scripts else ""
                edits.append(Edit(first.start, first.end, f'<script src="{href}"{defer}></script>'))
            for blk in result.inline_js[1:]:
                edits.append(Edit(blk.start, blk.end, ""))
        else:
            for blk in result.inline_js:
                name = (blk.name_hint + ".js") if blk.name_hint else f"inline_{blk.index}.js"
                p = self.store.allocate(page_dir, "js", name, blk.text.encode("utf-8"))
                self.store.write(p, blk.text.encode("utf-8"))
                if self.opts.rewrite:
                    href = rel_href(self._out_path_for(html_path), p)
                    defer = " defer" if self.opts.defer_scripts else ""
                    edits.append(Edit(blk.start, blk.end, f'<script src="{href}"{defer}></script>'))
        for i, txt in enumerate(result.jsonld, 1):
            p = self.store.allocate(page_dir, "data", f"jsonld_{i}.json", txt.encode("utf-8"))
            self.store.write(p, txt.encode("utf-8"))
        for i, txt in enumerate(result.importmaps, 1):
            p = self.store.allocate(page_dir, "data", f"importmap_{i}.json", txt.encode("utf-8"))
            self.store.write(p, txt.encode("utf-8"))
        for i, txt in enumerate(result.noscripts, 1):
            p = self.store.allocate(page_dir, "data", f"noscript_{i}.html", txt.encode("utf-8"))
            self.store.write(p, txt.encode("utf-8"))
        for i, txt in enumerate(result.templates, 1):
            p = self.store.allocate(page_dir, "data", f"template_{i}.html", txt.encode("utf-8"))
            self.store.write(p, txt.encode("utf-8"))
        by_sha: dict[str, list[DataUriHit]] = {}
        for hit in result.data_uris:
            by_sha.setdefault(hit.sha256, []).append(hit)
        for sha, hits in by_sha.items():
            first = hits[0]
            ext = _ext_for_mime(first.mime, ".bin")
            p = self.store.allocate(page_dir, "data", sha[:16] + ext, first.payload)
            self.store.write(p, first.payload)
            for h in hits:
                h.saved_path = p
                if self.opts.rewrite:
                    if self.opts.data_keep_small and self.opts.data_max and len(h.payload) <= self.opts.data_max:
                        continue
                    href = rel_href(self._out_path_for(html_path), p)
                    edits.append(Edit(h.start, h.end, href))
        remote_refs: list[Ref] = []
        for ref in result.refs:
            if ref.start < 0:
                continue
            if ref.url.startswith("data:"):
                continue
            if ref.url.startswith("//"):
                ref.url = "https:" + ref.url
            if ref.url.startswith(("http://", "https://")):
                remote_refs.append(ref)
            elif self.opts.resolve_relative and not ref.url.startswith(("#", "mailto:", "javascript:")):
                self._resolve_local(ref, html_path, page_dir, edits)
        if self.opts.remote and remote_refs and self.fetcher:
            self._fetch_remotes(remote_refs, html_path, page_dir, edits, result)
        if self.opts.rewrite and edits:
            new_source = apply_edits(source, edits)
            out_html = self._out_path_for(html_path)
            payload = new_source.encode(charset, "replace") if charset else new_source.encode("utf-8")
            if self.opts.dry_run:
                log.info("[dry-run] would write {} ({} bytes)", out_html, len(payload))
            else:
                out_html.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(out_html, payload)
                log.info("rewrote {} ({} bytes)", out_html, len(payload))
            result.out_path = out_html
        return result

    def _out_path_for(self, html_path: Path) -> Path:
        if self.opts.rewrite_inplace:
            return html_path
        return html_path.with_name(html_path.stem + self.opts.rewrite_suffix + html_path.suffix)

    def _resolve_local(self, ref: Ref, html_path: Path, page_dir: Path, edits: list[Edit]) -> None:
        candidates: list[Path] = []
        url = ref.url.split("?")[0].split("#")[0]
        url = urllib.parse.unquote(url)
        if url.startswith("/"):
            candidates.append(self.root / url.lstrip("/"))
            candidates.append(html_path.parent / url.lstrip("/"))
        else:
            candidates.append((html_path.parent / url).resolve())
        src: Path | None = None
        for c in candidates:
            if c.is_file():
                src = c
                break
        if src is None:
            return
        data = src.read_bytes()
        name = src.name
        p = self.store.allocate(page_dir, "local", name, data)
        self.store.write(p, data)
        ref.saved_path = p
        ref.sha256 = _sha256(data)
        ref.size = len(data)
        if self.opts.rewrite:
            href = rel_href(self._out_path_for(html_path), p)
            edits.append(Edit(ref.start, ref.end, href))

    def _fetch_remotes(
        self,
        refs: list[Ref],
        html_path: Path,
        page_dir: Path,
        edits: list[Edit],
        result: PageResult,
    ) -> None:
        fetcher = self.fetcher
        assert fetcher is not None
        referer = html_path.as_uri() if self.opts.referer_from_page and html_path.is_absolute() else None

        def work(ref: Ref) -> Ref:
            try:
                data, mime = fetcher.fetch(ref.url, referer=referer)
                ref.mime = mime
                ref.size = len(data)
                ref.sha256 = _sha256(data)
                if self.opts.sri_check and ref.integrity:
                    if not verify_sri(data, ref.integrity):
                        ref.error = "SRI mismatch"
                        return ref
                base = Path(urllib.parse.urlparse(ref.url).path).name
                if not base:
                    base = f"remote{_ext_for_mime(mime)}"
                p = self.store.allocate(
                    page_dir,
                    "remote",
                    f"{_short_hash(ref.url)}_{base}",
                    data,
                    url=ref.url,
                )
                self.store.write(p, data)
                ref.saved_path = p
            except Exception as e:
                if self.opts.quiet_404 and isinstance(e, urllib.error.HTTPError) and e.code == 404:
                    ref.error = "404"
                else:
                    ref.error = str(e)
                    result.errors.append(f"{ref.url}: {e}")
                    log.warning("fetch failed {}: {}", ref.url, e)
            return ref

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.opts.jobs) as pool:
            futs = [pool.submit(work, r) for r in refs]
            for f in concurrent.futures.as_completed(futs):
                r = f.result()
                if self.opts.rewrite and r.saved_path is not None:
                    href = rel_href(self._out_path_for(html_path), r.saved_path)
                    edits.append(Edit(r.start, r.end, href))


def _fill_inline_paths(
    blocks: list[InlineBlock],
    path: Path,
    source: str,
    out_html: Path,
    opts: Options,
    edits: list[Edit],
    *,
    concat_anchor: InlineBlock,
) -> None:
    """Used for --concat-style: rewrite the first block, blanks the rest."""
    if opts.rewrite:
        href = rel_href(out_html, path)
        edits.append(
            Edit(
                concat_anchor.start,
                concat_anchor.end,
                f'<link rel="stylesheet" href="{href}">',
            )
        )


def discover_html_files(opts: Options) -> list[Path]:
    """Discover html files."""
    root = opts.root
    out_resolved = opts.out.resolve() if opts.out.exists() else opts.out
    results: list[Path] = []
    ignore_spec = None
    if opts.respect_gitignore and _pathspec is not None:
        gi = root / ".gitignore"
        if gi.exists():
            try:
                ignore_spec = _pathspec.PathSpec.from_lines("gitwildmatch", gi.read_text().splitlines())
            except Exception:
                ignore_spec = None
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in opts.exts:
            continue
        if opts.max_depth and len(p.relative_to(root).parts) > opts.max_depth:
            continue
        if not opts.follow_symlinks and p.is_symlink():
            continue
        try:
            if p.resolve().is_relative_to(out_resolved):
                continue
        except (OSError, ValueError):
            pass
        rel = str(p.relative_to(root))
        if opts.include and not any(fnmatch.fnmatch(rel, pat) for pat in opts.include):
            continue
        if opts.exclude and any(fnmatch.fnmatch(rel, pat) for pat in opts.exclude):
            continue
        if ignore_spec is not None:
            try:
                if ignore_spec.match_file(rel):
                    continue
            except Exception:
                pass
        results.append(p)
    results.sort()
    return results


import fnmatch


def write_manifest(path: Path, store: AssetStore, pages: list[PageResult]) -> None:
    """Write manifest."""
    data: dict[str, Any] = {
        "version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "assets_dir": str(store.root),
        "pages": [],
    }
    for pr in pages:
        entry: dict[str, Any] = {
            "source": pr.html_rel,
            "output": str(pr.out_path) if pr.out_path else None,
            "charset": pr.charset,
            "inline_css": [
                {
                    "path": str(b.start),
                    "size": len(b.text.encode()),
                    "sha256": _sha256(b.text.encode()),
                    "name": b.name_hint,
                }
                for b in pr.inline_css
            ],
            "inline_js": [
                {
                    "path": str(b.start),
                    "size": len(b.text.encode()),
                    "sha256": _sha256(b.text.encode()),
                    "name": b.name_hint,
                }
                for b in pr.inline_js
            ],
            "refs": [
                {
                    "url": r.url,
                    "kind": r.kind,
                    "tag": r.tag,
                    "attr": r.attr,
                    "path": str(r.saved_path) if r.saved_path else None,
                    "sha256": r.sha256,
                    "size": r.size,
                    "mime": r.mime,
                    "error": r.error,
                }
                for r in pr.refs
            ],
            "data_uris": [
                {
                    "sha256": d.sha256,
                    "mime": d.mime,
                    "path": str(d.saved_path) if d.saved_path else None,
                    "size": len(d.payload),
                }
                for d in pr.data_uris
            ],
            "jsonld": len(pr.jsonld),
            "importmaps": len(pr.importmaps),
            "noscripts": len(pr.noscripts),
            "templates": len(pr.templates),
            "errors": pr.errors,
        }
        data["pages"].append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=2, default=str))
    log.info("manifest → {}", path)


def write_report(path: Path, fmt: str, pages: list[PageResult]) -> None:
    """Write report."""
    rows: list[dict[str, Any]] = []
    for pr in pages:
        for b in pr.inline_css + pr.inline_js:
            payload = b.text.encode()
            rows.append({
                "page": pr.html_rel,
                "kind": b.kind,
                "source": "inline",
                "url": "",
                "path": str(b.start),
                "size": len(payload),
                "sha256": _sha256(payload),
                "error": "",
            })
        for r in pr.refs:
            rows.append({
                "page": pr.html_rel,
                "kind": r.kind,
                "source": "remote" if r.url.startswith(("http://", "https://")) else "local",
                "url": r.url,
                "path": str(r.saved_path) if r.saved_path else "",
                "size": r.size or 0,
                "sha256": r.sha256 or "",
                "error": r.error or "",
            })
        for d in pr.data_uris:
            rows.append({
                "page": pr.html_rel,
                "kind": "data",
                "source": "data:",
                "url": "",
                "path": str(d.saved_path) if d.saved_path else "",
                "size": len(d.payload),
                "sha256": d.sha256,
                "error": "",
            })
    if fmt == "json":
        atomic_write(path, json.dumps(rows, indent=2))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "page",
                    "kind",
                    "source",
                    "url",
                    "path",
                    "size",
                    "sha256",
                    "error",
                ],
            )
            w.writeheader()
            for r in rows:
                w.writerow(r)
    log.info("report ({}) → {}", fmt, path)


def write_checksums(assets_dir: Path) -> None:
    """Write checksums."""
    lines: list[str] = []
    for p in sorted(assets_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(assets_dir).as_posix()
        try:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
        lines.append(f"{h}  {rel}")
    atomic_write((assets_dir / "SHA256SUMS"), "\n".join(lines) + "\n")
    log.info("SHA256SUMS → {} entries", len(lines))


def zip_assets(assets_dir: Path) -> None:
    zpath = assets_dir.with_suffix(".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in assets_dir.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(assets_dir).as_posix())
    log.info("zip → {}", zpath)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build arg parser."""
    p = argparse.ArgumentParser(
        prog="extract_assets.py",
        description="Recursively extract CSS/JS/data-URIs and other assets from HTML.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("root", nargs="?", default=".", help="root directory to scan")
    p.add_argument("-o", "--out", default="assets", help="assets output dir")
    p.add_argument(
        "-r",
        "--remote",
        action="store_true",
        help="download remote <script src> / <link rel=stylesheet>",
    )
    p.add_argument(
        "-b",
        "--base64",
        dest="extract_b64",
        action="store_true",
        help="decode data: URIs into files",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--help-optional",
        action="store_true",
        help="show optional 3rd-party library status",
    )
    g = p.add_argument_group("selection")
    g.add_argument("--include", action="append", default=[], metavar="GLOB")
    g.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    g.add_argument("--max-depth", type=int, default=0)
    g.add_argument("--exts", default=".html,.htm,.xhtml,.shtml")
    g.add_argument(
        "--no-follow-symlinks",
        dest="follow_symlinks",
        action="store_false",
        default=True,
    )
    g.add_argument("--respect-gitignore", action="store_true")
    g = p.add_argument_group("inline extraction")
    g.add_argument("--no-inline-styles", action="store_true")
    g.add_argument("--no-inline-scripts", action="store_true")
    g.add_argument("--concat-style", action="store_true")
    g.add_argument("--concat-script", action="store_true")
    g.add_argument("--name-by-id", action="store_true")
    g.add_argument("--strip-comments", action="store_true")
    g.add_argument("--minify", action="store_true")
    g.add_argument("--beautify", action="store_true")
    g.add_argument("--extract-noscript", action="store_true")
    g.add_argument("--extract-template", action="store_true")
    g.add_argument("--extract-jsonld", action="store_true")
    g.add_argument(
        "--no-skip-jsonld",
        dest="skip_jsonld",
        action="store_false",
        default=True,
        help="include JSON-LD blocks in .js extraction",
    )
    g.add_argument("--extract-importmap", action="store_true")
    g.add_argument(
        "--script-types",
        default="",
        help="comma-separated type= allow-list for inline scripts",
    )
    g = p.add_argument_group("remote fetching")
    g.add_argument("--allow-host", default="", help="comma-separated host allow-list (blocks SSRF)")
    g.add_argument("--deny-private-ips", action="store_true")
    g.add_argument("--retries", type=int, default=2)
    g.add_argument("--rate", type=float, default=0.0, help="global RPS (0=unlimited)")
    g.add_argument("--per-host", type=int, default=0, help="per-host concurrency")
    g.add_argument("-j", "--jobs", type=int, default=8)
    g.add_argument("-t", "--timeout", type=float, default=15.0)
    g.add_argument("--max-bytes", type=int, default=50 * 1024 * 1024)
    g.add_argument("--user-agent", default=f"html-extract/{__version__}")
    g.add_argument("--header", dest="headers", action="append", default=[], metavar="K: V")
    g.add_argument("--cookie-file", type=Path, default=None)
    g.add_argument("--auth", default="", metavar="user:pass")
    g.add_argument("--proxy", default="")
    g.add_argument("--insecure", action="store_true")
    g.add_argument("--ca-bundle", default="")
    g.add_argument("--sri-check", action="store_true")
    g.add_argument("--strip-integrity", action="store_true")
    g.add_argument(
        "--no-referer-from-page",
        dest="referer_from_page",
        action="store_false",
        default=True,
    )
    g.add_argument("--no-quiet-404", dest="quiet_404", action="store_false", default=True)
    g = p.add_argument_group("extra asset classes")
    g.add_argument("--favicon", action="store_true")
    g.add_argument("--preload", action="store_true")
    g.add_argument("--media", action="store_true")
    g.add_argument("--webfonts", action="store_true")
    g.add_argument("--manifest-webmanifest", action="store_true")
    g.add_argument("--serviceworkers", action="store_true")
    g.add_argument("--iframes", action="store_true")
    g.add_argument("--iframes-follow", action="store_true")
    g.add_argument("--meta-images", action="store_true")
    g.add_argument("--all-refs", action="store_true")
    g.add_argument("--sourcemaps", action="store_true")
    g.add_argument("--css-imports", action="store_true")
    g.add_argument("--css-urls", action="store_true")
    g = p.add_argument_group("data URIs")
    g.add_argument("--data-min", type=int, default=0)
    g.add_argument("--data-max", type=int, default=0)
    g.add_argument(
        "--data-keep-small",
        action="store_true",
        help="keep small data URIs inline when --rewrite is used",
    )
    g = p.add_argument_group("HTML rewrite")
    g.add_argument(
        "--rewrite",
        action="store_true",
        help="emit <stem>.extracted.html with asset paths substituted",
    )
    g.add_argument("--rewrite-inplace", action="store_true")
    g.add_argument("--rewrite-suffix", default=".extracted")
    g.add_argument("--resolve-relative", action="store_true")
    g.add_argument("--defer-scripts", action="store_true")
    g = p.add_argument_group("output layout")
    g.add_argument("--flat", action="store_true")
    g.add_argument("--content-addressed", action="store_true")
    g.add_argument("--mirror-host", action="store_true")
    g.add_argument("--global-remote-cache", action="store_true")
    g.add_argument("--overwrite", dest="overwrite", action="store_true", default=True)
    g.add_argument("--no-overwrite", dest="overwrite", action="store_false")
    g.add_argument("--skip-existing", action="store_true")
    g.add_argument("--incremental", action="store_true")
    g.add_argument("--no-atomic", dest="atomic", action="store_false", default=True)
    g.add_argument("--clean", action="store_true")
    g = p.add_argument_group("reporting")
    g.add_argument("--manifest", dest="manifest_file", type=Path, default=None, metavar="PATH")
    g.add_argument("--checksums", action="store_true")
    g.add_argument("--report", choices=["", "json", "csv"], default="")
    g.add_argument("--zip", dest="zip_output", action="store_true")
    g.add_argument("--fail-on-warn", action="store_true")
    g.add_argument("--exit-partial", type=int, default=2)
    g.add_argument("--dry-run", action="store_true")
    g = p.add_argument_group("misc")
    g.add_argument("--charset", default="auto")
    g.add_argument("-q", "--quiet", action="store_true")
    g.add_argument("-v", "--verbose", action="count", default=0)
    g.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    g.add_argument("--log-file", type=Path, default=None)
    g.add_argument("--config", dest="config_file", type=Path, default=None)
    g.add_argument("--print-config", action="store_true")
    return p


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        log.warning("config file not found: {}", path)
        return {}
    suffix = path.suffix.lower()
    if suffix in (".toml",) and _toml is not None:
        return _toml.loads(path.read_text())
    if suffix in (".yaml", ".yml") and _yaml is not None:
        return _yaml.safe_load(path.read_text()) or {}
    try:
        return json.loads(path.read_text())
    except Exception:
        log.warning("cannot parse config file: {}", path)
        return {}


def _apply_config(args: argparse.Namespace, parser: argparse.ArgumentParser, cfg: dict[str, Any]) -> None:
    for key, val in cfg.items():
        dest = key.replace("-", "_")
        if not hasattr(args, dest):
            continue
        default = parser.get_default(dest)
        if getattr(args, dest) == default:
            setattr(args, dest, val)


def _to_options(args: argparse.Namespace) -> Options:
    exts = tuple(x.strip() for x in args.exts.split(",") if x.strip())
    if args.all_refs:
        for flag in (
            "favicon",
            "preload",
            "media",
            "webfonts",
            "manifest_webmanifest",
            "serviceworkers",
            "iframes",
            "meta_images",
            "css_imports",
            "css_urls",
            "sourcemaps",
        ):
            setattr(args, flag, True)
    opts = Options(
        root=Path(args.root).resolve(),
        out=Path(args.out).resolve(),
        clean=args.clean,
        dry_run=args.dry_run,
        flat=args.flat,
        content_addressed=args.content_addressed,
        mirror_host=args.mirror_host,
        global_remote_cache=args.global_remote_cache,
        overwrite=args.overwrite,
        skip_existing=args.skip_existing,
        incremental=args.incremental,
        atomic=args.atomic,
        include=list(args.include),
        exclude=list(args.exclude),
        max_depth=args.max_depth,
        follow_symlinks=args.follow_symlinks,
        respect_gitignore=args.respect_gitignore,
        exts=exts,
        no_inline_styles=args.no_inline_styles,
        no_inline_scripts=args.no_inline_scripts,
        concat_style=args.concat_style,
        concat_script=args.concat_script,
        name_by_id=args.name_by_id,
        strip_comments=args.strip_comments,
        minify=args.minify,
        beautify=args.beautify,
        extract_noscript=args.extract_noscript,
        extract_template=args.extract_template,
        extract_jsonld=args.extract_jsonld,
        skip_jsonld=args.skip_jsonld,
        extract_importmap=args.extract_importmap,
        script_types=args.script_types,
        remote=args.remote,
        allow_host=args.allow_host,
        deny_private_ips=args.deny_private_ips,
        retries=args.retries,
        rate=args.rate,
        per_host=args.per_host,
        jobs=args.jobs,
        timeout=args.timeout,
        max_bytes=args.max_bytes,
        user_agent=args.user_agent,
        headers=list(args.headers),
        cookie_file=args.cookie_file,
        auth=args.auth,
        proxy=args.proxy,
        insecure=args.insecure,
        ca_bundle=args.ca_bundle,
        sri_check=args.sri_check,
        strip_integrity=args.strip_integrity,
        referer_from_page=args.referer_from_page,
        quiet_404=args.quiet_404,
        favicon=args.favicon,
        preload=args.preload,
        media=args.media,
        webfonts=args.webfonts,
        manifest_webmanifest=args.manifest_webmanifest,
        serviceworkers=args.serviceworkers,
        iframes=args.iframes,
        iframes_follow=args.iframes_follow,
        meta_images=args.meta_images,
        all_refs=args.all_refs,
        sourcemaps=args.sourcemaps,
        css_imports=args.css_imports,
        css_urls=args.css_urls,
        extract_b64=args.extract_b64,
        data_min=args.data_min,
        data_max=args.data_max,
        data_keep_small=args.data_keep_small,
        rewrite=args.rewrite,
        rewrite_inplace=args.rewrite_inplace,
        rewrite_suffix=args.rewrite_suffix,
        resolve_relative=args.resolve_relative,
        defer_scripts=args.defer_scripts,
        manifest_file=args.manifest_file,
        checksums=args.checksums,
        report=args.report,
        zip_output=args.zip_output,
        fail_on_warn=args.fail_on_warn,
        exit_partial=args.exit_partial,
        charset=args.charset,
        quiet=args.quiet,
        verbose=args.verbose,
        color=args.color,
        log_file=args.log_file,
        config_file=args.config_file,
    )
    return opts


def _print_optional_status() -> None:
    rows = [
        ("tomllib/tomli", _toml is not None, "TOML config files"),
        ("PyYAML", _yaml is not None, "YAML config files"),
        ("charset_normalizer/chardet", _cn is not None, "robust charset detection"),
        ("tqdm", _tqdm is not None, "progress bar"),
        ("brotli", _brotli is not None, "brotli content-encoding"),
        ("zstandard", _zstd is not None, "zstd content-encoding"),
        ("rjsmin/rcssmin", _jsmin is not None, "--minify"),
        ("jsbeautifier/cssbeautifier", _jsbeautifier is not None, "--beautify"),
        ("pathspec", _pathspec is not None, "--respect-gitignore"),
    ]
    print(f"{'library':<32} {'status':<10} feature")
    print("-" * 72)
    for name, ok, feat in rows:
        print(f"{name:<32} {'OK' if ok else 'missing':<10} {feat}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.help_optional:
        _print_optional_status()
        return 0
    if args.config_file:
        cfg = _load_config(args.config_file)
        _apply_config(args, parser, cfg)
    if args.print_config:
        print(json.dumps(vars(args), indent=2, default=str))
        return 0
    opts = _to_options(args)
    level = "WARNING" if opts.quiet else ("DEBUG" if opts.verbose >= 2 else "INFO")
    logger.remove()
    logger.add(sys.stderr, level=level, format="{level} {message}")
    if opts.log_file:
        logger.add(opts.log_file, level=level, format="{level} {message}")
    if opts.clean and opts.out.exists() and not opts.dry_run:
        log.info("cleaning {}", opts.out)
        shutil.rmtree(opts.out)
    opts.out.mkdir(parents=True, exist_ok=True)
    files = discover_html_files(opts)
    log.info("found {} HTML file(s) under {}", len(files), opts.root)
    fetcher = Fetcher(opts) if opts.remote else None
    store = AssetStore(opts)
    processor = PageProcessor(opts, store, fetcher, opts.root)
    iterator: Iterable[Path] = files
    if _tqdm is not None and files and not opts.quiet:
        iterator = _tqdm(files, desc="pages", unit="file")
    pages: list[PageResult] = []
    failed = 0
    for html_path in iterator:
        try:
            pr = processor.process(html_path)
            pages.append(pr)
            if pr.errors:
                failed += 1
        except Exception as e:
            log.exception("failed on {}: {}", html_path, e)
            failed += 1
    if opts.manifest_file:
        write_manifest(opts.manifest_file, store, pages)
    if opts.report:
        fmt = opts.report
        rpath = opts.out / f"report.{fmt}"
        write_report(rpath, fmt, pages)
    if opts.checksums:
        write_checksums(opts.out)
    if opts.zip_output:
        zip_assets(opts.out)
    total_refs = sum(len(p.refs) for p in pages)
    total_inline = sum(len(p.inline_css) + len(p.inline_js) for p in pages)
    total_data = sum(len(p.data_uris) for p in pages)
    log.info(
        "done: {} page(s), {} inline block(s), {} ref(s), {} data URI(s), {} failed",
        len(pages),
        total_inline,
        total_refs,
        total_data,
        failed,
    )
    if opts.fail_on_warn and failed:
        return opts.exit_partial
    return 0


if __name__ == "__main__":
    sys.exit(main())
