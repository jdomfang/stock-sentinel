"""Persist the state the adjudicator SAW, not just the answer it gave.

WHY THIS EXISTS ALONGSIDE verdict_log

verdict_log answers "did Buy outperform Avoid". It cannot answer any question
that would change the product, because it records outputs and the product is
tuned on inputs. Which pillar was blocking the Buys that would have worked; is
the conflict veto saving us or costing us; is quality 0.30 the right line --
each needs the inputs beside the outcome.

And each needs the NEAR MISSES. A log of issued Buys has no comparison group: a
Buy cohort returning +2% means nothing without the Watches that nearly became
Buys. So this records every verdict, and a "rejected Buy candidate" becomes a
query rather than a definition frozen in code at write time.

WHAT THE CALLER PASSES

The Verdict object itself, not forty keyword arguments. Every page that
adjudicates already holds one, and enumerating its fields at each call site is
how two call sites drift apart -- which has already happened once in this repo,
between the price rows on Deep Analyze and Discovery.

FAILURE POLICY

Identical to verdict_log, and for the same reason: the user has paid and the
panel has rendered. Nothing here may raise into a caller, but everything IS
logged at WARNING, because a logger that silently stopped working looks exactly
like one with nothing to record.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Sequence

logger = logging.getLogger(__name__)

TABLE = "signal_log"

# Bump whenever utils/verdict.py's cascade or any threshold behind it changes.
# Every number in that cascade is provisional, so rows from before and after a
# change measure different things; without this they are indistinguishable and
# pooling them is silently wrong.
ADJUDICATOR_VERSION = "2026.08.17-ledger"

# Well under the 15s used elsewhere, and deliberately. This POST runs on the
# request path AFTER the panel has rendered but BEFORE the rest of the page,
# and Streamlit cannot interrupt a blocked urlopen -- it only notices a rerun at
# the next st.* call. Two 15s writes back to back is a 30s uninterruptible
# freeze on a page the user has already paid for. A dropped log line costs one
# row; a 30s stall costs the session.
TIMEOUT_S = 4


def _config(name: str, default: str = "") -> str:
    v = os.getenv(name, "")
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets.get(name, "") or "") or default
    except Exception:
        return default


def _num(v: Any) -> float | None:
    """Coerce to float, or None. Never raises, and never stores NaN/inf.

    numeric columns accept NaN in postgres and then poison every aggregate
    computed over them -- an avg() across a column with one NaN is NaN.
    """
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


def _bool(v: Any) -> bool | None:
    return bool(v) if isinstance(v, (bool, int)) else None


def _txt(v: Any, n: int) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s[:n] if s else None


def _first_failed_pillar(verdict) -> str | None:
    """The first blocking pillar that failed, in DISPLAY order.

    NOT the cause of the verdict, and deliberately not named as if it were.
    `pillars` is built for the reader, and its order is not the cascade's:
    "Independent sources" fails below 5 clusters while the cascade only needs 3,
    so a risk-caused Avoid on a 4-cluster corpus reports "Independent sources"
    here. It is also non-null under a green Buy -- one taking the catalyst
    exemption still fails the price pillar by design.

    `Verdict.branch` is the column to group by. This one is kept because it is
    what the user saw first, which is worth having when a call is disputed.
    """
    try:
        for p in (getattr(verdict, "pillars", None) or []):
            if not p.passed and p.blocks_buy:
                return _txt(p.name, 64)
    except Exception:
        pass
    return None


def _ledger_shape(rows: Sequence | None) -> dict:
    """Counts only. The rows themselves are the corpus cache's job, not this."""
    out = {
        "ledger_rows": None, "ledger_eligible": None, "ledger_scored": None,
        "ledger_spam_high": None, "rows_social": None, "rows_newswire": None,
        "rows_seed": None,
    }
    if not rows:
        return out
    try:
        out["ledger_rows"] = len(rows)
        out["ledger_eligible"] = sum(1 for r in rows if r.evidence_eligible)
        out["ledger_scored"] = sum(1 for r in rows if r.scored)
        out["ledger_spam_high"] = sum(1 for r in rows if r.spam_risk == "high")
        out["rows_social"] = sum(1 for r in rows if r.channel == "social_base")
        out["rows_newswire"] = sum(1 for r in rows if r.channel == "newswire")
        out["rows_seed"] = sum(1 for r in rows if r.channel == "discovery_seed")
    except Exception:
        logger.warning("signal_log: ledger shape failed", exc_info=True)
    return out


