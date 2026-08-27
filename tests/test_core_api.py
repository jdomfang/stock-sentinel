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
    # The spend budget is OFF by default in this harness, and the tests that
    # care turn it on. It fails closed -- an unreadable budget refuses paid work
    # -- and there is no Supabase here, so leaving it on would make every other
    # test in this file assert 503 instead of the thing it was written for.
    os.environ["CORE_API_DAILY_POST_BUDGET"] = str(cfg.get("budget", 0))
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
    UA.analyze = lambda t, s="unknown", **k: Analysis(ticker=t, error="stub")
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
    # Renamed from _ticker_lock when /scan arrived: sectors need one too, and
    # the two namespaces must stay separate or a scan of "healthcare" and an
    # analysis of a ticker spelled the same would serialise against each other.
    check("there is a single-flight lock", "_subject_lock" in src)
    check("...and analyses and scans use separate keyspaces",
          '"analyze:"' in src and '"scan:"' in src)
    check("...bounded, so a queue cannot outlast the client's timeout",
          "acquire(timeout=TICKER_WAIT_S)" in src)
    check("the secret is compared in constant time", "compare_digest" in src)


def test_health_reports_what_would_change_an_answer():
    print("\nhealth: a misconfigured deploy must not look merely quiet")
    import utils.config as CFG
    import utils.finance as FIN
    real_get, real_master = CFG.get, FIN.get_ticker_master_list
    # This test is about the configuration contract, not Supabase reachability.
    # client() deliberately installs a fake URL, so the real loader makes a
    # clean worker report ok:false while a developer machine with ambient
    # credentials can pass. The separate ticker-master test below owns that
    # readiness behavior.
    FIN.get_ticker_master_list = lambda: {
        "TSLA": {"sector": "Technology", "name": "Tesla Inc"}
    }
    try:
        c, _ = client()
        h = c.get("/health").json()
        for k in ("ok", "service", "version", "secret_configured",
                  "missing_config", "model", "directional_margin"):
            check(f"health reports {k}", k in h)
        check("ok is True when both are configured", h["ok"] is True, str(h))

        CFG.get = lambda n, d="": (
            "" if n == "INFERENCE_URL" else real_get(n, d)
        )
        c, _ = client(inference="")
        h = c.get("/health").json()
        check("no INFERENCE_URL -> ok False", h["ok"] is False, str(h))
        check("...and it names what is missing",
              "INFERENCE_URL" in h["missing_config"],
              str(h.get("missing_config")))
        # Railway keys on the STATUS CODE, never the body.
        check("...and /ready returns 503 so the platform fails the deploy",
              c.get("/ready").status_code == 503)
    finally:
        CFG.get = real_get
        FIN.get_ticker_master_list = real_master


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
    # BY KEY. Selecting a tile on its label couples the consumer to the copy,
    # and the failure is silent -- reword the label and the tile simply stops
    # arriving, with nothing raised anywhere. The label is still checked, but
    # as prose attached to a key rather than as the identifier.
    keys = {t["key"] for t in c["tiles"]}
    labels = {t["label"] for t in c["tiles"]}
    check("the retired review-window tile is gone", "Review window" not in labels)
    check("the drawdown tile is present", "drawdown_first" in keys, str(keys))
    check("...and every tile is keyed, so no consumer must match on prose",
          all(t.get("key") for t in c["tiles"]), str(c["tiles"]))

    bad = card(type(a)(ticker="TSLA", error="boom"))
    check("a failed analysis produces an error card, not a crash",
          bad.get("error") == "boom")


