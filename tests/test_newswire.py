#!/usr/bin/env python3
"""Pin the newswire measurement, whose whole purpose is to decide a cut.

WHAT MAKES THIS DANGEROUS RATHER THAN MERELY WRONG

Twelve curated accounts are billed on every run and have never been examined.
This module exists so the owner can finally answer "do they earn it" -- which
means its output will be read as a verdict on a paid channel, and both possible
errors cost real money:

  say ZERO when the query was never made      -> a working channel gets cut
  say the wire ADDED something when it did not -> the owner keeps paying for
                                                  posts the main arm already had

Every test below guards one of those two. In particular:

  1. An empty corpus has FOUR causes -- not configured, query errored, served
     from cache, genuinely quiet -- and they all arrive downstream as the same
     `[]`. Without `newswire_state` a month of rate limiting reads as "these
     accounts return nothing".

  2. The corpus cache serves this arm across users for hours, so posts HELD is
     not posts BILLED. Counting cache hits as cost overstates the bill several
     fold, and overstates it precisely on the tickers where the wire returns
     something.

  3. verdict_without_wire is the only column that speaks to influence rather
     than participation -- and it was briefly WRONG in the most misleading
     possible direction: run without prices, the cascade fails closed on the
     price_missing branch and returns Watch, so every Buy read as wire-caused
     and the channel would have looked indispensable.

Costs nothing to run: no API calls, no model, no network.

Usage:
    python3 tests/test_newswire.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import newswire as NW  # noqa: E402
from utils.evidence import EvidenceRow  # noqa: E402
from utils.verdict import adjudicate  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

FLAT = [100.0] * 12
VOLS = [1.0] * 12
MIGRATION = REPO / "supabase" / "migrations" / "20260817010000_signal_log.sql"


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def row(i, ch="social_base", **kw):
    d = dict(post_id=str(i), channel=ch, text=f"p{i}", author_id=f"a{i}",
             target_match_type="cashtag", target_subject_status="primary",
             evidence_types=("directional_view",), margin=0.6, scored=True,
             cluster_id=i, spam_risk="low", evidence_eligible=True)
    d.update(kw)
    return EvidenceRow(**d)


def ids(n, start=0):
    return [{"id": str(start + i), "text": f"t{i}"} for i in range(n)]


def test_an_empty_corpus_says_why():
    print("\nZERO IS THE FINDING — so zero must say which zero it is")
    for state in ("not_configured", "error", "cache_hit", "fetched"):
        m = NW.measure([], wire_posts=[], main_posts=[], wire_state=state,
                       wire_billed=0)
        check(f"{state} survives into the row", m["newswire_state"] == state,
              str(m["newswire_state"]))
        check(f"...and into the log line", state in NW.summarise(m),
              NW.summarise(m))
    # The case that would cut a working channel.
    err = NW.measure([], wire_posts=[], main_posts=[], wire_state="error")
    quiet = NW.measure([], wire_posts=[], main_posts=[], wire_state="fetched")
    check("an outage is distinguishable from a quiet wire",
          err["newswire_state"] != quiet["newswire_state"])
    check("...even though both returned 0 posts",
          err["newswire_returned"] == quiet["newswire_returned"] == 0)


def test_held_is_not_billed():
    print("\nthe corpus cache serves this arm free; cost must not count it")
    wire = ids(6, 100)
    hit = NW.measure(None, wire_posts=wire, wire_state="cache_hit", wire_billed=0)
    check("a cache hit reports 6 held", hit["newswire_returned"] == 6)
    check("...and 0 billed", hit["newswire_billed"] == 0, str(hit["newswire_billed"]))
    live = NW.measure(None, wire_posts=wire, wire_state="fetched", wire_billed=6)
    check("a live fetch bills what it returned", live["newswire_billed"] == 6)
    check("the log line separates the two", "0 billed" in NW.summarise(hit),
          NW.summarise(hit))
    # And the source of the distinction exists upstream.
    src = (REPO / "utils" / "deep_analysis.py").read_text()
    check("_fetch_influencers reports cache_hit", '"wire_state": "cache_hit"' in src)
    check("...and bills 0 on that path", '"wire_billed": 0' in src)
    check("...and the sink carries both", 'sink["wire_state"]' in src
          and 'sink["wire_billed"]' in src)


def test_the_counterfactual_sees_the_same_tape():
    print("\nthe counterfactual is the decision — and it was briefly inverted")
    led = [row(i) for i in range(8)] + [row(100 + i, ch="newswire") for i in range(4)]
    real = adjudicate(led, FLAT, VOLS)
    check("the real verdict is a Buy for this fixture", real.recommendation == "Buy",
          real.recommendation)
    blind = NW._counterfactual(led, None, None)
    sighted = NW._counterfactual(led, FLAT, VOLS)
    # Documented rather than merely fixed: price-blind, the cascade fails closed
    # and returns Watch, so EVERY Buy would read as caused by the wire.
    check("price-blind would have reported a false change", blind == "Watch", str(blind))
    check("with the same prices it reports no change", sighted == "Buy", str(sighted))
    check("so the fixture's wire did NOT decide it",
          real.recommendation == sighted)


def test_it_detects_a_wire_that_genuinely_decides():
    print("\n...and still fires when the wire really is the reason")
    led = ([row(i, margin=0.02) for i in range(8)]
           + [row(100 + i, ch="newswire", catalyst_severity="hard", margin=0.8,
                  evidence_types=("catalyst", "directional_view")) for i in range(6)])
    real = adjudicate(led, FLAT, VOLS)
    m = NW.measure(led, wire_posts=ids(6, 100), main_posts=ids(8),
                   wire_state="fetched", wire_billed=6, prices=FLAT, volumes=VOLS)
    check("the wire carried the only catalyst, so the verdict differs",
          real.recommendation != m["verdict_without_wire"],
          f"{real.recommendation} vs {m['verdict_without_wire']}")
    check("and the log line names it",
          "verdict without it" in NW.summarise(m), NW.summarise(m))


def test_no_wire_rows_is_absent_not_unchanged():
    print("\nno wire rows: None, never a confident 'it changed nothing'")
    m = NW.measure([row(i) for i in range(8)], wire_posts=[], main_posts=ids(8),
                   wire_state="fetched", wire_billed=0, prices=FLAT, volumes=VOLS)
    check("verdict_without_wire is None", m["verdict_without_wire"] is None,
          str(m["verdict_without_wire"]))
    check("_counterfactual on a wire-free ledger is None",
          NW._counterfactual([row(i) for i in range(8)], FLAT, VOLS) is None)


def test_duplicate_counts_what_the_pages_actually_drop():
    print("\nduplicate: overlap with the main arm, counted before the drop")
    main = ids(20)
    wire = [{"id": str(i)} for i in (18, 19)] + ids(6, 100)
    m = NW.measure([row(100 + i, ch="newswire") for i in range(6)],
                   wire_posts=wire, main_posts=main)
    check("2 of 8 wire posts were already in the main arm",
          m["newswire_duplicate"] == 2, str(m["newswire_duplicate"]))
    check("returned counts everything held", m["newswire_returned"] == 8,
          str(m["newswire_returned"]))
    check("no main corpus -> None, not 0",
          NW.measure(None, wire_posts=wire)["newswire_duplicate"] is None)
    # The biases are documented, because the number will be read as redundancy
    # and it is only a floor on it.
    doc = NW.__doc__ or ""
    check("the 72h vs 48h window bias is documented", "72h" in doc and "48h" in doc)
    check("the id-vs-content bias is documented",
          "post id, not on content" in doc or "not on content" in doc)
    check("it is labelled a FLOOR on redundancy", "FLOOR" in doc)


def test_ledger_counts_only_the_wire_channel():
    print("\nchannel separation: a seed row is not a wire row")
    led = ([row(i) for i in range(5)]
           + [row(20 + i, ch="newswire") for i in range(4)]
           + [row(30 + i, ch="newswire", target_subject_status="mentioned_only",
                  evidence_eligible=False) for i in range(3)]
           + [row(40 + i, ch="discovery_seed") for i in range(2)])
    m = NW.measure(led, prices=FLAT, volumes=VOLS)
    check("about_target counts only on-subject wire rows",
          m["newswire_about_target"] == 4, str(m["newswire_about_target"]))
    check("eligible counts only wire rows that reach the adjudicator",
          m["newswire_eligible"] == 4, str(m["newswire_eligible"]))


def test_failure_is_all_or_nothing():
    print("\na half-written measurement breaks the only ratio anyone computes")
    # A junk ledger raises inside the try after `returned` was already set.
    m = NW.measure([object()], wire_posts=ids(4, 100), main_posts=ids(8),
                   wire_state="fetched", wire_billed=4)
    for k in ("newswire_returned", "newswire_duplicate",
              "newswire_about_target", "newswire_eligible"):
        check(f"{k} is None, not a partial count", m[k] is None, str(m[k]))
    check("state still survives, because it did not come from the ledger",
          m["newswire_state"] == "fetched")


def test_it_never_raises_and_costs_nothing():
    print("\nrobustness, and the standing rule: no experiment may burn X credits")
    for name, kw in {
        "everything None": {},
        "junk wire posts": {"wire_posts": [None, 3, {"id": 1}]},
        "string main posts": {"wire_posts": ids(2), "main_posts": "nonsense"},
        "junk ledger": {"ledger": [object()]},
        "junk verdict rows": {"ledger": [row(0)], "prices": "bad"},
    }.items():
        try:
            led = kw.pop("ledger", None)
            NW.measure(led, **kw)
            check(f"{name} -> no raise", True)
        except Exception as e:
            check(f"{name} -> no raise", False, f"{type(e).__name__}: {e}")
    check("summarise survives an empty dict", isinstance(NW.summarise({}), str))

    # NO NETWORK. The owner's standing rule is that nothing may spend X credits
    # without explicit approval, and this module's entire premise is that the
    # measurement is free.
    src = (REPO / "utils" / "newswire.py").read_text()
    for banned in ("requests", "urllib", "search_x_tweets", "http"):
        check(f"the module never reaches for {banned}", banned not in src)


def test_the_columns_exist_and_are_not_duplicated():
    print("\nschema: every counter has a column, and no column has a twin")
    sql = MIGRATION.read_text()
    m = NW.measure([row(0, ch="newswire")], wire_posts=ids(1, 100),
                   main_posts=ids(1), wire_state="fetched", wire_billed=1,
                   prices=FLAT, volumes=VOLS)
    for k in m:
        check(f"{k} exists in the migration", k in sql)
    check("the columns are re-addable to an existing table",
          "add column if not exists newswire_state" in sql)
    check("the constraints are guarded so the file can be re-run",
          "pg_constraint" in sql and "signal_log_once_per_event" in sql)
    # rows_newswire and newswire_posts were the same number by construction.
    sl = (REPO / "utils" / "signal_log.py").read_text()
    check("newswire_posts is no longer written beside rows_newswire",
          '"newswire_posts": _int' not in sl)


def main() -> int:
    print("=" * 74)
    print("  newswire: measuring a paid channel without paying for it")
    print("=" * 74)
    test_an_empty_corpus_says_why()
    test_held_is_not_billed()
    test_the_counterfactual_sees_the_same_tape()
    test_it_detects_a_wire_that_genuinely_decides()
    test_no_wire_rows_is_absent_not_unchanged()
    test_duplicate_counts_what_the_pages_actually_drop()
    test_ledger_counts_only_the_wire_channel()
    test_failure_is_all_or_nothing()
    test_it_never_raises_and_costs_nothing()
    test_the_columns_exist_and_are_not_duplicated()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
