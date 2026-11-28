#!/data/data/com.termux/files/usr/bin/env python
"""
resolve_reqs.py — resolve the full dependency tree for one or more packages
and emit a lock file (requirements.txt, uv-lock-style TOML, JSON, poetry,
conda, markdown, or a custom Jinja2 template).

Usage:
    python resolve_reqs.py libretranslate
    python resolve_reqs.py libretranslate[test] --format uv --with-hashes all
    python resolve_reqs.py -f pkgs.txt -o reqs.txt --audit --check
    python resolve_reqs.py flask --diff requirements.old.txt
    python resolve_reqs.py flask --explain werkzeug
    python resolve_reqs.py flask --backend uv --python-version 3.11

Exit codes:
    0 success
    1 resolution error
    2 argument error
    3 drift detected (--check)
    4 vulnerability / license violation
"""

from __future__ import annotations
import argparse
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Literal
import urllib.request

from dh import atomic_write
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------
try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name as _canonicalize_name

    _HAVE_PACKAGING = True
except ImportError:  # pragma: no cover
    _HAVE_PACKAGING = False

try:
    import tomllib as _tomllib
except ImportError:  # pragma: no cover
    try:
        import tomli as _tomllib
    except ImportError:
        _tomllib = None

try:
    from jinja2 import Template as _JinjaTemplate

    _HAVE_JINJA = True
except ImportError:  # pragma: no cover
    _HAVE_JINJA = False


# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------
EXIT_OK: int = 0
EXIT_RESOLVE: int = 1
EXIT_ARG: int = 2
EXIT_DRIFT: int = 3
EXIT_VULN: int = 4

USER_AGENT: str = "resolve_reqs.py/2.0 (+https://pypi.org/)"


def canonical(name: str) -> str:
    if _HAVE_PACKAGING:
        return _canonicalize_name(name)
    return re.sub(r"[-_.]+", "-", name).lower()


def dep_name(req_str: str) -> str | None:
    """Extract the canonical package name from a PEP 508 requirement string."""
    if _HAVE_PACKAGING:
        try:
            return canonical(Requirement(req_str).name)
        except Exception:
            return None
    first = re.split(r"[\s\[<>=!~;()]", req_str, 1)[0]
    return canonical(first) if first else None


def eprint(*a: Any, **kw: Any) -> None:
    kw.setdefault("file", sys.stderr)
    print(*a, **kw)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Package:
    """Store name, version, url, hashes, requires_dist and related state."""

    name: str
    version: str
    url: str = ""
    hashes: dict[str, str] = field(default_factory=dict)
    requires_dist: list[str] = field(default_factory=list)
    requires_python: str = ""
    license: str = ""
    summary: str = ""
    requested: bool = False
    is_yanked: bool = False
    direct: bool = False

    @property
    def key(self) -> str:
        """Return the key."""
        return canonical(self.name)

    @property
    def sha256(self) -> str | None:
        """Return the sha256."""
        return self.hashes.get("sha256")


@dataclass
class ResolveResult:
    """Store packages, specs, python_version, platform, backend."""

    packages: list[Package]
    specs: list[str]
    python_version: str
    platform: str
    backend: str


# ---------------------------------------------------------------------------
# Parsing pip / uv JSON reports
# ---------------------------------------------------------------------------
def parse_pip_report(data: dict[str, Any]) -> list[Package]:
    """Parse pip report."""
    out: list[Package] = []
    for item in data.get("install", []) or []:
        meta = item.get("metadata", {}) or {}
        name = meta.get("name")
        version = meta.get("version")
        if not name or not version:
            continue
        di = item.get("download_info", {}) or {}
        ai = di.get("archive_info", {}) or {}
        hashes: dict[str, str] = {}
        if isinstance(ai.get("hashes"), dict):
            hashes = {str(k): str(v) for k, v in ai["hashes"].items()}
        elif ai.get("hash"):
            h = str(ai["hash"])
            if "=" in h:
                alg, val = h.split("=", 1)
                hashes[alg] = val
        out.append(
            Package(
                name=name,
                version=version,
                url=di.get("url", "") or "",
                hashes=hashes,
                requires_dist=list(meta.get("requires_dist") or []),
                requires_python=str(meta.get("requires_python") or ""),
                license=str(meta.get("license") or ""),
                summary=str(meta.get("summary") or ""),
                requested=bool(item.get("requested", False)),
                is_yanked=bool(item.get("is_yanked", False)),
                direct=bool(item.get("is_direct", False)),
            )
        )
    return out


