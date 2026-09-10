"""Offline doubles shared by real-page AppTest and browser feedback checks."""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch


def pulse_rows():
    from utils.ui import PULSE_SECTORS
    return [dict(sector=s, trade_date="2026-09-08", state="accumulating" if s == "utilities" else "quiet", breadth=.1 if s == "communication" else .2,
                 acc_days_5d=1, top_contrib=[], n_eligible=100, ud_ratio_5d=None)
            for s in PULSE_SECTORS]


@contextmanager
def offline_discovery(case, observe=lambda phase: None, *, native_links=False):
    import streamlit as st
    from utils import auth, guard, credits, analyze_client, finance, ui

    events = case.setdefault("events", [])
    def consume(feature, metadata):
        observe("debit")
        events.append(("debit", metadata["sector"]))
        return SimpleNamespace(ok=case.get("mode") != "refused", event_id="offline-event",
                               reason="insufficient_credits")
    def remote(sector, **kwargs):
        observe("remote")
        events.append(("remote", sector))
        if case.get("mode") == "exception":
            raise RuntimeError("offline failure")
        if case.get("mode") in {"failure", "refund_failure"}:
            return analyze_client.RemoteScan(error="offline failure", kind="transport")
        if case.get("mode") == "empty":
            return analyze_client.RemoteScan(ok=True)
        return analyze_client.RemoteScan(ok=True, posts_seen=4, rows=[{
            "Ticker":"DUK", "Company Name":"Duke Energy", "Overall Sentiment":"Neutral",
            "Mentions":4, "Evidence":4,
        }])
    def refund(*args):
        observe("refund")
        events.append(("refund",))
        return case.get("mode") != "refund_failure"
    def complete(*args):
        observe("complete")
        events.append(("complete", args[1]))
    def prohibit(*args, **kwargs):
        raise AssertionError("Network is prohibited in offline feedback tests")

    with ExitStack() as stack:
        for module, name, replacement in (
            (guard, "require_login", lambda **k:auth.ensure_user_scoped_state_owner()),
            (guard, "require_active_account", lambda **k:dict(credits=case.get("credits",2))),
            (auth, "is_logged_in", lambda:True),
            (auth, "flush_pending_rt_save", lambda:None),
            (auth, "refresh_session_if_needed", lambda:True),
            (credits, "consume_credit", consume), (credits, "refund_credit", refund),
            (credits, "complete_work", complete),
            (analyze_client, "configured", lambda:case.get("mode") != "unconfigured"),
            (analyze_client, "scan_remote", remote),
            (analyze_client, "analyze_remote", prohibit),
            (finance, "get_last_close_prices_best_effort", lambda *a,**k:{"DUK":100}),
            (ui, "load_sector_pulse", lambda:ui.pulse_snapshot([] if case.get("missing") else pulse_rows())),
        ):
            stack.enter_context(patch.object(module,name,replacement))
        if not native_links:
            stack.enter_context(patch.object(st,"page_link",lambda *a,**k:None))
        # Fail closed if a later page edit accidentally reaches an HTTP client.
        stack.enter_context(patch("requests.sessions.Session.request",prohibit))
        stack.enter_context(patch("httpx.Client.send",prohibit))
        stack.enter_context(patch("urllib.request.urlopen",prohibit))
        yield
