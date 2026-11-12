#!/data/data/com.termux/files/usr/bin/env python
"""rlater - a local-first, terminal-native "read it later" app.

Save a URL into a local SQLite database, strip the page down to its readable
core (Reader-Mode style), and read it later in your terminal through `less`.

Requires Python 3.12+. Standard library only (SQLite FTS5 is used for search
when your Python's SQLite build has it, which almost all do).

Quick start
-----------
    rlater add https://example.com/some-article        # save
    echo https://example.com/post | rlater add          # pipe a URL in
    xclip -o | rlater add -t to-read                    # or pbpaste on macOS
    curl -s URL | rlater add --html - URL               # you fetched the HTML
    rlater ls                                           # unread queue
    rlater read 3                                       # read #3 in $PAGER
    rlater read                                         # read the oldest unread
    rlater search "sqlite wal"                          # full-text search
    rlater ls --ids -t python | xargs rlater archive    # scripting-friendly

Data lives in $RLATER_DB, or $XDG_DATA_HOME/rlater/rlater.db
(~/.local/share/rlater/rlater.db). Run `rlater COMMAND -h` for details.
"""

from __future__ import annotations

import argparse
import codecs
import contextlib
import json
import math
import os
import random
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import webbrowser
import zlib
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Final, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

__version__ = "1.0.0"
APP: Final = "rlater"
DEFAULT_UA: Final = f"Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0 {APP}/{__version__}"
WORDS_PER_MINUTE: Final = 230

sys.setrecursionlimit(max(sys.getrecursionlimit(), 4000))


# =============================================================================
# Small helpers
# =============================================================================


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def die(message: str, code: int = 1) -> NoReturn:
    print(f"{APP}: {message}", file=sys.stderr)
    raise SystemExit(code)


def warn(message: str) -> None:
    print(f"{APP}: {message}", file=sys.stderr)


def truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def clean_ws(text: str) -> str:
    return " ".join(text.split())