def _projection(proj: dict | None) -> dict:
    out = {
        "proj_band": None, "proj_horizon_days": None, "proj_mae_p75": None,
        "proj_up_first_5": None, "proj_up_rate_5": None, "proj_down_rate_5": None,
        "proj_quality_gated": None, "proj_sentiment_tilt": None,
    }
    if not proj:
        return out
    try:
        out["proj_band"] = _num(proj.get("band"))
        out["proj_horizon_days"] = _int(proj.get("decision_horizon_days"))
        # WITHOUT THESE TWO THE RATES BELOW ARE UNCOMPARABLE. The caller enables
        # the sentiment tilt only when the verdict is Buy, so the Buy cohort's
        # paths carry a bullish drift the Watch cohort's do not -- and a
        # group-by-verdict average would measure that flag, not the market.
        out["proj_quality_gated"] = _bool(proj.get("quality_gated"))
        out["proj_sentiment_tilt"] = _num(proj.get("sentiment_tilt"))
        five = (proj.get("movement_profile") or {}).get("5%") or {}
        out["proj_mae_p75"] = _num(five.get("mae_p75"))
        # None when nothing touched either side. Storing 0.5 there would record
        # a balanced reading where there was no reading at all. The two rates
        # below are always real numbers, so a 0.0 in them is a genuine zero.
        out["proj_up_first_5"] = _num(five.get("up_first_rate"))
        out["proj_up_rate_5"] = _num(five.get("up_rate"))
        out["proj_down_rate_5"] = _num(five.get("down_rate"))
    except Exception:
        logger.warning("signal_log: projection shape failed", exc_info=True)
    return out


