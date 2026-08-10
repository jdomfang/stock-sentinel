#!/usr/bin/env python3
"""Every source file must parse on the runtime we actually deploy to.

WHY THIS EXISTS

utils/ui.py once shipped an f-string containing a backslash inside the
expression. That is a SyntaxError before Python 3.12; runtime.txt pins 3.11.
Because ui.py is imported by every page, the whole app would have 500'd at
import -- and nothing caught it: the dev box is 3.12, and no test imported
ui.py. It was found by a reviewer running the pinned interpreter.

This walks every FormattedValue in every file and rejects the constructs the
pinned version cannot parse, so the gap between "compiles here" and "compiles
there" cannot silently reopen.

Usage:
    python3 tests/test_runtime_compat.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    if not cond:
        print(f"  FAIL  {name}  <- {detail}")


def pinned_version() -> tuple[int, int]:
    rt = REPO / "runtime.txt"
    if rt.exists():
        m = re.search(r"(\d+)\.(\d+)", rt.read_text())
        if m:
            return int(m.group(1)), int(m.group(2))
    return (3, 11)


def main() -> int:
    major, minor = pinned_version()
    print("=" * 74)
    print(f"  runtime compatibility: deploy target is python {major}.{minor}")
    print("=" * 74)

    files = sorted(list((REPO / "utils").glob("*.py"))
                   + list((REPO / "pages").glob("*.py"))
                   + [REPO / "app.py"])
    for f in files:
        src = f.read_text(encoding="utf-8")
        rel = f.relative_to(REPO)
        try:
            tree = ast.parse(src, str(f))
        except SyntaxError as e:
            check(f"{rel} parses", False, str(e))
            continue
        check(f"{rel} parses", True)

        if (major, minor) >= (3, 12):
            continue
        # PEP 701 landed in 3.12. Before it, a backslash anywhere inside an
        # f-string REPLACEMENT FIELD is a hard SyntaxError.
        for node in ast.walk(tree):
            if not isinstance(node, ast.FormattedValue):
                continue
            seg = ast.get_source_segment(src, node.value) or ""
            if "\\" in seg:
                check(f"{rel}:{node.lineno} f-string has no backslash in the expression",
                      False, f"python {major}.{minor} cannot parse: {seg[:60]}")

    print(f"\n  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