def parse_specs_file(path: Path) -> list[str]:
    """Parse specs file."""
    specs: list[str] = []
    for line in path.read_text().splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            specs.append(s)
    return specs


def parse_requirement_lines(text: str) -> list[str]:
    """Extract requirement lines from a requirements.txt-ish blob."""
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "-")):
            continue
        s = s.split(" #", 1)[0].strip()
        if s:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
def _run(cmd: list[str], env: dict[str, str] | None = None, verbose: bool = False) -> subprocess.CompletedProcess[str]:
    if verbose:
        eprint("[cmd]", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _extract_json(out: str) -> str:
    out = out.strip()
    if out.startswith("{"):
        return out
    idx = out.find("{")
    if idx == -1:
        raise SystemExit(f"no JSON report found in output:\n{out}")
    return out[idx:]


def resolve_via_pip(
    specs: Sequence[str],
    *,
    pre: bool = False,
    constraints: Sequence[str] = (),
    index_url: str | None = None,
    extra_index_urls: Sequence[str] = (),
    extra_env: dict[str, str] | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    cache_dir: str | None = None,
    offline: bool = False,
    no_deps: bool = False,
    upgrade: bool = False,
    upgrade_package: Sequence[str] = (),
    verbose: bool = False,
) -> dict[str, Any]:
    """Resolve via pip."""
    cmd: list[str] = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--quiet",
        "--disable-pip-version-check",
        "--report",
        "-",
    ]
    if pre:
        cmd.append("--pre")
    if no_deps:
        cmd.append("--no-deps")
    if upgrade:
        cmd.append("--upgrade")
    for up in upgrade_package:
        cmd.extend(["--upgrade", up])
    for c in constraints:
        cmd.extend(["-c", c])
    if index_url:
        cmd.extend(["--index-url", index_url])
    for u in extra_index_urls:
        cmd.extend(["--extra-index-url", u])
    if timeout is not None:
        cmd.extend(["--timeout", str(timeout)])
    if retries is not None:
        cmd.extend(["--retries", str(retries)])
    if cache_dir:
        cmd.extend(["--cache-dir", cache_dir])
    cmd.extend(specs)

    env = os.environ.copy()
    if offline:
        env["PIP_NO_INDEX"] = "1"
    if extra_env:
        env.update(extra_env)

    proc = _run(cmd, env=env, verbose=verbose)
    if proc.returncode != 0:
        eprint(proc.stderr)
        raise SystemExit(EXIT_RESOLVE)
    return json.loads(_extract_json(proc.stdout))


