#!/data/data/com.termux/files/usr/bin/env python
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import parse_qs, unquote, urljoin, urlparse
import zipfile
import httpx
from loguru import logger
from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import Tag, compatible_tags, cpython_tags, generic_tags, interpreter_name, interpreter_version
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

PYPI = "https://pypi.org/simple/"
TSINGHUA = "https://pypi.tuna.tsinghua.edu.cn/simple/"
YANDEX = "https://mirror-pypi.runflare.com/simple/"
DEFAULT_INDEXES = (YANDEX, TSINGHUA, PYPI)
HASH_ALGOS = frozenset({
    "sha256",
    "sha512",
    "sha384",
    "sha224",
    "sha1",
    "md5",
})
HASH_PREFERENCE = {
    "sha512": 6,
    "sha384": 5,
    "sha256": 4,
    "sha224": 3,
    "sha1": 2,
    "md5": 1,
}
SOURCE_SUFFIXES = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.bz",
    ".tar.xz",
    ".tar.lz",
    ".tar.lzma",
    ".tar.zst",
    ".tgz",
    ".tbz",
    ".tbz2",
    ".txz",
    ".tlz",
    ".zip",
    ".tar",
)
CHUNK = 1024 * 1024
PARALLEL_THRESHOLD = 5 * 1024 * 1024
WORKERS = min(8, max(4, os.cpu_count() or 4))
ACCEPT_HEADER = ", ".join((
    "application/vnd.pypi.simple.v1+json",
    "application/vnd.pypi.simple.v1+html; q=0.1",
    "text/html; q=0.01",
))
JSON_TYPES = frozenset({"application/vnd.pypi.simple.v1+json"})
HTML_TYPES = frozenset({"text/html", "application/vnd.pypi.simple.v1+html"})


@dataclass(frozen=True, slots=True)
class Link:
    url: str
    comes_from: str | None = None
    requires_python: str | None = None
    yanked: str | None = None
    hashes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        fragment = urlparse(self.url).fragment
        if not fragment:
            return
        hashes = dict(self.hashes)
        for algo, values in parse_qs(fragment).items():
            if algo in HASH_ALGOS and values:
                hashes.setdefault(algo, values[0])
        if hashes != self.hashes:
            object.__setattr__(self, "hashes", hashes)

    @property
    def clean_url(self):
        return self.url.split("#", 1)[0]

    @property
    def parsed(self):
        return urlparse(self.clean_url)

    @property
    def filename(self):
        return unquote(self.parsed.path.rsplit("/", 1)[-1])

    @property
    def is_wheel(self):
        return self.filename.endswith(".whl")

    @property
    def is_source(self):
        return self.filename.lower().endswith(SOURCE_SUFFIXES)

    @property
    def is_file(self):
        return self.parsed.scheme == "file"

    @property
    def file_path(self):
        if not self.is_file:
            raise ValueError(f"not a file URL: {self.url}")
        return Path(unquote(self.parsed.path))

    def __repr__(self):
        return f"<Link {self.url}>"


@dataclass(frozen=True, slots=True)
class Package:
    name: str
    version: str
    link: Link

    @property
    def parsed_version(self):
        return Version(self.version)

    def as_dict(self):
        return {
            "name": self.name,
            "version": self.version,
            "link": self.link.url,
            "filename": self.link.filename,
            "requires_python": self.link.requires_python,
            "yanked": self.link.yanked,
            "hashes": self.link.hashes,
        }


@dataclass(slots=True)
class TargetPython:
    py_version: tuple[int, ...] | None = None
    abis: list[str] | None = None
    implementation: str | None = None
    platforms: list[str] | None = None
    _tags: list[Tag] | None = field(default=None, init=False, repr=False)

    def supported_tags(self):
        if self._tags is None:
            self._tags = self._compute_tags()
        return self._tags

    def version_str(self):
        version = self.py_version or sys.version_info[:2]
        return ".".join(str(v) for v in version[:2])

    def _compute_tags(self):
        impl = self.implementation or interpreter_name()
        pyver = self.py_version[:2] if self.py_version else None
        digits = "".join(map(str, pyver)) if pyver else interpreter_version()
        interp = f"{impl}{digits}"
        tags = []
        if impl == "cp":
            tags.extend(cpython_tags(pyver, self.abis, self.platforms))
        else:
            tags.extend(generic_tags(interp, self.abis, self.platforms))
        tags.extend(compatible_tags(pyver, interp, self.platforms))
        return tags


class HTMLIndexParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.base_url = None
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        if tag == "base" and self.base_url is None:
            self.base_url = dict(attrs).get("href")
        elif tag == "a":
            self.anchors.append(dict(attrs))


def parse_html(response):
    parser = HTMLIndexParser()
    parser.feed(response.text)
    base = parser.base_url or str(response.url)
    for attrs in parser.anchors:
        href = attrs.get("href")
        if not href:
            continue
        yanked = attrs.get("data-yanked")
        yield Link(
            url=urljoin(base, href),
            comes_from=base,
            requires_python=attrs.get("data-requires-python"),
            yanked="" if yanked == "" else yanked,
        )


def parse_json(response):
    data = response.json()
    base = str(response.url)
    for item in data.get("files", ()):
        url = item.get("url")
        if not url:
            continue
        yanked = item.get("yanked")
        yield Link(
            url=urljoin(base, url),
            comes_from=base,
            requires_python=item.get("requires-python"),
            yanked=yanked if isinstance(yanked, str) else "yanked" if yanked else None,
            hashes=item.get("hashes") or {},
        )


def fetch_page(client, url):
    response = client.get(url, headers={"Accept": ACCEPT_HEADER})
    response.raise_for_status()
    ctype = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ctype in JSON_TYPES:
        return list(parse_json(response))
    if ctype in HTML_TYPES:
        return list(parse_html(response))
    raise ValueError(f"unsupported content-type: {ctype!r}")


def strip_source_ext(filename):
    lower = filename.lower()
    for suffix in sorted(SOURCE_SUFFIXES, key=len, reverse=True):
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    return filename


def python_matches(spec, target):
    try:
        specifier = SpecifierSet(spec)
    except InvalidSpecifier:
        return True
    return specifier.contains(target.version_str(), prereleases=True)


def tag_priority(link, priorities):
    fallback = len(priorities) + 1
    if not link.is_wheel:
        return fallback
    try:
        _, _, _, tags = parse_wheel_filename(link.filename)
    except (InvalidWheelFilename, InvalidVersion):
        return fallback
    return min((priorities.get(t, fallback) for t in tags), default=fallback)


def make_candidate(link, req, target, priorities, *, pre, no_binary, only_binary):
    if link.is_wheel:
        if no_binary:
            return None
        try:
            name, version, _, tags = parse_wheel_filename(link.filename)
        except (InvalidWheelFilename, InvalidVersion):
            return None
        if canonicalize_name(name) != canonicalize_name(req.name):
            return None
        if not req.specifier.contains(version, prereleases=pre):
            return None
        if not any(t in priorities for t in tags):
            return None
    else:
        if only_binary or not link.is_source:
            return None
        base = strip_source_ext(link.filename)
        name, sep, version_str = base.rpartition("-")
        if not sep or not name or not version_str:
            return None
        if canonicalize_name(name) != canonicalize_name(req.name):
            return None
        try:
            version = Version(version_str)
        except InvalidVersion:
            return None
        if not req.specifier.contains(version, prereleases=pre):
            return None
    if link.requires_python and not python_matches(link.requires_python, target):
        return None
    return Package(name=req.name, version=str(version), link=link)


def source_rank(link):
    name = link.filename.lower()
    if name.endswith((".tar.gz", ".tgz")):
        return 30
    if name.endswith((".tar.bz2", ".tar.bz")):
        return 29
    if name.endswith(".zip"):
        return 28
    if link.is_source:
        return 27
    return 0


