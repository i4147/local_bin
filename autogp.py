#!/data/data/com.termux/files/usr/bin/env python
import argparse
import concurrent.futures
from datetime import datetime
import json
import logging
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys

DEFAULT_CONFIG = {
    "skip_dirs": ["venv", ".venv", ".git", "__pycache__", ".mypy_cache", ".ruff_cache"],
    "dry_run": False,
    "precommit": True,
    "black_args": ["."],
    "commit_msg_prefix": "[AUTO]",
    "logging_level": "INFO",
    "max_workers": 4,
    "py_compile_timeout_seconds": 6,
}
HOME = Path.home()
CONFIG_PATH = HOME / ".auto_git_fmt_config.json"
GLOBAL_GITIGNORE = HOME / ".gitignore_global"
LOG = logging.getLogger("auto_git_fmt")
DEFAULT_GITIGNORE_CONTENT = "\n.vscode/\n.idea/\n*.swp\n*.swo\n*.sublime-project\n*.sublime-workspace\n*.pyc\n*.pyo\n__pycache__/\n*.egg\n*.egg-info/\nenv/\n.venv/\nvenv/\n.ipynb_checkpoints/\n.vscode/\n.idea/\n*.swo\n*.swp\n*.log\n"


def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as fh:
                user_cfg = json.load(fh)
            cfg.update(user_cfg)
            LOG.debug("Loaded config from %s", CONFIG_PATH)
        except Exception as e:
            LOG.warning("Failed to read %s: %s", CONFIG_PATH, e)
    return cfg


def setup_logging(level_name: str):
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")


def run_cmd(
    cmd: list[str], cwd: Path | None = None, capture: bool = False, check: bool = False
) -> subprocess.CompletedProcess:
    LOG.debug("Running: %s (cwd=%s)", " ".join(cmd), cwd)
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
            check=check,
        )
    except subprocess.CalledProcessError as e:
        LOG.debug("Command failed: %s", e)
        return e.returncode if isinstance(e, int) else subprocess.CompletedProcess(cmd, e.returncode, "", str(e))


def is_inside_git_repo() -> bool:
    try:
        res = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], capture=True)
        return res.returncode == 0 and (res.stdout or "").strip() == "true"
    except Exception:
        return False


def git_repo_root() -> Path | None:
    try:
        res = run_cmd(["git", "rev-parse", "--show-toplevel"], capture=True)
        if res.returncode == 0 and res.stdout:
            return Path(res.stdout.strip())
    except Exception:
        pass
    return None


def is_repo_clean(repo_root: Path) -> bool:
    try:
        unstaged = run_cmd(["git", "diff", "--quiet"], cwd=repo_root)
        staged = run_cmd(["git", "diff", "--cached", "--quiet"], cwd=repo_root)
        clean = unstaged.returncode == 0 and staged.returncode == 0
        LOG.debug("Repo clean=%s", clean)
        return clean
    except Exception:
        LOG.debug("Repo clean check failed.")
        return False


def staged_changes_names(repo_root: Path) -> list[str]:
    try:
        res = run_cmd(["git", "diff", "--cached", "--name-only"], cwd=repo_root, capture=True)
        if res.returncode == 0 and res.stdout:
            return [ln for ln in res.stdout.splitlines() if ln.strip()]
    except Exception:
        pass
    return []


def staged_changes_only_whitespace(repo_root: Path) -> bool:
    try:
        res1 = run_cmd(["git", "diff", "--cached", "--name-only"], cwd=repo_root, capture=True)
        res2 = run_cmd(["git", "diff", "--cached", "-w", "--name-only"], cwd=repo_root, capture=True)
        names1 = [ln for ln in (res1.stdout or "").splitlines() if ln.strip()] if res1 else []
        names2 = [ln for ln in (res2.stdout or "").splitlines() if ln.strip()] if res2 else []
        LOG.debug("staged normal=%s ignoring-ws=%s", names1, names2)
        return bool(names1) and len(names2) == 0
    except Exception:
        return False