def resolve_via_uv(
    specs: Sequence[str],
    *,
    pre: bool = False,
    constraints: Sequence[str] = (),
    index_url: str | None = None,
    extra_index_urls: Sequence[str] = (),
    python_version: str | None = None,
    platform: str | None = None,
    upgrade: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Resolve via uv."""
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("backend 'uv' requested but 'uv' is not on PATH")
    cmd = [
        uv,
        "pip",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--quiet",
        "--report",
        "-",
    ]
    if pre:
        cmd.append("--prerelease=allow")
    for c in constraints:
        cmd.extend(["--constraint", c])
    if index_url:
        cmd.extend(["--index-url", index_url])
    for u in extra_index_urls:
        cmd.extend(["--extra-index-url", u])
    if python_version:
        cmd.extend(["--python-version", python_version])
    if platform:
        cmd.extend(["--python-platform", platform])
    if upgrade:
        cmd.append("--upgrade")
    cmd.extend(specs)
    proc = _run(cmd, verbose=verbose)
    if proc.returncode != 0:
        eprint(proc.stderr)
        raise SystemExit(EXIT_RESOLVE)
    return json.loads(_extract_json(proc.stdout))


def resolve_via_pip_tools(
    specs: Sequence[str],
    *,
    python_version: str | None = None,
    upgrade: bool = False,
    verbose: bool = False,
) -> list[Package]:
    """Resolve via pip tools."""
    pc = shutil.which("pip-compile")
    if not pc:
        raise SystemExit("backend 'pip-tools' requested but 'pip-compile' is not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".in", delete=False) as f:
        f.write("\n".join(specs) + "\n")
        infile = f.name
    try:
        cmd = [pc, "--quiet", "--output-file=-", infile]
        if python_version:
            cmd.extend(["--python-version", python_version])
        if upgrade:
            cmd.append("--upgrade")
        proc = _run(cmd, verbose=verbose)
        if proc.returncode != 0:
            eprint(proc.stderr)
            raise SystemExit(EXIT_RESOLVE)
        lines = parse_requirement_lines(proc.stdout)
    finally:
        os.unlink(infile)

    pkgs: list[Package] = []
    for line in lines:
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)", line)
        if m:
            pkgs.append(Package(name=m.group(1), version=m.group(2), requested=False))
    return pkgs


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------
def filter_packages(
    pkgs: Sequence[Package],
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    top_only: bool = False,
    transitive_only: bool = False,
    skip_optional: bool = False,
) -> list[Package]:
    out: list[Package] = []
    for p in pkgs:
        if top_only and not p.requested:
            continue
        if transitive_only and p.requested:
            continue
        if skip_optional and not p.requested and p.name.lower() in {"pytest", "tox", "pre-commit"}:
            # conservative heuristic — real optional detection needs full marker eval
            continue
        if include and not any(fnmatch.fnmatchcase(p.name.lower(), pat.lower()) for pat in include):
            continue
        if exclude and any(fnmatch.fnmatchcase(p.name.lower(), pat.lower()) for pat in exclude):
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------
def format_requirements(
    pkgs: Sequence[Package],
    *,
    with_hashes: Literal["none", "all", "top", "both"] = "none",
    no_pin: bool = False,
    use_url: bool = False,
    show_licenses: bool = False,
    show_size: bool = False,
    markers: bool = False,
    header: str | None = None,
) -> str:
    """Format requirements."""
    lines: list[str] = []
    if header:
        lines.extend(f"# {ln}" for ln in header.splitlines())
        lines.append("")
    for p in pkgs:
        if use_url and p.url:
            line = f"{p.name} @ {p.url}"
        elif no_pin:
            line = p.name
        else:
            line = f"{p.name}=={p.version}"

        bits: list[str] = []
        if show_licenses and p.license:
            bits.append(f"license: {p.license}")
        if show_size and p.summary:
            bits.append(p.summary[:60])
        if bits:
            line = line + "  # " + "; ".join(bits)

        wants_hash = (
            with_hashes == "all"
            or (with_hashes == "top" and p.requested)
            or (with_hashes == "both" and (p.requested or p.sha256))
        )
        if wants_hash and p.sha256:
            line = line + f" \\\n    --hash=sha256:{p.sha256}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def format_json(res: ResolveResult) -> str:
    """Format json."""
    return (
        json.dumps(
            {
                "generated": datetime.now(UTC).isoformat(),
                "python_version": res.python_version,
                "platform": res.platform,
                "backend": res.backend,
                "specs": res.specs,
                "packages": [asdict(p) for p in res.packages],
            },
            indent=2,
        )
        + "\n"
    )


def format_uv_toml(res: ResolveResult) -> str:
    """Format uv toml."""
    parts: list[str] = [
        "# Mini uv.lock-style output produced by resolve_reqs.py",
        "version = 1",
        f'requires-python = ">={res.python_version}"',
        f'# generated = "{datetime.now(UTC).isoformat()}"',
        "",
    ]
    for p in res.packages:
        parts.append("[[package]]")
        parts.append(f'name = "{p.name}"')
        parts.append(f'version = "{p.version}"')
        if p.url:
            parts.append(f'source = {{ url = "{p.url}" }}')
        if p.sha256:
            parts.append(f'hash = "sha256:{p.sha256}"')
        if p.requires_python:
            parts.append(f'requires-python = "{p.requires_python}"')
        if p.requires_dist:
            deps = ", ".join(f'"{d}"' for d in p.requires_dist)
            parts.append(f"dependencies = [{deps}]")
        parts.append("")
    return "\n".join(parts)


def format_poetry(res: ResolveResult) -> str:
    """Format poetry."""
    parts = ["[tool.poetry.dependencies]", f'python = "^{res.python_version}"']
    for p in res.packages:
        if p.requested:
            parts.append(f'{p.name} = "^{p.version}"')
    parts.extend(["", "[tool.poetry.group.dev.dependencies]"])
    for p in res.packages:
        if not p.requested:
            parts.append(f'{p.name} = "^{p.version}"')
    return "\n".join(parts) + "\n"


def format_conda(res: ResolveResult) -> str:
    """Format conda."""
    parts = ["@EXPLICIT"]
    for p in res.packages:
        if p.url:
            entry = p.url
            if p.sha256:
                entry += f"#{p.sha256}"
            parts.append(entry)
    return "\n".join(parts) + "\n"


def format_markdown(res: ResolveResult) -> str:
    """Format markdown."""
    parts = ["| Package | Version | Direct | License |", "|---|---|---|---|"]
    for p in res.packages:
        parts.append(f"| {p.name} | {p.version} | {'yes' if p.requested else ''} | {p.license} |")
    return "\n".join(parts) + "\n"


def format_custom(res: ResolveResult, template_path: str) -> str:
    """Format custom."""
    if not _HAVE_JINJA:
        raise SystemExit("--template requires jinja2")
    tpl = _JinjaTemplate(Path(template_path).read_text())
    return tpl.render(
        packages=[asdict(p) for p in res.packages],
        specs=res.specs,
        python_version=res.python_version,
        platform=res.platform,
        backend=res.backend,
        generated=datetime.now(UTC).isoformat(),
    )


def render_output(res: ResolveResult, args: argparse.Namespace) -> str:
    """Render output."""
    if args.template:
        return format_custom(res, args.template)
    if args.format == "json":
        return format_json(res)
    if args.format == "uv":
        return format_uv_toml(res)
    if args.format == "poetry":
        return format_poetry(res)
    if args.format == "conda":
        return format_conda(res)
    if args.format == "markdown":
        return format_markdown(res)

    header = None
    if not args.no_header:
        header = (
            f"Generated by resolve_reqs.py on "
            f"{datetime.now(UTC).isoformat()}\n"
            f"Specs: {', '.join(res.specs)}\n"
            f"Backend: {res.backend}  Python: {res.python_version}  "
            f"Platform: {res.platform}"
        )
    return format_requirements(
        res.packages,
        with_hashes=args.with_hashes,
        no_pin=args.no_pin,
        use_url=args.url,
        show_licenses=args.show_licenses,
        show_size=args.show_size,
        markers=args.markers,
        header=header,
    )


# ---------------------------------------------------------------------------
# pyproject.toml integration
# ---------------------------------------------------------------------------
def load_pyproject(path: Path) -> dict[str, Any]:
    """Load pyproject."""
    if _tomllib is None:
        return {}
    try:
        return _tomllib.loads(path.read_text())
    except Exception as e:
        eprint(f"failed to parse {path}: {e}")
        return {}


def specs_from_pyproject(data: dict[str, Any]) -> list[str]:
    specs: list[str] = []
    proj = data.get("project", {}) or {}
    for d in proj.get("dependencies", []) or []:
        specs.append(d)
    for items in (proj.get("optional-dependencies", {}) or {}).values():
        for d in items or []:
            specs.append(d)
    return specs


# ---------------------------------------------------------------------------
# Diff / check / explain / verify / audit
# ---------------------------------------------------------------------------
def load_lockfile(path: Path) -> dict[str, str]:
    """Load lockfile."""
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "-", "@")):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)", s)
        if m:
            result[canonical(m.group(1))] = m.group(2)
    return result


def diff_locks(old: dict[str, str], new: dict[str, str]) -> str:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    bumped = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    lines: list[str] = []
    if added:
        lines.append("Added:")
        lines.extend(f"  + {k}=={new[k]}" for k in added)
    if removed:
        lines.append("Removed:")
        lines.extend(f"  - {k}=={old[k]}" for k in removed)
    if bumped:
        lines.append("Upgraded:")
        lines.extend(f"  ~ {k}: {old[k]} -> {new[k]}" for k in bumped)
    return "\n".join(lines) if lines else "(no changes)"


def explain(pkgs: Sequence[Package], target: str) -> str:
    target_key = canonical(target)
    found: Package | None = None
    for p in pkgs:
        if p.key == target_key:
            found = p
            break
    if not found:
        return f"{target} not found in resolved tree"
    header = f"{found.name}=={found.version}" + (" (top-level)" if found.requested else "")
    requirers: list[str] = []
    for p in pkgs:
        for dep in p.requires_dist:
            if dep_name(dep) == target_key:
                requirers.append(f"  <- {p.name}=={p.version} requires {dep}")
    return "\n".join([header] + (requirers or ["  (no direct requirers)"]))


def why_not(pkgs: Sequence[Package], arg: str) -> str:
    if "==" not in arg:
        return "--why-not expects PKG==VERSION"
    name, ver = arg.split("==", 1)
    k = canonical(name)
    for p in pkgs:
        if p.key == k:
            return (
                f"{p.name} resolved at {p.version}, not {ver}.\n"
                f"To force it, rerun with --constraint 'file' or add a direct spec."
            )
    return f"{name} not present in the resolved tree"


def verify_hashes(pkgs: Sequence[Package]) -> int:
    """Verify hashes."""
    failures = 0
    for p in pkgs:
        if not p.sha256:
            continue
        url = f"https://pypi.org/pypi/{p.name}/{p.version}/json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
        except Exception as e:
            eprint(f"{p.name}=={p.version}: fetch failed: {e}")
            failures += 1
            continue
        digests = {
            u["digests"]["sha256"]
            for u in data.get("urls", [])
            if isinstance(u.get("digests"), dict) and "sha256" in u["digests"]
        }
        if p.sha256 not in digests:
            eprint(f"{p.name}=={p.version}: hash mismatch")
            failures += 1
    return EXIT_VULN if failures else EXIT_OK


def run_audit(path: Path) -> int:
    """Run audit."""
    aud = shutil.which("pip-audit")
    if not aud:
        eprint("pip-audit not on PATH; skipping")
        return 0
    return subprocess.run([aud, "-r", str(path)]).returncode


def license_check(res: ResolveResult, mode: str, allow_list: str) -> int:
    allowed = {x.strip().lower() for x in allow_list.split(",") if x.strip()}
    violations: list[Package] = []
    for p in res.packages:
        lic = (p.license or "").lower()
        if not lic:
            continue
        if mode == "allow" and not any(a in lic for a in allowed) or mode == "deny" and any(a in lic for a in allowed):
            violations.append(p)
    if violations:
        eprint(f"License violations ({mode}):")
        for p in violations:
            eprint(f"  {p.name}=={p.version}  ({p.license})")
        return EXIT_VULN
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Build parser."""
    ap = argparse.ArgumentParser(
        description="Resolve package dependency trees into a lock file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--version", action="version", version="resolve_reqs.py 2.0")

    # inputs
    ap.add_argument("package", nargs="?", help="package to resolve")
    ap.add_argument("-f", "--file", help="file with one package per line")
    ap.add_argument("-o", "--output", default="requirements.txt")
    ap.add_argument("--config", help="pyproject.toml to read deps from")

    # output
    ap.add_argument(
        "--format",
        default="requirements",
        choices=["requirements", "json", "toml", "uv", "poetry", "conda", "markdown"],
    )
    ap.add_argument("--template", help="Jinja2 template file")
    ap.add_argument("--lock", action="store_true", help="write richer lock output")
    ap.add_argument("--with-hashes", default="none", choices=["none", "all", "top", "both"])
    ap.add_argument("--no-pin", action="store_true")
    ap.add_argument("--group", help="suffix for grouped output")
    ap.add_argument("--separate-dev", action="store_true", help="write <out> and <out>-dev")
    ap.add_argument("--top-level-only", action="store_true")
    ap.add_argument("--transitive-only", action="store_true")
    ap.add_argument("--url", action="store_true", help="emit 'name @ url'")
    ap.add_argument("--show-licenses", action="store_true")
    ap.add_argument("--show-size", action="store_true")
    ap.add_argument("--markers", action="store_true")
    ap.add_argument("--no-header", action="store_true")
    ap.add_argument("--json-out", help="write summary JSON (use '-' for stdout)")

    # resolver
    ap.add_argument("--backend", default="pip", choices=["pip", "uv", "pip-tools"])
    ap.add_argument("--index-url")
    ap.add_argument("--extra-index-url", action="append", default=[])
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--constraint", action="append", default=[])
    ap.add_argument("--platform", choices=["win", "linux", "macos"])
    ap.add_argument("--python-version")
    ap.add_argument("--min-python")
    ap.add_argument("--abi")
    ap.add_argument("--no-deps", action="store_true")
    ap.add_argument("--upgrade", action="store_true")
    ap.add_argument("--upgrade-package", action="append", default=[])
    ap.add_argument("--resolve-from")
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--frozen", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--cache-dir")
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--retries", type=int)
    ap.add_argument("--env-file")

    # selection
    ap.add_argument("--extras", help="comma-separated extras for positional pkg")
    ap.add_argument("--include", action="append", default=[])
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--skip-optional", action="store_true")

    # operations
    ap.add_argument("--check", action="store_true", help="CI drift detection")
    ap.add_argument("--diff", metavar="OLD_FILE")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--vulnerable-exit", action="store_true")
    ap.add_argument("--license-check", choices=["allow", "deny"])
    ap.add_argument("--license-list", default="")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--verify", action="store_true", help="verify hashes against PyPI")
    ap.add_argument("--explain", metavar="PKG")
    ap.add_argument("--why-not", metavar="PKG==VER")
    ap.add_argument("--conflicts", action="store_true")
    ap.add_argument("--emit-marker-table", action="store_true")

    # UX
    ap.add_argument("-y", "--yes", action="store_true")
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--print-pip-command", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true")
    return ap