def default_db_path() -> Path:
    if env := os.environ.get("RLATER_DB"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP / "rlater.db"


class Style:
    """Tiny ANSI helper that becomes a no-op when colour is disabled."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not text:
            return text
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def color_enabled(no_color_flag: bool) -> bool:
    return not no_color_flag and "NO_COLOR" not in os.environ and sys.stdout.isatty()


def terminal_width(requested: int | None) -> int:
    if requested:
        return max(20, requested)
    env = os.environ.get("RLATER_WIDTH", "")
    if env.isdigit():
        return max(20, int(env))
    cols = shutil.get_terminal_size((80, 24)).columns
    return max(30, min(cols - 2, 80))


def page(text: str, enabled: bool) -> None:
    """Send *text* through a pager when stdout is a terminal."""
    if not text.endswith("\n"):
        text += "\n"
    if not enabled or not sys.stdout.isatty():
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    pager = os.environ.get("RLATER_PAGER") or os.environ.get("PAGER") or "less"
    try:
        argv = shlex.split(pager)
    except ValueError:
        argv = ["less"]
    if not argv or shutil.which(argv[0]) is None:
        sys.stdout.write(text)
        return
    if Path(argv[0]).name == "less":
        argv += ["-R", "-F", "-i"]  # colours, quit if one screen, smart-case search
    env = os.environ.copy()
    env.setdefault("LESSCHARSET", "utf-8")
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    try:
        proc.communicate(text)
    except KeyboardInterrupt:
        pass
    while True:  # let the pager finish even if Ctrl-C hit us first
        try:
            proc.wait()
            break
        except KeyboardInterrupt:
            continue


# =============================================================================
# URL handling
# =============================================================================

TRACKING_PARAM: Final = re.compile(
    r"^(utm_.*|fbclid|gclid|dclid|msclkid|yclid|mc_cid|mc_eid|igshid|ref_src|"
    r"_hsenc|_hsmi|mkt_tok|oly_.*|vero_.*|spm|share)$",
    re.IGNORECASE,
)
URL_RE: Final = re.compile(r"""https?://[^\s<>"'`]+""", re.IGNORECASE)


def normalize_url(raw: str) -> str:
    """Canonicalise a URL: add scheme, lowercase host, drop fragment + trackers."""
    raw = raw.strip()
    if not raw:
        raise ValueError("empty URL")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not an http(s) URL: {raw!r}")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAM.match(k)]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def extract_urls(text: str) -> list[str]:
    """Pull URLs out of arbitrary text: one per line, or embedded in HTML/prose."""
    found: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        matches = [m.rstrip(".,;:)]}>") for m in URL_RE.findall(line)]
        if matches:
            found.extend(matches)
        elif " " not in line and "." in line:
            found.append(line)
    return found


def dedupe(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(items))


def normalize_tags(raw: Sequence[str]) -> list[str]:
    tags: list[str] = []
    for chunk in raw:
        for piece in chunk.split(","):
            tag = re.sub(r"\s+", "-", piece.strip().lower())
            if tag:
                tags.append(tag)
    return dedupe(tags)


# =============================================================================
# Fetching
# =============================================================================


class FetchError(Exception):
    """Raised for any recoverable problem while downloading a page."""


@dataclass(frozen=True, slots=True)
class FetchOptions:
    timeout: float = 20.0
    max_bytes: int = 10 * 1024 * 1024
    user_agent: str = DEFAULT_UA
    jobs: int = 4


@dataclass(frozen=True, slots=True)
class Page:
    final_url: str
    text: str
    content_type: str


META_CHARSET: Final = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""", re.IGNORECASE)


def decode_html(raw: bytes, declared: str | None = None) -> str:
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig", errors="replace")
    candidates: list[str] = []
    if declared:
        candidates.append(declared)
    if m := META_CHARSET.search(raw[:4096]):
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    for encoding in candidates:
        try:
            codecs.lookup(encoding)
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def fetch(url: str, opts: FetchOptions) -> Page:
    request = Request(
        url,
        headers={
            "User-Agent": opts.user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.5,*/*;q=0.1",
            "Accept-Language": "en;q=0.9,*;q=0.5",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    try:
        with urlopen(request, timeout=opts.timeout) as resp:
            content_type = resp.headers.get_content_type()
            if not (content_type.startswith("text/") or "xml" in content_type):
                raise FetchError(f"unsupported content type: {content_type}")
            raw = resp.read(opts.max_bytes + 1)
            if len(raw) > opts.max_bytes:
                raise FetchError(f"page larger than {opts.max_bytes // (1024 * 1024)} MB")
            encoding = (resp.headers.get("Content-Encoding") or "").lower()
            if encoding in ("gzip", "x-gzip", "deflate"):
                wbits = zlib.MAX_WBITS | 16 if "gzip" in encoding else zlib.MAX_WBITS
                try:
                    raw = zlib.decompressobj(wbits).decompress(raw, opts.max_bytes)
                except zlib.error as exc:
                    raise FetchError(f"bad compressed response: {exc}") from exc
            return Page(resp.geturl(), decode_html(raw, resp.headers.get_content_charset()), content_type)
    except HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise FetchError(f"{exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc


# =============================================================================
# Tiny, forgiving HTML tree
# =============================================================================

VOID: Final = frozenset("area base br col embed hr img input link meta param source track wbr".split())
HEADINGS: Final = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
P_CLOSERS: Final = (
    frozenset("div ul ol table blockquote pre section article aside header footer nav figure form hr".split())
    | HEADINGS
)
AUTO_CLOSE: Final = frozenset("p li dt dd tr td th option thead tbody".split())
MAX_DEPTH: Final = 120


@dataclass(eq=False, slots=True)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    parent: Node | None = None
    children: list[Node | str] = field(default_factory=list)

    def attr(self, name: str) -> str:
        return self.attrs.get(name, "")


class TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        top = self.stack[-1]
        if len(self.stack) > 1 and (tag in AUTO_CLOSE and top.tag == tag or tag in P_CLOSERS and top.tag == "p"):
            self.stack.pop()
        node = Node(tag, {k: v or "" for k, v in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID and len(self.stack) < MAX_DEPTH:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


def iter_nodes(root: Node) -> Iterator[Node]:
    """Yield *root* and all descendant elements in document order."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in reversed(node.children) if isinstance(c, Node))


def text_of(node: Node) -> str:
    parts: list[str] = []

    def walk(n: Node) -> None:
        for child in n.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                walk(child)

    walk(node)
    return clean_ws("".join(parts))


def raw_text(node: Node) -> str:
    """Text with whitespace preserved (for <pre>)."""
    parts: list[str] = []

    def walk(n: Node) -> None:
        for child in n.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag == "br":
                parts.append("\n")
            else:
                walk(child)

    walk(node)
    return "".join(parts)


# =============================================================================
# Clutter removal + main-content detection
# =============================================================================

DROP_TAGS: Final = frozenset(
    "script style noscript template svg canvas iframe object embed button select textarea input "
    "nav footer aside dialog audio video map datalist head title meta link base".split()
)
NOISE_ROLES: Final = frozenset({"navigation", "banner", "contentinfo", "complementary", "search", "dialog"})
NEG_TOKENS: Final = frozenset(
    "comment comments sidebar footer nav navbar navigation menu ad ads advert advertisement promo "
    "share sharing social cookie cookies popup modal newsletter subscribe subscription related "
    "breadcrumb breadcrumbs toolbar banner outbrain taboola sponsored recommended".split()
)
POS_TOKENS: Final = frozenset("article content post entry story body main text hentry prose".split())
TOKEN_SPLIT: Final = re.compile(r"[^a-z0-9]+")
HIDDEN_STYLE: Final = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
SCORE_TAGS: Final = frozenset({"p", "pre", "td", "blockquote"})
TAG_WEIGHT: Final = {
    "article": 10.0, "div": 5.0, "section": 3.0, "pre": 3.0, "blockquote": 3.0, "td": 3.0,
    "form": -3.0, "ul": -3.0, "ol": -3.0, "dl": -3.0, "li": -3.0, "address": -3.0, "th": -5.0,
}  # fmt: skip


def class_tokens(node: Node) -> set[str]:
    return set(TOKEN_SPLIT.split(f"{node.attr('class')} {node.attr('id')}".lower())) - {""}


def is_noise(node: Node, in_article: bool) -> bool:
    tag = node.tag
    if tag in DROP_TAGS or (tag == "header" and not in_article):
        return True
    if "hidden" in node.attrs or node.attr("aria-hidden") == "true" or HIDDEN_STYLE.search(node.attr("style")):
        return True
    if node.attr("role") in NOISE_ROLES:
        return True
    if tag in ("article", "main", "body", "html"):
        return False
    tokens = class_tokens(node)
    return bool(tokens & NEG_TOKENS) and not tokens & POS_TOKENS


def prune(node: Node, in_article: bool = False) -> None:
    kept: list[Node | str] = []
    for child in node.children:
        if isinstance(child, str):
            kept.append(child)
        elif not is_noise(child, in_article):
            prune(child, in_article or child.tag == "article")
            kept.append(child)
    node.children = kept


def text_stats(node: Node, cache: dict[Node, tuple[int, int]]) -> tuple[int, int]:
    """(total text length, text length inside links) for a subtree, cached."""
    if (hit := cache.get(node)) is not None:
        return hit
    total = link = 0
    for child in node.children:
        if isinstance(child, str):
            total += len(child.strip())
        else:
            t, lk = text_stats(child, cache)
            total += t
            link += t if child.tag == "a" else lk
    cache[node] = (total, link)
    return total, link


def link_density(node: Node, cache: dict[Node, tuple[int, int]]) -> float:
    total, link = text_stats(node, cache)
    return link / total if total else 1.0


def direct_text_len(node: Node) -> int:
    return sum(len(c.strip()) for c in node.children if isinstance(c, str))


def find_main(body: Node) -> Node:
    """Pick the subtree most likely to be the article (Readability-style scoring)."""
    cache: dict[Node, tuple[int, int]] = {}
    scores: dict[Node, float] = {}

    def initial(node: Node) -> float:
        score = TAG_WEIGHT.get(node.tag, 0.0)
        if class_tokens(node) & POS_TOKENS:
            score += 25.0
        if node.attr("itemprop").lower() == "articlebody":
            score += 30.0
        return score

    for node in iter_nodes(body):
        if node.tag not in SCORE_TAGS and not (node.tag == "div" and direct_text_len(node) >= 80):
            continue
        length = text_stats(node, cache)[0]
        if length < 25:
            continue
        points = 1.0 + min(length / 100.0, 3.0)
        ancestor = node.parent
        for divisor in (1.0, 2.0, 3.0):
            if ancestor is None or ancestor.tag in ("html", "#root"):
                break
            scores.setdefault(ancestor, initial(ancestor))
            scores[ancestor] += points / divisor
            ancestor = ancestor.parent
    if not scores:
        return body
    best = max(scores, key=lambda n: scores[n] * (1.0 - link_density(n, cache)))
    best_len = text_stats(best, cache)[0]
    # An <article> of similar size is a cleaner choice than a generic wrapper.
    articles = [n for n in iter_nodes(body) if n.tag == "article"]
    if articles:
        top = max(articles, key=lambda n: text_stats(n, cache)[0])
        length = text_stats(top, cache)[0]
        if length >= 400 and 0.6 * best_len <= length <= 1.5 * best_len and link_density(top, cache) < 0.5:
            return top
    return best


# =============================================================================
# Node tree -> light Markdown
# =============================================================================

BLOCK_TAGS: Final = frozenset(
    "address article aside blockquote body center dd details div dl dt fieldset figcaption figure "
    "footer form header hgroup html main nav section summary tbody thead tfoot caption p "
    "table tr td th #root".split()
)
BLOCKISH: Final = BLOCK_TAGS | HEADINGS | {"ul", "ol", "li", "pre", "hr"}
MD_START: Final = re.compile(r"^(#{1,6} |[-*+] |\d+[.)] |>|\||`{3})")
HEADING_MARK: Final = re.compile(r"^#{1,6} ")
LINK_RE: Final = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
BULLET_RE: Final = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
QUOTE_PREFIX: Final = re.compile(r"^(?:>\s?)+")


def has_block_child(node: Node) -> bool:
    return any(isinstance(c, Node) and (c.tag in BLOCKISH or has_block_child(c)) for c in node.children)


class Renderer:
    """Walks a pruned node tree and emits (kind, text) blocks."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url
        self.blocks: list[tuple[str, str]] = []
        self.buf: list[str] = []
        self.prefix = ""
        self.lists: list[list[int]] = []  # [ordered?, counter]

    # -- emission ----------------------------------------------------------
    def flush(self, heading: int = 0) -> None:
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf.clear()
        prefix, self.prefix = self.prefix, ""
        if not text:
            return
        if heading:
            self.blocks.append(("h", "#" * heading + " " + text))
        elif prefix:
            self.blocks.append(("li", prefix + text))
        else:
            self.blocks.append(("p", "\\" + text if MD_START.match(text) else text))

    def flat_text(self, node: Node) -> str:
        sub = Renderer(self.base)
        sub.walk(node)
        sub.flush()
        return " ".join(HEADING_MARK.sub("", t) if k == "h" else t for k, t in sub.blocks)

    def markdown(self) -> str:
        out: list[str] = []
        previous = ""
        for kind, text in self.blocks:
            if kind == "code":
                text = f"```\n{text}\n```"
            if out:
                out.append("\n" if kind == previous and kind in ("li", "table") else "\n\n")
            out.append(text)
            previous = kind
        return "".join(out)

    # -- traversal ---------------------------------------------------------
    def walk(self, node: Node) -> None:
        for child in node.children:
            if isinstance(child, str):
                self.buf.append(child)
            else:
                self.element(child)

    def element(self, n: Node) -> None:
        tag = n.tag
        if tag in DROP_TAGS:
            return
        if tag == "br":
            self.buf.append(" ")
        elif tag in HEADINGS:
            self.flush()
            self.walk(n)
            self.flush(heading=int(tag[1]))
        elif tag in ("ul", "ol"):
            self.flush()
            self.lists.append([int(tag == "ol"), 0])
            self.walk(n)
            self.flush()
            self.lists.pop()
        elif tag == "li":
            self.flush()
            if self.lists:
                state = self.lists[-1]
                state[1] += 1
                marker = f"{state[1]}. " if state[0] else "- "
            else:
                marker = "- "
            self.prefix = "  " * max(len(self.lists) - 1, 0) + marker
            self.walk(n)
            self.flush()
        elif tag == "pre":
            code = raw_text(n).strip("\n")
            if code.strip():
                self.flush()
                self.blocks.append(("code", code))
        elif tag == "blockquote":
            sub = Renderer(self.base)
            sub.walk(n)
            sub.flush()
            md = sub.markdown()
            if md:
                self.flush()
                self.blocks.append(("quote", "\n".join(f"> {ln}" if ln else ">" for ln in md.splitlines())))
        elif tag == "hr":
            self.flush()
            self.blocks.append(("p", "---"))
        elif tag == "img":
            alt = clean_ws(n.attr("alt"))
            if len(alt) >= 12:
                self.flush()
                self.blocks.append(("p", f"[image: {alt}]"))
        elif tag == "a":
            self.link(n)
        elif tag == "code":
            inner = self.flat_text(n)
            self.buf.append(f"`{inner}`" if inner else "")
        elif tag == "tr":
            self.table_row(n)
        elif tag in BLOCK_TAGS:
            self.flush()
            self.walk(n)
            self.flush()
        else:  # inline: span, strong, em, sup, ...
            self.walk(n)

    def link(self, n: Node) -> None:
        href = n.attr("href").strip()
        inner = self.flat_text(n).replace("[", "(").replace("]", ")")
        if not inner:
            return
        if href and not href.lower().startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            target = urljoin(self.base, href)
            if urlsplit(target).scheme in ("http", "https"):
                target = target.replace(" ", "%20").replace(")", "%29")
                self.buf.append(f"[{inner}]({target})")
                return
        self.buf.append(inner)

    def table_row(self, n: Node) -> None:
        cells = [c for c in n.children if isinstance(c, Node) and c.tag in ("td", "th")]
        self.flush()
        if any(has_block_child(c) for c in cells):  # layout table: treat cells as blocks
            for cell in cells:
                self.walk(cell)
                self.flush()
            return
        texts = [t for c in cells if (t := self.flat_text(c))]
        if texts:
            self.blocks.append(("table", "| " + " | ".join(texts) + " |"))


def render_markdown(node: Node, base_url: str, title: str) -> str:
    renderer = Renderer(base_url)
    renderer.walk(node)
    renderer.flush()
    blocks = renderer.blocks
    norm_title = clean_ws(title).lower()
    for i in range(min(3, len(blocks))):  # drop a leading heading that repeats the title
        kind, text = blocks[i]
        if kind == "h":
            heading = clean_ws(HEADING_MARK.sub("", text)).lower()
            if heading and (heading in norm_title or norm_title in heading):
                del blocks[i]
                break
    return renderer.markdown()


def count_words(markdown: str) -> int:
    return len(re.findall(r"[\w'’-]+", LINK_RE.sub(r"\1", markdown)))


# =============================================================================
# Extraction (metadata + content)
# =============================================================================


@dataclass(slots=True)
class Extracted:
    url: str
    final_url: str
    title: str
    author: str
    site: str
    published: str
    content: str
    word_count: int


def first_meta(meta: dict[str, str], *keys: str) -> str:
    return next((clean_ws(meta[k]) for k in keys if meta.get(k)), "")


def extract(page_text: str, url: str, final_url: str) -> Extracted:
    builder = TreeBuilder()
    try:
        builder.feed(page_text)
        builder.close()
    except Exception:  # noqa: BLE001 - keep whatever was parsed so far
        pass
    root = builder.root

    meta: dict[str, str] = {}
    title_node = h1_node = time_node = body = None
    for node in iter_nodes(root):
        if node.tag == "meta":
            key = (node.attr("property") or node.attr("name") or node.attr("itemprop")).lower()
            if key and node.attr("content").strip():
                meta.setdefault(key, node.attr("content").strip())
        elif node.tag == "title" and title_node is None:
            title_node = node
        elif node.tag == "h1" and h1_node is None:
            h1_node = node
        elif node.tag == "time" and time_node is None and node.attr("datetime"):
            time_node = node
        elif node.tag == "body" and body is None:
            body = node

    host = (urlsplit(final_url).hostname or "").removeprefix("www.")
    doc_title = text_of(title_node) if title_node else ""
    h1 = text_of(h1_node) if h1_node else ""
    title = first_meta(meta, "og:title", "twitter:title")
    if not title:
        if h1 and len(h1) < 200 and (not doc_title or h1.lower() in doc_title.lower()):
            title = h1
        else:
            title = doc_title or h1
    title = title or host or url

    author = first_meta(meta, "author", "article:author", "parsely-author", "dc.creator", "twitter:creator")
    if author.startswith("http"):
        author = ""
    published = first_meta(
        meta, "article:published_time", "og:published_time", "datepublished", "date", "dc.date", "pubdate"
    ) or (time_node.attr("datetime") if time_node else "")
    if re.match(r"^\d{4}-\d{2}-\d{2}", published):
        published = published[:10]
    site = first_meta(meta, "og:site_name", "application-name") or host

    body = body or root
    prune(body)
    main = find_main(body)
    markdown = render_markdown(main, final_url, title)
    words = count_words(markdown)
    if words < 80 and main is not body:  # detection may have latched onto a fragment
        fallback = render_markdown(body, final_url, title)
        fallback_words = count_words(fallback)
        if fallback_words > words * 2:
            markdown, words = fallback, fallback_words
    return Extracted(url, final_url, title, author, site, published[:40], markdown, words)


def extract_plain(text: str, url: str, final_url: str) -> Extracted:
    host = (urlsplit(final_url).hostname or "").removeprefix("www.")
    name = Path(urlsplit(final_url).path).name or host
    body = text.strip()
    return Extracted(url, final_url, name, "", host, "", body, count_words(body))


def acquire(url: str, opts: FetchOptions, html_override: str | None = None) -> Extracted | FetchError:
    """Download (or take supplied HTML) and extract. Never raises."""
    try:
        if html_override is not None:
            return extract(html_override, url, url)
        got = fetch(url, opts)
        if got.content_type == "text/plain":
            return extract_plain(got.text, url, got.final_url)
        return extract(got.text, url, got.final_url)
    except FetchError as exc:
        return exc
    except Exception as exc:  # noqa: BLE001 - a bad page must not kill a batch
        return FetchError(f"{type(exc).__name__}: {exc}")


def reading_minutes(words: int) -> int:
    return max(1, math.ceil(words / WORDS_PER_MINUTE))


# =============================================================================
# Database
# =============================================================================

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY,
    url         TEXT NOT NULL UNIQUE,
    final_url   TEXT,
    title       TEXT NOT NULL,
    author      TEXT NOT NULL DEFAULT '',
    site        TEXT NOT NULL DEFAULT '',
    published   TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL,
    word_count  INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    read_at     TEXT,
    archived    INTEGER NOT NULL DEFAULT 0,
    starred     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tags (
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    PRIMARY KEY (article_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_articles_added ON articles(added_at);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
"""

FTS_SCHEMA: Final = """
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, content, author, site,
    content='articles', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, content, author, site)
    VALUES (new.id, new.title, new.content, new.author, new.site);
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, content, author, site)
    VALUES ('delete', old.id, old.title, old.content, old.author, old.site);
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE OF title, content, author, site ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, content, author, site)
    VALUES ('delete', old.id, old.title, old.content, old.author, old.site);
    INSERT INTO articles_fts(rowid, title, content, author, site)
    VALUES (new.id, new.title, new.content, new.author, new.site);
END;
"""

FLAG_ACTIONS: Final[dict[str, tuple[str, str]]] = {
    "archive": ("archived = 1", "archived"),
    "unarchive": ("archived = 0", "unarchived"),
    "star": ("starred = 1", "starred"),
    "unstar": ("starred = 0", "unstarred"),
    "unread": ("read_at = NULL", "marked unread"),
    "done": ("read_at = COALESCE(read_at, :now)", "marked read"),
}


@dataclass(frozen=True, slots=True)
class Hit:
    id: int
    title: str
    site: str
    archived: bool
    snippet: str


SNIP_OPEN: Final = "\x02"
SNIP_CLOSE: Final = "\x03"


def tidy_snippet(snippet: str, mark: tuple[str, str]) -> str:
    """Hide Markdown link targets in a search snippet and apply highlight markers."""
    snippet = re.sub(r"\]\(https?://[^)]*\)?", "", snippet)  # "](url)" (maybe truncated)
    snippet = re.sub(r"\(https?://\S*", "", snippet)
    snippet = snippet.replace("[", "")
    return snippet.replace(SNIP_OPEN, mark[0]).replace(SNIP_CLOSE, mark[1])


class Library:
    """Thin wrapper around the SQLite database."""

    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        with contextlib.suppress(sqlite3.DatabaseError):
            self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()
        self.has_fts = bool(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'articles_fts'").fetchone())

    def _migrate(self) -> None:
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version >= 1:
            return
        with self.conn:
            self.conn.executescript(SCHEMA)
        try:
            with self.conn:
                self.conn.executescript(FTS_SCHEMA)
        except sqlite3.OperationalError:
            warn("SQLite FTS5 is unavailable; search will fall back to slow substring matching")
        self.conn.execute("PRAGMA user_version = 1")

    def close(self) -> None:
        self.conn.close()

    # -- reading -----------------------------------------------------------
    def find(self, *urls: str) -> sqlite3.Row | None:
        clauses = " OR ".join(["url = ? OR final_url = ?"] * len(urls))
        params = [u for url in urls for u in (url, url)]
        return self.conn.execute(f"SELECT id, url, title FROM articles WHERE {clauses}", params).fetchone()

    def get(self, ids: Sequence[int], *, quiet: bool = False) -> list[sqlite3.Row]:
        rows: list[sqlite3.Row] = []
        for article_id in ids:
            row = self.conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
            if row is None:
                if not quiet:
                    warn(f"no article with id {article_id}")
            else:
                rows.append(row)
        return rows

    def tags_of(self, article_id: int) -> list[str]:
        cur = self.conn.execute("SELECT tag FROM tags WHERE article_id = ? ORDER BY tag", (article_id,))
        return [r["tag"] for r in cur]

    def all_tags(self) -> list[tuple[str, int]]:
        cur = self.conn.execute("SELECT tag, COUNT(*) AS n FROM tags GROUP BY tag ORDER BY n DESC, tag")
        return [(r["tag"], r["n"]) for r in cur]

    def pick(self, mode: str) -> int | None:
        base = "SELECT id FROM articles WHERE read_at IS NULL AND archived = 0"
        order = {"oldest": "ORDER BY added_at ASC, id ASC", "newest": "ORDER BY added_at DESC, id DESC",
                 "random": "ORDER BY RANDOM()", "shortest": "ORDER BY word_count ASC"}[mode]  # fmt: skip
        row = self.conn.execute(f"{base} {order} LIMIT 1").fetchone()
        return None if row is None else int(row["id"])

    # -- writing -----------------------------------------------------------
    def save(self, ex: Extracted, article_id: int | None = None) -> int:
        stamp = now_iso()
        with self.conn:
            if article_id is None:
                cur = self.conn.execute(
                    "INSERT INTO articles (url, final_url, title, author, site, published, content, "
                    "word_count, added_at, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ex.url, ex.final_url, ex.title, ex.author, ex.site, ex.published, ex.content,
                     ex.word_count, stamp, stamp),
                )  # fmt: skip
                assert cur.lastrowid is not None
                return cur.lastrowid
            self.conn.execute(
                "UPDATE articles SET final_url=?, title=?, author=?, site=?, published=?, content=?, "
                "word_count=?, fetched_at=? WHERE id=?",
                (ex.final_url, ex.title, ex.author, ex.site, ex.published, ex.content,
                 ex.word_count, stamp, article_id),
            )  # fmt: skip
            return article_id

    def add_tags(self, article_id: int, tags: Sequence[str]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO tags (article_id, tag) VALUES (?, ?)", [(article_id, t) for t in tags]
            )

    def remove_tags(self, article_id: int, tags: Sequence[str]) -> None:
        with self.conn:
            self.conn.executemany("DELETE FROM tags WHERE article_id = ? AND tag = ?", [(article_id, t) for t in tags])

    def apply_flag(self, action: str, ids: Sequence[int]) -> int:
        clause = FLAG_ACTIONS[action][0]
        changed = 0
        with self.conn:
            for article_id in ids:
                cur = self.conn.execute(
                    f"UPDATE articles SET {clause} WHERE id = :id", {"id": article_id, "now": now_iso()}
                )
                changed += cur.rowcount
        return changed

    def mark_read(self, ids: Sequence[int]) -> None:
        self.apply_flag("done", ids)

    def delete(self, ids: Sequence[int]) -> int:
        with self.conn:
            return sum(self.conn.execute("DELETE FROM articles WHERE id = ?", (i,)).rowcount for i in ids)

    # -- search ------------------------------------------------------------
    def search(self, query: str, limit: int, *, raw: bool, mark: tuple[str, str]) -> list[Hit]:
        hits = self._search(query, limit, raw=raw)
        return [Hit(h.id, h.title, h.site, h.archived, tidy_snippet(h.snippet, mark)) for h in hits]

    def _search(self, query: str, limit: int, *, raw: bool) -> list[Hit]:
        mark = (SNIP_OPEN, SNIP_CLOSE)
        if self.has_fts:
            match = query if raw else " ".join('"' + t.replace('"', '""') + '"*' for t in query.split())
            try:
                cur = self.conn.execute(
                    "SELECT a.id, a.title, a.site, a.archived, snippet(articles_fts, -1, ?, ?, '…', 16) AS snip "
                    "FROM articles_fts JOIN articles a ON a.id = articles_fts.rowid "
                    "WHERE articles_fts MATCH ? ORDER BY bm25(articles_fts, 8.0, 1.0, 2.0, 1.0) LIMIT ?",
                    (mark[0], mark[1], match, limit),
                )
                return [Hit(r["id"], r["title"], r["site"], bool(r["archived"]), clean_ws(r["snip"] or ""))
                        for r in cur]  # fmt: skip
            except sqlite3.OperationalError as exc:
                die(f"bad search query ({exc}); try again without --raw")
        tokens = query.lower().split()
        where = " AND ".join(["(lower(title) LIKE ? OR lower(content) LIKE ?)"] * len(tokens))
        params: list[str | int] = [p for t in tokens for p in (f"%{t}%", f"%{t}%")]
        cur = self.conn.execute(
            f"SELECT id, title, site, archived, content FROM articles WHERE {where} ORDER BY added_at DESC LIMIT ?",
            [*params, limit],
        )
        hits: list[Hit] = []
        for r in cur:
            text = clean_ws(r["content"])
            pos = text.lower().find(tokens[0])
            start = max(pos - 60, 0) if pos >= 0 else 0
            hits.append(Hit(r["id"], r["title"], r["site"], bool(r["archived"]), text[start : start + 160]))
        return hits


# =============================================================================
# Terminal rendering of an article
# =============================================================================


def render_terminal(row: sqlite3.Row, tags: Sequence[str], width: int, style: Style, show_links: bool) -> str:
    refs: dict[str, int] = {}

    def strip_links(text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            label, url = m.group(1), m.group(2)
            if not show_links:
                return label
            return f"{label}[{refs.setdefault(url, len(refs) + 1)}]"

        return LINK_RE.sub(repl, text)

    def wrap(text: str, initial: str = "", subsequent: str = "") -> str:
        return textwrap.fill(
            text, width=width, initial_indent=initial, subsequent_indent=subsequent,
            break_long_words=False, break_on_hyphens=False,
        )  # fmt: skip

    out: list[str] = []

    # Header -----------------------------------------------------------------
    out.append(style(wrap(row["title"]), "1"))
    bits = [row["site"], row["author"], row["published"], f"{reading_minutes(row['word_count'])} min read"]
    out.append(style(" · ".join(b for b in bits if b), "2"))
    out.append(style(row["url"], "2", "4"))
    if tags:
        out.append(style("tags: " + ", ".join(tags), "2"))
    out.append(style("─" * min(width, 72), "2"))
    out.append("")

    # Body -------------------------------------------------------------------
    for kind, text in parse_blocks(row["content"]):
        if kind == "h":
            level = len(text) - len(text.lstrip("#"))
            heading = strip_links(text[level:].strip())
            wrapped = wrap(heading)
            out.append(style(wrapped, "1", "36" if level <= 2 else "1"))
            if level <= 2:
                out.append(style("─" * min(max(map(len, wrapped.splitlines())), width), "2"))
        elif kind == "li":
            lines: list[str] = []
            for line in text.splitlines():
                if m := BULLET_RE.match(line):
                    indent, marker, rest = m.groups()
                    bullet = "•" if marker in "-*+" else marker
                    lead = f"{indent}{bullet} "
                    lines.append(wrap(strip_links(rest), lead, " " * len(lead)))
                else:
                    lines.append(wrap(strip_links(line), "  ", "  "))
            out.append("\n".join(lines))
        elif kind == "quote":
            level = QUOTE_PREFIX.match(text.splitlines()[0]).group(0).count(">") if text else 1  # type: ignore[union-attr]
            bar = "│ " * level
            paras: list[list[str]] = [[]]
            for line in text.splitlines():
                stripped = QUOTE_PREFIX.sub("", line).strip()
                if stripped:
                    paras[-1].append(stripped)
                else:
                    paras.append([])
            rendered = [wrap(strip_links(" ".join(p)), bar, bar) for p in paras if p]
            out.append(style(("\n" + bar.rstrip() + "\n").join(rendered), "2"))
        elif kind == "code":
            out.append(style("\n".join("    " + ln for ln in text.splitlines()), "33"))
        elif kind == "table":
            rows = ["  │  ".join(c.strip() for c in ln.strip().strip("|").split(" | ")) for ln in text.splitlines()]
            out.append("\n".join(rows))
        else:
            text = text[1:] if text.startswith("\\") and MD_START.match(text[1:]) else text
            if text == "---":
                out.append(style("─" * min(width, 40), "2"))
            else:
                out.append(wrap(strip_links(text)))
        out.append("")

    if refs:
        out.append(style("References", "1"))
        for url, number in refs.items():
            out.append(style(f"[{number}] {url}", "2"))
        out.append("")
    return "\n".join(out)


def parse_blocks(markdown: str) -> list[tuple[str, str]]:
    """Group the stored light-Markdown into (kind, text) blocks."""
    blocks: list[tuple[str, str]] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("```"):
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", "\n".join(code)))
        else:
            chunk: list[str] = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith("```"):
                chunk.append(lines[i])
                i += 1
            text = "\n".join(chunk)
            if HEADING_MARK.match(text):
                kind = "h"
            elif text.startswith(">"):
                kind = "quote"
            elif text.startswith("|"):
                kind = "table"
            elif BULLET_RE.match(chunk[0]):
                kind = "li"
            else:
                kind = "p"
                text = " ".join(chunk)
            blocks.append((kind, text))
    return blocks


# =============================================================================
# Commands
# =============================================================================


def parse_ids(values: Sequence[str]) -> list[int]:
    """Accept `3`, `#3`, `1,2,5`, ranges `4-9`, and `-` to read ids from stdin."""
    tokens: list[str] = []
    for value in values:
        tokens.extend(sys.stdin.read().split() if value == "-" else value.replace(",", " ").split())
    ids: list[int] = []
    for token in tokens:
        token = token.lstrip("#")
        lo, sep, hi = token.partition("-")
        if sep and lo.isdigit() and hi.isdigit():
            ids.extend(range(int(lo), min(int(hi), int(lo) + 10_000) + 1))
        elif token.isdigit():
            ids.append(int(token))
        else:
            die(f"not an article id: {token!r}")
    return dedupe_ints(ids)


def dedupe_ints(ids: Sequence[int]) -> list[int]:
    return list(dict.fromkeys(ids))


def fetch_options(args: argparse.Namespace) -> FetchOptions:
    return FetchOptions(
        timeout=args.timeout,
        max_bytes=int(args.max_size * 1024 * 1024),
        user_agent=args.user_agent,
        jobs=max(1, args.jobs),
    )


def add_urls(
    lib: Library,
    urls: Sequence[str],
    *,
    tags: Sequence[str],
    star: bool,
    force: bool,
    opts: FetchOptions,
    html_override: str | None = None,
    title: str | None = None,
) -> tuple[list[int], int]:
    """Fetch + store each URL. Returns (article ids touched, failure count)."""
    ids: list[int] = []
    failures = 0
    todo: list[tuple[str, int | None]] = []
    for raw in urls:
        try:
            url = normalize_url(raw)
        except ValueError as exc:
            warn(f"skipping: {exc}")
            failures += 1
            continue
        existing = lib.find(url)
        if existing and not force:
            print(f"= #{existing['id']}  already saved: {truncate(existing['title'], 70)}")
            lib.add_tags(existing["id"], tags)
            if star:
                lib.apply_flag("star", [existing["id"]])
            ids.append(existing["id"])
        else:
            todo.append((url, existing["id"] if existing else None))
    if not todo:
        return ids, failures

    def work(item: tuple[str, int | None]) -> Extracted | FetchError:
        return acquire(item[0], opts, html_override)

    if opts.jobs > 1 and len(todo) > 1:
        with ThreadPoolExecutor(max_workers=min(opts.jobs, len(todo))) as pool:
            results = pool.map(work, todo)
            outcome = list(zip(todo, results, strict=True))
    else:
        outcome = [(item, work(item)) for item in todo]

    for (url, known_id), result in outcome:
        if isinstance(result, FetchError):
            warn(f"✗ {url}: {result}")
            failures += 1
            continue
        if title:
            result.title = title
        article_id = known_id
        if article_id is None and result.final_url != url:
            if dup := lib.find(result.final_url):
                print(f"= #{dup['id']}  already saved (redirects there): {truncate(dup['title'], 60)}")
                lib.add_tags(dup["id"], tags)
                ids.append(dup["id"])
                continue
        article_id = lib.save(result, article_id)
        lib.add_tags(article_id, tags)
        if star:
            lib.apply_flag("star", [article_id])
        ids.append(article_id)
        meta = " · ".join(b for b in (result.site, f"{reading_minutes(result.word_count)} min") if b)
        print(f"✓ #{article_id}  {truncate(result.title, 70)}  ({meta})")
        if result.word_count < 50:
            warn(f"  note: only {result.word_count} words extracted - the page may need JavaScript or a login")
    return ids, failures


def read_stdin_text() -> str:
    return sys.stdin.buffer.read().decode("utf-8", errors="replace")


def cmd_add(lib: Library, args: argparse.Namespace) -> int:
    html_override: str | None = None
    urls: list[str] = list(args.urls)
    if args.html:
        if len(urls) != 1 or urls[0] == "-":
            die("--html needs exactly one URL argument (the page's address)")
        data = sys.stdin.buffer.read() if args.html == "-" else Path(args.html).read_bytes()
        html_override = decode_html(data)
    elif not urls or urls == ["-"]:
        if sys.stdin.isatty():
            die("no URL given (pass one, or pipe URLs on stdin)", 2)
        urls = extract_urls(read_stdin_text())
        if not urls:
            die("no URLs found on stdin", 2)
    else:
        urls = [u for chunk in urls for u in (extract_urls(chunk) or [chunk])]
    ids, failures = add_urls(
        lib, dedupe(urls), tags=normalize_tags(args.tag), star=args.star, force=args.force,
        opts=fetch_options(args), html_override=html_override, title=args.title,
    )  # fmt: skip
    if args.read and ids:
        do_read(lib, ids[:1] if len(ids) == 1 else ids, width=terminal_width(None), no_color=False,
                links=False, use_pager=True, mark=True, archive=False, raw=False)  # fmt: skip
    return 1 if failures else 0


def cmd_import(lib: Library, args: argparse.Namespace) -> int:
    source = sys.stdin.buffer.read() if args.file == "-" else Path(args.file).read_bytes()
    text = decode_html(source)
    if re.search(r"<a\s", text, re.IGNORECASE):  # bookmarks/Pocket HTML export
        urls = [u.replace("&amp;", "&") for u in re.findall(r"""href=["'](https?://[^"']+)""", text, re.I)]
    else:
        urls = extract_urls(text)
    urls = dedupe(urls)
    if not urls:
        die("no URLs found in input")
    print(f"importing {len(urls)} URLs…", file=sys.stderr)
    _, failures = add_urls(lib, urls, tags=normalize_tags(args.tag), star=False, force=False, opts=fetch_options(args))
    return 1 if failures else 0


def cmd_list(lib: Library, args: argparse.Namespace) -> int:
    where: list[str] = []
    params: list[str | int] = []
    if args.all:
        pass
    elif args.archived:
        where.append("a.archived = 1")
    else:
        where.append("a.archived = 0")
    if args.unread:
        where.append("a.read_at IS NULL")
    if args.read:
        where.append("a.read_at IS NOT NULL")
    if args.starred:
        where.append("a.starred = 1")
    for tag in normalize_tags(args.tag):
        where.append("EXISTS (SELECT 1 FROM tags t WHERE t.article_id = a.id AND t.tag = ?)")
        params.append(tag)
    if args.site:
        where.append("a.site LIKE ?")
        params.append(f"%{args.site}%")
    if args.max_minutes:
        where.append("a.word_count <= ?")
        params.append(args.max_minutes * WORDS_PER_MINUTE)
    order = {
        "newest": "a.added_at DESC, a.id DESC", "oldest": "a.added_at ASC, a.id ASC",
        "title": "a.title COLLATE NOCASE", "longest": "a.word_count DESC", "shortest": "a.word_count ASC",
    }[args.sort]  # fmt: skip
    sql = (
        "SELECT a.id, a.url, a.title, a.site, a.author, a.published, a.added_at, a.read_at, a.archived, "
        "a.starred, a.word_count, (SELECT group_concat(tag, ',') FROM tags WHERE article_id = a.id) AS tags "
        "FROM articles a" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order}"
    )
    if args.limit:
        sql += " LIMIT ?"
        params.append(args.limit)
    rows = lib.conn.execute(sql, params).fetchall()

    if args.ids:
        print("\n".join(str(r["id"]) for r in rows))
        return 0
    if args.json:
        payload = [
            {
                **{k: r[k] for k in r.keys() if k != "tags"},
                "tags": sorted((r["tags"] or "").split(",")) if r["tags"] else [],
            }
            for r in rows
        ]
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    if not rows:
        print("nothing here", file=sys.stderr)
        return 0
    style = Style(color_enabled(args.no_color))
    cols = shutil.get_terminal_size((100, 24)).columns
    for r in rows:
        unread = "●" if r["read_at"] is None else " "
        star = "★" if r["starred"] else " "
        mins = f"{reading_minutes(r['word_count']):>3}m"
        site = truncate(r["site"], 16).ljust(16)
        head = f"{r['id']:>4} {unread}{star} {r['added_at'][:10]} {mins}  {site}  "
        tag_text = f"  [{r['tags']}]" if r["tags"] else ""
        room = max(cols - len(head) - len(tag_text), 20)
        line = style(f"{r['id']:>4}", "1") + f" {unread}{star} " + style(f"{r['added_at'][:10]} {mins}  {site}  ", "2")
        title = truncate(r["title"], room)
        print(line + (style(title, "2") if r["read_at"] else title) + style(tag_text, "36"))
    return 0


def do_read(
    lib: Library, ids: Sequence[int], *, width: int, no_color: bool, links: bool,
    use_pager: bool, mark: bool, archive: bool, raw: bool,
) -> int:  # fmt: skip
    rows = lib.get(ids)
    if not rows:
        return 1
    if raw:
        text = "\n\n".join(f"# {r['title']}\n\n{r['url']}\n\n{r['content']}" for r in rows)
        page(text, False)
    else:
        style = Style(color_enabled(no_color))
        divider = "\n" + style("═" * min(width, 72), "2") + "\n\n"
        text = divider.join(render_terminal(r, lib.tags_of(r["id"]), width, style, links) for r in rows)
        page(text, use_pager)
    got = [r["id"] for r in rows]
    if mark:
        lib.mark_read(got)
    if archive:
        lib.apply_flag("archive", got)
    return 0


def cmd_read(lib: Library, args: argparse.Namespace) -> int:
    ids = parse_ids(args.ids)
    if not ids:
        picked = lib.pick(args.pick)
        if picked is None:
            die("nothing unread - the queue is empty")
        ids = [picked]
    return do_read(
        lib, ids, width=terminal_width(args.width), no_color=args.no_color, links=args.links,
        use_pager=not args.no_pager, mark=not args.no_mark, archive=args.archive, raw=args.raw,
    )  # fmt: skip


def cmd_search(lib: Library, args: argparse.Namespace) -> int:
    query = " ".join(args.query).strip()
    if not query:
        die("empty search query", 2)
    style = Style(color_enabled(args.no_color))
    mark = ("\x1b[1;33m", "\x1b[0m") if style.enabled else ("«", "»")
    hits = lib.search(query, args.limit, raw=args.raw, mark=mark)
    if not hits:
        print("no matches", file=sys.stderr)
        return 1
    for hit in hits:
        flag = style(" (archived)", "2") if hit.archived else ""
        print(f"{style(f'{hit.id:>4}', '1')}  {hit.title}{style(f'  — {hit.site}' if hit.site else '', '2')}{flag}")
        if hit.snippet:
            print(
                textwrap.fill(
                    hit.snippet, width=terminal_width(None), initial_indent="      ", subsequent_indent="      "
                )
            )
    return 0


def cmd_flag(action: str) -> Callable[[Library, argparse.Namespace], int]:
    def run(lib: Library, args: argparse.Namespace) -> int:
        ids = parse_ids(args.ids)
        if not ids:
            die("no ids given", 2)
        known = [r["id"] for r in lib.get(ids)]
        lib.apply_flag(action, known)
        print(f"{FLAG_ACTIONS[action][1]}: {', '.join(f'#{i}' for i in known) or 'nothing'}")
        return 0 if len(known) == len(ids) else 1

    return run


def cmd_tag(lib: Library, args: argparse.Namespace) -> int:
    tags = normalize_tags(args.tags)
    if not tags:
        die("no tags given", 2)
    ids = parse_ids([args.id])
    rows = lib.get(ids)
    for row in rows:
        (lib.remove_tags if args.remove else lib.add_tags)(row["id"], tags)
        print(f"#{row['id']}: {', '.join(lib.tags_of(row['id'])) or '(no tags)'}")
    return 0 if len(rows) == len(ids) else 1


def cmd_tags(lib: Library, args: argparse.Namespace) -> int:
    for tag, count in lib.all_tags():
        print(f"{count:>4}  {tag}")
    return 0


def cmd_rm(lib: Library, args: argparse.Namespace) -> int:
    ids = parse_ids(args.ids)
    rows = lib.get(ids)
    if not rows:
        return 1
    if not args.yes:
        if not sys.stdin.isatty():
            die("refusing to delete without --yes when not interactive")
        for r in rows:
            print(f"  #{r['id']}  {truncate(r['title'], 70)}")
        if input(f"delete {len(rows)} article(s)? [y/N] ").strip().lower() not in ("y", "yes"):
            print("aborted")
            return 1
    print(f"deleted {lib.delete([r['id'] for r in rows])} article(s)")
    return 0


def cmd_refetch(lib: Library, args: argparse.Namespace) -> int:
    if args.all:
        ids = [r["id"] for r in lib.conn.execute("SELECT id FROM articles ORDER BY id")]
    else:
        ids = parse_ids(args.ids)
    if not ids:
        die("no ids given (or use --all)", 2)
    urls = [r["url"] for r in lib.get(ids)]
    _, failures = add_urls(lib, urls, tags=[], star=False, force=True, opts=fetch_options(args))
    return 1 if failures else 0


def cmd_open(lib: Library, args: argparse.Namespace) -> int:
    rows = lib.get(parse_ids(args.ids))
    for row in rows:
        if not webbrowser.open(row["url"]):
            warn(f"could not open a browser for {row['url']}")
    return 0 if rows else 1


def slugify(text: str, limit: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "article"


def front_matter(row: sqlite3.Row, tags: Sequence[str]) -> str:
    fields = {
        "title": row["title"], "url": row["url"], "site": row["site"], "author": row["author"],
        "published": row["published"], "saved": row["added_at"], "tags": list(tags),
    }  # fmt: skip
    return "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items()) + "\n---\n"


def cmd_export(lib: Library, args: argparse.Namespace) -> int:
    ids = parse_ids(args.ids)
    if ids:
        rows = lib.get(ids)
    else:
        clause = "" if args.all else " WHERE archived = 0"
        rows = lib.conn.execute(f"SELECT * FROM articles{clause} ORDER BY id").fetchall()
    if not rows:
        die("nothing to export")
    if args.format == "json":
        payload = [{**dict(r), "tags": lib.tags_of(r["id"])} for r in rows]
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"wrote {len(rows)} articles to {args.out}", file=sys.stderr)
        else:
            sys.stdout.write(text)
        return 0
    docs = [(r, f"{front_matter(r, lib.tags_of(r['id']))}\n# {r['title']}\n\n{r['content']}\n") for r in rows]
    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        for r, body in docs:
            (out_dir / f"{r['id']:04d}-{slugify(r['title'])}.md").write_text(body, encoding="utf-8")
        print(f"wrote {len(docs)} files to {out_dir}", file=sys.stderr)
    else:
        sys.stdout.write("\n\n".join(body for _, body in docs))
    return 0


def cmd_backup(lib: Library, args: argparse.Namespace) -> int:
    dest = Path(args.dest).expanduser()
    if dest.exists():
        die(f"{dest} already exists")
    target = sqlite3.connect(dest)
    try:
        lib.conn.backup(target)
    finally:
        target.close()
    print(f"backed up to {dest}")
    return 0


def cmd_stats(lib: Library, args: argparse.Namespace) -> int:
    row = lib.conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(read_at IS NULL AND archived = 0) AS unread, SUM(archived) AS archived, SUM(starred) AS starred, "
        "COALESCE(SUM(word_count), 0) AS words, "
        "COALESCE(SUM(CASE WHEN read_at IS NULL AND archived = 0 THEN word_count END), 0) AS unread_words "
        "FROM articles"
    ).fetchone()
    db_path = Path(lib.conn.execute("PRAGMA database_list").fetchone()["file"] or "")
    size = db_path.stat().st_size if db_path.is_file() else 0
    print(f"articles   {row['total']}")
    print(f"unread     {row['unread'] or 0}  (≈ {math.ceil(row['unread_words'] / WORDS_PER_MINUTE)} min of reading)")
    print(f"archived   {row['archived'] or 0}")
    print(f"starred    {row['starred'] or 0}")
    print(f"words      {row['words']:,}")
    print(f"database   {db_path}  ({size / 1024:.0f} KiB, search: {'FTS5' if lib.has_fts else 'substring'})")
    return 0


