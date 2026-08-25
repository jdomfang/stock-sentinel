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
class _Q:
    def __init__(self, db, cols): self.db, self.cols = db, cols
    def eq(self, *a): return self
    def maybe_single(self): return self
    def execute(self):
        calls["selects"].append(self.cols)
        if "credits" in self.cols.split(",") and self.db["missing"]:
            raise RuntimeError('column profiles.credits does not exist (42703)')
        if "credits" in self.cols.split(","):
            return types.SimpleNamespace(data={"user_id": "u1", "credits": 8})
        return types.SimpleNamespace(data={"user_id": "u1", "scan_credits": 5, "deep_credits": 3})
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
            data={"user_id": "u1", "scan_credits": 99, "deep_credits": 99})

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
print("\n" + "=" * 70)
print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
for n, d in FAILED:
    print(f"    - {n}: {d}")
print("=" * 70)
sys.exit(1 if FAILED else 0)
