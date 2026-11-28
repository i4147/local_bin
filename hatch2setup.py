#!/data/data/com.termux/files/usr/bin/env python
"""Convert a Hatch/Hatchling pyproject.toml to setuptools.

The tool is designed for one-shot migrations and CI enforcement. Supported
features include:

* In-place writes (atomic, permission- and mtime-preserving), plus ``--dry-run``,
  ``--stdout``, ``--diff``, ``--check`` (CI) and ``-o/--output`` modes.
* Backup (``-b/--backup``), ``-f/--force``, ``--keep-hatch`` and
  ``--no-version-file`` for fine-grained control.
* A stable warning-code system with ``--strict`` and ``--ignore-check``.
* Version-source conversion for Hatch:
    * ``source = "vcs"``   -> ``setuptools-scm`` (+ ``version-file``)
    * ``source = "path"``  -> ``[tool.setuptools.dynamic].version = {attr=...}``
    * ``source = "regex"`` -> resolved to a *static* ``project.version``
    * ``source = "code"``  -> explicit error (cannot be converted)
* Wheel target ``packages`` -> ``[tool.setuptools.packages.find]``.
* Wheel target ``sources``  -> ``[tool.setuptools.package-dir]``.
* Optional emissions: ``MANIFEST.in`` (``--emit-manifest``) and ``noxfile.py``
  (``--emit-nox``) derived from ``[tool.hatch.build.targets.sdist]`` and
  ``[tool.hatch.envs.*]``.
* Optional ``--validate`` (via validate-pyproject) and ``--build-check``
  (runs ``python -m build`` in a temp copy of the project).

Only Hatch/Hatchling is currently convertible; ``--from`` accepts only
``hatch`` and ``--to`` accepts only ``setuptools``.

Diagnostics are emitted on stderr by default; use ``--json`` for machine
readable output.
"""

from __future__ import annotations
import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
import difflib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

import tomlkit
from tomlkit.items import Table


# ---------------------------------------------------------------------------
# Backend identifiers
# ---------------------------------------------------------------------------

HATCH_BACKENDS: frozenset[str] = frozenset({"hatchling.build", "hatchling.build_meta"})
SETUPTOOLS_BACKENDS: frozenset[str] = frozenset({"setuptools.build_meta"})
KNOWN_BACKENDS: dict[str, str] = {
    "hatchling.build": "hatch",
    "hatchling.build_meta": "hatch",
    "setuptools.build_meta": "setuptools",
    "poetry.core.masonry.api": "poetry",
    "flit_core.buildapi": "flit",
    "pdm.backend": "pdm",
    "uv_build": "uv",
}


# ---------------------------------------------------------------------------
# Stable diagnostic codes
# ---------------------------------------------------------------------------

CODE_ALREADY_SETUPTOOLS = "already-setuptools"
CODE_UNKNOWN_BACKEND = "unknown-backend"
CODE_UNSUPPORTED_SOURCE = "unsupported-source"
CODE_HATCH_VERSION_CODE = "hatch-version-code"
CODE_HATCH_VERSION_REGEX = "hatch-version-regex"
CODE_HATCH_VERSION_PATH = "hatch-version-path"
CODE_HATCH_ENVS = "hatch-envs"
CODE_HATCH_HOOKS = "hatch-hooks"
CODE_HATCH_METADATA = "hatch-metadata"
CODE_WHEEL_PACKAGES = "wheel-packages"
CODE_WHEEL_SOURCES = "wheel-sources"
CODE_WHEEL_FORCE_INCLUDE = "wheel-force-include"
CODE_WHEEL_ARTIFACTS = "wheel-artifacts"
CODE_WHEEL_INCLUDE_EXCLUDE = "wheel-include-exclude"
CODE_SDIST_INCLUDE = "sdist-include"
CODE_VERSION_FILE_CONFLICT = "version-file-conflict"
CODE_VALIDATION = "validation-failed"
CODE_BUILD_CHECK = "build-check-failed"

ALL_CODES: frozenset[str] = frozenset({
    CODE_ALREADY_SETUPTOOLS,
    CODE_UNKNOWN_BACKEND,
    CODE_UNSUPPORTED_SOURCE,
    CODE_HATCH_VERSION_CODE,
    CODE_HATCH_VERSION_REGEX,
    CODE_HATCH_VERSION_PATH,
    CODE_HATCH_ENVS,
    CODE_HATCH_HOOKS,
    CODE_HATCH_METADATA,
    CODE_WHEEL_PACKAGES,
    CODE_WHEEL_SOURCES,
    CODE_WHEEL_FORCE_INCLUDE,
    CODE_WHEEL_ARTIFACTS,
    CODE_WHEEL_INCLUDE_EXCLUDE,
    CODE_SDIST_INCLUDE,
    CODE_VERSION_FILE_CONFLICT,
    CODE_VALIDATION,
    CODE_BUILD_CHECK,
})


# ---------------------------------------------------------------------------
# Errors, diagnostics, options, results
# ---------------------------------------------------------------------------


