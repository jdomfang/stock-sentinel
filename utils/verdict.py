"""The adjudicator: module outputs in, one of three words out.

WHAT IS DIFFERENT FROM WHAT THIS REPLACES

The old cascade compared a single scalar against two thresholds. Genuine
discussion, a 100-account spam campaign and posts about Indian politics all
produced "Watch / Moderate", and the rationale beneath was written prose that
could -- and did -- contradict the numbers above it: "sentiment is largely
neutral" printed directly beside "all 2 analysis signals point bearish".

Here the rationale IS the cascade state. Every pillar records what it required,
what it got, and whether it passed, and the "what would change this" line is
assembled from the pillars that failed. A contradiction between the explanation
and the decision is not unlikely, it is unrepresentable.

TWO ROUTES TO BUY, confirmed as the intended design: a confirmed event can
carry a Buy on its own, and so can clean positive sentiment that the tape is
not contradicting. Everything else is Watch.

CALIBRATION STATUS. Every threshold here is PROVISIONAL and HIGH CONFIDENCE IS
UNREACHABLE BY CONSTRUCTION until labels and forward outcomes exist. A system
that has never been checked against what the stocks did has not earned the word.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from utils import modules as M
from utils.evidence import EvidenceRow

logger = logging.getLogger(__name__)

MIN_CLUSTERS_MODERATE = 5
MIN_CLUSTERS_HIGH = 10
SPAM_SHARE_CAP = 0.35

# High is computed so the gap to it is visible in telemetry, and then withheld.
ALLOW_HIGH = False


@dataclass
class Pillar:
    """One gate: what it needed, what it got, whether it passed."""

    name: str
    passed: bool
    value: str
    requirement: str
    blocks_buy: bool = False

    def __str__(self) -> str:
        return f"{'PASS' if self.passed else 'FAIL'} {self.name}: {self.value}"


@dataclass
class Verdict:
    recommendation: str = "Watch"
    confidence: str = "Low"
    earned_confidence: str = "Low"      # before caps, for telemetry
    confidence_notes: list[str] = field(default_factory=list)
    pillars: list[Pillar] = field(default_factory=list)
    would_change: list[str] = field(default_factory=list)
    reason: str = ""
    quality: M.Quality = field(default_factory=M.Quality)
    social: M.Social = field(default_factory=M.Social)
    risk: M.Risk = field(default_factory=M.Risk)
    catalyst: M.Catalyst = field(default_factory=M.Catalyst)
    price: M.Price = field(default_factory=M.Price)
    newswire: M.Newswire = field(default_factory=M.Newswire)

    @property
    def failed(self) -> list[Pillar]:
        return [p for p in self.pillars if not p.passed]


def _confidence(q: M.Quality, s: M.Social, rows: Sequence[EvidenceRow],
                ) -> tuple[str, str, list[str]]:
    """(level, earned_before_caps, notes). Volume alone is never confidence.

    The old scheme awarded points for post count, which is how 100 spam posts
    earned "Moderate" and the phrase "strong evidence base".

    `earned` is returned separately because caps used to overwrite it: the spam
    cap set level to Low, after which the conflict and withheld-High checks
    tested `level == "High"` and never fired, so a corpus that HAD earned High
    left no record of it. That was the one thing the withheld-High note existed
    to preserve.
    """
    notes: list[str] = []
    clusters = q.eligible_clusters

    if q.tier == "reject" or clusters < 3:
        return "Low", "Low", ["insufficient clean evidence"]

    if q.score >= M.QUALITY_HIGH and clusters >= MIN_CLUSTERS_HIGH:
        earned = "High"
    elif q.tier in ("moderate", "high") and clusters >= MIN_CLUSTERS_MODERATE:
        earned = "Moderate"
    else:
        earned = "Low"
    level = earned

    sizes: dict[tuple, int] = {}
    soc = [r for r in rows if r.channel != "newswire"]
    for r in soc:
        key = (r.channel, r.cluster_id)
        sizes[key] = sizes.get(key, 0) + 1
    if soc and max(sizes.values()) / len(soc) > SPAM_SHARE_CAP:
        level = "Low"
        notes.append("one voice dominates the corpus")

    if s.conflict and level != "Low":
        level = "Moderate"
        notes.append("trader and press evidence disagree")
    elif s.minor_disagreement and level != "Low":
        level = "Moderate"
        notes.append("some counter-evidence")

    if level == "High" and not ALLOW_HIGH:
        level = "Moderate"
        notes.append("High is withheld until verdicts have been checked against outcomes")
    if earned == "High" and level != "High" and "High is withheld" not in " ".join(notes):
        notes.append("would otherwise have reached High")
    return level, earned, notes


def adjudicate(rows: Sequence[EvidenceRow],
               prices: Sequence[float] | None = None,
               volumes: Sequence[float] | None = None) -> Verdict:
    """One of three words, plus the state that produced it."""
    q = M.quality(rows)
    s = M.social(rows, q.score)
    r = M.risk(rows)
    c = M.catalyst(rows)
    p = M.price(prices, volumes)
    w = M.newswire(rows)

    v = Verdict(quality=q, social=s, risk=r, catalyst=c, price=p, newswire=w)

    # A corpus below the DECISION bar cannot support a call, and reading risk
    # or catalyst out of one re-admits the wrong-entity corpus -- which returns
    # catalyst_present=True at quality 0.11. Gating on `!= "reject"` did not do
    # that: 0.11 is tier "low", so the guard was inert against the exact corpus
    # its comment named.
    can_call = q.tier in ("moderate", "high")

    # The price veto has a catalyst exemption in the locked design and it
    # existed in neither module: a confirmed event on a -20% tape returned
    # Watch where the spec says Buy.
    # "unconfirmed" is a decline we could not corroborate with volume. It
    # blocks a sentiment-only Buy exactly as caution does; a confirmed event
    # still exempts, per the design.
    price_adverse = p.status in ("caution", "declining", "unconfirmed")
    price_blocks = price_adverse and c.hard_clusters < 1
    bullish_event = c.hard_clusters >= 1 and c.hard_direction >= 0

    v.pillars = [
        # Tests the bar that actually blocks a call, not the reject floor. The
        # readout previously printed ">= 0.10 to judge at all" while the Buy
        # branch required 0.30, so a corpus in between showed six green pillars
        # and named nothing standing in its way.
        Pillar("Evidence quality", can_call, f"{q.score:.2f} ({q.tier})",
               f">= {M.QUALITY_MODERATE:.2f} to support a call", blocks_buy=True),
        Pillar("Independent sources", q.eligible_clusters >= MIN_CLUSTERS_MODERATE,
               str(q.eligible_clusters), f">= {MIN_CLUSTERS_MODERATE}", blocks_buy=True),
        # No `usable` conjunct: a pass must never be printed beside a value that
        # is the thing the requirement forbids.
        Pillar("No disqualifying risk", not r.high, r.detail,
               "no confirmed severe item", blocks_buy=True),
        # hard_clusters, not `present` -- `present` includes soft catalysts, so
        # this pillar passed on "0 confirmed, 4 unconfirmed" beside a printed
        # requirement of ">= 1 confirmed event".
        Pillar("Confirmed catalyst", c.hard_clusters >= 1, c.detail,
               ">= 1 confirmed event"),
        # Covers BOTH downward routes. Social direction had no pillar at all,
        # so the Avoid it triggers rendered six passes and an empty remediation
        # list; the confirmed-bearish-event Avoid had the same hole, and the
        # "no remediation path" backstop below is what surfaced it.
        Pillar("Nothing points down",
               s.lean != "negative" and not (c.hard_clusters >= 1 and c.hard_direction < 0),
               s.detail + (f" · confirmed event {c.hard_direction:+.2f}"
                           if c.hard_clusters >= 1 else ""),
               "no negative lean and no bearish confirmed event", blocks_buy=True),
        Pillar("Crowds agree", not s.conflict, s.detail,
               "traders and press not opposed", blocks_buy=True),
        Pillar("Price not contradicting",
               not price_adverse and p.status != "missing", p.detail,
               "no material decline; price and volume data present",
               blocks_buy=True),
    ]

    # ---- The cascade. First match wins. ----
    if q.tier == "reject":
        v.recommendation, v.reason = "Watch", (
            "Not neutral conviction — there is not enough clean evidence about "
            "this company to judge.")
    elif q.eligible_clusters < 3:
        v.recommendation, v.reason = "Watch", (
            f"Only {q.eligible_clusters} independent source(s) survived filtering.")
    elif r.high:
        v.recommendation, v.reason = "Avoid", (
            f"Confirmed downside evidence ({r.detail})"
            + (" filed by a newswire source." if r.from_newswire else "."))
    elif can_call and s.lean == "negative":
        v.recommendation, v.reason = "Avoid", (
            f"Sentiment is negative ({s.direction:+.2f}) on evidence of adequate quality.")
    elif can_call and c.hard_clusters >= 1 and c.hard_direction < 0:
        # A confirmed BEARISH event. "Guidance cut", "downgrade" and "shares
        # plunge" are all hard catalysts; treating any of them as upside
        # recommended buying a guidance cut.
        v.recommendation, v.reason = "Avoid", (
            f"A confirmed event reads negative ({c.hard_direction:+.2f}).")
    elif s.conflict:
        v.recommendation, v.reason = "Watch", (
            "Trader chatter and press coverage point opposite ways. That "
            "disagreement is the finding; we will not average it away.")
    elif p.status == "missing":
        # FAILS CLOSED. Without price context no directional call is made.
        v.recommendation, v.reason = "Watch", (
            "No price context available, so no directional call is made.")
    elif not can_call:
        v.recommendation, v.reason = "Watch", (
            f"Evidence quality {q.score:.2f} is below the {M.QUALITY_MODERATE:.2f} "
            "needed to support a call.")
    elif q.eligible_clusters >= MIN_CLUSTERS_MODERATE and not price_blocks and (
            bullish_event or s.lean == "positive"):
        # TWO ROUTES, and the catalyst route now requires the event to READ
        # positive. A Buy also needs enough voices to be worth Moderate --
        # Buy/Low is not one of the four products the design describes.
        route = "a confirmed catalyst" if bullish_event else "positive sentiment"
        v.recommendation, v.reason = "Buy", (
            f"Evidence favours upside via {route}, with no disqualifying risk "
            "and price not contradicting.")
    else:
        v.recommendation, v.reason = "Watch", (
            "Real evidence, but it does not line up into a call.")

    v.confidence, v.earned_confidence, v.confidence_notes = _confidence(q, s, rows)

    # ---- What would change this. Assembled from the pillars that failed, for
    # every non-Buy verdict -- it used to run only for Watch, so an Avoid
    # rendered nothing at all.
    if v.recommendation != "Buy":
        remedy = {
            "Evidence quality": "more company-specific discussion",
            "Independent sources": "more independent voices, not more posts",
            "No disqualifying risk": "the risk item resolving or ageing out",
            "Nothing points down": "sentiment turning, or the negative event ageing out",
            "Crowds agree": "press coverage turning, or trader sentiment cooling",
            "Price not contradicting": ("any price history at all"
                                        if p.status == "missing" else
                                        "price stabilising, or volume normalising"),
        }
        for pill in v.pillars:
            if not pill.passed and pill.blocks_buy:
                v.would_change.append(remedy.get(pill.name, pill.name))
        if c.hard_clusters < 1 and s.lean != "positive":
            # BOTH routes named, not one.
            v.would_change.append("a confirmed catalyst, or traders leaning positive")
        if not v.would_change:
            v.would_change.append("nothing identified — this should not happen")
            logger.warning("verdict %s had no remediation path", v.recommendation)

    logger.info("verdict %s/%s (earned %s) q=%.2f clusters=%d lean=%s risk=%s "
                "cat=%d@%+.2f price=%s wire=%d",
                v.recommendation, v.confidence, v.earned_confidence, q.score,
                q.eligible_clusters, s.lean, r.high, c.hard_clusters,
                c.hard_direction, p.status, w.posts)
    return v