def read_lines_safe(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return [ln.rstrip("\n") for ln in fh]
    except Exception:
        return []


def write_lines_safe(path: Path, lines: list[str]):
    try:
        with path.open("w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
    except Exception as e:
        LOG.warning("Failed to write %s: %s", path, e)


def ensure_gitignore(repo_root: Path):
    repo_gitignore = repo_root / ".gitignore"
    repo_lines = read_lines_safe(repo_gitignore) if repo_gitignore.exists() else []
    global_lines = read_lines_safe(GLOBAL_GITIGNORE) if GLOBAL_GITIGNORE.exists() else []
    default_lines = [ln for ln in DEFAULT_GITIGNORE_CONTENT.splitlines() if ln.strip()]
    if repo_gitignore.exists():
        LOG.debug(".gitignore exists; merging missing default/global lines (preserve existing).")
        merged = list(dict.fromkeys(repo_lines + global_lines + default_lines))
        write_lines_safe(repo_gitignore, merged)
        LOG.info("Merged .gitignore with global/default entries (kept existing file).")
    else:
        LOG.info("Creating .gitignore from global and defaults.")
        merged = list(dict.fromkeys(global_lines + default_lines))
        write_lines_safe(repo_gitignore, merged)
        LOG.debug("Wrote new .gitignore with %d lines", len(merged))


def iter_files(repo_root: Path, skip_dirs: set[str]) -> list[Path]:
    files = []
    for root, dirs, filenames in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in filenames:
            files.append(Path(root) / fn)
    return files


def looks_like_python_by_shebang(p: Path) -> bool:
    try:
        with p.open("rb") as fh:
            first = fh.readline(200).decode("utf-8", errors="ignore").lower()
            return first.startswith("#!") and "python" in first
    except Exception:
        return False


def check_py_compile(path_str: str) -> bool:
    try:
        res = subprocess.run(
            [sys.executable, "-m", "py_compile", path_str], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        return res.returncode == 0
    except Exception:
        return False


def detect_python_files(repo_root: Path, skip_dirs: set[str], cfg: dict) -> list[Path]:
    LOG.info("Scanning for python files...")
    all_candidates = iter_files(repo_root, skip_dirs)
    py_files: set[Path] = set()
    for p in all_candidates:
        if p.suffix == ".py":
            py_files.add(p)
    extless = [p for p in all_candidates if p.suffix == "" and p.is_file()]
    LOG.debug("Extensionless candidates: %d", len(extless))
    for p in extless:
        try:
            if looks_like_python_by_shebang(p):
                py_files.add(p)
        except Exception:
            continue
    to_check = [str(p) for p in extless if p not in py_files]
    if to_check:
        max_workers = cfg.get("max_workers") or (multiprocessing.cpu_count() or 1)
        max_workers = min(max_workers, 8)
        LOG.debug("Running py_compile checks with %d workers", max_workers)
        try:
            with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as exe:
                for path_str, ok in zip(to_check, exe.map(check_py_compile, to_check)):
                    if ok:
                        py_files.add(Path(path_str))
        except Exception as e:
            LOG.warning("Multiprocessing failed (%s); falling back to sequential", e)
            for path_str in to_check:
                if check_py_compile(path_str):
                    py_files.add(Path(path_str))
    py_list = sorted(py_files)
    LOG.info("Detected %d python files", len(py_list))
    return py_list


def run_black_on_files(repo_root: Path, files: list[Path], cfg: dict) -> bool:
    if not files:
        LOG.info("No python files to format.")
        return True
    black_args = cfg.get("black_args", ["."])
    if black_args == ["."]:
        cmd = ["black", "."]
    else:
        cmd = ["black"] + [str(p.relative_to(repo_root)) for p in files]
    LOG.info("Running black: %s", " ".join(cmd))
    if cfg.get("dry_run"):
        LOG.info("[dry-run] would run: %s", " ".join(cmd))
        return True
    try:
        res = run_cmd(cmd, cwd=repo_root, capture=True)
        if res.returncode != 0:
            LOG.error("black failed: %s", res.stderr or res.stdout)
            return False
        LOG.debug("black output: %s", res.stdout)
        return True
    except FileNotFoundError:
        LOG.error("black not installed. Install with: pip install black")
        return False
    except Exception as e:
        LOG.exception("Running black failed: %s", e)
        return False


def git_add_all(repo_root: Path, cfg: dict) -> bool:
    if cfg.get("dry_run"):
        LOG.info("[dry-run] git add .")
        return True
    try:
        res = run_cmd(["git", "add", "."], cwd=repo_root, capture=True)
        if res.returncode != 0:
            LOG.error("git add failed: %s", res.stderr or res.stdout)
            return False
        return True
    except Exception as e:
        LOG.exception("git add failed: %s", e)
        return False


def git_commit_and_push(repo_root: Path, cfg: dict) -> bool:
    if cfg.get("dry_run"):
        LOG.info("[dry-run] would commit and push.")
        return True
    staged = staged_changes_names(repo_root)
    if not staged:
        LOG.info("Nothing staged to commit.")
        return False
    if staged_changes_only_whitespace(repo_root):
        LOG.info("Staged changes appear to be only whitespace. Skipping commit.")
        return False
    prefix = cfg.get("commit_msg_prefix", "").strip()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"{prefix} {ts}" if prefix else ts
    LOG.info("Committing with message: %s", message)
    try:
        res = run_cmd(["git", "commit", "-m", message], cwd=repo_root, capture=True)
        if res.returncode != 0:
            LOG.error("git commit failed: %s", res.stderr or res.stdout)
            return False
    except Exception as e:
        LOG.exception("git commit failed: %s", e)
        return False
    try:
        res = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, capture=True)
        branch = (res.stdout or "").strip() if res.returncode == 0 else None
    except Exception:
        branch = None
    if not branch:
        push_cmd = ["git", "push"]
    else:
        try:
            resu = run_cmd(
                ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=repo_root, capture=True
            )
            if resu.returncode == 0:
                push_cmd = ["git", "push"]
            else:
                push_cmd = ["git", "push", "-u", "origin", branch]
        except Exception:
            push_cmd = ["git", "push", "-u", "origin", branch]
    LOG.info("Pushing changes: %s", " ".join(push_cmd))
    try:
        res = run_cmd(push_cmd, cwd=repo_root, capture=True)
        if res.returncode != 0:
            LOG.error("git push failed: %s", res.stderr or res.stdout)
            return False
    except Exception as e:
        LOG.exception("git push failed: %s", e)
        return False
    LOG.info("Commit & push succeeded.")
    return True


PRECOMMIT_YAML = "repos:\n  - repo: https://github.com/psf/black\n    rev: stable\n    hooks:\n      - id: black\n"


def ensure_precommit(repo_root: Path, cfg: dict):
    if not cfg.get("precommit", True):
        LOG.debug("Pre-commit disabled in config.")
        return
    yml = repo_root / ".pre-commit-config.yaml"
    if not yml.exists():
        try:
            yml.write_text(PRECOMMIT_YAML, encoding="utf-8")
            LOG.info("Wrote minimal .pre-commit-config.yaml")
        except Exception as e:
            LOG.warning("Could not write pre-commit config: %s", e)
            return
    try:
        res = run_cmd(["pre-commit", "install"], cwd=repo_root, capture=True)
        if res.returncode == 0:
            LOG.info("pre-commit installed.")
        else:
            LOG.warning("pre-commit install returned non-zero: %s", res.stderr or res.stdout)
    except FileNotFoundError:
        LOG.warning("pre-commit not installed (pip install pre-commit to enable).")
    except Exception as e:
        LOG.debug("pre-commit install exception: %s", e)


def main(argv=None) -> int:
    cfg = load_config()
    parser = argparse.ArgumentParser(
        prog="auto-git-fmt", description="Auto format Python files and auto commit/push in a git repo."
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes.")
    parser.add_argument("--no-precommit", action="store_true", help="Do not set up pre-commit integration.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging (DEBUG).")
    args = parser.parse_args(argv)
    if args.verbose:
        cfg["logging_level"] = "DEBUG"
    if args.dry_run:
        cfg["dry_run"] = True
    if args.no_precommit:
        cfg["precommit"] = False
    setup_logging(cfg.get("logging_level", "INFO"))
    LOG.debug("Config: %s", cfg)
    if not is_inside_git_repo():
        LOG.info("Not inside a git repository. Doing nothing.")
        return 0
    repo_root = git_repo_root()
    if not repo_root:
        LOG.info("Could not determine git repo root. Doing nothing.")
        return 0
    LOG.info("Repo root: %s", repo_root)
    ensure_gitignore(repo_root)
    ensure_precommit(repo_root, cfg)
    skip_dirs = set(cfg.get("skip_dirs", []))
    if is_repo_clean(repo_root):
        LOG.info("Repository clean (no staged or unstaged changes). Nothing to do.")
        return 0
    py_files = detect_python_files(repo_root, skip_dirs, cfg)
    if not py_files:
        LOG.info("No python files detected. Exiting.")
        return 0
    if not run_black_on_files(repo_root, py_files, cfg):
        LOG.error("Black formatting failed. Aborting.")
        return 1
    if not git_add_all(repo_root, cfg):
        LOG.error("git add failed. Aborting.")
        return 1
    git_commit_and_push(repo_root, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
