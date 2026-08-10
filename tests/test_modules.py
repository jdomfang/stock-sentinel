#!/usr/bin/env python3
"""Pin the six modules against the failures that shaped them.

Each assertion is a measured defect, not a hypothetical. The recurring theme is
that a module found a signal where there was none -- a spam campaign that
scored perfectly, a corpus of parliamentary reporting that produced a confirmed
catalyst, a price veto that got EASIER when data was missing.

No network. No model. Pure functions over synthetic ledgers.

Usage:
    python3 tests/test_modules.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils.evidence import EvidenceRow  # noqa: E402
from utils import modules as M  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def row(pid, *, cluster=0, channel="social_base", match="cashtag", status="primary",
        types=("directional_view",), margin=0.0, risk="none", cat="none",
        spam="low", elig=True, author="a1", scored=True):
    return EvidenceRow(
        post_id=str(pid), channel=channel, text=f"post {pid}", author_id=author,
        target_match_type=match, target_subject_status=status,
        evidence_types=tuple(types), risk_severity=risk, catalyst_severity=cat,
        margin=margin, scored=scored, cluster_id=cluster, spam_risk=spam,
        evidence_eligible=elig,
    )


def test_cluster_ids_do_not_collide_across_channels():
    print("\nclusters: ids restart per ledger build")
    # Two ledgers built separately both number their clusters from 0. Merged,
    # risk_high flipped True -> False: the Avoid path vanishing silently.
    social = [row(i, cluster=i, risk="soft") for i in range(2)]
    wire = [row(10 + i, cluster=i, channel="newswire", risk="soft") for i in range(2)]
    check("4 voices across 2 channels, not 2",
          M._clusters(social + wire) == 4, str(M._clusters(social + wire)))


def test_quality_is_multiplicative():
    print("\nquality: aboutness gates hygiene, not the reverse")
    # The wrong-entity corpus is ORGANIC and DIVERSE -- it scores well on
    # integrity and coverage. An additive form let those rescue it to within
    # 0.12 of the genuine corpus, failing the 0.25 separation bar.
    wrong = [row(i, cluster=i, match="bare", status="wrong_entity", elig=False)
             for i in range(20)]
    real = [row(i, cluster=i, margin=0.4) for i in range(20)]
    q_wrong, q_real = M.quality(wrong).score, M.quality(real).score
    check("wrong-entity scores near zero", q_wrong < 0.10, f"{q_wrong}")
    check("separation clears 0.25", q_real - q_wrong >= 0.25, f"{q_real - q_wrong:.3f}")
    check("aboutness stays within [0,1]", 0.0 <= M.quality(real).aboutness <= 1.0)


def test_quality_ignores_the_wire():
    print("\nquality: the wire is a separate channel")
    # Adding wire posts dropped the genuine corpus a whole tier and dissolved
    # its conflict veto, purely through the quality weight.
    base = [row(i, cluster=i, margin=0.4) for i in range(20)]
    with_wire = base + [row(100 + i, cluster=i, channel="newswire", match="bare",
                            status="unclear", elig=False) for i in range(20)]
    check("wire posts do not move quality",
          M.quality(base).score == M.quality(with_wire).score,
          f"{M.quality(base).score} vs {M.quality(with_wire).score}")


def test_one_voice_one_vote():
    print("\nsocial: clusters vote, not posts")
    # 8 near-duplicates at +0.90 outvoted 3 independent voices at -0.40 and
    # reported POSITIVE, where one-voice-one-vote gives neutral.
    dupes = [row(i, cluster=0, margin=0.9) for i in range(8)]
    voices = [row(10 + i, cluster=1 + i, margin=-0.4) for i in range(3)]
    d = M.social(dupes + voices, 1.0).direction
    check("8 duplicates cannot outvote 3 voices", d < 0.15, f"direction {d:+.3f}")


def test_agreeing_channels_are_not_penalised():
    print("\nsocial: shrink applied once, on total evidence")
    # Shrinking per channel and re-weighting by cluster count applied evidence
    # volume at power 1.5, so two channels that AGREE lost 20-28% of their lean
    # purely because the evidence arrived split.
    one = [row(i, cluster=i, margin=0.5) for i in range(5)]
    split = ([row(i, cluster=i, margin=0.5) for i in range(3)] +
             [row(10 + i, cluster=10 + i, match="alias", margin=0.5) for i in range(2)])
    a, b = M.social(one, 1.0).direction, M.social(split, 1.0).direction
    check("same evidence, same lean regardless of split",
          abs(a - b) < 0.02, f"{a:+.3f} vs {b:+.3f}")


def test_conflict_needs_evidence_on_both_sides():
    print("\nsocial: the disagreement veto has a floor")
    many = [row(i, cluster=i, margin=0.5) for i in range(15)]
    one_article = [row(90, cluster=90, match="alias", margin=-0.9)]
    s = M.social(many + one_article, 1.0)
    check("1 article cannot veto 15 voices", not s.conflict, s.detail)
    check("but it is recorded as disagreement", s.minor_disagreement, s.detail)
    three = [row(90 + i, cluster=90 + i, match="alias", margin=-0.9) for i in range(3)]
    s = M.social(many + three, 1.0)
    check("3 articles do veto", s.conflict, s.detail)
    check("and direction is zeroed", s.direction == 0.0, str(s.direction))
    check("lean says conflicted, not neutral", s.lean == "conflicted", s.lean)


def test_lean_distinguishes_empty_from_balanced():
    print("\nsocial: three states, not one")
    check("no evidence reads insufficient",
          M.social([], 1.0).lean == "insufficient")
    flat = [row(i, cluster=i, margin=0.01) for i in range(5)]
    check("genuine balance reads neutral", M.social(flat, 1.0).lean == "neutral")


def test_risk_and_catalyst_count_voices():
    print("\nrisk / catalyst: independent voices, and a quality-blind corpus")
    check("one severe item is enough",
          M.risk([row(1, cluster=1, risk="severe")]).high)
    soft = [row(i, cluster=i, risk="soft") for i in range(3)]
    check("3 soft voices at 100% rate fire", M.risk(soft).high, M.risk(soft).detail)
    check("one hard catalyst is enough",
          M.catalyst([row(1, cluster=1, cat="hard")]).present)
    one_author = [row(i, cluster=i, cat="soft", author="same") for i in range(4)]
    check("4 soft items from ONE author do not",
          not M.catalyst(one_author).present, M.catalyst(one_author).detail)


def test_newswire_respects_the_subject_gate():
    print("\nnewswire: one item can trigger a rule, so it must be gated")
    # Wire posts about an Indian MP reported a confirmed catalyst and a filed
    # risk while risk() and catalyst() correctly returned nothing.
    off = [row(i, cluster=i, channel="newswire", match="bare",
               status="wrong_entity", elig=False, cat="hard", risk="severe")
           for i in range(3)]
    n = M.newswire(off)
    check("off-target wire posts are not confirmed events",
          n.hard_catalysts == 0 and n.severe_risks == 0,
          f"hard={n.hard_catalysts} severe={n.severe_risks}")
    check("but they are still counted as retrieved", n.posts == 3)


def test_price_can_veto_but_never_endorse():
    print("\nprice: fails toward neutral, not toward confidence")
    falling = [100, 98, 95, 92, 88, 85, 82, 80]
    # Missing volume used to SATISFY the volume condition, so having no data
    # made the veto easier and acquiring real data made the system less careful.
    check("missing volume does not create caution",
          not M.price(falling).caution, M.price(falling).detail)
    check("both conditions together do",
          M.price(falling, [1, 1, 1, 1, 1, 1, 1, 5]).caution)
    check("flat volume on a falling tape does not",
          not M.price(falling, [1] * 8).caution)
    # A newest-first series silently flipped the sign and cleared the veto.
    r = M.price(list(reversed(falling)), [1, 1, 1, 1, 1, 1, 1, 5])
    check("a rising window is not caution", not r.caution, r.detail)
    check("no history is 'missing', not 'neutral'",
          M.price([]).status == "missing" and M.price(None).status == "missing")
    check("NaN prices do not pass as usable",
          M.price([float("nan")] * 10).status == "missing")
    check("zero volume is reported, not treated as absent",
          M.price(falling, [1, 1, 1, 1, 1, 1, 1, 0]).volume_ratio == 0.0,
          str(M.price(falling, [1, 1, 1, 1, 1, 1, 1, 0]).volume_ratio))


def test_nothing_raises_on_degenerate_input():
    print("\nrobustness")
    for fn, arg in [(M.quality, []), (M.risk, []), (M.catalyst, []), (M.newswire, [])]:
        try:
            fn(arg)
            check(f"{fn.__name__}([]) is safe", True)
        except Exception as e:
            check(f"{fn.__name__}([]) is safe", False, f"{type(e).__name__}: {e}")
    try:
        M.social([], 0.0)
        check("social([], 0.0) is safe", True)
    except Exception as e:
        check("social([], 0.0) is safe", False, f"{type(e).__name__}: {e}")
    unscored = [row(i, cluster=i, types=(), scored=False) for i in range(10)]
    check("an unscored corpus has no lean",
          M.social(unscored, 1.0).lean == "insufficient")


def main() -> int:
    print("=" * 74)
    print("  modules: six questions, six honest answers")
    print("=" * 74)
    test_cluster_ids_do_not_collide_across_channels()
    test_quality_is_multiplicative()
    test_quality_ignores_the_wire()
    test_one_voice_one_vote()
    test_agreeing_channels_are_not_penalised()
    test_conflict_needs_evidence_on_both_sides()
    test_lean_distinguishes_empty_from_balanced()
    test_risk_and_catalyst_count_voices()
    test_newswire_respects_the_subject_gate()
    test_price_can_veto_but_never_endorse()
    test_nothing_raises_on_degenerate_input()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
