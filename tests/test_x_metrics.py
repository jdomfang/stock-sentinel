#!/usr/bin/env python3
"""Prove the X-effectiveness telemetry is accurate and cannot break a scan.

WHAT IS AT RISK

1. BEHAVIOUR DRIFT. extract_tickers_detailed replaces the body of
   extract_tickers, which drives every ticker the product has ever shown. If
   the wrapper is not byte-identical, this "measurement" change silently
   alters recommendations -- and its own baseline becomes unreadable, because
   the before and after would no longer be the same pipeline.

2. A DISHONEST PARTITION. The four post buckets exist to answer "how much did
   we throw away". If they double-count or lose posts, the waste number is
   wrong in the flattering direction and every decision made from it is wrong
   too.

3. BREAKING A PAID SCAN. Telemetry that raises costs the user the product to
   gain a metric. Every entry point must swallow its errors.

No network. Supabase is stubbed.

Usage:
    python3 tests/test_x_metrics.py
"""

from __future__ import annotations

import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import x_metrics  # noqa: E402
from utils.sentiment import (  # noqa: E402
    EXCLUDED_WORDS, extract_tickers, extract_tickers_detailed,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def legacy_extract(text):
    """An independent restatement of the extraction algorithm.

    Kept as a second implementation -- including the duplicate-emitting defect,
    because the point is equivalence with what SHIPS, not with what it should
    have been. Any accidental drift in the real function shows up here.

    The cashtag bound is {1,5}, deliberately widened from {2,5}: ticker_master
    holds 21 single-letter symbols and the old bound made $T, $F, $V, $C and $D
    unextractable. That was a real defect, not a style choice -- $D appeared in
    a live corpus, in posts we had paid for, and was silently discarded.
    """
    tickers, seen = [], set()
    for m in re.findall(r'\$([A-Z]{1,5})\b', text):
        if m not in EXCLUDED_WORDS and m not in seen:
            tickers.append(m); seen.add(m)
    scored = []
    for m in re.findall(r'\b([A-Z]{2,5})\b', text):
        if m not in EXCLUDED_WORDS and m not in seen:
            scored.append((m, 1.0 if 3 <= len(m) <= 4 else 0.5))
    scored.sort(key=lambda x: x[1], reverse=True)
    for t, _ in scored[:5]:
        tickers.append(t); seen.add(t)
    return tickers


def corpus(n: int = 3000) -> list[str]:
    random.seed(20260806)
    syms = ["CAT", "LMT", "BA", "AIR", "RAIL", "BOOM", "NVDA", "GE", "HON",
            "UPS", "XLI", "ITA", "PMI", "ISM", "T", "ON", "AA", "TT"]
    words = ["beats", "guidance", "the", "supply", "chain", "backlog", "orders",
             "up", "strong", "FREIGHT", "VOLUMES", "DATA", "NOW", "OPEN", "a"]
    out = ["", "no tickers here at all", "$CAT $CAT CAT", "RAIL AIR RAIL AIR RAIL"]
    for _ in range(n):
        toks = []
        for _ in range(random.randint(1, 25)):
            r = random.random()
            toks.append("$" + random.choice(syms) if r < 0.3
                        else random.choice(syms) if r < 0.65
                        else random.choice(words))
        out.append(" ".join(toks))
    return out


# ── the wrapper must not change the product ──────────────────────────────────

def test_extraction_is_unchanged():
    print("\nequivalence: measuring must not alter a single recommendation")
    texts = corpus()
    bad = [t for t in texts if legacy_extract(t) != extract_tickers(t)]
    check(f"byte-identical across {len(texts)} inputs", not bad,
          f"{len(bad)} mismatches, first: {bad[0][:60] if bad else ''}")

    # The duplicate defect is preserved on purpose -- pin it so a future
    # "cleanup" cannot silently change mention counts and, with them, the
    # validation ranking that decides which tickers a user sees.
    dupes = extract_tickers("RAIL and AIR rising. RAIL up, AIR up, RAIL again.")
    check("the known duplicate defect is preserved, not quietly fixed",
          dupes == ['RAIL', 'AIR', 'RAIL', 'AIR', 'RAIL'], str(dupes))


def test_single_letter_cashtags_are_extracted():
    print("\ncashtags: one-letter tickers are real and were being dropped")
    # 21 single-letter symbols exist in ticker_master. $T is AT&T, likely the
    # single most-discussed name in Telecommunications, and it was invisible to
    # every scan ever run. Measured: $D (Dominion) appeared in a live utilities
    # corpus we had paid for and was silently discarded by the {2,5} bound.
    for sym in ["T", "F", "V", "C", "D"]:
        got = extract_tickers_detailed(f"${sym} looks strong today")["cashtag"]
        check(f"${sym} is extracted", got == [sym], str(got))

    # Currency amounts must NOT match: the character after $ is a digit, so the
    # pattern never engages. This is what makes widening to one char safe.
    for txt in ["revenue of $5B this quarter", "$10M raised", "$1T market cap"]:
        d = extract_tickers_detailed(txt)
        check(f"{txt!r} yields no cashtag", d["cashtag"] == [], str(d["cashtag"]))

    # Bare single letters must still be refused -- at one character every "A"
    # and "I" in ordinary English would become a ticker candidate.
    d = extract_tickers_detailed("A T V C D are all letters")
    check("bare single letters are still refused",
          not any(len(t) == 1 for t in d["bare"]), str(d["bare"]))

    # Upper bound stays 5: the 73 longer symbols are preferred shares written
    # like AHRT^A, which no [A-Z] class can match at any length.
    d = extract_tickers_detailed("$TOOLONG should not match as written")
    check("six or more letters is not a cashtag", "TOOLONG" not in d["cashtag"],
          str(d["cashtag"]))


def test_provenance_separates_cashtags_from_bare_words():
    print("\nprovenance: $CAT and bare CAT are distinguishable")
    d = extract_tickers_detailed("$CAT beats. RAIL and AIR freight rising.")
    check("cashtags identified", d["cashtag"] == ["CAT"], str(d["cashtag"]))
    check("bare words identified", set(d["bare"]) == {"RAIL", "AIR"}, str(d["bare"]))
    check("a symbol is never in both", not (set(d["cashtag"]) & set(d["bare"])))

    d2 = extract_tickers_detailed("$CAT $CAT $CAT strong")
    check("repeated cashtags are counted", d2["cashtag_counts"].get("CAT") == 3,
          str(d2["cashtag_counts"]))
    check("but only listed once", d2["cashtag"] == ["CAT"], str(d2["cashtag"]))

    # Step 1 claims a symbol, so Step 2 must not re-add it as a bare word --
    # otherwise a cashtag post would double-count its own ticker.
    d3 = extract_tickers_detailed("$CAT is great, CAT is great")
    check("a cashtag symbol is not re-counted as bare", d3["bare"] == [], str(d3["bare"]))


# ── the partition must be honest ─────────────────────────────────────────────

def build(posts):
    t = x_metrics.ScanTally()
    for cash, bare in posts:
        t.record(cash, bare,
                 {s: 1 for s in cash}, {s: 1 for s in bare})
    return t


def test_every_post_lands_in_exactly_one_bucket():
    print("\npartition: the four buckets account for every processed post")
    t = build([
        (["CAT"], []),        # displayed -> contributed
        ([], ["AIR"]),        # displayed -> contributed
        (["HON"], []),        # validated but ranked out -> hidden
        ([], ["ZZZZ"]),       # symbol found, not valid -> no_valid_ticker
        ([], []),             # nothing at all -> waste
    ])
    s = t.finalize(validated={"CAT", "AIR", "HON"}, displayed=["CAT", "AIR"])

    check("processed counted", s["posts_processed"] == 5, str(s["posts_processed"]))
    check("contributed", s["posts_contributed"] == 2, str(s["posts_contributed"]))
    check("validated but hidden", s["posts_validated_hidden"] == 1,
          str(s["posts_validated_hidden"]))
    check("candidates, none valid", s["posts_no_valid_ticker"] == 1,
          str(s["posts_no_valid_ticker"]))
    check("no candidates at all", s["posts_no_candidates"] == 1,
          str(s["posts_no_candidates"]))

    total = (s["posts_contributed"] + s["posts_validated_hidden"]
             + s["posts_no_valid_ticker"] + s["posts_no_candidates"])
    check("buckets sum to processed (the table's CHECK constraint)",
          total == s["posts_processed"], f"{total} != {s['posts_processed']}")


def test_the_bare_word_variant_is_derivable_without_running_it():
    print("\nderivation: the cashtags-only variant needs no extra X spend")
    # A run with bare-word extraction disabled keeps only symbols seen with a $.
    t = build([
        (["CAT"], ["AIR"]),   # cashtag validated -> survives
        ([], ["RAIL"]),       # bare only        -> disappears
        ([], ["AIR"]),        # bare only        -> disappears
    ])
    s = t.finalize(validated={"CAT", "AIR", "RAIL"}, displayed=["CAT", "AIR", "RAIL"])

    check("posts carrying a validating cashtag are counted",
          s["posts_with_valid_cashtag"] == 1, str(s["posts_with_valid_cashtag"]))

    prov = s["ticker_provenance"]
    survivors = [t_ for t_, p in prov.items() if p["cashtag"] > 0]
    check("survivors of a cashtags-only run are computable", survivors == ["CAT"],
          str(survivors))
    check("and the losses are named", sorted(
        t_ for t_, p in prov.items() if p["cashtag"] == 0) == ["AIR", "RAIL"], str(prov))


def test_phantom_suspects_use_bare_share_not_a_binary_test():
    print("\nphantoms: overwhelmingly-bare evidence, on enough mentions to matter")
    # AIR (AAR Corp) and RAIL (FreightCar America) are real Industrials symbols
    # AND ordinary English words, so prose about freight mints them as picks.
    t = build([([], ["AIR", "RAIL"]) for _ in range(4)] + [(["CAT"], [])] * 6)
    s = t.finalize(validated={"CAT", "AIR", "RAIL"}, displayed=["CAT", "AIR", "RAIL"])
    check("bare-only tickers with real volume are suspect",
          sorted(s["_suspect_symbols"]) == ["AIR", "RAIL"], str(s["_suspect_symbols"]))
    check("a cashtag-backed ticker is not", "CAT" not in s["_suspect_symbols"])

    # THE CASE THE BINARY RULE MISSED. One legitimate $CAT anywhere used to
    # whitewash unlimited bare "CAT" inflation elsewhere -- and that is exactly
    # what the preserved duplicate defect amplifies hardest.
    t2 = build([([], ["CAT"]) for _ in range(40)] + [(["CAT"], [])])
    s2 = t2.finalize(validated={"CAT"}, displayed=["CAT"])
    check("40 bare mentions are not excused by 1 cashtag",
          s2["_suspect_symbols"] == ["CAT"], str(s2["ticker_provenance"]))

    # RECALIBRATED AGAINST PRODUCTION DATA. The first version of this rule
    # required bare >= 3, which was tuned against a hypothetical "RAIL x40"
    # case. The first three real scans showed mention counts are 1-2, so that
    # floor could never fire -- it reported zero suspects while DOW sat in the
    # industrials table with cashtag=0, bare=2, and "the Dow" essentially never
    # means Dow Inc. Only data could have caught that; no reviewer knew the
    # distribution.
    #
    # The rule is now recall-oriented on purpose: this is a REVIEW QUEUE, and a
    # fabricated pick shown to a paying user costs more than clearing a false
    # alarm. Genuine low-mention tickers land here too, and that is accepted.
    t3 = build([([], ["DOW"]), ([], ["DOW"])])
    s3 = t3.finalize(validated={"DOW"}, displayed=["DOW"])
    check("bare-only evidence is queued even at two mentions",
          s3["_suspect_symbols"] == ["DOW"], str(s3["_suspect_symbols"]))

    # Independent confirmation clears it: someone wrote $SYM, so the text was
    # demonstrably about the security.
    t4 = build([([], ["HON"]), (["HON"], [])])
    s4 = t4.finalize(validated={"HON"}, displayed=["HON"])
    check("a single cashtag clears a low-mention ticker",
          s4["_suspect_symbols"] == [], str(s4["_suspect_symbols"]))


def test_cashtag_mentions_are_counted_per_post_not_per_dollar_sign():
    print("\nunits: the two halves of provenance must be comparable")
    # extract_tickers dedupes cashtags within a post but NOT bare words, so a
    # post saying "$CAT $CAT $CAT" contributes ONE mention while "CAT CAT CAT"
    # contributes three. Counting raw $-occurrences would put the two halves of
    # ticker_provenance in different units and silently corrupt every ratio
    # built from them -- including the phantom rule.
    t = x_metrics.ScanTally()
    t.record(["CAT"], [], {"CAT": 3}, {})
    check("three $CAT in one post is one mention", t.cashtag_totals["CAT"] == 1,
          str(t.cashtag_totals))
    t.record(["CAT"], [], {"CAT": 1}, {})
    check("a second post adds one more", t.cashtag_totals["CAT"] == 2,
          str(t.cashtag_totals))


def test_validatable_ignores_the_ten_ticker_cap():
    print("\ncensoring: the headline count must not be clipped by the early stop")
    # validated_set can never exceed TARGET_VALIDATED=10, and validation walks
    # candidates in MENTION-RANK order. Bare-word phantoms are ordinary English
    # words -- the most frequent tokens in any corpus -- so they consume the ten
    # slots first and real cashtag symbols are never checked. Measuring the
    # phantom problem with a metric phantoms crowd out would answer "can we drop
    # bare-word extraction?" with a confident NO on evidence that could never
    # have said anything else.
    t = x_metrics.ScanTally()
    for i in range(25):
        t.record([f"C{i}"], [f"B{i}"], {}, {f"B{i}": 1})

    everything, cashtag_only = t.validatable(lambda s: True)
    check("counts every candidate, not the first ten", everything == 50,
          str(everything))
    check("and reports the cashtag-only subset", cashtag_only == 25,
          str(cashtag_only))

    # It must apply the CALLER's rule, so it cannot drift from what the scan
    # actually accepts.
    only_c, only_c_cash = t.validatable(lambda s: s.startswith("C"))
    check("uses the caller's own validation predicate", only_c == 25, str(only_c))
    check("and splits it correctly", only_c_cash == 25, str(only_c_cash))

    # A predicate that throws must not take the scan down with it.
    def boom(_):
        raise RuntimeError("simulated ticker_master failure")
    check("a broken predicate degrades to zero, not an exception",
          t.validatable(boom) == (0, 0))


def test_provenance_stays_small():
    print("\nstorage: only surviving symbols are kept")
    t = build([([], [f"Z{i}" for i in range(5)]) for _ in range(50)])
    s = t.finalize(validated={"Z0"}, displayed=["Z0"])
    check("unvalidated noise is not stored", list(s["ticker_provenance"]) == ["Z0"],
          str(list(s["ticker_provenance"])[:6]))


def test_an_empty_scan_is_recorded_as_total_waste():
    print("\nhonesty: a scan that produced nothing is the key data point")
    t = build([([], []) for _ in range(99)])
    s = t.finalize(validated=set(), displayed=[])
    check("all 99 posts land in the waste bucket", s["posts_no_candidates"] == 99,
          str(s["posts_no_candidates"]))
    check("nothing displayed", s["displayed"] == 0)
    check("no false phantom signal", s["phantom_suspects"] == 0)


# ── it must never break a scan ───────────────────────────────────────────────

def test_telemetry_never_raises_into_a_paid_scan():
    print("\nfailure policy: a broken recorder costs a metric, not the product")
    saved = x_metrics._config
    x_metrics._config = lambda name, default="": ""
    try:
        ok = x_metrics.record_scan(
            event_id=None, subject="industrials", query="q",
            tally=build([(["CAT"], [])]), validated={"CAT"}, displayed=["CAT"],
            posts_billed=99, pages_fetched=1, from_cache=False,
        )
        check("unconfigured Supabase reports failure, does not raise", ok is False)
    except Exception as e:
        check("unconfigured Supabase reports failure, does not raise", False,
              f"raised {type(e).__name__}")
    finally:
        x_metrics._config = saved

    x_metrics._config = lambda name, default="": (
        "http://127.0.0.1:9" if name == "SUPABASE_URL" else "key")
    try:
        ok = x_metrics.record_scan(
            event_id=None, subject="s", query="q",
            tally=build([([], [])]), validated=set(), displayed=[],
            posts_billed=1, pages_fetched=1, from_cache=False,
        )
        check("unreachable Supabase reports failure, does not raise", ok is False)
    except Exception as e:
        check("unreachable Supabase reports failure, does not raise", False,
              f"raised {type(e).__name__}")
    finally:
        x_metrics._config = saved

    class Exploding:
        def finalize(self, *a, **k):
            raise ValueError("simulated bug in summarisation")
    try:
        ok = x_metrics.record_scan(
            event_id=None, subject="s", query="q", tally=Exploding(),
            validated=set(), displayed=[], posts_billed=0, pages_fetched=0,
            from_cache=False,
        )
        check("a bug inside the tally is swallowed", ok is False)
    except Exception as e:
        check("a bug inside the tally is swallowed", False, f"raised {type(e).__name__}")


def test_query_hash_tracks_query_versions():
    print("\nfeedback loop: each query version gets its own track record")
    a = x_metrics.query_hash("(aerospace OR defense) lang:en")
    check("deterministic", a == x_metrics.query_hash("(aerospace OR defense) lang:en"))
    check("an edit starts a new record",
          a != x_metrics.query_hash("(aerospace OR defense) has:cashtags lang:en"))
    check("matches the corpus cache's scheme", len(a) == 12, a)


def test_ticker_master_is_actually_cached():
    """A memo that looks live and is dead costs ~7,600 rows per Streamlit rerun.

    @st.cache_data keys on (module, qualname) in a global store, so decorating a
    function NESTED inside another still hit one entry. Its replacement keeps
    state in a closure, so re-decorating on each call built a fresh empty cache.
    Measured before the fix: five calls, five full loads of ticker_master --
    and pages/Discovery.py calls this on every scan, on every rerun.
    """
    from unittest.mock import patch
    import utils.finance as F

    print("\nticker master: the cache must survive repeated calls")
    F._cached_ticker_master.cache_clear()
    calls = []
    with patch.object(F, "_load_ticker_master_from_supabase_table",
                      lambda: (calls.append(1), {"AAA": {"name": "A"}})[1]):
        for _ in range(5):
            F.get_ticker_master_list()
    check("5 calls -> 1 underlying load", len(calls) == 1, f"{len(calls)} loads")
    check("the decorator is applied at module scope, not inside a function",
          hasattr(F, "_cached_ticker_master"))
    F._cached_ticker_master.cache_clear()


def main() -> int:
    print("=" * 74)
    print("  x_call_metrics: honest partition, unchanged behaviour, safe failure")
    print("=" * 74)

    test_extraction_is_unchanged()
    test_single_letter_cashtags_are_extracted()
    test_provenance_separates_cashtags_from_bare_words()
    test_every_post_lands_in_exactly_one_bucket()
    test_the_bare_word_variant_is_derivable_without_running_it()
    test_phantom_suspects_use_bare_share_not_a_binary_test()
    test_cashtag_mentions_are_counted_per_post_not_per_dollar_sign()
    test_validatable_ignores_the_ten_ticker_cap()
    test_provenance_stays_small()
    test_an_empty_scan_is_recorded_as_total_waste()
    test_telemetry_never_raises_into_a_paid_scan()
    test_query_hash_tracks_query_versions()

    test_ticker_master_is_actually_cached()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name, detail in FAILED:
            print(f"    - {name}: {detail}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