class ConversionError(Exception):
    """Raised when a conversion cannot proceed."""


@dataclass(frozen=True)
class Diagnostic:
    """A warning/error/info produced during conversion."""

    code: str
    level: str  # "info" | "warning" | "error"
    message: str


@dataclass
class ConvertOptions:
    """All options for a conversion run."""

    dry_run: bool = False
    stdout: bool = False
    diff: bool = False
    check: bool = False
    output: Path | None = None
    backup: bool = False
    force: bool = False
    strict: bool = False
    keep_hatch: bool = False
    no_version_file: bool = False
    setuptools_version: str = ">=61"
    scm_version: str = ">=8"
    validate: bool = False
    build_check: bool = False
    json_output: bool = False
    ignore_checks: frozenset[str] = frozenset()
    follow_symlinks: bool = False
    preserve_mtime: bool = False
    merge_policy: str = "abort"  # "abort" | "keep" | "overwrite"
    source_backend: str = "hatch"
    emit_manifest: bool = False
    emit_nox: bool = False
    verbose: int = 0
    quiet: int = 0


@dataclass
class ConversionResult:
    """Outcome of converting a single file."""

    path: Path
    original: str
    converted: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    changed: bool = False
    wrote_to: Path | None = None
    exit_code: int = 0

    @property
    def has_errors(self) -> bool:
        return any(d.level == "error" for d in self.diagnostics)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _diag(diags: list[Diagnostic], code: str, level: str, message: str) -> None:
    diags.append(Diagnostic(code=code, level=level, message=message))


def _get_table(parent: Any, key: str) -> Table | None:
    value = parent.get(key)
    return value if isinstance(value, Table) else None


def _ensure_table(parent: Any, key: str, *, super_table: bool = False) -> Table:
    """Return ``parent[key]`` as a Table, creating it if it does not exist."""
    existing = parent.get(key)
    if isinstance(existing, Table):
        return existing
    parent[key] = tomlkit.table(is_super_table=super_table)
    result = parent[key]
    if not isinstance(result, Table):
        raise ConversionError(f"failed to create table {key!r}")
    return result


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _safe_py_name(name: str) -> str:
    out = re.sub(r"\W", "_", name)
    if not out or out[0].isdigit():
        out = "env_" + out
    return out


def _path_to_attr(path: str) -> str | None:
    """Best-effort conversion of a file path to an import-path attr.

    ``src/pkg/__about__.py`` -> ``pkg.__about__.__version__``.
    """
    p = path.strip().replace("\\", "/")
    if not p.endswith(".py"):
        return None
    p = p[: -len(".py")]
    parts = [part for part in p.split("/") if part and part != "."]
    if not parts:
        return None
    if parts[0] == "src" and len(parts) > 1:
        parts = parts[1:]
    if not parts:
        return None
    return ".".join(parts) + ".__version__"


def _render_diff(original: str, converted: str, path: Path) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            converted.splitlines(keepends=True),
            fromfile=f"{path} (original)",
            tofile=f"{path} (setuptools)",
        )
    )


def _filter_diagnostics(diags: list[Diagnostic], opts: ConvertOptions) -> tuple[list[Diagnostic], bool]:
    """Apply --ignore-check and --strict. Returns (filtered, has_error)."""
    filtered: list[Diagnostic] = []
    has_error = False
    for d in diags:
        if d.code in opts.ignore_checks:
            continue
        level = "error" if (opts.strict and d.level == "warning") else d.level
        filtered.append(Diagnostic(code=d.code, level=level, message=d.message))
        if level == "error":
            has_error = True
    return filtered, has_error


def _print_diagnostics(result: ConversionResult, opts: ConvertOptions) -> None:
    if opts.json_output:
        return
    for d in result.diagnostics:
        if d.level == "info":
            if opts.stdout or opts.quiet >= 1 or opts.verbose < 1:
                continue
        elif d.level == "warning" and (opts.stdout or opts.quiet >= 1):
            continue
        print(
            f"{d.level}: {result.path}: [{d.code}] {d.message}",
            file=sys.stderr,
        )


