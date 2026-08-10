"""Six modules over the evidence ledger. Each one judges a single question.

The old adjudicator collapsed a corpus into ONE scalar and tested it against
two thresholds, so genuine discussion, a spam campaign and posts about Indian
politics all produced the identical verdict. These modules exist so that
different kinds of evidence reach the decision by different routes:

  quality   is this corpus about the company at all
  social    which way do the people talking about it lean
  risk      is there confirmed downside
  catalyst  did something actually happen
  price     does the tape contradict the story
  newswire  did an authority file anything

Nothing here decides the verdict; the cascade does. Each module answers, and
says how confident it is entitled to be.

CALIBRATION STATUS. Every constant in this file is PROVISIONAL. They separate
the three corpora we hold and have been fitted to nothing else. The labelling
work in Phase 7 replaces them with measurements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from utils.evidence import EvidenceRow

logger = logging.getLogger(__name__)

# --- Quality tiers, placed against what this code actually produces ---------
#
# Measured on the three retained corpora: genuine 0.383, spam 0.000,
# wrong-entity 0.110. Separation A-B +0.383 and A-C +0.273, both clearing the
# 0.25 bar.
#
# The MODERATE bar sits outside the noise band, which an earlier placement did
# not: leave-one-out over the genuine corpus spans 0.351-0.389 and 200 90%
# subsamples span 0.324-0.416, so the corpus is Moderate in 200/200 of them.
# A boundary a single post can straddle is a coin flip between two products,
# since Moderate and Low are displayed differently by design.
#
# Still a gate that separates known controls, not calibrated truth.
QUALITY_REJECT = 0.10
QUALITY_MODERATE = 0.30
QUALITY_HIGH = 0.55

DIRECTION_GATE = 0.15          # |effective direction| to count as a lean
CONFLICT_MIN_CLUSTERS = 3      # per channel, before disagreement can veto
PRICE_CAUTION_RETURN = -0.15
PRICE_CAUTION_VOLUME = 1.5


def _clusters(rows: Sequence[EvidenceRow]) -> int:
    """Distinct independent voices, keyed by CHANNEL AND id.

    assign_clusters restarts at 0 for every build_ledger call, and the design
    retrieves newswire separately, so concatenating two ledgers collides their
    ids. Measured consequence: two 2-post soft-risk ledgers merged to 2 clusters
    instead of 4 and risk_high flipped True -> False, removing the Avoid path
    without a trace.
    """
    return len({(r.channel, r.cluster_id) for r in rows})


def _social_rows(rows: Sequence[EvidenceRow]) -> list[EvidenceRow]:
    """Everything except the wire.

    The wire is factual and neutral by construction. Excluding it from
    _channel was not enough: it still fed quality, and through quality_w it
    shrank both social channels. Measured on the genuine corpus, adding 20 wire
    posts dropped quality 0.305 -> 0.230 (a whole tier) and made the conflict
    veto vanish, purely through that shrink.
    """
    return [r for r in rows if r.channel != "newswire"]


def _eligible(rows: Sequence[EvidenceRow]) -> list[EvidenceRow]:
    return [r for r in rows if r.evidence_eligible]


@dataclass
class Quality:
    score: float = 0.0
    aboutness: float = 0.0
    integrity: float = 0.0
    coverage: float = 0.0
    eligible_clusters: int = 0
    tier: str = "reject"       # reject | low | moderate | high
    detail: str = ""


def quality(rows: Sequence[EvidenceRow]) -> Quality:
    """Is this corpus about the company, and is it made of real voices?

    MULTIPLICATIVE, and that is the whole design. An additive form scored the
    wrong-entity corpus at 0.443 against the genuine corpus's 0.565 -- only 0.12
    apart, failing the 0.25 separation bar -- because 40% of the weight rewarded
    low spam-share and evidence diversity, and organic political news has both
    in abundance. Multiplying by aboutness makes wrong-entity fail FIRST, so
    hygiene can never rescue it.
    """
    rows = _social_rows(rows)
    n = len(rows)
    if not n:
        return Quality(detail="empty corpus")

    on_target = sum(1 for r in rows if r.target_match_type in ("cashtag", "alias"))
    elig = _eligible(rows)

    # TWO independent terms. Is the post attached to this company at all, and
    # did it survive into usable evidence. The earlier form added a third,
    # nested term (subject status, which eligibility already requires) and then
    # multiplied by a bare-only penalty applied to rows `precision` had already
    # excluded -- double-counting in both directions at once.
    precision = on_target / n
    yield_rate = len(elig) / n
    aboutness = 0.5 * precision + 0.5 * yield_rate

    sizes: dict[tuple, int] = {}
    for r in rows:
        key = (r.channel, r.cluster_id)
        sizes[key] = sizes.get(key, 0) + 1
    integrity = 1.0 - (max(sizes.values()) / n)

    kinds = {t for r in elig for t in r.evidence_types
             if t in ("directional_view", "risk", "catalyst", "trading")}
    coverage = len(kinds) / 4.0

    score = aboutness * (0.75 * integrity + 0.25 * coverage)
    tier = ("high" if score >= QUALITY_HIGH else
            "moderate" if score >= QUALITY_MODERATE else
            "low" if score >= QUALITY_REJECT else "reject")
    return Quality(round(score, 3), round(aboutness, 3), round(integrity, 3),
                   round(coverage, 3), _clusters(elig), tier,
                   f"{on_target}/{n} on target, {len(elig)} eligible, "
                   f"{_clusters(elig)} independent")


@dataclass
class Social:
    direction: float = 0.0
    cashtag_direction: float = 0.0
    alias_direction: float = 0.0
    cashtag_clusters: int = 0
    alias_clusters: int = 0
    conflict: bool = False
    minor_disagreement: bool = False
    lean: str = "neutral"      # positive | negative | neutral | conflicted | insufficient
    detail: str = ""


def _channel(rows: Sequence[EvidenceRow], match_type: Sequence[str]) -> tuple[float, int]:
    """(RAW cluster-mean direction, cluster count). No shrink applied here.

    Averaged per CLUSTER, then across clusters -- one independent voice, one
    vote. Averaging posts let eight near-duplicates at +0.90 outvote three
    independent voices at -0.40 and report POSITIVE, where one-voice-one-vote
    gives neutral. A medium-spam cluster is still eligible, so this was live.
    """
    sel = [r for r in _eligible(rows)
           if r.target_match_type in match_type
           and r.scored and "directional_view" in r.evidence_types]
    if not sel:
        return 0.0, 0
    by_cluster: dict[tuple, list[float]] = {}
    for r in sel:
        by_cluster.setdefault((r.channel, r.cluster_id), []).append(r.margin)
    means = [sum(v) / len(v) for v in by_cluster.values()]
    return sum(means) / len(means), len(means)


def social(rows: Sequence[EvidenceRow], q: float) -> Social:
    """Which way the talk leans -- from DIRECTIONAL posts only, by channel.

    Two channels, kept apart deliberately. Measured on the genuine corpus:
    cashtag-matched posts averaged +0.244 while alias-matched posts averaged
    -0.528. Trader chatter and press prose sample different populations, and
    when they disagree that is a finding, not noise to average away. So the
    disagreement is preserved and it blocks a Buy.

    The conflict veto needs >=3 clusters on BOTH sides. Without that floor a
    single bearish article could cancel fifteen independent trader posts.
    """
    soc = _social_rows(rows)
    # Bare matches join the trader channel. Phase 2 gives a bare post a route to
    # primary only when it also carries a confirmed event, which is press copy
    # written without a cashtag; dropping it discarded a +0.93 row from the
    # genuine corpus with nothing logged.
    cash_raw, cash_cl = _channel(soc, ("cashtag", "bare"))
    alias_raw, alias_cl = _channel(soc, ("alias",))

    quality_w = min(1.0, q / 0.40) if q > 0 else 0.0
    shrink = lambda raw, cl: raw * min(1.0, (cl / 5.0) ** 0.5) * quality_w
    cash_dir, alias_dir = shrink(cash_raw, cash_cl), shrink(alias_raw, alias_cl)

    conflict = (cash_cl >= CONFLICT_MIN_CLUSTERS and alias_cl >= CONFLICT_MIN_CLUSTERS
                and abs(cash_dir) >= DIRECTION_GATE and abs(alias_dir) >= DIRECTION_GATE
                and cash_dir * alias_dir < 0)
    minor = (not conflict and cash_raw * alias_raw < 0
             and (abs(cash_dir) >= DIRECTION_GATE or abs(alias_dir) >= DIRECTION_GATE))

    total = cash_cl + alias_cl
    if conflict or not total:
        direction = 0.0
    else:
        # Combine the RAW means, then shrink ONCE on the TOTAL evidence.
        # Shrinking per channel and then weighting by cluster count again
        # applied volume at power 1.5, so two channels that AGREE perfectly lost
        # 20-28% of their lean purely because the evidence arrived split -- the
        # very structure the design mandates.
        combined = (cash_raw * cash_cl + alias_raw * alias_cl) / total
        direction = combined * min(1.0, (total / 5.0) ** 0.5) * quality_w

    lean = ("conflicted" if conflict else
            "insufficient" if not total else
            "positive" if direction >= DIRECTION_GATE else
            "negative" if direction <= -DIRECTION_GATE else "neutral")
    bits = []
    if cash_cl:
        bits.append(f"traders {cash_dir:+.2f} ({cash_cl})")
    if alias_cl:
        bits.append(f"press {alias_dir:+.2f} ({alias_cl})")
    if conflict:
        bits.append("CONFLICT")
    elif minor:
        bits.append("minor disagreement")
    return Social(round(direction, 3), round(cash_dir, 3), round(alias_dir, 3),
                  cash_cl, alias_cl, conflict, minor, lean, " · ".join(bits) or "no directional evidence")


@dataclass
class Risk:
    high: bool = False
    severe_clusters: int = 0
    soft_clusters: int = 0
    soft_rate: float = 0.0
    from_newswire: bool = False
    items: list[str] = field(default_factory=list)
    detail: str = ""


def risk(rows: Sequence[EvidenceRow]) -> Risk:
    """Confirmed downside, counted in independent voices.

    One severe item is enough. Soft language needs three independent voices AND
    a fifth of the eligible corpus, because hedging vocabulary is ambient on
    finance X and a lower bar simply returns Avoid for every liquid ticker.
    """
    elig = _eligible(rows)
    severe = [r for r in elig if r.risk_severity == "severe"]
    soft = [r for r in elig if r.risk_severity == "soft"]
    sev_cl, soft_cl = _clusters(severe), _clusters(soft)
    # Clusters over clusters. Mixing a post count with a cluster threshold let
    # one five-post cluster read as five twentieths of the corpus.
    rate = (soft_cl / _clusters(elig)) if elig else 0.0

    high = sev_cl >= 1 or (soft_cl >= 3 and rate >= 0.20)
    items = [r.text[:110] for r in severe[:3]] or [r.text[:110] for r in soft[:3]]
    return Risk(high, sev_cl, soft_cl, round(rate, 3),
                any(r.channel == "newswire" for r in (severe or soft)), items,
                f"{sev_cl} severe, {soft_cl} soft ({rate:.0%})" if elig else "no eligible evidence")


@dataclass
class Catalyst:
    present: bool = False
    hard_clusters: int = 0
    soft_clusters: int = 0
    soft_authors: int = 0
    from_newswire: bool = False
    items: list[str] = field(default_factory=list)
    detail: str = ""


def catalyst(rows: Sequence[EvidenceRow]) -> Catalyst:
    """Did something actually happen.

    Soft catalysts need three independent voices AND two distinct authors: a
    single account repeating "possible catalyst" across a thread is one opinion,
    not corroboration.
    """
    elig = _eligible(rows)
    hard = [r for r in elig if r.catalyst_severity == "hard"]
    soft = [r for r in elig if r.catalyst_severity == "soft"]
    hard_cl, soft_cl = _clusters(hard), _clusters(soft)
    authors = len({r.author_id for r in soft if r.author_id})

    if soft and not authors:
        # X only returns author_id when tweet.fields asks for it. Without the
        # fallback a retrieval change disables soft catalysts entirely, silently.
        logger.warning("catalyst: %d soft items carry no author_id; "
                       "falling back to cluster count", len(soft))
        authors = soft_cl
    present = hard_cl >= 1 or (soft_cl >= 3 and authors >= 2)
    items = [r.text[:110] for r in hard[:3]] or [r.text[:110] for r in soft[:3]]
    return Catalyst(present, hard_cl, soft_cl, authors,
                    any(r.channel == "newswire" for r in hard), items,
                    f"{hard_cl} confirmed, {soft_cl} unconfirmed"
                    if elig else "no eligible evidence")


@dataclass
class Price:
    status: str = "missing"       # neutral | caution | missing
    return_20d: float | None = None
    volume_ratio: float | None = None
    caution: bool = False         # can VETO. Never an endorsement.
    detail: str = ""


def price(prices: Sequence[float] | None,
          volumes: Sequence[float] | None = None,
          window: int = 21) -> Price:
    """Does the tape contradict the story. It can veto; it can never endorse.

    The earlier field was called `buy_allowed`, which read as a positive
    finding from a module that has no positive finding to give -- and it encoded
    only two of the spec's three conditions, having no sight of the catalyst
    exemption. `caution` is the honest name; the cascade applies the exemption.

    MISSING DATA IS NOT CAUTION. Absent volume used to satisfy the volume
    condition, so having no data made the veto EASIER and acquiring real data
    made the system less careful -- a direct inversion of the spec, which says
    price stays neutral when the call is unavailable.
    """
    import math

    clean = [float(p) for p in (prices or [])
             if isinstance(p, (int, float)) and math.isfinite(float(p)) and float(p) > 0]
    if len(clean) < 5:
        return Price(detail="no usable price history")

    # Windowed and explicitly oldest-first. An unwindowed first-to-last read a
    # 60-day return as a 20-day one, and a newest-first series silently flipped
    # the sign -- turning a -19% tape into +23% and clearing the veto.
    w = clean[-window:]
    r20 = (w[-1] - w[0]) / w[0]

    vratio = None
    vclean = [float(v) for v in (volumes or [])
              if isinstance(v, (int, float)) and math.isfinite(float(v)) and float(v) >= 0]
    if len(vclean) >= 5:
        base = sorted(vclean[-window:-1])
        if base:
            # MEDIAN, not mean. One 10x day inside the baseline dragged a true
            # 2.7x ratio down to 1.36 and cleared the veto on a -20% tape.
            mid = base[len(base) // 2] if len(base) % 2 else (
                (base[len(base) // 2 - 1] + base[len(base) // 2]) / 2)
            if mid > 0:
                vratio = vclean[-1] / mid

    # BOTH conditions, per the spec.
    caution = (r20 <= PRICE_CAUTION_RETURN
               and vratio is not None and vratio >= PRICE_CAUTION_VOLUME)
    return Price("caution" if caution else "neutral", round(r20, 4),
                 round(vratio, 2) if vratio is not None else None, caution,
                 f"{r20:+.1%} over {len(w)} sessions"
                 + (f", volume {vratio:.1f}x median" if vratio is not None
                    else ", volume unavailable"))


@dataclass
class Newswire:
    posts: int = 0
    hard_catalysts: int = 0
    severe_risks: int = 0
    detail: str = ""


def newswire(rows: Sequence[EvidenceRow]) -> Newswire:
    """The authority channel, reported separately and never averaged in.

    Its copy is factual and therefore neutral, so folding it into a directional
    mean dilutes exactly the signal it should sharpen -- measured at +0.244
    falling to +0.175, enough to flip a Buy to a Watch. One confirmed item here
    can trigger risk or satisfy catalyst on its own.
    """
    sel = [r for r in rows if r.channel == "newswire"]
    # ELIGIBLE only. Three wire posts about an Indian MP mentioning a lawsuit
    # and an upgrade were reporting 1 hard catalyst and 1 severe risk while
    # risk() and catalyst() correctly returned nothing -- and one such item is
    # enough to trigger either rule on its own.
    qual = _eligible(sel)
    hard = _clusters([r for r in qual if r.catalyst_severity == "hard"])
    sev = _clusters([r for r in qual if r.risk_severity == "severe"])
    return Newswire(len(sel), hard, sev,
                    f"{len(sel)} wire posts, {len(qual)} on target, "
                    f"{hard} confirmed events, {sev} filed risks"
                    if sel else "no wire coverage")
