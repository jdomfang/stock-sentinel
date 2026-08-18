"""Measure the curated-account channel, using only what a run already paid for.

WHY THIS EXISTS

Twelve curated accounts -- Reuters, Bloomberg, WSJ Markets, options-flow feeds --
are queried on every Deep Analyze and every Discovery deep panel, billed per
post returned like every other X call, and wired into the verdict as a source of
confirmed events and risks.

They have never been examined. Not once. Three rounds of external review each
put "measure the newswire" near the top of the list, and each round it stayed
unmeasured because measuring it looked like it needed a spending experiment.

It does not. Every number below is derivable from the corpora a normal run
already holds in memory, so this costs NOTHING and accumulates from ordinary
use. After a few weeks of rows the channel either earns its place or it is cut,
and that becomes a decision instead of an argument.

THE QUESTIONS, AND THE COLUMNS THAT ANSWER THEM

  why is it this size    state           not_configured | error | cache_hit | fetched
  what did it cost       billed          posts CHARGED; 0 on a cache hit
                         returned        posts held, cached or not
  is it on topic         about_target    unique rows that are about the company
  is it redundant        duplicate       ids the main arm had already returned
  did it reach the call  eligible        unique rows that reach the adjudicator
  DID IT MATTER          verdict_without_wire

THE LAST ONE IS THE DECISION; the rest are context. Everything above it
measures PARTICIPATION, and participation is not influence -- the channel can
supply twenty eligible rows and change nothing because the main arm already
carried the same finding. `verdict_without_wire` re-adjudicates the same
in-memory ledger with the wire rows removed, so `verdict <> verdict_without_wire`
counts the runs where those twelve accounts changed what the user was told.

WHAT `duplicate` DOES NOT CATCH, and it is a lot:

  - The wire arm queries 72h and the ticker arm 48h, so a wire post older than
    48h CANNOT appear in the main corpus and is structurally incapable of
    counting as a duplicate.
  - The ticker arm early-stops at one page, so this is overlap with what the
    main arm HAPPENED to retrieve, not with what it covers.
  - It matches on post id, not on content. A Reuters headline and a trader
    paraphrasing that same headline are two ids, two clusters and two
    "independent voices" -- and CATALYST_STRONG already matches the literal
    words "reuters" and "bloomberg", so the main arm promotes wire-sourced
    content to a confirmed event without the wire arm being involved at all.

All three biases point the same way: they make the channel look more additive
than it is. Read `duplicate` as a FLOOR on redundancy, never as a measure of it,
and prefer `verdict_without_wire` when the two disagree.

A curated wire list is also structurally biased toward mega-caps: twelve macro
accounts naming one mid-cap inside 72 hours is rare, and the cache stores the
empty result specifically because that is the common outcome. Expect these
counts to be zero for most tickers -- which is why `state` exists. Zero from a
quiet wire and zero from a rate-limited query are the same [] downstream, and
cutting a working channel on the strength of an outage is the failure this
whole module is most likely to cause.

FAILURE POLICY

Never raises. A measurement that can break a paid analysis is not worth taking.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

logger = logging.getLogger(__name__)

CHANNEL = "newswire"


def _ids(posts: Sequence[dict] | None) -> set[str]:
    out: set[str] = set()
    for p in posts or []:
        if isinstance(p, dict) and p.get("id") is not None:
            out.add(str(p["id"]))
    return out


def _counterfactual(ledger: Sequence, prices, volumes,
                    benchmark_prices=None, benchmark: str = "",
                    bar_date: str | None = None) -> str | None:
    """The verdict this analysis would have reached with the wire arm removed.

    THE ONLY COLUMN THAT SPEAKS TO THE DECISION. Every other counter measures
    participation -- posts returned, rows eligible, events contributed -- and
    participation is not influence. A channel can contribute twenty eligible
    rows and change nothing, because the main arm already carried the same
    finding; `catalyst.from_newswire` is likewise a flag saying the wire was
    present, not that it mattered.

    Costs one extra pass over a list already in memory. No API call, no model
    call. Compare it against the real verdict and the answer is direct: in what
    share of runs did those twelve accounts change what the user was told.

    THE SAME PRICES MUST BE PASSED. Omitting them does not "isolate the
    evidence" -- the cascade fails closed on absent price data and returns
    Watch via the price_missing branch, so every Buy would appear to have been
    caused by the wire and the column would read 100% influence. Measured: a
    ledger whose verdict was Buy with the wire and Buy without it reported
    Watch, purely because the counterfactual was run price-blind.
    """
    try:
        stripped = [r for r in ledger if r.channel != CHANNEL]
        if len(stripped) == len(ledger):
            return None          # no wire rows: nothing to counterfactual
        from utils.verdict import adjudicate
        # THE BENCHMARK TOO, for the same reason as the prices. Without it the
        # counterfactual runs an un-exempted price veto, so every market_wide
        # verdict reports a change the wire had nothing to do with -- in the one
        # column whose entire purpose is attributing changes to the wire.
        return str(adjudicate(stripped, prices, volumes,
                              benchmark_prices=benchmark_prices,
                              benchmark=benchmark,
                              bar_date=bar_date).recommendation)
    except Exception:
        logger.warning("newswire: counterfactual failed", exc_info=True)
        return None


def measure(ledger: Sequence | None,
            *,
            wire_posts: Sequence[dict] | None = None,
            main_posts: Sequence[dict] | None = None,
            wire_state: str | None = None,
            wire_billed: Any = None,
            prices: Sequence[float] | None = None,
            volumes: Sequence[float] | None = None,
            benchmark_prices=None,
            benchmark: str = "",
            bar_date: str | None = None) -> dict:
    """Newswire counters for one analysis. Never raises.

    `wire_posts` and `main_posts` are the RAW corpora, before the ledger drops
    the overlap between them -- which is the only point at which the overlap can
    still be seen.

    Every field is committed together at the end. A half-written measurement,
    where `returned` survives and the ledger-derived counts are NULL, silently
    breaks the one ratio anyone will actually compute -- sum(eligible) over
    sum(returned) -- by dropping rows from the numerator that stay in the
    denominator.
    """
    returned = duplicate = about = eligible = None
    counterfactual = None
    try:
        if wire_posts is not None:
            wire = _ids(wire_posts)
            returned = len(wire_posts)
            # Only meaningful when we can see both sides. None, not 0, when the
            # main corpus was not supplied: "we did not look" and "we looked and
            # found no overlap" are different findings and a 0 would merge them.
            if main_posts is not None:
                duplicate = len(wire & _ids(main_posts))

        if ledger is not None:
            sel = [r for r in ledger if r.channel == CHANNEL]
            # Rows surviving to the ledger are already deduplicated against the
            # main arm, so these count the channel's UNIQUE contribution.
            about = sum(1 for r in sel
                        if r.target_subject_status in ("primary", "comparison"))
            eligible = sum(1 for r in sel if r.evidence_eligible)
            if sel:
                counterfactual = _counterfactual(
                    ledger, prices, volumes, benchmark_prices, benchmark,
                    bar_date)
    except Exception:
        logger.warning("newswire: measurement failed", exc_info=True)
        # All-or-nothing rather than partial: see the docstring.
        returned = duplicate = about = eligible = counterfactual = None

    return {
        "newswire_returned": returned,
        "newswire_duplicate": duplicate,
        "newswire_about_target": about,
        "newswire_eligible": eligible,
        # WHY the corpus is the size it is. Without this a rate-limited month
        # of empty results reads as "these accounts return nothing", and a
        # working channel gets cut on the strength of an outage.
        "newswire_state": (str(wire_state)[:24] if wire_state else None),
        # Posts actually CHARGED. The corpus cache serves this arm across users
        # for hours, so len(wire_posts) is what we held, not what we paid.
        "newswire_billed": wire_billed,
        "verdict_without_wire": counterfactual,
    }


def summarise(m: dict) -> str:
    """One log line. The channel's whole story, for someone reading a tail."""
    state = m.get("newswire_state") or "unknown"
    ret = m.get("newswire_returned")
    if not ret:
        # Logged even at zero, and NAMED. "0 posts" was the outcome the design
        # expected and also the outcome an outage produces; printing the state
        # is what separates them for anyone reading a log rather than the table.
        return f"newswire: 0 posts ({state})"
    dup = m.get("newswire_duplicate")
    cf = m.get("verdict_without_wire")
    return (f"newswire: {ret} returned ({state}, {m.get('newswire_billed')} billed), "
            f"{m.get('newswire_about_target')} on target, "
            f"{m.get('newswire_eligible')} eligible"
            + (f", {dup} already in the main arm" if dup is not None else "")
            + (f", verdict without it: {cf}" if cf else ""))
