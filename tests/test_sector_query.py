#!/usr/bin/env python3
"""Prove generated sector queries cover the sector and cannot break validation.

WHAT IS AT RISK

1. THE SECTOR MAP MOVED. pages/Discovery.py used to define its own
   ui_to_nasdaq_sectors dict; it now imports UI_TO_NASDAQ from utils.sector_query.
   If a single string drifted in that move, validation silently accepts nothing
   and every scan of that sector returns an empty table. The first test pins the
   mapping to exactly what the inline dict held.

2. SILENT TICKER LOSS. If packing drops a ticker, the query stops asking about
   it and it can never be discovered -- with no error anywhere. Coverage has to
   be exact, not approximate.

3. OVER-LENGTH QUERIES. X rejects anything past ~512 characters with a 400, so
   an off-by-one in the packer turns into a scan that fails at fetch time.

4. THE PHANTOM PROBLEM AT THE NAME LAYER. Company names include real firms
   called INNOVATE and Outdoor. Matching those against prose recreates exactly
   the fabrications that bare-word extraction produced -- measured: "makes it
   much harder to innovate" matched INNOVATE Corp, and "Outdoor feetpaws -$450"
   matched Outdoor Holding.

No network: the ticker universe is stubbed.

Usage:
    python3 tests/test_sector_query.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import sector_query as sq  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def stub(universe):
    sq._CACHE.clear()
    sq.sector_universe = lambda sector: universe


REAL = sq.sector_universe


def restore():
    sq.sector_universe = REAL
    sq._CACHE.clear()


# ── the mapping that validation depends on ───────────────────────────────────

def test_the_sector_map_is_exactly_what_discovery_used_to_hold():
    print("\nmapping: moving it out of Discovery must not have changed a string")
    # Verbatim from pages/Discovery.py before the import replaced it. A typo
    # here does not raise -- it makes selected_nasdaq_sectors empty, validation
    # rejects every candidate, and the sector silently returns nothing.
    was = {
        "tech": {"Technology"},
        "healthcare": {"Health Care"},
        "energy": {"Energy"},
        "finance": {"Finance"},
        "consumer": {"Consumer Discretionary", "Consumer Staples"},
        "utilities": {"Utilities"},
        "real estate": {"Real Estate"},
        "industrials": {"Industrials"},
        "materials": {"Basic Materials"},
        "communication": {"Telecommunications"},
    }
    check("every UI sector still maps", set(sq.UI_TO_NASDAQ) == set(was),
          f"missing {set(was) - set(sq.UI_TO_NASDAQ)}, extra {set(sq.UI_TO_NASDAQ) - set(was)}")
    for ui, nasdaq in was.items():
        check(f"{ui!r} -> {sorted(nasdaq)}", sq.UI_TO_NASDAQ.get(ui) == nasdaq,
              str(sq.UI_TO_NASDAQ.get(ui)))


# ── coverage ─────────────────────────────────────────────────────────────────

def test_every_ticker_is_asked_about_exactly_once():
    print("\ncoverage: packing must not drop or duplicate a ticker")
    uni = [{"symbol": f"TK{i:04d}", "name": f"Test {i} Corp", "dollar_volume": 1e9 - i}
           for i in range(500)]
    stub(uni)
    try:
        baskets = sq.build_baskets("utilities", "cashtag")
        packed = []
        for b in baskets:
            packed += [t.strip() for t in b.split(")")[0].lstrip("(").split(" OR ")]
        check("no ticker is lost", len(packed) == 500, f"packed {len(packed)}")
        check("no ticker is duplicated", len(set(packed)) == 500, f"{len(set(packed))} unique")
        check("all of them are ours",
              set(packed) == {f"${t['symbol']}" for t in uni})
        # A dropped ticker is invisible: no error, it just stops being findable.
        check("more than one basket was needed", len(baskets) > 1, str(len(baskets)))
    finally:
        restore()


def test_no_query_can_exceed_x_s_limit():
    print("\nlimit: an over-length query is a 400 at fetch time")
    for n, namelen in [(500, 4), (50, 5), (3, 2), (1, 5)]:
        uni = [{"symbol": "A" * namelen + str(i % 10), "name": f"Co {i} Inc",
                "dollar_volume": 1e6 - i} for i in range(n)]
        stub(uni)
        try:
            for kind in ("cashtag", "name"):
                over = [q for q in sq.build_baskets("tech", kind) if len(q) > sq.MAX_QUERY_LEN]
                check(f"{kind} baskets within {sq.MAX_QUERY_LEN} chars (n={n})",
                      not over, f"{len(over)} too long, first={len(over[0]) if over else 0}")
        finally:
            restore()


def test_the_loudest_names_land_in_the_first_basket():
    print("\norder: sorting quarantines the loud names into basket 1")
    # This is the whole point of volume-descending. A capped request returns
    # whatever is loudest inside it, so mixing a mega-cap into every basket
    # would let it crowd out the quiet names everywhere. Sorting confines that
    # to one basket which can then be capped deliberately.
    uni = ([{"symbol": "LOUD1", "name": "Loud One Inc", "dollar_volume": 9e9},
            {"symbol": "LOUD2", "name": "Loud Two Inc", "dollar_volume": 8e9}]
           + [{"symbol": f"Q{i:04d}", "name": f"Quiet {i} Inc", "dollar_volume": 1000 - i}
              for i in range(200)])
    stub(uni)
    try:
        baskets = sq.build_baskets("industrials", "cashtag")
        check("the loudest ticker is in basket 1", "$LOUD1" in baskets[0], baskets[0][:60])
        check("so is the second loudest", "$LOUD2" in baskets[0])
        check("and the quiet tail is not", "$LOUD1" not in " ".join(baskets[1:]))
    finally:
        restore()


# ── the name channel must not recreate the phantom problem ───────────────────

def test_single_word_company_names_are_refused():
    print("\nnames: one-word names are how phantoms come back")
    # Measured against real corpora: "INNOVATE" matched "harder to innovate",
    # and "Outdoor" matched "Outdoor feetpaws -$450". Both are real companies
    # whose names are ordinary English words.
    uni = [
        {"symbol": "VATE", "name": "INNOVATE Corp", "dollar_volume": 5e6},
        {"symbol": "POWW", "name": "Outdoor Holding Company", "dollar_volume": 4e6},
        {"symbol": "HWM", "name": "Howmet Aerospace Inc. Common Stock", "dollar_volume": 3e6},
        {"symbol": "NEE", "name": "NextEra Energy Inc.", "dollar_volume": 2e6},
    ]
    stub(uni)
    try:
        q = " ".join(sq.build_baskets("utilities", "name"))
        check("multi-word names are used", '"Howmet Aerospace"' in q and '"NextEra Energy"' in q, q)
        check("INNOVATE is refused", "INNOVATE" not in q.upper(), q)
        check("Outdoor is refused", "Outdoor" not in q, q)
    finally:
        restore()


def test_corporate_boilerplate_is_stripped():
    print("\nnames: match what a person writes, not what the filing says")
    cases = [
        ("Howmet Aerospace Inc. Common Stock", "Howmet Aerospace"),
        ("NextEra Energy Inc.", "NextEra Energy"),
        ("Consolidated Edison Inc (The)", "Consolidated Edison"),
        ("Brookfield Renewable Partners L.P. Limited Partnership", "Brookfield Renewable Partners"),
    ]
    for raw, want in cases:
        got = sq.normalize_company_name(raw)
        check(f"{raw[:34]!r} -> {want!r}", got == want, got)


# ── failing safe ─────────────────────────────────────────────────────────────

def test_an_unusable_sector_yields_no_baskets_rather_than_a_bad_query():
    print("\nfail-safe: no baskets means Discovery falls back to the topic query")
    stub([])
    try:
        check("an empty universe yields nothing", sq.build_baskets("utilities", "cashtag") == [])
    finally:
        restore()
    # An unmapped sector must not build a query over the WRONG universe.
    sq._CACHE.clear()
    check("an unknown sector yields nothing", sq.sector_universe("nonsense") == [])
    check("a blank sector yields nothing", sq.sector_universe("") == [])


# ── the loop that spends money ───────────────────────────────────────────────

class FakeX:
    """Records every request and replays a scripted response per basket."""

    def __init__(self, script: dict[str, list[dict]]):
        self.script = script          # query -> list of per-call responses
        self.calls: list[tuple[str, int, str | None]] = []

    def __call__(self, query, per_page, token):
        self.calls.append((query, per_page, token))
        queue = self.script.get(query, [])
        i = sum(1 for c in self.calls if c[0] == query) - 1
        if i < len(queue):
            return queue[i]
        return {"success": True, "tweets": [], "next_token": None}


def posts(n, tag="t"):
    return {"success": True, "tweets": [{"id": f"{tag}{i}"} for i in range(n)],
            "next_token": None}


def test_an_empty_basket_does_not_end_the_sweep():
    print("\nfetcher: a quiet basket must not abandon the rest")
    # THE BUG THIS EXISTS TO PREVENT. Baskets are volume-sorted, so 2..N hold
    # only quiet names and returning zero is their NORMAL outcome. An earlier
    # version treated the first empty page as "sector exhausted" and abandoned
    # every remaining basket -- silently defeating full-sector coverage.
    x = FakeX({"b1": [posts(25, "a")], "b2": [posts(0)],
               "b3": [posts(4, "c")], "b4": [posts(0)]})
    f = sq.BasketFetcher(["b1", "b2", "b3", "b4"], fetch=x, per_page=25)
    got = []
    while not f.exhausted:
        got.append(f.next_page())
    check("every basket was fetched", [c[0] for c in x.calls] == ["b1", "b2", "b3", "b4"],
          str([c[0] for c in x.calls]))
    check("the empty basket did not stop it", len(x.calls) == 4, str(len(x.calls)))
    check("posts from AFTER the empty basket were collected",
          sum(len(p) for p in f.pages) == 29, str(sum(len(p) for p in f.pages)))
    check("has_more was true while work remained", got[0]["has_more"] is True)
    check("and false at the end", got[-1]["has_more"] is False)


def test_breadth_before_depth():
    print("\nfetcher: every basket's page 1 before any basket's page 2")
    # Paginating basket 1 buys more posts about tickers it already covered.
    # Fetching basket 2 covers tickers nobody has asked about -- only the second
    # can surface a ticker we do not have.
    x = FakeX({
        "b1": [dict(posts(25, "a"), next_token="t1"), posts(25, "a2")],
        "b2": [dict(posts(25, "b"), next_token="t2"), posts(25, "b2")],
        "b3": [posts(3, "c")],
    })
    f = sq.BasketFetcher(["b1", "b2", "b3"], fetch=x, per_page=25)
    for _ in range(5):
        if f.exhausted:
            break
        f.next_page()
    order = [c[0] for c in x.calls]
    check("first pass covers every basket", order[:3] == ["b1", "b2", "b3"], str(order))
    check("only then does it go deeper", order[3:] == ["b1", "b2"], str(order))
    check("continuation tokens are carried per basket",
          x.calls[3][2] == "t1" and x.calls[4][2] == "t2",
          str([c[2] for c in x.calls]))


def test_it_cannot_run_away():
    print("\nfetcher: bounded, because every request is a serial round trip")
    # Requests are free in billing terms, but each one lengthens the window in
    # which a user re-click aborts the scan and forces a refund.
    x = FakeX({f"b{i}": [dict(posts(5), next_token="always")] * 99 for i in range(3)})
    f = sq.BasketFetcher(["b0", "b1", "b2"], fetch=x, per_page=25, max_requests=7)
    n = 0
    while not f.exhausted and n < 100:
        f.next_page(); n += 1
    check("stops at max_requests", f.requests == 7, str(f.requests))
    check("and reports itself exhausted", f.exhausted is True)


def test_a_failing_basket_propagates():
    print("\nfetcher: an X error must reach the caller, not be swallowed")
    # The scan needs to see this so _x_api_error is set, the corpus is NOT
    # cached, and the credit refund path can run.
    x = FakeX({"b1": [posts(5)], "b2": [{"success": False, "error": "X 429"}]})
    f = sq.BasketFetcher(["b1", "b2"], fetch=x, per_page=25)
    f.next_page()
    bad = f.next_page()
    check("failure is reported", bad.get("success") is False, str(bad))
    check("the error text survives", "429" in str(bad.get("error")), str(bad))
    check("no phantom page was recorded", len(f.pages) == 1, str(len(f.pages)))


def test_pages_are_what_the_scan_bills_for():
    print("\nfetcher: .pages is the billing and corpus record")
    x = FakeX({"b1": [posts(25)], "b2": [posts(7)], "b3": [posts(0)]})
    f = sq.BasketFetcher(["b1", "b2", "b3"], fetch=x, per_page=25)
    while not f.exhausted:
        f.next_page()
    check("one entry per successful request", len(f.pages) == 3, str(len(f.pages)))
    check("flattening gives every post bought",
          len([t for p in f.pages for t in p]) == 32,
          str(len([t for p in f.pages for t in p])))
    check("the requested page size is honoured",
          all(c[1] == 25 for c in x.calls), str([c[1] for c in x.calls]))


def test_the_first_pass_gate():
    print("\nfull pass: the scan must not stop before every basket is sampled")
    # Baskets are volume-ordered and volume does not predict chatter. Measured
    # on utilities, four of the six loudest tickers were outside basket 1 --
    # $AVA (14 mentions, basket 2) and $AWX (12, basket 4). A scan that stops
    # once basket 1 fills ten slots returns exactly the names that are NOT
    # unusual, which is the opposite of the feature's purpose.
    x = FakeX({f"b{i}": [posts(5)] for i in range(4)})
    f = sq.BasketFetcher([f"b{i}" for i in range(4)], fetch=x, per_page=25)
    check("not done before any fetch", f.first_pass_done is False)
    for i in range(3):
        f.next_page()
        check(f"still not done after {i+1}/4 baskets", f.first_pass_done is False)
    f.next_page()
    check("done once every basket has a page", f.first_pass_done is True)


def test_parallel_prefetch_covers_every_basket_once():
    print("\nparallel: wide sectors fetch their first pass concurrently")
    # Finance has 27 baskets; serialising them is ~30s of a paid scan inside the
    # window where a re-click aborts the run.
    x = FakeX({f"b{i}": [posts(3, f"p{i}")] for i in range(8)})
    f = sq.BasketFetcher([f"b{i}" for i in range(8)], fetch=x, per_page=25)
    f.prefetch_first_pass()
    check("every basket was fetched exactly once",
          sorted(c[0] for c in x.calls) == sorted(f"b{i}" for i in range(8)),
          str(sorted(c[0] for c in x.calls)))
    # NOT complete yet: prefetch has PAID for these pages but the caller has
    # not seen their tickers. Counting them as sampled at fetch time let the
    # scan stop before processing baskets 2..N -- exactly what the full pass
    # exists to prevent, while still being billed for every basket.
    check("first pass NOT complete until pages are delivered",
          f.first_pass_done is False)
    delivered = []
    while not f.exhausted:
        r = f.next_page()
        if r.get("tweets"): delivered.append(len(r["tweets"]))
    check("prefetched pages are delivered, not refetched",
          len(x.calls) == 8, f"{len(x.calls)} calls")
    check("all posts reach the caller", sum(delivered) == 24, str(sum(delivered)))
    check("first pass complete once every page is delivered", f.first_pass_done is True)


def test_small_sectors_are_not_parallelised():
    print("\nparallel: 3 baskets or fewer stay sequential")
    x = FakeX({f"b{i}": [posts(2)] for i in range(3)})
    f = sq.BasketFetcher([f"b{i}" for i in range(3)], fetch=x, per_page=25)
    f.prefetch_first_pass()
    check("no requests made by prefetch", x.calls == [], str(x.calls))
    check("first pass not yet done", f.first_pass_done is False)


def test_no_baskets_is_inert():
    print("\nfetcher: an empty basket list makes no calls at all")
    x = FakeX({})
    f = sq.BasketFetcher([], fetch=x, per_page=25)
    check("reports exhausted immediately", f.exhausted is True)
    r = f.next_page()
    check("returns a benign empty page", r == {"success": True, "tweets": [], "has_more": False},
          str(r))
    check("and never called X", x.calls == [], str(x.calls))



def test_company_alias_survives_refactors():
    """The alias is the difference between reading a company and reading a ticker.

    A regex that collapsed a duplicated config helper also swallowed the
    _GENERIC_SINGLE_WORD frozenset sitting under it. company_alias then raised
    NameError, its only caller swallowed that in an `except Exception`, and
    every Deep Analyze query silently degraded from ($TSLA OR "Tesla") to
    $TSLA-only.

    Nothing caught it. This suite passed 67/67 because nothing here called
    company_alias, and the golden oracle does not reach retrieval. The cost was
    not only a thinner corpus: the query text is what corpus_cache hashes, so
    every cached corpus missed and re-bought up to 300 billed X posts.
    """
    print("\ncompany_alias: real names resolve, generic words are refused")
    for raw, want in (("Tesla, Inc.", "Tesla"), ("Apple Inc.", "Apple"),
                      ("NVIDIA Corporation", "NVIDIA"),
                      ("MP Materials Corp.", "MP Materials")):
        got = sq.company_alias(raw)
        check(f"{raw!r} -> {want!r}", got == want, repr(got))
    for raw in ("Visa Inc.", "Southern Company"):
        got = sq.company_alias(raw)
        check(f"{raw!r} is refused as generic", got == "", repr(got))
    check("the vocabulary the refusal depends on still exists",
          bool(getattr(sq, "_GENERIC_SINGLE_WORD", None))
          and "visa" in sq._GENERIC_SINGLE_WORD)


def main() -> int:
    print("=" * 74)
    print("  sector_query: exact coverage, safe names, unchanged mapping")
    print("=" * 74)
    test_the_sector_map_is_exactly_what_discovery_used_to_hold()
    test_every_ticker_is_asked_about_exactly_once()
    test_no_query_can_exceed_x_s_limit()
    test_the_loudest_names_land_in_the_first_basket()
    test_single_word_company_names_are_refused()
    test_corporate_boilerplate_is_stripped()
    test_company_alias_survives_refactors()
    test_an_unusable_sector_yields_no_baskets_rather_than_a_bad_query()
    test_an_empty_basket_does_not_end_the_sweep()
    test_breadth_before_depth()
    test_it_cannot_run_away()
    test_a_failing_basket_propagates()
    test_pages_are_what_the_scan_bills_for()
    test_the_first_pass_gate()
    test_parallel_prefetch_covers_every_basket_once()
    test_small_sectors_are_not_parallelised()
    test_no_baskets_is_inert()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for name, detail in FAILED:
        print(f"    - {name}: {detail}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