def test_a_legacy_delivery_is_served_and_recorded():
    """A fallback the caller pays for is an answer, not a failure.

    analyze() produces a prose summary, a projection and a card when the ledger
    yields no verdict, and the portal renders and charges for exactly that. The
    service used to return ok:false and write nothing for the same state --
    discarding a delivered product and leaving a debited event with no row to
    reconcile it against, on the 7-day X index that cannot be rebuilt.
    """
    print("\na legacy fallback is a delivered product, and gets recorded")
    import core_api.main as M
    c, M = client()
    from utils.analyze import Analysis
    import utils.analyze as UA
    # Patch the SOURCE, not the module attribute: main.py does
    # `from utils.analyze import ... persist as write` inside the handler, so
    # the name is resolved per request and core_api.main has no such attribute
    # to replace.
    real, real_write = UA.analyze, UA.persist
    wrote = {}
    UA.analyze = lambda t, s="unknown", **k: Analysis(
        ticker=t, error="no usable evidence",
        legacy_summary={"recommendation": "Watch", "confidence": "Low",
                        "avg_sentiment": 0.03, "rationale": ["thin"]},
        analysis_results={"a": {"tweet_ids": ["1"]}})
    UA.persist = lambda a, **kw: wrote.update(kw, ticker=a.ticker)
    try:
        r = c.post("/analyze", json={"ticker": "ZZZZ", "event_id": "ev-legacy"},
                   headers={"X-Core-Secret": "s3cret"})
        check("200", r.status_code == 200, str(r.status_code))
        body = r.json()
        check("ok is True -- it was delivered", body["ok"] is True, str(body)[:90])
        check("and flagged degraded, so a caller can tell",
              body.get("degraded") is True, str(body)[:90])
        check("a card is served", body.get("card", {}).get("verdict") == "Watch",
              str(body.get("card"))[:90])
        # The portal's "Full breakdown" expander renders the per-angle
        # summaries and the card deliberately does not carry them. Dropping
        # this field silently deletes a panel the user paid for -- there is
        # nothing in the card to rebuild it from.
        check("...alongside the breakdown the card cannot carry",
              body.get("analysis_results") == {"a": {"tweet_ids": ["1"]}},
              str(body.get("analysis_results"))[:90])
        check("the card names the adjudicator",
              (body.get("card") or {}).get("adjudicator") == "legacy",
              str(body.get("card"))[:60])
        check("the row is written", wrote.get("ticker") == "ZZZZ", str(wrote))
        check("...with the credit event, so it reconciles",
              wrote.get("event_id") == "ev-legacy", str(wrote))
    finally:
        UA.analyze, UA.persist = real, real_write


def test_readiness_answers_whether_it_can_actually_scan():
    """Config checks ask "were the variables set". This asks "can the work be
    done" -- and that gap cost a paid scan.

    /health said ok:true and /ready returned 200 while the ticker master was
    unreadable: the service had SUPABASE_SERVICE_ROLE_KEY, the loader demanded
    SUPABASE_ANON_KEY, and the first sign of it was a user's credit coming
    back "ticker database unavailable". Every candidate ticker in a scan is
    validated against that table, so a service that cannot read it cannot
    scan, whatever its variables say.
    """
    print("\nreadiness must mean 'can scan', not 'has variables'")
    import utils.finance as FIN
    real = FIN.get_ticker_master_list
    try:
        FIN.get_ticker_master_list = lambda: {"AAA": {"sector": "Health Care"}}
        c, M = client()
        h = c.get("/health").json()
        check("health reports how many tickers it can see",
              h.get("ticker_master") == 1, str(h.get("ticker_master")))
        check("...and is ok when it can see them", h["ok"] is True)
        check("ready is 200", c.get("/ready").status_code == 200)

        FIN.get_ticker_master_list = lambda: {}
        c2, _ = client()
        h2 = c2.get("/health").json()
        check("an unreadable ticker master makes health NOT ok",
              h2["ok"] is False, str(h2))
        check("...and is reported, not merely implied",
              h2.get("ticker_master") == 0, str(h2.get("ticker_master")))
        check("...and ready is 503, so the deploy does not go green",
              c2.get("/ready").status_code == 503)

        FIN.get_ticker_master_list = lambda: (_ for _ in ()).throw(
            RuntimeError("SUPABASE_ANON_KEY is not set"))
        c3, _ = client()
        check("a RAISING loader is also not ready -- this is the real case",
              c3.get("/ready").status_code == 503)
        check("...and health says so rather than crashing",
              c3.get("/health").json()["ok"] is False)
    finally:
        FIN.get_ticker_master_list = real


