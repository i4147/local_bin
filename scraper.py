#!/data/data/com.termux/files/usr/bin/env python
import argparse
import contextlib
import hashlib
import json
import multiprocessing as mp
import os
import random
import re
import signal
import string
import sys
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from importlib.machinery import SourceFileLoader
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from dh import MAX_WORKERS
from loguru import logger

DEFAULT_UA = "Mozilla/5.0 (compatible; MergedTools/1.0)"
_COLORS = {
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "white": "\033[37m",
}


def cprint(msg: str, color: str | None = None) -> None:
    if color and color in _COLORS:
        print(f"{_COLORS[color]}{msg}\033[0m")
    else:
        print(msg)


def get_installed_packages() -> dict[str, str]:
    import importlib.metadata as md

    out: dict[str, str] = {}
    for d in md.distributions():
        try:
            name = d.metadata["Name"]
            ver = d.version
        except Exception:
            continue
        if name and ver:
            out[name] = ver
    return out


def http_get(url: str, *, timeout: float = 15, headers: dict | None = None, stream: bool = False) -> requests.Response:
    h = {"User-Agent": DEFAULT_UA}
    if headers:
        h.update(headers)
    r = requests.get(url, headers=h, timeout=timeout, stream=stream)
    r.raise_for_status()
    return r


def make_session(headers: dict | None = None) -> requests.Session:
    s = requests.Session()
    h = {"User-Agent": DEFAULT_UA}
    if headers:
        h.update(headers)
    s.headers.update(h)
    return s