# =============================================================================
# CLI wiring
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP,
        description="Local-first read-it-later: save pages to SQLite, read them in your terminal.",
        epilog="Environment: RLATER_DB, RLATER_PAGER (or PAGER), RLATER_WIDTH, RLATER_USER_AGENT, NO_COLOR.\n"
        "Run with no arguments to list your queue, or pipe URLs in to save them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", type=Path, help="database file (default: $RLATER_DB or XDG data dir)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    net = argparse.ArgumentParser(add_help=False)
    net.add_argument("--timeout", type=float, default=20.0, metavar="SEC", help="network timeout (default: 20)")
    net.add_argument("--max-size", type=float, default=10.0, metavar="MB", help="max page size (default: 10)")
    net.add_argument("--user-agent", default=os.environ.get("RLATER_USER_AGENT", DEFAULT_UA), metavar="UA")
    net.add_argument("-j", "--jobs", type=int, default=4, help="parallel downloads (default: 4)")

    def command(name: str, func: Callable[[Library, argparse.Namespace], int], *, aliases: Sequence[str] = (),
                parents: Sequence[argparse.ArgumentParser] = (), help_: str) -> argparse.ArgumentParser:  # fmt: skip
        p = sub.add_parser(name, aliases=list(aliases), parents=list(parents), help=help_, description=help_)
        p.set_defaults(func=func)
        return p

    p = command("add", cmd_add, aliases=["a"], parents=[net], help_="save URLs (arguments, or piped on stdin)")
    p.add_argument("urls", nargs="*", metavar="URL", help="URLs to save; '-' or nothing reads stdin")
    p.add_argument("-t", "--tag", action="append", default=[], help="tag(s); repeat or comma-separate")
    p.add_argument("-s", "--star", action="store_true", help="star the article")
    p.add_argument("-f", "--force", action="store_true", help="re-download if already saved")
    p.add_argument("--title", help="override the detected title")
    p.add_argument("--html", metavar="FILE", help="use this HTML ('-' = stdin) instead of downloading URL")
    p.add_argument("-r", "--read", action="store_true", help="open it in the pager right after saving")

    p = command(
        "import", cmd_import, parents=[net], help_="bulk-add URLs from a text file or a bookmarks/Pocket HTML export"
    )
    p.add_argument("file", help="file path, or '-' for stdin")
    p.add_argument("-t", "--tag", action="append", default=[], help="tag every imported article")

    p = command("ls", cmd_list, aliases=["list"], help_="list saved articles (default: unread queue)")
    p.add_argument("-a", "--all", action="store_true", help="include archived")
    p.add_argument("--archived", action="store_true", help="only archived")
    p.add_argument("-u", "--unread", action="store_true", help="only unread")
    p.add_argument("--read", action="store_true", help="only already-read")
    p.add_argument("--starred", action="store_true", help="only starred")
    p.add_argument("-t", "--tag", action="append", default=[], help="filter by tag (repeat for AND)")
    p.add_argument("--site", help="filter by site name (substring)")
    p.add_argument("--max-minutes", type=int, metavar="N", help="only articles up to N minutes long")
    p.add_argument("--sort", choices=["newest", "oldest", "title", "longest", "shortest"], default="newest")
    p.add_argument("-n", "--limit", type=int, default=0, metavar="N", help="max rows")
    p.add_argument("--ids", action="store_true", help="print only ids (for xargs)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-color", action="store_true")

    p = command("read", cmd_read, aliases=["r"], help_="read articles in a pager (no id: next unread)")
    p.add_argument("ids", nargs="*", metavar="ID", help="ids, ranges (3-7), or '-' for stdin")
    p.add_argument("--pick", choices=["oldest", "newest", "random", "shortest"], default="oldest",
                   help="which unread article to open when no id is given")  # fmt: skip
    p.add_argument("-w", "--width", type=int, help="wrap width (default: terminal width, max 80)")
    p.add_argument("-l", "--links", action="store_true", help="number links and list them at the end")
    p.add_argument("--no-pager", action="store_true", help="print to stdout instead of using a pager")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--no-mark", action="store_true", help="don't mark as read")
    p.add_argument("--archive", action="store_true", help="archive after reading")
    p.add_argument("--raw", action="store_true", help="print stored Markdown, unwrapped")

    p = command("search", cmd_search, aliases=["s"], help_="full-text search (titles + bodies)")
    p.add_argument("query", nargs="+")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--raw", action="store_true", help='pass the query straight to FTS5 (AND/OR/NEAR, "phrases")')
    p.add_argument("--no-color", action="store_true")

    for name, (_, past) in FLAG_ACTIONS.items():
        p = command(name, cmd_flag(name), help_=f"mark articles {past}")
        p.add_argument("ids", nargs="+", metavar="ID")

    p = command("tag", cmd_tag, help_="add tags to an article (or remove with -d)")
    p.add_argument("id", metavar="ID")
    p.add_argument("tags", nargs="+", metavar="TAG")
    p.add_argument("-d", "--remove", action="store_true", help="remove instead of add")

    command("tags", cmd_tags, help_="list tags with counts")

    p = command("rm", cmd_rm, aliases=["delete"], help_="permanently delete articles")
    p.add_argument("ids", nargs="+", metavar="ID")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")

    p = command("refetch", cmd_refetch, parents=[net], help_="re-download and re-extract articles")
    p.add_argument("ids", nargs="*", metavar="ID")
    p.add_argument("--all", action="store_true")

    p = command("open", cmd_open, help_="open the original page in your web browser")
    p.add_argument("ids", nargs="+", metavar="ID")

    p = command("export", cmd_export, help_="export articles as JSON or Markdown")
    p.add_argument("ids", nargs="*", metavar="ID", help="default: everything not archived")
    p.add_argument("--format", choices=["json", "markdown"], default="json")
    p.add_argument("-o", "--out", help="file (json) or directory (markdown); default stdout")
    p.add_argument("-a", "--all", action="store_true", help="include archived")

    p = command("backup", cmd_backup, help_="write a consistent copy of the database")
    p.add_argument("dest")

    command("stats", cmd_stats, help_="library statistics")
    command("path", lambda lib, args: 0, help_="print the database path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        raw = ["ls"] if sys.stdin.isatty() else ["add"]
    elif "://" in raw[0] and not raw[0].startswith("-"):
        raw.insert(0, "add")  # `rlater https://…` is shorthand for `rlater add https://…`
    args = build_parser().parse_args(raw)
    db_path: Path = args.db.expanduser() if args.db else default_db_path()
    if args.command == "path":
        print(db_path)
        return 0
    try:
        with contextlib.closing(Library(db_path)) as lib:
            return int(args.func(lib, args))
    except BrokenPipeError:  # e.g. `rlater ls | head`
        with contextlib.suppress(Exception):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141
    except KeyboardInterrupt:
        return 130
    except sqlite3.Error as exc:
        die(f"database error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