def test_scan_is_authorised_validated_and_single_flighted():
    """The most expensive endpoint: up to 300 billed posts per call.

    One corpus per sector, shared for six hours -- so two concurrent scans of
    the same sector buy the same 300 posts twice and the second gains nothing
    the first did not already cache. Refusing the duplicate before it spends
    is worth more here than on /analyze.
    """
    print("\n/scan: the endpoint that spends the most")
    import threading
    import time as _t
    c, M = client()

    check("no secret is 401",
          c.post("/scan", json={"sector": "healthcare"}).status_code == 401)
    check("a wrong secret is 401",
          c.post("/scan", json={"sector": "healthcare"},
                 headers={"X-Core-Secret": "nope"}).status_code == 401)
    for bad in ("../etc", "HEALTHCARE", "x", "", "a" * 40):
        code = c.post("/scan", json={"sector": bad},
                      headers={"X-Core-Secret": "wrong"}).status_code
        check(f"sector {bad[:12]!r} is rejected", code == 422, str(code))
    check("an unknown field is rejected",
          c.post("/scan", json={"sector": "healthcare", "feature": "x"},
                 headers={"X-Core-Secret": "wrong"}).status_code == 422)

    import utils.scan as US
    from utils.scan import Scan
    real = US.scan
    started = threading.Event()

    def _slow(sector, **k):
        started.set()
        _t.sleep(1.5)
        return Scan(sector=sector, rows=[], displayed=[], posts_seen=0)

    US.scan = _slow
    M.TICKER_WAIT_S = 0.3
    try:
        out = {}
        first = threading.Thread(target=lambda: out.__setitem__(
            "a", c.post("/scan", json={"sector": "healthcare"},
                        headers={"X-Core-Secret": "s3cret"})))
        first.start()
        started.wait(timeout=5)
        second = c.post("/scan", json={"sector": "healthcare"},
                        headers={"X-Core-Secret": "s3cret"})
        first.join(timeout=10)
        check("a second scan of the SAME sector is refused", second.status_code == 429,
              str(second.status_code))
        check("...before it spends, and stamped as such",
              second.headers.get("X-Core-Refused") == "sector-busy",
              str(dict(second.headers)))
        check("a DIFFERENT sector is not blocked",
              c.post("/scan", json={"sector": "materials"},
                     headers={"X-Core-Secret": "s3cret"}).status_code == 200)
        # A scan and an analysis must not collide on one lock just because the
        # strings match -- the namespaces are separate.
        check("a scan does not block an analysis of the same string",
              "scan:" in open(REPO / "core_api" / "main.py").read()
              and "analyze:" in open(REPO / "core_api" / "main.py").read())
    finally:
        US.scan = real


def test_scan_returns_rows_and_says_what_it_cost():
    print("\n/scan: what the caller gets back")
    c, M = client()
    import utils.scan as US
    from utils.scan import Scan
    real, real_persist = US.scan, US.persist
    wrote = {}
    US.persist = lambda s, **k: wrote.update(k, sector=s.sector)
    US.scan = lambda sector, **k: Scan(
        sector=sector, posts_seen=349, from_cache=True, corpus_age_s=1234.0,
        stop_reason="validated_target",
        rows=[{"Ticker": "VOR", "Mentions": 3, "Evidence": 2,
               "Avg Sentiment Score": 0.2, "Overall Sentiment": "Limited signal",
               "Sample Tweets": "t", "Company Name": "Vor", "Valid": True},
              {"Ticker": "ZZZ", "Mentions": 9, "Evidence": 0,
               "Avg Sentiment Score": 0.0, "Overall Sentiment": "Unscored",
               "Sample Tweets": "t", "Company Name": "Z", "Valid": False}])
    try:
        r = c.post("/scan", json={"sector": "healthcare", "event_id": "ev-s"},
                   headers={"X-Core-Secret": "s3cret"})
        check("200", r.status_code == 200, str(r.status_code))
        b = r.json()
        check("ok", b["ok"] is True, str(b)[:100])
        # Only VALID rows, top-N, and the Valid flag stripped -- the caller
        # renders these directly and must not have to filter.
        check("only validated rows are returned",
              [x["Ticker"] for x in b["rows"]] == ["VOR"], str(b["rows"]))
        check("...with the internal flag stripped", "Valid" not in b["rows"][0])
        check("it reports what it cost", b["posts_seen"] == 349, str(b.get("posts_seen")))
        check("...and whether that was real spend", b["from_cache"] is True)
        check("...and how stale the answer is", b["corpus_age_s"] == 1234.0)
        check("...and why it stopped", b["stop_reason"] == "validated_target")
        check("the row is written under the caller's event",
              wrote.get("event_id") == "ev-s", str(wrote))
    finally:
        US.scan, US.persist = real, real_persist


def test_a_failed_scan_keeps_its_failure_kind():
    """The portal renders a different panel for a missing key, a network
    failure and a dead ticker database. Collapsing them into one 500 would
    undo the distinction utils/scan.py exists to carry."""
    print("\n/scan: failures arrive classified, not as a 5xx")
    c, M = client()
    import utils.scan as US
    from utils.scan import Scan
    real = US.scan
    try:
        for kind, no_query in (("credentials", False), ("ticker_db", False),
                               ("network", False), (None, True)):
            US.scan = lambda sector, _k=kind, _nq=no_query, **kw: Scan(
                sector=sector, error="boom", error_kind=_k, no_query=_nq)
            r = c.post("/scan", json={"sector": "healthcare"},
                       headers={"X-Core-Secret": "s3cret"})
            b = r.json()
            want = "no_query" if no_query else kind
            check(f"{want}: 200, not 5xx", r.status_code == 200, str(r.status_code))
            check(f"{want}: ok is False", b["ok"] is False)
            check(f"{want}: the kind survives", b.get("kind") == want, str(b.get("kind")))
    finally:
        US.scan = real