def record(
    ticker: str,
    verdict,
    *,
    feature: str,
    price_at_decision: Any = None,
    sector: str | None = None,
    model: str | None = None,
    event_id: str | None = None,
    corpus_key: str | None = None,
    ledger: Sequence | None = None,
    projection: dict | None = None,
    decision_trade_date: str | None = None,
    evidence_age_s: Any = None,
    wire_posts: Sequence[dict] | None = None,
    main_posts: Sequence[dict] | None = None,
    wire_state: str | None = None,
    wire_billed: Any = None,
    prices: Sequence[float] | None = None,
    volumes: Sequence[float] | None = None,
    benchmark_prices=None,
    benchmark: str = "",
) -> bool:
    """Write one full signal state. Returns True on success; never raises.

    `verdict` is a utils.verdict.Verdict. `feature` separates deep_analyze from
    discovery, whose evidence comes from a basket query with a different
    selection bias -- pooling them without that column would be a silent error
    in every cohort comparison the table exists to support.
    """
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        # LOUD. This module's whole premise is that a logger which quietly
        # stopped looks exactly like one with nothing to record, and a missing
        # service-role key is precisely that failure.
        logger.warning("signal_log: no Supabase credentials; nothing recorded")
        return False
    if not ticker or verdict is None or not str(feature or "").strip():
        logger.warning("signal_log: refusing to write ticker=%r feature=%r "
                       "verdict=%s", ticker, feature, verdict is not None)
        return False

    def _get(obj, name, default=None):
        """One renamed attribute must not cost the other fifty columns.

        This table's value is entirely in months of accumulated rows. An
        all-or-nothing build meant a single field rename on Verdict dropped
        every row from that moment on, with a WARNING nobody is watching.
        """
        try:
            return getattr(obj, name)
        except Exception:
            return default

    try:
        q, s = verdict.quality, verdict.social
        r, c = verdict.risk, verdict.catalyst
        p, w = verdict.price, verdict.newswire

        row = {
            "event_id": event_id or None,
            "corpus_key": _txt(corpus_key, 128),
            "feature": _txt(feature, 32),
            "ticker": str(ticker).upper()[:16],
            "sector": _txt(sector, 64),
            "model": _txt(model, 64),

            "verdict": _txt(verdict.recommendation, 16),
            "confidence": _txt(verdict.confidence, 16),
            "earned_confidence": _txt(verdict.earned_confidence, 16),
            # Long enough for the real sentences the adjudicator produces, and
            # truncated rather than dropped so a long reason is still queryable.
            "reason": _txt(verdict.reason, 1000),
            "branch": _txt(_get(verdict, "branch"), 64),
            "first_failed_pillar": _first_failed_pillar(verdict),
            "adjudicator_version": ADJUDICATOR_VERSION,
            "price_at_decision": _num(price_at_decision),
            # The session price_at_decision belongs to. Without it a verdict
            # issued outside market hours joins no price_history row at all.
            "decision_trade_date": _txt(decision_trade_date, 10),
            "evidence_age_s": _int(evidence_age_s),

            "quality_score": _num(q.score),
            "quality_tier": _txt(q.tier, 16),
            "quality_aboutness": _num(q.aboutness),
            "quality_integrity": _num(q.integrity),
            "quality_coverage": _num(q.coverage),
            "eligible_clusters": _int(q.eligible_clusters),
            "own_clusters": _int(_get(verdict, "own_clusters")),
            "seed_only": _bool(_get(verdict, "seed_only")),
            "confidence_notes": _txt(
                "; ".join(_get(verdict, "confidence_notes") or []), 500),

            "social_direction": _num(s.direction),
            "cashtag_direction": _num(s.cashtag_direction),
            "alias_direction": _num(s.alias_direction),
            "cashtag_clusters": _int(s.cashtag_clusters),
            "alias_clusters": _int(s.alias_clusters),
            "social_conflict": _bool(s.conflict),
            "social_minor_disagreement": _bool(s.minor_disagreement),
            "social_lean": _txt(s.lean, 16),

            "risk_high": _bool(r.high),
            "risk_severe_clusters": _int(r.severe_clusters),
            "risk_soft_clusters": _int(r.soft_clusters),
            "risk_soft_rate": _num(r.soft_rate),
            "risk_from_newswire": _bool(r.from_newswire),

            "catalyst_present": _bool(c.present),
            "catalyst_hard_clusters": _int(c.hard_clusters),
            "catalyst_hard_direction": _num(c.hard_direction),
            "catalyst_hard_scored": _int(_get(c, "hard_scored")),
            "catalyst_soft_clusters": _int(c.soft_clusters),
            "catalyst_soft_authors": _int(_get(c, "soft_authors")),
            "catalyst_from_newswire": _bool(c.from_newswire),

            "price_status": _txt(p.status, 16),
            "price_return_20d": _num(p.return_20d),
            "price_volume_ratio": _num(p.volume_ratio),
            "price_caution": _bool(p.caution),
            # Sector context. `excess` is the number that decides whether a
            # decline was about this company, and `benchmark` names what it was
            # judged against -- the ETF map is expected to change, and a stored
            # comparison whose benchmark is unknown cannot be re-read later.
            "price_sector_return_20d": _num(_get(p, "sector_return_20d")),
            "price_excess_return_20d": _num(_get(p, "excess_return_20d")),
            "price_benchmark": _txt(_get(p, "benchmark"), 16),
            # The window the excess was measured over. Pooling a 7-session
            # excess with a 21-session one in a single column makes the
            # calibration these fields exist for silently wrong.
            "price_excess_window": _int(_get(p, "excess_window")),
            "price_excess_noise": _num(_get(p, "excess_noise")),

            # `w.posts` is not written: it is len(rows with channel="newswire"),
            # which is exactly what _ledger_shape already stores as
            # rows_newswire. Two columns holding the same number by
            # construction is two chances to read the wrong one.
            "newswire_hard_catalysts": _int(w.hard_catalysts),
            "newswire_severe_risks": _int(w.severe_risks),
        }
        row.update(_ledger_shape(ledger))
        row.update(_projection(projection))
    except Exception:
        # A shape change in Verdict must not take down a page that has already
        # delivered. Loud, because this is the failure that would otherwise
        # look exactly like "no analyses were run".
        logger.warning("signal_log: could not build row", exc_info=True)
        return False

    # ITS OWN TRY, outside the block above. The newswire measurement is the
    # newest and least-exercised code here, and sitting inside the main build it
    # could discard a fully-assembled sixty-column row over an optional
    # counter -- the exact failure this module's `_get` helper exists to
    # prevent. The import is inside too: an unshipped new file is the realistic
    # way this breaks.
    try:
        from utils import newswire as _nw
        # The SAME prices the real verdict saw. Without them the counterfactual
        # hits the cascade's fail-closed price_missing branch and every Buy
        # reads as wire-caused.
        _wire = _nw.measure(ledger, wire_posts=wire_posts, main_posts=main_posts,
                            wire_state=wire_state, wire_billed=wire_billed,
                            prices=prices, volumes=volumes,
                            benchmark_prices=benchmark_prices,
                            benchmark=benchmark,
                            bar_date=decision_trade_date)
        row["newswire_state"] = _txt(_wire.pop("newswire_state", None), 24)
        row["verdict_without_wire"] = _txt(_wire.pop("verdict_without_wire", None), 16)
        row.update({k: _int(v) for k, v in _wire.items()})
        logger.info("📰 %s", _nw.summarise(
            {**_wire, "newswire_state": row["newswire_state"],
             "verdict_without_wire": row["verdict_without_wire"]}))
    except Exception:
        logger.warning("signal_log: newswire measurement skipped", exc_info=True)

    req = urllib.request.Request(
        f"{base}/rest/v1/{TABLE}",
        data=json.dumps([row]).encode(),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            if resp.status not in (200, 201, 204):
                logger.warning("signal_log HTTP %s", resp.status)
                return False
    except urllib.error.HTTPError as e:
        logger.warning("signal_log HTTP %s: %s", e.code,
                       (e.read() or b"")[:200].decode(errors="replace"))
        return False
    except Exception as e:
        logger.warning("signal_log failed: %s: %s", type(e).__name__, str(e)[:160])
        return False

    logger.info("🧾 signal_log: %s %s/%s via %s (branch=%s, bar=%s)",
                row["ticker"], row["verdict"], row["confidence"],
                row["feature"], row["branch"], row["decision_trade_date"])
    return True
