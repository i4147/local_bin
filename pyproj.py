#!/data/data/com.termux/files/usr/bin/env python

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from dotenv import load_dotenv

ENV_PATH: Final[Path] = Path.home() / ".env"
GITHUB_API: Final[str] = "https://api.github.com"
DEFAULT_BRANCH: Final[str] = "main"
SCAFFOLD_VERSION: Final[str] = "2.0.0"
DEFAULT_VERSION: Final[str] = "0.1.0"
DEFAULT_CONFIG_PATH: Final[Path] = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "scaffold" / "config.toml"
)
RESERVED_NAMES: Final[set[str]] = {
    "test",
    "tests",
    "setup",
    "src",
    "main",
    "__main__",
    "conftest",
    "pyproject",
    "build",
    "dist",
    "wheel",
    "site-packages",
}
TEXT_EXTS: Final[set[str]] = {
    ".py",
    ".pyi",
    ".pyx",
    ".pxd",
    ".toml",
    ".cfg",
    ".ini",
    ".md",
    ".rst",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".in",
    ".j2",
    ".jinja",
    ".dockerfile",
}
SKIP_DIRS: Final[set[str]] = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".eggs",
}
VALID_BACKENDS: Final[tuple[str, ...]] = ("setuptools", "hatchling", "flit", "poetry-core", "pdm-backend")
VALID_CI: Final[tuple[str, ...]] = ("github", "gitlab", "none")
VALID_DOCS: Final[tuple[str, ...]] = ("mkdocs", "sphinx", "none")
VALID_CLI: Final[tuple[str, ...]] = ("typer", "argparse", "click", "none")
VALID_TEST_FRAMEWORKS: Final[tuple[str, ...]] = ("pytest", "unittest")
VALID_LAYOUTS: Final[tuple[str, ...]] = ("package", "single", "cython")
VALID_LICENSES: Final[tuple[str, ...]] = ("MIT", "Apache-2.0", "BSD-3-Clause", "GPL-3.0-only", "proprietary")

GITIGNORE: Final[str] = """\
__pycache__/
*.py[codz]
*$py.class
*.so
build/
dist/
eggs/
.eggs/
*.egg-info/
*.egg
MANIFEST
htmlcov/
.tox/
.nox/
.coverage
.coverage.*
coverage.xml
.hypothesis/
.pytest_cache/
.env
.envrc
.venv
uv.lock
.ruff_cache/
.mypy_cache/
.dmypy.json
dmypy.json
.pyre/
.pytype/
site/
"""

SETUP_PY_SHIM: Final[str] = """\
from setuptools import setup

setup()
"""

MIT_LICENSE: Final[str] = """MIT License

Copyright (c) {year} {author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

BSD3_LICENSE: Final[str] = """BSD 3-Clause License

Copyright (c) {year} {author}
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


@dataclass
class ProjectConfig:
    name: str
    version: str = DEFAULT_VERSION
    author: str = ""
    email: str = ""
    description: str = ""
    python: str = ">=3.12"
    license: str = ""
    url: str = ""
    keywords: list[str] = field(default_factory=list)
    readme_format: str = "md"
    backend: str = "setuptools"
    layout: str = "package"
    with_cli: bool = True
    cli_framework: str = "typer"
    with_tests: bool = True
    tests_framework: str = "pytest"
    docs: str = "none"
    ci: str = "github"
    pre_commit: bool = True
    docker: bool = False
    py_typed: bool = False
    executable: bool = False
    module_vars: bool = False
    cython_annotate: bool = False
    cython_directives: dict[str, str] = field(default_factory=dict)
    cython_include_dirs: list[str] = field(default_factory=list)
    cython_pure_python: bool = False
    cython_package: bool = False
    repo_templates: bool = False
    git_message: str = "Initial commit"
    initial_tag: str = ""
    git_user_name: str = ""
    git_user_email: str = ""
    ssh_remote: bool = False
    no_scrub: bool = False
    github_public: bool = False
    github_description: str = ""
    github_topics: list[str] = field(default_factory=list)
    github_org: str = ""
    token_env: str = "GITHUB_TOKEN"
    do_git: bool = False


