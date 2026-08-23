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


def test_selectively_copied_services_can_import():
    """A service Dockerfile that copies four files must not import a fifth.

    utils/prices.py promises "standard library only" and sync/Dockerfile ships
    exactly the files that promise implies. Adding one shared import to
    prices.py -- which is the natural thing to do while decoupling -- killed the
    nightly price sync at container start, with nothing in this repo to catch
    it. The failure is invisible locally, where the whole tree is on sys.path.
    """
    import re
    import subprocess
    import tempfile

    print("\nservice images: every import must be in the image")
    for svc in ("sync",):
        df = REPO / svc / "Dockerfile"
        if not df.exists():
            continue
        copied = []
        for line in df.read_text().splitlines():
            m = re.match(r"\s*COPY\s+(\S+)\s+(\S+)", line)
            if m and not m.group(1).startswith(("requirements", "--")):
                copied.append(m.group(1))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for src in copied:
                s = REPO / src
                if not s.exists():
                    continue
                dst = root / src
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(s.read_bytes())
            mods = []
            for src in copied:
                if src.endswith(".py") and "__init__" not in src:
                    mods.append(src[:-3].replace("/", "."))
            r = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0,'.')\n"
                 + "\n".join(f"import {m}" for m in mods)],
                cwd=root, capture_output=True, text=True)
            detail = (r.stderr or "").strip().splitlines()[-1:] or [""]
            check(f"{svc} image imports its own modules", r.returncode == 0, detail[0])


def _re_split(spec: str) -> str:
    """The distribution name from a requirements line, extras and pin removed."""
    import re
    return re.split(r"[\[<>=!~; ]", spec, 1)[0].strip()


def test_core_api_can_run_without_the_portal():
    """core-api ships utils/ but no streamlit, no torch and no transformers.

    The failure this prevents is the one that killed price-sync: an image that
    BUILDS and then dies at import, or worse, imports fine and raises on the
    first request that reaches a lazily-imported dependency the image does not
    contain. utils/config.py, utils/deep_analysis.py, utils/supabase_client.py
    and utils/sentiment.py all reach for one of those three -- every one of
    them must stay inside a function AND inside a try, which is the contract
    Step 1 of the migration established.
    """
    import ast

    print("\ncore-api image: the portal's dependencies must stay optional")
    df = REPO / "core_api" / "Dockerfile"
    if not df.exists():
        return

    HEAVY = ("streamlit", "torch", "transformers")
    closure, queue = set(), ["core_api/main.py"]
    third: set[str] = set()
    while queue:
        rel = queue.pop()
        if rel in closure:
            continue
        closure.add(rel)
        path = REPO / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            else:
                continue
            for m in mods:
                top = m.split(".")[0]
                if top == "utils":
                    cand = m.replace(".", "/") + ".py"
                    if (REPO / cand).exists():
                        queue.append(cand)
                    continue
                if top == "core_api":
                    continue
                if top in HEAVY:
                    cur, guarded, in_func = node, False, False
                    while cur in parents:
                        cur = parents[cur]
                        if isinstance(cur, ast.Try):
                            guarded = True
                        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            in_func = True
                    check(f"{rel}:{node.lineno} {top} import is optional",
                          guarded and in_func,
                          f"try={guarded} in_function={in_func} -- the image has no {top}")
                elif top not in sys.stdlib_module_names:
                    third.add(top)

    # Every third-party module reachable at MODULE scope must be installed, or
    # the container dies on the first import exactly as price-sync did.
    # PARSED, not substring-matched. `"numpy" in text` is satisfied by the
    # line "#numpy==1.26.4", so commenting a dependency out passed this check
    # while the container lost the package.
    installed = set()
    for line in (REPO / "core_api" / "requirements.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if not line:
            continue
        name = _re_split(line)
        installed.add(name)
    alias = {"polygon": "polygon-api-client", "dotenv": "python-dotenv",
             "dateutil": "python-dateutil", "yaml": "pyyaml"}
    for mod in sorted(third):
        want = alias.get(mod, mod)
        check(f"{mod} is in core_api/requirements.txt", want in installed,
              f"imported by the closure; installed: {sorted(installed)}")
    check("the closure was actually walked", len(closure) > 5, str(len(closure)))

    # Every path the Dockerfile copies must exist, or the COPY fails the build.
    import re as _re
    for line in df.read_text().splitlines():
        m = _re.match(r"\s*COPY\s+(.+)", line)
        if not m:
            continue
        parts = m.group(1).split()
        for src in parts[:-1]:
            if src.startswith("--"):
                continue
            check(f"Dockerfile COPY {src} exists", (REPO / src).exists(), src)


def main() -> int:
    major, minor = pinned_version()
    print("=" * 74)
    print(f"  runtime compatibility: deploy target is python {major}.{minor}")
    print("=" * 74)

    # Every directory that ships, not just the two that were changed last.
    files = sorted(
        [f for d in ("utils", "pages", "scripts", "worker", "sync",
                     "inference", "payments_api", "tests")
         for f in (REPO / d).glob("*.py")]
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

        # The OTHER half of PEP 701, and the half this file used to miss: before
        # 3.12 a replacement field may not reuse the quote character that
        # delimits its own f-string. f"{d["k"]}" parses clean on a 3.12 dev box
        # and is a hard SyntaxError on the 3.11 deploy target, exactly like the
        # backslash case that took every page down once already.
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            whole = ast.get_source_segment(src, node) or ""
            body = whole.lstrip("fFrRbBuU")
            for delim in ('"""', "'''", '"', "'"):
                if body.startswith(delim):
                    break
            else:
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.FormattedValue):
                    continue
                expr = ast.get_source_segment(src, sub.value) or ""
                if delim in expr:
                    check(f"{rel}:{node.lineno} f-string expression does not "
                          f"reuse its own {delim} delimiter", False,
                          f"python {major}.{minor} cannot parse: {expr[:60]}")

        # Other post-3.11 syntax that would ship silently.
        for node in ast.walk(tree):
            if (major, minor) < (3, 12) and isinstance(
                    node, (getattr(ast, "TypeAlias", ()),)):
                check(f"{rel}:{node.lineno} no PEP 695 type alias", False,
                      f"requires python 3.12")
            if (major, minor) < (3, 11) and isinstance(node, ast.TryStar):
                check(f"{rel}:{node.lineno} no except* group", False,
                      "requires python 3.11")
            if (major, minor) < (3, 12) and isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if getattr(node, "type_params", None):
                    check(f"{rel}:{node.lineno} no PEP 695 generic parameters",
                          False, "requires python 3.12")

    test_selectively_copied_services_can_import()
    test_core_api_can_run_without_the_portal()
    print(f"\n  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0



if __name__ == "__main__":
    sys.exit(main())