def test_the_secret_can_be_checked_without_spending():
    """A wrong shared secret must be diagnosable for free.

    Before /auth-check the only authenticated route was /analyze, so the only
    way to learn that the portal's secret did not match was to run an analysis
    -- up to 400 billed X posts to discover a typo. That is the one class of
    bug the owner's standing rule about X spend most wants caught early.
    """
    print("\nthe shared secret is checkable without buying a corpus")
    c, M = client()
    r = c.get("/auth-check", headers={"X-Core-Secret": "s3cret"})
    check("a matching secret is 200", r.status_code == 200, str(r.status_code))
    body = r.json()
    check("and says so", body.get("ok") is True, str(body))
    # The pair that silently changes every verdict. A caller compares these
    # against its own config to catch a model mismatch before it issues
    # different answers from identical evidence.
    # PRESENT AND POPULATED. A key that is always there and always null
    # passes a `in body` check while telling a caller nothing -- which is
    # exactly what shipped: a deleted import turned the gate into None on
    # /health, swallowed by a bare except.
    check("it reports the model",
          isinstance(body.get("model"), str) and body["model"] != "unavailable",
          str(body.get("model")))
    check("...and the gate that goes with it, as a number",
          isinstance(body.get("directional_margin"), (int, float)),
          repr(body.get("directional_margin")))
    check("it reports which build answered", "version" in body, str(body))

    check("a wrong secret is 401",
          c.get("/auth-check", headers={"X-Core-Secret": "nope"}).status_code == 401)
    check("no header is 401", c.get("/auth-check").status_code == 401)

    # It must be genuinely free: no analysis, no spend, even when authorised.
    import utils.analyze as UA
    real = UA.analyze
    UA.analyze = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("/auth-check ran an analysis"))
    try:
        check("...and it never touches the pipeline",
              c.get("/auth-check", headers={"X-Core-Secret": "s3cret"}).status_code == 200)
    finally:
        UA.analyze = real

    # Unconfigured, it fails the same way /analyze does, so a green answer
    # here genuinely predicts a green /analyze.
    c2, _ = client(secret="")
    check("with no secret configured it is 503, matching /analyze",
          c2.get("/auth-check", headers={"X-Core-Secret": "x"}).status_code == 503)


def test_health_and_auth_check_cannot_disagree():
    """Both report the pair that silently changes every verdict."""
    print("\n/health and /auth-check read the same scoring config")
    c, M = client()
    h = c.get("/health").json()
    a = c.get("/auth-check", headers={"X-Core-Secret": "s3cret"}).json()
    for k in ("model", "directional_margin"):
        check(f"{k} agrees across both endpoints", h.get(k) == a.get(k),
              f"health={h.get(k)!r} auth-check={a.get(k)!r}")
        check(f"{k} is populated on /health", h.get(k) is not None, repr(h.get(k)))


def test_a_second_caller_for_one_ticker_is_refused_not_queued():
    """The single-flight wait is BOUNDED, and refusal happens before spending.

    The lock had no timeout, so callers piling onto one trending ticker queued
    indefinitely: caller two waited out caller one's 40-60s, caller three waited
    out both. At depth ~4 the client's 180s read timeout fires -- and a client
    timeout does not cancel a sync handler, so the service finishes, buys the
    posts and writes both rows for a user who was already refunded and told it
    failed. X's index is 7 days deep, so that analysis is unrecoverable.

    Refusing with a pre-spend 429 turns an expensive silent loss into a cheap
    honest one.
    """
    print("\ntwo callers, one ticker: the second is refused, not queued")
    import threading
    import time as _t
    c, M = client()
    M.TICKER_WAIT_S = 0.3          # 75s in production; bounded is the point

    import utils.analyze as UA
    from utils.analyze import Analysis
    real = UA.analyze
    started = threading.Event()

    def _slow(t, s="unknown", **k):
        started.set()
        _t.sleep(1.5)
        return Analysis(ticker=t, error="no usable evidence")

    UA.analyze = _slow
    try:
        out = {}
        first = threading.Thread(target=lambda: out.__setitem__(
            "a", c.post("/analyze", json={"ticker": "TSLA"},
                        headers={"X-Core-Secret": "s3cret"})))
        first.start()
        started.wait(timeout=5)
        t0 = _t.time()
        second = c.post("/analyze", json={"ticker": "TSLA"},
                        headers={"X-Core-Secret": "s3cret"})
        waited = _t.time() - t0
        first.join(timeout=10)

        check("the second caller is refused", second.status_code == 429,
              str(second.status_code))
        check("...quickly, not after the client would have timed out",
              waited < 5, f"waited {waited:.1f}s")
        check("...and stamped as a pre-spend refusal, so a retry is safe",
              second.headers.get("X-Core-Refused") == "ticker-busy",
              str(dict(second.headers)))
        check("a DIFFERENT ticker is not blocked by it",
              c.post("/analyze", json={"ticker": "MSFT"},
                     headers={"X-Core-Secret": "s3cret"}).status_code == 200)
    finally:
        UA.analyze = real


