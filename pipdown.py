#!/data/data/com.termux/files/usr/bin/env python
"Find and download Python distributions from PEP 503/691 indexes."

from __future__ import annotations
import argparse
from collections.abc import Iterator
from dataclasses import dataclass, field
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import tarfile
from typing import Any, Self
from urllib.parse import parse_qs, unquote, urljoin, urlparse
import zipfile

import httpx
from loguru import logger
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import (
    Tag,
    compatible_tags,
    cpython_tags,
    generic_tags,
    interpreter_name,
    interpreter_version,
)
from packaging.utils import (
    InvalidWheelFilename,
    canonicalize_name,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version


PYPI: str = "https://pypi.org/simple/"
TSINGHUA: str = "https://pypi.tuna.tsinghua.edu.cn/simple/"
YANDEX: str = "https://mirror-pypi.runflare.com/simple/"
DEFAULT_INDEXES: tuple[str, ...] = (YANDEX, TSINGHUA, PYPI)
HASH_ALGOS: frozenset[str] = frozenset({
    "sha256",
    "sha512",
    "sha384",
    "sha224",
    "sha1",
    "md5",
})
HASH_PREFERENCE: dict[str, int] = {
    "sha512": 6,
    "sha384": 5,
    "sha256": 4,
    "sha224": 3,
    "sha1": 2,
    "md5": 1,
}
SOURCE_SUFFIXES: tuple[str, ...] = (
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
CHUNK: int = 1024 * 1024
WORKERS: int = 4
ACCEPT_HEADER: str = (
    "application/vnd.pypi.simple.v1+json, application/vnd.pypi.simple.v1+html; q=0.1, text/html; q=0.01"
)
JSON_TYPES: frozenset[str] = frozenset({"application/vnd.pypi.simple.v1+json"})
HTML_TYPES: frozenset[str] = frozenset({"text/html", "application/vnd.pypi.simple.v1+html"})


@dataclass(frozen=True, slots=True)
class Link:
    "Store url, comes_from, requires_python, yanked, hashes."

    url: str
    comes_from: str | None = None
    requires_python: str | None = None
    yanked: str | None = None
    hashes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Finish initialization after the dataclass fields are set."""
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
    def clean_url(self) -> str:
        "Clean url."
        return self.url.split("#", 1)[0]

    @property
    def parsed(self):
        """Return the parsed."""
        return urlparse(self.clean_url)

    @property
    def filename(self) -> str:
        """Return the filename."""
        return unquote(self.parsed.path.rsplit("/", 1)[-1])

    @property
    def is_wheel(self) -> bool:
        "Return whether wheel."
        return self.filename.endswith(".whl")

    @property
    def is_source(self) -> bool:
        "Return whether source."
        return self.filename.lower().endswith(SOURCE_SUFFIXES)

    @property
    def is_file(self) -> bool:
        "Return whether file."
        return self.parsed.scheme == "file"

    @property
    def file_path(self) -> Path:
        """Return the file path."""
        if not self.is_file:
            msg = f"not a file URL: {self.url}"
            raise ValueError(msg)
        return Path(unquote(self.parsed.path))

    def __repr__(self) -> str:
        "Return the object's developer-facing representation."
        return f"<Link {self.url}>"


@dataclass(frozen=True, slots=True)
class Package:
    "Store name, version, link."

    name: str
    version: str
    link: Link

    @property
    def parsed_version(self) -> Version:
        """Return the parsed version."""
        return Version(self.version)

    def as_dict(self) -> dict[str, Any]:
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
    "Store py_version, abis, implementation, platforms, _tags."

    py_version: tuple[int, ...] | None = None
    abis: list[str] | None = None
    implementation: str | None = None
    platforms: list[str] | None = None
    _tags: list[Tag] | None = field(default=None, init=False, repr=False)

    def supported_tags(self) -> list[Tag]:
        if self._tags is None:
            self._tags = self._compute_tags()
        return self._tags

    def version_str(self) -> str:
        version = self.py_version or sys.version_info[:2]
        return ".".join(str(v) for v in version[:2])

    def _compute_tags(self) -> list[Tag]:
        impl = self.implementation or interpreter_name()
        pyver = self.py_version[:2] if self.py_version else None
        digits = "".join(map(str, pyver)) if pyver else interpreter_version()
        interp = f"{impl}{digits}"
        tags: list[Tag] = []
        if impl == "cp":
            tags.extend(cpython_tags(pyver, self.abis, self.platforms))
        else:
            tags.extend(generic_tags(interp, self.abis, self.platforms))
        tags.extend(compatible_tags(pyver, interp, self.platforms))
        return tags


class HTMLIndexParser(HTMLParser):
    def __init__(self) -> None:
        "Initialize the instance state from the supplied arguments."
        super().__init__(convert_charrefs=True)
        self.base_url: str | None = None
        self.anchors: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        "Handle starttag."
        if tag == "base" and self.base_url is None:
            self.base_url = dict(attrs).get("href")
        elif tag == "a":
            self.anchors.append(dict(attrs))


def parse_html(response: httpx.Response) -> Iterator[Link]:
    "Parse html."
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


def parse_json(response: httpx.Response) -> Iterator[Link]:
    "Parse json."
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


def fetch_page(client: httpx.Client, url: str) -> list[Link]:
    "Fetch page."
    response = client.get(url, headers={"Accept": ACCEPT_HEADER})
    response.raise_for_status()
    ctype = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ctype in JSON_TYPES:
        return list(parse_json(response))
    if ctype in HTML_TYPES:
        return list(parse_html(response))
    msg = f"unsupported content-type: {ctype!r}"
    raise ValueError(msg)


def strip_source_ext(filename: str) -> str:
    "Strip source ext."
    lower = filename.lower()
    for suffix in sorted(SOURCE_SUFFIXES, key=len, reverse=True):
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    return filename


def python_matches(spec: str, target: TargetPython) -> bool:
    try:
        specifier = SpecifierSet(spec)
    except InvalidSpecifier:
        return True
    return specifier.contains(target.version_str(), prereleases=True)


def tag_priority(link: Link, priorities: dict[Tag, int]) -> int:
    fallback = len(priorities) + 1
    if not link.is_wheel:
        return fallback
    try:
        _, _, _, tags = parse_wheel_filename(link.filename)
    except (InvalidWheelFilename, InvalidVersion):
        return fallback
    return min((priorities.get(t, fallback) for t in tags), default=fallback)


def make_candidate(
    link: Link,
    req: Requirement,
    target: TargetPython,
    priorities: dict[Tag, int],
    *,
    pre: bool | None,
    no_binary: bool,
    only_binary: bool,
) -> Package | None:
    "Create candidate."
    version: Version
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


def source_rank(link: Link) -> int:
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
    def __init__(
        self,
        indexes: list[str] | tuple[str, ...] = (),
        find_links: list[str] | tuple[str, ...] = (),
        target: TargetPython | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        "Initialize the instance state from the supplied arguments."
        self.sources: list[tuple[str, str]] = []
        idx = list(indexes)
        if not idx:
            idx = list(DEFAULT_INDEXES)
        self.sources.extend(("index", u) for u in idx)
        self.sources.extend(("find_links", u) for u in find_links)
        self.target = target or TargetPython()
        self._client = client
        self.priorities: dict[Tag, int] = {t: i for i, t in enumerate(self.target.supported_tags())}

    @property
    def client(self) -> httpx.Client:
        """Return the client."""
        if self._client is None:
            self._client = httpx.Client(
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        "Enter the managed context and return its resource."
        return self

    def __exit__(self, *args: object) -> None:
        "Release resources acquired by the managed context."
        self.close()

    def find(
        self,
        req: Requirement,
        *,
        pre: bool | None = None,
        no_binary: bool = False,
        only_binary: bool = False,
    ) -> list[Package]:
        results: list[Package] = []
        seen: set[str] = set()
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

    def _collect(self, kind: str, source: str, req: Requirement) -> list[Link]:
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
    def _file_links(url: str) -> list[Link]:
        path = Path(unquote(urlparse(url).path))
        if path.is_file():
            return [Link(url=path.resolve().as_uri())]
        if not path.is_dir():
            raise FileNotFoundError(path)
        return [Link(url=item.resolve().as_uri()) for item in path.iterdir() if item.is_file()]

    def _sort_key(self, pkg: Package) -> tuple[Any, ...]:
        link = pkg.link
        return (
            -int(link.yanked is not None),
            pkg.parsed_version,
            source_rank(link),
            -tag_priority(link, self.priorities),
            link.filename.lower(),
        )


def pick_hash(hashes: dict[str, str]) -> tuple[str, str] | None:
    valid = [(a.lower(), d.lower()) for a, d in hashes.items() if a.lower() in HASH_ALGOS]
    if not valid:
        return None
    return max(valid, key=lambda item: HASH_PREFERENCE.get(item[0], 0))


def file_hash(path: Path, algo: str) -> str:
    digest = hashlib.new(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_hash(path: Path, hashes: dict[str, str]) -> bool:
    "Verify hash."
    chosen = pick_hash(hashes)
    if chosen is None:
        logger.warning("no published hash for {}; checking archive only", path.name)
        return True
    algo, expected = chosen
    actual = file_hash(path, algo)
    if actual.lower() != expected.lower():
        logger.error(
            "hash mismatch for {} using {}: expected {}, got {}",
            path,
            algo,
            expected,
            actual,
        )
        return False
    logger.debug("verified {} for {}", algo, path.name)
    return True


def verify_archive(path: Path) -> bool:
    "Verify archive."
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


def _display_name(name: str) -> str:
    return name[:35] + "..." if len(name) > 38 else name


def download_all(
    links: list[Link],
    dest_dir: Path,
    *,
    workers: int = WORKERS,
) -> list[Path | None]:
    """Download multiple links in parallel using pycurl's multi interface.

    Writes data in chunks as it arrives and shows per-file progress with rich.
    Per-link failures are logged and returned as ``None``; the batch keeps
    going so one bad artifact does not abort the others.
    """
    try:
        import pycurl
    except ImportError as exc:
        msg = "pycurl backend requires: pip install pycurl"
        raise RuntimeError(msg) from exc
    try:
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            TextColumn,
            TimeRemainingColumn,
            TransferSpeedColumn,
        )
    except ImportError as exc:
        msg = "progress display requires: pip install rich"
        raise RuntimeError(msg) from exc

    dest_dir.mkdir(parents=True, exist_ok=True)
    results: list[Path | None] = [None] * len(links)
    pending: list[tuple[int, Link, Path, Path]] = []

    # Phase 0: short-circuit local files and valid cached downloads.
    for i, link in enumerate(links):
        if link.is_file:
            try:
                src = link.file_path
                if not src.is_file():
                    logger.error("local artifact not found: {}", src)
                    continue
                if not verify_hash(src, link.hashes):
                    logger.error("hash mismatch for local artifact: {}", src)
                    continue
                if not verify_archive(src):
                    logger.error("invalid archive: {}", src)
                    continue
            except (OSError, ValueError) as exc:
                logger.error("local artifact check failed for {}: {}", link.url, exc)
                continue
            results[i] = src
            continue
        filename = link.filename or "download.bin"
        dest = dest_dir / filename
        if dest.exists():
            try:
                if verify_hash(dest, link.hashes) and verify_archive(dest):
                    logger.info("using cached {}", dest)
                    results[i] = dest
                    continue
                logger.warning("removing invalid cache {}", dest)
                dest.unlink(missing_ok=True)
            except OSError as exc:
                logger.error("cache cleanup failed for {}: {}", dest, exc)
                dest.unlink(missing_ok=True)
        tmp = dest.with_name(f"{dest.name}.{i}.part")
        tmp.unlink(missing_ok=True)
        pending.append((i, link, tmp, dest))

    if not pending:
        return results

    errors: list[str] = []

    with Progress(
        TextColumn("[bold blue]{task.fields[name]:<40}", justify="right"),
        BarColumn(bar_width=None),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        DownloadColumn(),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
    ) as progress:
        multi = pycurl.CurlMulti()
        multi.setopt(pycurl.M_MAX_TOTAL_CONNECTIONS, workers)
        multi.setopt(pycurl.M_MAX_HOST_CONNECTIONS, workers)

        queue = list(pending)
        state: dict[int, dict[str, Any]] = {}
        active = 0

        try:
            while queue or active > 0:
                # Launch new transfers up to `workers`.
                while queue and active < workers:
                    idx, link, tmp, dest = queue.pop(0)

                    try:
                        file_obj = tmp.open("wb")
                    except OSError as exc:
                        logger.error("cannot open {}: {}", tmp, exc)
                        continue

                    display = _display_name(dest.name)
                    task_id = progress.add_task("download", name=display, total=None)
                    curl = pycurl.Curl()

                    def write_cb(data, _f=file_obj):
                        # Chunked write as data arrives.
                        _f.write(data)
                        return len(data)

                    def progress_cb(dt, dd, ut, ud, _task=task_id, _p=progress):
                        if dt and dt > 0:
                            _p.update(_task, completed=dd, total=dt)
                        elif dd:
                            _p.update(_task, completed=dd)
                        return 0

                    curl.setopt(pycurl.URL, link.clean_url)
                    curl.setopt(pycurl.FOLLOWLOCATION, True)
                    curl.setopt(pycurl.CONNECTTIMEOUT, 15)
                    curl.setopt(pycurl.TIMEOUT, 600)
                    curl.setopt(pycurl.WRITEFUNCTION, write_cb)
                    curl.setopt(pycurl.NOPROGRESS, 0)
                    curl.setopt(pycurl.XFERINFOFUNCTION, progress_cb)
                    curl.setopt(pycurl.USERAGENT, "pkg-downloader/1.0")

                    try:
                        multi.add_handle(curl)
                    except Exception as exc:
                        file_obj.close()
                        try:
                            curl.close()
                        except Exception:
                            pass
                        tmp.unlink(missing_ok=True)
                        progress.update(task_id, name=f"[red]✗ {display}")
                        errors.append(f"{link.clean_url}: {exc}")
                        continue

                    state[id(curl)] = {
                        "idx": idx,
                        "link": link,
                        "tmp": tmp,
                        "dest": dest,
                        "file": file_obj,
                        "task": task_id,
                        "curl": curl,
                        "display": display,
                    }
                    active += 1

                if active == 0:
                    break

                ret, _num_handles = multi.perform()
                if ret == pycurl.E_CALL_MULTI_PERFORM:
                    continue
                multi.select(0.05)

                while True:
                    num_q, ok_list, err_list = multi.info_read()

                    for curl in ok_list:
                        s = state.pop(id(curl))
                        multi.remove_handle(curl)
                        active -= 1
                        code = curl.getinfo(pycurl.RESPONSE_CODE)
                        try:
                            s["file"].close()
                        except Exception:
                            pass
                        try:
                            curl.close()
                        except Exception:
                            pass

                        tmp = s["tmp"]
                        dest = s["dest"]
                        link = s["link"]
                        display = s["display"]
                        task_id = s["task"]

                        if code >= 400:
                            tmp.unlink(missing_ok=True)
                            progress.update(task_id, name=f"[red]✗ {display}")
                            errors.append(f"{link.clean_url}: HTTP {code}")
                            continue

                        try:
                            tmp.replace(dest)
                            if not verify_hash(dest, link.hashes):
                                dest.unlink(missing_ok=True)
                                raise ValueError("hash mismatch")
                            if not verify_archive(dest):
                                dest.unlink(missing_ok=True)
                                raise ValueError("archive integrity failed")
                        except (OSError, ValueError) as exc:
                            tmp.unlink(missing_ok=True)
                            dest.unlink(missing_ok=True)
                            progress.update(task_id, name=f"[red]✗ {display}")
                            errors.append(f"{link.clean_url}: {exc}")
                            continue

                        progress.update(task_id, name=f"[green]✓ {display}")
                        results[s["idx"]] = dest
                        logger.success("downloaded {}", dest)

                    for curl, errno, errmsg in err_list:
                        s = state.pop(id(curl))
                        multi.remove_handle(curl)
                        active -= 1
                        try:
                            s["file"].close()
                        except Exception:
                            pass
                        try:
                            curl.close()
                        except Exception:
                            pass
                        s["tmp"].unlink(missing_ok=True)
                        progress.update(s["task"], name=f"[red]✗ {s['display']}")
                        errors.append(f"{s['link'].clean_url}: curl error {errno}: {errmsg}")

                    if num_q == 0:
                        break
        finally:
            # Any handles still in `state` were interrupted by an exception.
            for s in state.values():
                try:
                    s["file"].close()
                except Exception:
                    pass
                try:
                    s["curl"].close()
                except Exception:
                    pass
                s["tmp"].unlink(missing_ok=True)
                errors.append(f"{s['link'].clean_url}: interrupted")
            multi.close()

    for e in errors:
        logger.error("{}", e)

    return results


def parse_version_arg(value: str) -> tuple[int, ...]:
    "Parse version arg."
    parts = value.split(".")
    if not parts or any(not p.isdigit() for p in parts):
        msg = f"invalid Python version: {value!r}"
        raise argparse.ArgumentTypeError(msg)
    return tuple(int(p) for p in parts)


def parse_requirements_file(path: Path) -> list[str]:
    "Parse requirements file."
    try:
        content = path.expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read requirements file {path}: {exc}"
        raise RuntimeError(msg) from exc
    result: list[str] = []
    for line in content.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            result.append(line)
    return result


def build_parser() -> argparse.ArgumentParser:
    "Build parser."
    p = argparse.ArgumentParser(
        prog="pkg-downloader",
        description="Find and download Python distributions from PEP 503/691 indexes.",
    )
    p.add_argument(
        "requirement",
        nargs="*",
        metavar="REQ",
        help="one or more PEP 508 requirements, e.g. requests>=2.28 black==24.3.0",
    )
    p.add_argument(
        "-f",
        "--file",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="file containing one requirement per line; repeatable",
    )
    p.add_argument(
        "-i",
        "--index-url",
        action="append",
        default=[],
        metavar="URL",
        help="simple index URL, repeatable; defaults to PyPI, Tsinghua, Yandex",
    )
    p.add_argument(
        "-L",
        "--find-link",
        action="append",
        default=[],
        metavar="DIR_OR_URL",
        help="additional find-links source, repeatable",
    )
    p.add_argument(
        "-d",
        "--dest",
        type=Path,
        default=Path(),
        metavar="DIR",
        help="download destination directory",
    )
    p.add_argument(
        "--py-version",
        type=parse_version_arg,
        default=None,
        metavar="X.Y",
        help="override target Python version",
    )
    p.add_argument(
        "--platform",
        action="append",
        default=[],
        metavar="TAG",
        help="override platform tag, repeatable",
    )
    p.add_argument(
        "--abi",
        action="append",
        default=[],
        metavar="TAG",
        help="override ABI tag, repeatable",
    )
    p.add_argument(
        "--impl",
        default=None,
        metavar="IMPL",
        help="override Python implementation",
    )
    p.add_argument(
        "--no-binary",
        action="store_true",
        help="exclude wheels",
    )
    p.add_argument(
        "--only-binary",
        action="store_true",
        help="wheels only",
    )
    p.add_argument(
        "--pre",
        action="store_true",
        help="allow prereleases",
    )
    p.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="download every matching candidate",
    )
    p.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="print JSON metadata",
    )
    p.add_argument(
        "--no-download",
        action="store_true",
        help="resolve only, no download",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="debug logging",
    )
    return p


def setup_logging(verbose: bool) -> None:
    "Set up logging."
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="<level>{level: <8}</level> {message}",
    )


def main(argv: list[str] | None = None) -> int:
    "Run the command-line interface."
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.no_binary and args.only_binary:
        parser.error("--no-binary and --only-binary are mutually exclusive")

    raw_requirements: list[str] = list(args.requirement)
    for path in args.file:
        try:
            raw_requirements.extend(parse_requirements_file(path))
        except RuntimeError as exc:
            logger.error("{}", exc)
            # continue to next file
            continue

    if not raw_requirements:
        logger.error("no requirements specified (use positional args or -f FILE)")
        return 1

    requirements: list[Requirement] = []
    for raw in raw_requirements:
        try:
            requirements.append(Requirement(raw))
        except InvalidRequirement as exc:
            logger.error("invalid requirement {!r}: {}", raw, exc)
            # continue to next requirement
            continue

    if not requirements:
        logger.error("no valid requirements after filtering")
        return 1

    target = TargetPython(
        py_version=args.py_version,
        abis=args.abi or None,
        implementation=args.impl,
        platforms=args.platform or None,
    )
    dest_dir = args.dest.expanduser().resolve()

    with Finder(
        indexes=args.index_url,
        find_links=args.find_link,
        target=target,
    ) as finder:
        all_pkgs: list[Package] = []
        failures = 0
        for req in requirements:
            matches = finder.find(
                req,
                pre=True if args.pre else None,
                no_binary=args.no_binary,
                only_binary=args.only_binary,
            )
            if not matches:
                logger.error("no matching distributions for {}", req)
                failures += 1
                # continue to next requirement
                continue
            if not args.all:
                matches = matches[:1]
            all_pkgs.extend(matches)

    results: list[dict[str, Any]] = [pkg.as_dict() for pkg in all_pkgs]

    if not args.no_download and all_pkgs:
        try:
            paths = download_all(
                [pkg.link for pkg in all_pkgs],
                dest_dir,
                workers=WORKERS,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.exception("download batch failed: {}", exc)
            return 2
        for meta, path in zip(results, paths):
            meta["local_path"] = str(path) if path is not None else None
            if path is None:
                failures += 1

    if args.json or args.no_download:
        out: Any = results[0] if len(results) == 1 else results
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for r in results:
            if r.get("local_path"):
                print(r["local_path"])

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