def collect_specs(args: argparse.Namespace) -> list[str]:
    """Collect specs."""
    specs: list[str] = []
    if args.package:
        pkg = args.package
        if args.extras and "[" not in pkg:
            pkg = f"{pkg}[{args.extras}]"
        specs.append(pkg)
    if args.file:
        path = Path(args.file)
        if not path.is_file():
            raise SystemExit(EXIT_ARG)
        specs.extend(parse_specs_file(path))
    if args.config:
        specs.extend(specs_from_pyproject(load_pyproject(Path(args.config))))
    return specs


def detect_platform() -> str:
    """Detect platform."""
    if sys.platform.startswith("win"):
        return "win"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def do_resolve(args: argparse.Namespace, specs: Sequence[str]) -> ResolveResult:
    if args.print_pip_command:
        eprint(f"[backend={args.backend}] specs={list(specs)}")

    if args.backend == "uv":
        data = resolve_via_uv(
            specs,
            pre=args.pre,
            constraints=args.constraint,
            index_url=args.index_url,
            extra_index_urls=args.extra_index_url,
            python_version=args.python_version or args.min_python,
            platform=args.platform,
            upgrade=args.upgrade,
            verbose=args.verbose,
        )
        pkgs = parse_pip_report(data)
    elif args.backend == "pip-tools":
        pkgs = resolve_via_pip_tools(
            specs,
            python_version=args.python_version or args.min_python,
            upgrade=args.upgrade,
            verbose=args.verbose,
        )
    else:
        extra_env: dict[str, str] = {}
        if args.env_file:
            for line in Path(args.env_file).read_text().splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                extra_env[k.strip()] = v.strip()
        data = resolve_via_pip(
            specs,
            pre=args.pre,
            constraints=args.constraint,
            index_url=args.index_url,
            extra_index_urls=args.extra_index_url,
            extra_env=extra_env,
            timeout=args.timeout,
            retries=args.retries,
            cache_dir=args.cache_dir,
            offline=args.offline,
            no_deps=args.no_deps,
            upgrade=args.upgrade,
            upgrade_package=args.upgrade_package,
            verbose=args.verbose,
        )
        pkgs = parse_pip_report(data)

    pkgs = filter_packages(
        pkgs,
        include=args.include,
        exclude=args.exclude,
        top_only=args.top_level_only,
        transitive_only=args.transitive_only,
        skip_optional=args.skip_optional,
    )
    if not args.no_header:
        pkgs.sort(key=lambda p: p.name.lower())

    return ResolveResult(
        packages=pkgs,
        specs=list(specs),
        python_version=(args.python_version or args.min_python or f"{sys.version_info.major}.{sys.version_info.minor}"),
        platform=args.platform or detect_platform(),
        backend=args.backend,
    )


