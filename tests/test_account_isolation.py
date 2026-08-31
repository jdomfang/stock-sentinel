#!/usr/bin/env python3
"""Account A's session-local product state must never be visible to Account B.

No network and no database. Streamlit and Supabase are stubbed so these tests
exercise the identity-boundary behavior directly.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

st = types.ModuleType("streamlit")
st.session_state = {}
sys.modules["streamlit"] = st


class _AuthClient:
    def __init__(self) -> None:
        self.sign_out_calls = 0

    def sign_out(self) -> None:
        self.sign_out_calls += 1


_auth_client = _AuthClient()
_client = types.SimpleNamespace(auth=_auth_client)
supabase_client = types.ModuleType("utils.supabase_client")
supabase_client.get_client = lambda: _client
sys.modules["utils.supabase_client"] = supabase_client

import utils.auth as auth  # noqa: E402


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED.append(name) if condition else FAILED.append((name, detail)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}"
          f"{'' if condition else '  <- ' + detail}")


def _seed_private_state(owner: str = "account-a") -> None:
    st.session_state.clear()
    st.session_state.update({
        auth.USER_KEY: {"id": owner, "email": f"{owner}@test.invalid"},
        auth.SESSION_KEY: {"access_token": f"token-{owner}"},
        auth.PRODUCT_STATE_OWNER_KEY: owner,
        "df_valid": [f"scan-for-{owner}"],
        "deep_analysis_card": {"ticker": "PRIVATE"},
        "deep_analysis_results": {"evidence": ["PRIVATE"]},
        "_pending_discovery_analysis": {"ticker": "PRIVATE"},
        "_autostart_discovery_scan": True,
        "billing.return": "success",
        "billing.url": {"uid": owner, "url": "https://checkout.invalid"},
        "auth_email": f"{owner}@test.invalid",
        "auth_password": "must-not-survive-logout",
        # This represents public/shared application state and is deliberately
        # outside the account-scoped allowlist.
        "public_demo_cache": {"ticker": "SHARED"},
    })


def test_same_user_refresh_preserves_work() -> None:
    _seed_private_state()
    ok = auth.ensure_user_scoped_state_owner()
    check("same-user guard accepts the owner", ok)
    check("same-user refresh preserves the scan",
          st.session_state.get("df_valid") == ["scan-for-account-a"])
    check("same-user refresh preserves delivered analysis",
          st.session_state.get("deep_analysis_card", {}).get("ticker") == "PRIVATE")


def test_account_change_clears_every_private_surface() -> None:
    _seed_private_state()
    st.session_state[auth.USER_KEY] = {"id": "account-b"}
    ok = auth.ensure_user_scoped_state_owner()
    check("new identity is bound", ok)
    check("owner changes to Account B",
          st.session_state.get(auth.PRODUCT_STATE_OWNER_KEY) == "account-b")
    leaked = sorted(
        key for key in auth.USER_SCOPED_SESSION_KEYS if key in st.session_state
    )
    check("Account A scan, analysis, intents, and billing state are cleared",
          not leaked, str(leaked))
    check("shared public demo state is not cleared",
          st.session_state.get("public_demo_cache") == {"ticker": "SHARED"})


def test_unowned_legacy_state_is_not_adopted() -> None:
    _seed_private_state()
    st.session_state.pop(auth.PRODUCT_STATE_OWNER_KEY)
    ok = auth.ensure_user_scoped_state_owner()
    check("legacy state is rebound to the signed-in user", ok)
    check("unowned legacy scan is cleared rather than adopted",
          "df_valid" not in st.session_state)
    check("unowned legacy pending action cannot replay",
          "_pending_discovery_analysis" not in st.session_state)


def test_direct_identity_replacement_clears_credentials_and_work() -> None:
    _seed_private_state()
    st.session_state.update({
        auth.CACHE_SESSION_KEY: "old-session",
        auth.CACHE_USER_KEY: "old-user",
        auth.REMEMBER_ME_KEY: True,
        "_pending_rt_save": "old-code",
        "_remember_hash": "old-hash",
        # These are the currently-instantiated Account B form widgets. The
        # identity boundary must not mutate them during their submit callback.
        "auth_email": "account-b@test.invalid",
        "auth_password": "account-b-password",
    })
    auth._establish_authenticated_state(
        {"access_token": "token-b"}, {"id": "account-b"}
    )
    check("direct A-to-B sign-in installs Account B",
          auth.user_id() == "account-b")
    check("direct A-to-B sign-in clears Account A work",
          "deep_analysis_card" not in st.session_state)
    stale_auth = sorted(
        key for key in (
            auth.CACHE_SESSION_KEY,
            auth.CACHE_USER_KEY,
            auth.REMEMBER_ME_KEY,
            "_pending_rt_save",
            "_remember_hash",
        ) if key in st.session_state
    )
    check("direct A-to-B sign-in clears Account A remember artifacts",
          not stale_auth, str(stale_auth))
    check("direct sign-in does not mutate instantiated form widgets",
          st.session_state.get("auth_email") == "account-b@test.invalid"
          and st.session_state.get("auth_password") == "account-b-password")


def test_logout_clears_private_state() -> None:
    _seed_private_state()
    revoked: list[str] = []
    original_revoke = auth.revoke_remember_codes
    original_clear_browser = auth._clear_browser_cache
    auth.revoke_remember_codes = lambda uid: revoked.append(uid)
    auth._clear_browser_cache = lambda: None
    try:
        auth.sign_out()
    finally:
        auth.revoke_remember_codes = original_revoke
        auth._clear_browser_cache = original_clear_browser
    check("logout revokes Account A remember codes", revoked == ["account-a"], str(revoked))
    check("logout removes the authenticated identity",
          auth.USER_KEY not in st.session_state and auth.SESSION_KEY not in st.session_state)
    check("logout removes the private owner binding",
          auth.PRODUCT_STATE_OWNER_KEY not in st.session_state)
    check("logout removes retained sign-in form credentials",
          "auth_email" not in st.session_state
          and "auth_password" not in st.session_state)
    leaked = sorted(
        key for key in auth.USER_SCOPED_SESSION_KEYS if key in st.session_state
    )
    check("logout removes all private product state", not leaked, str(leaked))


def test_protected_and_admin_boundaries_are_wired() -> None:
    guard = (REPO / "utils" / "guard.py").read_text()
    admin = (REPO / "pages" / "Admin.py").read_text()
    discovery = (REPO / "pages" / "Discovery.py").read_text()
    deep = (REPO / "pages" / "Deep_Analysis.py").read_text()
    check("every protected page enforces ownership through require_login",
          "ensure_user_scoped_state_owner()" in guard)
    check("Admin enforces ownership before reading demo source",
          admin.index("ensure_user_scoped_state_owner()") < admin.index(
              'session_scan = st.session_state.get("df_valid")'
          ))
    check("Admin blocks publication unless the source owner matches",
          "user_scoped_state_belongs_to(user)" in admin
          and "or not session_owner_ok" in admin)
    check("Market Scan stamps ownership before storing a scan",
          discovery.index("ensure_user_scoped_state_owner()",
                          discovery.index("df_valid = pd.DataFrame"))
          < discovery.index("st.session_state.df_valid = df_valid"))
    check("Deep Analyze stamps ownership before storing a result",
          deep.index("ensure_user_scoped_state_owner()",
                     deep.index("if not _card:"))
          < deep.index("st.session_state.deep_analysis_card = _card"))


def main() -> int:
    print("=" * 72)
    print("  Account-isolated Streamlit product state")
    print("=" * 72)
    test_same_user_refresh_preserves_work()
    test_account_change_clears_every_private_surface()
    test_unowned_legacy_state_is_not_adopted()
    test_direct_identity_replacement_clears_credentials_and_work()
    test_logout_clears_private_state()
    test_protected_and_admin_boundaries_are_wired()
    print("\n" + "=" * 72)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name}: {detail}")
    print("=" * 72)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
