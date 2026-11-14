#!/data/data/com.termux/files/usr/bin/env python
"""env-audit: Reconcile .env, .env.example, CI, and Python code via static AST analysis."""

import ast
import os
import re
import sys
import argparse
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

# --- Constants & Ignored Defaults ---
DEFAULT_IGNORE: set[str] = {
    "PATH",
    "HOME",
    "PWD",
    "SHELL",
    "VIRTUAL_ENV",
    "LANG",
    "TERM",
    "TMPDIR",
    "TZ",
    "USER",
    "LOGNAME",
}

SECRET_PREFIXES: tuple[str, ...] = ("sk_live_", "ghp_", "AKIA", "-----BEGIN", "xoxb-", "ghs_")
HIGH_ENTROPY_MIN_LEN: int = 20


@dataclass
class UsageRecord:
    key: str
    source_file: str
    line: int
    is_read: bool
    is_write: bool
    default: Optional[str] = None
    is_dynamic: bool = False
    suppressions: set[str] = field(default_factory=set)


@dataclass
class Finding:
    code: str
    message: str
    severity: str  # error, warning, info
    key: Optional[str] = None
    file: Optional[str] = None
    line: Optional[int] = None


# --- Dotenv Parser ---
def parse_dotenv(content: str) -> dict[str, str]:
    """Tolerant dotenv parser handling export prefixes, quotes, multi-line, and ${VAR} interpolation."""
    env: dict[str, str] = {}
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()

        # Handle quotes
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            quote = val[0]
            val = val[1:-1]
            while not val.endswith(quote) and i < len(lines):
                next_line = lines[i]
                i += 1
                val += "\n" + next_line
            if val.endswith(quote):
                val = val[:-1]

        # Simple interpolation ${VAR}
        def replace_var(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return env.get(var_name, os.environ.get(var_name, ""))

        val = re.sub(r"\$\{([^}^{]+)\}", replace_var, val)
        env[key] = val
    return env


# --- AST Usage Visitor ---
class UsageVisitor(ast.NodeVisitor):
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.usages: list[UsageRecord] = []

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        if isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name):
            if node.value.value.id == "os" and node.value.attr == "environ":
                self._handle_env_access(node)
        elif isinstance(node.value, ast.Name) and node.value.id in ("environ", "env"):
            self._handle_env_access(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        func_name = ""
        if isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr in ("getenv", "environ")
            ):
                func_name = node.func.attr
            elif (
                node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "environ"
            ):
                func_name = "environ_get"
        elif isinstance(node.func, ast.Name) and node.func.id == "getenv":
            func_name = "getenv"

        if func_name:
            self._handle_call_access(node)
        self.generic_visit(node)

    def _handle_env_access(self, node: ast.Subscript):
        slice_node = node.slice
        key, is_dynamic = self._extract_key(slice_node)
        is_write = isinstance(node.ctx, ast.Store)
        is_read = isinstance(node.ctx, (ast.Load, ast.Del)) or not is_write

        self.usages.append(
            UsageRecord(
                key=key if key else "<dynamic>",
                source_file=self.filepath,
                line=getattr(node, "lineno", 0),
                is_read=is_read,
                is_write=is_write,
                is_dynamic=is_dynamic,
            )
        )

    def _handle_call_access(self, node: ast.Call):
        if not node.args:
            return
        key, is_dynamic = self._extract_key(node.args[0])
        default = None
        if len(node.args) > 1:
            try:
                default = str(ast.literal_eval(node.args[1]))
            except Exception:
                default = "<expression>"

        self.usages.append(
            UsageRecord(
                key=key if key else "<dynamic>",
                source_file=self.filepath,
                line=getattr(node, "lineno", 0),
                is_read=True,
                is_write=False,
                default=default,
                is_dynamic=is_dynamic,
            )
        )

    def _extract_key(self, node: ast.AST) -> tuple[Optional[str], bool]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value, False
        elif isinstance(node, ast.JoinedStr):
            return "<dynamic-fstring>", True
        return None, True


def scan_python_file(filepath: pathlib.Path) -> list[UsageRecord]:
    try:
        content = filepath.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(filepath))
        visitor = UsageVisitor(str(filepath))
        visitor.visit(tree)

        lines = content.splitlines()
        for usage in visitor.usages:
            if 0 < usage.line <= len(lines):
                line_text = lines[usage.line - 1]
                if "# env-audit: ignore" in line_text:
                    parts = line_text.split("ignore")
                    if len(parts) > 1:
                        codes = re.findall(r"E\d{3}", parts[1])
                        usage.suppressions.update(codes)
        return visitor.usages
    except Exception as e:
        print(f"Warning: Failed to parse {filepath}: {e}", file=sys.stderr)
        return []


