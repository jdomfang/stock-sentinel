#!/usr/bin/env python3
"""The cutover's failure surface: when may the portal re-run an analysis?

WHY THIS EXISTS

utils/analyze_client.py is the only place in the codebase where "the request
failed" and "the request failed WITHOUT spending money" are different answers,
and getting it wrong is expensive in a way no test failure would reveal:

  signal_log has `unique (event_id, ticker, feature)` -- a duplicate write is
  rejected by the database.
  verdict_log has NO such constraint -- a duplicate lands as a second row that
  nothing downstream can distinguish from a genuine second analysis.

So a fallback on the wrong error class buys a second X corpus AND silently
corrupts the table the whole product is being measured by. A read timeout is
the case that matters: a Deep Analyze runs 40-60s, so a timeout is both the
most likely failure and the one where the service most probably finished.

No network. urlopen is stubbed.

Usage:
    python3 tests/test_analyze_client.py
"""

from __future__ import annotations

import io
import json
import socket
import sys
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import utils.analyze_client as C  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name, cond, detail=""):
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


class _Resp(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def install(*, cfg=None, response=None, raises=None, capture=None):
    """Point the client at a stub, and record what it would have sent."""
    import utils.config as CFG
    defaults = {"CORE_API_URL": "https://core.test",
                "CORE_API_SHARED_SECRET": "s3cret"}
    values = {**defaults, **(cfg or {})}
    CFG.get = lambda name, default=None: values.get(name, default)

    def _open(req, timeout=None):
        if capture is not None:
            capture["url"] = req.full_url
            capture["headers"] = dict(req.headers)
            capture["body"] = json.loads(req.data or b"{}")
            capture["timeout"] = timeout
        if raises is not None:
            raise raises
        return _Resp(response or {})

    C.urllib.request.urlopen = _open


def _http_error(code, body=b"", refused=None):
    hdrs = {"X-Core-Refused": refused} if refused else {}
    return urllib.error.HTTPError("https://core.test/analyze", code, "err",
                                  hdrs, io.BytesIO(body))


# ------------------------------------------------- the rule that costs money

def test_only_pre_spend_failures_are_retryable():
    print("\nthe portal may only re-run when the service provably did not spend")
    cases = [
        ("connection refused", urllib.error.URLError(ConnectionRefusedError(111)), True),
        ("DNS failure",        urllib.error.URLError(socket.gaierror(-2)), True),
        ("401 unauthorised",   _http_error(401), True),
        ("403 forbidden",      _http_error(403), True),
        ("422 rejected input", _http_error(422), True),
        # The service STAMPS a header on everything it refuses before working.
        ("503 missing config",  _http_error(503, b'{"detail":"missing"}', "config"), True),
        ("503 secret unset",    _http_error(503, b'{"detail":"CORE_API_SHARED_SECRET is not configured"}', "config"), True),
        ("429 at capacity",     _http_error(429, refused="capacity"), True),
        ("bare 429",            _http_error(429), True),
        # The expensive ones. Each may have completed, bought a corpus and
        # written a verdict_log row that has no unique constraint to stop a
        # second one landing beside it.
        ("read timeout",       urllib.error.URLError(socket.timeout()), False),
        ("bare TimeoutError",  TimeoutError(), False),
        ("500 from handler",   _http_error(500), False),
        ("502 bad gateway",    _http_error(502), False),
        # No header: this came from the platform, not from the service's own
        # pre-spend refusal, so it may have interrupted a paid analysis.
        ("503 from the platform", _http_error(503, b"<html>upstream</html>"), False),
        # The old body-substring test would have re-run this one locally.
        ("500 whose body says 'missing'",
         _http_error(500, b'{"detail":"missing price data"}'), False),
    ]
    for label, exc, retryable in cases:
        install(raises=exc)
        r = C.analyze_remote("TSLA")
        check(f"{label}: retryable={retryable}", r.retryable is retryable,
              f"got retryable={r.retryable} error={r.error}")
        check(f"{label}: not ok", r.ok is False)


def test_a_real_no_evidence_answer_is_never_retried():
    print("\n'no usable evidence' is the ANSWER -- the money is already gone")
    install(response={"ok": False, "error": "no usable evidence",
                      "posts_billed": 214, "elapsed_s": 41.2})
    r = C.analyze_remote("ZZZZ")
    check("not ok", r.ok is False)
    check("the reason is carried", r.error == "no usable evidence", str(r.error))
    check("NOT retryable -- a re-run buys the corpus again", r.retryable is False)


# --------------------------------------------------------------- the request

def test_the_request_says_who_is_asking():
    print("\nthe caller owns the cohort labels, not the service")
    cap: dict = {}
    install(capture=cap, response={"ok": True, "card": {"verdict": "Buy"},
                                   "analysis_results": {"a": {}}})
    r = C.analyze_remote("TSLA", "technology", feature="deep_analyze",
                         event_id="ev-1")
    check("posts to /analyze", cap["url"].endswith("/analyze"), cap["url"])
    check("sends the shared secret",
          cap["headers"].get("X-core-secret") == "s3cret", str(cap["headers"]))
    check("carries the feature", cap["body"]["feature"] == "deep_analyze")
    check("carries the credit event", cap["body"]["event_id"] == "ev-1")
    check("asks the service to persist", cap["body"]["persist"] is True)
    check("a Deep Analyze is given time to finish",
          cap["timeout"] >= 120, str(cap["timeout"]))
    check("the card comes back", r.card.get("verdict") == "Buy")
    check("and the breakdown the expander needs",
          r.analysis_results == {"a": {}}, str(r.analysis_results))
    check("ok", r.ok is True)


def test_a_degraded_answer_is_still_an_answer():
    print("\na legacy fallback is delivered and billed, and says so")
    install(response={"ok": True, "card": {"verdict": "Watch",
                                           "adjudicator": "legacy"},
                      "analysis_results": {"a": {}}, "degraded": True})
    r = C.analyze_remote("TSLA")
    check("ok", r.ok is True)
    check("flagged degraded", r.degraded is True)


# ------------------------------------------------------------------ the wire

def test_plaintext_is_refused():
    print("\nthe secret is a request HEADER; http:// puts it on the wire")
    install(cfg={"CORE_API_URL": "http://core.test"})
    r = C.analyze_remote("TSLA")
    check("http:// is refused", r.ok is False and "https" in (r.error or ""),
          str(r.error))
    # There is no local path left to fall back to, and a typo persists until
    # someone edits config -- so this must NOT tell the user to try again.
    check("...and it does not invite a pointless retry", r.retryable is False)
    # configured() has to reject it too, or the caller charges a credit and
    # only then discovers it cannot call anything.
    check("...and configured() refuses it before a credit is taken",
          C.configured() is False)

    install(cfg={"CORE_API_URL": "core.test"})
    check("a scheme-less URL is refused too",
          C.analyze_remote("TSLA").ok is False)


def test_configured_needs_both_halves():
    print("\nhalf-configured is not configured")
    install()
    check("url + secret -> on", C.configured() is True)
    install(cfg={"CORE_API_SHARED_SECRET": ""})
    check("url without secret -> off", C.configured() is False)
    install(cfg={"CORE_API_URL": ""})
    check("secret without url -> off", C.configured() is False)


def test_labels_that_reach_the_database_are_constrained():
    print("\nfeature and route land in DB columns, so they are not free text")
    install(response={"ok": True, "card": {}, "analysis_results": {}})
    for bad in ("scan", "'; drop table", "", "DEEP_ANALYZE"):
        try:
            C.analyze_remote("TSLA", feature=bad)
            check(f"feature {bad!r} rejected", False, "accepted")
        except ValueError:
            check(f"feature {bad!r} rejected", True)
    try:
        C.analyze_remote("TSLA", route="portal")
        check("an unknown route is rejected", False, "accepted")
    except ValueError:
        check("an unknown route is rejected", True)
    # The two that must work.
    check("deep_analyze is accepted",
          C.analyze_remote("TSLA", feature="deep_analyze").ok is True)
    check("discovery+route is accepted",
          C.analyze_remote("TSLA", feature="discovery", route="discovery").ok is True)


def test_a_malformed_body_does_not_raise_into_the_page():
    print("\nthe page is mid-render; nothing here may raise")
    class _Bad(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    import utils.config as CFG
    CFG.get = lambda n, d=None: {"CORE_API_URL": "https://core.test",
                                 "CORE_API_SHARED_SECRET": "s"}.get(n, d)
    C.urllib.request.urlopen = lambda req, timeout=None: _Bad(b"<html>502</html>")
    r = C.analyze_remote("TSLA")
    check("returns a result rather than raising", r.ok is False, str(r))
    check("and does not invite a re-run", r.retryable is False, str(r.retryable))


# ------------------------------------------------------------------ the scan

def test_scan_is_given_longer_than_an_analysis():
    print("\na cold scan paginates 14 times; an analysis paginates 3")
    cap: dict = {}
    install(capture=cap, response={"ok": True, "rows": [], "sector": "healthcare"})
    C.scan_remote("healthcare", event_id="ev-1")
    check("posts to /scan", cap["url"].endswith("/scan"), cap["url"])
    check("sends the shared secret",
          cap["headers"].get("X-core-secret") == "s3cret", str(cap["headers"]))
    check("carries the credit event", cap["body"]["event_id"] == "ev-1")
    check("asks the service to persist", cap["body"]["persist"] is True)
    # A client timeout does NOT cancel the server. Cutting a scan short just
    # guarantees nobody sees the 300 posts it bought.
    check("the scan budget exceeds the analyze budget",
          cap["timeout"] > C.DEFAULT_TIMEOUT_S,
          f"{cap['timeout']} vs {C.DEFAULT_TIMEOUT_S}")


def test_a_scan_reports_what_it_cost():
    print("\nwhether money moved is what a refund decision turns on")
    install(response={"ok": True, "sector": "healthcare",
                      "rows": [{"Ticker": "VOR", "Mentions": 3}],
                      "posts_seen": 349, "from_cache": True,
                      "corpus_age_s": 1234.0, "stop_reason": "validated_target"})
    r = C.scan_remote("healthcare")
    check("ok", r.ok is True)
    check("the rows come back", [x["Ticker"] for x in r.rows] == ["VOR"], str(r.rows))
    check("it says what it cost", r.posts_seen == 349, str(r.posts_seen))
    check("...and that this one was free", r.from_cache is True)
    check("...and how stale it is", r.corpus_age_s == 1234.0)


def test_a_partial_scan_is_still_a_scan():
    print("\nX failing mid-pagination keeps what was already bought")
    install(response={"ok": True, "sector": "healthcare", "rows": [{"Ticker": "A"}],
                      "posts_seen": 75, "x_error": "X API request failed"})
    r = C.scan_remote("healthcare")
    check("ok, with rows", r.ok is True and bool(r.rows))
    check("the partial failure is carried so the page can warn",
          r.x_error == "X API request failed", str(r.x_error))


def test_a_failed_scan_keeps_its_kind():
    print("\nthe page keeps a distinct panel per failure; the kind must survive")
    for kind in ("credentials", "network", "ticker_db", "no_query", "pipeline"):
        install(response={"ok": False, "sector": "healthcare",
                          "error": "boom", "kind": kind, "posts_billed": 0})
        r = C.scan_remote("healthcare")
        check(f"{kind} survives the wire", r.ok is False and r.kind == kind,
              f"{r.ok}/{r.kind}")


def test_a_scan_timeout_is_never_retryable():
    print("\na timed-out scan may have bought 300 posts; do not buy them twice")
    import socket
    for label, exc, retry in (
            ("read timeout", urllib.error.URLError(socket.timeout()), False),
            ("bare TimeoutError", TimeoutError(), False),
            ("500", _http_error(500), False),
            ("connection refused", urllib.error.URLError(ConnectionRefusedError(111)), True),
            ("429 sector busy", _http_error(429, refused="sector-busy"), True),
            ("401", _http_error(401), True)):
        install(raises=exc)
        r = C.scan_remote("healthcare")
        check(f"{label}: retryable={retry}", r.retryable is retry,
              f"got {r.retryable} ({r.error})")


def test_scan_never_raises_into_the_page():
    print("\nthe page is mid-render; nothing here may raise")
    class _Bad(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    import utils.config as CFG
    CFG.get = lambda n, d=None: {"CORE_API_URL": "https://core.test",
                                 "CORE_API_SHARED_SECRET": "s"}.get(n, d)
    C.urllib.request.urlopen = lambda req, timeout=None: _Bad(b"<html>502</html>")
    r = C.scan_remote("healthcare")
    check("returns instead of raising", r.ok is False, str(r))
    install(cfg={"CORE_API_URL": "http://core.test"})
    check("plaintext is refused for the scan too",
          C.scan_remote("healthcare").ok is False)


def main() -> int:
    print("=" * 74)
    print("  analyze_client: when re-running is safe, and when it costs money")
    print("=" * 74)
    for t in (test_only_pre_spend_failures_are_retryable,
              test_a_real_no_evidence_answer_is_never_retried,
              test_the_request_says_who_is_asking,
              test_a_degraded_answer_is_still_an_answer,
              test_plaintext_is_refused,
              test_configured_needs_both_halves,
              test_labels_that_reach_the_database_are_constrained,
              test_a_malformed_body_does_not_raise_into_the_page,
              test_scan_is_given_longer_than_an_analysis,
              test_a_scan_reports_what_it_cost,
              test_a_partial_scan_is_still_a_scan,
              test_a_failed_scan_keeps_its_kind,
              test_a_scan_timeout_is_never_retryable,
              test_scan_never_raises_into_the_page):
        t()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
