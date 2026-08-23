"""One Deep Analyze, start to finish, with no UI. The migration's centre of gravity.

WHY THIS MODULE EXISTS

The whole pipeline -- buy posts, fetch prices, fetch the sector benchmark, score,
build the ledger, adjudicate, project, log -- lived inline in
pages/Deep_Analysis.py, interleaved with progress bars and st.markdown. So it
could only run inside a Streamlit script, and pages/Discovery.py had to
reimplement the same sequence to offer the same product. Two implementations of
one analysis is how they drift, and they have: the price row on one page said
"Proj. Gain 30d" for months after the other retired the phrase.

THE RESULT CARRIES ITS OWN PRESENTATION, and that is deliberate.

The invariant this codebase is built on -- the explanation can never contradict
the decision -- is enforced on the Verdict object. It is NOT enforced on the
four hand-written renderers that draw the card, three of which never see that
object. Every contradiction found in review lived in that gap: "Strong upside
signal" was a dict lookup on the word "Buy" with no access to price status.

So `card()` is produced HERE, beside the state that justifies it. A renderer
consumes it and adds nothing.

WHAT IS NOT HERE

Credits and identity. Charging needs a user, and a user comes from a session or
a request token -- both of which belong to the caller. The portal still charges
before calling this; a service will charge from an authenticated request. Either
way this function is about the analysis and nothing else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger(__name__)

DEFAULT_HORIZON_DAYS = 30


@dataclass
class Analysis:
    """Everything one Deep Analyze produced. Serialisable, UI-free."""

    ticker: str
    sector: str = ""
    verdict: Any = None                    # utils.verdict.Verdict, or None
    projection: dict = field(default_factory=dict)
    ledger: list = field(default_factory=list)
    prices: list = field(default_factory=list)
    volumes: list = field(default_factory=list)
    benchmark: str = ""
    benchmark_prices: Any = None
    bar_date: str | None = None
    last_close: float | None = None
    corpus: dict = field(default_factory=dict)
    analysis_results: dict = field(default_factory=dict)
    error: str | None = None
    # The older prose adjudicator's output, filled ONLY when the ledger path
    # produced no verdict. The portal has always still delivered this and kept
    # the credit -- a degradation, not a failure -- so it is part of the
    # analysis, not a thing the caller reconstructs.
    legacy_summary: dict = field(default_factory=dict)
    # An exception occurred, as against an empty but valid answer. The portal
    # shows a red "Analysis failed" panel for the first and stops silently for
    # the second, and `error` alone cannot tell them apart.
    raised: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.verdict is not None


def unique_mentions(a: "Analysis") -> int:
    """Unique post ids across the analysis angles -- the retrieved corpus.

    One definition, because `total_mentions` is a column three writers fill and
    a column holding two populations cannot be read afterwards.
    """
    return len({str(t) for r in (a.analysis_results or {}).values()
                for t in (r.get("tweet_ids") or [])})


def price_tiles(a: "Analysis") -> list:
    """The two price tiles, verdict or no verdict.

    Split out of card() because the legacy path renders them too and card()
    contractually returns an error stub when there is no verdict.
    """
    proj = a.projection or {}
    mae = ((proj.get("movement_profile") or {}).get("5%") or {}).get("mae_p75")
    # KEY AND LABEL ARE DIFFERENT THINGS. A renderer selects on `key`; `label`
    # is prose and may be reworded. Selecting on the label couples every
    # renderer to the copy, and the failure is silent -- the tile simply stops
    # appearing, with no error anywhere.
    tiles = [{"key": "last_price", "label": "Last Price",
              "value": "Unavailable" if a.last_close is None else f"${a.last_close:.2f}"}]
    # GUARDED. These format numbers that came out of a Monte Carlo or across a
    # network, and this is the single producer for both pages and the service.
    # A raise here used to degrade three tiles to "Unavailable"; now that the
    # page reads its whole card from here, the same raise would destroy a
    # delivered analysis AND the only permanent record of it.
    try:
        if proj.get("error") is None and proj.get("band") is not None:
            tiles.append({"key": "range_30d", "label": "30d range (vol)",
                          "value": f"{proj['scenario_bear']:.1f}% to {proj['scenario_bull']:.1f}%"})
            tiles.append({"key": "drawdown_first", "label": "Drawdown first",
                          "value": ("under 0.1%" if (mae is not None and mae < 0.001)
                                    else "Unavailable" if mae is None
                                    else f"-{mae * 100:.1f}%")})
    except Exception:
        logger.warning("price_tiles: formatting failed for %s", a.ticker,
                       exc_info=True)
    return tiles


def _fmt_pct(x, nd=1) -> str:
    return "Unavailable" if x is None else f"{x:+.{nd}f}%"


def card(a: Analysis) -> dict:
    """The rendered card, as data. One producer, so renderers cannot disagree.

    Every string a user reads about the verdict is built here, from the state
    that produced it. A renderer that invents a label is reintroducing the bug
    this exists to remove.
    """
    # The legacy fallback is a DELIVERED product at full price, so it gets a
    # card. Without one every renderer that supports the fallback keeps its own
    # copy of the headline and confidence-note wording -- which is how
    # pages/Discovery.py came to hold a fourth set of those dictionaries.
    if not a.ok and not a.legacy_summary:
        return {"ticker": a.ticker, "error": a.error or "analysis unavailable"}

    v, proj = a.verdict, (a.projection or {})
    leg = a.legacy_summary or {}
    tiles = price_tiles(a)
    recommendation = v.recommendation if v is not None else leg.get("recommendation", "")
    confidence = v.confidence if v is not None else leg.get("confidence", "")

    return {
        "ticker": a.ticker,
        "sector": a.sector or None,
        "verdict": recommendation,
        "confidence": confidence,
        # Which adjudicator spoke. A renderer that cannot tell them apart will
        # eventually present a fallback with the confidence of a full cascade.
        "adjudicator": "ledger" if v is not None else "legacy",
        # NOT a lookup on the verdict word. These read the state, which is the
        # entire reason the card is built here and not in a renderer.
        "headline": {"Buy": "Evidence leans upside",
                     "Watch": "Hold — monitor closely",
                     "Avoid": "Risk outweighs reward"}.get(recommendation, ""),
        "confidence_note": {"High": "Strong data backing",
                            "Moderate": "Highest we issue — unvalidated",
                            "Low": "Thin data — use caution"}.get(confidence, ""),
        # The number the sentiment pill is drawn from. Exposed because a
        # renderer that reaches into a.verdict for it will read None on the
        # legacy path and format it as 0.00 -- "Neutral" stated as a finding.
        # None, not 0.0, when there is nothing to report -- the comment above
        # objects to exactly the coercion the `or 0.0` version performed, and a
        # renderer printing "Neutral (+0.00)" states a finding nobody made.
        # NOTE the two paths carry different quantities: the cascade's
        # margin-based direction, and the legacy mean of post scores. They are
        # separable in the database by the |legacy tag on `model`.
        "avg_sentiment": (float(v.social.direction) if v is not None
                          else None if leg.get("avg_sentiment") is None
                          else float(leg["avg_sentiment"])),
        # A LIST, kept separate from `reason`. The legacy adjudicator emits
        # several bullets and the cascade emits one; flattening them into a
        # single string turned a bullet list into one run-on line on the
        # renderer that shows them as <li>.
        "rationale": ([v.reason] if v is not None
                      else list(leg.get("rationale") or [])),
        "reason": (v.reason if v is not None
                   else "; ".join(leg.get("rationale") or []) or
                   "Fallback summary -- the evidence ledger produced no verdict."),
        "branch": v.branch if v is not None else "legacy",
        "would_change": list(v.would_change) if v is not None else [],
        "confidence_notes": list(v.confidence_notes) if v is not None else [],
        "pillars": [{"name": p.name, "passed": p.passed, "value": p.value,
                     "requirement": p.requirement, "blocks_buy": p.blocks_buy}
                    for p in v.pillars] if v is not None else [],
        "tiles": tiles,
        "evidence": {
            # None, not 0: the legacy adjudicator does not cluster, so there is
            # no measurement here. A zero would read as "we looked and found
            # none", which is a different and stronger claim.
            "independent_voices": v.quality.eligible_clusters if v is not None else None,
            "own_voices": v.own_clusters if v is not None else None,
            "quality": round(v.quality.score, 3) if v is not None else None,
            "quality_tier": v.quality.tier if v is not None else None,
            "mentions": unique_mentions(a),
            "price_points": len(a.prices or []),
        },
        "movement": {
            "band_pct": proj.get("band"),
            "horizon_days": proj.get("decision_horizon_days"),
            "targets": {k: val for k, val in (proj.get("movement_profile") or {}).items()},
        } if proj.get("error") is None else None,
    }


def analyze(ticker: str, sector: str = "unknown", *,
            corpus_sink: dict | None = None) -> Analysis:
    """Run one Deep Analyze. Never raises; failures land in `.error`.

    The sequence is the one pages/Deep_Analysis.py performed inline, in the same
    order and with the same guards. Order matters in one place and it is not
    obvious: PRICES ARE FETCHED BEFORE ADJUDICATION, because the price veto
    consumes them and fails closed -- fetching after would leave the veto
    permanently blind and Buy permanently unreachable.
    """
    from utils.deep_analysis import run_deep_analysis
    from utils.evidence import build_ledger
    from utils.finance import get_stock_data
    from utils.projections import simple_projection
    from utils.sentiment import analyze_sentiment_batch
    from utils.verdict import adjudicate

    t = (ticker or "").strip().upper()
    a = Analysis(ticker=t, sector=sector or "")
    if not t:
        a.error = "no ticker"
        return a

    sink = corpus_sink if corpus_sink is not None else {}
    try:
        a.analysis_results = run_deep_analysis(t, sector, sink=sink)
    except Exception as e:
        logger.exception("analyze: retrieval failed for %s", t)
        a.error = f"{type(e).__name__}: {e}"
        a.raised = True
        return a
    a.corpus = sink

    # DEFENSIVE, and today unreachable: run_deep_analysis returns one entry per
    # prompt in ANALYSIS_PROMPTS even on a total X outage, filling each with an
    # error stub rather than returning {}. Kept because the caller refunds on
    # this path regardless, so everything below -- prices, the benchmark ETF,
    # two scoring passes, seed.fetch -- would be provider calls spent to learn
    # what is already known. It does NOT catch the state that actually occurs,
    # which is a full eight-angle dict containing zero unique post ids; that
    # still runs the whole pipeline and still charges, exactly as it did
    # before this refactor.
    if not a.analysis_results:
        a.error = "no results retrieved"
        return a

    # Prices first. See the docstring.
    try:
        sd = get_stock_data(t)
        if sd.get("error") is None and sd.get("prices"):
            a.prices = sd["prices"]
            a.volumes = sd.get("volumes") or []
            a.bar_date = sd.get("last_bar_date")
            last = a.prices[-1]
            if isinstance(last, (int, float)):
                a.last_close = float(last)
    except Exception:
        logger.warning("analyze: price fetch failed for %s", t, exc_info=True)

    try:
        if a.prices:
            from utils.relative import benchmark_prices as _bench
            a.benchmark, a.benchmark_prices = _bench(t, sector)
    except Exception:
        logger.warning("analyze: benchmark fetch failed for %s", t, exc_info=True)

    # ---- ledger over every channel ----
    try:
        alias = sink.get("alias") or ""
        ledger: list = []
        for key, channel in (("ticker_corpus", "social_base"),
                             ("influencer_corpus", "newswire")):
            posts = sink.get(key) or []
            if not posts:
                continue
            dists = analyze_sentiment_batch([(p.get("text") or "")[:512] for p in posts])
            by_id = {str(p["id"]): d for p, d in zip(posts, dists)
                     if p.get("id") is not None}
            seen = {r.post_id for r in ledger}
            ledger += [r for r in build_ledger(posts, t, alias=alias,
                                               channel=channel, scores=by_id)
                       if r.post_id not in seen]

        # Free evidence: a recent sector scan may hold posts this ticker's own
        # query will never reach. Two arms on one ticker in the same window
        # shared zero posts out of 198.
        try:
            from utils import seed as _seed
            extra = _seed.fetch(t, sector, exclude_ids={r.post_id for r in ledger})
            if extra:
                dists = analyze_sentiment_batch(
                    [(p.get("text") or "")[:512] for p in extra])
                by_id = {str(p["id"]): d for p, d in zip(extra, dists)
                         if p.get("id") is not None}
                ledger += build_ledger(extra, t, alias=alias,
                                       channel="discovery_seed", scores=by_id)
        except Exception:
            logger.warning("analyze: seed reuse failed for %s", t, exc_info=True)

        a.ledger = ledger
        if ledger:
            a.verdict = adjudicate(ledger, a.prices or None, a.volumes or None,
                                   benchmark_prices=a.benchmark_prices,
                                   benchmark=a.benchmark, bar_date=a.bar_date)
    except Exception as e:
        logger.exception("analyze: adjudication failed for %s", t)
        # NOT a return, and that is the whole point. The portal has always
        # treated a failed ledger as a DEGRADATION: it still renders the older
        # prose summary, still keeps the credit, and still records the row
        # under a `|legacy` discriminator. Returning here delivered a paid
        # analysis that wrote nothing to either table -- and X's index is 7
        # days deep, so a row not written now can never be reconstructed.
        a.error = f"{type(e).__name__}: {e}"
        a.raised = True
        a.verdict = None

    if a.verdict is None:
        if a.error is None:
            a.error = "no usable evidence"
        try:
            from utils.deep_analysis import generate_ai_summary
            a.legacy_summary = generate_ai_summary(a.analysis_results) or {}
        except Exception:
            logger.warning("analyze: legacy summary failed for %s", t, exc_info=True)

    try:
        # The tilt is gated on a Buy, so the projection is NOT comparable across
        # verdicts. signal_log records quality_gated for exactly that reason.
        # GUARDED ON PRICES, as the page is. Unguarded, simple_projection([])
        # returns an error dict carrying suggested_hold_days=0 and
        # success_rate=0.0 -- which land in verdict_log as measured zeros where
        # the portal writes NULL, and are indistinguishable afterwards.
        # THE GUARD IS NOT COMPLETE, and saying so is the point: with prices
        # present but fewer than 5 bars, or non-finite closes, that same error
        # dict comes back and those zeros ARE written. Byte-identical to the
        # page this replaced, so not a regression -- but it is a live hole in
        # a column meant to distinguish "absent" from "measured".
        if a.prices:
            if a.verdict is not None:
                q_ok = (a.verdict.quality.tier in ("moderate", "high")
                        and a.verdict.recommendation == "Buy")
                direction = a.verdict.social.direction
            else:
                # The legacy gate: an evidence-COUNT proxy, because the legacy
                # adjudicator produces no quality tier. Weaker than the tier
                # test above and deliberately kept as it was -- this path is a
                # fallback, not a second product.
                q_ok = unique_mentions(a) >= 8
                direction = a.legacy_summary.get("avg_sentiment") or 0.0
            a.projection = simple_projection(a.prices, direction,
                                             days=DEFAULT_HORIZON_DAYS,
                                             quality_ok=q_ok)
    except Exception:
        logger.warning("analyze: projection failed for %s", t, exc_info=True)

    return a


def persist(a: Analysis, *, feature: str, event_id: str | None = None,
            model: str | None = None, route: str | None = None) -> None:
    """Write the verdict and the full signal state. Never raises into a caller.

    Separate from analyze() because a service may want the analysis without the
    telemetry (a dry run, a replay), and because a logging failure must never
    turn a delivered analysis into an error.

    WHAT A ROW MEANS CHANGED AT THE CUTOVER, and it is worth being exact.

    While the portal ran the analysis itself, this was the last statement on the
    main thread and it only ran after the panel had rendered -- so a row implied
    a DELIVERED analysis, and a refund implied no row. core-api writes before it
    can respond, so the portal may still refund afterwards: the user aborted
    mid-render, the call timed out, the response was unusable. The row stands.

    So a row now means COMPUTED, not DELIVERED. For scoring verdicts against
    forward returns that is the better population -- it is not conditioned on
    what the user did next. For "was this debit honoured?" it is not enough on
    its own, and the answer is a join: a refund records the original event as
    `original_event_id` in usage_events, so

        verdict_log.event_id NOT IN (refunded original_event_ids)

    is the delivered cohort. Nothing is lost; it takes two tables to ask.

    The alternative -- have the service withhold the write until the portal
    confirms delivery -- was rejected deliberately. It trades a row that exists
    for an analysis nobody saw (harmless to scoring, detectable by join) for a
    MISSING row after a delivered analysis, and X's 7-day index means a missing
    row can never be rebuilt. Everything else in this module errs the same way.
    """
    # NOT `if not a.ok`. The legacy path -- no verdict, prose summary rendered,
    # credit kept -- is a DELIVERED analysis and has always been recorded, under
    # a `|legacy` discriminator that exists for no other purpose. Skipping it
    # left a debited usage_events row with no verdict_log row to reconcile
    # against, on exactly the runs a user is most likely to dispute.
    if a.verdict is None and not a.legacy_summary:
        return
    if model is None:
        try:
            from utils.sentiment import MODEL_NAME
            model = MODEL_NAME
        except Exception:
            model = "unknown"

    v = a.verdict
    leg = a.legacy_summary or {}
    # Which adjudicator produced this row. Without it the same columns hold
    # different quantities on different runs and nothing downstream can
    # separate them.
    # "the cascade crashed" and "the ledger was thin" are different events and
    # were being recorded identically. A cascade failing on every request then
    # looks like a run of quiet tickers, which is the one thing this column
    # exists to make visible.
    branch = ("ledger" if v is not None
              else "legacy_error" if a.raised else "legacy")
    # verdict_log has no `feature` column, so `model` is the ONLY thing that can
    # separate entry routes in it. Discovery has always appended its own tag and
    # keeps it; dropping it here would merge two cohorts with different
    # selection biases -- a basket query and a typed ticker -- into one column
    # with no way back. signal_log stores `feature` as well and does not need it.
    if route:
        branch = f"{branch}|{route}"
    try:
        from utils import verdict_log
        verdict_log.record(
            a.ticker,
            v.recommendation if v is not None else leg.get("recommendation", ""),
            sector=a.sector or None,
            confidence=v.confidence if v is not None else leg.get("confidence"),
            avg_sentiment=(v.social.direction if v is not None
                           else leg.get("avg_sentiment")),
            # The legacy adjudicator DOES measure both -- it simply used not to
            # return them. NULL is reserved for genuinely absent readings, not
            # for readings the caller declined to plumb through.
            red_flag_rate=(v.risk.soft_rate if v is not None
                           else leg.get("red_flag_rate")),
            disagreement=((1.0 if v.social.conflict else 0.0) if v is not None
                          else leg.get("disagreement")),
            # THE SAME POPULATION THE PORTAL COUNTS: unique post ids across the
            # analysis angles, i.e. the retrieved corpus. len(ledger) is
            # evidence rows including reused seed, a different quantity in the
            # same column -- and an `or` fallback to it put both populations in
            # the column this comment exists to protect. A true zero is a
            # measurement and stays zero.
            # A LEGACY ROW WITH total_mentions=0 IS A RULE DEFAULT, NOT A
            # READING. On a total X outage run_deep_analysis returns a full
            # eight-angle dict of error stubs, and generate_ai_summary's
            # "fewer than 8 mentions" rule then hands back Watch/Low/0.0 from
            # nothing at all. The row is still written -- the credit was spent
            # and the reconciliation matters -- but any scoring of the legacy
            # cohort must filter total_mentions > 0 or it will be measuring
            # the emptiness rule.
            total_mentions=unique_mentions(a),
            price_at_verdict=a.last_close,
            projected_p10=a.projection.get("gain_p10"),
            projected_p90=a.projection.get("gain_p90"),
            suggested_hold_days=a.projection.get("suggested_hold_days"),
            success_rate=a.projection.get("success_rate"),
            # BYTE-IDENTICAL to the signal_log discriminator below. The portal
            # keeps them equal on purpose so the two tables can be joined on
            # (ticker, model); appending the feature here broke that, and
            # signal_log already has a feature column.
            event_id=event_id, model=f"{model}|{branch}")
    except Exception:
        logger.warning("analyze: verdict_log failed", exc_info=True)

    # signal_log is ledger-only, as it has always been: every column in it
    # describes cascade state -- pillars, clusters, branch -- that the legacy
    # adjudicator never computes. A row of NULLs there is worse than no row.
    if v is None:
        return

    try:
        from utils import signal_log
        signal_log.record(
            a.ticker, a.verdict, feature=feature,
            price_at_decision=a.last_close, decision_trade_date=a.bar_date,
            sector=(a.sector if a.sector and a.sector != "unknown" else None),
            # Byte-identical to the verdict_log discriminator above -- the
            # two tables are joined on (ticker, model), so this is written
            # from the same expression rather than kept equal by hand.
            model=f"{model}|{branch}", event_id=event_id,
            corpus_key=a.corpus.get("corpus_key"),
            evidence_age_s=a.corpus.get("corpus_age_s"),
            ledger=a.ledger, projection=a.projection,
            wire_posts=a.corpus.get("influencer_corpus"),
            main_posts=a.corpus.get("ticker_corpus"),
            wire_state=a.corpus.get("wire_state"),
            wire_billed=a.corpus.get("wire_billed"),
            prices=a.prices or None, volumes=a.volumes or None,
            benchmark_prices=a.benchmark_prices, benchmark=a.benchmark)
    except Exception:
        logger.warning("analyze: signal_log failed", exc_info=True)