# --- CI & Config Scanner ---
def scan_ci_files(root: pathlib.Path) -> set[str]:
    keys: set[str] = set()
    wf_dir = root / ".github" / "workflows"
    if wf_dir.exists():
        for yml in list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml")):
            keys.update(_extract_keys_from_text(yml.read_text(errors="ignore")))

    for compose_file in list(root.glob("docker-compose*.yml")) + list(root.glob("docker-compose*.yaml")):
        keys.update(_extract_keys_from_text(compose_file.read_text(errors="ignore")))

    for dockerfile in root.glob("Dockerfile*"):
        keys.update(_extract_keys_from_dockerfile(dockerfile.read_text(errors="ignore")))

    return keys


def _extract_keys_from_text(content: str) -> set[str]:
    keys: set[str] = set()
    env_lines = re.findall(r"(?:ENV|env):\s*\n((?:\s+[\w_-]+[:=].*\n)+)", content)
    for block in env_lines:
        for line in block.splitlines():
            part = line.strip().split(":")[0].split("=")[0].strip()
            if part and not part.startswith("#"):
                keys.add(part)

    matches = re.findall(r"^\s*([A-Z0-9_]+):\s*(?:\$\{\{\s*secrets\.|\$)?", content, re.MULTILINE)
    for m in matches:
        if len(m) > 1:
            keys.add(m)

    vars_found = re.findall(r"\$\{([A-Z0-9_]+)(?::-[^}]*)?\}", content)
    keys.update(vars_found)
    return keys


def _extract_keys_from_dockerfile(content: str) -> set[str]:
    keys: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("ENV ") or line.startswith("ARG "):
            parts = line.split()[1:]
            for p in parts:
                key = p.split("=")[0]
                if key:
                    keys.add(key)
    return keys


# --- Secrets Detection ---
def check_secrets(root: pathlib.Path, env_path: pathlib.Path) -> list[Finding]:
    findings: list[Finding] = []
    if not env_path.exists():
        return findings

    is_tracked = False
    try:
        res = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(env_path.relative_to(root))],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            is_tracked = True
    except Exception:
        pass

    content = env_path.read_text(errors="ignore")
    for line_no, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            v = v.strip().strip('"').strip("'")

            has_prefix = any(v.startswith(p) for p in SECRET_PREFIXES)
            is_entropy = len(v) >= HIGH_ENTROPY_MIN_LEN and not v.startswith("http") and " " not in v

            if is_tracked and (has_prefix or is_entropy):
                findings.append(
                    Finding(
                        code="E006",
                        message=f"Value for '{k.strip()}' looks like a live secret and the .env file is git-tracked!",
                        severity="error",
                        key=k.strip(),
                        file=str(env_path),
                        line=line_no,
                    )
                )
    return findings


# --- Reconciliation Engine ---
def reconcile(
    code_usages: list[UsageRecord],
    example_env: dict[str, str],
    active_env: dict[str, str],
    ci_keys: set[str],
    ignore_keys: set[str],
    severity_map: dict[str, str],
) -> list[Finding]:
    findings: list[Finding] = []

    read_keys = {u.key for u in code_usages if u.is_read and not u.is_dynamic}
    all_code_keys = {u.key for u in code_usages if not u.is_dynamic}

    # E001: read by code, absent from .env.example
    for k in read_keys:
        if k in ignore_keys:
            continue
        if k not in example_env:
            suppressed = any("E001" in u.suppressions for u in code_usages if u.key == k)
            if not suppressed:
                sev = severity_map.get("E001", "error")
                findings.append(
                    Finding(
                        code="E001",
                        message=f"Key '{k}' is read by code but absent from .env.example",
                        severity=sev,
                        key=k,
                    )
                )

    # E002: in .env.example, never read anywhere
    for k in example_env:
        if k in ignore_keys:
            continue
        if k not in all_code_keys:
            sev = severity_map.get("E002", "warning")
            findings.append(
                Finding(
                    code="E002", message=f"Key '{k}' is in .env.example but never read in code", severity=sev, key=k
                )
            )

    # E003: read with no default and missing from active .env
    for u in code_usages:
        if u.is_read and not u.is_dynamic and u.key not in ignore_keys:
            if u.default is None and u.key not in active_env and u.key not in os.environ:
                if not any("E003" in u.suppressions for u in code_usages if u.key == u.key):
                    sev = severity_map.get("E003", "error")
                    findings.append(
                        Finding(
                            code="E003",
                            message=f"Key '{u.key}' is read with no default and missing from active .env",
                            severity=sev,
                            key=u.key,
                            file=u.source_file,
                            line=u.line,
                        )
                    )

    # E004: present in .env but not in .env.example
    for k in active_env:
        if k in ignore_keys:
            continue
        if k not in example_env:
            sev = severity_map.get("E004", "info")
            findings.append(
                Finding(
                    code="E004",
                    message=f"Key '{k}' is present in .env but not in .env.example (likely local-only)",
                    severity=sev,
                    key=k,
                )
            )

    # E005: in CI but not in .env.example
    for k in ci_keys:
        if k in ignore_keys:
            continue
        if k not in example_env:
            sev = severity_map.get("E005", "warning")
            findings.append(
                Finding(
                    code="E005",
                    message=f"Key '{k}' is present in CI config but absent from .env.example",
                    severity=sev,
                    key=k,
                )
            )

    # E007: fuzzy spelling conflicts
    all_known = list(example_env.keys()) + list(active_env.keys()) + list(read_keys)
    seen: set[tuple[str, str]] = set()
    for k1 in all_known:
        for k2 in all_known:
            if k1 != k2 and k1.replace("_", "").lower() == k2.replace("_", "").lower():
                pair = tuple(sorted([k1, k2]))
                if pair not in seen:
                    seen.add(pair)
                    sev = severity_map.get("E007", "warning")
                    findings.append(
                        Finding(
                            code="E007",
                            message=f"Potential fuzzy spelling conflict between '{k1}' and '{k2}'",
                            severity=sev,
                            key=f"{k1}/{k2}",
                        )
                    )

    return findings


