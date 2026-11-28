#!/data/data/com.termux/files/usr/bin/env python
"""Git clean/smudge filter: normalize shebangs.

clean  (stage):   #!/abs/path/interp [args]        -> #!/usr/bin/env interp [args]
                  (uses `env -S` when args are present)
smudge (checkout):#!/usr/bin/env [-S] interp [args] -> #!$PREFIX/bin/interp [args]
                  (only if that interpreter exists in $PREFIX/bin)

Non-shebang files are streamed through unchanged.
"""

from __future__ import annotations

import os
import sys


PREFIX = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
BIN_DIR = os.path.join(PREFIX, "bin")
CHUNK = 65536


def copy_rest(src, dst):
    while True:
        b = src.read(CHUNK)
        if not b:
            return
        dst.write(b)


def clean(interp, rest):
    base = os.path.basename(interp)
    if rest:
        return f"#!/usr/bin/env -S {base} {rest}"
    return f"#!/usr/bin/env {base}"


def smudge(interp, rest):
    if interp != "/usr/bin/env":
        return None
    rest = rest.strip()
    if not rest:
        return None
    if rest.startswith("-S "):
        rest = rest[3:].strip()
    parts = rest.split(None, 1)
    cmd = parts[0]
    args = parts[1] if len(parts) > 1 else ""
    target = os.path.join(BIN_DIR, cmd)
    if not os.path.isfile(target):
        return None  # interpreter not installed — leave as-is
    return f"#!{target}" + (" " + args if args else "")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("clean", "smudge"):
        sys.stderr.write("usage: shebang-filter clean|smudge\n")
        sys.exit(2)
    mode = sys.argv[1]
    inp, out = sys.stdin.buffer, sys.stdout.buffer

    head = inp.read(CHUNK)
    nl = head.find(b"\n")
    # Not a shebang, or first line longer than 64 KB (impossible for a real
    # shebang — kernel only reads ~127 bytes): pass through untouched.
    if not head.startswith(b"#!") or nl < 0:
        out.write(head)
        copy_rest(inp, out)
        return

    first = head[:nl]
    tail = head[nl + 1 :]

    body = first[2:].decode("utf-8", "surrogateescape")
    parts = body.split(None, 1)
    interp = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    new = None
    if interp:
        if mode == "clean" and interp.startswith("/") and os.path.basename(interp) != "env":
            new = clean(interp, rest)
        elif mode == "smudge":
            new = smudge(interp, rest)

    out.write(first if new is None else new.encode("utf-8", "surrogateescape"))
    out.write(b"\n")
    out.write(tail)
    copy_rest(inp, out)


if __name__ == "__main__":
    main()
