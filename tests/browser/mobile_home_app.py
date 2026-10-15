"""Render the real Home page offline for mobile/desktop browser reviews.

The runner supplies registered placeholder pages for native ``st.page_link``
targets. MOBILE_REVIEW_CONTROL is a directory containing case.json, or the
case JSON file itself. Supported case key: signed_in (default False).
MOBILE_REVIEW_REPO points to the checkout when this fixture is copied elsewhere.
No account, publication, pulse, or HTTP service is accessed.
"""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

repo = Path(os.environ.get("MOBILE_REVIEW_REPO", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / "tests"))
control = Path(os.environ["MOBILE_REVIEW_CONTROL"])
case_path = control / "case.json" if control.is_dir() else control
case = json.loads(case_path.read_text()) if case_path.exists() else {}
signed_in = bool(case.get("signed_in", False))


def prohibit(*args, **kwargs):
    # Persist attempts too: production fallback handlers may catch exceptions.
    # Do not log arguments, which could contain credentials from the environment.
    with (case_path.parent / "network-attempts.log").open("a") as log:
        log.write("Blocked external service access in offline Home fixture\n")
    raise AssertionError("External services are prohibited in offline Home tests")


with ExitStack() as stack:
    for target in (
        "requests.sessions.Session.request",
        "httpx.Client.send",
        "httpx.AsyncClient.send",
        "urllib.request.urlopen",
    ):
        stack.enter_context(patch(target, prohibit))

    import streamlit as st
    from utils import auth, demo_repository, supabase_client, ui
    from sector_pulse_feedback_harness import pulse_rows

    user = {"id": "offline-browser", "email": "fixture@example.invalid"}
    stack.enter_context(patch.dict(os.environ, {"ADMIN_EMAIL": user["email"] if case.get("admin") else ""}))
    if signed_in:
        st.session_state[auth.USER_KEY] = user
        st.session_state[auth.PRODUCT_STATE_OWNER_KEY] = user["id"]
    else:
        st.session_state.pop(auth.USER_KEY, None)
        st.session_state.pop(auth.PRODUCT_STATE_OWNER_KEY, None)

    for module, name, replacement in (
        (auth, "is_logged_in", lambda: signed_in),
        (auth, "get_user", lambda: user if signed_in else None),
        (auth, "flush_pending_rt_save", lambda: None),
        (auth, "refresh_session_if_needed", lambda: signed_in),
        (supabase_client, "get_client", prohibit),
        (demo_repository, "get_client", prohibit),
        # Exercise Home's real checked-in demo fallback without database access.
        (demo_repository, "load_latest_public_demo", lambda: None),
        (ui, "load_sector_pulse", lambda: ui.pulse_snapshot(pulse_rows())),
    ):
        stack.enter_context(patch.object(module, name, replacement))

    page = repo / "pages" / "Home.py"
    exec(compile(page.read_text(), str(page), "exec"),
         {"__file__": str(page), "__name__": "__main__"})
