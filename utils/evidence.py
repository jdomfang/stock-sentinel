"""The evidence ledger: one typed row per post, replacing eight keyword buckets.

WHY THIS SHAPE

The old architecture partitioned a corpus into eight "analysis angles" by
substring matching, then reasoned over the angles. That made three things
impossible:

  - a post could not say "I am risk evidence but carry no direction", so
    factual risk had to be averaged into a sentiment mean, where it diluted the
    signal it was supposed to sharpen
  - a post's SUBJECT status was never represented, so 69 posts about Members of
    Parliament were valid evidence about MP Materials
  - "why did this say Watch" had no answer, because the reasoning lived in
    whichever angle happened to be iterated first

A ledger inverts it: buckets of posts become posts with attributes. Every
downstream question is then a query over one table, and every verdict can be
traced to the rows that produced it.

NOTHING HERE SCORES OR DECIDES. The ledger describes; the modules judge.

CALIBRATION STATUS

The point weights and thresholds in this file are PROVISIONAL. They separate
the three corpora we hold and have never been fitted to labelled data. They are
recorded in docs/deep-analyze-locked-design.md as provisional so they do not
harden into the next generation of uncalibrated constants.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

logger = logging.getLogger(__name__)

CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")
_URL = re.compile(r"https?://\S+")
_HANDLE = re.compile(r"@\w+")
_NONWORD = re.compile(r"[^a-z0-9$ ]+")

# Near-duplicate clustering. 0.75 was chosen once, on the observation that it
# collapses a 100-account campaign whose posts differ only by a random emoji
# prefix to a single cluster, while leaving 78 genuine TSLA posts as 78. It has
# not been swept.
CLUSTER_JACCARD = 0.75

# A post is "directional" when the model is confident enough to have an opinion
# rather than merely a winner.
#
# THIS IS A PROPERTY OF THE MODEL, NOT OF THE DOMAIN. Different sentiment
# models have wildly different confidence distributions, so one hardcoded
# number cannot serve two of them. Measured against 175 human-labelled posts by
# sweeping the threshold for each candidate:
#
#   ProsusAI/finbert        best F1 0.41 at 0.05   direction accuracy 68%
#   FinTwitBERT-sentiment   best F1 0.56 at 0.70   direction accuracy 86%
#
# FinBERT is trained on financial news and reads trader prose as neutral; at
# 0.70 it finds 18% of the views a human sees, where FinTwitBERT finds 68%.
_DIRECTIONAL_MARGIN_BY_MODEL = {
    "prosusai/finbert": 0.15,
    "stephanakkerman/fintwitbert-sentiment": 0.70,
    "zhayunduo/roberta-base-stocktwits-finetuned": 0.85,
    "cardiffnlp/twitter-roberta-base-sentiment-latest": 0.05,
}
_DEFAULT_DIRECTIONAL_MARGIN = 0.50


def directional_margin(model: str | None = None) -> float:
    """The |p_pos - p_neg| a post must clear to count as carrying a view."""
    if model is None:
        try:
            from utils.sentiment import MODEL_NAME as model
        except Exception:
            model = ""
    return _DIRECTIONAL_MARGIN_BY_MODEL.get(
        str(model or "").strip().lower(), _DEFAULT_DIRECTIONAL_MARGIN)


# Kept for callers that read the constant; resolved at import for the model in
# force. Prefer directional_margin() where the model may vary.
DIRECTIONAL_MARGIN = directional_margin()

# --- Evidence vocabularies -------------------------------------------------
#
# BARE "risk" IS DELIBERATELY ABSENT. It is a retrieval term in the ticker
# query, so counting it would measure our own query rather than the stock: all
# three "risk items" in the genuine corpus were false positives, the first
# being the literal phrase "would reset risk/reward nicely".

RISK_SEVERE = ("dilution", "bankruptcy", "delisting", "lawsuit", "investigation",
               "fraud", "sec filing", "recall", "guidance cut", "downgrade",
               "probe", "subpoena", "restatement", "going concern")
RISK_SOFT = ("concern", "warning", "red flag", "downside", "pressure", "miss",
             "weak", "cut", "lowers", "threatens", "rejected", "denied")
RISK_NEGATED = ("no concern", "no risk", "risk/reward", "risk reward",
                "worth the risk", "de-risked", "derisked", "risk is priced in",
                "no red flag", "without risk")

CATALYST_KEYWORDS = ("earnings", "guidance", "catalyst", "upgrade", "downgrade",
                     "price target", "contract", "order", "deal", "partnership",
                     "acquisition", "merger", "spinoff", "fda", "production",
                     "delivery", "deliveries", "launch", "permit", "approval",
                     "settlement", "buyback")

# A keyword alone is not a catalyst. "contract", "order", "deal", "production",
# "launch", "approval" and "settlement" are ordinary news vocabulary -- applied
# to the wrong-entity corpus of parliamentary reporting they produced as many
# "hard catalysts" as the genuine one. A confirmed event needs a second signal:
# a number, a date, a named source, or a market reaction.
# STRONG signals are event-shaped on their own: a metric tied to a number, an
# analyst action with a direction, a named wire, or a price reaction.
CATALYST_STRONG = (
    re.compile(r"\b(eps|revenues?|q[1-4]|fy\s?\d{2}(\d{2})?|guidance)\b.{0,40}"
               r"(\d|beat|miss|rais|cut|lower|above|below)", re.I),
    re.compile(r"\b(beat|miss|rais|cut|lower|above|below)\w*.{0,40}"
               r"\b(eps|revenues?|q[1-4]|guidance|estimates?)\b", re.I),
    re.compile(r"\b(price target|pt)\b.{0,20}\b(to|rais|lower|cut|set)\w*", re.I),
    re.compile(r"\b(upgrade|downgrade|initiate|reiterate)\w*\b", re.I),
    re.compile(r"\b(reuters|bloomberg|wsj|cnbc|barron|sec filing|8-k|10-q|10-k)\b", re.I),
    re.compile(r"\b(shares?|stock)\b.{0,25}"
               r"\b(jump|fall|surge|drop|slide|soar|plunge|halt)\w*", re.I),
    re.compile(r"\b(board|regulator|fda|doj|ftc)\b.{0,30}"
               r"\b(approve|clear|reject|award|grant|block)\w*", re.I),
)
# WEAK signals corroborate but never confirm alone. A dollar figure and a
# weekday are both ubiquitous in ordinary chatter.
CATALYST_WEAK = (
    re.compile(r"\d+(\.\d+)?\s?%"),
    re.compile(r"\$\s?\d"),
    re.compile(r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|"
               r"next week|after the bell|premarket|this morning)\b", re.I),
    re.compile(r"\b(reports?|announce|announces|announced|filing|"
               r"according to|confirms?|confirmed)\b", re.I),
)
CATALYST_NEGATED = ("no catalyst", "need a catalyst", "waiting for catalyst",
                    "no news", "already priced in", "old news", "rumor only",
                    "rumour only", "were last week", "ignore the price target")

TRADING_KEYWORDS = ("watchlist", "setup", "breakout", "entry", "support",
                    "resistance", "levels", "swing", "scalp", "calls", "puts",
                    "options", "stop loss", "target")

# Symbols whose bare form collides with ordinary English or another domain.
# A general rule cannot cover these, so they are named. Not optional: "MP"
# alone retrieved Members of Parliament and the Indian state of Madhya Pradesh.
AMBIGUOUS_SYMBOLS = frozenset({
    "MP", "D", "ON", "IT", "ALL", "SO", "T", "F", "A", "B", "C", "V", "K", "L",
    "M", "R", "X", "Y", "Z", "GO", "BE", "AN", "OR", "IF", "IS", "AT", "BY",
    "DO", "HE", "IN", "ME", "MY", "NO", "OF", "UP", "US", "WE", "NOW", "KEY",
    "CAR", "GAP", "RUN", "OPEN", "WELL", "HOOD", "LOW", "REAL", "PLAY", "LIFE",
})

_LIST_MARKERS = ("watchlist", "recap", "movers", "top gainers", "unusual",
                 "market update", "daily update", "premarket movers",
                 "poised for", "stocks to")
# "5 Stocks Poised for Explosive Revenue Growth ... $QCOM $AMD $TSLA" scored 10
# -- higher than any genuine analysis in the corpus -- because three cashtags
# stays under the >=4 penalty and no marker phrase matched.
_LISTICLE = re.compile(r"^\s*\d+\s+(stocks?|tickers?|names?|picks?)\b", re.I)
_COMPARISON_MARKERS = (" vs ", " versus ", " over ", "prefer", "better than",
                       "rather than", "pair trade", "instead of")


def normalise(text: str) -> str:
    """Lowercase, strip URLs/handles/emoji/punctuation. For clustering only."""
    t = _HANDLE.sub(" ", _URL.sub(" ", text or "")).lower()
    return " ".join(_NONWORD.sub(" ", t).split())


def assign_clusters(texts: list[str], threshold: float = CLUSTER_JACCARD) -> list[int]:
    """Greedy LEADER clustering on token sets (not single-link).

    Each post is compared against the first member of each existing cluster,
    never against later members, so a chain A~B~C does not necessarily merge.
    Measured over reversal, 8 shuffles and 4,000 randomised trials on the
    retained corpora: cluster COUNTS are stable at this threshold, but cluster
    IDS permute with input order, so an id must never be persisted as if it
    identified anything.

    One cluster is one INDEPENDENT VOICE. A template posted by 100 accounts is
    one voice, not 100 -- which is the whole defence against a corpus that
    scores 100% on ticker attribution while containing no evidence at all.

    Greedy and order-dependent by construction. Acceptable here because the
    inputs are a fixed retrieved page, not a stream.
    """
    sets = [set(normalise(t).split()) for t in texts]
    reps: list[set] = []
    out: list[int] = []
    empty_cluster: int | None = None
    for tokens in sets:
        if not tokens:
            # All-emoji / link-only posts strip to nothing. Two empty sets have
            # an empty union and so never matched, giving a link-only campaign
            # one cluster per post and a near-perfect integrity score. They are
            # one voice, not many.
            if empty_cluster is None:
                reps.append(set())
                empty_cluster = len(reps) - 1
            out.append(empty_cluster)
            continue
        for i, r in enumerate(reps):
            union = tokens | r
            if union and len(tokens & r) / len(union) >= threshold:
                out.append(i)
                break
        else:
            reps.append(tokens)
            out.append(len(reps) - 1)
    return out


def _int(v: Any) -> int:
    """Engagement metrics arrive from an external API and are not trusted."""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _mentions(text: str, term: str) -> bool:
    if not term:
        return False
    return re.search(rf"(^|\W){re.escape(term)}(\W|$)", text or "",
                     flags=re.IGNORECASE) is not None


def _negated_near(low: str, terms: Iterable[str], negations: Iterable[str],
                  window: int = 40) -> bool:
    """True when every hit of `terms` sits beside a negation phrase.

    Scoped rather than document-wide, so a hedge in one clause cannot silence a
    warning in another.
    """
    spans = [m.start() for n in terms
             for m in re.finditer(rf"(^|\W){re.escape(n)}(s|es|ed|ing)?(\W|$)", low)]
    if not spans:
        return False
    neg = [m.start() for n in negations for m in re.finditer(re.escape(n), low)]
    return all(any(abs(p - q) <= window for q in neg) for p in spans)


def _any(text_lower: str, needles: Iterable[str]) -> bool:
    """Whole-word containment, with a plural/tense allowance.

    Plain `in` is how "execution" became a risk ("cut"), "followers" became
    "lowers", "missiles" became "miss", and "broker-dealer" became a catalyst
    ("deal"). Measured: that artefact was the majority of the risk signal on
    both retained corpora.
    """
    for n in needles:
        if " " in n:                       # phrases match literally
            if n in text_lower:
                return True
        elif re.search(rf"(^|\W){re.escape(n)}(s|es|ed|ing)?(\W|$)", text_lower):
            return True
    return False


@dataclass
class EvidenceRow:
    """One post, described. No judgement here."""

    post_id: str
    channel: str                 # social_base | newswire | discovery_seed
    text: str
    author_id: str | None = None
    created_at: str | None = None
    like_count: int = 0
    retweet_count: int = 0

    cashtags: tuple[str, ...] = ()
    cashtag_count: int = 0
    target_match_type: str = "none"      # cashtag | alias | bare | none
    subject_score: int = 0
    target_subject_status: str = "unclear"

    evidence_types: tuple[str, ...] = ()
    risk_severity: str = "none"          # severe | soft | none
    catalyst_severity: str = "none"      # hard | soft | none

    p_positive: float = 0.0
    p_negative: float = 0.0
    p_neutral: float = 0.0
    margin: float = 0.0
    scored: bool = False          # False => the probabilities are absent, not neutral

    cluster_id: int = -1
    spam_risk: str = "low"               # low | medium | high

    evidence_eligible: bool = False


def _subject(text: str, ticker: str, alias: str, tags: tuple[str, ...]) -> tuple[int, str, str]:
    """Score how much this post is ABOUT the target. (score, status, match_type)

    An additive point system rather than a rule cascade, so that several weak
    indications can combine and a single strong disqualifier can override them.
    Measured on the retained corpora: 55% of the genuine TSLA corpus classifies
    primary against 9% of the wrong-entity MP corpus -- a 6x separation, with no
    model call.
    """
    t = ticker.upper()
    low = (text or "").lower()
    head = (text or "")[:120].upper()
    n_tags = len(tags)

    has_cash = t in tags
    has_alias = _mentions(text, alias) if alias else False
    has_bare = _mentions(text, t)

    match_type = "cashtag" if has_cash else ("alias" if has_alias else
                                             ("bare" if has_bare else "none"))
    if match_type == "none":
        return 0, "wrong_entity", "none"

    score = 0
    if has_cash:
        score += 4
    if has_alias:
        score += 4
    if _mentions(head, t) or (alias and _mentions(head, alias)):
        score += 2
    if has_cash and n_tags <= 3:
        score += 2
    if re.search(rf"\${re.escape(t)}'s\b", text or "", re.I) or (
            alias and re.search(rf"{re.escape(alias)}\s+(reports?|announce|posts?|says?)",
                                text or "", re.I)):
        score += 2

    # A bare match on an ambiguous symbol, with nothing else pointing at the
    # company, is the Members-of-Parliament failure. Disqualify outright rather
    # than let other points rescue it.
    if match_type == "bare" and t in AMBIGUOUS_SYMBOLS:
        return score, "wrong_entity", match_type

    # A bare match on an UNambiguous symbol can still be real evidence. The
    # newswire post "TSLA soared +2.83% to $328.58 ... following a Buy upgrade
    # from Argus" carries no cashtag and no alias, and was being discarded --
    # from the very channel the design leans on for catalysts. Give it a route,
    # but only with corroboration.
    if match_type == "bare" and len(t) >= 3 and any(
            p.search(text or "") for p in CATALYST_STRONG):
        score += 3

    is_list = n_tags >= 4 or _any(low, _LIST_MARKERS) or bool(_LISTICLE.search(text or ""))

    # ONE mechanism, not two. The -3 penalties were dead: the override below
    # re-tested the same conditions and pinned the status regardless, so the
    # subtraction only skewed subject_score away from the status beside it.
    if is_list:
        status = "mentioned_only"
    elif 2 <= n_tags <= 3 and _any(low, _COMPARISON_MARKERS):
        # BEFORE the primary test. A target cashtag among <=3 tags scores 6, so
        # this branch was unreachable for exactly the cashtag-vs-cashtag posts
        # it exists to catch -- 0 rows in 298 carried it.
        status = "comparison"
    elif score >= 5:
        status = "primary"
    elif score > 0:
        status = "unclear"
    else:
        status = "mentioned_only"
    return score, status, match_type


def _types(text: str) -> tuple[tuple[str, ...], str, str]:
    """(evidence_types, risk_severity, catalyst_severity)."""
    low = (text or "").lower()
    types: list[str] = []

    # SEVERE risk is never negated away. A hedging phrase elsewhere in the post
    # must not erase a filed lawsuit: "The SEC investigation is real but at
    # these levels the risk/reward is fine" is risk evidence.
    risk = "none"
    if _any(low, RISK_SEVERE):
        risk = "severe"
    elif _any(low, RISK_SOFT) and not _negated_near(low, RISK_SOFT, RISK_NEGATED):
        risk = "soft"
    if risk != "none":
        types.append("risk")

    catalyst = "none"
    if _any(low, CATALYST_KEYWORDS) and not _any(low, CATALYST_NEGATED):
        strong = any(p.search(text or "") for p in CATALYST_STRONG)
        weak = sum(1 for p in CATALYST_WEAK if p.search(text or ""))
        # One strong signal confirms. Two weak ones corroborate. A lone dollar
        # figure does not -- it hit 28.6% of the genuine corpus and by itself
        # promoted a P/E explainer and a joke about a merger to "hard".
        catalyst = "hard" if (strong or weak >= 2) else "soft"
        types.append("catalyst")

    if _any(low, TRADING_KEYWORDS):
        types.append("trading")
    if _any(low, _LIST_MARKERS):
        types.append("market_recap")
    return tuple(types), risk, catalyst


def build_ledger(
    posts: list[dict],
    ticker: str,
    *,
    alias: str = "",
    channel: str = "social_base",
    scores: dict[str, dict] | None = None,
) -> list[EvidenceRow]:
    """Describe every post. `scores` maps POST ID -> FinBERT distribution."""
    t = (ticker or "").strip().upper()
    texts = [str(p.get("text") or "") for p in posts]
    clusters = assign_clusters(texts)
    sizes: dict[int, int] = {}
    for c in clusters:
        sizes[c] = sizes.get(c, 0) + 1
    n = max(1, len(posts))

    rows: list[EvidenceRow] = []
    unscored = 0
    for post, text, cid in zip(posts, texts, clusters):
        pid = post.get("id")
        if pid is None:
            pid = hashlib.sha1(text.encode("utf-8")).hexdigest()

        # findall on the ORIGINAL text. Upper-casing first invented cashtags
        # from ordinary $-prefixed words -- measured: TSLAX, SPCX, UFO, REMX,
        # NASA -- and an inflated tag count pushes a post past the >=4 list
        # threshold and out of the evidence set entirely.
        tags = tuple(sorted({m.upper() for m in CASHTAG.findall(text)}))
        score, status, match_type = _subject(text, t, alias, tags)
        types, risk, catalyst = _types(text)

        # Keyed by POST ID. Text-keying missed whenever a caller built the map
        # from the truncated strings analyze_sentiment_batch actually scores,
        # and could not distinguish two different posts sharing text.
        dist = (scores or {}).get(str(pid)) or {}
        _pp = float(dist.get("p_positive") or 0.0)
        _pn = float(dist.get("p_negative") or 0.0)
        _pu = float(dist.get("p_neutral") or 0.0)
        # An all-zero distribution is what a failed or pre-distribution scorer
        # returns. It is ABSENT, not neutral.
        scored = bool(dist) and (_pp + _pn + _pu) > 0.0
        if not scored:
            unscored += 1
        p_pos = float(dist.get("p_positive") or 0.0)
        p_neg = float(dist.get("p_negative") or 0.0)
        margin = p_pos - p_neg
        if abs(margin) >= DIRECTIONAL_MARGIN:
            types = types + ("directional_view",)

        size = sizes[cid]
        share = size / n
        # Share ALONE makes every small corpus look like a campaign: at n=3 a
        # unique post is 33% of the corpus. A campaign needs repetition in
        # absolute terms as well as relative.
        if size >= 5 and share >= 0.30:
            spam = "high"
        elif size >= 3 and share >= 0.10:
            spam = "medium"
        else:
            spam = "low"

        metrics = post.get("public_metrics") or {}
        rows.append(EvidenceRow(
            post_id=str(pid), channel=channel, text=text,
            author_id=str(post.get("author_id")) if post.get("author_id") else None,
            created_at=post.get("created_at"),
            like_count=_int(metrics.get("like_count")),
            retweet_count=_int(metrics.get("retweet_count")),
            cashtags=tags, cashtag_count=len(tags),
            target_match_type=match_type, subject_score=score,
            target_subject_status=status,
            evidence_types=types, risk_severity=risk, catalyst_severity=catalyst,
            p_positive=p_pos, p_negative=p_neg,
            p_neutral=float(dist.get("p_neutral") or 0.0), margin=margin,
            scored=scored,
            cluster_id=cid, spam_risk=spam,
            evidence_eligible=is_eligible(status, spam, types),
        ))
    if unscored:
        # An unscored row is NOT a neutral one. Callers that average margin must
        # filter on `scored`; a silent 0.0 diluted a true +0.70 mean to +0.56.
        logger.warning("ledger: %d/%d posts had no sentiment score", unscored, len(rows))
    logger.info("ledger %s: %d rows, %d eligible, %d clusters",
                t, len(rows), sum(1 for r in rows if r.evidence_eligible), len(sizes))
    return rows


def is_eligible(status: str, spam_risk: str, types: tuple[str, ...]) -> bool:
    """Deterministic. Never a human label -- production cannot ask a person.

    Risk and catalyst qualify WITHOUT a directional view: a filing that says an
    investigation has opened is decision-critical and carries no sentiment at
    all. Averaging such posts into a directional mean is what moved the genuine
    corpus from +0.244 to +0.175 and flipped a Buy to a Watch.
    """
    if status not in ("primary", "comparison"):
        return False
    if spam_risk == "high":
        return False
    return bool({"directional_view", "risk", "catalyst"} & set(types))
