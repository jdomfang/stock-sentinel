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

    @property
    def ok(self) -> bool:
        return self.error is None and self.verdict is not None


def _fmt_pct(x, nd=1) -> str:
    return "Unavailable" if x is None else f"{x:+.{nd}f}%"


def card(a: Analysis) -> dict:
    """The rendered card, as data. One producer, so renderers cannot disagree.

    Every string a user reads about the verdict is built here, from the state
    that produced it. A renderer that invents a label is reintroducing the bug
    this exists to remove.
    """
    if not a.ok:
        return {"ticker": a.ticker, "error": a.error or "analysis unavailable"}

    v, proj = a.verdict, (a.projection or {})
    five = (proj.get("movement_profile") or {}).get("5%") or {}
    mae = five.get("mae_p75")

    tiles = [{"label": "Last Price",
              "value": "Unavailable" if a.last_close is None else f"${a.last_close:.2f}"}]
    if proj.get("error") is None and proj.get("band") is not None:
        tiles.append({"label": "30d range (vol)",
                      "value": f"{proj['scenario_bear']:.1f}% to {proj['scenario_bull']:.1f}%"})
        tiles.append({"label": "Drawdown first",
                      "value": ("under 0.1%" if (mae is not None and mae < 0.001)
                                else "Unavailable" if mae is None
                                else f"-{mae * 100:.1f}%")})

    return {
        "ticker": a.ticker,
        "sector": a.sector or None,
        "verdict": v.recommendation,
        "confidence": v.confidence,
        # NOT a lookup on the verdict word. These read the state, which is the
        # entire reason the card is built here and not in a renderer.
        "headline": {"Buy": "Evidence leans upside",
                     "Watch": "Hold — monitor closely",
                     "Avoid": "Risk outweighs reward"}.get(v.recommendation, ""),
        "confidence_note": {"High": "Strong data backing",
                            "Moderate": "Highest we issue — unvalidated",
                            "Low": "Thin data — use caution"}.get(v.confidence, ""),
        "reason": v.reason,
        "branch": v.branch,
        "would_change": list(v.would_change),
        "confidence_notes": list(v.confidence_notes),
        "pillars": [{"name": p.name, "passed": p.passed, "value": p.value,
                     "requirement": p.requirement, "blocks_buy": p.blocks_buy}
                    for p in v.pillars],
        "tiles": tiles,
        "evidence": {
            "independent_voices": v.quality.eligible_clusters,
            "own_voices": v.own_clusters,
            "quality": round(v.quality.score, 3),
            "quality_tier": v.quality.tier,
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
        return a
    a.corpus = sink

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
        a.error = f"{type(e).__name__}: {e}"
        return a

    if a.verdict is None:
        a.error = "no usable evidence"
        return a

    try:
        # The tilt is gated on a Buy, so the projection is NOT comparable across
        # verdicts. signal_log records quality_gated for exactly that reason.
        # GUARDED ON PRICES, as the page is. Unguarded, simple_projection([])
        # returns an error dict carrying suggested_hold_days=0 and
        # success_rate=0.0 -- which land in verdict_log as measured zeros where
        # the portal writes NULL, and are indistinguishable afterwards.
        if a.prices:
            q_ok = (a.verdict.quality.tier in ("moderate", "high")
                    and a.verdict.recommendation == "Buy")
            a.projection = simple_projection(a.prices, a.verdict.social.direction,
                                             days=DEFAULT_HORIZON_DAYS,
                                             quality_ok=q_ok)
    except Exception:
        logger.warning("analyze: projection failed for %s", t, exc_info=True)

    return a


def persist(a: Analysis, *, feature: str, event_id: str | None = None,
            model: str | None = None) -> None:
    """Write the verdict and the full signal state. Never raises into a caller.

    Separate from analyze() because a service may want the analysis without the
    telemetry (a dry run, a replay), and because a logging failure must never
    turn a delivered analysis into an error.
    """
    if not a.ok:
        return
    if model is None:
        try:
            from utils.sentiment import MODEL_NAME
            model = MODEL_NAME
        except Exception:
            model = "unknown"

    try:
        from utils import verdict_log
        verdict_log.record(
            a.ticker, a.verdict.recommendation, sector=a.sector or None,
            confidence=a.verdict.confidence,
            avg_sentiment=a.verdict.social.direction,
            red_flag_rate=a.verdict.risk.soft_rate,
            disagreement=1.0 if a.verdict.social.conflict else 0.0,
            # THE SAME POPULATION THE PORTAL COUNTS: unique post ids across the
            # analysis angles, i.e. the retrieved corpus. len(ledger) is
            # evidence rows including reused seed, a different quantity in the
            # same column -- and months of rows would silently mix the two.
            total_mentions=len({str(t) for r in (a.analysis_results or {}).values()
                                for t in (r.get("tweet_ids") or [])}) or len(a.ledger),
            price_at_verdict=a.last_close,
            projected_p10=a.projection.get("gain_p10"),
            projected_p90=a.projection.get("gain_p90"),
            suggested_hold_days=a.projection.get("suggested_hold_days"),
            success_rate=a.projection.get("success_rate"),
            # BYTE-IDENTICAL to the signal_log discriminator below. The portal
            # keeps them equal on purpose so the two tables can be joined on
            # (ticker, model); appending the feature here broke that, and
            # signal_log already has a feature column.
            event_id=event_id, model=f"{model}|ledger")
    except Exception:
        logger.warning("analyze: verdict_log failed", exc_info=True)

    try:
        from utils import signal_log
        signal_log.record(
            a.ticker, a.verdict, feature=feature,
            price_at_decision=a.last_close, decision_trade_date=a.bar_date,
            sector=(a.sector if a.sector and a.sector != "unknown" else None),
            model=f"{model}|ledger", event_id=event_id,
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
