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


def test_every_symbol_a_page_imports_actually_exists():
    """A page that imports a name which is not there is a 500 on that page.

    THIS FILE ALREADY PARSED every page with ast and pronounced them fine
    while pages/Deep_Analysis.py imported `deliverable` from utils.analyze,
    which did not exist. Both Deep Analyze and Discovery crashed at import in
    production. Parsing proves the syntax; it says nothing about whether the
    other module exports the symbol.

    The pages cannot just be imported here -- they call st.set_page_config and
    render at module scope. So resolve their imports instead: for every
    `from utils... import X` a page performs, import the real module and look
    X up on it, falling back to importing it as a submodule.

    This is the check that turns "the tests were green" into something worth
    believing for a file no test can execute.
    """
    import ast
    import importlib

    print("\npage imports: every symbol must exist in the module it comes from")
    sys.path.insert(0, str(REPO))
    pages = sorted((REPO / "pages").glob("*.py"))
    if (REPO / "app.py").exists():
        pages.append(REPO / "app.py")
    LOCAL = ("utils", "core_api")

    for page in pages:
        tree = ast.parse(page.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module
                    and node.level == 0
                    and node.module.split(".")[0] in LOCAL):
                continue
            where = f"{page.name}:{node.lineno}"
            try:
                mod = importlib.import_module(node.module)
            except Exception as e:
                # streamlit-dependent modules are legitimately unimportable in
                # some environments; only report a genuinely missing module.
                if isinstance(e, ModuleNotFoundError) and \
                        e.name and e.name.split(".")[0] not in LOCAL:
                    continue
                check(f"{where} {node.module} imports", False,
                      f"{type(e).__name__}: {e}")
                continue
            for alias in node.names:
                if alias.name == "*" or hasattr(mod, alias.name):
                    continue
                try:
                    importlib.import_module(f"{node.module}.{alias.name}")
                except Exception:
                    check(f"{where} {node.module} exports {alias.name!r}",
                          False, "imported by a page, absent from the module")
    check("every page import resolves", True)


def test_every_entrypoint_guards_the_utils_import():
    """`utils` must resolve to this repo, not to site-packages.

    Streamlit Cloud can shadow it, and the symptom is not a clean ImportError --
    it is `KeyError: 'utils'` from inside _find_and_load_unlocked, because the
    parent package vanishes from sys.modules midway through loading a submodule.
    Home died this way on 2026-08-24 while Discovery, which had carried the
    sys.path guard for exactly this reason, kept working.

    Every entrypoint Streamlit can execute directly needs the guard, and it has
    to come BEFORE the first utils import -- afterwards it is decoration.
    """
    print("\nevery page pins `utils` to this repo before importing it")
    for f in [REPO / "app.py"] + sorted((REPO / "pages").glob("*.py")):
        lines = f.read_text().split("\n")
        guard = next((i for i, l in enumerate(lines) if "sys.path.insert" in l), None)
        first_utils = next((i for i, l in enumerate(lines)
                            if l.startswith("from utils") or l.startswith("import utils")), None)
        rel = f.relative_to(REPO)
        if first_utils is None:
            continue                      # imports nothing from utils; nothing to guard
        check(f"{rel} guards sys.path", guard is not None,
              "an unguarded entrypoint fails with KeyError: 'utils' on Streamlit Cloud")
        if guard is not None:
            check(f"{rel} guards BEFORE its first utils import", guard < first_utils,
                  f"guard at line {guard + 1}, utils imported at line {first_utils + 1}")