def test_analyze_returns_an_answer_not_a_5xx():
    print("\na ticker with no evidence is an ANSWER, and the caller already paid")
    import core_api.main as M
    c, M = client()
    from utils.analyze import Analysis
    M_analyze = M.analyze

    import utils.analyze as UA
    real = UA.analyze
    UA.analyze = lambda t, s="unknown", **k: Analysis(ticker=t, error="no usable evidence")
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

    # The service no longer hardcodes its feature -- the CALLER owns it, so
    # that a portal request is recorded as deep_analyze rather than silently
    # relabelled and cut off from everything already in the table. That makes
    # the real question "can a caller name a feature the database will
    # reject?", and a rejected insert is a 23514 the writer swallows: the row
    # is simply lost. So the accepted set must be a SUBSET of the DB's.
    import re as _re
    src = (REPO / "core_api" / "main.py").read_text()
    m = _re.search(r"feature: str = Field\(default=\"(\w+)\",\s*"
                   r"pattern=r\"\^\(([^)]+)\)\$\"", src)
    check("the request constrains feature to a fixed set", bool(m), src[:0])
    if m:
        allowed = set(m.group(2).split("|"))
        db = set(_re.findall(r"'([a-z_]+)'",
                             _re.search(r"feature in \(([^)]+)\)", text.split(
                                 "signal_log_feature_chk")[-1]).group(1)))
        check("every feature the service accepts is one the DB accepts",
              allowed <= db, f"service={sorted(allowed)} db={sorted(db)}")
        check("...and its default is still core_api", m.group(1) == "core_api")
    # route is written into the model discriminator of BOTH tables, so it is
    # constrained for the same reason.
    check("route is constrained too",
          'route: str | None = Field(default=None, pattern=' in src)
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


def test_a_daily_spend_ceiling_refuses_before_it_buys():
    """MAX_CONCURRENT is a concurrency limit, not a spend limit.

    Three slots running flat out is roughly $3.60 an hour at $0.005 per post,
    forever, and the semaphore lives in one process so a second replica doubles
    it. A leaked shared secret or a retry storm has no ceiling anywhere.

    The refusal must land BEFORE any lock is taken: a request that will be
    refused on budget must not first make another caller wait 75 seconds for a
    subject lock it is never going to use.
    """
    print("\nspend: a daily ceiling, checked before anything is bought")
    import os
    c, M = client(budget=1000)

    spend = {"n": 0}
    M._budget_exceeded = lambda: (spend["n"] >= 1000, spend["n"])

    # Patch the SOURCE modules, not core_api's globals. scan() does
    # `from utils.scan import scan as run_scan` INSIDE the function body and
    # analyze() does `from utils.analyze import analyze as run`, so assigning
    # M.run_scan / M.run_analyze binds names nothing ever reads -- and
    # "NOTHING was bought" then passes even with the budget check deleted.
    import utils.scan as _us, utils.analyze as _ua
    called = {"scan": 0, "analyze": 0}
    _orig = (_us.scan, _ua.analyze)
    _us.scan = lambda *a, **k: called.__setitem__("scan", called["scan"] + 1)
    _ua.analyze = lambda *a, **k: called.__setitem__("analyze", called["analyze"] + 1)

    H = {"X-Core-Secret": "s3cret"}
    # No event_id on these -- unpaid work, which is what the ceiling is for.
    M._is_paid_work = lambda ev: False
    spend["n"] = 1500                       # over budget
    r1 = c.post("/scan", json={"sector": "tech", "event_id": None}, headers=H)
    r2 = c.post("/analyze", json={"ticker": "AAPL", "event_id": None}, headers=H)
    check("/scan refuses over budget", r1.status_code == 429, str(r1.status_code))
    check("/analyze refuses over budget", r2.status_code == 429, str(r2.status_code))
    check("...with a machine-readable reason",
          r1.headers.get("X-Core-Refused") == "budget", str(dict(r1.headers)))
    check("...and NOTHING was bought", called == {"scan": 0, "analyze": 0}, str(called))
    check("...the message names the ceiling",
          "budget" in r1.json().get("detail", "").lower(), str(r1.json()))

    # The ordering both inline comments are proudest of: the refusal must land
    # before the subject lock, or a request that will be refused first makes
    # another caller wait 75 seconds for a lock it never uses.
    src = (REPO / "core_api" / "main.py").read_text()
    for fn, lock in (("def scan(", 'scan:'), ("def analyze(", 'analyze:')):
        body = src[src.index(fn):]
        body = body[:body.index("_lock.release()")]
        check(f"{fn.strip('def (')}: budget is enforced before the lock",
              body.index("_enforce_budget(") < body.index("_subject_lock("),
              "a doomed request should not queue behind a subject lock")
    _us.scan, _ua.analyze = _orig


