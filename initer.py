#!/data/data/com.termux/files/usr/bin/env python
"""
Multi-template script / code initializer.

Creates a new file from a built-in template. The template is picked
automatically from the target filename's extension or exact name
(e.g. ``Makefile``), or explicitly via a flag or ``--template NAME``.

Examples
--------
    initer.py app.py                  # Python argparse CLI  (auto)
    initer.py app.py -t               # Typer + Loguru + Rich
    initer.py lib.rs                  # Rust single-file
    initer.py index.html              # HTML5 skeleton
    initer.py style.css               # modern CSS
    initer.py Makefile                # Makefile skeleton
    initer.py foo -r                  # Rust, appends .rs
    initer.py --template rust foo     # same
    initer.py --template-file ./x.tmpl out.txt
    initer.py -l                      # list all templates
    initer.py -l --as-json            # machine-readable
"""

from __future__ import annotations
import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date as date_cls
import difflib
import json as json_mod
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any

from dh import atomic_write
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level=os.environ.get("LOGURU_LEVEL", "INFO"),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)


VERSION: str = "1.0.0"


def esc_generic(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def esc_shell(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def esc_json(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def esc_sql(s: str) -> str:
    return s.replace("'", "''")


def esc_tex(s: str) -> str:
    specials = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(specials.get(c, c) for c in s)


def esc_none(s: str) -> str:
    return s


PYTHON_GENERIC = r'''#!/data/data/com.termux/files/usr/bin/env python
"""
@@DESCRIPTION@@

Created on: @@DATE@@
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    logging.info("Script started successfully.")
    # TODO: add your logic here
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

PYTHON_TYPER = r'''#!/data/data/com.termux/files/usr/bin/env python
"""
@@DESCRIPTION@@

Created on: @@DATE@@
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from itertools import islice
import os
from pathlib import Path
from typing import Annotated, TypeVar

from loguru import logger
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
import typer

T = TypeVar("T")
console = Console(stderr=True)

app = typer.Typer(
    help="@@DESCRIPTION@@",
    add_completion=False,
    no_args_is_help=True,
)


def setup_logging(verbose: bool = False) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(
        lambda msg: console.print(msg, end=""),
        level=level,
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    )


def walk_files(root: Path, pattern: str = "*", recursive: bool = True) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    walker = root.rglob(pattern) if recursive else root.glob(pattern)
    for path in walker:
        if path.is_file():
            yield path


def parallel_runner(
    func: Callable[[Path], T],
    files: Iterable[Path],
    max_workers: int | None = None,
    use_threads: bool = False,
    max_in_flight: int | None = None,
) -> Iterator[T]:
    workers = max_workers or os.cpu_count() or 4
    buffer_limit = max(1, max_in_flight if max_in_flight is not None else workers * 2)
    executor_cls = ThreadPoolExecutor if use_threads else ProcessPoolExecutor
    file_iter = iter(files)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    )

    executor = executor_cls(max_workers=workers)
    in_flight: dict[Future[T], Path] = {}

    try:
        with progress:
            task_id = progress.add_task("Processing files", total=None)
            submitted = 0

            for file_path in islice(file_iter, buffer_limit):
                in_flight[executor.submit(func, file_path)] = file_path
                submitted += 1

            while in_flight:
                done, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    file_path = in_flight.pop(future)
                    progress.advance(task_id)
                    next_file = next(file_iter, None)
                    if next_file is not None:
                        in_flight[executor.submit(func, next_file)] = next_file
                        submitted += 1
                    try:
                        yield future.result()
                    except Exception as exc:
                        logger.exception(f"Failed processing {file_path}: {exc}")
                if len(in_flight) < buffer_limit:
                    progress.update(task_id, total=submitted)

    except KeyboardInterrupt:
        logger.warning("Interrupt received (Ctrl+C). Cancelling pending tasks...")
        for future in in_flight:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise typer.Exit(code=130)
    finally:
        executor.shutdown(wait=True)


def process_file(file_path: Path) -> Path:
    logger.debug(f"Processing {file_path}")
    return file_path


@app.command()
def run(
    target: Annotated[Path, typer.Argument(exists=True, help="Target file or dir.")],
    pattern: Annotated[str, typer.Option("--pattern", "-p")] = "*",
    workers: Annotated[int | None, typer.Option("--workers", "-w", min=1)] = None,
    in_flight: Annotated[int | None, typer.Option("--in-flight", "-b", min=1)] = None,
    threads: Annotated[bool, typer.Option("--threads")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Walk target files and process them in parallel."""
    setup_logging(verbose)
    logger.info(f"Scanning {target} with pattern '{pattern}'")
    results = parallel_runner(
        process_file,
        walk_files(target, pattern=pattern),
        max_workers=workers,
        use_threads=threads,
        max_in_flight=in_flight,
    )
    n = 0
    for _ in results:
        n += 1
    logger.success(f"Completed processing {n} file(s).")


if __name__ == "__main__":
    app()
'''

SHELL_SH = r"""#!/data/data/com.termux/files/usr/bin/bash
# @@DESCRIPTION@@
#
# Created on: @@DATE@@
set -eu

main() {
    :
    # TODO: add logic
}

main "$@"
"""

SHELL_ZSH = r"""#!/data/data/com.termux/files/usr/bin/zsh

# @@DESCRIPTION@@
#
# Created on: @@DATE@@
emulate -L zsh
setopt errexit nounset pipefail

main() {
    :
    # TODO: add logic
}

main "$@"
"""

RUST = r"""#!/data/data/com.termux/files/usr/bin/env rust-script

// @@DESCRIPTION@@
//
// Created on: @@DATE@@

use std::env;
use std::process::ExitCode;

fn print_usage(prog: &str) {
    eprintln!("Usage: {} [options]", prog);
    eprintln!("  -h, --help   Show this help");
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    let prog = args.first().map(String::as_str).unwrap_or("prog");
    for arg in &args[1..] {
        match arg.as_str() {
            "-h" | "--help" => {
                print_usage(prog);
                return ExitCode::SUCCESS;
            }
            other => {
                eprintln!("Unknown argument: {other}");
                print_usage(prog);
                return ExitCode::FAILURE;
            }
        }
    }
    println!("@@DESCRIPTION@@");
    // TODO: add logic
    ExitCode::SUCCESS
}
"""

JS_MJS = r"""// @@DESCRIPTION@@
//
// Created on: @@DATE@@

import process from "node:process";

function main(argv) {
    for (const arg of argv) {
        if (arg === "-h" || arg === "--help") {
            console.log("Usage: node script.mjs [options]");
            return 0;
        }
        console.error(`Unknown argument: ${arg}`);
        return 1;
    }
    console.log("@@DESCRIPTION@@");
    // TODO: add logic
    return 0;
}

process.exit(main(process.argv.slice(2)));
"""

JS_CJS = r"""// @@DESCRIPTION@@
//
// Created on: @@DATE@@
"use strict";

const process = require("node:process");

function main(argv) {
    for (const arg of argv) {
        if (arg === "-h" || arg === "--help") {
            console.log("Usage: node script.cjs [options]");
            return 0;
        }
        console.error(`Unknown argument: ${arg}`);
        return 1;
    }
    console.log("@@DESCRIPTION@@");
    // TODO: add logic
    return 0;
}

process.exit(main(process.argv.slice(2)));
"""

TS = r"""// @@DESCRIPTION@@
//
// Created on: @@DATE@@

import process from "node:process";

function main(argv: string[]): number {
    for (const arg of argv) {
        if (arg === "-h" || arg === "--help") {
            console.log("Usage: tsx script.ts [options]");
            return 0;
        }
        console.error(`Unknown argument: ${arg}`);
        return 1;
    }
    console.log("@@DESCRIPTION@@");
    // TODO: add logic
    return 0;
}

process.exit(main(process.argv.slice(2)));
"""

HTML = r"""<!DOCTYPE html>
<html lang="@@LANG@@">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>@@TITLE@@</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <main>
        <h1>@@TITLE@@</h1>
        <p>@@DESCRIPTION@@</p>
    </main>
    <script src="app.js" type="module"></script>
</body>
</html>
"""

CSS = r"""/* @@DESCRIPTION@@ */
/* Created on: @@DATE@@ */

@layer reset, base, components, utilities;

@layer base {
    :root {
        color-scheme: light dark;
        --fg: #111;
        --bg: #fff;
    }

    @media (prefers-color-scheme: dark) {
        :root { --fg: #eee; --bg: #111; }
    }

    *, *::before, *::after { box-sizing: border-box; }

    body {
        margin: 0;
        font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
        line-height: 1.5;
        color: var(--fg);
        background: var(--bg);
    }
}
"""

C_LANG = r"""/* @@DESCRIPTION@@ */
/* Created on: @@DATE@@ */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void print_usage(const char *prog) {
    fprintf(stderr, "Usage: %s [options]\n", prog);
    fprintf(stderr, "  -h, --help   Show this help\n");
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help")) {
            print_usage(argv[0]);
            return 0;
        }
        fprintf(stderr, "Unknown argument: %s\n", argv[i]);
        print_usage(argv[0]);
        return 1;
    }
    puts("@@DESCRIPTION@@");
    // TODO: add logic
    return 0;
}
"""

CPP = r"""/**
 * @@DESCRIPTION@@
 *
 * Created on: @@DATE@@
 */

#include <iostream>
#include <string_view>

namespace {

void print_usage(std::string_view prog) {
    std::cerr << "Usage: " << prog << " [options]\n"
              << "  -h, --help   Show this help\n";
}

}  // namespace

int main(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        std::string_view arg{argv[i]};
        if (arg == "-h" || arg == "--help") {
            print_usage(argv[0]);
            return 0;
        }
        std::cerr << "Unknown argument: " << arg << "\n";
        print_usage(argv[0]);
        return 1;
    }
    std::cout << "@@DESCRIPTION@@\n";
    // TODO: add logic
    return 0;
}
"""

GO = r"""// @@DESCRIPTION@@
//
// Created on: @@DATE@@

package main

import (
	"fmt"
	"os"
)

func usage(prog string) {
	fmt.Fprintf(os.Stderr, "Usage: %s [options]\n", prog)
	fmt.Fprintln(os.Stderr, "  -h, --help   Show this help")
}

func main() {
	for _, arg := range os.Args[1:] {
		if arg == "-h" || arg == "--help" {
			usage(os.Args[0])
			return
		}
		fmt.Fprintf(os.Stderr, "Unknown argument: %s\n", arg)
		usage(os.Args[0])
		os.Exit(1)
	}
	fmt.Println("@@DESCRIPTION@@")
}
"""

KOTLIN = r"""// @@DESCRIPTION@@
//
// Created on: @@DATE@@

import kotlin.system.exitProcess

fun usage() {
    System.err.println("Usage: app [options]")
    System.err.println("  -h, --help   Show this help")
}

fun main(args: Array<String>) {
    for (arg in args) {
        when (arg) {
            "-h", "--help" -> { usage(); return }
            else -> {
                System.err.println("Unknown argument: $arg")
                usage()
                exitProcess(1)
            }
        }
    }
    println("@@DESCRIPTION@@")
}
"""

LUA = r"""#!/usr/bin/env lua
-- @@DESCRIPTION@@
-- Created on: @@DATE@@

local function usage()
    io.stderr:write("Usage: lua script.lua [options]\n")
    io.stderr:write("  -h, --help   Show this help\n")
end

for _, a in ipairs(arg) do
    if a == "-h" or a == "--help" then
        usage()
        os.exit(0)
    else
        io.stderr:write("Unknown argument: " .. a .. "\n")
        usage()
        os.exit(1)
    end
end

print("@@DESCRIPTION@@")
"""

RUBY = r"""#!/usr/bin/env ruby
# frozen_string_literal: true

# @@DESCRIPTION@@
# Created on: @@DATE@@

require "optparse"

OptionParser.new do |opts|
  opts.banner = "Usage: #{File.basename($PROGRAM_NAME)} [options]"
  opts.on("-h", "--help", "Show this help") do
    puts opts
    exit
  end
end.parse!

puts "@@DESCRIPTION@@"
"""

JAVA = r"""// @@DESCRIPTION@@
//
// Created on: @@DATE@@

public class @@CLASSNAME@@ {
    public static void main(String[] args) {
        for (String arg : args) {
            switch (arg) {
                case "-h", "--help" -> { printUsage(); return; }
                default -> {
                    System.err.println("Unknown argument: " + arg);
                    printUsage();
                    System.exit(1);
                }
            }
        }
        System.out.println("@@DESCRIPTION@@");
        // TODO: add logic
    }

    private static void printUsage() {
        System.err.println("Usage: java @@CLASSNAME@@ [options]");
        System.err.println("  -h, --help   Show this help");
    }
}
"""

SQL = r"""-- @@DESCRIPTION@@
-- Created on: @@DATE@@

SELECT 1 AS ok;
"""

TEX = r"""% @@DESCRIPTION@@
% Created on: @@DATE@@
\documentclass{article}
\begin{document}
Hello, world!
\end{document}
"""

MARKDOWN = r"""# @@TITLE@@

@@DESCRIPTION@@

_Created on: @@DATE@@_
"""

NIX = r"""# @@DESCRIPTION@@
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = [ ];
}
"""

JSON_TPL = r"""{
  "name": "@@STEM@@",
  "description": "@@DESCRIPTION@@",
  "version": "0.1.0"
}
"""

YAML = r"""# @@DESCRIPTION@@
name: @@STEM@@
version: 0.1.0
"""

TOML = r"""# @@DESCRIPTION@@
[package]
name = "@@STEM@@"
version = "0.1.0"
"""

INI = r"""; @@DESCRIPTION@@
[main]
key = value
"""

MAKEFILE = r"""# @@DESCRIPTION@@
.PHONY: all clean

all:
	@echo "Hello from Makefile"

clean:
	@rm -f *.o
"""

DOCKERFILE = r"""# @@DESCRIPTION@@
FROM alpine:latest
CMD ["sh", "-c", "echo hello"]
"""

GITIGNORE = r"""# @@DESCRIPTION@@
*.pyc
__pycache__/
.venv/
node_modules/
target/
*.o
*.so
*.tmp
.DS_Store
"""

EDITORCONFIG = r"""# @@DESCRIPTION@@
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
indent_style = space
indent_size = 4
trim_trailing_whitespace = true

[*.{js,ts,html,css,json,yaml,yml}]
indent_size = 2
"""

LICENSE_TPL = r"""@@LICENSE_TEXT@@
"""

LICENSE_TEXTS: dict[str, str] = {
    "MIT": (
        "MIT License\n\n"
        "Copyright (c) @@YEAR@@ @@AUTHOR@@\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
        'of this software and associated documentation files (the "Software"), to deal\n'
        "in the Software without restriction, including without limitation the rights\n"
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
        "copies of the Software, and to permit persons to whom the Software is\n"
        "furnished to do so, subject to the following conditions:\n\n"
        "The above copyright notice and this permission notice shall be included in all\n"
        "copies or substantial portions of the Software.\n\n"
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
        "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
        "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
        "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
        "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
        "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
        "SOFTWARE.\n"
    ),
    "ISC": (
        "ISC License\n\n"
        "Copyright (c) @@YEAR@@ @@AUTHOR@@\n\n"
        "Permission to use, copy, modify, and/or distribute this software for any\n"
        "purpose with or without fee is hereby granted, provided that the above\n"
        "copyright notice and this permission notice appear in all copies.\n\n"
        'THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES\n'
        "WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF\n"
        "MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR\n"
        "ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES\n"
        "WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN\n"
        "ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF\n"
        "OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.\n"
    ),
    "Unlicense": (
        "This is free and unencumbered software released into the public domain.\n\n"
        "Anyone is free to copy, modify, publish, use, compile, sell, or distribute\n"
        "this software, either in source code form or as a compiled binary, for any\n"
        "purpose, commercial or non-commercial, and by any means.\n\n"
        "For more information, please refer to <https://unlicense.org>\n"
    ),
}
DEFAULT_LICENSE = "MIT"


# ===========================================================================
# Spec & registry
# ===========================================================================
@dataclass(frozen=True)
class TemplateSpec:
    name: str
    label: str
    ext: str
    body: str
    default_desc: str
    flag: tuple[str, ...] = ()
    escape: Callable[[str], str] = esc_generic
    exact_names: tuple[str, ...] = ()
    executable: bool = False
    comment: str = "#"
    check: tuple[str, ...] = ()
    formatter: tuple[str, ...] = ()


TEMPLATES: dict[str, TemplateSpec] = {
    "python": TemplateSpec(
        name="python",
        label="Python CLI (argparse)  [default for .py]",
        ext=".py",
        body=PYTHON_GENERIC,
        default_desc="A brief description of what this script does.",
        flag=("-g", "--generic"),
        executable=True,
        comment="#",
        check=("python3", "-m", "py_compile", "__PATH__"),
        formatter=("black",),
    ),
    "typer": TemplateSpec(
        name="typer",
        label="Typer + Loguru + Rich parallel CLI",
        ext=".py",
        body=PYTHON_TYPER,
        default_desc="A parallel file-processing CLI built with Typer, Loguru, and Rich.",
        flag=("-t", "--typer"),
        executable=True,
        comment="#",
        check=("python3", "-m", "py_compile", "__PATH__"),
        formatter=("black",),
    ),
    "sh": TemplateSpec(
        name="sh",
        label="POSIX shell script",
        ext=".sh",
        body=SHELL_SH,
        default_desc="A POSIX shell script.",
        flag=("-s", "--sh"),
        escape=esc_shell,
        executable=True,
        comment="#",
        check=("sh", "-n", "__PATH__"),
        formatter=("shfmt", "-w"),
    ),
    "zsh": TemplateSpec(
        name="zsh",
        label="Zsh script",
        ext=".zsh",
        body=SHELL_ZSH,
        default_desc="A zsh script.",
        flag=("-z", "--zsh"),
        escape=esc_shell,
        executable=True,
        comment="#",
        check=("zsh", "-n", "__PATH__"),
        formatter=("shfmt", "-w", "-ln", "zsh"),
    ),
    "rust": TemplateSpec(
        name="rust",
        label="Rust single-file (rustc)",
        ext=".rs",
        body=RUST,
        default_desc="A Rust single-file utility.",
        flag=("-r", "--rust"),
        comment="//",
        check=(
            "rustc",
            "--edition=2021",
            "--emit=metadata",
            "--crate-type=lib",
            "__PATH__",
        ),
        formatter=("rustfmt",),
    ),
    "js": TemplateSpec(
        name="js",
        label="Node.js ESM (.mjs)",
        ext=".mjs",
        body=JS_MJS,
        default_desc="A Node.js ESM script.",
        flag=("-j", "--js"),
        comment="//",
        check=("node", "--check", "__PATH__"),
        formatter=("prettier", "--write"),
    ),
    "cjs": TemplateSpec(
        name="cjs",
        label="Node.js CommonJS (.cjs)",
        ext=".cjs",
        body=JS_CJS,
        default_desc="A Node.js CommonJS script.",
        flag=("--cjs",),
        comment="//",
        check=("node", "--check", "__PATH__"),
        formatter=("prettier", "--write"),
    ),
    "ts": TemplateSpec(
        name="ts",
        label="TypeScript",
        ext=".ts",
        body=TS,
        default_desc="A TypeScript script.",
        flag=("-T", "--ts"),
        comment="//",
        formatter=("prettier", "--write"),
    ),
    "html": TemplateSpec(
        name="html",
        label="HTML5 page",
        ext=".html",
        body=HTML,
        default_desc="A minimal HTML5 page.",
        flag=("-w", "--html"),
        escape=esc_html,
        comment="<!--",
        formatter=("prettier", "--write"),
    ),
    "css": TemplateSpec(
        name="css",
        label="Modern CSS",
        ext=".css",
        body=CSS,
        default_desc="A modern CSS stylesheet.",
        flag=("-y", "--css"),
        comment="/*",
        formatter=("prettier", "--write"),
    ),
    "c": TemplateSpec(
        name="c",
        label="C program",
        ext=".c",
        body=C_LANG,
        default_desc="A C program.",
        flag=("-C", "--c-lang"),
        comment="/*",
        check=("gcc", "-fsyntax-only", "__PATH__"),
        formatter=("clang-format", "-i"),
    ),
    "cpp": TemplateSpec(
        name="cpp",
        label="C++17 program",
        ext=".cpp",
        body=CPP,
        default_desc="A modern C++ command-line utility.",
        flag=("-c", "--cpp"),
        comment="//",
        check=("g++", "-std=c++17", "-fsyntax-only", "__PATH__"),
        formatter=("clang-format", "-i"),
    ),
    "go": TemplateSpec(
        name="go",
        label="Go program",
        ext=".go",
        body=GO,
        default_desc="A Go command-line utility.",
        flag=("-G", "--go"),
        comment="//",
        check=("gofmt", "-e", "__PATH__"),
        formatter=("gofmt", "-w"),
    ),
    "kotlin": TemplateSpec(
        name="kotlin",
        label="Kotlin program",
        ext=".kt",
        body=KOTLIN,
        default_desc="A Kotlin program.",
        flag=("-k", "--kotlin"),
        comment="//",
    ),
    "lua": TemplateSpec(
        name="lua",
        label="Lua script",
        ext=".lua",
        body=LUA,
        default_desc="A Lua script.",
        flag=("-L", "--lua"),
        executable=True,
        comment="--",
        check=("luac", "-p", "__PATH__"),
    ),
    "ruby": TemplateSpec(
        name="ruby",
        label="Ruby script",
        ext=".rb",
        body=RUBY,
        default_desc="A Ruby script.",
        flag=("-b", "--ruby"),
        executable=True,
        comment="#",
        check=("ruby", "-c", "__PATH__"),
    ),
    "java": TemplateSpec(
        name="java",
        label="Java program",
        ext=".java",
        body=JAVA,
        default_desc="A Java program.",
        flag=("-J", "--java"),
        comment="//",
    ),
    "sql": TemplateSpec(
        name="sql",
        label="SQL script",
        ext=".sql",
        body=SQL,
        default_desc="A SQL script.",
        flag=("--sql",),
        escape=esc_sql,
        comment="--",
    ),
    "tex": TemplateSpec(
        name="tex",
        label="LaTeX document",
        ext=".tex",
        body=TEX,
        default_desc="A LaTeX document.",
        flag=("-x", "--tex"),
        escape=esc_tex,
        comment="%",
    ),
    "markdown": TemplateSpec(
        name="markdown",
        label="Markdown document",
        ext=".md",
        body=MARKDOWN,
        default_desc="A Markdown document.",
        flag=("-m", "--md"),
        comment="<!--",
    ),
    "nix": TemplateSpec(
        name="nix",
        label="Nix shell expression",
        ext=".nix",
        body=NIX,
        default_desc="A Nix shell expression.",
        flag=("--nix",),
        comment="#",
    ),
    "json": TemplateSpec(
        name="json",
        label="JSON document",
        ext=".json",
        body=JSON_TPL,
        default_desc="A JSON document.",
        flag=("--json",),
        escape=esc_json,
        check=("python3", "-m", "json.tool", "__PATH__"),
    ),
    "yaml": TemplateSpec(
        name="yaml",
        label="YAML document",
        ext=".yaml",
        body=YAML,
        default_desc="A YAML document.",
        flag=("--yaml",),
        comment="#",
        check=(
            "python3",
            "-c",
            "import sys,yaml;yaml.safe_load(open(sys.argv[1]))",
            "__PATH__",
        ),
    ),
    "toml": TemplateSpec(
        name="toml",
        label="TOML document",
        ext=".toml",
        body=TOML,
        default_desc="A TOML document.",
        flag=("--toml",),
        comment="#",
        check=(
            "python3",
            "-c",
            "import sys,tomllib;tomllib.load(open(sys.argv[1],'rb'))",
            "__PATH__",
        ),
    ),
    "ini": TemplateSpec(
        name="ini",
        label="INI config",
        ext=".ini",
        body=INI,
        default_desc="An INI configuration file.",
        flag=("--ini",),
        comment=";",
    ),
    "makefile": TemplateSpec(
        name="makefile",
        label="Makefile",
        ext="",
        body=MAKEFILE,
        default_desc="A Makefile.",
        flag=("--makefile",),
        exact_names=("Makefile", "makefile", "GNUmakefile"),
        comment="#",
    ),
    "dockerfile": TemplateSpec(
        name="dockerfile",
        label="Dockerfile",
        ext="",
        body=DOCKERFILE,
        default_desc="A Dockerfile.",
        flag=("--dockerfile",),
        exact_names=("Dockerfile", "Containerfile"),
        comment="#",
    ),
    "gitignore": TemplateSpec(
        name="gitignore",
        label=".gitignore",
        ext="",
        body=GITIGNORE,
        default_desc="A .gitignore file.",
        flag=("--gitignore",),
        exact_names=(".gitignore", ".gitignore.template"),
        comment="#",
    ),
    "editorconfig": TemplateSpec(
        name="editorconfig",
        label=".editorconfig",
        ext="",
        body=EDITORCONFIG,
        default_desc="An .editorconfig file.",
        flag=("--editorconfig",),
        exact_names=(".editorconfig",),
        comment="#",
    ),
    "license": TemplateSpec(
        name="license",
        label="LICENSE file",
        ext="",
        body=LICENSE_TPL,
        default_desc="A LICENSE file.",
        flag=("--license-file",),
        exact_names=("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"),
        comment="#",
    ),
}

EXT_MAP: dict[str, str] = {
    ".py": "python",
    ".sh": "sh",
    ".bash": "sh",
    ".zsh": "zsh",
    ".rs": "rust",
    ".js": "js",
    ".mjs": "js",
    ".cjs": "cjs",
    ".ts": "ts",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".go": "go",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".lua": "lua",
    ".rb": "ruby",
    ".java": "java",
    ".sql": "sql",
    ".tex": "tex",
    ".md": "markdown",
    ".markdown": "markdown",
    ".nix": "nix",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
}

EXACT_NAMES: dict[str, str] = {}
for _name, _spec in TEMPLATES.items():
    for _exact in _spec.exact_names:
        EXACT_NAMES[_exact] = _name


# ===========================================================================
# Helpers
# ===========================================================================
def render(template: str, values: Mapping[str, str]) -> str:
    """Replace @@KEY@@ placeholders with escaped values."""
    out = template
    for key, val in values.items():
        out = out.replace(f"@@{key}@@", val)
    return out


def pascal_case(s: str) -> str:
    parts = re.split(r"[_\-]+", s)
    name = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if not name:
        name = "Main"
    if name[0].isdigit():
        name = "_" + name
    return name


def get_git_config(key: str) -> str | None:
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "config", "--get", key],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def run_cmd(cmd: Sequence[str], cwd: Path | None = None) -> tuple[bool, str]:
    if shutil.which(cmd[0]) is None:
        return True, f"skipped (not found): {cmd[0]}"
    try:
        result = subprocess.run(
            list(cmd),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, ""


def inject_license(content: str, license_name: str, comment: str, year: int, author: str) -> str:
    header_lines = [f"{comment} SPDX-License-Identifier: {license_name}"]
    if author:
        header_lines.append(f"{comment} Copyright (c) {year} {author}")
    header = "\n".join(header_lines) + "\n"

    if content.startswith("#!"):
        first, _, rest = content.partition("\n")
        return first + "\n" + header + rest
    return header + content


def replace_shebang(content: str, new_shebang: str) -> str:
    if content.startswith("#!"):
        _, _, rest = content.partition("\n")
        return new_shebang + "\n" + rest
    return new_shebang + "\n" + content


def strip_todos(content: str) -> str:
    return "\n".join(line for line in content.splitlines() if "TODO:" not in line) + (
        "\n" if content.endswith("\n") else ""
    )


# ===========================================================================
# Template resolution
# ===========================================================================
def resolve_template(args: argparse.Namespace, target: Path) -> TemplateSpec:
    if args.template:
        if args.template not in TEMPLATES:
            _die(f"unknown template '{args.template}'. Use -l to list.")
        return TEMPLATES[args.template]

    name = target.name
    if name in EXACT_NAMES:
        return TEMPLATES[EXACT_NAMES[name]]

    suffix = target.suffix.lower()
    if suffix in EXT_MAP:
        return TEMPLATES[EXT_MAP[suffix]]

    _die(
        f"cannot infer a template from '{target.name}'. "
        "Pass a template flag, use --template NAME, or add an extension. "
        "Run with -l to see all templates."
    )
    raise AssertionError("unreachable")


def _die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def _resolve_target(args: argparse.Namespace, spec: TemplateSpec) -> Path:
    target = args.filename
    if args.output:
        target = Path(args.output) / target.name

    # Append the template extension if the user gave no suffix.
    if spec.ext and not target.suffix and target.name not in EXACT_NAMES:
        target = target.with_suffix(spec.ext)

    return target


# ===========================================================================
# Output handling
# ===========================================================================
def _print_diff(path: Path, old: str, new: str) -> None:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=str(path),
        tofile=str(path) + " (new)",
    )
    sys.stderr.writelines(diff)


def _write_output(
    args: argparse.Namespace,
    target: Path,
    content: str,
    spec: TemplateSpec,
    quiet: bool,
) -> str:
    """Write content (respecting all output flags). Returns 'created'/'overwritten'/'unchanged'."""
    if args.stdout:
        sys.stdout.write(content)
        return "created"

    target.parent.mkdir(parents=True, exist_ok=True)

    existed = target.exists()
    if existed:
        try:
            old = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            old = ""
        if old == content:
            if not quiet:
                print(f"unchanged: {target}")
            return "unchanged"
        if not args.force:
            _die(f"'{target}' already exists. Use --force to overwrite.")
        if args.diff:
            _print_diff(target, old, content)
        if args.backup:
            backup = target.with_name(target.name + ".bak")
            backup.write_text(old, encoding="utf-8")
            if not quiet:
                print(f"backup:    {backup}")

    if args.dry_run:
        action = "would overwrite" if existed else "would create"
        print(f"[dry-run] {action}: {target}  (template={spec.name})")
        return "overwritten" if existed else "created"

    atomic_write(target, content, encoding="utf-8")

    if args.mode is not None:
        os.chmod(target, args.mode)
    elif spec.executable and not args.no_exec:
        current = target.stat().st_mode
        target.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if not quiet:
        print(f"{'overwritten' if existed else 'created'}: {target}  (template={spec.name})")

    return "overwritten" if existed else "created"


# ===========================================================================
# Post-processing
# ===========================================================================
def _maybe_check(spec: TemplateSpec, path: Path, quiet: bool) -> bool:
    if not spec.check:
        return True
    cmd = [part.replace("__PATH__", str(path)) for part in spec.check]
    ok, msg = run_cmd(cmd)
    if not ok:
        print(f"check FAILED ({' '.join(cmd)}):\n{msg}", file=sys.stderr)
        return False
    if not quiet:
        print(f"check OK: {' '.join(cmd[:2])}")
    return True


def _maybe_format(spec: TemplateSpec, path: Path, quiet: bool) -> None:
    if not spec.formatter:
        return
    cmd = [part.replace("__PATH__", str(path)) for part in spec.formatter]
    # Append the path if the template didn't reference it.
    if "__PATH__" not in " ".join(spec.formatter):
        cmd.append(str(path))
    ok, msg = run_cmd(cmd)
    if not ok:
        print(f"format failed ({' '.join(cmd)}):\n{msg}", file=sys.stderr)
    elif not quiet:
        print(f"formatted: {path}")


def _maybe_git(path: Path, quiet: bool) -> None:
    if shutil.which("git") is None:
        return
    d = path.resolve().parent
    while d != d.parent:
        if (d / ".git").exists():
            subprocess.run(["git", "add", "--", str(path)], cwd=d, check=False)
            if not quiet:
                print(f"git add: {path}")
            return
        d = d.parent
    subprocess.run(["git", "init"], cwd=path.resolve().parent, check=False)
    subprocess.run(["git", "add", "--", str(path)], cwd=path.resolve().parent, check=False)
    if not quiet:
        print(f"git init + add: {path}")


def _maybe_hook(hook: str, path: Path, quiet: bool) -> None:
    result = subprocess.run(
        hook,
        shell=True,
        cwd=path.resolve().parent,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"post-hook failed (exit {result.returncode}):\n{result.stderr}",
            file=sys.stderr,
        )
    elif not quiet:
        print(f"post-hook ok: {hook}")


def _open_path(path: Path, is_dir: bool) -> None:
    if is_dir:
        opener = shutil.which("open") or shutil.which("xdg-open") or shutil.which("explorer")
        target = path.resolve().parent
        if opener:
            subprocess.run([opener, str(target)], check=False)
        else:
            print(f"open manually: {target}")
    else:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        if editor:
            subprocess.run([editor, str(path)], check=False)
        else:
            print(f"$EDITOR not set; open manually: {path}")


# ===========================================================================
# --list / --version output
# ===========================================================================
def list_templates(as_json: bool) -> None:
    if as_json:
        data: list[dict[str, Any]] = []
        for spec in TEMPLATES.values():
            data.append({
                "name": spec.name,
                "label": spec.label,
                "extension": spec.ext,
                "flags": list(spec.flag),
                "exact_names": list(spec.exact_names),
                "executable": spec.executable,
                "default_description": spec.default_desc,
            })
        print(json_mod.dumps(data, indent=2))
        return

    rows: list[tuple[str, str, str, str]] = []
    for spec in TEMPLATES.values():
        flags = ", ".join(spec.flag) if spec.flag else "--template " + spec.name
        ext = spec.ext or ", ".join(spec.exact_names) or "(none)"
        rows.append((spec.name, ext, flags, spec.label))
    width_name = max(len(r[0]) for r in rows)
    width_ext = max(len(r[1]) for r in rows)
    width_flag = max(len(r[2]) for r in rows)
    print(f"{'NAME':<{width_name}}  {'EXT/NAME':<{width_ext}}  {'FLAG':<{width_flag}}  LABEL")
    for name, ext, flag, label in rows:
        print(f"{name:<{width_name}}  {ext:<{width_ext}}  {flag:<{width_flag}}  {label}")


# ===========================================================================
# CLI
# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="initer.py",
        description="Generate a new source/config file from a built-in template.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  initer.py app.py               # Python argparse (auto)\n"
            "  initer.py app.py -t            # Typer variant\n"
            "  initer.py lib.rs               # Rust\n"
            "  initer.py index.html           # HTML5\n"
            "  initer.py Makefile             # exact-name match\n"
            "  initer.py foo -r               # Rust, appends .rs\n"
            "  initer.py -l                   # list all\n"
        ),
    )

    parser.add_argument(
        "filename",
        type=Path,
        nargs="?",
        help="Target filename. Extension or exact name selects the template.",
    )

    # --- template selection (mutually exclusive, all optional) ---
    tmpl = parser.add_mutually_exclusive_group()
    for spec in TEMPLATES.values():
        if spec.flag:
            tmpl.add_argument(
                *spec.flag,
                dest="template",
                action="store_const",
                const=spec.name,
                help=spec.label,
            )
    tmpl.add_argument(
        "--template",
        dest="template",
        metavar="NAME",
        help="Select template by name (see --list).",
    )

    parser.add_argument(
        "--template-file",
        metavar="PATH",
        help="Render a custom template file (use '-' for stdin).",
    )

    # --- global ---
    parser.add_argument("-V", "--version", action="store_true", help="Print version and exit.")
    parser.add_argument("-l", "--list", action="store_true", help="List templates and exit.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output.")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error output.")
    parser.add_argument("--as-json", action="store_true", help="Machine-readable output.")

    # --- output ---
    parser.add_argument("-o", "--output", metavar="DIR", help="Write into DIR.")
    parser.add_argument("--stdout", action="store_true", help="Print to stdout, don't write a file.")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Show plan, don't write.")
    parser.add_argument("--diff", action="store_true", help="Show unified diff before overwriting.")
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing files.")
    parser.add_argument("-B", "--backup", action="store_true", help="Write .bak before overwriting.")
    parser.add_argument("--no-exec", action="store_true", help="Don't chmod +x shebang'd scripts.")
    parser.add_argument("--mode", metavar="OCTAL", default=None, help="Explicit file mode (e.g. 0o755).")
    parser.add_argument("--shebang", metavar="STR", default=None, help="Override the shebang line.")

    # --- content ---
    parser.add_argument(
        "-d",
        "--description",
        default=None,
        help="Description (default depends on template).",
    )
    parser.add_argument("--author", default=None, help="Author name (default: git config).")
    parser.add_argument("--email", default=None, help="Author email (default: git config).")
    parser.add_argument("--year", type=int, default=None, help="Copyright year.")
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Creation date (for reproducible output).",
    )
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra template variable (repeatable).",
    )
    parser.add_argument(
        "--license",
        default=None,
        metavar="SPDX",
        help="SPDX license identifier injected as header (and used by LICENSE template).",
    )
    parser.add_argument("--title", default=None, help="Document title (HTML/Markdown).")
    parser.add_argument("--lang", default="en", help="HTML lang attribute (default: en).")
    parser.add_argument(
        "--no-timestamp",
        action="store_true",
        help="Omit the creation date from templates.",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Skip git config lookups for author/email.",
    )
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="Strip TODO scaffolding lines from the output.",
    )

    # --- post-processing ---
    parser.add_argument("--check", action="store_true", help="Syntax-check the output.")
    parser.add_argument("--format", action="store_true", help="Run the language formatter.")
    parser.add_argument("--git", action="store_true", help="git init + add if not already in a repo.")
    parser.add_argument(
        "--post-hook",
        metavar="CMD",
        default=None,
        help="Shell command to run after writing.",
    )
    parser.add_argument("-i", "--interactive", action="store_true", help="Prompt for missing values.")
    parser.add_argument("--open", action="store_true", help="Open the new file in $EDITOR.")
    parser.add_argument("--open-dir", action="store_true", help="Open the containing directory.")

    return parser


def _parse_var(s: str) -> tuple[str, str]:
    if "=" not in s:
        raise argparse.ArgumentTypeError(f"--var expects KEY=VALUE, got: {s!r}")
    key, _, val = s.partition("=")
    return key.strip(), val


def _build_tokens(args: argparse.Namespace, target: Path, spec: TemplateSpec) -> dict[str, str]:
    # Date
    if args.no_timestamp:
        date_str = ""
    elif args.date:
        try:
            date_obj = date_cls.fromisoformat(args.date)
        except ValueError:
            _die(f"--date must be YYYY-MM-DD, got {args.date!r}")
        date_str = date_obj.isoformat()
    else:
        date_str = date_cls.today().isoformat()

    if args.year is not None:
        year = args.year
    elif args.date:
        year = date_cls.fromisoformat(args.date).year
    else:
        year = date_cls.today().year

    # Author / email
    author = args.author
    email = args.email
    if not args.no_config:
        if author is None:
            author = get_git_config("user.name")
        if email is None:
            email = get_git_config("user.email")
    author = author or os.environ.get("USER") or ""
    email = email or ""

    # Description
    description = args.description
    if description is None and args.interactive:
        try:
            description = input(f"Description [{spec.default_desc}]: ").strip() or spec.default_desc
        except EOFError:
            description = spec.default_desc
    if description is None:
        description = spec.default_desc

    # Custom-var dict
    extra: dict[str, str] = {}
    for pair in args.var:
        k, v = _parse_var(pair)
        extra[k.upper()] = v

    # Title / lang
    title = args.title if args.title is not None else target.stem
    lang = args.lang

    # LICENSE-text resolution
    license_name = args.license or DEFAULT_LICENSE
    license_text = LICENSE_TEXTS.get(license_name)
    if license_text is None:
        license_text = LICENSE_TEXTS[DEFAULT_LICENSE]
        if spec.name == "license":
            print(
                f"warning: no bundled text for '{license_name}'; using MIT text. Add it to LICENSE_TEXTS if needed.",
                file=sys.stderr,
            )
    license_text = license_text.replace("@@YEAR@@", str(year)).replace("@@AUTHOR@@", author or "<author>")

    # Apply per-template escaping
    esc = spec.escape
    tokens: dict[str, str] = {
        "DESCRIPTION": esc(description),
        "DATE": date_str,
        "YEAR": str(year),
        "AUTHOR": esc(author),
        "EMAIL": esc(email),
        "TITLE": esc(title),
        "LANG": lang,
        "FILENAME": target.name,
        "STEM": esc(target.stem),
        "CLASSNAME": pascal_case(target.stem),
        "LICENSE_TEXT": license_text,
    }
    tokens.update(extra)  # user vars are NOT escaped — caller's responsibility
    return tokens


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"initer.py {VERSION}")
        return 0

    if args.list:
        list_templates(args.as_json)
        return 0

    if args.filename is None:
        parser.error("the following arguments are required: filename")

    # Parse --mode as octal
    if args.mode is not None:
        try:
            args.mode = int(args.mode, 8) if isinstance(args.mode, str) else args.mode
        except ValueError:
            parser.error(f"--mode expects an octal integer (e.g. 0755), got {args.mode!r}")

    # Resolve target + spec
    # First resolve template to know the extension, then fix up target.
    target = args.filename
    if args.template_file:
        if args.template_file == "-":
            body = sys.stdin.read()
        else:
            try:
                body = Path(args.template_file).read_text(encoding="utf-8")
            except OSError as exc:
                _die(f"cannot read --template-file: {exc}")
        spec = TemplateSpec(
            name="custom",
            label="custom template",
            ext="",
            body=body,
            default_desc="",
            escape=esc_generic,
            comment="#",
        )
    else:
        spec = resolve_template(args, target)

    target = _resolve_target(args, spec)

    # Build tokens
    tokens = _build_tokens(args, target, spec)

    # Apply shebang override
    rendered_body = spec.body
    if args.shebang and spec.body.startswith("#!"):
        rendered_body = replace_shebang(spec.body, args.shebang)

    # Render
    content = render(rendered_body, tokens)

    # Optional TODO-stripping
    if args.minimal:
        content = strip_todos(content)

    # Optional SPDX license header injection
    if args.license:
        content = inject_license(
            content,
            args.license,
            spec.comment,
            int(tokens["YEAR"]) if tokens.get("YEAR") else 0,
            tokens.get("AUTHOR", ""),
        )

    quiet = args.quiet

    # Handle --as-json summary mode: collect, don't print, at end.
    action = _write_output(args, target, content, spec, quiet=quiet)

    # Post-processing (skipped for --stdout / --dry-run / unchanged)
    if not args.stdout and not args.dry_run and action != "unchanged":
        if args.check and not _maybe_check(spec, target, quiet):
            return 1
        if args.format:
            _maybe_format(spec, target, quiet)
        if args.git:
            _maybe_git(target, quiet)
        if args.post_hook:
            _maybe_hook(args.post_hook, target, quiet)
        if args.open:
            _open_path(target, is_dir=False)
        if args.open_dir:
            _open_path(target, is_dir=True)

    if args.as_json and not args.stdout:
        print(
            json_mod.dumps({
                "path": str(target.resolve()),
                "template": spec.name,
                "action": action,
                "bytes": len(content.encode("utf-8")),
            })
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