class Finder:
    def __init__(self, indexes=(), find_links=(), target=None, client=None):
        self.sources = []
        idx = list(indexes)
        if not idx:
            idx = list(DEFAULT_INDEXES)
        self.sources.extend(("index", u) for u in idx)
        self.sources.extend(("find_links", u) for u in find_links)
        self.target = target or TargetPython()
        self._client = client
        self.priorities = {t: i for i, t in enumerate(self.target.supported_tags())}

    @property
    def client(self):
        if self._client is None:
            self._client = httpx.Client(
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def find(self, req, *, pre=None, no_binary=False, only_binary=False):
        results = []
        seen = set()
        for kind, source in self.sources:
            try:
                links = self._collect(kind, source, req)
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.warning("skipping {} {}: {}", kind, source, exc)
                continue
            for link in links:
                pkg = make_candidate(
                    link,
                    req,
                    self.target,
                    self.priorities,
                    pre=pre,
                    no_binary=no_binary,
                    only_binary=only_binary,
                )
                if pkg is None:
                    continue
                key = pkg.link.clean_url
                if key in seen:
                    continue
                seen.add(key)
                results.append(pkg)
        results.sort(key=self._sort_key, reverse=True)
        return results

    def _collect(self, kind, source, req):
        if kind == "index":
            url = urljoin(source.rstrip("/") + "/", canonicalize_name(req.name) + "/")
        else:
            url = source
            if not url.startswith(("http://", "https://", "file://")):
                url = Path(url).expanduser().resolve().as_uri()
        if url.startswith("file://"):
            return self._file_links(url)
        return fetch_page(self.client, url)

    @staticmethod
    def _file_links(url):
        path = Path(unquote(urlparse(url).path))
        if path.is_file():
            return [Link(url=path.resolve().as_uri())]
        if not path.is_dir():
            raise FileNotFoundError(path)
        return [Link(url=item.resolve().as_uri()) for item in path.iterdir() if item.is_file()]

    def _sort_key(self, pkg):
        link = pkg.link
        return (
            -int(link.yanked is not None),
            pkg.parsed_version,
            source_rank(link),
            -tag_priority(link, self.priorities),
            link.filename.lower(),
        )


def pick_hash(hashes):
    valid = [(a.lower(), d.lower()) for a, d in hashes.items() if a.lower() in HASH_ALGOS]
    if not valid:
        return None
    return max(valid, key=lambda item: HASH_PREFERENCE.get(item[0], 0))


def file_hash(path, algo):
    digest = hashlib.new(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_hash(path, hashes):
    chosen = pick_hash(hashes)
    if chosen is None:
        logger.warning("no published hash for {}; checking archive only", path.name)
        return True
    algo, expected = chosen
    actual = file_hash(path, algo)
    if actual.lower() != expected.lower():
        logger.error("hash mismatch for {} using {}: expected {}, got {}", path, algo, expected, actual)
        return False
    logger.debug("verified {} for {}", algo, path.name)
    return True


def verify_archive(path):
    name = path.name.lower()
    try:
        if name.endswith((".whl", ".zip")):
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
            if bad is not None:
                logger.error("corrupt zip member {} in {}", bad, path)
                return False
            return True
        if any(name.endswith(s) for s in SOURCE_SUFFIXES):
            try:
                with tarfile.open(path, mode="r:*") as t:
                    for _ in t:
                        pass
                return True
            except tarfile.ReadError:
                logger.debug("cannot inspect {} as tar; skipping", path.name)
                return True
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        logger.error("archive check failed for {}: {}", path, exc)
        return False
    return True


def remote_size(client, url):
    try:
        r = client.head(url, follow_redirects=True)
        r.raise_for_status()
        v = r.headers.get("content-length")
        return int(v) if v and v.isdigit() else None
    except (httpx.HTTPError, ValueError):
        return None


def supports_ranges(client, url):
    try:
        r = client.head(url, follow_redirects=True)
        r.raise_for_status()
        return r.headers.get("accept-ranges", "").lower() == "bytes"
    except httpx.HTTPError:
        return False


def download_single(client, url, dest):
    with client.stream("GET", url) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(CHUNK):
                f.write(chunk)


def download_parallel(url, dest, size, workers=WORKERS):
    workers = min(workers, max(2, (size + CHUNK - 1) // CHUNK))
    part_dir = dest.with_name(dest.name + ".parts")
    if part_dir.exists():
        shutil.rmtree(part_dir)
    part_dir.mkdir(parents=True, exist_ok=True)
    span = (size + workers - 1) // workers
    ranges = []
    for i in range(workers):
        start = i * span
        end = min(size - 1, start + span - 1)
        if start > end:
            continue
        ranges.append((start, end, part_dir / f"{i:03d}.part"))

    def fetch_range(start, end, part):
        headers = {"Range": f"bytes={start}-{end}"}
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(90.0, connect=15.0),
        ) as c:
            with c.stream("GET", url, headers=headers) as r:
                if r.status_code != 206:
                    raise httpx.HTTPStatusError(
                        f"server ignored range {start}-{end}",
                        request=r.request,
                        response=r,
                    )
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(CHUNK):
                        f.write(chunk)
        if part.stat().st_size != end - start + 1:
            raise OSError(f"incomplete range {start}-{end}")

    try:
        with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
            futures = [pool.submit(fetch_range, s, e, p) for s, e, p in ranges]
            for fut in as_completed(futures):
                fut.result()
        with dest.open("wb") as out:
            for _, _, part in ranges:
                with part.open("rb") as p:
                    shutil.copyfileobj(p, out, length=CHUNK)
        if dest.stat().st_size != size:
            raise OSError(f"size mismatch: expected {size}, got {dest.stat().st_size}")
    finally:
        shutil.rmtree(part_dir, ignore_errors=True)


def download_requests(url, dest):
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests backend requires: pip install requests") from exc
    with requests.get(url, stream=True, timeout=(15, 90)) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK):
                if chunk:
                    f.write(chunk)


def download_pycurl(url, dest):
    try:
        import pycurl
    except ImportError as exc:
        raise RuntimeError("pycurl backend requires: pip install pycurl") from exc
    curl = pycurl.Curl()
    try:
        with dest.open("wb") as f:
            curl.setopt(curl.URL, url)
            curl.setopt(curl.FOLLOWLOCATION, True)
            curl.setopt(curl.CONNECTTIMEOUT, 15)
            curl.setopt(curl.TIMEOUT, 120)
            curl.setopt(curl.WRITEDATA, f)
            curl.perform()
            if curl.getinfo(curl.RESPONSE_CODE) >= 400:
                raise OSError(f"HTTP {curl.getinfo(curl.RESPONSE_CODE)} for {url}")
    finally:
        curl.close()


def download_aria2c(url, dest):
    aria = shutil.which("aria2c")
    if aria is None:
        raise RuntimeError("aria2c not found in PATH")
    cmd = [
        aria,
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--continue=true",
        "--file-allocation=none",
        "--max-connection-per-server=8",
        "--min-split-size=5M",
        "--split=8",
        "--summary-interval=0",
        "--dir",
        str(dest.parent),
        "--out",
        dest.name,
        url,
    ]
    subprocess.run(cmd, check=True)


def download(link, dest_dir, *, backend="httpx", client=None):
    dest_dir.mkdir(parents=True, exist_ok=True)
    if link.is_file:
        src = link.file_path
        if not src.is_file():
            raise FileNotFoundError(src)
        if not verify_hash(src, link.hashes):
            raise ValueError(f"hash mismatch for local artifact: {src}")
        if not verify_archive(src):
            raise ValueError(f"invalid archive: {src}")
        return src
    filename = link.filename or "download.bin"
    dest = dest_dir / filename
    if dest.exists():
        if verify_hash(dest, link.hashes) and verify_archive(dest):
            logger.info("using cached {}", dest)
            return dest
        logger.warning("removing invalid cache {}", dest)
        dest.unlink(missing_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.unlink(missing_ok=True)
    logger.info("downloading {} via {}", link.clean_url, backend)
    try:
        if backend == "httpx":
            owns = client is None
            c = client or httpx.Client(
                follow_redirects=True,
                timeout=httpx.Timeout(90.0, connect=15.0),
            )
            try:
                size = remote_size(c, link.clean_url)
                ranged = supports_ranges(c, link.clean_url)
                if size is not None and size > PARALLEL_THRESHOLD and ranged:
                    logger.info("parallel ranged download ({} MiB)", round(size / 1024 / 1024, 2))
                    download_parallel(link.clean_url, tmp, size)
                else:
                    download_single(c, link.clean_url, tmp)
            finally:
                if owns:
                    c.close()
        elif backend == "requests":
            download_requests(link.clean_url, tmp)
        elif backend == "pycurl":
            download_pycurl(link.clean_url, tmp)
        elif backend == "aria2c":
            download_aria2c(link.clean_url, tmp)
        else:
            raise ValueError(f"unsupported backend: {backend}")
        tmp.replace(dest)
        if not verify_hash(dest, link.hashes):
            dest.unlink(missing_ok=True)
            raise ValueError(f"hash mismatch for {link.clean_url}")
        if not verify_archive(dest):
            dest.unlink(missing_ok=True)
            raise ValueError(f"archive integrity failed: {dest}")
        logger.success("downloaded {}", dest)
        return dest
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def parse_version_arg(value):
    parts = value.split(".")
    if not parts or any(not p.isdigit() for p in parts):
        raise argparse.ArgumentTypeError(f"invalid Python version: {value!r}")
    return tuple(int(p) for p in parts)


def build_parser():
    p = argparse.ArgumentParser(
        prog="pkgfetch",
        description="Find and download Python distributions from PEP 503/691 indexes.",
    )
    p.add_argument("requirement", type=Requirement, help="PEP 508 requirement, e.g. requests>=2.28")
    p.add_argument(
        "-i",
        "--index-url",
        action="append",
        default=[],
        metavar="URL",
        help="simple index URL, repeatable; defaults to PyPI, Tsinghua, Yandex",
    )
    p.add_argument(
        "-f",
        "--find-link",
        action="append",
        default=[],
        metavar="DIR_OR_URL",
        help="additional find-links source, repeatable",
    )
    p.add_argument("-d", "--dest", type=Path, default=Path("."), metavar="DIR", help="download destination directory")
    p.add_argument(
        "-b", "--backend", choices=("httpx", "requests", "pycurl", "aria2c"), default="httpx", help="download backend"
    )
    p.add_argument(
        "--py-version", type=parse_version_arg, default=None, metavar="X.Y", help="override target Python version"
    )
    p.add_argument("--platform", action="append", default=[], metavar="TAG", help="override platform tag, repeatable")
    p.add_argument("--abi", action="append", default=[], metavar="TAG", help="override ABI tag, repeatable")
    p.add_argument("--impl", default=None, metavar="IMPL", help="override Python implementation")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--no-binary", action="store_true", help="exclude wheels")
    g.add_argument("--only-binary", action="store_true", help="wheels only")
    p.add_argument("--pre", action="store_true", help="allow prereleases")
    p.add_argument("-a", "--all", action="store_true", help="download every matching candidate")
    p.add_argument("-j", "--json", action="store_true", help="print JSON metadata")
    p.add_argument("--no-download", action="store_true", help="resolve only, no download")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def setup_logging(verbose):
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="<level>{level: <8}</level> {message}",
    )


def main(argv=None):
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    target = TargetPython(
        py_version=args.py_version,
        abis=args.abi or None,
        implementation=args.impl,
        platforms=args.platform or None,
    )
    with Finder(
        indexes=args.index_url,
        find_links=args.find_link,
        target=target,
    ) as finder:
        matches = finder.find(
            args.requirement,
            pre=True if args.pre else None,
            no_binary=args.no_binary,
            only_binary=args.only_binary,
        )
        if not matches:
            logger.error("no matching distributions for {}", args.requirement)
            return 1
        if not args.all:
            matches = matches[:1]
        results = []
        for pkg in matches:
            meta = pkg.as_dict()
            if not args.no_download:
                try:
                    path = download(
                        pkg.link,
                        args.dest.expanduser().resolve(),
                        backend=args.backend,
                        client=finder.client if args.backend == "httpx" else None,
                    )
                except (httpx.HTTPError, OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
                    logger.exception("download failed for {}: {}", pkg.link.clean_url, exc)
                    return 2
                meta["local_path"] = str(path)
            results.append(meta)
    if args.json or args.no_download:
        out = results[0] if len(results) == 1 else results
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for r in results:
            print(r["local_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