# --- CLI & Main Logic ---
def main():
    parser = argparse.ArgumentParser(description="env-audit: Reconcile .env, .env.example, CI, and Python code.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_check = subparsers.add_parser("check", help="Auto-discover and check environment configuration")
    p_check.add_argument("--example", default=".env.example")
    p_check.add_argument("--env", default=".env")
    p_check.add_argument("--strict", action="store_true")

    p_scan = subparsers.add_parser("scan", help="Generate usage table from source code alone")
    p_scan.add_argument("--source", default=".")
    p_scan.add_argument("--redact", action="store_true")

    p_init = subparsers.add_parser("init", help="Propose a .env.example from code + current .env")
    p_init.add_argument("--output", default=".env.example")

    args = parser.parse_args()
    root = pathlib.Path(".")

    if args.command == "scan":
        source_dir = root / args.source
        usages = []
        for py_file in source_dir.rglob("*.py"):
            if ".venv" in py_file.parts or "venv" in py_file.parts:
                continue
            usages.extend(scan_python_file(py_file))

        print(f"Scanned {source_dir}, found {len(usages)} environment variable usages:")
        for u in usages:
            val_str = f" (default: {u.default})" if u.default else ""
            print(f" - {u.key} at {u.source_file}:{u.line}{val_str}")
        return

    if args.command == "init":
        source_dir = root
        usages = []
        for py_file in source_dir.rglob("*.py"):
            if ".venv" in py_file.parts or "venv" in py_file.parts:
                continue
            usages.extend(scan_python_file(py_file))

        active_env = {}
        env_file = root / ".env"
        if env_file.exists():
            active_env = parse_dotenv(env_file.read_text())

        keys = {u.key for u in usages if not u.is_dynamic} | set(active_env.keys())
        lines = ["# Auto-generated by env-audit init\n"]
        for k in sorted(keys):
            if k in DEFAULT_IGNORE:
                continue
            default_val = active_env.get(k, "placeholder")
            lines.append(f"{k}={default_val}\n")

        out_path = root / args.output
        out_path.write_text("".join(lines))
        print(f"Generated {out_path} with {len(keys)} keys.")
        return

    if args.command == "check":
        example_path = root / args.example
        env_path = root / args.env

        example_env = parse_dotenv(example_path.read_text()) if example_path.exists() else {}
        active_env = parse_dotenv(env_path.read_text()) if env_path.exists() else {}

        usages = []
        for py_file in root.rglob("*.py"):
            if ".venv" in py_file.parts or "venv" in py_file.parts or "tests" in py_file.parts:
                continue
            usages.extend(scan_python_file(py_file))

        ci_keys = scan_ci_files(root)
        secret_findings = check_secrets(root, env_path)

        findings = reconcile(
            code_usages=usages,
            example_env=example_env,
            active_env=active_env,
            ci_keys=ci_keys,
            ignore_keys=DEFAULT_IGNORE,
            severity_map={"E002": "warning", "E007": "warning"},
        )
        findings.extend(secret_findings)

        print(f"=== env-audit check results ===")
        print(f"Files scanned: Python files, CI workflows, {args.example}, {args.env}")
        print(f"Total findings: {len(findings)}\n")

        has_errors = False
        for f in findings:
            print(f"[{f.code}] ({f.severity.upper()}) {f.message}")
            if f.severity == "error":
                has_errors = True

        if has_errors or (args.strict and findings):
            sys.exit(1)
        sys.exit(0)


if __name__ == "__main__":
    main()
