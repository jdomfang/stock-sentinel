#!/usr/bin/env python3
"""The cross-feature channel: free evidence, and the limits on trusting it.

A sector scan's corpus is biased by construction -- a basket query matches any
of ~55 cashtags, so it over-selects multi-ticker list posts. It is admitted
because different queries reach disjoint parts of the index (two arms on the
same ticker in the same window shared ZERO posts out of 198), and refused as a
sole basis for a call for the same reason it is useful: it is not this ticker's
own retrieval.

No network. Supabase is stubbed.

Usage:
    python3 tests/test_seed.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import seed as S  # noqa: E402
from utils.evidence import EvidenceRow  # noqa: E402
from utils.verdict import adjudicate  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []
RISING = [80, 82, 84, 86, 88, 91, 94, 97, 100, 102, 104, 106]
FLATVOL = [1.0] * 12


def check(name, cond, detail=""):
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def row(i, channel="social_base", **kw):
    d = dict(post_id=str(i), channel=channel, text=f"post {i}", author_id=f"a{i}",
             target_match_type="cashtag", target_subject_status="primary",
             evidence_types=("directional_view",), margin=0.6, scored=True,
             cluster_id=i, spam_risk="low", evidence_eligible=True)
    d.update(kw)
    return EvidenceRow(**d)


def test_only_target_posts_are_reused():
    print("\nfilter: the target cashtag must be present")
    corpus = [
        {"id": "1", "text": "$MP wins a DoD contract"},
        {"id": "2", "text": "MP Materials in the news"},          # no cashtag
        {"id": "3", "text": "$MP $NG $RIO $AEM $KGC daily recap"},  # list post
        {"id": "4", "text": "$NG only"},                          # wrong ticker
        {"id": "5", "text": "$MP and $NG both moving"},
    ]
    S._get = lambda path, timeout=15: (
        [{"tweets": corpus, "fetched_at": "2026-08-10T00:00:00Z", "subject": "materials"}]
        if "x_corpus_cache" in path else [{"sector": "materials"}])
    got = S.fetch("MP", "materials")
    ids = {p["id"] for p in got}
    check("cashtag post kept", "1" in ids)
    check("bare company name dropped", "2" not in ids, str(ids))
    check("5-cashtag list post dropped", "3" not in ids, str(ids))
    check("other ticker dropped", "4" not in ids, str(ids))
    check("2-cashtag post kept", "5" in ids, str(ids))
    check("already-seen ids excluded",
          "1" not in {p["id"] for p in S.fetch("MP", "materials", exclude_ids={"1"})})


def test_the_two_sector_vocabularies_are_reconciled():
    print("\nsector: ticker_master speaks Nasdaq, the cache speaks Discovery")
    # ticker_master.sector holds "Basic Materials"; x_corpus_cache.subject holds
    # "materials". Querying one with the other matched nothing, silently, and
    # killed this channel for half the sectors -- including the $MP case used to
    # justify building it.
    for nasdaq, slug in [("Basic Materials", "materials"), ("Health Care", "healthcare"),
                         ("Consumer Discretionary", "consumer"), ("Technology", "tech")]:
        check(f"{nasdaq} -> {slug}", S._to_ui_slug(nasdaq) == slug, S._to_ui_slug(nasdaq))
    check("an unknown sector yields nothing", S._to_ui_slug("Nonsense") == "")


def test_sector_is_resolved_for_the_typed_path():
    print("\nsector: the typed path passes 'unknown'")
    calls = []

    def fake(path, timeout=15):
        calls.append(path)
        if "ticker_master" in path:
            # The value the REAL table holds, not a convenient one.
            return [{"sector": "Basic Materials"}]
        return [{"tweets": [{"id": "9", "text": "$MP up"}], "subject": "materials"}]

    S._get = fake
    got = S.fetch("MP", "unknown")
    check("looked the sector up", any("ticker_master" in c for c in calls))
    check("and still found posts", len(got) == 1, str(got))


def test_malformed_payloads_do_not_raise():
    print("\nfailure policy: the contract says never raises")
    for payload in ('[{"id":"1","text":"$MP up"}]', "not json", 42, [None, "x"]):
        S._get = lambda path, timeout=15, _p=payload: (
            [{"tweets": _p, "subject": "materials"}] if "x_corpus_cache" in path
            else [{"sector": "Basic Materials"}])
        try:
            S.fetch("MP", "materials")
            check(f"survives tweets={str(payload)[:26]}", True)
        except Exception as e:
            check(f"survives tweets={str(payload)[:26]}", False, f"{type(e).__name__}: {e}")


def test_absence_is_normal_and_failure_is_silent():
    print("\nfailure policy: no seed is the ordinary case")
    S._get = lambda path, timeout=15: []
    check("no recent scan -> []", S.fetch("MP", "materials") == [])
    S._get = lambda path, timeout=15: (_ for _ in ()).throw(RuntimeError("down"))
    try:
        check("an unreachable cache -> []", S.fetch("MP", "materials") == [])
    except Exception as e:
        check("an unreachable cache -> []", False, f"raised {type(e).__name__}")
    check("no ticker -> []", S.fetch("", "materials") == [])
    S._get = lambda path, timeout=15: []
    check("unresolvable sector -> []", S.fetch("MP", "") == [])


def test_seed_alone_cannot_make_a_call():
    print("\nverdict: corroborates, never decides alone")
    # The risk branch used to sit ABOVE the seed guard and was ungated, so a
    # seed-only corpus carrying a severe item produced AVOID -- the one thing
    # the design says this channel may never do.
    seeded_risk = [row(i, "discovery_seed", risk_severity="severe",
                       evidence_types=("risk",)) for i in range(6)]
    v = adjudicate(seeded_risk, RISING, FLATVOL)
    check("seed-only + severe risk is NOT an Avoid", v.recommendation == "Watch",
          f"{v.recommendation}: {v.reason}")

    seed_only = [row(i, "discovery_seed") for i in range(9)]
    v = adjudicate(seed_only, RISING, FLATVOL)
    check("seed-only bullish is a Watch", v.recommendation == "Watch", v.reason)
    check("and it names a remedy", bool(v.would_change), str(v.would_change))

    # Requiring merely ONE non-seed row let a single own post unlock a call the
    # seed then drove: one own row plus nine seed rows returned Buy on a social
    # direction of +0.90 that was almost entirely the seed's.
    thin = [row(0)] + [row(50 + i, "discovery_seed") for i in range(9)]
    v = adjudicate(thin, RISING, FLATVOL)
    check("1 own row + 9 seed rows cannot reach a call",
          v.recommendation == "Watch", f"{v.recommendation}: {v.reason}")

    mixed = [row(i) for i in range(6)] + [row(50 + i, "discovery_seed") for i in range(4)]
    v = adjudicate(mixed, RISING, FLATVOL)
    check("own evidence standing on its own CAN reach a call",
          v.recommendation == "Buy", v.reason)
    # The seed must not steer direction: it comes from a basket query that
    # over-selects multi-ticker list posts.
    own_only = [row(i) for i in range(6)]
    check("the seed does not move social direction",
          abs(adjudicate(mixed, RISING, FLATVOL).social.direction
              - adjudicate(own_only, RISING, FLATVOL).social.direction) < 1e-9)


def main() -> int:
    print("=" * 74)
    print("  discovery seed: free evidence, bounded trust")
    print("=" * 74)
    test_only_target_posts_are_reused()
    test_the_two_sector_vocabularies_are_reconciled()
    test_sector_is_resolved_for_the_typed_path()
    test_malformed_payloads_do_not_raise()
    test_absence_is_normal_and_failure_is_silent()
    test_seed_alone_cannot_make_a_call()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
