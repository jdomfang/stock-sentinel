#!/usr/bin/env python3
"""Pin the evidence ledger against the defects that produced it.

Every assertion below corresponds to a measured failure, not a hypothetical.
The ledger's whole job is to describe a post honestly enough that a later stage
can refuse to reason about it, so the failures that matter are the ones where a
post LOOKED like evidence and was not.

No network. No model. Pure functions over text.

Usage:
    python3 tests/test_evidence.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils.evidence import (  # noqa: E402
    assign_clusters, build_ledger, is_eligible, _subject, _types,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def post(text, pid="1", **kw):
    return {"id": pid, "text": text, **kw}


def test_substring_artefacts_are_not_evidence():
    print("\nrisk: whole words only")
    # Each of these produced a risk row on a real corpus. "execution" contains
    # "cut", "followers" contains "lowers", "missiles" contains "miss",
    # "broker-dealer" contains "deal". Three of five risk rows in the genuine
    # corpus and four of five in the wrong-entity corpus were this artefact.
    for text, why in [
        ("aiming for 20 GW compute EOY 2027 execution", "cut in execution"),
        ("I may not have many followers, but all I can say", "lowers in followers"),
        ("Burning Through Weapons in Iran, missiles", "miss in missiles"),
    ]:
        _, risk, _ = _types(text)
        check(f"no risk from {why}", risk == "none", f"got {risk}")
    _, risk, _ = _types("SG Americas, the U.S. broker-dealer arm")
    _, _, cat = _types("SG Americas, the U.S. broker-dealer arm")
    check("no catalyst from 'deal' in broker-dealer", cat == "none", f"got {cat}")
    # ...but the real words still register.
    _, risk, _ = _types("guidance cut announced, shares fall")
    check("a genuine severe term still fires", risk == "severe", f"got {risk}")


def test_severe_risk_survives_a_hedge_elsewhere():
    print("\nrisk: negation is scoped, not document-wide")
    # "risk/reward" is one of the commonest phrases on finance X. As a
    # whole-text guard it silently erased a filed investigation, which under
    # risk_high = severe >= 1 is the difference between Avoid and Buy.
    _, risk, _ = _types("The SEC investigation is real but the risk/reward is fine")
    check("severe risk is never negated away", risk == "severe", f"got {risk}")
    _, risk, _ = _types("would reset risk/reward nicely, no concerns here")
    check("a hedged soft term IS suppressed", risk == "none", f"got {risk}")


def test_a_dollar_sign_is_not_a_catalyst():
    print("\ncatalyst: one strong signal, or two weak ones")
    # A bare dollar figure hit 28.6% of the genuine corpus and by itself
    # promoted a P/E explainer, a joke about a merger and a chart opinion to
    # "hard catalyst". In a stock corpus, "$" is price chatter.
    for text in [
        "P/E ratio = Price / Earnings Per Share, over many years",
        'Dumb $TSLA investors: "We want a merger and want $600/share!"',
    ]:
        _, _, cat = _types(text)
        check(f"not hard: {text[:38]}", cat != "hard", f"got {cat}")
    for text in [
        "TSLA soared following a Buy upgrade from Argus",
        "Reuters reports the board approved the acquisition",
    ]:
        _, _, cat = _types(text)
        check(f"hard: {text[:38]}", cat == "hard", f"got {cat}")


def test_wrong_entity_cannot_become_evidence():
    print("\nsubject: the Members-of-Parliament failure")
    s, status, mt = _subject("Congress MP P. Chidambaram slams absence of TN parties",
                             "MP", "MP Materials", ())
    check("bare ambiguous symbol is wrong_entity", status == "wrong_entity", status)
    check("and is never eligible", not is_eligible(status, "low", ("risk",)))
    s, status, _ = _subject("MP Materials wins DoD contract", "MP", "MP Materials", ())
    check("the real company still reaches primary", status == "primary", f"{status} {s}")


def test_comparison_is_reachable():
    print("\nsubject: comparison ordering")
    # A target cashtag among <=3 tags scores 6, so a `score >= 5` test placed
    # first made this branch unreachable for exactly the posts it exists for:
    # ZERO rows carried it across 298 real posts.
    _, status, _ = _subject("$TSLA vs $RIVN which do you prefer here",
                            "TSLA", "Tesla", ("RIVN", "TSLA"))
    check("cashtag-vs-cashtag is a comparison", status == "comparison", status)
    check("comparison is eligible evidence", is_eligible("comparison", "low", ("directional_view",)))


def test_a_small_corpus_is_not_a_campaign():
    print("\nspam: absolute size, not just share")
    # Share alone graded every corpus of <=3 posts as 100% spam and returned
    # zero evidence -- the thin-ticker path, failing silently after the credit
    # was already spent.
    rows = build_ledger([post(f"$MP unique post {i} guidance cut", str(i)) for i in range(3)],
                        "MP", alias="MP Materials")
    check("3 distinct posts are not spam",
          all(r.spam_risk == "low" for r in rows), str([r.spam_risk for r in rows]))
    # A real campaign still collapses.
    camp = [post(f"{'x' * 0}same promo text here every time", str(i)) for i in range(20)]
    rows = build_ledger(camp, "MP", alias="MP Materials")
    check("20 identical posts are one voice", len({r.cluster_id for r in rows}) == 1)
    check("and are graded high spam", all(r.spam_risk == "high" for r in rows))
    check("and none is eligible", not any(r.evidence_eligible for r in rows))


def test_link_only_campaign_does_not_score_perfect_integrity():
    print("\nspam: posts that normalise to nothing")
    # Emoji+link posts strip to "". Two empty sets have an empty union and so
    # never matched, giving one cluster per post -- a perfect integrity score
    # for exactly the shape the metric exists to catch.
    rows = build_ledger([post("\U0001F680\U0001F525 https://t.co/abc%d" % i, str(i))
                         for i in range(12)], "MP", alias="MP Materials")
    check("link-only posts share one cluster", len({r.cluster_id for r in rows}) == 1,
          str(len({r.cluster_id for r in rows})))


def test_unscored_is_not_neutral():
    print("\nscoring: absence is not a zero")
    rows = build_ledger([post("$MP guidance cut confirmed", "1")], "MP", alias="MP Materials")
    check("no score -> scored is False", rows[0].scored is False)
    rows = build_ledger([post("$MP guidance cut confirmed", "1")], "MP", alias="MP Materials",
                        scores={"1": {"p_positive": 0.8, "p_negative": 0.1, "p_neutral": 0.1}})
    check("scored by POST ID, not text", rows[0].scored is True and abs(rows[0].margin - 0.7) < 1e-9,
          f"scored={rows[0].scored} margin={rows[0].margin}")
    check("a confident margin adds directional_view",
          "directional_view" in rows[0].evidence_types, str(rows[0].evidence_types))


def test_cashtags_are_not_invented():
    print("\nextraction: no upper-casing before matching")
    # Upper-casing the text first turned ordinary $-prefixed words into
    # cashtags, and an inflated tag count pushes a post past the >=4 list
    # threshold and out of the evidence set entirely.
    rows = build_ledger([post("$hit happens $if you $do this with $MP", "1")],
                        "MP", alias="MP Materials")
    check("lowercase $words are not cashtags", rows[0].cashtags == ("MP",), str(rows[0].cashtags))


def test_malformed_input_does_not_raise():
    print("\nrobustness: external data is not trusted")
    for bad in [[], [post(None)], [{"id": "1"}], [post(123)],
                [post("ok", public_metrics={"like_count": "abc"})],
                [post("éè \U0001F600 x" * 50)]]:
        try:
            build_ledger(bad, "MP", alias="MP Materials")
            check(f"survives {str(bad)[:38]}", True)
        except Exception as e:
            check(f"survives {str(bad)[:38]}", False, f"{type(e).__name__}: {e}")


def test_output_is_reproducible():
    print("\ndeterminism")
    posts = [post(f"$MP post number {i} with guidance cut", str(i)) for i in range(30)]
    a = build_ledger(posts, "MP", alias="MP Materials")
    b = build_ledger(posts, "MP", alias="MP Materials")
    check("identical input, identical ledger",
          [(r.post_id, r.subject_score, r.target_subject_status, r.evidence_eligible) for r in a] ==
          [(r.post_id, r.subject_score, r.target_subject_status, r.evidence_eligible) for r in b])
    # Cluster COUNT is stable under reordering; cluster IDS are not, which is
    # why an id must never be persisted as an identifier.
    rev = build_ledger(list(reversed(posts)), "MP", alias="MP Materials")
    check("cluster count survives reordering",
          len({r.cluster_id for r in a}) == len({r.cluster_id for r in rev}))


def main() -> int:
    print("=" * 74)
    print("  evidence ledger: describe honestly, or refuse")
    print("=" * 74)
    test_substring_artefacts_are_not_evidence()
    test_severe_risk_survives_a_hedge_elsewhere()
    test_a_dollar_sign_is_not_a_catalyst()
    test_wrong_entity_cannot_become_evidence()
    test_comparison_is_reachable()
    test_a_small_corpus_is_not_a_campaign()
    test_link_only_campaign_does_not_score_perfect_integrity()
    test_unscored_is_not_neutral()
    test_cashtags_are_not_invented()
    test_malformed_input_does_not_raise()
    test_output_is_reproducible()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