def run(cmd: list[str], cwd: Path, *, quiet: bool = False) -> None:
    if not quiet:
        print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def git_quiet(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def github_request(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: bytes | None = json.dumps(payload).encode() if payload is not None else None
    req: urllib.request.Request = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            body: str = resp.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail: str = e.read().decode(errors="replace")
        raise SystemExit(f"GitHub API error {e.code} on {method} {url}:\n{detail}") from e


def get_github_token(env_var: str) -> str:
    token: str | None = os.environ.get(env_var)
    if not token:
        raise SystemExit(f"{env_var} not found. Add it to {ENV_PATH} or export it, e.g.\n    {env_var}=ghp_xxx")
    return token


def get_github_user(token: str) -> str:
    data: dict[str, Any] = github_request("GET", f"{GITHUB_API}/user", token)
    return str(data["login"])


def set_github_topics(token: str, owner: str, name: str, topics: list[str]) -> None:
    if not topics:
        return
    github_request(
        "PUT",
        f"{GITHUB_API}/repos/{owner}/{name}/topics",
        token,
        {"names": topics},
    )


def create_github_repo(
    token: str,
    name: str,
    *,
    private: bool = True,
    description: str = "",
    org: str = "",
) -> dict[str, Any]:
    if org:
        url = f"{GITHUB_API}/orgs/{org}/repos"
    else:
        url = f"{GITHUB_API}/user/repos"
    return github_request(
        "POST",
        url,
        token,
        {
            "name": name,
            "private": private,
            "description": description,
            "auto_init": False,
        },
    )


def get_or_create_github_repo(
    token: str,
    owner: str,
    name: str,
    *,
    private: bool = True,
    description: str = "",
    org: str = "",
) -> dict[str, Any]:
    try:
        return github_request("GET", f"{GITHUB_API}/repos/{owner}/{name}", token)
    except SystemExit as exc:
        if "404" not in str(exc):
            raise
    print(f"Repo {owner}/{name} not found, creating ...")
    return create_github_repo(
        token,
        name,
        private=private,
        description=description,
        org=org,
    )


def parse_py_version(spec: str) -> str | None:
    m = re.match(r"(?:>=?|==|~=)?\s*(\d+)\.(\d+)", spec)
    if m:
        return f"py{m.group(1)}{m.group(2)}"
    return None


def render_pyproject(cfg: ProjectConfig) -> str:
    lines: list[str] = []
    if cfg.backend == "setuptools":
        lines += ["[build-system]", 'requires = ["setuptools"]', 'build-backend = "setuptools.build_meta"']
    elif cfg.backend == "hatchling":
        lines += ["[build-system]", 'requires = ["hatchling"]', 'build-backend = "hatchling.build"']
    elif cfg.backend == "flit":
        lines += ["[build-system]", 'requires = ["flit_core>=3.2,<4"]', 'build-backend = "flit_core.buildapi"']
    elif cfg.backend == "poetry-core":
        lines += ["[build-system]", 'requires = ["poetry-core"]', 'build-backend = "poetry.core.masonry.api"']
    elif cfg.backend == "pdm-backend":
        lines += ["[build-system]", 'requires = ["pdm-backend"]', 'build-backend = "pdm.backend"']

    lines += ["", "[project]", f'name = "{cfg.name}"', f'version = "{cfg.version}"']
    if cfg.description:
        lines.append(f'description = "{cfg.description}"')
    lines.append(f'readme = "README.{cfg.readme_format}"')
    lines.append(f'requires-python = "{cfg.python}"')
    if cfg.license:
        lines.append(f'license = {{text = "{cfg.license}"}}')
    if cfg.author or cfg.email:
        lines.append("authors = [")
        if cfg.author and cfg.email:
            lines.append(f'  {{name = "{cfg.author}", email = "{cfg.email}"}}')
        elif cfg.author:
            lines.append(f'  {{name = "{cfg.author}"}}')
        else:
            lines.append(f'  {{email = "{cfg.email}"}}')
        lines.append("]")
    if cfg.keywords:
        lines.append("keywords = [" + ", ".join(f'"{k}"' for k in cfg.keywords) + "]")

    if cfg.url:
        lines += ["", "[project.urls]", f'Homepage = "{cfg.url}"']

    if cfg.with_cli:
        entry = f"{cfg.name}.cli:main" if cfg.layout == "package" else f"{cfg.name}:main"
        lines += ["", "[project.scripts]", f'{cfg.name} = "{entry}"']

    lines += ["", "[project.optional-dependencies]", 'dev = ["ruff", "mypy", "pytest"]']

    if cfg.backend == "hatchling" and cfg.layout == "package":
        lines += ["", "[tool.hatch.build.targets.wheel]", f'packages = ["src/{cfg.name}"]']

    if cfg.backend == "flit" and cfg.layout == "package":
        lines += ["", "[tool.flit.module]", f'name = "{cfg.name}"']

    lines += ["", "[tool.ruff]", "line-length = 120"]
    target = parse_py_version(cfg.python)
    if target:
        lines.append(f'target-version = "{target}"')

    lines += [
        "",
        "[tool.mypy]",
        "strict = true",
        "warn_unused_ignores = true",
        "warn_redundant_casts = true",
        "warn_unreachable = true",
    ]
    if cfg.layout == "package":
        lines.append('files = ["src"]')
    else:
        lines.append(f'files = ["{cfg.name}.py"]')

    lines += ["", "[tool.pytest.ini_options]", 'testpaths = ["tests"]', 'addopts = "-ra -q"']
    return "\n".join(lines) + "\n"


def render_setup_py(cfg: ProjectConfig) -> str:
    if cfg.layout == "cython":
        directives = {"language_level": "3"}
        directives.update(cfg.cython_directives)
        d_str = ", ".join(f'"{k}": "{v}"' for k, v in directives.items())
        if cfg.cython_package:
            pkg = f"src/{cfg.name}"
            if cfg.cython_pure_python:
                ext_args = f'Extension("{cfg.name}", ["{pkg}/__init__.py"]'
            else:
                ext_args = f'Extension("{cfg.name}", ["{pkg}/__init__.pyx"]'
            exts = [
                ext_args
                + (
                    ", include_dirs=[" + ", ".join(f'"{d}"' for d in cfg.cython_include_dirs) + "]"
                    if cfg.cython_include_dirs
                    else ""
                )
                + ")"
            ]
            if cfg.with_cli:
                if cfg.cython_pure_python:
                    exts.append(f'Extension("{cfg.name}.cli", ["{pkg}/cli.py"])')
                else:
                    exts.append(f'Extension("{cfg.name}.cli", ["{pkg}/cli.pyx"])')
        else:
            if cfg.cython_pure_python:
                ext_args = f'Extension("{cfg.name}", ["{cfg.name}.py"]'
            else:
                ext_args = f'Extension("{cfg.name}", ["{cfg.name}.pyx"]'
            extra = (
                ", include_dirs=[" + ", ".join(f'"{d}"' for d in cfg.cython_include_dirs) + "]"
                if cfg.cython_include_dirs
                else ""
            )
            exts = [ext_args + extra + ")"]
        ext_lines = ",\n    ".join(exts)
        return (
            "from setuptools import Extension, setup\n"
            "from Cython.Build import cythonize\n\n"
            "extensions = [\n    " + ext_lines + ",\n]\n\n"
            "setup(\n"
            "    ext_modules=cythonize(\n"
            "        extensions,\n"
            f"        compiler_directives={{{d_str}}},\n"
            f"        annotate={cfg.cython_annotate},\n"
            "    ),\n"
            ")\n"
        )
    return SETUP_PY_SHIM


def render_setup_cfg(cfg: ProjectConfig) -> str:
    if cfg.layout == "package":
        return (
            "[options]\n"
            "package_dir =\n    = src\n"
            "packages = find:\n"
            f"python_requires = {cfg.python}\n\n"
            "[options.packages.find]\n"
            "where = src\n"
        )
    if cfg.layout == "single":
        return f"[options]\npy_modules = {cfg.name}\npython_requires = {cfg.python}\n"
    return f"[options]\npython_requires = {cfg.python}\n"


def render_init_py(cfg: ProjectConfig) -> str:
    return f'__version__ = "{cfg.version}"\n'


def render_cli_py_typer(cfg: ProjectConfig) -> str:
    return (
        "import typer\n"
        "from rich.console import Console\n\n"
        "app = typer.Typer()\n"
        "console = Console()\n\n\n"
        "@app.command()\n"
        "def main() -> None:\n"
        '    console.print("Hello from ' + cfg.name + '")\n\n\n'
        'if __name__ == "__main__":\n'
        "    app()\n"
    )


def render_cli_py_argparse(cfg: ProjectConfig) -> str:
    return (
        "import argparse\n\n\n"
        "def main() -> None:\n"
        '    parser = argparse.ArgumentParser(prog="' + cfg.name + '")\n'
        "    parser.parse_args()\n"
        '    print("Hello from ' + cfg.name + '")\n\n\n'
        'if __name__ == "__main__":\n'
        "    main()\n"
    )


def render_cli_py_click(cfg: ProjectConfig) -> str:
    return (
        "import click\n\n\n"
        "@click.command()\n"
        "def main() -> None:\n"
        '    click.echo("Hello from ' + cfg.name + '")\n\n\n'
        'if __name__ == "__main__":\n'
        "    main()\n"
    )


def render_cli_py(cfg: ProjectConfig) -> str:
    if cfg.cli_framework == "argparse":
        return render_cli_py_argparse(cfg)
    if cfg.cli_framework == "click":
        return render_cli_py_click(cfg)
    return render_cli_py_typer(cfg)


def render_single_file_py(cfg: ProjectConfig) -> str:
    lines: list[str] = []
    if cfg.executable:
        lines.append("#!/usr/bin/env python3")
    lines.append(f'"""{cfg.name} -- a single-file Python module."""')
    lines.append(f'__version__ = "{cfg.version}"')
    if cfg.with_cli or cfg.module_vars:
        if cfg.with_cli and cfg.cli_framework == "argparse":
            lines += ["", "", "def main() -> None:", '    print("Hello from ' + cfg.name + '")']
        elif cfg.with_cli and cfg.cli_framework == "click":
            lines += [
                "",
                "",
                "import click",
                "",
                "",
                "@click.command()",
                "def main() -> None:",
                '    click.echo("Hello from ' + cfg.name + '")',
            ]
        elif cfg.with_cli and cfg.cli_framework == "typer":
            lines += [
                "",
                "",
                "import typer",
                "",
                "",
                "app = typer.Typer()",
                "",
                "",
                "@app.command()",
                "def main() -> None:",
                '    print("Hello from ' + cfg.name + '")',
            ]
        else:
            lines += ["", "", "def main() -> None:", '    print("Hello from ' + cfg.name + '")']
        lines += ["", "", 'if __name__ == "__main__":', "    main()"]
    elif cfg.executable:
        pass
    return "\n".join(lines) + "\n"


def render_cython_pyx(cfg: ProjectConfig) -> str:
    ext = ".py" if cfg.cython_pure_python else ".pyx"
    lines: list[str] = [f"# cython: language_level=3"]
    lines.append(f'"""{cfg.name} -- a Cython extension module."""')
    lines.append(f'__version__ = "{cfg.version}"')
    if cfg.cython_package and cfg.with_cli:
        lines += ["", "", "def main() -> None:", '    print("Hello from ' + cfg.name + '")']
    else:
        lines += ["", "", "def main() -> None:", '    print("Hello from ' + cfg.name + '")']
    return "\n".join(lines) + "\n"


def render_test(cfg: ProjectConfig) -> str:
    if cfg.layout == "package" and cfg.with_cli:
        import_target = f"{cfg.name}.cli"
        has_main = True
    elif cfg.layout == "package":
        import_target = cfg.name
        has_main = False
    else:
        import_target = cfg.name
        has_main = cfg.with_cli or cfg.module_vars
    if cfg.tests_framework == "unittest":
        if has_main:
            return (
                "import unittest\n\n"
                f"from {import_target} import main\n\n\n"
                "class TestMain(unittest.TestCase):\n"
                "    def test_main(self) -> None:\n"
                "        main()\n"
            )
        return (
            "import unittest\n\n"
            f"import {import_target}\n\n\n"
            "class TestImport(unittest.TestCase):\n"
            "    def test_import(self) -> None:\n"
            f"        self.assertIsNotNone({import_target})\n"
        )
    if has_main:
        return f"from {import_target} import main\n\n\ndef test_main() -> None:\n    main()\n"
    return f"import {import_target}\n\n\ndef test_import() -> None:\n    assert {import_target} is not None\n"


def render_license(cfg: ProjectConfig) -> str:
    import datetime

    year = datetime.date.today().year
    author = cfg.author or "The Authors"
    if cfg.license == "MIT":
        return MIT_LICENSE.format(year=year, author=author)
    if cfg.license == "BSD-3-Clause":
        return BSD3_LICENSE.format(year=year, author=author)
    return f"Copyright (c) {year} {author}\n\n{cfg.license} License\n\n[Please add the license text here]\n"


def render_pre_commit(_cfg: ProjectConfig) -> str:
    return (
        "repos:\n"
        "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    rev: v0.6.9\n"
        "    hooks:\n"
        "      - id: ruff\n"
        "      - id: ruff-format\n"
        "  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: v4.6.0\n"
        "    hooks:\n"
        "      - id: trailing-whitespace\n"
        "      - id: end-of-file-fixer\n"
        "      - id: check-yaml\n"
        "      - id: check-toml\n"
        "      - id: check-added-large-files\n"
    )


def render_ci_github(cfg: ProjectConfig) -> str:
    return (
        "name: CI\n"
        "on: [push, pull_request]\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    strategy:\n"
        "      matrix:\n"
        '        python-version: ["3.11", "3.12", "3.13"]\n'
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: ${{ matrix.python-version }}\n"
        '      - run: pip install -e ".[dev]"\n'
        "      - run: ruff check .\n"
        "      - run: mypy .\n"
        "      - run: pytest\n"
    )


def render_ci_gitlab(_cfg: ProjectConfig) -> str:
    return (
        "stages:\n"
        "  - test\n\n"
        "test:\n"
        "  image: python:3.12\n"
        "  stage: test\n"
        "  script:\n"
        '    - pip install -e ".[dev]"\n'
        "    - ruff check .\n"
        "    - mypy .\n"
        "    - pytest\n"
    )


def render_dockerfile(cfg: ProjectConfig) -> str:
    return f'FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nRUN pip install --no-cache-dir -e .\nCMD ["{cfg.name}"]\n'


def render_dockerignore(_cfg: ProjectConfig) -> str:
    return ".git\n.venv\n__pycache__\n*.pyc\n.pytest_cache\n.mypy_cache\n.ruff_cache\n"


def render_mkdocs_yml(cfg: ProjectConfig) -> str:
    return f"site_name: {cfg.name}\nnav:\n  - Home: index.md\ntheme:\n  name: material\n"


def render_docs_index_md(cfg: ProjectConfig) -> str:
    return f"# {cfg.name}\n\n{cfg.description}\n"


def render_sphinx_conf(cfg: ProjectConfig) -> str:
    return (
        f'project = "{cfg.name}"\n'
        f'release = "{cfg.version}"\n'
        "extensions = []\n"
        "templates_path = []\n"
        "exclude_patterns = []\n"
        'html_theme = "alabaster"\n'
    )


def render_docs_index_rst(cfg: ProjectConfig) -> str:
    title = cfg.name.capitalize()
    return f"{title}\n=" * len(title) + f"\n\n{cfg.description}\n"


def render_readme(cfg: ProjectConfig) -> str:
    if cfg.readme_format == "rst":
        title = cfg.name.capitalize()
        return f"{title}\n{'=' * len(title)}\n\n{cfg.description}\n"
    return f"# {cfg.name}\n\n{cfg.description}\n"


def render_repo_templates(cfg: ProjectConfig) -> dict[Path, str]:
    templates: dict[Path, str] = {}
    templates[Path(".github") / "ISSUE_TEMPLATE" / "bug_report.md"] = (
        "---\nname: Bug report\nabout: Report a bug\n---\n\n"
        "**Describe the bug**\n\n**To reproduce**\n\n**Expected behavior**\n"
    )
    templates[Path(".github") / "ISSUE_TEMPLATE" / "feature_request.md"] = (
        "---\nname: Feature request\nabout: Suggest an idea\n---\n\n**Describe the feature**\n\n**Use case**\n"
    )
    templates[Path(".github") / "PULL_REQUEST_TEMPLATE.md"] = (
        "## Summary\n\n## Testing\n\n## Checklist\n- [ ] Tests added\n- [ ] Docs updated\n"
    )
    templates[Path("CONTRIBUTING.md")] = f"# Contributing to {cfg.name}\n\nPRs welcome!\n"
    templates[Path("CODE_OF_CONDUCT.md")] = (
        "# Contributor Covenant Code of Conduct\n\nBe respectful and constructive.\n"
    )
    templates[Path("SECURITY.md")] = "# Security Policy\n\nPlease report vulnerabilities privately.\n"
    return templates


def render_files(cfg: ProjectConfig) -> dict[Path, str]:
    files: dict[Path, str] = {}
    files[Path(".gitignore")] = GITIGNORE
    files[Path(f"README.{cfg.readme_format}")] = render_readme(cfg)
    files[Path("pyproject.toml")] = render_pyproject(cfg)

    if cfg.backend == "setuptools":
        files[Path("setup.py")] = render_setup_py(cfg)
        files[Path("setup.cfg")] = render_setup_cfg(cfg)

    if cfg.layout == "package":
        src = Path("src") / cfg.name
        files[src / "__init__.py"] = render_init_py(cfg)
        if cfg.with_cli:
            files[src / "cli.py"] = render_cli_py(cfg)
        if cfg.py_typed:
            files[src / "py.typed"] = ""
    elif cfg.layout == "single":
        files[Path(f"{cfg.name}.py")] = render_single_file_py(cfg)
    elif cfg.layout == "cython":
        if cfg.cython_package:
            src = Path("src") / cfg.name
            ext = ".py" if cfg.cython_pure_python else ".pyx"
            files[src / f"__init__{ext}"] = render_cython_pyx(cfg)
            if cfg.with_cli:
                files[src / f"cli{ext}"] = render_cython_pyx(cfg)
            if cfg.py_typed:
                files[src / "py.typed"] = ""
        else:
            ext = ".py" if cfg.cython_pure_python else ".pyx"
            files[Path(f"{cfg.name}{ext}")] = render_cython_pyx(cfg)

    if cfg.with_tests:
        files[Path("tests") / "__init__.py"] = ""
        files[Path("tests") / f"test_{cfg.name}.py"] = render_test(cfg)

    if cfg.license:
        files[Path("LICENSE")] = render_license(cfg)

    if cfg.pre_commit:
        files[Path(".pre-commit-config.yaml")] = render_pre_commit(cfg)

    if cfg.ci == "github":
        files[Path(".github") / "workflows" / "ci.yml"] = render_ci_github(cfg)
    elif cfg.ci == "gitlab":
        files[Path(".gitlab-ci.yml")] = render_ci_gitlab(cfg)

    if cfg.docker:
        files[Path("Dockerfile")] = render_dockerfile(cfg)
        files[Path(".dockerignore")] = render_dockerignore(cfg)

    if cfg.docs == "mkdocs":
        files[Path("mkdocs.yml")] = render_mkdocs_yml(cfg)
        files[Path("docs") / "index.md"] = render_docs_index_md(cfg)
    elif cfg.docs == "sphinx":
        files[Path("docs") / "conf.py"] = render_sphinx_conf(cfg)
        files[Path("docs") / "index.rst"] = render_docs_index_rst(cfg)

    if cfg.repo_templates:
        files.update(render_repo_templates(cfg))

    return files


def validate_name(name: str, *, strict: bool = True) -> None:
    if not name.isidentifier():
        raise SystemExit(f"Error: {name!r} is not a valid Python identifier")
    if strict and name.lower() in RESERVED_NAMES:
        raise SystemExit(f"Error: {name!r} is a reserved name; choose another")


def check_pypi_availability(name: str) -> bool:
    url = f"https://pypi.org/pypi/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            if resp.status == 200:
                return False
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True
        raise
    except urllib.error.URLError:
        return True
    return False


def check_git_clean(cwd: Path) -> None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise SystemExit("Error: git not installed")
    if out.returncode == 0 and out.stdout.strip() == "true":
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        if status.strip():
            raise SystemExit("Error: git working tree is not clean")


def scaffold_project(cfg: ProjectConfig, *, force: bool, dry_run: bool, quiet: bool) -> dict[str, Any]:
    root: Path = Path.cwd() / cfg.name
    files: dict[Path, str] = render_files(cfg)
    created: list[Path] = []
    skipped: list[Path] = []
    if dry_run:
        print(f"[dry-run] Would create project at {root}")
        for rel, _ in files.items():
            print(f"[dry-run]   + {rel}")
        return {"root": str(root), "created": [], "skipped": [], "github_url": None}
    root.mkdir(parents=True, exist_ok=True)
    for rel_path, content in files.items():
        abs_path: Path = root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        if abs_path.exists() and not force:
            skipped.append(abs_path)
            continue
        abs_path.write_text(content, encoding="utf-8")
        if cfg.executable and rel_path.suffix == ".py" and rel_path == Path(f"{cfg.name}.py"):
            mode = abs_path.stat().st_mode
            abs_path.chmod(mode | 0o111)
        created.append(abs_path)
    return {
        "root": str(root),
        "created": [str(p.relative_to(root)) for p in created],
        "skipped": [str(p.relative_to(root)) for p in skipped],
        "github_url": None,
    }


def git_init_and_push(cfg: ProjectConfig, root: Path, remote_url: str, quiet: bool) -> None:
    if cfg.git_user_name:
        run(["git", "config", "user.name", cfg.git_user_name], cwd=root, quiet=quiet)
    if cfg.git_user_email:
        run(["git", "config", "user.email", cfg.git_user_email], cwd=root, quiet=quiet)
    run(["git", "init", "-b", DEFAULT_BRANCH], cwd=root, quiet=quiet)
    run(["git", "add", "."], cwd=root, quiet=quiet)
    run(["git", "commit", "--allow-empty", "-m", cfg.git_message], cwd=root, quiet=quiet)
    existing = subprocess.run(
        ["git", "remote"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    if "origin" in existing:
        run(["git", "remote", "set-url", "origin", remote_url], cwd=root, quiet=quiet)
    else:
        run(["git", "remote", "add", "origin", remote_url], cwd=root, quiet=quiet)
    run(["git", "push", "-u", "origin", DEFAULT_BRANCH], cwd=root, quiet=quiet)
    if cfg.initial_tag:
        run(["git", "tag", "-a", cfg.initial_tag, "-m", cfg.initial_tag], cwd=root, quiet=quiet)
        run(["git", "push", "origin", cfg.initial_tag], cwd=root, quiet=quiet)


def scrub_remote_token(cfg: ProjectConfig, root: Path, owner: str, quiet: bool) -> None:
    if cfg.ssh_remote:
        clean_url: str = f"git@github.com:{owner}/{cfg.name}.git"
    else:
        clean_url = f"https://github.com/{owner}/{cfg.name}.git"
    run(["git", "remote", "set-url", "origin", clean_url], cwd=root, quiet=quiet)


def rename_tree(root: Path, old: str, new: str) -> None:
    for entry in list(root.iterdir()):
        if entry.name in SKIP_DIRS:
            continue
        target: Path | None = None
        if entry.name == old:
            target = entry.with_name(new)
        elif entry.suffix and entry.name == f"{old}{entry.suffix}":
            target = entry.with_name(f"{new}{entry.suffix}")
        if target is not None:
            if target.exists():
                raise SystemExit(f"Error: cannot rename {entry} -> {target}: target exists")
            entry.rename(target)
            entry = target
        if entry.is_dir():
            rename_tree(entry, old, new)


def replace_content(root: Path, old: str, new: str) -> list[Path]:
    pattern = re.compile(r"\b" + re.escape(old) + r"\b")
    modified: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_EXTS and path.name != "Dockerfile":
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new_content = pattern.sub(new, content)
        if new_content != content:
            path.write_text(new_content, encoding="utf-8")
            modified.append(path)
    return modified


def do_rename(args: argparse.Namespace) -> int:
    if args.pkgname:
        root = Path.cwd() / args.pkgname
        if not root.is_dir():
            print(f"Error: {root} does not exist", file=sys.stderr)
            return 1
    else:
        root = Path.cwd()
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        print(f"Error: {pyproject} not found", file=sys.stderr)
        return 1
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    old_name = data.get("project", {}).get("name")
    if not old_name:
        print(f"Error: could not determine project name from {pyproject}", file=sys.stderr)
        return 1
    new_name = args.rename
    validate_name(new_name)
    if old_name == new_name:
        print(f"Project is already named {new_name!r}; nothing to do.")
        return 0
    if args.dry_run:
        print(f"[dry-run] Would rename {old_name!r} -> {new_name!r} in {root}")
        for entry in root.rglob("*"):
            rel = entry.relative_to(root)
            if any(p == old_name for p in rel.parts):
                new_rel = Path(*[new_name if p == old_name else p for p in rel.parts])
                print(f"[dry-run]   mv {rel} -> {new_rel}")
        if root.name == old_name:
            print(f"[dry-run]   mv {root} -> {root.parent / new_name}")
        return 0
    rename_tree(root, old_name, new_name)
    modified = replace_content(root, old_name, new_name)
    if root.name == old_name:
        new_root = root.parent / new_name
        if new_root.exists():
            print(f"Error: {new_root} already exists", file=sys.stderr)
            return 1
        root.rename(new_root)
        root = new_root
    print(f"Renamed project {old_name!r} -> {new_name!r} in {root}")
    print(f"  modified {len(modified)} file(s)")
    return 0


def prompt_missing(cfg: dict[str, Any]) -> dict[str, Any]:
    prompts = [
        ("author", "Author name"),
        ("email", "Author email"),
        ("description", "Project description"),
        ("license", f"License ({'/'.join(VALID_LICENSES)})"),
        ("backend", f"Build backend ({'/'.join(VALID_BACKENDS)})"),
    ]
    for key, label in prompts:
        current = cfg.get(key) or ""
        if current:
            continue
        val = input(f"{label}: ").strip()
        if val:
            cfg[key] = val
    return cfg


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


CONFIG_KEYS: Final[dict[str, Any]] = {
    "author": "",
    "email": "",
    "description": "",
    "version": DEFAULT_VERSION,
    "python": ">=3.12",
    "license": "",
    "url": "",
    "keywords": [],
    "readme_format": "md",
    "backend": "setuptools",
    "cli_framework": "typer",
    "tests_framework": "pytest",
    "docs": "none",
    "ci": "github",
    "pre_commit": True,
    "tests": True,
    "docker": False,
    "py_typed": False,
    "executable": False,
    "module_vars": False,
    "cython_annotate": False,
    "cython_directives": [],
    "cython_include_dirs": [],
    "cython_pure_python": False,
    "cython_package": False,
    "repo_templates": False,
    "git_message": "Initial commit",
    "initial_tag": "",
    "git_user_name": "",
    "git_user_email": "",
    "ssh_remote": False,
    "no_scrub": False,
    "public": False,
    "github_description": "",
    "github_topics": [],
    "github_org": "",
    "token_env": "GITHUB_TOKEN",
    "env_file": str(ENV_PATH),
    "no_dotenv": False,
    "force": False,
    "check_pypi": False,
    "require_git_clean": False,
}


def load_config_file(path: Path, profile: str | None) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    if profile:
        return dict(data.get("profile", {}).get(profile, {}))
    return {k: v for k, v in data.items() if not isinstance(v, dict)}


def apply_config(args: argparse.Namespace, config: dict[str, Any]) -> None:
    for key, default in CONFIG_KEYS.items():
        if not hasattr(args, key) or getattr(args, key, None) is None:
            setattr(args, key, config.get(key, default))


def save_config(args: argparse.Namespace, path: Path, profile: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if profile:
        lines.append(f"[profile.{profile}]")
    for key in CONFIG_KEYS:
        value = getattr(args, key, None)
        if value is None:
            continue
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, str) and not value:
            continue
        lines.append(f"{key} = {_toml_value(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote config to {path}")


def print_tree(path: Path, prefix: str = "", is_last: bool = True, root: bool = True) -> None:
    if path.is_dir() and path.name in SKIP_DIRS:
        return
    connector = "" if root else ("└── " if is_last else "├── ")
    label = path.name + ("/" if path.is_dir() else "")
    print(f"{prefix}{connector}{label}")
    if path.is_dir():
        children = sorted(
            [c for c in path.iterdir() if c.name not in SKIP_DIRS],
            key=lambda p: (p.is_file(), p.name),
        )
        for i, child in enumerate(children):
            extension = "" if root else ("    " if is_last else "│   ")
            print_tree(child, prefix + extension, i == len(children) - 1, root=False)


def print_next_steps(cfg: ProjectConfig, root: Path, *, did_git: bool, github_url: str | None) -> None:
    print("\nNext steps:")
    print(f"  cd {root.name}")
    print("  python -m venv .venv && source .venv/bin/activate")
    print('  pip install -e ".[dev]"')
    if cfg.pre_commit:
        print("  pre-commit install")
    if cfg.with_tests:
        print("  pytest")
    if not did_git:
        print(f"  git init -b {DEFAULT_BRANCH} && git add . && git commit -m {cfg.git_message!r}")
    elif github_url:
        print(f"  open {github_url}")


def build_config_from_args(args: argparse.Namespace) -> ProjectConfig:
    layout = args.layout or ("single" if args.single_file else "cython" if args.cython else "package")
    if args.cython_package:
        layout = "cython"
    with_cli = True
    if layout == "package":
        with_cli = not args.no_cli
    elif layout == "single":
        with_cli = bool(args.cli)
    elif layout == "cython":
        with_cli = bool(args.cli) if not args.cython_package else not args.no_cli
    keywords = [k.strip() for k in (args.keywords or "").split(",") if k.strip()]
    topics = [t.strip() for t in (args.github_topics or "").split(",") if t.strip()]
    directives: dict[str, str] = {}
    if args.cython_directives:
        for pair in args.cython_directives.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                directives[k.strip()] = v.strip()
    include_dirs = [d.strip() for d in (args.cython_include_dirs or "").split(",") if d.strip()]
    return ProjectConfig(
        name=args.pkgname or "",
        version=args.version or DEFAULT_VERSION,
        author=args.author or "",
        email=args.email or "",
        description=args.description or args.github_description or "",
        python=args.python or ">=3.12",
        license=args.license or "",
        url=args.url or "",
        keywords=keywords,
        readme_format=args.readme_format or "md",
        backend=args.backend or "setuptools",
        layout=layout,
        with_cli=with_cli,
        cli_framework=args.cli_framework or "typer",
        with_tests=not args.no_tests,
        tests_framework=args.tests_framework or "pytest",
        docs=args.docs or "none",
        ci=args.ci or "github",
        pre_commit=not args.no_pre_commit,
        docker=bool(args.docker),
        py_typed=bool(args.py_typed),
        executable=bool(args.executable),
        module_vars=bool(args.module_vars),
        cython_annotate=bool(args.cython_annotate),
        cython_directives=directives,
        cython_include_dirs=include_dirs,
        cython_pure_python=bool(args.cython_pure_python),
        cython_package=bool(args.cython_package),
        repo_templates=bool(args.repo_templates),
        git_message=args.git_message or "Initial commit",
        initial_tag=args.initial_tag or "",
        git_user_name=args.git_user_name or "",
        git_user_email=args.git_user_email or "",
        ssh_remote=bool(args.ssh_remote),
        no_scrub=bool(args.no_scrub),
        github_public=bool(args.public),
        github_description=args.github_description or "",
        github_topics=topics,
        github_org=args.github_org or "",
        token_env=args.token_env or "GITHUB_TOKEN",
        do_git=bool(args.git),
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description=(
            "Scaffold a new Python project or rename an existing one. "
            "Dry-run by default; use --apply / -g for side effects."
        ),
    )
    parser.add_argument("pkgname", nargs="?", help="Package name (or old name for --rename)")
    parser.add_argument("-V", "--scaffold-version", action="version", version=f"scaffold {SCAFFOLD_VERSION}")
    parser.add_argument("--rename", metavar="NEW", help="Rename the current (or given) project to NEW")

    layout_group = parser.add_mutually_exclusive_group()
    layout_group.add_argument("-s", "--single-file", action="store_true", dest="single_file")
    layout_group.add_argument("-c", "--cython", action="store_true", dest="cython")
    layout_group.add_argument("--cython-package", action="store_true", dest="cython_package")

    cli_group = parser.add_mutually_exclusive_group()
    cli_group.add_argument("--cli", action="store_true", dest="cli")
    cli_group.add_argument("--no-cli", action="store_true", dest="no_cli")

    test_group = parser.add_mutually_exclusive_group()
    test_group.add_argument("--tests", action="store_true", dest="tests")
    test_group.add_argument("--no-tests", action="store_true", dest="no_tests")

    pc_group = parser.add_mutually_exclusive_group()
    pc_group.add_argument("--pre-commit", action="store_true", dest="pre_commit")
    pc_group.add_argument("--no-pre-commit", action="store_true", dest="no_pre_commit")

    parser.add_argument("-g", "--git", action="store_true", dest="git")
    parser.add_argument("--author", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--version", default=None)
    parser.add_argument("--python", default=None)
    parser.add_argument("--license", default=None, choices=VALID_LICENSES)
    parser.add_argument("--url", default=None)
    parser.add_argument("--keywords", default=None, help="Comma-separated")
    parser.add_argument("--readme-format", default=None, choices=("md", "rst"))
    parser.add_argument("--build-backend", default=None, choices=VALID_BACKENDS, dest="backend")
    parser.add_argument("--layout", default=None, choices=VALID_LAYOUTS)
    parser.add_argument("--cli-framework", default=None, choices=VALID_CLI)
    parser.add_argument("--tests-framework", default=None, choices=VALID_TEST_FRAMEWORKS)
    parser.add_argument("--docs", default=None, choices=VALID_DOCS)
    parser.add_argument("--ci", default=None, choices=VALID_CI)
    parser.add_argument("--docker", action="store_true", dest="docker")
    parser.add_argument("--py-typed", action="store_true", dest="py_typed")
    parser.add_argument("--executable", action="store_true", dest="executable")
    parser.add_argument("--module-vars", action="store_true", dest="module_vars")
    parser.add_argument("--cython-annotate", action="store_true", dest="cython_annotate")
    parser.add_argument("--cython-directives", default=None)
    parser.add_argument("--cython-include-dirs", default=None)
    parser.add_argument("--cython-pure-python", action="store_true", dest="cython_pure_python")
    parser.add_argument("--repo-templates", action="store_true", dest="repo_templates")
    parser.add_argument("--git-message", default=None, dest="git_message")
    parser.add_argument("--initial-tag", default=None, dest="initial_tag")
    parser.add_argument("--git-user-name", default=None, dest="git_user_name")
    parser.add_argument("--git-user-email", default=None, dest="git_user_email")
    parser.add_argument("--ssh-remote", action="store_true", dest="ssh_remote")
    parser.add_argument("--no-scrub", action="store_true", dest="no_scrub")
    parser.add_argument("--public", action="store_true", dest="public")
    parser.add_argument("--github-description", default=None, dest="github_description")
    parser.add_argument("--github-topics", default=None, dest="github_topics")
    parser.add_argument("--github-org", default=None, dest="github_org")
    parser.add_argument("--env-file", default=None, dest="env_file")
    parser.add_argument("--token-env", default=None, dest="token_env")
    parser.add_argument("--no-dotenv", action="store_true", dest="no_dotenv")
    parser.add_argument("--check-auth", action="store_true", dest="check_auth")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    parser.add_argument("--force", action="store_true", dest="force")
    parser.add_argument("--check-pypi", action="store_true", dest="check_pypi")
    parser.add_argument("--require-git-clean", action="store_true", dest="require_git_clean")
    parser.add_argument("-q", "--quiet", action="store_true", dest="quiet")
    parser.add_argument("-v", "--verbose", action="store_true", dest="verbose")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--no-color", action="store_true", dest="no_color")
    parser.add_argument("--tree", action="store_true", dest="tree")
    parser.add_argument("--next-steps", action="store_true", dest="next_steps")
    parser.add_argument("--interactive", action="store_true", dest="interactive")
    parser.add_argument("--config", default=None, dest="config")
    parser.add_argument("--save-config", action="store_true", dest="save_config")
    parser.add_argument("--profile", default=None, dest="profile")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace = parse_args(argv if argv is not None else sys.argv[1:])

    if args.no_dotenv:
        os.environ.pop("SCAFFOLD_NO_DOTENV", None)
    else:
        env_path = Path(args.env_file) if args.env_file else ENV_PATH
        load_dotenv(dotenv_path=env_path, override=False)

    config_path: Path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    config: dict[str, Any] = load_config_file(config_path, args.profile)
    apply_config(args, config)

    if args.interactive and not args.rename and not args.check_auth:
        updated = prompt_missing({
            "author": args.author,
            "email": args.email,
            "description": args.description,
            "license": args.license,
            "backend": args.backend,
        })
        args.author = updated.get("author") or args.author
        args.email = updated.get("email") or args.email
        args.description = updated.get("description") or args.description
        args.license = updated.get("license") or args.license
        args.backend = updated.get("backend") or args.backend

    if args.save_config:
        save_config(args, config_path, args.profile)

    if args.check_auth:
        token = get_github_token(args.token_env or "GITHUB_TOKEN")
        user = get_github_user(token)
        print(f"Authenticated as GitHub user: {user}")
        return 0

    if args.rename:
        if args.dry_run:
            pass
        return do_rename(args)

    if not args.pkgname:
        print("Error: pkgname is required (or use --rename)", file=sys.stderr)
        return 1

    validate_name(args.pkgname)
    if args.require_git_clean:
        check_git_clean(Path.cwd())
    if args.check_pypi:
        if not check_pypi_availability(args.pkgname):
            print(f"Warning: PyPI name {args.pkgname!r} is already taken", file=sys.stderr)

    cfg: ProjectConfig = build_config_from_args(args)

    if cfg.layout == "cython" and cfg.backend != "setuptools":
        raise SystemExit("Error: cython layouts require --build-backend setuptools")
    if cfg.layout == "single" and cfg.backend not in VALID_BACKENDS:
        raise SystemExit(f"Error: unknown backend {cfg.backend!r}")
    if cfg.initial_tag and not cfg.do_git:
        raise SystemExit("Error: --initial-tag requires -g/--git")

    if args.interactive and not cfg.do_git:
        pass

    if not args.quiet:
        print(f"Scaffolding {cfg.layout} layout for {cfg.name!r}")
    result: dict[str, Any] = scaffold_project(
        cfg,
        force=bool(args.force),
        dry_run=bool(args.dry_run),
        quiet=bool(args.quiet),
    )
    root: Path = Path(result["root"])

    if args.dry_run:
        if args.json_output:
            print(json.dumps(result, indent=2))
        return 0

    if not args.quiet:
        print(f"Project root: {root}")
        print(f"  created: {len(result['created'])} file(s)")
        for rel in result["created"]:
            print(f"    + {rel}")
        if result["skipped"]:
            print(f"  skipped (already exist): {len(result['skipped'])} file(s)")

    github_url: str | None = None
    if cfg.do_git:
        token = get_github_token(cfg.token_env)
        user = get_


if __name__ == "__main__":
    raise SystemExit(main())
