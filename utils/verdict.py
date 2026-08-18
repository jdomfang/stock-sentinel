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

    # WHICH BRANCH FIRED, as a stable token. `reason` is prose and will be
    # reworded; the pillar list is in DISPLAY order, which is not the cascade's
    # -- "Independent sources" fails at 4 clusters while the cascade only needs
    # 3, so reading the first failed pillar reports that pillar for a verdict
    # the risk branch actually caused. Anything analysing this later needs the
    # branch itself, and it cannot be reconstructed from the stored scalars
    # without reimplementing the very cascade that is expected to change.
    branch: str = ""
    # The two states that block a call and appear nowhere in the module
    # outputs, so a near-miss cannot otherwise be told from a distant one.
    own_clusters: int = 0
    seed_only: bool = False

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
    soc = [r for r in rows if r.channel not in ("newswire", "discovery_seed")]
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
               volumes: Sequence[float] | None = None,
               benchmark_prices=None,
               benchmark: str = "",
               bar_date: str | None = None) -> Verdict:
    """One of three words, plus the state that produced it."""
    q = M.quality(rows)
    s = M.social(rows, q.score)
    r = M.risk(rows)
    c = M.catalyst(rows)
    p = M.price(prices, volumes, benchmark_prices=benchmark_prices,
                benchmark=benchmark, bar_date=bar_date)
    w = M.newswire(rows)

    v = Verdict(quality=q, social=s, risk=r, catalyst=c, price=p, newswire=w)

    # A corpus below the DECISION bar cannot support a call, and reading risk
    # or catalyst out of one re-admits the wrong-entity corpus -- which returns
    # catalyst_present=True at quality 0.11. Gating on `!= "reject"` did not do
    # that: 0.11 is tier "low", so the guard was inert against the exact corpus
    # its comment named.
    can_call = q.tier in ("moderate", "high")

    # A seeded corpus is biased by construction: it comes from a basket query
    # matching any of ~55 cashtags, which over-selects multi-ticker list posts.
    # It may CORROBORATE, and it may lift confidence, but a directional call
    # resting on it alone is a call resting on a channel we know is skewed.
    _elig = [r for r in rows if r.evidence_eligible]
    _own = [r for r in _elig if r.channel not in ("discovery_seed",)]
    _seeded = [r for r in _elig if r.channel == "discovery_seed"]
    _own_clusters = len({(r.channel, r.cluster_id) for r in _own})
    # PROPORTIONAL, not binary. Requiring merely one non-seed row let a single
    # own post unlock a call the seed then drove: measured, one own eligible row
    # plus nine seed rows returned Buy on a social direction of +0.90 that was
    # almost entirely the seed's, and fourteen seed rows lifted a rejected
    # corpus from 0.250 to 0.543 and flipped Watch to Buy.
    # `bool(_seeded)`, not `bool(_elig)`. Keyed on eligible evidence of ANY
    # kind, a corpus of four own posts and ZERO seed posts set this True and
    # was told "the usable evidence came mostly from a recent sector scan" --
    # a statement about a channel that had contributed nothing. Thin evidence
    # and seed-dominated evidence are different findings with different
    # remedies, and the branch that fires must be the true one.
    seed_only = bool(_seeded) and _own_clusters < MIN_CLUSTERS_MODERATE
    if seed_only:
        can_call = False
    # Carried on the verdict so a log can tell a seed-starved Watch from a
    # genuine quality failure. Both print "Evidence quality" as their first
    # failing pillar; only this distinguishes them.
    v.own_clusters, v.seed_only = _own_clusters, seed_only

    # The price veto has a catalyst exemption in the locked design and it
    # existed in neither module: a confirmed event on a -20% tape returned
    # Watch where the spec says Buy.
    # "unconfirmed" is a decline we could not corroborate with volume. It
    # blocks a sentiment-only Buy exactly as caution does; a confirmed event
    # still exempts, per the design.
    price_adverse = p.status in ("caution", "declining", "unconfirmed")
    price_blocks = price_adverse and c.hard_clusters < 1
    # hard_scored, not merely hard_clusters. `hard_direction` falls back to 0.0
    # when NO confirmed row carried a score (utils/modules.py), and 0.0 passes
    # `>= 0` -- so an inference outage, a batch-length mismatch or a post-id
    # keying miss produced a Buy on "6 confirmed (+0.00)", printed that +0.00 as
    # though it were a measurement, and exempted the price veto on a 22% fall.
    # The diagnostic for this shipped in an earlier phase and was never wired in.
    bullish_event = (c.hard_clusters >= 1 and c.hard_scored >= 1
                     and c.hard_direction >= 0)

    v.pillars = [
        # Tests the bar that actually blocks a call, not the reject floor. The
        # readout previously printed ">= 0.10 to judge at all" while the Buy
        # branch required 0.30, so a corpus in between showed six green pillars
        # and named nothing standing in its way.
        Pillar("Evidence quality", q.tier in ("moderate", "high") and not seed_only,
               f"{q.score:.2f} ({q.tier})"
               + (f" · only {_own_clusters} own source(s)" if seed_only else ""),
               f">= {M.QUALITY_MODERATE:.2f} to support a call", blocks_buy=True),
        Pillar("Independent sources", q.eligible_clusters >= MIN_CLUSTERS_MODERATE,
               str(q.eligible_clusters), f">= {MIN_CLUSTERS_MODERATE}", blocks_buy=True),
        # No `usable` conjunct: a pass must never be printed beside a value that
        # is the thing the requirement forbids.
        Pillar("No disqualifying risk", not r.high, r.detail,
               "no severe item, and fewer than 3 independent warning voices",
               blocks_buy=True),
        # hard_clusters, not `present` -- `present` includes soft catalysts, so
        # this pillar passed on "0 confirmed, 4 unconfirmed" beside a printed
        # requirement of ">= 1 confirmed event".
        Pillar("Confirmed catalyst", c.hard_clusters >= 1, c.detail,
               ">= 1 confirmed event"),
        # SENTIMENT and confirmed events only -- risk has its own pillar. The
        # earlier name, "Nothing points down", promised to cover every downward
        # signal while testing two of them, so it rendered green beside an Avoid
        # the risk pillar had caused.
        # Covers both SENTIMENT routes. Social direction had no pillar at all,
        # so the Avoid it triggers rendered six passes and an empty remediation
        # list; the confirmed-bearish-event Avoid had the same hole, and the
        # "no remediation path" backstop below is what surfaced it.
        Pillar("Sentiment not against it",
               s.lean != "negative" and not (c.hard_clusters >= 1
                                             and c.hard_scored >= 1
                                             and c.hard_direction < 0),
               s.detail + (f" · confirmed event {c.hard_direction:+.2f}"
                           if c.hard_clusters >= 1 and c.hard_scored >= 1 else
                           " · confirmed event, direction unmeasured"
                           if c.hard_clusters >= 1 else ""),
               "no negative lean and no bearish confirmed event", blocks_buy=True),
        Pillar("Crowds agree", not s.conflict, s.detail,
               "traders and press not opposed", blocks_buy=True),
        # "no material decline" was the requirement until the sector benchmark
        # existed, and it would now be a lie on the market_wide path: that
        # status passes this pillar with a double-digit fall on the card. The
        # requirement states the question the veto actually asks.
        Pillar("Price not contradicting",
               not price_adverse and p.status != "missing", p.detail,
               "no COMPANY-SPECIFIC decline; price data present",
               blocks_buy=True),
    ]

    # ---- The cascade. First match wins. ----
    if q.tier == "reject":
        v.branch = "quality_reject"
        v.recommendation, v.reason = "Watch", (
            "Not neutral conviction — there is not enough clean evidence about "
            "this company to judge.")
    elif q.eligible_clusters < 3:
        v.branch = "too_few_sources"
        v.recommendation, v.reason = "Watch", (
            f"Only {q.eligible_clusters} independent source(s) survived filtering.")
    elif seed_only:
        v.branch = "seed_only"
        v.recommendation, v.reason = "Watch", (
            "The usable evidence came mostly from a recent sector scan rather "
            "than this ticker's own retrieval. That can corroborate a call; it "
            "cannot make one on its own.")
    elif not can_call:
        # BEFORE any Avoid. A corpus that cannot support a Buy cannot support a
        # sell signal either -- the wrong-entity corpus was issuing Avoid on a
        # parliamentary committee referral while its own quality pillar read
        # FAIL. Also before the missing-price branch, which otherwise blamed
        # price for a call that quality was blocking.
        v.branch = "quality_below_bar"
        v.recommendation, v.reason = "Watch", (
            f"Evidence quality {q.score:.2f} is below the {M.QUALITY_MODERATE:.2f} "
            "needed to support a call in either direction.")
    elif r.high:
        v.branch = "risk_high"
        # The rule is severe>=1 OR (soft>=3 AND rate>=20%), so "confirmed" was
        # a lie on the soft route: "Confirmed downside evidence (0 severe, 3
        # soft)" appeared beside a green "Sentiment not against it".
        v.recommendation = "Avoid"
        v.reason = (
            f"Confirmed downside evidence ({r.detail})"
            + (" filed by a newswire source." if r.from_newswire else ".")
            if r.severe_clusters >= 1 else
            f"Repeated warning language across {r.soft_clusters} independent "
            f"voices ({r.detail}). Not a confirmed event.")
    elif can_call and s.lean == "negative":
        v.branch = "sentiment_negative"
        v.recommendation, v.reason = "Avoid", (
            f"Sentiment is negative ({s.direction:+.2f}) on evidence of adequate quality.")
    elif can_call and c.hard_clusters >= 1 and c.hard_scored >= 1 and c.hard_direction < 0:
        v.branch = "bearish_event"
        # A confirmed BEARISH event. "Guidance cut", "downgrade" and "shares
        # plunge" are all hard catalysts; treating any of them as upside
        # recommended buying a guidance cut.
        v.recommendation, v.reason = "Avoid", (
            f"A confirmed event reads negative ({c.hard_direction:+.2f}).")
    elif s.conflict:
        v.branch = "channel_conflict"
        v.recommendation, v.reason = "Watch", (
            "Trader chatter and press coverage point opposite ways. That "
            "disagreement is the finding; we will not average it away.")
    elif p.status == "missing":
        v.branch = "price_missing"
        # FAILS CLOSED. Without price context no directional call is made.
        v.recommendation, v.reason = "Watch", (
            "No price context available, so no directional call is made.")
    elif q.eligible_clusters >= MIN_CLUSTERS_MODERATE and not price_blocks and (
            bullish_event or s.lean == "positive"):
        # TWO ROUTES, and the catalyst route now requires the event to READ
        # positive. A Buy also needs enough voices to be worth Moderate --
        # Buy/Low is not one of the four products the design describes.
        route = "a confirmed catalyst" if bullish_event else "positive sentiment"
        # The absolute number, in the sentence the user actually reads. On the
        # market_wide path "price not contradicting" would print beside a -21%
        # tape, which is the contradiction this module exists to make
        # unrepresentable -- the sector qualifier lived only in a pillar row
        # further down the card.
        r20_txt = f"{p.return_20d:+.1%}" if p.return_20d is not None else "recent"
        v.branch = "buy_catalyst" if bullish_event else "buy_sentiment"
        v.recommendation = "Buy"
        v.reason = (
            f"Evidence favours upside via {route}, with no disqualifying risk "
            + ("and price not contradicting." if p.status == "neutral" else
               f"— the {r20_txt} fall tracks its sector rather than this "
               "company." if p.status == "market_wide" else
               f"— and a confirmed event overriding a {p.detail}."))
    else:
        v.branch = "no_alignment"
        v.recommendation, v.reason = "Watch", (
            "Real evidence, but it does not line up into a call.")

    v.confidence, v.earned_confidence, v.confidence_notes = _confidence(q, s, rows)

    # A Buy the confidence rules then mark Low is not one of the four products
    # the design describes -- it rendered "Strong upside signal" beside "Thin
    # data, use caution". The confidence rules are the stricter judge.
    if v.recommendation == "Buy" and v.confidence == "Low":
        # THE NEAREST MISS THERE IS: every blocking pillar passed and the Buy
        # branch fired, then the confidence rules took it back. Without its own
        # token it is indistinguishable from any other Watch.
        v.branch = "buy_downgraded_low_confidence"
        v.recommendation = "Watch"
        v.reason = ("Upside evidence, but confidence rules downgraded it: "
                    + "; ".join(v.confidence_notes or ["thin evidence"]) + ".")

    # ---- What would change this. Assembled from the pillars that failed, for
    # every non-Buy verdict -- it used to run only for Watch, so an Avoid
    # rendered nothing at all.
    # Built for EVERY verdict. On a Watch or an Avoid these are the remedies;
    # on a Buy the failing blockers ARE the invalidation condition the design
    # requires -- omitting them left a red pillar sitting under a green Buy
    # with nothing on the card explaining why it still stood.
    remedy = {
        "Evidence quality": ("evidence from this ticker's own retrieval, "
                             "not only the sector scan" if seed_only else
                             "more company-specific discussion"),
        "Independent sources": "more independent voices, not more posts",
        "No disqualifying risk": "the risk item resolving or ageing out",
        "Sentiment not against it": "sentiment turning, or the negative event ageing out",
        "Confirmed catalyst": "a confirmed event",
        "Crowds agree": "press coverage turning, or trader sentiment cooling",
        "Price not contradicting": ("any price history at all"
                                    if p.status == "missing" else
                                    "price stabilising, or volume normalising"),
    }
    for pill in v.pillars:
        if not pill.passed and pill.blocks_buy:
            v.would_change.append(remedy.get(pill.name, pill.name))
    # A market_wide Buy passes every blocking pillar, so the loop above emits
    # NOTHING and the card shipped a Buy on a double-digit drawdown with no
    # stated invalidation condition at all.
    #
    # GATED ON Buy. Ungated, this clause rendered under "What would make this a
    # Buy" on Watch and Avoid cards too -- telling the user that a CONTINUING
    # 18% decline was what would turn the call positive, and asserting "the call
    # rests on the fall being sector-wide" about a call that rested on nothing
    # of the sort. The fix for one contradiction introduced five others. The exemption rests entirely on the
    # fall being the sector's, so the sector ceasing to explain it IS the
    # invalidation, and it belongs on the card.
    if p.status == "market_wide" and v.recommendation == "Buy":
        v.would_change.append(
            f"the {p.return_20d:+.1%} decline continuing after its sector stops "
            "falling — the call rests on the fall being sector-wide")
    if v.recommendation != "Buy" and c.hard_clusters < 1 and s.lean != "positive":
        v.would_change.append("a confirmed catalyst, or traders leaning positive")
    if v.recommendation != "Buy" and not v.would_change:
        v.would_change.append("nothing identified — this should not happen")
        logger.warning("verdict %s had no remediation path", v.recommendation)

    logger.info("verdict %s/%s (earned %s) q=%.2f clusters=%d lean=%s risk=%s "
                "cat=%d@%+.2f price=%s wire=%d",
                v.recommendation, v.confidence, v.earned_confidence, q.score,
                q.eligible_clusters, s.lean, r.high, c.hard_clusters,
                c.hard_direction, p.status, w.posts)
    return v