def test_no_suite_defines_a_test_it_never_runs():
    """A test that is never called is worse than no test.

    Three runners in this repo carried hand-typed call lists, and each one
    silently omitted tests added in the same commit: the low-balance threshold
    test in test_billing, and four spend-budget tests in test_core_api. Both
    suites reported green while asserting nothing about the thing they were
    written for -- and a mutation that broke the feature changed nothing.

    A `def test_*` at module scope must be reachable from main(), either by name
    or through discovery over globals().
    """
    print("\nevery test a suite defines is a test the suite runs")
    import ast
    for f in sorted((REPO / "tests").glob("test_*.py")):
        src = f.read_text()
        tree = ast.parse(src)
        defined = [n.name for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
        if not defined:
            continue                      # module-level script style; nothing to wire

        main_fn = next((n for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
        if main_fn is None:
            continue                      # runs at import; every def executes

        body = ast.dump(main_fn)
        # Discovery counts: iterating globals() picks up everything by name.
        discovers = "globals" in body
        if discovers:
            check(f"{f.name} discovers its tests", True)
            continue
        missing = [n for n in defined if f"'{n}'" not in body and n not in body]
        check(f"{f.name} runs every test it defines", not missing,
              f"defined but never called: {missing}")


# The modules that may reach Supabase with the SERVICE-ROLE key today.
#
# service_role bypasses RLS entirely. Any module holding it can read or write
# every row in every table regardless of policy, so this list IS the blast
# radius of a Streamlit compromise: a dependency takeover or one authorization
# slip on the admin page becomes a full-database incident rather than a
# user-scoped one.
#
# The list is frozen, not endorsed. Several entries are here because the whole
# analysis pipeline was built inside Streamlit and moved out in pieces; a
# privileged read behind a narrow FastAPI endpoint or a SECURITY DEFINER RPC
# would be correct for most of them. That migration is real work and is not
# done. What this test does is stop the radius GROWING while it is planned --
# adding a new one is now a deliberate act with a failing test attached.
SERVICE_ROLE_ALLOWED = {
    # services -- isolated, no user-supplied code path, correct place for it
    "core_api/main.py", "payments_api/main.py", "worker/reap.py",
    # the credit engine and its logs, all called through service-role RPCs
    "utils/credits.py", "utils/supabase_client.py", "utils/obs.py",
    "utils/signal_log.py", "utils/verdict_log.py", "utils/scan_log.py",
    "utils/x_metrics.py", "utils/sentiment_cache.py", "utils/corpus_cache.py",
    # pipeline reads and caches
    "utils/prices.py", "utils/finance.py", "utils/sector_query.py",
    "utils/deep_analysis.py", "utils/seed.py",
    # auth + contact: remember_tokens and contact_messages are service-role only
    "utils/auth.py", "utils/contact.py",
    # the admin page. The single riskiest entry, and the first one that should
    # move behind an authorized endpoint.
    "pages/Admin.py",
}


def test_the_service_role_blast_radius_does_not_grow():
    """A frozen inventory of who can bypass RLS.

    Not an assertion that the current shape is right -- it is an assertion that
    it does not get worse by accident. An external review rated this High and
    recommended an incremental migration; incremental only works if the
    starting point stops moving.
    """
    print("\nservice-role usage is frozen while the boundary is planned")
    import re
    found = set()
    for d in ("utils", "pages", "core_api", "worker", "sync", "payments_api"):
        for f in sorted((REPO / d).glob("*.py")):
            src = f.read_text(encoding="utf-8")
            code = "\n".join(l for l in src.split("\n")
                              if not l.lstrip().startswith("#"))
            if re.search(r"get_admin_client|SUPABASE_SERVICE_ROLE_KEY", code):
                found.add(str(f.relative_to(REPO)))

    added = sorted(found - SERVICE_ROLE_ALLOWED)
    check("no NEW module reaches for the service-role key", not added,
          f"{added} -- service_role bypasses RLS; prefer a narrow endpoint or a "
          f"SECURITY DEFINER RPC, and only widen this list deliberately")

    # And shrinking it is the goal, so a stale entry should be removed rather
    # than left to imply a dependency that no longer exists.
    gone = sorted(SERVICE_ROLE_ALLOWED - found)
    check("the allow-list has no stale entries", not gone,
          f"{gone} no longer uses it -- delete from SERVICE_ROLE_ALLOWED so the "
          f"list keeps meaning something")

    # The portal is the exposed surface. Anything here is reachable from a
    # browser session, so its count is the number that matters most.
    in_pages = sorted(f for f in found if f.startswith("pages/"))
    check("only the admin page holds it in the portal's page layer",
          in_pages == ["pages/Admin.py"], str(in_pages))


def test_no_module_references_an_undefined_name():
    """The check that would have caught a NameError before a user did.

    utils/auth.py referenced `logger` four times and never defined it. One of
    those was on the ORDINARY path -- every expired or already-spent remember
    code -- so it raised past the caller into a Streamlit exception page,
    skipping the branch that clears the dead code from localStorage, and the
    next load did it again. An unrecoverable loop for a normal user.

    Every suite was green. Nothing imports utils/auth.py, and the suite that
    covers it reads it as TEXT and greps substrings, so a name that does not
    exist is indistinguishable from one that does.

    Only "undefined name" is fatal here. Unused imports and shadowed builtins
    are style; a name that is not there is a crash.
    """
    print("\nno module references a name that does not exist")
    import subprocess
    targets = [str(REPO / d) for d in
               ("utils", "pages", "core_api", "payments_api", "worker", "sync")
               if (REPO / d).exists()] + [str(REPO / "app.py")]
    try:
        r = subprocess.run([sys.executable, "-m", "pyflakes", *targets],
                           capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        check("pyflakes is installed", False,
              "pip install -r requirements-dev.txt -- this check is why it exists")
        return
    if "No module named" in r.stderr:
        check("pyflakes is installed", False,
              "pip install -r requirements-dev.txt -- this check is why it exists")
        return

    undefined = [l for l in r.stdout.split("\n") if "undefined name" in l]
    check("no undefined names in shipped code", not undefined,
          " | ".join(undefined[:6]))


def test_only_utils_config_touches_st_secrets():
    """One module reads st.secrets. Everything else goes through utils.config.

    WHY THE RULE IS ABSOLUTE RATHER THAN "GUARD YOUR CALLS"

    st.secrets raises FileNotFoundError when no secrets.toml exists -- so on any
    host configured from environment variables (Railway, a VPS, any container)
    an unguarded read kills the page. utils/navigation.py did that on EVERY
    page, and pages/Admin.py did it at module scope.

    But guarding is not the fix, and an earlier version of this test enforced
    guarding and was WORSE THAN USELESS. It certified utils/billing.py:_cfg as
    safe: that function wrapped the read in `except: return ""`, so on Railway
    both payment secrets came back empty and checkout answered "Payments are
    not configured yet." to every user, silently, forever. The test blessed the
    money path being dead because the crash had been swallowed.

    So the criterion is not "does it crash" but "does it read config the way the
    host supplies it". utils.config does: os.environ first, st.secrets only if
    importable, exception swallowed. One implementation, one place to fix.

    MATCHES THE ATTRIBUTE, NOT THE CALL. A reviewer defeated the previous
    matcher with st.secrets["KEY"], st.secrets.KEY, and "KEY" in st.secrets --
    all of which raise identically and none of which is an ast.Call.
    """
    print("\nutils/config.py is the only module that may touch st.secrets")
    import ast

    allowed = (REPO / "utils" / "config.py").resolve()
    offenders = []

    targets = [REPO / "app.py"]
    for d in ("utils", "pages"):
        targets += sorted((REPO / d).rglob("*.py"))

    for f in targets:
        if not f.exists() or f.resolve() == allowed:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))

        # Whatever local name streamlit is bound to -- `import streamlit as st`
        # is the convention, but an alias must not launder the rule.
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name == "streamlit":
                        names.add(a.asname or "streamlit")
            elif isinstance(n, ast.ImportFrom) and n.module == "streamlit":
                for a in n.names:
                    if a.name == "secrets":
                        offenders.append(f"{f.relative_to(REPO)}:{n.lineno} "
                                         f"(from streamlit import secrets)")

        for n in ast.walk(tree):
            if (isinstance(n, ast.Attribute) and n.attr == "secrets"
                    and isinstance(n.value, ast.Name) and n.value.id in names):
                offenders.append(f"{f.relative_to(REPO)}:{n.lineno}")

    check("no module outside utils/config.py reads st.secrets",
          not offenders,
          f"{offenders} -- route through utils.config.get, which reads "
          f"os.environ first. A try/except is NOT a fix: it turns a missing "
          f"credential into a silent empty string.")


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
    test_every_symbol_a_page_imports_actually_exists()
    test_every_entrypoint_guards_the_utils_import()
    test_no_suite_defines_a_test_it_never_runs()
    test_the_service_role_blast_radius_does_not_grow()
    test_no_module_references_an_undefined_name()
    test_only_utils_config_touches_st_secrets()
    print(f"\n  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0



if __name__ == "__main__":
    sys.exit(main())