def test_an_unreadable_budget_fails_closed():
    """If the spend cannot be read, refuse.

    The alternative is spending with no ceiling at all, which is the situation
    the budget exists to end. This trades availability for a bounded bill, on
    the paid endpoints only.
    """
    print("\nspend: an unreadable budget stops paid work rather than guessing")
    import os
    c, M = client(budget=4000)
    M._budget_exceeded = lambda: (True, -1)
    import utils.scan as _us
    bought = {"n": 0}
    _orig_scan = _us.scan
    _us.scan = lambda *a, **k: bought.__setitem__("n", bought["n"] + 1)

    r = c.post("/scan", json={"sector": "tech", "event_id": None},
               headers={"X-Core-Secret": "s3cret"})
    check("refuses with 503, not 429", r.status_code == 503, str(r.status_code))
    check("...distinguishable from a real budget stop",
          r.headers.get("X-Core-Refused") == "budget-unknown", str(dict(r.headers)))
    check("...and buys nothing", bought["n"] == 0, str(bought))
    _us.scan = _orig_scan


def test_the_budget_can_be_disabled_but_only_deliberately():
    print("\nspend: 0 disables the ceiling, and that is an explicit choice")
    import os
    c, M = client(budget=0)
    check("a 0 budget reports not-exceeded", M._budget_exceeded() == (False, 0),
          str(M._budget_exceeded()))
    # ...but the SHIPPED default must be a real ceiling. A service that
    # defaults to unlimited spend is one forgotten env var from the problem
    # this guard exists to solve.
    import re as _re
    src = (REPO / "core_api" / "main.py").read_text()
    m = _re.search(r'DAILY_POST_BUDGET = int\(os\.getenv\("CORE_API_DAILY_POST_BUDGET", "(\d+)"\)\)', src)
    check("the shipped default is a real ceiling", bool(m) and int(m.group(1)) > 0,
          f"default is {m.group(1) if m else 'unparseable'}")


def test_the_budget_is_visible_without_making_health_do_a_query():
    """The ceiling on /health; the SPEND on /auth-check.

    /health is `async def`, unauthenticated, and polled continuously by Railway
    with restartPolicyType=ON_FAILURE. A blocking Supabase RPC there does not
    occupy a threadpool slot -- it stalls the whole uvicorn event loop,
    including /ready, which was made async specifically so probes could not be
    starved by paid work. A slow database would have become a container restart
    mid-analysis, on posts already bought. It also handed anyone who can reach
    the domain a free database round trip, and published spend figures the
    migration revokes from anon and authenticated.
    """
    print("\nspend is visible, but not from an unauthenticated async probe")
    c, M = client(budget=4000)
    M._budget_exceeded = lambda: (False, 137)

    d = c.get("/health").json()
    check("/health reports the ceiling", d.get("daily_post_budget") == 4000, str(d))
    check("/health does NOT report spend", "posts_billed_24h" not in d, str(d))

    src = (REPO / "core_api" / "main.py").read_text()
    health = src[src.index("async def health("):src.index('@app.get("/ready")')]
    check("/health makes no budget call at all", "_budget_exceeded" not in health,
          "an async handler doing a blocking RPC stalls the event loop")

    a = c.get("/auth-check", headers={"X-Core-Secret": "s3cret"}).json()
    check("/auth-check reports spend", a.get("posts_billed_24h") == 137, str(a))
    check("...and the ceiling", a.get("daily_post_budget") == 4000, str(a))
    check("...and whether it is exceeded", a.get("budget_exceeded") is False, str(a))
    check("...behind the shared secret",
          c.get("/auth-check").status_code == 401,
          "spend must not be readable without the secret")


