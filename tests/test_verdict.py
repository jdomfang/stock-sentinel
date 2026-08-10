#!/usr/bin/env python3
"""Pin the adjudicator, and above all pin the property it exists to guarantee:

    THE EXPLANATION CAN NEVER CONTRADICT THE DECISION.

The module this replaces printed "sentiment is largely neutral" directly beside
"all 2 analysis signals point bearish", and its successor's first draft emitted
an AVOID whose evidence check was six green ticks and whose remediation list was
empty. Both are the same defect: reasoning that lives somewhere other than the
state that produced the verdict.

So the invariants below are stronger than the individual cases. Every non-Buy
verdict must name something that would change it, and every FAIL must be one the
cascade could actually have acted on.

Usage:
    python3 tests/test_verdict.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils.evidence import EvidenceRow  # noqa: E402
from utils.verdict import adjudicate  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

FLAT = [100.0] * 12
FALLING = [100, 97, 94, 91, 88, 86, 84, 82, 81, 80, 79, 78]


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def row(i, **kw):
    d = dict(post_id=str(i), channel="social_base", text=f"post {i}",
             author_id=f"a{i}", target_match_type="cashtag",
             target_subject_status="primary", evidence_types=("directional_view",),
             margin=0.0, scored=True, cluster_id=i, spam_risk="low",
             evidence_eligible=True)
    d.update(kw)
    return EvidenceRow(**d)


def bullish(n=6, m=0.6):
    return [row(i, margin=m) for i in range(n)]


def test_every_non_buy_verdict_names_a_remedy():
    print("\nINVARIANT: a verdict the user cannot act on is a bug")
    cases = {
        "empty": ([], None),
        "spam-only": ([row(i, cluster_id=0, spam_risk="high", evidence_eligible=False)
                       for i in range(20)], FLAT),
        "negative lean": ([row(i, margin=-0.7) for i in range(8)], FLAT),
        "bearish confirmed event": ([row(i, catalyst_severity="hard", margin=-0.6,
                                         evidence_types=("catalyst", "directional_view"))
                                     for i in range(2)]
                                    + [row(10 + i) for i in range(10)], FLAT),
        "severe risk": ([row(i, risk_severity="severe",
                             evidence_types=("risk",)) for i in range(6)], FLAT),
        "no price data": (bullish(), None),
        "thin evidence": (bullish(n=2), FLAT),
    }
    for name, (rows, prices) in cases.items():
        v = adjudicate(rows, prices)
        if v.recommendation == "Buy":
            continue
        ok = bool(v.would_change) and "nothing identified" not in " ".join(v.would_change)
        check(f"{name} -> {v.recommendation} names a remedy", ok, str(v.would_change))


def test_pillars_agree_with_the_decision():
    print("\nINVARIANT: a non-Buy must have a failed blocking pillar")
    for name, (rows, prices) in {
        "negative lean": ([row(i, margin=-0.7) for i in range(8)], FLAT),
        "bearish event": ([row(i, catalyst_severity="hard", margin=-0.6,
                               evidence_types=("catalyst", "directional_view"))
                           for i in range(2)] + [row(10 + i) for i in range(10)], FLAT),
        "severe risk": ([row(i, risk_severity="severe", evidence_types=("risk",))
                         for i in range(6)], FLAT),
    }.items():
        v = adjudicate(rows, prices)
        blocking_fail = any(not p.passed and p.blocks_buy for p in v.pillars)
        check(f"{name} -> {v.recommendation} has a failed blocker", blocking_fail,
              str([str(p) for p in v.pillars]))


def test_a_confirmed_event_is_not_automatically_good_news():
    print("\nBuy route 1: a confirmed event must READ positive")
    # CATALYST_STRONG matches "downgrade", "guidance cut" and "shares plunge".
    # Treating the mere presence of a confirmed event as upside recommended
    # buying a guidance cut.
    cut = ([row(i, catalyst_severity="hard", margin=-0.6,
                evidence_types=("catalyst", "directional_view")) for i in range(2)]
           + [row(10 + i) for i in range(10)])
    v = adjudicate(cut, FLAT)
    check("a confirmed guidance cut is not a Buy", v.recommendation == "Avoid",
          f"{v.recommendation}: {v.reason}")
    good = [row(i, catalyst_severity="hard", margin=0.6,
                evidence_types=("catalyst", "directional_view")) for i in range(6)]
    v = adjudicate(good, FLAT)
    check("a confirmed bullish event IS a Buy", v.recommendation == "Buy", v.reason)


def test_buy_is_reachable_by_both_routes():
    print("\nBuy: two routes, and it must actually be reachable")
    v = adjudicate(bullish(), FLAT)
    check("route 2 — positive sentiment", v.recommendation == "Buy", v.reason)
    check("and it is never Buy/Low", v.confidence != "Low", v.confidence)


def test_price_fails_closed():
    print("\nprice: absent data blocks a Buy, it does not permit one")
    check("no prices -> Watch", adjudicate(bullish(), None).recommendation == "Watch")
    check("short history -> Watch", adjudicate(bullish(), [1, 2]).recommendation == "Watch")
    # ...but the catalyst exemption in the locked design still applies.
    hard = [row(i, catalyst_severity="hard", margin=0.6,
                evidence_types=("catalyst", "directional_view")) for i in range(6)]
    v = adjudicate(hard, FALLING, [1] * 11 + [9])
    check("a confirmed event survives a heavy-volume decline",
          v.recommendation == "Buy", f"{v.recommendation}: {v.reason}")
    v = adjudicate(bullish(), FALLING, [1] * 11 + [9])
    check("sentiment alone does not", v.recommendation == "Watch", v.reason)


def test_confidence_is_not_post_volume():
    print("\nconfidence: 100 posts from one voice is one voice")
    spam = [row(i, cluster_id=0, spam_risk="high", evidence_eligible=False, margin=0.6)
            for i in range(100)]
    v = adjudicate(spam, FLAT)
    check("a 100-post campaign is Low", v.confidence == "Low", v.confidence)
    check("and does not read as a Buy", v.recommendation == "Watch", v.recommendation)
    check("High is unreachable while unvalidated",
          adjudicate([row(i, margin=0.9) for i in range(40)], FLAT).confidence != "High")


def test_conflict_blocks_but_is_not_silence():
    print("\nconflict: preserved, named, and it blocks")
    rows = ([row(i, margin=0.6) for i in range(12)]
            + [row(50 + i, target_match_type="alias", margin=-0.7) for i in range(4)])
    v = adjudicate(rows, FLAT)
    check("disagreement blocks the Buy", v.recommendation == "Watch", v.reason)
    check("and is named in the reason", "opposite" in v.reason.lower(), v.reason)
    check("and caps confidence", "disagree" in " ".join(v.confidence_notes),
          str(v.confidence_notes))


def test_determinism_and_safety():
    print("\nrobustness")
    rows = bullish()
    check("same input, same verdict",
          adjudicate(rows, FLAT).recommendation == adjudicate(rows, FLAT).recommendation)
    for bad in ([], None):
        try:
            adjudicate(bad or [], None)
            check(f"adjudicate({bad!r}) is safe", True)
        except Exception as e:
            check(f"adjudicate({bad!r}) is safe", False, f"{type(e).__name__}: {e}")


def main() -> int:
    print("=" * 74)
    print("  verdict: the explanation IS the decision")
    print("=" * 74)
    test_every_non_buy_verdict_names_a_remedy()
    test_pillars_agree_with_the_decision()
    test_a_confirmed_event_is_not_automatically_good_news()
    test_buy_is_reachable_by_both_routes()
    test_price_fails_closed()
    test_confidence_is_not_post_volume()
    test_conflict_blocks_but_is_not_silence()
    test_determinism_and_safety()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