def _print_json(results: Sequence[ConversionResult]) -> None:
    payload = {
        "results": [
            {
                "path": str(r.path),
                "changed": r.changed,
                "wrote_to": str(r.wrote_to) if r.wrote_to else None,
                "exit_code": r.exit_code,
                "diagnostics": [{"code": d.code, "level": d.level, "message": d.message} for d in r.diagnostics],
            }
            for r in results
        ]
    }
    print(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Version conversion
# ---------------------------------------------------------------------------


def _convert_hatch_version(
    document: Any,
    opts: ConvertOptions,
    diags: list[Diagnostic],
) -> tuple[bool, str | None]:
    """Convert ``[tool.hatch.version]`` to setuptools-compatible config.

    Returns ``(version_kept_dynamic, scm_source)``:

    * ``version_kept_dynamic`` is True if ``"version"`` remains in
      ``project.dynamic`` after conversion.
    * ``scm_source`` is ``"vcs"`` when ``setuptools-scm`` should be added to
      the build requirements, otherwise ``None``.
    """
    project = _get_table(document, "project")
    if project is None:
        return False, None
    dynamic = list(project.get("dynamic", []) or [])
    if "version" not in dynamic:
        return False, None

    tool = _get_table(document, "tool")
    hatch = tool.get("hatch") if isinstance(tool, Table) else None
    hatch_version = hatch.get("version") if isinstance(hatch, Table) else None

    if not isinstance(hatch_version, Table):
        _diag(
            diags,
            CODE_UNSUPPORTED_SOURCE,
            "warning",
            "project.dynamic contains 'version' but no [tool.hatch.version]; assuming setuptools-scm.",
        )
        return True, "vcs"

    source = hatch_version.get("source")

    if source == "vcs":
        return True, "vcs"

    if source == "path":
        path_val = hatch_version.get("path")
        if not isinstance(path_val, str) or not path_val:
            _diag(
                diags,
                CODE_UNSUPPORTED_SOURCE,
                "error",
                "[tool.hatch.version].path is required for source='path'.",
            )
            return True, None
        attr = _path_to_attr(path_val)
        if attr is None:
            _diag(
                diags,
                CODE_HATCH_VERSION_PATH,
                "warning",
                f"cannot derive import path from {path_val!r}; add [tool.setuptools.dynamic].version manually.",
            )
            return True, None
        st_tool = _get_table(document, "tool")
        setuptools = st_tool.get("setuptools") if isinstance(st_tool, Table) else None
        dyn = setuptools.get("dynamic") if isinstance(setuptools, Table) else None
        if isinstance(dyn, Table) and "version" in dyn:
            _diag(
                diags,
                CODE_HATCH_VERSION_PATH,
                "warning",
                "[tool.setuptools.dynamic].version already set; not overwriting.",
            )
            return True, None
        tool_t = _ensure_table(document, "tool", super_table=True)
        setuptools_t = _ensure_table(tool_t, "setuptools", super_table=True)
        dynamic_t = _ensure_table(setuptools_t, "dynamic")
        inline = tomlkit.inline_table()
        inline["attr"] = attr
        dynamic_t["version"] = inline
        _diag(
            diags,
            CODE_HATCH_VERSION_PATH,
            "info",
            f"mapped source='path' to [tool.setuptools.dynamic].version = {{attr = {attr!r}}}.",
        )
        return True, None

    if source == "regex":
        path_val = hatch_version.get("path")
        pattern = hatch_version.get("pattern")
        if not (isinstance(path_val, str) and path_val) or not isinstance(pattern, str):
            _diag(
                diags,
                CODE_UNSUPPORTED_SOURCE,
                "error",
                "source='regex' requires both 'path' and 'pattern'.",
            )
            return True, None
        try:
            text = Path(path_val).read_text(encoding="utf-8")
        except OSError as exc:
            _diag(
                diags,
                CODE_HATCH_VERSION_REGEX,
                "warning",
                f"cannot read {path_val!r}: {exc}",
            )
            return True, None
        match = re.search(pattern, text)
        if not match or match.lastindex is None:
            _diag(
                diags,
                CODE_HATCH_VERSION_REGEX,
                "warning",
                f"pattern {pattern!r} did not match with a capture group; "
                "add [tool.setuptools.dynamic].version manually.",
            )
            return True, None
        version = match.group(1)
        project["version"] = version
        dynamic.remove("version")
        project["dynamic"] = dynamic
        _diag(
            diags,
            CODE_HATCH_VERSION_REGEX,
            "info",
            f"resolved dynamic version to static {version!r}.",
        )
        return False, None

    if source == "code":
        _diag(
            diags,
            CODE_HATCH_VERSION_CODE,
            "error",
            "source='code' cannot be converted automatically; refactor to a static version or use setuptools-scm.",
        )
        return True, None

    _diag(
        diags,
        CODE_UNSUPPORTED_SOURCE,
        "error",
        f"[tool.hatch.version].source = {source!r} is not supported.",
    )
    return True, None


# ---------------------------------------------------------------------------
# Wheel targets
# ---------------------------------------------------------------------------


def _convert_wheel_targets(
    document: Any,
    opts: ConvertOptions,
    diags: list[Diagnostic],
) -> None:
    tool = _get_table(document, "tool")
    hatch = tool.get("hatch") if isinstance(tool, Table) else None
    if not isinstance(hatch, Table):
        return
    build = hatch.get("build")
    if not isinstance(build, Table):
        return
    targets = build.get("targets")
    if not isinstance(targets, Table):
        return
    wheel = targets.get("wheel")
    if not isinstance(wheel, Table):
        return

    packages = wheel.get("packages")
    if isinstance(packages, list) and packages:
        _convert_wheel_packages(document, [str(p) for p in packages], diags)

    sources = wheel.get("sources")
    if isinstance(sources, Table) and sources:
        _convert_wheel_sources(document, sources, diags)

    force_include = wheel.get("force-include")
    if force_include:
        _diag(
            diags,
            CODE_WHEEL_FORCE_INCLUDE,
            "warning",
            f"[tool.hatch.build.targets.wheel].force-include has no automatic setuptools mapping: {force_include!r}",
        )

    if wheel.get("artifacts"):
        _diag(
            diags,
            CODE_WHEEL_ARTIFACTS,
            "warning",
            "[tool.hatch.build.targets.wheel].artifacts has no setuptools equivalent; not migrated.",
        )

    if wheel.get("include") or wheel.get("exclude") or wheel.get("only-include"):
        _diag(
            diags,
            CODE_WHEEL_INCLUDE_EXCLUDE,
            "warning",
            "[tool.hatch.build.targets.wheel] include/exclude/only-include "
            "needs MANIFEST.in or package-data; not migrated.",
        )


def _convert_wheel_packages(document: Any, packages: Sequence[str], diags: list[Diagnostic]) -> None:
    where: list[str] = []
    include: list[str] = []
    for spec in packages:
        p = spec.strip().rstrip("/")
        if not p:
            continue
        parts = [part for part in p.split("/") if part]
        if not parts:
            continue
        if parts[0] == "src" and len(parts) > 1:
            if "src" not in where:
                where.append("src")
            include.append(parts[-1] + "*")
        else:
            include.append(parts[-1] + "*")
    if not include:
        return
    if not where:
        where = ["."]

    tool = _ensure_table(document, "tool", super_table=True)
    st = _ensure_table(tool, "setuptools", super_table=True)
    packages_t = _ensure_table(st, "packages", super_table=True)
    find = _ensure_table(packages_t, "find")
    if "where" not in find:
        find["where"] = where
    if "include" not in find:
        find["include"] = include
    _diag(
        diags,
        CODE_WHEEL_PACKAGES,
        "info",
        f"mapped wheel.packages {list(packages)!r} -> packages.find where={where} include={include}.",
    )


def _convert_wheel_sources(document: Any, sources: Table, diags: list[Diagnostic]) -> None:
    pkg_dir: dict[str, str] = {}
    for src, dst in sources.items():
        src_s = str(src)
        dst_s = str(dst)
        if not dst_s or dst_s == ".":
            _diag(
                diags,
                CODE_WHEEL_SOURCES,
                "warning",
                f"wheel.sources entry {src_s!r} = {dst_s!r} has no clean package-dir mapping; not migrated.",
            )
            continue
        pkg_dir[dst_s] = src_s
    if not pkg_dir:
        return
    tool = _ensure_table(document, "tool", super_table=True)
    st = _ensure_table(tool, "setuptools", super_table=True)
    pd = _ensure_table(st, "package-dir")
    for k, v in pkg_dir.items():
        pd[k] = v
    _diag(
        diags,
        CODE_WHEEL_SOURCES,
        "info",
        f"mapped wheel.sources -> [tool.setuptools.package-dir]: {pkg_dir}.",
    )


# ---------------------------------------------------------------------------
# Metadata / envs / hooks / sdist
# ---------------------------------------------------------------------------


def _handle_hatch_metadata(hatch: Table, diags: list[Diagnostic]) -> None:
    meta = hatch.get("metadata")
    if not isinstance(meta, Table):
        return
    for key in meta:
        if key == "allow-direct-references":
            _diag(
                diags,
                CODE_HATCH_METADATA,
                "warning",
                "[tool.hatch.metadata].allow-direct-references has no "
                "setuptools equivalent; PEP 508 direct refs will not be "
                "validated.",
            )
        else:
            _diag(
                diags,
                CODE_HATCH_METADATA,
                "warning",
                f"[tool.hatch.metadata].{key} is not migrated.",
            )


def _handle_hatch_envs(hatch: Table, opts: ConvertOptions, diags: list[Diagnostic]) -> None:
    envs = hatch.get("envs")
    if not isinstance(envs, Table) or not envs:
        return
    names = [str(k) for k in envs]
    _diag(
        diags,
        CODE_HATCH_ENVS,
        "warning",
        f"[tool.hatch.envs] contains {len(names)} env(s) "
        f"({', '.join(names)}); setuptools does not cover this — consider "
        "nox or tox (see --emit-nox).",
    )


def _handle_hatch_hooks(hatch: Table, diags: list[Diagnostic]) -> None:
    build = hatch.get("build")
    if not isinstance(build, Table):
        return
    hooks = build.get("hooks")
    if not isinstance(hooks, Table):
        return
    for name in hooks:
        if name == "vcs":
            continue
        _diag(
            diags,
            CODE_HATCH_HOOKS,
            "warning",
            f"[tool.hatch.build.hooks.{name}] cannot be reproduced by setuptools and is not migrated.",
        )


def _handle_hatch_sdist(hatch: Table, opts: ConvertOptions, diags: list[Diagnostic]) -> None:
    build = hatch.get("build")
    if not isinstance(build, Table):
        return
    targets = build.get("targets")
    if not isinstance(targets, Table):
        return
    sdist = targets.get("sdist")
    if not isinstance(sdist, Table):
        return
    if sdist.get("include") or sdist.get("exclude"):
        _diag(
            diags,
            CODE_SDIST_INCLUDE,
            "warning",
            "[tool.hatch.build.targets.sdist] include/exclude is not "
            "migrated automatically; setuptools uses MANIFEST.in "
            "(use --emit-manifest to generate one).",
        )


# ---------------------------------------------------------------------------
# [tool.setuptools_scm].version_file from Hatch's vcs build hook
# ---------------------------------------------------------------------------


def _apply_version_file(
    document: Any,
    hatch: Table,
    opts: ConvertOptions,
    diags: list[Diagnostic],
) -> None:
    build = hatch.get("build")
    if not isinstance(build, Table):
        return
    hooks = build.get("hooks")
    if not isinstance(hooks, Table):
        return
    vcs = hooks.get("vcs")
    if not isinstance(vcs, Table):
        return
    version_file = vcs.get("version-file")
    if not version_file:
        return
    version_file_str = str(version_file)

    tool = _ensure_table(document, "tool", super_table=True)
    st_scm = _ensure_table(tool, "setuptools_scm")
    existing = st_scm.get("version_file")
    if existing is not None and str(existing) != version_file_str:
        if opts.merge_policy == "abort":
            raise ConversionError(
                f"[tool.setuptools_scm].version_file is already "
                f"{str(existing)!r}, conflicting with Hatch "
                f"{version_file_str!r}. Use --merge-policy=keep or "
                "--merge-policy=overwrite."
            )
        if opts.merge_policy == "keep":
            _diag(
                diags,
                CODE_VERSION_FILE_CONFLICT,
                "warning",
                f"kept existing [tool.setuptools_scm].version_file="
                f"{str(existing)!r}; ignoring Hatch value "
                f"{version_file_str!r}.",
            )
            return
    st_scm["version_file"] = version_file_str


# ---------------------------------------------------------------------------
# Optional emissions: MANIFEST.in and noxfile.py
# ---------------------------------------------------------------------------


def _write_manifest(
    pyproject_path: Path,
    original: str,
    diags: list[Diagnostic],
) -> None:
    document = tomlkit.parse(original)
    tool = _get_table(document, "tool")
    hatch = tool.get("hatch") if isinstance(tool, Table) else None
    if not isinstance(hatch, Table):
        return
    build = hatch.get("build")
    if not isinstance(build, Table):
        return
    targets = build.get("targets")
    if not isinstance(targets, Table):
        return
    sdist = targets.get("sdist")
    if not isinstance(sdist, Table):
        return
    includes = sdist.get("include") or []
    excludes = sdist.get("exclude") or []
    if not includes and not excludes:
        return
    manifest_path = pyproject_path.parent / "MANIFEST.in"
    if manifest_path.exists():
        _diag(
            diags,
            CODE_SDIST_INCLUDE,
            "warning",
            f"{manifest_path} already exists; not overwriting.",
        )
        return
    lines: list[str] = [f"include {pat}" for pat in includes]
    lines += [f"exclude {pat}" for pat in excludes]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _diag(
        diags,
        CODE_SDIST_INCLUDE,
        "info",
        f"wrote {manifest_path} from [tool.hatch.build.targets.sdist].",
    )


def _write_nox(
    pyproject_path: Path,
    original: str,
    diags: list[Diagnostic],
) -> None:
    document = tomlkit.parse(original)
    tool = _get_table(document, "tool")
    hatch = tool.get("hatch") if isinstance(tool, Table) else None
    if not isinstance(hatch, Table):
        return
    envs = hatch.get("envs")
    if not isinstance(envs, Table) or not envs:
        return
    nox_path = pyproject_path.parent / "noxfile.py"
    if nox_path.exists():
        _diag(
            diags,
            CODE_HATCH_ENVS,
            "warning",
            f"{nox_path} already exists; not overwriting.",
        )
        return

    lines: list[str] = [
        "# Generated by pyproject-convert from [tool.hatch.envs].",
        "# Review before committing: matrices, templates, detached envs,",
        "# custom install commands and env-level overrides are not fully",
        "# reproduced.",
        "",
        "import nox",
        "",
    ]
    for name, env in envs.items():
        if not isinstance(env, Table):
            continue
        func_name = _safe_py_name(str(name))
        decorator = "@nox.session"
        if str(name) != "default":
            decorator = f"@nox.session(name={str(name)!r})"
        lines.append("")
        lines.append(decorator)
        lines.append(f"def {func_name}(session):")
        deps = env.get("dependencies") or []
        for dep in deps:
            lines.append(f"    session.install({str(dep)!r})")
        scripts = env.get("scripts")
        if isinstance(scripts, Table):
            for cmd in scripts.values():
                lines.append(f"    session.run({str(cmd)!r}, external=True)")
        if not deps and not isinstance(scripts, Table):
            lines.append("    pass")
    nox_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _diag(
        diags,
        CODE_HATCH_ENVS,
        "info",
        f"wrote {nox_path} from [tool.hatch.envs].",
    )


# ---------------------------------------------------------------------------
# Validation and build-check
# ---------------------------------------------------------------------------


def _validate_toml(text: str) -> None:
    try:
        from validate_pyproject.api import Validator
    except ImportError as exc:
        raise ConversionError("--validate requires 'validate-pyproject' (pip install validate-pyproject).") from exc
    data = tomlkit.parse(text).unwrap()
    Validator()(data)


def _build_check(source_dir: Path, converted_pyproject: str) -> None:
    try:
        import build as _build  # noqa: F401
    except ImportError as exc:
        raise ConversionError("--build-check requires the 'build' package (pip install build).") from exc

    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "project"
        shutil.copytree(
            source_dir,
            work,
            ignore=shutil.ignore_patterns(
                ".git",
                "__pycache__",
                "*.egg-info",
                ".venv",
                "build",
                "dist",
                ".tox",
                ".nox",
            ),
        )
        (work / "pyproject.toml").write_text(converted_pyproject, encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--sdist",
                "--wheel",
                "--no-isolation",
            ],
            cwd=work,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ConversionError("--build-check failed:\n" + proc.stdout + proc.stderr)


# ---------------------------------------------------------------------------
# Atomic write / backup
# ---------------------------------------------------------------------------


def _backup(target: Path) -> Path:
    backup_path = target.with_name(target.name + ".bak")
    shutil.copy2(target, backup_path)
    return backup_path


def _atomic_write(
    target: Path,
    content: str,
    *,
    preserve_mode: bool,
    preserve_mtime: bool,
) -> None:
    st = target.stat() if target.exists() else None
    mode = st.st_mode if st else None
    atime = st.st_atime if st else None
    mtime = st.st_mtime if st else None

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        delete=False,
        newline="",
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        if preserve_mode and mode is not None:
            os.chmod(tmp_path, mode)
        tmp_path.replace(target)
        if preserve_mtime and atime is not None and mtime is not None:
            os.utime(target, (atime, mtime))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------


def convert_document(
    original_text: str,
    opts: ConvertOptions,
) -> tuple[str, list[Diagnostic], bool]:
    """Convert TOML text. Returns ``(new_text, diagnostics, changed)``."""
    document = tomlkit.parse(original_text)
    diags: list[Diagnostic] = []

    build_system = _get_table(document, "build-system")
    project = _get_table(document, "project")
    if not isinstance(build_system, Table) or not isinstance(project, Table):
        raise ConversionError("expected [build-system] and [project] tables in pyproject.toml.")

    backend = build_system.get("build-backend")
    backend_str = str(backend) if backend else None

    if backend_str in SETUPTOOLS_BACKENDS:
        if not opts.force:
            _diag(
                diags,
                CODE_ALREADY_SETUPTOOLS,
                "info",
                "already using setuptools; nothing to do.",
            )
            return original_text, diags, False
        _diag(
            diags,
            CODE_ALREADY_SETUPTOOLS,
            "info",
            "already using setuptools; re-running due to --force.",
        )

    if backend_str is not None and backend_str not in HATCH_BACKENDS and backend_str not in SETUPTOOLS_BACKENDS:
        name = KNOWN_BACKENDS.get(backend_str, backend_str)
        raise ConversionError(f"build-backend is {backend_str!r} ({name}), not Hatch; refusing to convert.")

    if opts.source_backend != "hatch":
        raise ConversionError(f"--from {opts.source_backend!r} is not implemented; only 'hatch' is supported.")

    tool = _get_table(document, "tool")
    hatch = tool.get("hatch") if isinstance(tool, Table) else None

    _kept_dynamic, scm_source = _convert_hatch_version(document, opts, diags)
    _convert_wheel_targets(document, opts, diags)

    if isinstance(hatch, Table):
        _handle_hatch_metadata(hatch, diags)
        _handle_hatch_envs(hatch, opts, diags)
        _handle_hatch_hooks(hatch, diags)
        _handle_hatch_sdist(hatch, opts, diags)

    requires = [f"setuptools{opts.setuptools_version}"]
    if scm_source == "vcs":
        requires.append(f"setuptools-scm{opts.scm_version}")
    build_system["requires"] = requires
    build_system["build-backend"] = "setuptools.build_meta"
    build_system.pop("backend-path", None)

    if scm_source == "vcs" and not opts.no_version_file and isinstance(hatch, Table):
        _apply_version_file(document, hatch, opts, diags)

    if not opts.keep_hatch and isinstance(tool, Table) and "hatch" in tool:
        del tool["hatch"]

    result_text = tomlkit.dumps(document)
    return result_text, diags, result_text != original_text


# ---------------------------------------------------------------------------
# Per-file driver
# ---------------------------------------------------------------------------


def run_one(path: Path, opts: ConvertOptions) -> ConversionResult:
    is_stdin = str(path) == "-"

    if is_stdin:
        if opts.check:
            raise ConversionError("--check is not meaningful with stdin input.")
        if opts.output is not None:
            raise ConversionError("--output is not meaningful with stdin input.")
        display_path = Path("<stdin>")
        actual_path: Path | None = None
        original = sys.stdin.read()
        force_stdout = True
    else:
        actual_path = path.resolve() if path.is_symlink() else path
        display_path = path
        try:
            original = actual_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConversionError(f"cannot read {path}: {exc}") from exc
        force_stdout = False

    converted, raw_diags, changed = convert_document(original, opts)

    if opts.validate:
        try:
            _validate_toml(converted)
        except Exception as exc:  # noqa: BLE001 — surface any validator error
            raw_diags.append(Diagnostic(CODE_VALIDATION, "error", str(exc)))

    if opts.build_check and actual_path is not None:
        try:
            _build_check(actual_path.parent, converted)
        except Exception as exc:  # noqa: BLE001
            raw_diags.append(Diagnostic(CODE_BUILD_CHECK, "error", str(exc)))

    filtered, has_error = _filter_diagnostics(raw_diags, opts)

    preview_mode = opts.dry_run or opts.stdout or force_stdout
    check_mode = opts.check
    write_mode = not preview_mode and not check_mode

    if write_mode and not has_error and actual_path is not None:
        target_for_side = opts.output or actual_path
        if opts.emit_manifest:
            _write_manifest(target_for_side, original, raw_diags)
        if opts.emit_nox:
            _write_nox(target_for_side, original, raw_diags)
        filtered, has_error = _filter_diagnostics(raw_diags, opts)

    result = ConversionResult(
        path=display_path,
        original=original,
        converted=converted,
        diagnostics=filtered,
        changed=changed,
    )

    _print_diagnostics(result, opts)

    if has_error:
        result.exit_code = 2
        return result

    if preview_mode:
        if opts.diff:
            sys.stdout.write(_render_diff(original, converted, display_path))
        else:
            sys.stdout.write(_ensure_trailing_newline(converted))
        return result

    if check_mode:
        if changed:
            if opts.diff:
                sys.stdout.write(_render_diff(original, converted, display_path))
            elif not opts.quiet:
                print(f"{display_path}: needs conversion (setuptools).")
            result.exit_code = 1
        return result

    if not changed:
        if not opts.quiet and not opts.json_output:
            print(f"{display_path}: already setuptools; no changes.")
        return result

    if actual_path is None:  # defensive; should be covered by force_stdout
        result.exit_code = 2
        return result

    if opts.backup:
        backup = _backup(actual_path)
        if not opts.quiet and not opts.json_output:
            print(f"{display_path}: backed up to {backup}")

    target = opts.output or actual_path
    _atomic_write(
        target,
        _ensure_trailing_newline(converted),
        preserve_mode=True,
        preserve_mtime=opts.preserve_mtime,
    )
    result.wrote_to = target
    if not opts.quiet and not opts.json_output:
        print(f"Converted {display_path}")
    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run(paths: Sequence[Path], opts: ConvertOptions) -> int:
    exit_code = 0
    results: list[ConversionResult] = []
    for path in paths:
        try:
            result = run_one(path, opts)
        except ConversionError as exc:
            print(f"error: {path}: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 2)
            continue
        results.append(result)
        exit_code = max(exit_code, result.exit_code)
    if opts.json_output:
        _print_json(results)
    return exit_code


# ---------------------------------------------------------------------------
# Config file / path expansion / CLI
# ---------------------------------------------------------------------------


def _apply_config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.config is None:
        return
    cfg_path: Path = args.config
    if not cfg_path.exists():
        parser.error(f"--config not found: {cfg_path}")
    try:
        cfg = tomlkit.parse(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        parser.error(f"cannot parse --config {cfg_path}: {exc}")
        return

    def _apply(key: str, attr: str, cast: Any = str) -> None:
        if key in cfg and getattr(args, attr) == parser.get_default(attr):
            setattr(args, attr, cast(cfg[key]))

    _apply("setuptools-version", "setuptools_version")
    _apply("scm-version", "scm_version")
    _apply("keep-hatch", "keep_hatch", bool)
    _apply("strict", "strict", bool)
    _apply("backup", "backup", bool)
    _apply("preserve-mtime", "preserve_mtime", bool)
    _apply("merge-policy", "merge_policy")
    _apply("emit-manifest", "emit_manifest", bool)
    _apply("emit-nox", "emit_nox", bool)
    _apply("validate", "validate", bool)

    if "ignore-check" in cfg and not args.ignore_check:
        raw = cfg["ignore-check"]
        if isinstance(raw, list):
            args.ignore_check = [str(x) for x in raw]
        else:
            args.ignore_check = [str(raw)]


def _expand_paths(
    paths: Sequence[Path],
    *,
    recursive: bool,
    excludes: Sequence[str],
) -> list[Path]:
    if not paths:
        return [Path("pyproject.toml")]
    result: list[Path] = []
    for p in paths:
        if str(p) == "-":
            result.append(p)
            continue
        if recursive and p.is_dir():
            for f in sorted(p.rglob("pyproject.toml")):
                if any(f.match(glob) for glob in excludes):
                    continue
                result.append(f)
        else:
            result.append(p)
    return result


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyproject-convert",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p.add_argument(
        "paths",
        nargs="*",
        type=Path,
        metavar="PATH",
        help="pyproject.toml file(s); default: ./pyproject.toml; '-' for stdin",
    )

    # Backend selection
    p.add_argument(
        "--from",
        dest="source_backend",
        choices=["hatch"],
        default="hatch",
        help="source build backend (only 'hatch' is implemented)",
    )
    p.add_argument(
        "--to",
        dest="target_backend",
        choices=["setuptools"],
        default="setuptools",
        help="target build backend (only 'setuptools' is implemented)",
    )

    # Output modes
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the converted TOML to stdout; do not write",
    )
    p.add_argument(
        "--stdout",
        action="store_true",
        help="like --dry-run but suppress diagnostics on stderr",
    )
    p.add_argument(
        "--diff",
        action="store_true",
        help="print a unified diff instead of the full converted TOML",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if conversion is needed; never write",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write result to this file instead of in-place",
    )

    # Safety / behavior
    p.add_argument(
        "-b",
        "--backup",
        action="store_true",
        help="write a .bak copy before modifying in place",
    )
    p.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="rewrite even if the file already uses setuptools",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors",
    )
    p.add_argument(
        "--keep-hatch",
        action="store_true",
        help="keep [tool.hatch] (staged migration)",
    )
    p.add_argument(
        "--no-version-file",
        action="store_true",
        help="do not migrate Hatch's vcs version-file setting",
    )
    p.add_argument(
        "--merge-policy",
        choices=["abort", "keep", "overwrite"],
        default="abort",
        help="how to resolve [tool.setuptools_scm].version_file conflicts (default: abort)",
    )
    p.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="resolve symlinks before reading/writing",
    )
    p.add_argument(
        "--preserve-mtime",
        action="store_true",
        help="preserve mtime on the rewritten file",
    )

    # Dependencies
    p.add_argument(
        "--setuptools-version",
        default=">=61",
        metavar="SPEC",
        help="version specifier for setuptools (default: >=61)",
    )
    p.add_argument(
        "--scm-version",
        default=">=8",
        metavar="SPEC",
        help="version specifier for setuptools-scm (default: >=8)",
    )

    # Extra checks
    p.add_argument(
        "--validate",
        action="store_true",
        help="validate the converted pyproject with validate-pyproject",
    )
    p.add_argument(
        "--build-check",
        action="store_true",
        help="run 'python -m build' in a temp copy of the project",
    )

    # Diagnostics
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="emit diagnostics as JSON on stdout",
    )
    p.add_argument(
        "--ignore-check",
        action="append",
        default=[],
        metavar="CODE[,CODE...]",
        help="suppress specific diagnostic codes (repeatable, comma-separated)",
    )
    p.add_argument(
        "-q",
        "--quiet",
        action="count",
        default=0,
        help="reduce output (repeatable)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase output (repeatable)",
    )

    # Batch / config
    p.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="walk directories looking for pyproject.toml",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="glob pattern to exclude when recursing (repeatable)",
    )
    p.add_argument(
        "--config",
        type=Path,
        help="TOML file with default option values",
    )

    # Side emissions
    p.add_argument(
        "--emit-manifest",
        action="store_true",
        help="generate MANIFEST.in from [tool.hatch.build.targets.sdist]",
    )
    p.add_argument(
        "--emit-nox",
        action="store_true",
        help="generate noxfile.py from [tool.hatch.envs]",
    )

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.target_backend != "setuptools":
        parser.error(f"--to {args.target_backend!r} is not implemented; only 'setuptools' is supported.")

    try:
        _apply_config(args, parser)
    except ConversionError as exc:
        parser.error(str(exc))

    ignore_codes: set[str] = set()
    for raw in args.ignore_check or []:
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            if part not in ALL_CODES:
                parser.error(f"--ignore-check: unknown code {part!r}; valid codes: {sorted(ALL_CODES)}")
            ignore_codes.add(part)

    opts = ConvertOptions(
        dry_run=args.dry_run,
        stdout=args.stdout,
        diff=args.diff,
        check=args.check,
        output=args.output,
        backup=args.backup,
        force=args.force,
        strict=args.strict,
        keep_hatch=args.keep_hatch,
        no_version_file=args.no_version_file,
        setuptools_version=args.setuptools_version,
        scm_version=args.scm_version,
        validate=args.validate,
        build_check=args.build_check,
        json_output=args.json_output,
        ignore_checks=frozenset(ignore_codes),
        follow_symlinks=args.follow_symlinks,
        preserve_mtime=args.preserve_mtime,
        merge_policy=args.merge_policy,
        source_backend=args.source_backend,
        emit_manifest=args.emit_manifest,
        emit_nox=args.emit_nox,
        verbose=args.verbose,
        quiet=args.quiet,
    )

    paths = _expand_paths(
        args.paths,
        recursive=args.recursive,
        excludes=args.exclude,
    )

    try:
        return run(paths, opts)
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