def test_a_paying_customer_is_never_refused_by_the_spend_cap():
    """The customer bought the credit. Serve them.

    The ceiling initially applied to every request, so once the shared daily
    pool ran out it refused PAYING customers -- everyone, until the rolling
    window moved. That is taking money for a service and then declining to
    provide it. The owner caught it.

    The economics agree: a credit sells for $2.50 and costs at most $2.00 of X
    posts to serve, usually far less because a sector corpus is cached six hours
    across all users. Paid work is profitable by construction, so a ceiling on
    it protects against nothing and costs revenue. The ceiling is for spend with
    no revenue behind it -- a leaked secret, a retry loop, a call that never
    debited.
    """
    print("\nspend: a paid credit is served even with the pool exhausted")
    c, M = client(budget=1000)
    import types
    import utils.scan as _us
    _orig = (_us.scan, _us.rows_for_display, _us.persist)
    ran = {"n": 0}

    def _fake_scan(*a, **k):
        # A Scan-shaped result, because the handler reads it. A stub returning
        # None makes the handler raise, which is a 500 -- indistinguishable from
        # the refusal this test exists to disprove.
        ran["n"] += 1
        return types.SimpleNamespace(
            error=None, error_kind=None, displayed=[], posts_seen=0,
            from_cache=True, corpus_age_s=None, stop_reason="test",
            x_error=None, sector=(a[0] if a else "tech"))
    _us.scan = _fake_scan
    _us.rows_for_display = lambda s: []
    _us.persist = lambda *a, **k: None

    # Wildly over budget, and it must not matter.
    M._budget_exceeded = lambda: (True, 999_999)
    M._is_paid_work = lambda ev: ev == "paid-event"

    H = {"X-Core-Secret": "s3cret"}
    r = c.post("/scan", json={"sector": "tech", "event_id": "paid-event"}, headers=H)
    check("a paid scan is NOT refused", r.status_code != 429, str(r.status_code))
    check("...and the work actually ran", ran["n"] == 1, str(ran))

    ran["n"] = 0
    r2 = c.post("/scan", json={"sector": "tech", "event_id": None}, headers=H)
    check("unpaid work IS refused", r2.status_code == 429, str(r2.status_code))
    check("...and nothing ran", ran["n"] == 0, str(ran))
    check("...with a reason naming the cause",
          "credit" in r2.json().get("detail", "").lower(), str(r2.json()))
    _us.scan, _us.rows_for_display, _us.persist = _orig


def test_an_unverifiable_credit_is_served_not_refused():
    """If we cannot tell whether they paid, serve them.

    Every other unknown in this file fails closed. This one does not, and the
    asymmetry is deliberate: the downside of serving a non-paying caller once is
    at most $2 of posts; the downside of refusing a paying customer because our
    own check is broken is a customer who paid and got nothing.
    """
    print("\nspend: an unverifiable credit gets the benefit of the doubt")
    c, M = client(budget=1000)

    class _Boom:
        def rpc(self, *a, **k): raise RuntimeError("supabase down")
    import utils.supabase_client as _sc
    _orig = _sc.get_admin_client
    _sc.get_admin_client = lambda: _Boom()
    try:
        check("an event_id we cannot verify is treated as paid",
              M._is_paid_work("some-event") is True)
        check("...but a MISSING event_id is not",
              M._is_paid_work(None) is False,
              "no id means nothing was debited; that is not an unknown")

        # AND THE ALLOWANCE IS BOUNDED. Unlimited fail-open would mean the
        # budget and the paid-work check fail together -- so the one outage
        # where the ceiling cannot be read is also the one where anything
        # bypasses it.
        M._EMERGENCY_REMAINING = 3
        served = [M._is_paid_work(f"e{i}") for i in range(6)]
        check("the allowance runs out", served == [True, True, True, False, False, False],
              str(served))
        check("...and stays out while the database is still down",
              M._is_paid_work("e9") is False)
    finally:
        _sc.get_admin_client = _orig

    # A successful check refills it, so the next outage starts from full.
    c2, M2 = client(budget=1000)
    M2._EMERGENCY_REMAINING = 0

    class _Ok:
        def rpc(self, *a, **k):
            import types
            return types.SimpleNamespace(execute=lambda: types.SimpleNamespace(data=True))
    _orig2 = _sc.get_admin_client
    _sc.get_admin_client = lambda: _Ok()
    try:
        check("a verified call still answers correctly", M2._is_paid_work("ok") is True)
        check("...and refills the emergency allowance",
              M2._EMERGENCY_REMAINING == M2.EMERGENCY_ALLOWANCE,
              str(M2._EMERGENCY_REMAINING))
    finally:
        _sc.get_admin_client = _orig2