def write_output(text: str, output: Path, args: argparse.Namespace) -> None:
    """Write output."""
    if args.stdout or str(output) == "-":
        sys.stdout.write(text)
        return
    if output.exists() and not args.yes and not args.quiet:
        try:
            ans = input(f"{output} exists — overwrite? [y/N] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("y", "yes"):
            raise SystemExit(EXIT_OK)
    if args.backup and output.exists():
        bak = output.with_suffix(output.suffix + ".bak")
        output.rename(bak)
        eprint(f"backed up -> {bak}")
    atomic_write(output, text)
    eprint(f"Wrote {output}")


def load_existing_as_result(path: Path) -> ResolveResult:
    """Used by --frozen: read an existing lock file and reformat it."""
    data = load_lockfile(path)
    pkgs = [Package(name=n, version=v) for n, v in data.items()]
    return ResolveResult(
        packages=pkgs,
        specs=[str(path)],
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        platform=detect_platform(),
        backend="frozen",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    args = build_parser().parse_args(argv)

    # implicit incremental: use existing output as a constraint seed
    if args.incremental and not args.resolve_from and Path(args.output).exists():
        args.resolve_from = args.output
    if args.resolve_from:
        args.constraint.append(args.resolve_from)

    # --conflicts / --emit-marker-table are honest no-ops with explanation
    if args.conflicts:
        eprint(
            "note: pip's dry-run report does not expose resolver conflicts; "
            "install pip>=24 or use --backend pip-tools for verbose conflict output"
        )
    if args.emit_marker_table:
        eprint("note: --emit-marker-table is meaningful only with a multi-platform resolver (use --backend uv)")

    # --frozen: skip resolution
    if args.frozen:
        if not Path(args.output).exists():
            eprint(f"--frozen requires an existing {args.output}")
            return EXIT_ARG
        res = load_existing_as_result(Path(args.output))
        text = render_output(res, args)
        write_output(text, Path(args.output), args)
        return EXIT_OK

    specs = collect_specs(args)
    if not specs:
        eprint("provide a package, -f FILE, or --config pyproject.toml")
        return EXIT_ARG

    if args.verbose:
        eprint(f"specs: {specs}")

    res = do_resolve(args, specs)

    # --explain short-circuit
    if args.explain:
        print(explain(res.packages, args.explain))
        return EXIT_OK

    if args.why_not:
        print(why_not(res.packages, args.why_not))
        return EXIT_OK

    text = render_output(res, args)

    # --diff
    if args.diff:
        other = Path(args.diff)
        if not other.exists():
            eprint(f"--diff file not found: {other}")
            return EXIT_ARG
        old = load_lockfile(other)
        new = {p.key: p.version for p in res.packages}
        print(diff_locks(old, new))
        return EXIT_OK

    # --check drift detection
    if args.check:
        out = Path(args.output)
        if not out.exists():
            eprint(f"{out} does not exist (drift)")
            return EXIT_DRIFT
        if out.read_text() != text:
            eprint(f"drift detected in {out}")
            return EXIT_DRIFT
        eprint(f"{out} up to date")
        return EXIT_OK

    # --verify hashes
    if args.verify:
        rc = verify_hashes(res.packages)
        if rc != EXIT_OK:
            return rc

    # license policy
    if args.license_check:
        rc = license_check(res, args.license_check, args.license_list)
        if rc != EXIT_OK:
            return rc

    # write (with optional group / separate-dev handling)
    output = Path(args.output)
    if args.group:
        output = output.with_name(f"{output.stem}-{args.group}{output.suffix}")

    if args.separate_dev:
        main_pkgs = [p for p in res.packages if p.requested]
        dev_pkgs = [p for p in res.packages if not p.requested]
        main_res = ResolveResult(main_pkgs, res.specs, res.python_version, res.platform, res.backend)
        dev_res = ResolveResult(dev_pkgs, res.specs, res.python_version, res.platform, res.backend)
        write_output(render_output(main_res, args), output, args)
        dev_out = output.with_name(f"{output.stem}-dev{output.suffix}")
        write_output(render_output(dev_res, args), dev_out, args)
    else:
        write_output(text, output, args)

    # --audit (post-write, because pip-audit wants a file)
    if args.audit:
        rc = run_audit(output)
        if rc != 0:
            eprint(f"pip-audit exit code {rc}")
            if args.vulnerable_exit:
                return EXIT_VULN

    # summary JSON
    if args.json_out:
        payload = {
            "resolved": len(res.packages),
            "specs": res.specs,
            "backend": res.backend,
            "output": str(output),
        }
        if args.json_out == "-":
            print(json.dumps(payload))
        else:
            atomic_write(Path(args.json_out), json.dumps(payload, indent=2))

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
