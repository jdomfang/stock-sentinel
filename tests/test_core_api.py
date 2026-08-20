#!/usr/bin/env python3
"""Pin core-api's contract. No network, no X spend, no model.

WHY THESE PARTICULAR THINGS

This service can spend real money: an analysis buys X posts and X bills per post
RETURNED, not per request. So the tests that matter most are not about happy
paths, they are about the ways a deployment can quietly become expensive or
quietly become useless:

  auth        an unprotected /analyze is a spending endpoint, not just a leak,
              so it must fail CLOSED when no secret is configured
  inference   without INFERENCE_URL the image has no scorer -- and its failure
              mode is "no usable evidence", which reads exactly like a quiet
              market rather than a broken deploy
  the card    every string the user reads is built in one place now; a renderer
              that invents a label reintroduces the bug this design removes
  persistence a feature name the database CHECK rejects loses the row silently

Everything here stubs utils.analyze, so running this suite costs nothing.

Usage:
    python3 tests/test_core_api.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def client(secret: str = "s3cret", inference: str = "https://inf.test", **cfg):
    import importlib
    os.environ["CORE_API_SHARED_SECRET"] = secret
    os.environ["INFERENCE_URL"] = inference
    os.environ["INFERENCE_SHARED_SECRET"] = "x"
    # Everything /analyze must have before it is allowed to spend.
    for k, v in {"X_BEARER_TOKEN": "t", "POLYGON_API_KEY": "p",
                 "SUPABASE_URL": "https://db.test",
                 "SUPABASE_SERVICE_ROLE_KEY": "k"}.items():
        os.environ[k] = cfg.get(k, v)
    import core_api.main as M
    M = importlib.reload(M)
    from fastapi.testclient import TestClient
    return TestClient(M.app), M


def test_it_fails_closed_without_a_secret():
    print("\nauth: an open /analyze is a spending endpoint")
    c, _ = client(secret="")
    r = c.post("/analyze", json={"ticker": "TSLA"})
    check("no secret configured -> 503, never runs", r.status_code == 503,
          str(r.status_code))
    check("...and /ready returns 503, which is what Railway reads",
          c.get("/ready").status_code == 503)

    c, _ = client()
    check("missing header -> 401",
          c.post("/analyze", json={"ticker": "TSLA"}).status_code == 401)
    check("wrong secret -> 401",
          c.post("/analyze", json={"ticker": "TSLA"},
                 headers={"X-Core-Secret": "nope"}).status_code == 401)


def test_it_refuses_to_spend_when_it_cannot_answer():
    """The failure that costs money and looks like a quiet market.

    Without INFERENCE_URL the image has no scorer, so retrieval buys up to 400
    billed posts, scoring raises, and the caller gets a 200 saying there was no
    evidence. Retrieval raises BEFORE corpus_cache.put, so the corpus is not
    even kept -- every retry re-buys it.
    """
    print("\nmoney: never buy inputs for an answer that cannot be produced")
    # Blanked at utils.config, not via os.environ: config falls back to
    # st.secrets, and a developer box has a secrets.toml that would quietly
    # refill every value the test is trying to remove.
    import utils.config as CFG
    real_get = CFG.get
    for missing in ("INFERENCE_URL", "X_BEARER_TOKEN", "POLYGON_API_KEY",
                    "SUPABASE_URL"):
        c, _ = client()
        CFG.get = lambda n, d="", _m=missing: ("" if n == _m else real_get(n, d))
        try:
            r = c.post("/analyze", json={"ticker": "TSLA"},
                       headers={"X-Core-Secret": "s3cret"})
            check(f"no {missing} -> 503 before any spend", r.status_code == 503,
                  str(r.status_code))
            check(f"...and /ready fails so the platform sees it",
                  c.get("/ready").status_code == 503)
        finally:
            CFG.get = real_get


def test_the_ticker_cannot_rewrite_a_billed_query():
    print("\nthe ticker is interpolated into a query that costs money")
    c, _ = client()
    for bad in ('A OR B', 'A"x', 'A)b', '', 'ABCDEFG', '$TSLA', ' TSLA'):
        r = c.post("/analyze", json={"ticker": bad},
                   headers={"X-Core-Secret": "s3cret"})
        check(f"{bad!r} rejected", r.status_code == 422, str(r.status_code))
    # And a real one still validates.
    import utils.analyze as UA
    from utils.analyze import Analysis
    real = UA.analyze
    UA.analyze = lambda t, s="unknown": Analysis(ticker=t, error="stub")
    try:
        for good in ("TSLA", "BRK.B", "A"):
            r = c.post("/analyze", json={"ticker": good},
                       headers={"X-Core-Secret": "s3cret"})
            check(f"{good!r} accepted", r.status_code == 200, str(r.status_code))
    finally:
        UA.analyze = real


def test_concurrency_is_bounded():
    print("\nspend is bounded by slots, not by the caller's patience")
    import core_api.main as M
    c, M = client()
    check("a concurrency cap exists", M.MAX_CONCURRENT >= 1)
    check("...and it is small by default", M.MAX_CONCURRENT <= 8,
          str(M.MAX_CONCURRENT))
    src = (REPO / "core_api" / "main.py").read_text()
    check("over capacity returns 429 rather than queueing", "429" in src)
    check("there is a per-ticker single-flight lock", "_ticker_lock" in src)
    check("the secret is compared in constant time", "compare_digest" in src)


def test_health_reports_what_would_change_an_answer():
    print("\nhealth: a misconfigured deploy must not look merely quiet")
    c, _ = client()
    h = c.get("/health").json()
    for k in ("ok", "service", "version", "secret_configured",
              "missing_config", "model", "directional_margin"):
        check(f"health reports {k}", k in h)
    check("ok is True when both are configured", h["ok"] is True)

    import utils.config as CFG
    real_get = CFG.get
    CFG.get = lambda n, d="": ("" if n == "INFERENCE_URL" else real_get(n, d))
    c, _ = client(inference="")
    h = c.get("/health").json()
    check("no INFERENCE_URL -> ok False", h["ok"] is False, str(h))
    check("...and it names what is missing", "INFERENCE_URL" in h["missing_config"],
          str(h.get("missing_config")))
    # Railway keys on the STATUS CODE, never the body.
    check("...and /ready returns 503 so the platform fails the deploy",
          c.get("/ready").status_code == 503)
    CFG.get = real_get


def _stub_analysis(recommendation="Buy"):
    """A real Verdict over synthetic rows. No network, no model."""
    from utils.analyze import Analysis
    from utils.evidence import EvidenceRow
    from utils.verdict import adjudicate
    from utils.projections import simple_projection

    rows = [EvidenceRow(post_id=str(i), channel="social_base", text=f"p{i}",
                        author_id=f"a{i}", target_match_type="cashtag",
                        target_subject_status="primary",
                        evidence_types=("directional_view",),
                        margin=(0.6 if recommendation == "Buy" else -0.7),
                        scored=True, cluster_id=i, spam_risk="low",
                        evidence_eligible=True) for i in range(8)]
    prices = [100.0] * 24
    v = adjudicate(rows, prices, [1.0] * 24)
    a = Analysis(ticker="TSLA", sector="tech", verdict=v, ledger=rows,
                 prices=prices, volumes=[1.0] * 24, last_close=100.0,
                 bar_date="2026-08-14")
    a.projection = simple_projection(prices, v.social.direction, days=30,
                                     quality_ok=(v.recommendation == "Buy"))
    return a


def test_the_card_is_built_from_state_not_from_the_verdict_word():
    print("\nthe card: one producer, so renderers cannot disagree")
    from utils.analyze import card
    a = _stub_analysis("Buy")
    c = card(a)
    check("verdict matches the object", c["verdict"] == a.verdict.recommendation)
    check("reason is the adjudicator's, not the renderer's",
          c["reason"] == a.verdict.reason)
    check("pillars survive with their requirements",
          len(c["pillars"]) == len(a.verdict.pillars)
          and all("requirement" in p for p in c["pillars"]))
    check("would_change survives", c["would_change"] == list(a.verdict.would_change))
    check("evidence counts the voices that decided it, not the corpus",
          c["evidence"]["independent_voices"] == a.verdict.quality.eligible_clusters)
    # The string that caused the trouble: it must never be a lookup that
    # outruns the state.
    check("no 'Strong' claim on a Moderate-capped system",
          "Strong" not in c["headline"], c["headline"])
    check("Moderate is named as the ceiling",
          "unvalidated" in c["confidence_note"].lower(), c["confidence_note"])
    tiles = {t["label"] for t in c["tiles"]}
    check("the retired review-window tile is gone", "Review window" not in tiles)
    check("the drawdown tile is present", "Drawdown first" in tiles, str(tiles))

    bad = card(type(a)(ticker="TSLA", error="boom"))
    check("a failed analysis produces an error card, not a crash",
          bad.get("error") == "boom")


def test_analyze_returns_an_answer_not_a_5xx():
    print("\na ticker with no evidence is an ANSWER, and the caller already paid")
    import core_api.main as M
    c, M = client()
    from utils.analyze import Analysis
    M_analyze = M.analyze

    import utils.analyze as UA
    real = UA.analyze
    UA.analyze = lambda t, s="unknown": Analysis(ticker=t, error="no usable evidence")
    try:
        r = c.post("/analyze", json={"ticker": "ZZZZ"},
                   headers={"X-Core-Secret": "s3cret"})
        check("200, not 5xx", r.status_code == 200, str(r.status_code))
        body = r.json()
        check("ok is False", body["ok"] is False)
        check("the reason is carried", body["error"] == "no usable evidence")
        check("no card is invented", "card" not in body)
    finally:
        UA.analyze = real


def test_persist_uses_a_feature_the_database_accepts():
    print("\npersistence: a rejected feature name loses the row silently")
    sql = (REPO / "supabase" / "migrations").glob("*signal_log*.sql")
    text = "\n".join(p.read_text() for p in sql)
    check("core_api is an allowed feature", "'core_api'" in text)
    src = (REPO / "core_api" / "main.py").read_text()
    check("the service persists under that exact name",
          'feature="core_api"' in src)
    check("...and the constraint change is re-runnable",
          "drop constraint signal_log_feature_chk" in text)


def test_the_service_shares_utils_rather_than_copying_it():
    print("\none analysis, one implementation")
    df = (REPO / "core_api" / "Dockerfile").read_text()
    check("the image ships utils/", "COPY utils/" in df)
    check("...and does not vendor a second copy",
          not (REPO / "core_api" / "evidence.py").exists()
          and not (REPO / "core_api" / "verdict.py").exists())
    check("watch paths are set, so a deploy does not restart every service",
          "watchPatterns" in (REPO / "core_api" / "railway.toml").read_text())
    check("the image pins the deploy interpreter",
          "python:3.11" in df, "runtime.txt pins 3.11")


def main() -> int:
    print("=" * 74)
    print("  core-api: a service that can spend money")
    print("=" * 74)
    test_it_fails_closed_without_a_secret()
    test_it_refuses_to_spend_when_it_cannot_answer()
    test_the_ticker_cannot_rewrite_a_billed_query()
    test_concurrency_is_bounded()
    test_health_reports_what_would_change_an_answer()
    test_the_card_is_built_from_state_not_from_the_verdict_word()
    test_analyze_returns_an_answer_not_a_5xx()
    test_persist_uses_a_feature_the_database_accepts()
    test_the_service_shares_utils_rather_than_copying_it()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
