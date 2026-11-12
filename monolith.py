#!/data/data/com.termux/files/usr/bin/env python
"""monolith.py -- save a complete web page as a single self-contained HTML file.

A pure-stdlib Python 3.12+ port of the Rust ``monolith`` utility
(https://github.com/Y2Z/monolith). All CSS, JavaScript, images, fonts,
audio, video, favicons and (recursively) iframe contents are inlined as
``data:`` URIs or inline elements, producing one portable ``.html`` file
with no external resource references.

Example usage
-------------
CLI::

    python monolith.py https://example.com -o example.html
    python monolith.py https://example.com            # -> stdout
    cat page.html | python monolith.py - -o out.html  # read from stdin
    python -m monolith https://example.com -o out.html --no-js --no-css

Library::

    from monolith import Monolith

    m = Monolith("https://example.com").no_js().no_css().timeout(30).user_agent("monolith-py/2.0")
    if m.ok():
        m.save("example.html")
    else:
        print("failed:", m.error)

    # local HTML, no network:
    m2 = Monolith.from_string("<html>...</html>", base_url="https://x/")
    m2.ok()
    print(m2.html)

Upstream flag -> argparse flag mapping
--------------------------------------
    -o/--output        -> -o/--output
    -i  remove images  -> -i/--no-images
    -s  exclude styles -> -s/--no-css        (DESIGN NOTE: long renamed)
    -j  exclude JS     -> -j/--no-js
    -f  exclude fonts  -> -f/--no-fonts
    -I  isolate        -> -I/--isolate
    -M  no timestamp   -> -M/--no-metadata
    -q  quiet          -> -q/--silent        (DESIGN NOTE: -s taken by styles)
    -t  timeout        -> -t/--timeout
    -u  user-agent     -> -u/--user-agent
    -b  base URL       -> -b/--base-url
    -E  encoding       -> -E/--encoding      (DESIGN NOTE: -e is ignore-errors)
    -e  ignore errors  -> -e/--ignore-network-errors
    -k  insecure TLS   -> -k/--insecure
    -D  make dirs      -> -D/--make-dirs
    -V  version        -> -V/--version
    (extension)        -> -a/--no-audio      (Python extension; -a free upstream)
    (extension)        -> -F/--no-frames     (Python extension; -F free upstream)
    (extension)        -> -l/--no-links      (Python extension; -l free upstream)
    (extension)        -> -v/--no-video      (Python extension; -v free upstream)
    (extension)        -> --no-color         (long-only; upstream -C is cookies)

Not yet ported (require extra deps / a CSS-selector engine / a browser):
    -B browser render, -C cookies, -d/-W domain allow/deny, -m MHTML,
    -n noscript extraction, -r/-w selector remove/wipe, -R reader mode.

Design notes
------------
* ``.html`` returns ``str`` (UTF-8 decoded). ``.data`` returns ``bytes``
  (encoded with ``--encoding`` or UTF-8 by default).
* Asset fetch failures are fatal by default (mirrors upstream). Pass
  ``--ignore-network-errors`` / ``.ignore_network_errors(True)`` to skip
  bad assets and still produce a document. Main-document fetch failure
  is always fatal.
* Iframes are recursively inlined up to ``DEPTH_LIMIT`` (5) levels; the
  iframe element is replaced by the processed content of its document.
* CSS ``@import`` is inlined recursively (media queries on @import are
  dropped -- documented limitation). ``url(...)`` in CSS and inline
  ``style=""`` attributes is rewritten to ``data:`` URIs.
* Output is deterministic for a fixed input: attribute order is preserved
  from the source, data URIs are base64 (no whitespace), and the metadata
  timestamp can be disabled with ``--no-metadata``.
* Only the Python standard library is used. The fetcher is a swappable
  callable (``fetcher=``) so tests can inject fakes with no network.
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import re
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

__all__ = ["Monolith", "FetchResult", "AssetFetchError", "default_fetcher"]

# Register a few MIME types that the stdlib doesn't know by default.
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/otf", ".otf")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("image/svg+xml", ".svg")

DEPTH_LIMIT = 5
CSS_DEPTH_LIMIT = 5
DEFAULT_USER_AGENT = "monolith-py/2.0"
DEFAULT_TIMEOUT = 30.0

# Void elements (no end tag). End tags for these are dropped on emit.
_VOID = {
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
}

_EXT_MIME = {
    "woff": "font/woff",
    "woff2": "font/woff2",
    "ttf": "font/ttf",
    "otf": "font/otf",
    "eot": "application/vnd.ms-fontobject",
    "js": "application/javascript",
    "mjs": "application/javascript",
    "css": "text/css",
    "svg": "image/svg+xml",
}

# @import "x"; | @import url("x"); | @import url(x);  (optionally w/ media)
_CSS_IMPORT_RE = re.compile(
    r'@import\s+(?:url\(\s*["\']?([^"\')\s]+)["\']?\s*\)|["\']([^"\']+)["\'])' r"[^;]*;?",
    re.IGNORECASE,
)
# url("x") | url('x') | url(x)
_CSS_URL_RE = re.compile(r'url\(\s*(["\']?)([^"\')]+)\1\s*\)', re.IGNORECASE)
_BASE_RE = re.compile(r'<base\s+[^>]*href\s*=\s*["\']?([^"\'>\s]+)', re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _guess_mime(url: str) -> str:
    """Return the MIME type for ``url`` based on its path extension."""
    path = url.split("#", 1)[0].split("?", 1)[0]
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _EXT_MIME.get(ext, "application/octet-stream")


def _is_font(mime: str) -> bool:
    return (
        mime.startswith("font/")
        or "font" in mime
        or mime
        in (
            "application/vnd.ms-fontobject",
            "application/x-font-ttf",
        )
    )


def _is_image(mime: str) -> bool:
    return mime.startswith("image/")


def _data_uri(content: bytes, mime: str) -> str:
    """Encode ``content`` as a ``data:`` URI (base64, deterministic)."""
    return "data:%s;base64,%s" % (mime, base64.b64encode(content).decode("ascii"))


def _attr_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;")


def _text_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_js(text: str) -> str:
    # Prevent ``</script>`` from terminating an inline script early.
    return re.sub(r"</(script)", r"<\/\1", text, flags=re.IGNORECASE)


def _find_base(html: str) -> str:
    m = _BASE_RE.search(html)
    return m.group(1) if m else ""


def _charset_from_ct(content_type: str) -> str:
    for part in content_type.split(";"):
        part = part.strip().lower()
        if part.startswith("charset="):
            return part.split("=", 1)[1].strip("\"'")
    return "utf-8"


# --------------------------------------------------------------------------- #
# Fetcher seam
# --------------------------------------------------------------------------- #
@dataclass
class FetchResult:
    """Result of fetching a single URL."""

    url: str
    status: int
    content: bytes
    content_type: str = ""
    charset: str = "utf-8"
    headers: dict[str, str] = field(default_factory=dict)


# A fetcher is called as ``fetcher(url, *, timeout, user_agent, insecure, ctx)``
# and returns a :class:`FetchResult`. Tests inject a fake to avoid network.
Fetcher = Callable[..., FetchResult]


class AssetFetchError(Exception):
    """Raised when an asset fetch fails and ``ignore_network_errors`` is off."""


def default_fetcher(
    url: str,
    *,
    timeout: float,
    user_agent: str,
    insecure: bool,
    ctx: "Monolith",
) -> FetchResult:
    """Default network fetcher built on :mod:`urllib.request`."""
    if not url.startswith(("http://", "https://", "file://", "ftp://")):
        # Bare existing file path -> file URI; otherwise assume http.
        p = Path(url)
        if p.exists():
            url = p.resolve().as_uri()
        else:
            url = "http://" + url
    headers = {"User-Agent": user_agent or DEFAULT_USER_AGENT}
    req = urllib.request.Request(url, headers=headers)
    ssl_ctx = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
        content = resp.read()
        ct = resp.headers.get("Content-Type", "")
        final_url = resp.geturl()
        status = getattr(resp, "status", 200) or 200
        return FetchResult(
            url=final_url,
            status=status,
            content=content,
            content_type=ct,
            charset=_charset_from_ct(ct),
            headers=dict(resp.headers),
        )


# --------------------------------------------------------------------------- #
# HTML rewriter (inlines everything)
# --------------------------------------------------------------------------- #
class _Rewriter(HTMLParser):
    """Streaming HTML rewriter that inlines external resources in place."""

    def __init__(self, ctx: "Monolith", base: str, depth: int) -> None:
        super().__init__(convert_charrefs=True)
        self.ctx = ctx
        self._base = base
        self._depth = depth
        self.out: list[str] = []
        self._skip_until: str | None = None
        self._in_style = False
        self._style_base = base
        self._in_script = False

    # -- URL helpers ------------------------------------------------------- #
    def _resolve(self, href: str) -> str:
        if not href:
            return href
        return urljoin(self._base, href)

    def _is_external_ref(self, value: str) -> bool:
        v = (value or "").strip().lower()
        return not v.startswith(("data:", "javascript:", "about:", "#", "mailto:", "tel:", "blob:"))

    def _inline_url(self, value: str) -> str:
        """Fetch ``value`` and return a ``data:`` URI (or original if local)."""
        if not value or not self._is_external_ref(value):
            return value
        return self.ctx._fetch_data_uri(self._resolve(value))

    # -- HTMLParser callbacks --------------------------------------------- #
    def handle_decl(self, decl: str) -> None:
        if self._skip_until:
            return
        self.out.append("<!%s>" % decl)

    def handle_comment(self, data: str) -> None:
        if self._skip_until:
            return
        self.out.append("<!--%s-->" % data)

    def handle_pi(self, data: str) -> None:
        if self._skip_until:
            return
        self.out.append("<?%s>" % data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._dispatch(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._dispatch(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if self._skip_until:
            if tag_l == self._skip_until:
                self._skip_until = None
            return
        if tag_l in _VOID:
            return
        if self._in_style and tag_l == "style":
            self._in_style = False
            self.out.append("</style>")
            return
        if self._in_script and tag_l == "script":
            self._in_script = False
            self.out.append("</script>")
            return
        self.out.append("</%s>" % tag)

    def handle_data(self, data: str) -> None:
        if self._skip_until:
            return
        if self._in_style:
            self.out.append(self.ctx._process_css(data, self._style_base, 0))
            return
        if self._in_script:
            self.out.append(_escape_js(data))
            return
        self.out.append(_text_escape(data))

    # -- dispatch ---------------------------------------------------------- #
    def _dispatch(self, tag: str, attrs: list[tuple[str, str | None]], self_closing: bool) -> None:
        if self._skip_until:
            return
        tag_l = tag.lower()
        if tag_l == "base":
            return  # base already consumed in pre-pass
        if tag_l == "link":
            out = self._handle_link(attrs, self_closing)
            if out is not None:
                self.out.append(out)
            return
        if tag_l == "script":
            out = self._handle_script(attrs, self_closing)
            if out is not None:
                self.out.append(out)
            return
        if tag_l == "style":
            out = self._handle_style(attrs, self_closing)
            if out is not None:
                self.out.append(out)
            return
        if tag_l == "img":
            out = self._handle_media(tag, attrs, self_closing, "images", ("src", "srcset"))
            if out is not None:
                self.out.append(out)
            return
        if tag_l == "source":
            out = self._handle_media(tag, attrs, self_closing, "images", ("src", "srcset"))
            if out is not None:
                self.out.append(out)
            return
        if tag_l == "video":
            out = self._handle_media(tag, attrs, self_closing, "video", ("src", "poster", "srcset"))
            if out is not None:
                self.out.append(out)
            return
        if tag_l == "audio":
            out = self._handle_media(tag, attrs, self_closing, "audio", ("src", "srcset"))
            if out is not None:
                self.out.append(out)
            return
        if tag_l == "iframe":
            out = self._handle_iframe(attrs, self_closing)
            if out is not None:
                self.out.append(out)
            return
        if tag_l == "a":
            attrs = self._handle_anchor(attrs)
        # generic: process inline style="" attribute, then emit
        attrs = self._process_style_attr(attrs)
        self.out.append(self._render(tag, attrs, self_closing))

    # -- tag-specific handlers -------------------------------------------- #
    def _handle_link(self, attrs, sc):
        am = {k.lower(): v for k, v in attrs}
        rel = (am.get("rel") or "").lower()
        href = am.get("href")
        if href and self._is_external_ref(href):
            if "stylesheet" in rel:
                if self.ctx._no_css:
                    return None
                css = self.ctx._fetch_text(self._resolve(href))
                processed = self.ctx._process_css(css, self._resolve(href), 0)
                return "<style>\n%s\n</style>" % processed
            if "icon" in rel:
                if self.ctx._no_images:
                    return None
                uri = self._inline_url(href)
                new = [(k, v) for k, v in attrs if k.lower() != "href"]
                new.append(("href", uri))
                return self._render("link", new, sc)
        if self.ctx._no_links and href:
            return None  # drop non-stylesheet external <link> when stripping
        return self._render("link", attrs, sc)

    def _handle_script(self, attrs, sc):
        am = {k.lower(): v for k, v in attrs}
        src = am.get("src")
        if self.ctx._no_js:
            if not sc:
                self._skip_until = "script"
            return None
        if src and self._is_external_ref(src):
            js = self.ctx._fetch_text(self._resolve(src))
            new = [(k, v) for k, v in attrs if k.lower() != "src"]
            attr_str = self._render_attrs(new)
            self._skip_until = "script"  # drop original (empty) body + endtag
            return "<script%s>\n%s\n</script>" % (attr_str, _escape_js(js))
        attr_str = self._render_attrs(attrs)
        self._in_script = True
        return "<script%s>" % attr_str

    def _handle_style(self, attrs, sc):
        if self.ctx._no_css:
            if not sc:
                self._skip_until = "style"
            return None
        attr_str = self._render_attrs(attrs)
        self._in_style = True
        self._style_base = self._base
        return "<style%s>" % attr_str

    def _handle_media(self, tag, attrs, sc, kind, url_attrs):
        flag = {
            "images": self.ctx._no_images,
            "video": self.ctx._no_video,
            "audio": self.ctx._no_audio,
        }[kind]
        if flag:
            return None
        new = []
        for k, v in attrs:
            kl = k.lower()
            if kl in url_attrs and v:
                if kl == "srcset":
                    new.append((k, self._inline_srcset(v)))
                else:
                    new.append((k, self._inline_url(v)))
            else:
                new.append((k, v))
        new = self._process_style_attr(new)
        return self._render(tag, new, sc)

    def _handle_iframe(self, attrs, sc):
        if self.ctx._no_frames:
            return None
        am = {k.lower(): v for k, v in attrs}
        src = am.get("src")
        if not src or not self._is_external_ref(src):
            return self._render("iframe", attrs, sc)
        inlined = self.ctx._inline_iframe(self._resolve(src), self._depth)
        self._skip_until = "iframe"  # drop original fallback content + endtag
        return inlined

    def _handle_anchor(self, attrs):
        if self.ctx._no_links:
            return [(k, v) for k, v in attrs if k.lower() != "href"]
        return attrs

    def _process_style_attr(self, attrs):
        out = []
        for k, v in attrs:
            if k and k.lower() == "style" and v:
                out.append((k, self.ctx._process_css(v, self._base, 0)))
            else:
                out.append((k, v))
        return out

    def _inline_srcset(self, value: str) -> str:
        items = []
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            bits = item.split(None, 1)
            url = bits[0]
            descriptor = bits[1] if len(bits) > 1 else ""
            if self._is_external_ref(url):
                uri = self.ctx._fetch_data_uri(self._resolve(url))
            else:
                uri = url
            items.append(("%s %s" % (uri, descriptor)).rstrip())
        return ", ".join(items)

    # -- rendering --------------------------------------------------------- #
    def _render_attrs(self, attrs) -> str:
        parts = []
        for k, v in attrs:
            if v is None:
                parts.append(k)
            else:
                parts.append('%s="%s"' % (k, _attr_escape(v)))
        return (" " + " ".join(parts)) if parts else ""

    def _render(self, tag: str, attrs, self_closing: bool) -> str:
        return "<%s%s%s>" % (
            tag,
            self._render_attrs(attrs),
            " /" if self_closing else "",
        )


# --------------------------------------------------------------------------- #
# Monolith
# --------------------------------------------------------------------------- #
class Monolith:
    """Save a web page as a single self-contained HTML file.

    Construct with a URL (lazy -- no I/O). Chain option setters, then call
    :meth:`ok` to process. Read :attr:`html` / :attr:`data`, or call
    :meth:`save`.
    """

    def __init__(self, url: str, *, fetcher: Fetcher | None = None) -> None:
        self._url = url
        self._fetcher: Fetcher = fetcher or default_fetcher
        self._source_html: str | None = None
        self._source_url: str = url
        self._base_url_override: str | None = None
        # options
        self._no_css = False
        self._no_images = False
        self._no_js = False
        self._no_frames = False
        self._no_links = False
        self._no_metadata = False
        self._no_audio = False
        self._no_video = False
        self._no_fonts = False
        self._timeout: float = DEFAULT_TIMEOUT
        self._user_agent: str = DEFAULT_USER_AGENT
        self._insecure = False
        self._ignore_network_errors = False
        self._make_dirs = False
        self._isolate = False
        self._encoding: str | None = None
        # result
        self._ok: bool | None = None
        self._html: str = ""
        self._error: Exception | None = None
        # processing state (populated in _process)
        self._visited: set[str] = set()
        self._visited_assets: dict[str, bytes] = {}

    # -- chainable option setters (each returns self) --------------------- #
    def no_css(self) -> "Monolith":
        self._no_css = True
        return self

    def no_images(self) -> "Monolith":
        self._no_images = True
        return self

    def no_js(self) -> "Monolith":
        self._no_js = True
        return self

    def no_frames(self) -> "Monolith":
        self._no_frames = True
        return self

    def no_links(self) -> "Monolith":
        self._no_links = True
        return self

    def no_metadata(self) -> "Monolith":
        self._no_metadata = True
        return self

    def no_audio(self) -> "Monolith":
        self._no_audio = True
        return self

    def no_video(self) -> "Monolith":
        self._no_video = True
        return self

    def no_fonts(self) -> "Monolith":
        self._no_fonts = True
        return self

    def timeout(self, seconds: float) -> "Monolith":
        self._timeout = float(seconds)
        return self

    def user_agent(self, ua: str) -> "Monolith":
        self._user_agent = ua
        return self

    def base_url(self, url: str) -> "Monolith":
        self._base_url_override = url
        return self

    def isolate(self, value: bool = True) -> "Monolith":
        self._isolate = value
        return self

    def encoding(self, enc: str) -> "Monolith":
        self._encoding = enc
        return self

    def ignore_network_errors(self, value: bool = True) -> "Monolith":
        self._ignore_network_errors = value
        return self

    def insecure(self, value: bool = True) -> "Monolith":
        self._insecure = value
        return self

    def make_dirs(self, value: bool = True) -> "Monolith":
        self._make_dirs = value
        return self

    # -- result accessors -------------------------------------------------- #
    def ok(self) -> bool:
        """Process the document (idempotent). Return ``True`` on success."""
        if self._ok is None:
            self._process()
        return bool(self._ok)

    @property
    def html(self) -> str:
        """The serialized single-file HTML (str, UTF-8 decoded)."""
        if self._ok is None:
            self._process()
        return self._html

    @property
    def data(self) -> bytes:
        """The serialized HTML as bytes (encoded with ``encoding`` or UTF-8)."""
        enc = self._encoding or "utf-8"
        return self.html.encode(enc, errors="replace")

    @property
    def error(self) -> Exception | None:
        """The last exception, if processing failed (``None`` on success)."""
        return self._error

    def save(self, path: str | Path) -> None:
        """Write the result to ``path``. Use ``"-"`` for stdout."""
        if not self._ok:
            self.ok()
        target = str(path)
        data = self.data
        if target == "-":
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            return
        p = Path(path)
        if self._make_dirs and p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    # -- classmethod for local input -------------------------------------- #
    @classmethod
    def from_string(cls, html: str, base_url: str = "", *, fetcher: Fetcher | None = None) -> "Monolith":
        """Build a :class:`Monolith` from an HTML string (no network for body)."""
        m = cls(base_url or "about:blank", fetcher=fetcher)
        m._source_html = html
        m._base_url_override = base_url or None
        return m

    # -- internals --------------------------------------------------------- #
    def _process(self) -> None:
        try:
            self._visited = set()
            self._visited_assets = {}
            if self._source_html is not None:
                html = self._source_html
                base = self._base_url_override or self._source_url or ""
            else:
                url = self._url
                if url == "-":
                    html = sys.stdin.read()
                    base = self._base_url_override or ""
                else:
                    res = self._fetch_main(url)
                    html = self._decode(res.content)
                    base = res.url or url
                    if self._base_url_override:
                        base = self._base_url_override
            ibase = _find_base(html)
            if ibase:
                base = urljoin(base, ibase) if base else ibase
            self._html = self._process_html(html, base, 0)
            self._ok = True
        except Exception as exc:  # record, don't raise (CLI handles exit)
            self._error = exc
            self._ok = False

    def _fetch_main(self, url: str) -> FetchResult:
        return self._fetcher(
            url,
            timeout=self._timeout,
            user_agent=self._user_agent,
            insecure=self._insecure,
            ctx=self,
        )

    def _fetch_bytes(self, url: str) -> bytes | None:
        """Fetch ``url`` bytes. Returns ``None`` if skipped (ignore-errors)."""
        if url in self._visited_assets:
            return self._visited_assets[url]
        try:
            res = self._fetcher(
                url,
                timeout=self._timeout,
                user_agent=self._user_agent,
                insecure=self._insecure,
                ctx=self,
            )
        except Exception as exc:
            if self._ignore_network_errors:
                return None
            raise AssetFetchError(url) from exc
        content = res.content
        self._visited_assets[url] = content
        return content

    def _fetch_data_uri(self, url: str) -> str:
        content = self._fetch_bytes(url)
        if content is None:
            return ""
        return _data_uri(content, _guess_mime(url))

    def _fetch_text(self, url: str) -> str:
        content = self._fetch_bytes(url)
        if content is None:
            return ""
        return self._decode(content)

    def _decode(self, content: bytes) -> str:
        for enc in ("utf-8", "latin-1"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        return content.decode("utf-8", errors="replace")

    def _process_html(self, html: str, base: str, depth: int) -> str:
        rw = _Rewriter(self, base, depth)
        rw.feed(html)
        rw.close()
        out = "".join(rw.out)
        if depth == 0:
            out = self._inject_meta(out)
        return out

    def _inline_iframe(self, url: str, depth: int) -> str:
        if depth >= DEPTH_LIMIT or url in self._visited:
            return ""
        self._visited.add(url)
        content = self._fetch_bytes(url)
        if content is None:
            return ""
        html = self._decode(content)
        base = url
        ibase = _find_base(html)
        if ibase:
            base = urljoin(url, ibase)
        return self._process_html(html, base, depth + 1)

    def _process_css(self, css: str, base: str, depth: int) -> str:
        # @import (recursive)
        def import_repl(m: re.Match) -> str:
            u = m.group(1) or m.group(2)
            if not u:
                return m.group(0)
            if depth >= CSS_DEPTH_LIMIT:
                return ""
            resolved = urljoin(base, u)
            if resolved in self._visited:
                return ""
            self._visited.add(resolved)
            content = self._fetch_bytes(resolved)
            if content is None:
                return ""
            sub = self._decode(content)
            return self._process_css(sub, resolved, depth + 1)

        css = _CSS_IMPORT_RE.sub(import_repl, css)

        # url(...)
        def url_repl(m: re.Match) -> str:
            u = m.group(2).strip().strip("\"'")
            if (not u) or u.lower().startswith(("data:", "#", "javascript:")):
                return m.group(0)
            resolved = urljoin(base, u)
            mime = _guess_mime(resolved)
            if self._no_fonts and _is_font(mime):
                return "url()"
            if self._no_images and _is_image(mime):
                return "url()"
            content = self._fetch_bytes(resolved)
            if content is None:
                return "url()"
            return "url(%s)" % _data_uri(content, mime)

        css = _CSS_URL_RE.sub(url_repl, css)
        return css

    def _inject_meta(self, html: str) -> str:
        # 1. metadata comment (timestamp + source URL)
        if not self._no_metadata:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            src = self._source_url or self._url or "(stdin)"
            comment = "<!-- Saved by monolith.py from %s at %s -->" % (src, ts)
            m = re.match(r"\s*<!DOCTYPE[^>]*>\s*", html, re.IGNORECASE)
            if m:
                html = html[: m.end()] + comment + "\n" + html[m.end() :]
            else:
                html = comment + "\n" + html
        # 2. meta charset (if absent)
        if not re.search(r"<meta[^>]+charset\s*=", html, re.IGNORECASE):
            name = (self._encoding or "utf-8").lower()
            mc = '<meta charset="%s">' % name
            m = re.search(r"<head[^>]*>", html, re.IGNORECASE)
            if m:
                html = html[: m.end()] + mc + html[m.end() :]
            else:
                html = mc + html
        # 3. isolate CSP
        if self._isolate:
            csp = (
                '<meta http-equiv="Content-Security-Policy" '
                "content=\"default-src 'none'; img-src data:; "
                "media-src data:; font-src data:; "
                "style-src 'unsafe-inline'; script-src 'unsafe-inline';"
                '">'
            )
            m = re.search(r"<head[^>]*>", html, re.IGNORECASE)
            if m:
                html = html[: m.end()] + csp + html[m.end() :]
            else:
                html = csp + html
        return html


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="monolith",
        description=("Save a web page as a single self-contained HTML file (pure-stdlib port of Y2Z/monolith)."),
    )
    p.add_argument("url", help="URL to fetch, or '-' to read HTML from stdin")
    p.add_argument("-o", "--output", default=None, help="write output to file ('-' for stdout; default stdout)")
    # resource exclusion flags
    p.add_argument("-a", "--no-audio", action="store_true", help="exclude audio")
    p.add_argument("-s", "--no-css", action="store_true", help="exclude CSS (upstream: -s exclude styles)")
    p.add_argument("-f", "--no-fonts", action="store_true", help="exclude fonts")
    p.add_argument("-F", "--no-frames", action="store_true", help="exclude iframes (extension)")
    p.add_argument("-i", "--no-images", action="store_true", help="exclude images")
    p.add_argument("-j", "--no-js", action="store_true", help="exclude JavaScript")
    p.add_argument("-l", "--no-links", action="store_true", help="strip link/anchor hrefs (extension)")
    p.add_argument("-M", "--no-metadata", action="store_true", help="don't add timestamp/URL comment (upstream -M)")
    p.add_argument("-v", "--no-video", action="store_true", help="exclude video (extension)")
    # behavior flags
    p.add_argument("-q", "--silent", action="store_true", help="suppress informational output (upstream: -q quiet)")
    p.add_argument("-t", "--timeout", type=float, default=DEFAULT_TIMEOUT, help="network request timeout in seconds")
    p.add_argument("-u", "--user-agent", default=DEFAULT_USER_AGENT, help="custom User-Agent string")
    p.add_argument("-b", "--base-url", default=None, help="custom base URL for resolving relative refs")
    p.add_argument("-E", "--encoding", default=None, help="output encoding (upstream -E; default utf-8)")
    p.add_argument("-I", "--isolate", action="store_true", help="isolate the document (inject strict CSP)")
    p.add_argument("--no-color", action="store_true", help="suppress ANSI color in stderr messages (extension)")
    p.add_argument("-D", "--make-dirs", action="store_true", help="create missing directories in the output path")
    p.add_argument(
        "-e", "--ignore-network-errors", action="store_true", help="ignore network errors when fetching assets"
    )
    p.add_argument("-k", "--insecure", action="store_true", help="accept invalid X.509 (TLS) certificates")
    p.add_argument("-V", "--version", action="version", version="monolith.py 2.0.0")
    return p


def _ansi(code: str, text: str, enabled: bool) -> str:
    if not enabled:
        return text
    return "\033[%sm%s\033[0m" % (code, text)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    use_color = (not args.no_color) and sys.stderr.isatty()
    m = Monolith(args.url)
    if args.no_audio:
        m.no_audio()
    if args.no_css:
        m.no_css()
    if args.no_fonts:
        m.no_fonts()
    if args.no_frames:
        m.no_frames()
    if args.no_images:
        m.no_images()
    if args.no_js:
        m.no_js()
    if args.no_links:
        m.no_links()
    if args.no_metadata:
        m.no_metadata()
    if args.no_video:
        m.no_video()
    if args.encoding:
        m.encoding(args.encoding)
    if args.base_url:
        m.base_url(args.base_url)
    if args.isolate:
        m.isolate(True)
    m.timeout(args.timeout)
    m.user_agent(args.user_agent)
    m.ignore_network_errors(args.ignore_network_errors)
    m.insecure(args.insecure)
    m.make_dirs(args.make_dirs)
    if m.ok():
        if args.output:
            m.save(args.output)
            if not args.silent:
                sys.stderr.write(_ansi("32", "saved", use_color) + " %d bytes -> %s\n" % (len(m.data), args.output))
                sys.stderr.flush()
        else:
            sys.stdout.buffer.write(m.data)
            sys.stdout.buffer.flush()
        return 0
    if not args.silent:
        sys.stderr.write(_ansi("31", "error", use_color) + ": %s\n" % m.error)
        sys.stderr.flush()
    return 1


if __name__ == "__main__":
    sys.exit(main())
