#!/usr/bin/env python3
"""The profile read across the merge deploy window.

WHY THIS EXISTS

The merge migration is applied BY HAND in the Supabase editor; the app deploys
from a git push. Those cannot be simultaneous, so one of the two orders happens
first and the code has to survive it.

Push-first is the dangerous one. utils/profile.py selects `credits`, and
require_active_account() is called at module scope on both spend pages -- so an
unknown-column error there is not a degraded balance, it is Discovery and Deep
Analysis down for every user until someone pastes SQL.

The fallback computes the same sum the migration does. The two assertions that
matter are that it engages for the right error and NOT for any other one: a
network blip read as "the migration has not run" would silently serve a balance
from frozen columns forever.

No network, no database. streamlit and supabase are stubbed.

Usage:
    python3 tests/test_profile_fallback.py
"""
import sys, types
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

st = types.ModuleType("streamlit")
st.session_state = {"auth.user": {"id": "u1"}}
st.secrets = {}
sys.modules["streamlit"] = st

calls = {"selects": []}
class _APIError(Exception):
    """Shaped like postgrest.exceptions.APIError: the code is an attribute."""
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(str({"code": code, "message": message}))


class _Q:
    def __init__(self, db, cols): self.db, self.cols = db, cols
    def eq(self, *a): return self
    def limit(self, n): return self
    def maybe_single(self): return self
    def execute(self):
        calls["selects"].append(self.cols)
        if "credits" in self.cols.split(",") and self.db["missing"]:
            raise _APIError("42703", "column profiles.credits does not exist")
        if "credits" in self.cols.split(","):
            return types.SimpleNamespace(data=[{"user_id": "u1", "credits": 8}])
        return types.SimpleNamespace(
            data=[{"user_id": "u1", "scan_credits": 5, "deep_credits": 3}])
class _T:
    def __init__(self, db): self.db = db
    def select(self, cols): return _Q(self.db, cols)
class _C:
    def __init__(self, db): self.db = db
    def table(self, n): return _T(self.db)

DB = {"missing": False}
sc = types.ModuleType("utils.supabase_client")
sc.get_client = lambda: _C(DB)
sys.modules["utils.supabase_client"] = sc

import utils.profile as P
PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


print("=" * 70)
print("  profile read: surviving the window before the migration lands")
print("=" * 70)

DB["missing"] = False; calls["selects"].clear()
r = P.get_my_profile()
check("normal path reads credits", r.get("credits") == 8, str(r))
check("...and does not touch the frozen columns",
      all("scan_credits" not in c for c in calls["selects"]), str(calls["selects"]))

DB["missing"] = True; calls["selects"].clear()
r = P.get_my_profile()
check("missing column does NOT raise (would take both spend pages down)", r is not None)
check("falls back to scan+deep, the same sum the migration computes",
      r.get("credits") == 8, str(r))
check("...after trying credits first", calls["selects"][0].endswith("credits"),
      str(calls["selects"]))

# An unrelated error must NOT be swallowed into a wrong balance.
#
# The FIRST select fails transiently; the fallback select would succeed. That
# asymmetry is the whole test: without the error-shape guard the code treats a
# network blip as "the migration has not run", quietly serves a balance built
# from frozen pre-merge columns, and never raises again. Making both selects
# fail proves nothing -- it raises either way, which is how this assertion
# passed against the broken version on the first attempt.
class _OnlyFirstFails(_Q):
    def execute(self):
        calls["selects"].append(self.cols)
        if "credits" in self.cols.split(","):
            raise RuntimeError("connection refused: could not reach the database")
        return types.SimpleNamespace(
            data=[{"user_id": "u1", "scan_credits": 99, "deep_credits": 99}])

_T.select = lambda self, cols: _OnlyFirstFails(self.db, cols)
calls["selects"].clear()
raised, got = False, None
try:
    got = P.get_my_profile()
except RuntimeError:
    raised = True
check("a transient failure propagates instead of falling back", raised,
      f"swallowed it and returned {got} -- a network blip must not be read as "
      f"'migration not applied'")