def read_lines(path: str | Path) -> list[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        return []


def write_lines(path: str | Path, items: Iterable[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(f"{x}\n" for x in items)


def strip_fragment(url: str) -> str:
    return urldefrag(url)[0]


def same_site(url_a: str, url_b: str) -> bool:
    return urlparse(url_a).netloc.lower() == urlparse(url_b).netloc.lower()


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def build_robots(session: requests.Session, base_url: str) -> RobotFileParser | None:
    rp = RobotFileParser()
    rp.set_url(urljoin(base_url, "/robots.txt"))
    try:
        rp.read()
        print(f"✅ Loaded robots.txt: {rp.url}")
        return rp
    except Exception as e:
        print(f"⚠️  Could not load robots.txt ({e}). Proceeding with caution.")
        return None


def _latest_from_mirror(pkg: str, mirror: str, timeout: float) -> str | None:
    from packaging.version import Version

    url = urljoin(mirror, pkg)
    try:
        body = requests.get(url, timeout=timeout).text
    except Exception:
        return None
    pat = re.compile(
        rf"{re.escape(pkg)}-([0-9][A-Za-z0-9\.\-_]*?)\.(?:whl|tar\.gz|zip)",
        re.IGNORECASE,
    )
    versions: list[Any] = []
    for m in pat.finditer(body):
        with contextlib.suppress(Exception):
            versions.append(Version(m.group(1)))
    if not versions:
        return None
    latest = str(max(versions))
    print(f"{pkg}:{latest}")
    return latest


def cmd_pkg_updates(args: argparse.Namespace) -> int:
    from packaging.version import Version

    start = time.time()
    installed = get_installed_packages()
    cprint(f"Found {len(installed)} installed packages.", "blue")
    state_path = Path(args.state)
    prev: dict[str, dict] = {}
    if state_path.exists():
        try:
            prev = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cprint(
                f"Warning: Corrupted results file '{state_path}'. Starting fresh.",
                "red",
            )
    results: dict[str, dict] = {}
    to_check: list[tuple[str, str]] = []
    for name, ver in installed.items():
        cached = prev.get(name)
        if cached and cached.get("latest_version") == "null":
            to_check.append((name, ver))
            continue
        if cached and cached.get("installed_version") == ver:
            results[name] = cached
            continue
        to_check.append((name, ver))
    cprint(f"Will check {len(to_check)} packages.", "blue")
    updatable: list[tuple[str, str, str]] = []
    for i, (name, ver) in enumerate(to_check, 1):
        latest = _latest_from_mirror(name, args.mirror, args.timeout)
        results[name] = {
            "installed_version": ver,
            "latest_version": latest,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if latest:
            try:
                if Version(ver) < Version(latest):
                    updatable.append((name, ver, latest))
                    cprint(
                        f"[{i}/{len(to_check)}] {name}: {ver} -> {latest} (Updatable!)",
                        "green",
                    )
                else:
                    cprint(
                        f"[{i}/{len(to_check)}] {name}: {ver} (Latest: {latest})",
                        "white",
                    )
            except Exception as e:
                cprint(f"[{i}/{len(to_check)}] {name}: version parse error: {e}", "yellow")
        else:
            cprint(f"[{i}/{len(to_check)}] {name}: Could not get latest version.", "yellow")
        if i % 10 == 0 or i == len(to_check):
            state_path.write_text(json.dumps(results, indent=4), encoding="utf-8")
            cprint("Results saved periodically.", "blue")
    cprint("\n--- Summary of Updatable Packages ---", "blue")
    if updatable:
        for name, old, new in updatable:
            cprint(f"{name}: {old} -> {new}", "magenta")
        cprint(
            f"\nTo update these packages, you can use: pip install --upgrade {' '.join(p[0] for p in updatable)}",
            "yellow",
        )
    else:
        cprint("All installed packages are up to date or could not be checked.", "green")
    cprint(f"\nFinished in {time.time() - start:.2f} seconds.", "blue")
    return 0


def cmd_import_check(args: argparse.Namespace) -> int:
    import site

    from loguru import logger

    logger.remove()
    logger.add(
        args.log,
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
        encoding="utf-8",
        backtrace=True,
        diagnose=True,
    )
    logger.add(sys.stderr, level="INFO", format="<level>{level:<8}</level> | {message}")

    def _site_roots() -> list[Path]:
        roots: list[Path] = []
        for p in site.getsitepackages():
            pp = Path(p)
            if pp.is_dir():
                roots.append(pp)
        try:
            us = Path(site.getusersitepackages())
            if us.is_dir():
                roots.append(us)
        except Exception:
            pass
        return roots

    def _iter_py(roots: list[Path]):
        for r in roots:
            logger.debug(f"Scanning {r}")
            yield from r.rglob("*.py")

    def _try_load(path: Path) -> bool:
        name = "".join(random.choice(string.ascii_letters) for _ in range(20))
        try:
            SourceFileLoader(name, str(path)).load_module()
            logger.success(f"OK   {path}")
            return True
        except Exception:
            logger.error(f"FAIL {path}")
            logger.opt(exception=True).debug("Traceback:")
            return False

    if args.paths:
        files = [Path(p) for p in args.paths]
    else:
        roots = _site_roots()
        if not roots:
            logger.error("No site-packages directories found.")
            return 2
        print(f"Site-packages roots: {[str(r) for r in roots]}")
        files = list(_iter_py(roots))
    print(f"Checking {len(files)} file(s)...")
    ok = fail = 0
    for f in files:
        if not f.is_file() or f.suffix != ".py":
            logger.debug(f"Skip (not a .py file): {f}")
            continue
        if _try_load(f):
            ok += 1
        else:
            fail += 1
    print(f"Done. OK={ok} FAIL={fail} TOTAL={ok + fail}")
    return 1 if fail else 0


_COC_KEYWORDS = ["th18", "town hall 18", "townhall 18", "th-18"]
_COC_HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Clash of Clans TH18 Base Links</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
               background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
               min-height: 100vh; padding: 20px; }
        .container { max-width: 1000px; margin: 0 auto; background: white;
                     border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,0.3);
                     padding: 30px; }
        h1 { color: #333; margin-bottom: 10px; text-align: center; }
        .info { text-align: center; color: #666; margin-bottom: 30px; font-size: 14px; }
        .stats { display: flex; justify-content: center; gap: 30px;
                 margin-bottom: 30px; flex-wrap: wrap; }
        .stat { text-align: center; }
        .stat-number { font-size: 28px; font-weight: bold; color: #667eea; }
        .stat-label { color: #999; font-size: 12px; text-transform: uppercase; margin-top: 5px; }
        .bases-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                      gap: 20px; }
        .base-card { background: #f8f9fa; border-left: 4px solid #667eea;
                     border-radius: 5px; padding: 15px; transition: all 0.3s ease; }
        .base-card:hover { background: #fff; box-shadow: 0 5px 15px rgba(0,0,0,0.1);
                           transform: translateY(-2px); }
        .base-title { font-weight: bold; color: #333; margin-bottom: 10px; word-break: break-word; }
        .base-link { display: inline-block; background: #667eea; color: white;
                     padding: 10px 15px; border-radius: 5px; text-decoration: none;
                     font-size: 14px; margin-bottom: 10px; word-break: break-all; }
        .base-link:hover { background: #764ba2; }
        .base-source { font-size: 12px; color: #999; margin-top: 10px; word-break: break-word; }
        .empty { text-align: center; padding: 40px; color: #999; }
        .footer { text-align: center; margin-top: 30px; padding-top: 20px;
                  border-top: 1px solid #eee; font-size: 12px; color: #999; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🏰 Clash of Clans TH18 Base Links</h1>
        <p class="info">Extracted and compiled base links from various sources</p>
"""


def _scrape_th18_from_site(url: str, timeout: float, keywords: list[str]) -> list[dict]:
    found: list[dict] = []
    try:
        r = http_get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        soup = BeautifulSoup(r.content, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True).lower()
            if any(k in text or k in href.lower() for k in keywords):
                full = urljoin(url, href)
                if full not in [f["url"] for f in found]:
                    found.append({"url": full, "title": text or "TH18 Base", "source": url})
        print(f"✓ Found {len(found)} TH18 bases from {url}")
    except requests.RequestException as e:
        print(f"✗ Error fetching {url}: {e}")
    return found


def _render_coc_report(bases: list[dict], out_path: Path) -> None:
    html = _COC_HTML_HEAD
    html += f"""
        <div class="stats">
            <div class="stat">
                <div class="stat-number">{len(bases)}</div>
                <div class="stat-label">Total Bases</div>
            </div>
            <div class="stat">
                <div class="stat-number">{len({b["source"] for b in bases})}</div>
                <div class="stat-label">Sources</div>
            </div>
        </div>
        <div class="bases-grid">
"""
    if bases:
        for i, b in enumerate(bases, 1):
            html += f"""            <div class="base-card">
                <div class="base-title">Base #{i}: {b["title"]}</div>
                <a href="{b["url"]}" class="base-link" target="_blank">🔗 Open Base</a>
                <div class="base-source"><strong>Source:</strong> {b["source"]}</div>
            </div>
"""
    else:
        html += """            <div class="empty">
                <p>No TH18 bases found. Make sure your links file contains valid URLs.</p>
            </div>
"""
    html += """        </div>
        <div class="footer">
            <p>Generated automatically | Clash of Clans Base Scraper</p>
        </div>
    </div>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")
    print(f"\n✓ HTML file saved as {out_path}")


def cmd_coc_links(args: argparse.Namespace) -> int:
    print("-" * 40)
    print("Clash of Clans TH18 Base Link Extractor")
    print("-" * 40)
    sites = read_lines(args.links)
    if not sites:
        print(f"Error: {args.links} not found or empty. Please create a file with website URLs.")
        return 1
    print(f"\nFound {len(sites)} websites to scrape...\n")
    all_bases: list[dict] = []
    for i, site in enumerate(sites, 1):
        print(f"[{i}/{len(sites)}] Scraping {site}...")
        if not site.startswith(("http://", "https://")):
            site = "https://" + site
        all_bases.extend(_scrape_th18_from_site(site, args.timeout, _COC_KEYWORDS))
        time.sleep(args.delay)
    print(f"\nTotal TH18 bases found: {len(all_bases)}")
    _render_coc_report(all_bases, Path(args.output))
    print(f"\nDone! Open '{args.output}' in your browser to view the results.")
    return 0


_DEFAULT_CHANNELS = {
    "Blueprint_CoC": "UCQJJGSWnPUCb8uKV_MoJeOA",
    "iTzu": "UCLKKvlo0yK8OgWvjCiZQ3sA",
    "Clash_Champs": "UC_mD8S6pWpSstY3mXJ9nEqw",
}
_LINK_RE = re.compile(r"(https?://link\.clashofclans\.com/[^\s]+)")


def _yt_recent_videos(yt, channel_id: str, days: int = 30, max_videos: int = 100) -> list[dict]:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    out: list[dict] = []
    req = yt.search().list(
        part="snippet",
        channelId=channel_id,
        publishedAfter=cutoff,
        maxResults=50,
        order="date",
        type="video",
    )
    while req:
        resp = req.execute()
        for item in resp.get("items", []):
            vid = item["id"]["videoId"]
            info = yt.videos().list(part="snippet", id=vid).execute()
            snip = info["items"][0]["snippet"]
            out.append({
                "title": snip["title"],
                "description": snip["description"],
                "url": f"https://www.youtube.com/watch?v={vid}",
            })
        req = yt.search().list_next(req, resp)
        if len(out) > max_videos:
            break
    return out


def _extract_th18_links(text: str) -> list[str]:
    links = _LINK_RE.findall(text)
    return [l for l in links if "TH18" in l.upper() or "TH18" in text.upper()]


def _write_coc_youtube_report(channel_name: str, videos: list[dict], out_dir: Path) -> Path:
    stamp = datetime.now().strftime("%d-%m-%Y")
    d = out_dir / f"{stamp}_{channel_name}"
    d.mkdir(parents=True, exist_ok=True)
    out = d / "bases.html"
    html = f"""<html>
<head>
  <title>{channel_name} TH18 Bases</title>
  <style>
    body {{ font-family: sans-serif; padding: 20px; background: #f4f4f9; }}
    .card {{ background: white; margin-bottom: 15px; padding: 15px;
             border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
    a {{ color: #667eea; }}
    .vid-ref {{ font-size: 0.9em; color: #888; }}
  </style>
</head>
<body>
  <h1>TH18 Bases from {channel_name} (Last 30 Days)</h1>
"""
    for v in videos:
        html += f"""
  <div class="card">
    <h3>{v["title"]}</h3>
    <p class="vid-ref">Source: <a href="{v["video_url"]}" target="_blank">Watch Video</a></p>
    <ul>
"""
        for link in v["links"]:
            html += f'<li><a href="{link}">Get Base Layout</a></li>'
        html += "</ul></div>"
    html += "</body></html>"
    out.write_text(html, encoding="utf-8")
    print(f"Generated: {out}")
    return out


def cmd_coc_youtube(args: argparse.Namespace) -> int:
    from googleapiclient.discovery import build

    with contextlib.suppress(Exception):
        from dotenv import load_dotenv

        load_dotenv()
    api_key = args.api_key or os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("Error: YouTube API key not provided (use --api-key or YOUTUBE_API_KEY env).")
        return 1
    channels = dict(_DEFAULT_CHANNELS)
    if args.channels:
        for pair in args.channels.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                channels[k.strip()] = v.strip()
    yt = build("youtube", "v3", developerKey=api_key)
    out_dir = Path(args.output_dir)
    for name, cid in channels.items():
        print(f"Processing {name}...")
        vids = _yt_recent_videos(yt, cid, days=args.days)
        collected = []
        for v in vids:
            links = _extract_th18_links(v["description"])
            if links:
                collected.append({
                    "title": v["title"],
                    "video_url": v["url"],
                    "links": list(set(links)),
                })
        if collected:
            _write_coc_youtube_report(name, collected, out_dir)
        else:
            print(f"No TH18 links found for {name}.")
    return 0


_SIZE_RE = re.compile(r"([\d.]+)\s*([KMG]?)i?B?")


def _parse_size_mb(text: str) -> float | None:
    if not text or text.strip() == "-":
        return None
    m = _SIZE_RE.search(text.strip())
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "G":
        return val * 1024
    if unit == "M":
        return val
    if unit == "K":
        return val / 1024
    return val / 1024 / 1024


def _quality_of(name: str) -> str | None:
    low = name.lower()
    if "480p" in low:
        return "480"
    if "720p" in low:
        return "720"
    return None


def _wanted_movie(
    name: str,
    size_mb: float | None,
    max_mb: float,
    extensions: tuple[str, ...],
    qualities: tuple[str, ...],
) -> bool:
    low = name.lower()
    if not any(low.endswith(e) for e in extensions):
        return False
    if qualities and not any(q in low for q in qualities):
        return False
    return not (size_mb is None or size_mb >= max_mb)


def _movie_basic(args: argparse.Namespace) -> int:
    base = args.url or "https://dls2.aparatchi-dlcenter.top/DonyayeSerial/"
    movies_file = Path(args.movies_file)
    state_file = Path(args.state_file)
    max_mb = args.size
    extensions = tuple(args.extensions)
    qualities = tuple(args.qualities)
    visited: set[str] = set()
    found: list[str] = []
    if state_file.exists():
        try:
            st = json.loads(state_file.read_text(encoding="utf-8"))
            visited = set(st.get("visited", []))
            found = st.get("found_movies", [])
            print(f"📂 Loaded state: {len(visited)} visited, {len(found)} movies found")
        except Exception as e:
            print(f"⚠️ Error loading state: {e}")
    if not found and movies_file.exists():
        found = read_lines(movies_file)
        print(f"📂 Loaded {len(found)} movies from {movies_file}")

    def save_state() -> None:
        state_file.write_text(
            json.dumps({"visited": list(visited), "found_movies": found}, indent=2),
            encoding="utf-8",
        )

    def add_movie(url: str) -> None:
        if url not in found:
            found.append(url)
            with movies_file.open("a", encoding="utf-8") as f:
                f.write(url + "\n")

    def crawl(url: str, depth: int = 0) -> None:
        if url in visited or "movie" in url.lower():
            return
        print(f"{'  ' * depth}Crawling: {url}")
        visited.add(url)
        try:
            r = requests.get(url, timeout=args.timeout)
            r.raise_for_status()
        except Exception as e:
            print(f"❌ Failed to access {url}: {e}")
            return
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.find_all("tr") or soup.select("table tbody tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            link = cells[0].find("a")
            if not link:
                continue
            name = link.text.strip()
            href = link.get("href")
            if not href or "Parent directory" in name or "Parent Directory" in name:
                continue
            size_txt = cells[2].text.strip() if len(cells) > 2 else ""
            size_mb = _parse_size_mb(size_txt)
            full = urljoin(url, href)
            if href.endswith("/"):
                crawl(full, depth + 1)
            elif _wanted_movie(name, size_mb, max_mb, extensions, qualities):
                print(f"✅ Found: {full} ({size_mb:.2f} MB)")
                add_movie(full)
                save_state()

    print("🎬 Movie Crawler (basic engine)")
    print("=" * 40)
    try:
        crawl(base)
    except KeyboardInterrupt:
        print("\n⏹️ Interrupted. Saving state...")
        save_state()
        return 0
    save_state()
    print(f"\n✅ Done. {len(found)} movies saved to {movies_file}")
    return 0


def _movie_simple(args: argparse.Namespace) -> int:
    base = args.url or "https://dls2.aparatchi-dlcenter.top/DonyayeSerial/"
    movies_file = Path(args.movies_file)
    max_mb = args.size
    extensions = tuple(args.extensions)
    qualities = tuple(args.qualities)
    results: list[str] = []

    def extract(soup: BeautifulSoup, src_url: str) -> None:
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            link = cells[0].find("a")
            if not link:
                continue
            name = link.text.strip()
            href = link.get("href")
            if not href or "Parent directory" in name:
                continue
            full = urljoin(src_url, href)
            if href.endswith("/"):
                _crawl(full)
                continue
            size_txt = cells[1].text.strip()
            size_mb = _parse_size_mb(size_txt)
            if _wanted_movie(name, size_mb, max_mb, extensions, qualities):
                print(f"  ✓ Found in table: {full} ({size_mb} MB)")
                results.append(full)
        for ta in soup.find_all("textarea", class_="value"):
            text = ta.text.strip()
            if not text:
                continue
            for line in text.splitlines():
                line = line.strip()
                size_mb = None
                if line.lower().endswith(extensions) and any(q in line.lower() for q in qualities):
                    print(f"  ✓ Found in textarea: {line}")
                    results.append(line)
        for p in soup.find_all("p", style=lambda v: v and "text-align: center" in v):
            for a in p.find_all("a", href=True):
                href = a["href"]
                if href.lower().endswith(extensions) and any(q in href.lower() for q in qualities):
                    print(f"  ✓ Found in p tag: {href}")
                    results.append(href)

    seen: set[str] = set()

    def _crawl(url: str) -> None:
        if url in seen or "movie" in url.lower():
            return
        print(f"\n🔍 Crawling: {url}")
        seen.add(url)
        try:
            r = requests.get(url, timeout=args.timeout)
            r.raise_for_status()
        except Exception as e:
            print(f"  ❌ Failed to access {url}: {e}")
            return
        extract(BeautifulSoup(r.text, "html.parser"), url)

    print("🚀 Movie Crawler (simple engine)")
    print(f"📁 Base URL: {base}")
    print(f"📊 Max size: {max_mb} MB")
    _crawl(base)
    unique = list(dict.fromkeys(results))
    with movies_file.open("w", encoding="utf-8") as f:
        f.writelines(u + "\n" for u in unique)
    print(f"\n✅ Done. {len(unique)} movies saved to {movies_file}")
    return 0


_STOP = False


def _sigint(_sig, _frame):
    global _STOP
    _STOP = True
    print("\n⚠️ Interrupt received. Saving progress...")


def _parallel_worker(url: str, max_mb: float):
    from loguru import logger

    matches: list[dict] = []
    subdirs: list[str] = []
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        logger.debug(f"Failed to fetch {url}: {exc}")
        return matches, subdirs
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        link = cells[0].find("a")
        if not link:
            continue
        name = link.text.strip()
        href = link.get("href")
        if not href or "Parent directory" in name:
            continue
        full = urljoin(url, href)
        if href.endswith("/"):
            subdirs.append(full)
            continue
        if not name.lower().endswith(".mkv"):
            continue
        quality = _quality_of(name)
        if quality not in ("480", "720"):
            continue
        size_mb = _parse_size_mb(cells[1].text.strip())
        if size_mb is None or size_mb > max_mb:
            continue
        matches.append({"url": full, "quality": quality, "size_mb": size_mb})
    return matches, subdirs


def _movie_parallel(args: argparse.Namespace) -> int:
    from multiprocessing import Manager, Pool

    global _STOP
    _STOP = False
    signal.signal(signal.SIGINT, _sigint)
    base = args.url or "https://sr.moviesho.com/Series/"
    if not base.endswith("/"):
        base += "/"
    max_mb = args.size
    processes = MAX_WORKERS
    state_file = Path(args.state_file)
    movies_file = Path(args.movies_file)
    json_out = Path(args.json_out)
    visited: set[str] = set()
    queue: list[str] = []
    if state_file.exists():
        try:
            st = json.loads(state_file.read_text(encoding="utf-8"))
            visited = set(st.get("visited", []))
            queue = list(st.get("queue", []))
            print("🔁 Resuming previous crawl...")
        except Exception:
            pass
    if not queue:
        queue = [base]

    def save_state(q, v):
        state_file.write_text(json.dumps({"queue": list(q), "visited": list(v)}), encoding="utf-8")

    def append(matches):
        with movies_file.open("a", encoding="utf-8") as f:
            f.writelines(m["url"] + "\n" for m in matches)
        with json_out.open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(m) + "\n" for m in matches)

    mgr = Manager()
    shared_visited = mgr.list(visited)
    shared_queue = mgr.list(queue)
    print(f"🚀 Parallel movie crawler using {processes} processes")
    with Pool(processes=processes) as pool:
        while shared_queue and not _STOP:
            batch = []
            for _ in range(min(len(shared_queue), processes)):
                url = shared_queue.pop(0)
                if url in shared_visited:
                    continue
                shared_visited.append(url)
                batch.append((pool.apply_async(_parallel_worker, (url, max_mb)), url))
            for res, _ in batch:
                if _STOP:
                    break
                try:
                    matches, subs = res.get()
                except Exception as e:
                    print(f"worker error: {e}")
                    continue
                if matches:
                    append(matches)
                    print(f"✅ Found {len(matches)} movies")
                for s in subs:
                    if s not in shared_visited:
                        shared_queue.append(s)
    save_state(list(shared_queue), set(shared_visited))
    if _STOP:
        print("💾 Progress saved. Run again to continue.")
    else:
        if state_file.exists():
            state_file.unlink()
        print("✅ Crawl completed successfully.")
    return 0


def cmd_movie_crawl(args: argparse.Namespace) -> int:
    if args.engine == "basic":
        return _movie_basic(args)
    if args.engine == "simple":
        return _movie_simple(args)
    if args.engine == "parallel":
        return _movie_parallel(args)
    print(f"Unknown engine: {args.engine}")
    return 2


def _img_urls_from_soup(soup: BeautifulSoup, base: str) -> set[str]:
    out: set[str] = set()
    for img in soup.find_all("img"):
        for attr in ("src", "data-src", "data-lazy-src", "data-original", "data-image"):
            v = img.get(attr)
            if v:
                full = strip_fragment(urljoin(base, v.strip()))
                if urlparse(full).scheme in {"http", "https"}:
                    out.add(full)
        srcset = img.get("srcset")
        if srcset:
            for item in srcset.split(","):
                cand = item.strip().split(" ")[0]
                if cand:
                    full = strip_fragment(urljoin(base, cand))
                    if urlparse(full).scheme in {"http", "https"}:
                        out.add(full)
    return out


def _same_host_links(soup: BeautifulSoup, base: str, host: str) -> set[str]:
    out: set[str] = set()
    for a in soup.find_all("a", href=True):
        full = strip_fragment(urljoin(base, a["href"]))
        if urlparse(full).scheme in {"http", "https"} and urlparse(full).netloc == host:
            out.add(full)
    return out


def _check_image_size(url: str, min_w: int, min_h: int):
    from PIL import Image

    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
        r.raise_for_status()
        if not r.headers.get("Content-Type", "").startswith("image/"):
            return None
        with Image.open(BytesIO(r.content)) as im:
            w, h = im.size
        if w > min_w and h > min_h:
            return (url, w, h)
    except Exception:
        return None
    return None


def _download_image(item) -> str | None:
    url, _, _ = item
    out_dir = Path("images")
    out_dir.mkdir(exist_ok=True)
    p = urlparse(url)
    name = Path(p.path).name or "image"
    ext = Path(name).suffix.lower() or ".img"
    stem = Path(name).stem or "image"
    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    dest = out_dir / f"{stem}_{digest}{ext}"
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        dest.write_bytes(r.content)
        return str(dest)
    except Exception:
        return None


def _hunt_images(
    start_url: str,
    max_pages: int,
    min_w: int,
    min_h: int,
    print_urls: bool,
    download: bool,
    urls_file: Path,
) -> None:
    start_url = strip_fragment(start_url)
    host = urlparse(start_url).netloc
    session = make_session()
    queue = deque([start_url])
    seen_pages: set[str] = set()
    seen_imgs: set[str] = set()
    matches: list[tuple[str, int, int]] = []
    workers = max(1, mp.cpu_count() - 1)
    with mp.Pool(workers) as pool:
        while queue and len(seen_pages) < max_pages:
            page = queue.popleft()
            if page in seen_pages:
                continue
            seen_pages.add(page)
            print(f"Scanning: {page}", flush=True)
            try:
                r = session.get(page, timeout=20)
                r.raise_for_status()
            except requests.RequestException as e:
                print(f"Could not scan page: {e}")
                continue
            if "text/html" not in r.headers.get("Content-Type", ""):
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            new_imgs = _img_urls_from_soup(soup, page) - seen_imgs
            seen_imgs.update(new_imgs)
            for res in pool.map(lambda u: _check_image_size(u, min_w, min_h), new_imgs):
                if res is not None:
                    matches.append(res)
            for link in _same_host_links(soup, page, host):
                if link not in seen_pages:
                    queue.append(link)
        if print_urls:
            with urls_file.open("w", encoding="utf-8") as f:
                for url, w, h in matches:
                    print(f"{w}x{h} {url}")
                    f.write(url + "\n")
            print(f"Saved URLs to {urls_file}")
        if download:
            results = pool.map(_download_image, matches)
            count = sum(1 for r in results if r is not None)
            print(f"Downloaded {count} image(s) to images/")
    print(
        f"Scanned {len(seen_pages)} page(s), checked {len(seen_imgs)} image(s), found {len(matches)} matching image(s)."
    )


def cmd_image_hunt(args: argparse.Namespace) -> int:
    if not args.print_urls and not args.download:
        print("Error: use -p and/or -d to select an action.")
        return 2
    _hunt_images(
        args.url,
        args.max_pages,
        args.min_width,
        args.min_height,
        args.print_urls,
        args.download,
        Path(args.urls_file),
    )
    return 0


def cmd_image_save(args: argparse.Namespace) -> int:
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        r = http_get(args.url, timeout=args.timeout)
    except Exception as e:
        print(f"Error: {e}")
        return 1
    soup = BeautifulSoup(r.text, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        full = urljoin(args.url, src)
        try:
            ir = requests.get(full, stream=True, timeout=args.timeout)
            ir.raise_for_status()
            dest = out_dir / Path(urlparse(full).path).name
            with dest.open("wb") as f:
                f.writelines(ir.iter_content(1024))
            print(f"Downloaded: {dest}")
        except Exception as e:
            print(f"Failed to download {full}: {e}")
    return 0


def _crawl_site(
    start_url: str,
    *,
    mode: str,
    ext: str,
    keyword: str,
    max_pages: int,
    delay: float,
    timeout: float,
) -> list[str]:
    if not start_url.startswith(("http://", "https://")):
        start_url = "https://" + start_url
    host = urlparse(start_url).netloc.lower().split(":")[0]
    if mode in ("ext", "pdf"):
        rp = build_robots(requests.Session(), start_url)
    else:
        rp = None
    session = make_session()

    def allowed(url: str) -> bool:
        if rp is None:
            return True
        try:
            return rp.can_fetch(session.headers["User-Agent"], url)
        except Exception:
            return True

    def same_domain(url: str) -> bool:
        h = urlparse(url).netloc.lower().split(":")[0]
        return h == host or h.endswith("." + host)

    queue = deque([start_url])
    visited: set[str] = set()
    results: set[str] = set()
    skip_exts = (".jpg", ".jpeg", ".png", ".gif", ".css", ".js")
    while queue and len(visited) < max_pages:
        url = strip_fragment(queue.popleft())
        if url in visited:
            continue
        if not allowed(url):
            print(f"🚫 Skipping (robots.txt): {url}")
            continue
        visited.add(url)
        print(f"🔍 Checking: {url}")
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  ⚠️  Request error: {e}")
            continue
        ctype = r.headers.get("Content-Type", "").lower()
        if mode == "pdf" and "pdf" in ctype:
            results.add(url)
            print(f"  📄 PDF (via Content-Type): {url}")
            continue
        if mode == "ext" and ext and ext.lower() in ctype:
            results.add(url)
            print(f"  📄 {ext} (via Content-Type): {url}")
            continue
        if mode == "keyword" and keyword and keyword in url.lower():
            results.add(url)
        if "html" not in ctype and not url.lower().endswith((".html", ".htm")):
            continue
        soup = BeautifulSoup(r.content, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full = urljoin(url, href)
            if not full.startswith(("http://", "https://")):
                continue
            if not same_domain(full):
                continue
            low = full.lower()
            if mode == "pdf" and low.endswith(".pdf"):
                results.add(full)
                print(f"  📄 PDF (via link): {full}")
                continue
            if mode == "ext" and ext and low.endswith(ext.lower()):
                results.add(full)
                print(f"  📄 found {ext} (via link): {full}")
                continue
            if mode == "keyword" and keyword and keyword in low:
                results.add(full)
            if full not in visited and not low.endswith(skip_exts):
                queue.append(full)
        time.sleep(delay)
    return sorted(results)


def cmd_link_crawl(args: argparse.Namespace) -> int:
    if args.mode == "ext" and not args.ext:
        print("Error: --ext is required with --mode ext.")
        return 2
    if args.mode == "keyword" and not args.keyword:
        print("Error: --keyword is required with --mode keyword.")
        return 2
    out = _crawl_site(
        args.url,
        mode=args.mode,
        ext=args.ext or "",
        keyword=(args.keyword or "").lower(),
        max_pages=args.max_pages,
        delay=args.delay,
        timeout=args.timeout,
    )
    write_lines(args.output, out)
    print(f"\n✅ Saved {len(out)} URLs to '{args.output}'")
    return 0


_ZZZ_ID_RE = re.compile(r"zzztube\.com/(\d+)")


def _inspect_video_page(url: str, timeout: float) -> dict:
    try:
        r = http_get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
    except requests.RequestException as e:
        return {"error": f"Failed to fetch URL: {e}"}
    soup = BeautifulSoup(r.content, "html.parser")
    video_id = None
    m = _ZZZ_ID_RE.search(url)
    if m:
        video_id = m.group(1)
    og_video = soup.find("meta", {"property": "og:video"})
    if og_video:
        content = og_video.get("content", "")
        if "zzztube" in content:
            m2 = re.search(r"/(\d+)", content)
            if m2:
                video_id = m2.group(1)
    title = None
    og_title = soup.find("meta", {"property": "og:title"})
    if og_title:
        title = og_title.get("content")
    iframe = soup.find("iframe", {"src": re.compile("zzztube")})
    iframe_src = iframe.get("src") if iframe else None
    return {
        "video_id": video_id,
        "title": title,
        "playable_url": f"https://zzztube.com/{video_id}" if video_id else None,
        "iframe_src": iframe_src,
        "direct_url": (f"https://player.zzztube.com/video/{video_id}" if video_id else None),
    }


def cmd_video_info(args: argparse.Namespace) -> int:
    out: list[dict] = []
    for url in args.urls:
        print(f"\n📹 Processing: {url}")
        info = _inspect_video_page(url, args.timeout)
        out.append(info)
        if "error" in info:
            print(f"❌ {info['error']}")
        else:
            print(f"✅ Video ID: {info['video_id']}")
            print(f"📝 Title: {info['title']}")
            print(f"🔗 Playable URL: {info['playable_url']}")
            print(f"▶️  Player URL: {info['direct_url']}")
    out_path = Path(args.output)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n💾 Results saved to: {out_path.absolute()}")
    return 0


def cmd_google_search(args: argparse.Namespace) -> int:
    from googlesearch import search as gsearch

    for result in gsearch(args.query, num_results=args.num_results):
        print(result)
    return 0


_WEBSIZE_TAGS = {
    "link": ["href"],
    "script": ["src"],
    "img": ["src", "srcset"],
    "source": ["src", "srcset"],
    "video": ["src", "poster"],
    "audio": ["src"],
    "iframe": ["src"],
    "embed": ["src"],
    "object": ["data"],
}


def _expand_srcset(base: str, value: str) -> list[str]:
    out = []
    for item in value.split(","):
        url = item.strip().split(" ")[0]
        if url:
            out.append(urljoin(base, url))
    return out


def _head_size(session: requests.Session, url: str) -> tuple[int, str] | None:
    try:
        r = session.get(url, stream=True, timeout=15, allow_redirects=True)
        total = 0
        for chunk in r.iter_content(chunk_size=8192):
            total += len(chunk)
        ctype = r.headers.get("content-type", "").split(";")[0]
        return total, ctype
    except requests.RequestException as e:
        print(f"  ! Failed: {url} ({e})", file=sys.stderr)
        return None


def cmd_web_size(args: argparse.Namespace) -> int:
    session = make_session({"User-Agent": args.user_agent})
    start = args.url
    host = urlparse(start).netloc
    seen_resources: set[str] = set()
    queue = [start]
    visited_pages: set[str] = set()
    total = 0
    html_total = 0
    by_type: dict[str, int] = defaultdict(int)
    sub_count = 0
    while queue:
        page = queue.pop(0)
        if page in visited_pages:
            continue
        visited_pages.add(page)
        if args.crawl and len(visited_pages) > args.max_pages:
            break
        print(f"\n>> Page: {page}")
        try:
            r = session.get(page, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"   ! Failed: {e}", file=sys.stderr)
            continue
        html_len = len(r.content)
        total += html_len
        html_total += html_len
        by_type["text/html"] += html_len
        print(f"   HTML: {fmt_bytes(html_len)}")
        soup = BeautifulSoup(r.text, "html.parser")
        for tag_name, attrs in _WEBSIZE_TAGS.items():
            for tag in soup.find_all(tag_name):
                for attr in attrs:
                    val = tag.get(attr)
                    if not val:
                        continue
                    urls = _expand_srcset(page, val) if attr == "srcset" else [urljoin(page, val)]
                    for u in urls:
                        if u in seen_resources:
                            continue
                        seen_resources.add(u)
                        res = _head_size(session, u)
                        if res is None:
                            continue
                        size, ctype = res
                        total += size
                        by_type[ctype or "unknown"] += size
                        sub_count += 1
        if args.crawl:
            for a in soup.find_all("a", href=True):
                nxt = urljoin(page, a["href"])
                p = urlparse(nxt)
                if p.netloc == host and p.scheme in ("http", "https"):
                    stripped = p._replace(fragment="").geturl()
                    if stripped not in visited_pages:
                        queue.append(stripped)
    print("\n" + "=" * 60)
    print(f"Pages visited:      {len(visited_pages)}")
    print(f"Sub-resources:      {sub_count}")
    print(f"HTML total:         {fmt_bytes(html_total)}")
    print(f"TOTAL DOWNLOAD:     {fmt_bytes(total)}")
    print("=" * 60)
    print("\nBreakdown by content-type:")
    for ctype, size in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {ctype:<30} {fmt_bytes(size):>12}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="merged_tools.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("pkg-updates", help="Check installed packages against a mirror.")
    sp.add_argument("--mirror", default="https://mirror-pypi.runflare.com/")
    sp.add_argument("--timeout", type=int, default=15)
    sp.add_argument("--state", default="/sdcard/c4u.json")
    sp.set_defaults(func=cmd_pkg_updates)
    sp = sub.add_parser("import-check", help="Try importing every .py in site-packages.")
    sp.add_argument("paths", nargs="*", help="Optional explicit .py files to test.")
    sp.add_argument("--log", default="check_modules.log")
    sp.set_defaults(func=cmd_import_check)
    sp = sub.add_parser("coc-links", help="Scrape CoC TH18 base links from a list of sites.")
    sp.add_argument("-l", "--links", default="links.txt")
    sp.add_argument("-o", "--output", default="th18_bases.html")
    sp.add_argument("--timeout", type=float, default=10)
    sp.add_argument("--delay", type=float, default=1)
    sp.set_defaults(func=cmd_coc_links)
    sp = sub.add_parser("coc-youtube", help="Extract CoC TH18 links from YouTube channels.")
    sp.add_argument("--api-key", default=None)
    sp.add_argument("--channels", default=None, help="Override channels as 'name=id,name=id'.")
    sp.add_argument("--days", type=int, default=30)
    sp.add_argument("--output-dir", default="output")
    sp.set_defaults(func=cmd_coc_youtube)
    sp = sub.add_parser("movie-crawl", help="Movie index crawler with three engines.")
    sp.add_argument("--engine", choices=("basic", "simple", "parallel"), default="basic")
    sp.add_argument(
        "-u",
        "--url",
        default=None,
        help="Base index URL. Engine defaults apply if omitted.",
    )
    sp.add_argument("-s", "--size", type=float, default=300, help="Max size in MB (default 300).")
    sp.add_argument("--movies-file", default="movies.txt")
    sp.add_argument("--state-file", default="crawler_state.json")
    sp.add_argument("--json-out", default="movies.json", help="JSON output for --engine parallel.")
    sp.add_argument("--timeout", type=float, default=15)
    sp.add_argument("--extensions", nargs="+", default=[".mkv", ".mp4"])
    sp.add_argument("--qualities", nargs="+", default=["480p", "720p"])
    sp.set_defaults(func=cmd_movie_crawl)
    sp = sub.add_parser("image-hunt", help="Find/download large images from a site.")
    sp.add_argument("url")
    sp.add_argument("-p", "--print", action="store_true", dest="print_urls")
    sp.add_argument("-d", "--download", action="store_true", dest="download")
    sp.add_argument("--max-pages", type=int, default=100)
    sp.add_argument("--min-width", type=int, default=300)
    sp.add_argument("--min-height", type=int, default=400)
    sp.add_argument("--urls-file", default="img_urls.txt")
    sp.set_defaults(func=cmd_image_hunt)
    sp = sub.add_parser("image-save", help="Save every <img> from one page.")
    sp.add_argument("url")
    sp.add_argument("output_dir", nargs="?", default="output")
    sp.add_argument("--timeout", type=float, default=5)
    sp.set_defaults(func=cmd_image_save)
    sp = sub.add_parser("link-crawl", help="Crawl a site, collect links by mode.")
    sp.add_argument("url")
    sp.add_argument("--mode", choices=("ext", "pdf", "keyword"), default="ext")
    sp.add_argument("--ext", default=None, help="Extension for --mode ext.")
    sp.add_argument("--keyword", default=None, help="Keyword for --mode keyword.")
    sp.add_argument("--max-pages", type=int, default=1000)
    sp.add_argument("--delay", type=float, default=1.0)
    sp.add_argument("--timeout", type=float, default=10)
    sp.add_argument("-o", "--output", default="urls.txt")
    sp.set_defaults(func=cmd_link_crawl)
    sp = sub.add_parser("video-info", help="Inspect zzztube video URLs.")
    sp.add_argument("urls", nargs="+")
    sp.add_argument("-o", "--output", default="zzztube_links.json")
    sp.add_argument("--timeout", type=float, default=10)
    sp.set_defaults(func=cmd_video_info)
    sp = sub.add_parser("google-search", help="DuckDuckGo/Google search wrapper.")
    sp.add_argument("query")
    sp.add_argument("-n", "--num-results", type=int, default=10)
    sp.set_defaults(func=cmd_google_search)
    sp = sub.add_parser("web-size", help="Measure total download size of a page.")
    sp.add_argument("url")
    sp.add_argument("--crawl", action="store_true")
    sp.add_argument("--max-pages", type=int, default=50)
    sp.add_argument("--user-agent", default="Mozilla/5.0 (size-checker)")
    sp.set_defaults(func=cmd_web_size)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print("\n⏹️ Interrupted by user.")
        return 130


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
