#!/usr/bin/env python3
"""The orchestration nobody was testing.

WHY THIS EXISTS

pages/Deep_Analysis.py used to contain the pipeline inline. Step 3 moved it to
utils.analyze, and at that moment every assertion pointed at the wrong file:
test_golden pins the MODULES (it calls build_ledger, adjudicate and
simple_projection directly, so it is itself a third hand-written copy of the
sequence), test_core_api monkey-patches analyze() away, and test_projections
pins pure functions. Nothing read the order the steps run in, or what happens
when one of them fails.

A reviewer proved the size of the hole: changing the adjudicate() call to pass
`None, None` for prices -- making the price veto permanently blind and Buy
permanently unreachable, the exact failure analyze()'s own docstring warns
about -- left 262 assertions green across six suites.

So this file tests the ORCHESTRATION and nothing else. It asserts the order,
the failure fallbacks, and the arguments the log writers are actually called
with. Every dependency is stubbed; no network, no model, no database.

Usage:
    python3 tests/test_analyze.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import utils.analyze as A  # noqa: E402
from utils.evidence import EvidenceRow  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

# 40 sessions of a gently rising stock: enough bars for simple_projection
# (which needs >= 5) and enough dispersion that the band is not degenerate.
PRICES = [100.0 + i * 0.4 + (1.3 if i % 3 else -1.1) for i in range(40)]
VOLUMES = [1.0] * 40


def check(name, cond, detail=""):
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


# ---------------------------------------------------------------- stub harness

class Recorder:
    """Captures every call the pipeline makes, in the order it makes them."""

    def __init__(self):
        self.calls: list[str] = []
        self.adjudicate_args: tuple = ()
        self.verdict_kwargs: dict = {}
        self.verdict_pos: tuple = ()
        self.signal_kwargs: dict = {}
        self.signal_pos: tuple = ()
        self.projection_args: tuple = ()


def _verdict_obj(recommendation="Watch"):
    """The shape persist() and card() read. Not utils.verdict.Verdict --
    building a real one needs a real cascade, and this file is about the
    orchestration around it, not the adjudicator inside it."""
    return SimpleNamespace(
        recommendation=recommendation, confidence="Moderate",
        reason="stub reason", branch="stub_branch", would_change=[],
        confidence_notes=[], pillars=[],
        social=SimpleNamespace(direction=0.20, conflict=False),
        risk=SimpleNamespace(soft_rate=0.10),
        quality=SimpleNamespace(eligible_clusters=6, score=0.62, tier="moderate"),
        own_clusters=1,
    )


def install(rec, *, results=None, ledger_rows=1, verdict=_verdict_obj,
            prices=PRICES, retrieval_raises=None, adjudicate_raises=None):
    """Replace every boundary utils.analyze reaches through.

    analyze() imports its dependencies INSIDE the function, so patching the
    source modules here is what the real call will pick up.
    """
    import utils.deep_analysis as DA
    import utils.evidence as EV
    import utils.finance as FIN
    import utils.relative as REL
    import utils.seed as SEED
    import utils.sentiment as SENT
    import utils.projections as PROJ
    import utils.verdict as VER
    import utils.verdict_log as VL
    import utils.signal_log as SL

    if results is None:
        results = {"angle_1": {"tweet_ids": ["1", "2", "3"], "summary": "s"},
                   "angle_2": {"tweet_ids": ["2", "4"], "summary": "s"}}

    def _retrieve(t, sector, sink=None):
        rec.calls.append("retrieve")
        if retrieval_raises:
            raise retrieval_raises
        if sink is not None:
            sink["ticker_corpus"] = [{"id": "1", "text": "aaa"}]
            sink["alias"] = ""
            sink["corpus_key"] = "ck"
            sink["wire_billed"] = 0
        return results

    def _stock(t):
        rec.calls.append("prices")
        return {"error": None, "prices": list(prices), "volumes": list(VOLUMES),
                "last_bar_date": "2026-08-21"} if prices else {"error": "none"}

    def _bench(t, sector):
        rec.calls.append("benchmark")
        return "XLK", [50.0] * 40

    def _score(texts):
        rec.calls.append("score")
        return [{"positive": 0.6, "negative": 0.2, "neutral": 0.2} for _ in texts]

    def _build(posts, t, alias="", channel="", scores=None):
        rec.calls.append("ledger")
        return [EvidenceRow(post_id=f"{channel}{i}", channel=channel,
                            text="stub", author_id=f"a{i}",
                            target_subject_status="clear",
                            catalyst_severity="hard", scored=True,
                            p_positive=0.7, p_negative=0.1, margin=0.6)
                for i in range(ledger_rows)]

    def _adj(ledger, prices_, volumes_, **kw):
        rec.calls.append("adjudicate")
        rec.adjudicate_args = (ledger, prices_, volumes_, kw)
        if adjudicate_raises:
            raise adjudicate_raises
        return verdict() if callable(verdict) else verdict

    def _proj(prices_, direction, days=30, quality_ok=False):
        rec.calls.append("projection")
        rec.projection_args = (prices_, direction, days, quality_ok)
        return {"error": None, "band": 8.0, "scenario_bear": -8.0,
                "scenario_bull": 8.4, "gain_p10": -8.0, "gain_p90": 8.4,
                "suggested_hold_days": 7, "success_rate": 41.0,
                "decision_horizon_days": 10,
                "movement_profile": {"5%": {"mae_p75": 0.034, "up_rate": 0.41,
                                            "down_rate": 0.39}}}

    def _legacy(results_):
        rec.calls.append("legacy_summary")
        return {"recommendation": "Watch", "confidence": "Low",
                "avg_sentiment": 0.05, "rationale": ["legacy"],
                "red_flag_rate": 0.25, "disagreement": 0.5}

    def _vl(*a, **kw):
        rec.calls.append("verdict_log")
        rec.verdict_pos, rec.verdict_kwargs = a, kw

    def _sl(*a, **kw):
        rec.calls.append("signal_log")
        rec.signal_pos, rec.signal_kwargs = a, kw

    DA.run_deep_analysis = _retrieve
    DA.generate_ai_summary = _legacy
    FIN.get_stock_data = _stock
    REL.benchmark_prices = _bench
    SENT.analyze_sentiment_batch = _score
    SENT.MODEL_NAME = "STUBMODEL"
    EV.build_ledger = _build
    VER.adjudicate = _adj
    PROJ.simple_projection = _proj
    SEED.fetch = lambda t, s, exclude_ids=None: []
    VL.record = _vl
    SL.record = _sl


# ------------------------------------------------------------------- the order

def test_prices_are_fetched_before_adjudication():
    print("\nORDER: the price veto fails closed, so it must never run blind")
    rec = Recorder()
    install(rec)
    a = A.analyze("TSLA", "technology")
    check("prices are fetched before adjudicate is called",
          rec.calls.index("prices") < rec.calls.index("adjudicate"),
          " -> ".join(rec.calls))
    # The invariant the docstring names, asserted on the ARGUMENT rather than
    # on call order alone: fetching prices and then not passing them is the
    # same blindness with a healthier-looking trace.
    _ledger, _prices, _vols, _kw = rec.adjudicate_args
    check("...and the prices reach the adjudicator", _prices == PRICES,
          repr(_prices)[:60])
    check("...and so do the volumes", _vols == VOLUMES, repr(_vols)[:60])
    check("...and so does the sector benchmark",
          _kw.get("benchmark") == "XLK" and _kw.get("benchmark_prices"),
          str(_kw))
    check("...and the bar date, without which forward return is uncomputable",
          _kw.get("bar_date") == "2026-08-21", str(_kw.get("bar_date")))
    check("a verdict was produced", a.ok, str(a.error))


def test_no_provider_calls_after_an_empty_retrieval():
    print("\nan empty answer is not worth a single paid call to confirm")
    rec = Recorder()
    install(rec, results={})
    a = A.analyze("TSLA")
    check("nothing is fetched after retrieval came back empty",
          rec.calls == ["retrieve"], " -> ".join(rec.calls))
    check("the caller can tell it apart from a crash", a.raised is False)
    check("...and is told why", a.error == "no results retrieved", str(a.error))


# -------------------------------------------------- the legacy fallback path

def test_an_empty_ledger_still_delivers_and_still_records():
    print("\nno verdict is a DEGRADATION: delivered, charged, and recorded")
    rec = Recorder()
    install(rec, ledger_rows=0)
    a = A.analyze("TSLA", "unknown")
    check("no verdict was produced", a.verdict is None)
    check("the legacy summary is computed by the pipeline, not the caller",
          a.legacy_summary.get("recommendation") == "Watch",
          str(a.legacy_summary))
    check("the projection still runs -- the user paid for it",
          a.projection.get("band") == 8.0, str(a.projection)[:80])
    # The gate DIFFERS by path, and pinning it is the point: the ledger gate
    # reads a quality tier the legacy adjudicator never computes.
    _p, _dir, _days, _q = rec.projection_args
    check("...under the legacy evidence-count gate, on legacy sentiment",
          _q is False and abs(_dir - 0.05) < 1e-9, f"quality_ok={_q} dir={_dir}")

    A.persist(a, feature="deep_analyze", event_id="ev-1")
    check("the verdict_log row is written", "verdict_log" in rec.calls,
          " -> ".join(rec.calls))
    check("...tagged plain |legacy, because nothing crashed",
          rec.verdict_kwargs.get("model") == "STUBMODEL|legacy",
          str(rec.verdict_kwargs.get("model")))
    check("...carrying the credit event, so charge and delivery reconcile",
          rec.verdict_kwargs.get("event_id") == "ev-1")
    check("...with the legacy recommendation, not an empty string",
          rec.verdict_pos[1] == "Watch", str(rec.verdict_pos))
    # The legacy adjudicator DOES measure these; it merely used not to return
    # them, and the row stored NULL -- "not measured" -- for real readings.
    check("...carrying the risk rate the fallback actually measured",
          rec.verdict_kwargs.get("red_flag_rate") == 0.25,
          str(rec.verdict_kwargs.get("red_flag_rate")))
    check("...and its disagreement spread",
          rec.verdict_kwargs.get("disagreement") == 0.5,
          str(rec.verdict_kwargs.get("disagreement")))
    check("signal_log is skipped -- every column in it is cascade state",
          "signal_log" not in rec.calls)


def test_a_failed_adjudication_degrades_rather_than_terminating():
    print("\nthe ledger raising must not cost a delivery or its row")
    rec = Recorder()
    install(rec, adjudicate_raises=ValueError("cascade exploded"))
    a = A.analyze("TSLA")
    check("the exception is reported, not swallowed",
          a.raised is True and "cascade exploded" in (a.error or ""), str(a.error))
    check("the analysis results survive it", bool(a.analysis_results))
    check("the legacy summary is produced", bool(a.legacy_summary))
    check("the projection still runs", a.projection.get("band") == 8.0)
    A.persist(a, feature="deep_analyze", event_id="ev-2")
    check("and the row is still written", "verdict_log" in rec.calls)
    # A crashed cascade and a merely thin one used to be recorded identically,
    # so a cascade failing on every request looked like a run of quiet tickers.
    check("...tagged legacy_error, not legacy",
          rec.verdict_kwargs.get("model") == "STUBMODEL|legacy_error",
          str(rec.verdict_kwargs.get("model")))


def test_retrieval_failure_is_distinguishable_from_an_empty_answer():
    print("\nthe portal shows a red panel for one and stops silently for the other")
    rec = Recorder()
    install(rec, retrieval_raises=RuntimeError("X is down"))
    a = A.analyze("TSLA")
    check("the exception is flagged", a.raised is True, str(a.error))
    check("...with its type, which bare str(e) loses",
          (a.error or "").startswith("RuntimeError:"), str(a.error))
    check("nothing downstream ran", rec.calls == ["retrieve"], " -> ".join(rec.calls))
    A.persist(a, feature="deep_analyze")
    check("and nothing is recorded -- there is no analysis to record",
          "verdict_log" not in rec.calls)


# ------------------------------------------------------------ what gets written

def test_the_two_tables_stay_joinable():
    print("\nverdict_log and signal_log are joined on (ticker, model)")
    rec = Recorder()
    install(rec)
    a = A.analyze("TSLA", "unknown")
    A.persist(a, feature="deep_analyze", event_id="ev-3")
    check("both rows carry the same discriminator",
          rec.verdict_kwargs.get("model") == rec.signal_kwargs.get("model")
          == "STUBMODEL|ledger",
          f"{rec.verdict_kwargs.get('model')} vs {rec.signal_kwargs.get('model')}")
    check("both carry the credit event",
          rec.verdict_kwargs.get("event_id") == rec.signal_kwargs.get("event_id")
          == "ev-3")
    # The behavioural form of the assertion tests/test_signal_log.py can only
    # make against source text.
    check("signal_log nulls the literal 'unknown' sector rather than storing it",
          rec.signal_kwargs.get("sector") is None,
          repr(rec.signal_kwargs.get("sector")))
    check("signal_log gets the bar date",
          rec.signal_kwargs.get("decision_trade_date") == "2026-08-21",
          repr(rec.signal_kwargs.get("decision_trade_date")))
    check("...and the price the call was decided on",
          rec.signal_kwargs.get("price_at_decision") == PRICES[-1])


def test_total_mentions_is_one_population():
    print("\nthe column three writers fill must hold one quantity")
    rec = Recorder()
    install(rec)
    a = A.analyze("TSLA")
    A.persist(a, feature="deep_analyze")
    # ids 1,2,3 and 2,4 -> four unique, not five, and not len(ledger).
    check("unique post ids across the angles, deduped",
          rec.verdict_kwargs.get("total_mentions") == 4,
          str(rec.verdict_kwargs.get("total_mentions")))

    rec2 = Recorder()
    install(rec2, results={"angle_1": {"tweet_ids": []}})
    b = A.analyze("TSLA")
    A.persist(b, feature="deep_analyze")
    # An `or len(a.ledger)` fallback here silently substituted evidence rows
    # -- including reused discovery seed -- into the same column. A measured
    # zero is a measurement.
    check("a true zero stays zero, and is not swapped for the ledger size",
          rec2.verdict_kwargs.get("total_mentions") == 0,
          str(rec2.verdict_kwargs.get("total_mentions")))


def test_no_prices_writes_null_not_a_measured_zero():
    print("\nan absent projection is NULL; 0 would be a number nobody measured")
    rec = Recorder()
    install(rec, prices=[])
    a = A.analyze("TSLA")
    A.persist(a, feature="deep_analyze")
    check("the projection never ran", "projection" not in rec.calls)
    for k in ("projected_p10", "projected_p90", "suggested_hold_days",
              "success_rate"):
        check(f"{k} is NULL", rec.verdict_kwargs.get(k) is None,
              repr(rec.verdict_kwargs.get(k)))


# ------------------------------------------------------------------ the card

def test_the_card_is_selected_by_key_not_by_prose():
    print("\nrenderers select on key; label is copy and may be reworded")
    rec = Recorder()
    install(rec)
    a = A.analyze("TSLA")
    keys = [t["key"] for t in A.price_tiles(a)]
    check("all three tiles are keyed",
          keys == ["last_price", "range_30d", "drawdown_first"], str(keys))
    check("card() serves the same tiles",
          [t["key"] for t in A.card(a)["tiles"]] == keys)
    vals = {t["key"]: t["value"] for t in A.price_tiles(a)}
    # .get, not []: a renamed key must be REPORTED as a failed assertion, not
    # raised as a KeyError that takes the rest of the suite down with it.
    check("the drawdown renders p75 as a loss",
          vals.get("drawdown_first") == "-3.4%", str(vals.get("drawdown_first")))
    check("the range renders both scenarios",
          vals.get("range_30d") == "-8.0% to 8.4%", str(vals.get("range_30d")))
    check("card() still returns the error stub with no verdict",
          A.card(A.Analysis(ticker="TSLA", error="boom")).get("error") == "boom")


def test_the_legacy_path_still_gets_its_price_tiles():
    print("\nthe legacy path is charged full price and must not be a thinner card")
    rec = Recorder()
    install(rec, ledger_rows=0)
    a = A.analyze("TSLA")
    keys = [t["key"] for t in A.price_tiles(a)]
    check("the range and drawdown tiles are present without a verdict",
          keys == ["last_price", "range_30d", "drawdown_first"], str(keys))


def test_a_route_keeps_its_cohort_separable():
    print("\nverdict_log has no feature column; model is the only separator")
    rec = Recorder()
    install(rec)
    a = A.analyze("TSLA", "technology")
    A.persist(a, feature="discovery", event_id="ev-9", route="discovery")
    check("the route is appended to the discriminator",
          rec.verdict_kwargs.get("model") == "STUBMODEL|ledger|discovery",
          str(rec.verdict_kwargs.get("model")))
    check("...and both tables still agree on it",
          rec.signal_kwargs.get("model") == rec.verdict_kwargs.get("model"))
    check("...and the feature column still separates them in signal_log",
          rec.signal_kwargs.get("feature") == "discovery")
    check("the route reaches verdict_log's event_id, which it used to lose",
          rec.verdict_kwargs.get("event_id") == "ev-9")

    rec2 = Recorder()
    install(rec2, ledger_rows=0)
    b = A.analyze("TSLA", "technology")
    A.persist(b, feature="discovery", event_id="ev-10", route="discovery")
    check("a routed legacy row is tagged on both axes",
          rec2.verdict_kwargs.get("model") == "STUBMODEL|legacy|discovery",
          str(rec2.verdict_kwargs.get("model")))
    # A FRESH recorder: install() repoints the stubs, so writing through an
    # older Recorder after a second install() silently reads a stale capture.
    rec3 = Recorder()
    install(rec3)
    c = A.analyze("TSLA", "technology")
    A.persist(c, feature="deep_analyze")
    check("no route means no suffix, so the portal is unchanged",
          rec3.verdict_kwargs.get("model") == "STUBMODEL|ledger",
          str(rec3.verdict_kwargs.get("model")))


def test_the_legacy_card_is_a_card_not_an_error():
    print("\na delivered fallback is a product and needs presentation")
    rec = Recorder()
    install(rec, ledger_rows=0)
    a = A.analyze("TSLA")
    c = A.card(a)
    check("it is not an error stub", not c.get("error"), str(c)[:80])
    check("it says which adjudicator spoke", c.get("adjudicator") == "legacy",
          str(c.get("adjudicator")))
    check("the verdict and confidence come from the fallback",
          (c["verdict"], c["confidence"]) == ("Watch", "Low"), str(c)[:80])
    check("the headline is produced here, not by a renderer's own dictionary",
          c["headline"] == "Hold — monitor closely", c["headline"])
    check("rationale stays a LIST, so bullet renderers keep their bullets",
          c["rationale"] == ["legacy"], str(c["rationale"]))
    # 0 would read as "we clustered and found none", which is a stronger and
    # false claim than "we did not cluster".
    check("uncomputed cascade measurements are None, not zero",
          c["evidence"]["independent_voices"] is None
          and c["evidence"]["quality_tier"] is None, str(c["evidence"]))
    check("...but the mention count is real and present",
          c["evidence"]["mentions"] == 4, str(c["evidence"]))
    rec4 = Recorder()
    install(rec4)                      # a ledger run, not the legacy one above
    lc = A.card(A.analyze("TSLA"))
    check("a ledger card still reports its adjudicator",
          lc.get("adjudicator") == "ledger", str(lc.get("adjudicator")))
    # Pinned on BOTH paths. Emptying only the ledger branch of this list left
    # every suite green while the panel's bullet list rendered nothing.
    check("...and carries the cascade's reason as its single bullet",
          lc.get("rationale") == ["stub reason"], str(lc.get("rationale")))


def main() -> int:
    print("=" * 74)
    print("  utils.analyze: the pipeline the portal and core-api both call")
    print("=" * 74)
    test_prices_are_fetched_before_adjudication()
    test_no_provider_calls_after_an_empty_retrieval()
    test_an_empty_ledger_still_delivers_and_still_records()
    test_a_failed_adjudication_degrades_rather_than_terminating()
    test_retrieval_failure_is_distinguishable_from_an_empty_answer()
    test_the_two_tables_stay_joinable()
    test_total_mentions_is_one_population()
    test_no_prices_writes_null_not_a_measured_zero()
    test_the_card_is_selected_by_key_not_by_prose()
    test_the_legacy_path_still_gets_its_price_tiles()
    test_a_route_keeps_its_cohort_separable()
    test_the_legacy_card_is_a_card_not_an_error()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