check("...and no frozen-column balance was served",
      got is None, f"served {got}")


# ── fetch_credits: the SHARED helper Home now delegates to ──────────────────
#
# Behavioural, not a source grep. The previous version of this checked Home's
# source for the strings "scan_credits" and "42703" -- and passed against a
# fallback disabled with `if False:`, because the docstring above it mentioned
# both. It also passed when the fallback was made dead code by an early return,
# because dead code is still text. Only running it can tell the difference.
_T.select = lambda self, cols: _Q(self.db, cols)

DB["missing"] = False
check("fetch_credits reads the merged balance", P.fetch_credits("u1") == 8,
      str(P.fetch_credits("u1")))

DB["missing"] = True
check("fetch_credits falls back to scan+deep", P.fetch_credits("u1") == 8,
      str(P.fetch_credits("u1")))

# The dead-code case the source check could not catch.
class _NoFallback(_Q):
    def execute(self):
        if "credits" in self.cols.split(","):
            raise _APIError("42703", "column profiles.credits does not exist")
        raise AssertionError("fallback query should have run but did not")

_T.select = lambda self, cols: _NoFallback(self.db, cols)
fell_back = True
try:
    P.fetch_credits("u1")
except AssertionError:
    pass                      # the fallback DID run -- correct
except RuntimeError:
    fell_back = False         # it never attempted the fallback
check("the fallback actually executes, not merely exists", fell_back,
      "the frozen-column query was unreachable")

check("an empty user id reads as unknown", P.fetch_credits("") is None)
# ── the query SHAPE, which is what actually broke ──────────────────────────
#
# The detection above was correct from the start. What defeated it was
# .maybe_single(): asked for a column that does not exist, PostgREST answers
#
#     {"code": "42703", "message": "column profiles.credits does not exist"}
#
# and maybe_single() catches that and re-raises
#
#     {"code": "204",   "message": "Missing response"}
#
# with no code and no column name. Nothing downstream can tell that apart from
# a network failure, so the fallback declined to run, Home rendered no balance,
# and the owner asked where the credit balance had gone.
src = (REPO / "utils" / "profile.py").read_text()
import ast as _ast
_tree = _ast.parse(src)
for _fn in ("get_my_profile", "fetch_credits", "_one"):
    _node = next((n for n in _ast.walk(_tree)
                  if isinstance(n, _ast.FunctionDef) and n.name == _fn), None)
    check(f"{_fn} exists", _node is not None)
    if _node is None:
        continue
    _uses = any(isinstance(n, _ast.Attribute) and n.attr == "maybe_single"
                for n in _ast.walk(_node))
    check(f"{_fn} does not use maybe_single",
          not _uses,
          "maybe_single() masks the real PostgREST error as a generic 204, "
          "which is what hid the balance on Home")

# And the predicate must key on the structured code, not only the message.
# The code attribute must be read on its own. A fake whose __str__ embeds the
# code proves nothing -- the message fallback catches it either way, which is
# how this assertion first passed against a predicate that had stopped looking
# at .code entirely.
class _CodeOnly(Exception):
    code = "42703"
    def __str__(self): return "request failed"

check("a 42703 error is recognised by .code alone, with nothing in the message",
      P._is_missing_credits_column(_CodeOnly()),
      "the structured code is the only reliable signal; library message text "
      "changes between versions")
check("...and by the message when no code attribute is present",
      P._is_missing_credits_column(
          RuntimeError("column profiles.credits does not exist")))
check("a generic 204 'Missing response' is NOT recognised",
      not P._is_missing_credits_column(_APIError("204", "Missing response")),
      "treating maybe_single's masked error as 'not migrated' would serve a "
      "frozen balance through any outage")
check("a plain connection error is NOT recognised",
      not P._is_missing_credits_column(RuntimeError("connection refused")))

print("\n" + "=" * 70)
print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
for n, d in FAILED:
    print(f"    - {n}: {d}")
print("=" * 70)
sys.exit(1 if FAILED else 0)