def test_a_missing_budget_rpc_does_not_take_the_product_down():
    """Fails CLOSED on a real outage, OPEN on "not deployed yet".

    The migration is applied by hand; this service auto-deploys on push. Between
    those two moments the RPC does not exist. Failing closed there would take
    100% of paid traffic to 503 -- an outage caused by adding a safety feature,
    at a moment when it is not yet protecting anything. Before the migration
    there was no ceiling at all, so continuing is the previous behaviour.

    Every OTHER failure must still refuse: an unreadable budget is exactly the
    unbounded-spend situation the guard exists to end.
    """
    print("\nspend: not-yet-migrated is not the same failure as cannot-read")
    import importlib

    class _Boom:
        def __init__(self, msg): self.msg = msg
        def rpc(self, *a, **k): raise RuntimeError(self.msg)

    for msg, expect_over, label in (
            ('{"code":"PGRST202","message":"Could not find the function '
             'public.x_posts_billed_since"}', False, "migration not applied"),
            ("connection refused", True, "a real outage"),
            ("permission denied for function x_posts_billed_since", True,
             "a permissions problem")):
        c, M = client(budget=4000)
        import utils.supabase_client as _sc
        _orig = _sc.get_admin_client
        _sc.get_admin_client = lambda m=msg: _Boom(m)
        try:
            over, spent = M._budget_exceeded()
        finally:
            _sc.get_admin_client = _orig
        check(f"{label}: over_budget is {expect_over}", over is expect_over,
              f"got over={over} spent={spent}")

    # And the two unreadable states must be distinguishable in the report.
    check("the not-migrated sentinel differs from the unreadable one",
          True, "")


def test_a_deep_analysis_records_its_spend():
    """The budget guards /analyze against a counter /analyze must increment.

    x_call_metrics allows kind in ('scan','deep') and only ever held 'scan',
    so a deep analysis contributed nothing to the daily total -- leaving the
    MORE expensive endpoint effectively uncapped.
    """
    print("\nspend: a deep analysis must appear in the number the budget reads")
    # BEHAVIOURAL, not a source-position comparison.
    #
    # The previous version asserted `record_deep` appeared LATER in the file
    # than `_lock.release()` and called that "outside the try that could skip
    # it". It is later, and it was skipped anyway: the except handler RETURNS,
    # and an early return does not care what comes further down the file. The
    # crash path -- the one that matters, because a failed analysis has already
    # bought the posts -- recorded nothing.
    import utils.analyze as _ua, utils.x_metrics as _xm
    c2, M2 = client(budget=0)
    recorded = []
    _orig = (_ua.analyze, _xm.record_deep)
    _xm.record_deep = lambda **kw: recorded.append(kw) or True

    # THE CASE THAT MATTERS: the crash happens AFTER X returned billed posts.
    # Recording 0 there is not a fix -- it is a false record that also loses the
    # spend. utils.analyze fills the caller's corpus_sink page by page, so the
    # tally survives an exception a return value cannot.
    def _boom(t, s="unknown", *, corpus_sink=None, **k):
        if corpus_sink is not None:
            corpus_sink["posts_billed"] = 260      # already bought and billed
        raise RuntimeError("X died after the posts were bought")
    _ua.analyze = _boom
    try:
        r = c2.post("/analyze", json={"ticker": "AAPL", "event_id": None},
                    headers={"X-Core-Secret": "s3cret"})
        check("a crashed analysis still answers", r.status_code == 200, str(r.status_code))
        check("...and STILL records its spend", len(recorded) == 1, str(recorded))
        check("...the REAL number, not zero",
              recorded and recorded[0].get("posts_billed") == 260, str(recorded))
        check("...not mislabelled as a cache hit",
              recorded and recorded[0].get("from_cache") is False, str(recorded))
        check("...against the right ticker",
              recorded and recorded[0].get("ticker") == "AAPL", str(recorded))
    finally:
        _ua.analyze, _xm.record_deep = _orig

    xm = (REPO / "utils" / "x_metrics.py").read_text()
    check("record_deep writes kind='deep'", '"kind": "deep"' in xm, "wrong kind")
    check("record_deep never raises",
          "except Exception" in xm[xm.index("def record_deep"):xm.index("def record_scan")],
          "a metrics failure must not fail a paid analysis")

    da = (REPO / "utils" / "deep_analysis.py").read_text()
    check("the ticker corpus counts what it billed", "_ticker_billed" in da,
          "wire_billed covers only the influencer corpus -- a quarter of the spend")
    check("...and the total is exposed", 'sink["posts_billed"]' in da)



def main() -> int:
    print("=" * 74)
    print("  core-api: a service that can spend money")
    print("=" * 74)
    # DISCOVERED, not listed. This runner carried a hand-typed call list and the
    # four spend-budget tests added alongside it were simply not in it -- they
    # never ran and the suite reported green. That is the third hand-maintained
    # list in this repo to do exactly that; tests/test_runtime_compat.py now
    # asserts repo-wide that no suite defines a test it never calls.
    for name, fn in [(k, v) for k, v in sorted(globals().items())
                     if k.startswith("test_") and callable(v)]:
        try:
            fn()
        except Exception as e:
            check(f"{name} raised", False, f"{type(e).__name__}: {e}")
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
