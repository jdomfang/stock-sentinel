#!/usr/bin/env python3
""""Remember me" must not put a Supabase refresh token in a URL.

WHAT IT USED TO DO

The refresh token was written to localStorage, then read back out and pushed
into the address bar as ?rt=<refresh_token>, because Streamlit gives browser JS
no other way to hand a value to Python. The parameter was cleared on the next
rerun -- which tidies the address bar and nothing else. By then the token is in
browser history, the platform request log, any reverse proxy, referrer headers
on outbound links, and any screenshot of the window.

A Supabase refresh token is long-lived and full-scope: whoever holds it can mint
access tokens for that user until it is revoked.

WHAT REPLACES IT

An opaque, single-use code. The token stays in public.remember_tokens, which
service_role alone can read, and the code is exchanged for it exactly once --
the row is deleted by the same statement that reads it.

The code still travels as a query parameter. That is not fixed and cannot be
while the app is Streamlit; what changed is that a leaked URL now carries a
credential that was spent on use.

Usage:
    docker compose -f docker-compose.test.yml up -d
    python3 tests/test_remember_me.py
"""

from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("psycopg2 is required:  pip install psycopg2-binary")
    sys.exit(2)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tests.migrations import chain as _chain  # noqa: E402

DSN = "host=127.0.0.1 port=5433 dbname=sentinel_test user=supabase_admin password=postgres"
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name, cond, detail=""):
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def h(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def rebuild(cur):
    cur.execute("drop schema if exists public cascade; create schema public;"
                " drop schema if exists auth cascade;")
    for p in _chain():
        cur.execute(p.read_text())


def user(cur) -> str:
    uid = str(uuid.uuid4())
    cur.execute("insert into auth.users (id,email) values (%s,%s)", (uid, f"{uid[:8]}@t.test"))
    return uid


def rpc(cur, fn, *args):
    cur.execute(f"select public.{fn}(" + ",".join(["%s"] * len(args)) + ")", args)
    return cur.fetchone()[0]


# ─────────────────────────────────────────────────────────── the exchange

def test_a_code_works_exactly_once(cur):
    print("\na stolen URL is worth nothing once the code has been used")
    uid = user(cur)
    rpc(cur, "remember_issue", h("code-a"), uid, "rt_secret", "30 days", None)

    first = rpc(cur, "remember_consume", h("code-a"))
    check("the first exchange returns the token", first.get("ok") is True, str(first))
    check("...the right token", first.get("refresh_token") == "rt_secret", str(first))
    check("...for the right user", first.get("user_id") == uid, str(first))

    again = rpc(cur, "remember_consume", h("code-a"))
    check("a replay is refused", again.get("ok") is False, str(again))
    check("...and yields no token", again.get("refresh_token") is None, str(again))

    unknown = rpc(cur, "remember_consume", h("never-issued"))
    check("an unknown code is refused the same way as a spent one",
          unknown.get("reason") == again.get("reason"),
          "distinguishing them tells an attacker whether a guess was ever real")


def test_an_expired_code_is_refused_and_consumed(cur):
    print("\nan expired code cannot be retried until it races a cleanup")
    uid = user(cur)
    rpc(cur, "remember_issue", h("code-b"), uid, "rt_x", "30 days", None)
    cur.execute("update public.remember_tokens set expires_at = now() - interval '1 day'"
                " where code_hash = %s", (h("code-b"),))
    out = rpc(cur, "remember_consume", h("code-b"))
    check("expired is refused", out.get("ok") is False and out.get("reason") == "expired", str(out))
    cur.execute("select count(*) from public.remember_tokens where code_hash=%s", (h("code-b"),))
    check("...and the row is gone, not left to retry", cur.fetchone()[0] == 0)


def test_rotation_does_not_mint_a_new_credential(cur):
    """One code per device, not one per page load.

    GoTrue rotates the refresh token on every refresh, so the stored row goes
    stale and must be updated. Doing that by ISSUING A NEW CODE leaves the
    previous one valid for its full thirty days -- and rotation runs at the top
    of each spend page and again on every per-row Deep Analyze, so a busy
    session left a dozen live credentials behind, of which the browser held one.
    Every earlier one is a working sign-in for whoever captured it.
    """
    print("\nrotation updates the row; it does not mint another credential")
    uid = user(cur)
    rpc(cur, "remember_issue", h("dev-1"), uid, "rt_v1", "30 days", None)

    for i, tok in enumerate(("rt_v2", "rt_v3", "rt_v4"), start=2):
        ok = rpc(cur, "remember_rotate", h("dev-1"), tok, "30 days")
        check(f"rotation {i} updates in place", ok is True, str(ok))

    cur.execute("select count(*) from public.remember_tokens where user_id=%s", (uid,))
    check("three rotations leave ONE row", cur.fetchone()[0] == 1,
          "each rotation minted a new thirty-day credential")

    out = rpc(cur, "remember_consume", h("dev-1"))
    check("the browser's original code still works", out.get("ok") is True, str(out))
    check("...and yields the LATEST token", out.get("refresh_token") == "rt_v4", str(out))

    check("rotating a consumed code fails, so the caller mints a fresh one",
          rpc(cur, "remember_rotate", h("dev-1"), "rt_v5", "30 days") is False)

    # A second device gets its own row and is untouched by the first's rotation.
    rpc(cur, "remember_issue", h("dev-A"), uid, "rt_a", "30 days", None)
    rpc(cur, "remember_issue", h("dev-B"), uid, "rt_b", "30 days", None)
    rpc(cur, "remember_rotate", h("dev-A"), "rt_a2", "30 days")
    check("rotating one device does not disturb another",
          rpc(cur, "remember_consume", h("dev-B")).get("refresh_token") == "rt_b")

    # An expired row is not silently revived by a rotation.
    rpc(cur, "remember_issue", h("dev-old"), uid, "rt_o", "30 days", None)
    cur.execute("update public.remember_tokens set expires_at = now() - interval '1 day'"
                " where code_hash = %s", (h("dev-old"),))
    check("an expired code cannot be rotated back to life",
          rpc(cur, "remember_rotate", h("dev-old"), "rt_o2", "30 days") is False)


def test_sign_out_revokes_every_code(cur):
    print("\nsigning out must invalidate codes, not just forget them locally")
    uid = user(cur)
    for c in ("c1", "c2", "c3"):
        rpc(cur, "remember_issue", h(c), uid, "rt_y", "30 days", None)
    other = user(cur)
    rpc(cur, "remember_issue", h("other"), other, "rt_z", "30 days", None)

    n = rpc(cur, "remember_revoke_all", uid)
    check("all three revoked", n == 3, str(n))
    check("a revoked code no longer works",
          rpc(cur, "remember_consume", h("c1")).get("ok") is False)
    check("another user's code is untouched",
          rpc(cur, "remember_consume", h("other")).get("ok") is True)


def test_the_table_is_unreadable_by_a_user_jwt(cur):
    """It holds refresh tokens. RLS is enabled with NO policies, which denies
    everything to anon and authenticated while service_role bypasses."""
    print("\nthe token store is service_role-only")
    uid = user(cur)
    rpc(cur, "remember_issue", h("secret"), uid, "rt_private", "30 days", None)

    # request.jwt.claim.sub, not request.jwt.claims -- see the stub. The wrong
    # GUC leaves auth.uid() NULL and makes this pass regardless.
    cur.execute("set role authenticated")
    cur.execute("select set_config('request.jwt.claim.sub', %s, false)", (uid,))
    cur.execute("select auth.uid()")
    check("the test can actually impersonate the user",
          str(cur.fetchone()[0]) == uid, "impersonation failed; the check below is vacuous")
    try:
        cur.execute("select count(*) from public.remember_tokens")
        seen = cur.fetchone()[0]
    except psycopg2.Error:
        seen = 0
    finally:
        cur.execute("reset role")
        cur.execute("select set_config('request.jwt.claim.sub','',false)")
    check("a signed-in user sees zero rows -- including their own",
          seen == 0, f"{seen} row(s) visible")

    for fn, sig in (("remember_issue", "public.remember_issue(text,uuid,text,interval,text)"),
                    ("remember_consume", "public.remember_consume(text)"),
                    ("remember_revoke_all", "public.remember_revoke_all(uuid)")):
        cur.execute("select has_function_privilege('authenticated',%s,'EXECUTE')", (sig,))
        check(f"authenticated cannot execute {fn}", cur.fetchone()[0] is False)


# ───────────────────────────────────────────────── the transport, in source

def test_no_refresh_token_reaches_the_url_or_localstorage():
    print("\nthe browser never holds a Supabase refresh token again")
    auth_py = (REPO / "utils" / "auth.py").read_text()
    page = (REPO / "pages" / "Auth.py").read_text()

    check("the page reads ?rc=, not ?rt=",
          'query_params.get("rc"' in page and 'query_params.get("rt"' not in page,
          "the URL parameter still carries a refresh token")
    check("the JS pushes the code, not the token",
          "searchParams.set('rc'" in page and "searchParams.set('rt'" not in page,
          str([l for l in page.split("\n") if "searchParams.set" in l]))
    check("localStorage holds ss_remember_code",
          "ss_remember_code" in auth_py and "ss_remember_code" in page)
    check("nothing WRITES the old ss_refresh_token key",
          "setItem('ss_refresh_token'" not in auth_py,
          "an old key would feed a real refresh token into the new path")

    # THE VALUE, not the key name. Renaming the localStorage key while still
    # passing it a refresh token made the key-name assertion above pass on code
    # that put the token straight back in the URL. Check every call site of the
    # writer and demand the argument be a code.
    import ast as _ast
    _tree = _ast.parse(auth_py)
    _bad = []
    for _n in _ast.walk(_tree):
        if not (isinstance(_n, _ast.Call) and isinstance(_n.func, _ast.Name)
                and _n.func.id == "_save_token_to_browser"):
            continue
        arg = _n.args[0] if _n.args else None
        name = arg.id if isinstance(arg, _ast.Name) else _ast.dump(arg)
        if "code" not in name.lower():
            _bad.append((_n.lineno, name))
    check("every localStorage write is handed a CODE, not a token",
          not _bad, f"suspicious arguments: {_bad}")

    # And the sign-in caching path must not write to the browser at all.
    _cache = auth_py.split("def _cache_auth_to_browser")[1].split("\ndef ")[0]
    check("_cache_auth_to_browser writes nothing to localStorage",
          "_save_token_to_browser" not in _cache,
          "it used to persist session['refresh_token'] straight to the browser")
    check("...but the old key is still cleaned up",
          "removeItem('ss_refresh_token')" in auth_py,
          "browsers that already stored a token must have it removed")
    check("sign_out revokes server-side",
          "revoke_remember_codes" in auth_py.split("def sign_out")[1].split("def ")[0],
          "clearing localStorage leaves the row valid for 30 days")
    check("a session refresh rotates IN PLACE before minting",
          auth_py.index("rotate_remember_token") <
          auth_py.split("def refresh_session_if_needed")[1].find("issue_remember_code")
          + auth_py.index("def refresh_session_if_needed"),
          "minting on every refresh accumulates live thirty-day credentials")
    check("a restored session rotates its code",
          "issue_remember_code" in auth_py.split("def restore_session_from_refresh_token")[1].split("\ndef ")[0],
          "without rotation 'remember me' works exactly once")


def main() -> int:
    print("=" * 74)
    print("  remember me: an opaque single-use code, never a refresh token")
    print("=" * 74)
    test_no_refresh_token_reaches_the_url_or_localstorage()
    c = psycopg2.connect(DSN); c.autocommit = True
    with c.cursor() as cur:
        rebuild(cur)
        for t in (test_a_code_works_exactly_once,
                  test_rotation_does_not_mint_a_new_credential,
                  test_an_expired_code_is_refused_and_consumed,
                  test_sign_out_revokes_every_code,
                  test_the_table_is_unreadable_by_a_user_jwt):
            try:
                t(cur)
            except Exception as e:
                FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
                print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")
    c.close()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
