#!/usr/bin/env python3
"""The sector scan, replayed over corpora that already cost money.

WHY THIS EXISTS

745 lines of pagination, deduplication, ticker validation, sentiment
attribution and telemetry moved out of pages/Discovery.py into utils/scan.py.
None of it had a test, because none of it could run outside a Streamlit script.

The fixtures in tests/golden/scan/corpora are REAL sector corpora pulled from
x_corpus_cache -- 621 posts across four scans that were actually bought,
covering both retrieval modes (basket and topic) and the validated_target stop
reason. Replaying them costs nothing: X bills per post returned, and these were
returned months ago.

WHAT THIS PINS

The orchestration, which is what the extraction could have broken: the order of
the steps, the early-stop, the safety cap, the cross-basket dedup, the
attribution rule, and the evidence floor. Sentiment is stubbed deterministically
-- utils/sentiment has its own tests, and a remote model would make this
non-reproducible.

Usage:
    python3 tests/test_scan.py            # compare against the recorded golden
    python3 tests/test_scan.py --record   # re-record it, deliberately
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import utils.scan as S  # noqa: E402

# UNDER tests/golden/scan/, not directly in tests/golden/. The Deep Analyze
# oracle enumerates its own recordings from that directory and reported a
# missing corpus the moment a file it did not write appeared beside them --
# "3 of 4 corpora unchanged", which reads exactly like a real regression.
CORPORA = REPO / "tests" / "golden" / "scan" / "corpora"
GOLDEN = REPO / "tests" / "golden" / "scan" / "expected.json"

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name, cond, detail=""):
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


# --------------------------------------------------------------- the harness

def _margin_for(text: str) -> float:
    """Deterministic pseudo-sentiment in [-1, 1], stable across runs and hosts.

    NOT a model. The point is that the same post always yields the same margin,
    so a change in the ORCHESTRATION shows up and a change in FinBERT does not.
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    return round((int.from_bytes(h[:4], "big") / 0xFFFFFFFF) * 2 - 1, 6)


def install(corpus: dict, *, sectors=None, master=None, fail_page=None,
            repeat_pages=False, cached=False, spy=None):
    """Point utils.scan at a recorded corpus instead of X."""
    import utils.config as CFG
    import utils.corpus_cache as CC
    import utils.deep_analysis as DA
    import utils.finance as FIN
    import utils.sector_query as SQ
    import utils.sentiment as SENT
    import utils.x_metrics as XM

    # Replays must never depend on a developer's secrets.toml. scan() checks
    # this credential before consulting the installed corpus/fetcher stubs, so
    # a clean CI worker otherwise returns "missing API credentials" without
    # exercising any of the orchestration this suite exists to pin.
    CFG.get = lambda name, default="": (
        "test-token" if name == "X_BEARER_TOKEN" else default
    )

    tweets = corpus["tweets"]
    # Every cashtag in the corpus becomes a real ticker in the master, in the
    # sector under test, so validation has something to accept. Uppercase
    # bare-word noise is deliberately NOT added -- rejecting it is the
    # behaviour worth pinning.
    cashtags = set()
    for t in tweets:
        for tok in (t.get("text") or "").split():
            if tok.startswith("$") and tok[1:].isalpha() and 1 <= len(tok) - 1 <= 5:
                cashtags.add(tok[1:].upper())
    SECTOR = "Health Care"
    FIN.get_ticker_master_list = lambda: (master if master is not None else
                                          {c: {"sector": SECTOR, "name": f"{c} Inc"}
                                           for c in sorted(cashtags)})
    SQ.UI_TO_NASDAQ = {corpus["subject"].lower(): (sectors if sectors is not None
                                                   else {SECTOR})}
    SQ.build_baskets = lambda sector, mode: ["$AAA OR $BBB"]

    class _Fetcher:
        """Serves the recorded corpus one page at a time, like BasketFetcher."""
        def __init__(self, *a, **k):
            if spy is not None:
                spy.append("construct")
            self.pages = []
            self._chunks = [tweets[i:i + S.BASKET_PER_PAGE]
                            for i in range(0, len(tweets), S.BASKET_PER_PAGE)]
            if repeat_pages:
                # What overlapping baskets actually do: the SAME posts come
                # back from two different queries. Doubling the corpus does
                # not reproduce this -- the scan early-stops long before it
                # reaches the copy -- which made the dedup test vacuous.
                self._chunks = [c for c in self._chunks for _ in (0, 1)]
            self._i = 0
            self.first_pass_done = True
        def prefetch_first_pass(self, post_budget=None):
            # THE BUG THIS RECORDS: prefetch goes to the network the moment
            # the fetcher is built, so constructing one on a cache hit bought
            # an entire sector and threw it away.
            if spy is not None:
                spy.append("prefetch")
            return None
        def next_page(self):
            if fail_page is not None and self._i == fail_page:
                return {"success": False, "error": "X API request failed"}
            if self._i >= len(self._chunks):
                return {"success": True, "tweets": [], "has_more": False}
            pg = self._chunks[self._i]; self._i += 1
            self.pages.append(pg)
            return {"success": True, "tweets": pg, "has_more": self._i < len(self._chunks)}
    SQ.BasketFetcher = _Fetcher

    if cached:
        CC.get = lambda *a, **k: {"tweets": tweets, "age_s": 1234.0}
    else:
        CC.get = lambda *a, **k: None
    _puts = spy if spy is not None else []
    def _put(*a, **k):
        _puts.append(("put", k.get("stop_reason"), len(k.get("tweets") or [])))
    CC.put = _put
    CC.make_key = lambda *a, **k: "test-key"
    CC.chunk_pages = lambda tw, n: [tw[i:i + n] for i in range(0, len(tw), n)]
    DA.search_x_tweets_page = lambda **k: {"success": True, "tweets": [], "next_token": None}
    SENT.analyze_sentiment_batch = lambda texts: [{"margin": _margin_for(t)} for t in texts]
    XM.record_scan = lambda **k: None


def summarise(s: S.Scan) -> dict:
    """The facts a regression would move. Not the whole object -- sample
    tweets are long and prove nothing beyond what the counts already do."""
    return {
        "posts_seen": s.posts_seen,
        "pages": s.pages,
        "stop_reason": s.stop_reason,
        "displayed": s.displayed,
        "n_rows": len(s.rows),
        "valid_rows": sum(1 for r in s.rows if r.get("Valid")),
        "top": [{k: r[k] for k in ("Ticker", "Mentions", "Evidence",
                                   "Avg Sentiment Score", "Overall Sentiment")}
                for r in S.rows_for_display(s)],
    }


def run_all() -> dict:
    out = {}
    for f in sorted(CORPORA.glob("*.json")):
        corpus = json.loads(f.read_text())
        install(corpus)
        out[f.stem] = summarise(S.scan(corpus["subject"]))
    return out


# ----------------------------------------------------------------- the tests

def test_replay_matches_the_recording():
    print("\nreplaying 4 real corpora: nothing about the scan may change")
    actual = run_all()
    if "--record" in sys.argv:
        GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True))
        print(f"  RECORDED {GOLDEN}")
        return
    if not GOLDEN.exists():
        check("golden exists", False, "run with --record first")
        return
    expected = json.loads(GOLDEN.read_text())
    for name in sorted(set(expected) | set(actual)):
        same = expected.get(name) == actual.get(name)
        detail = ""
        if not same:
            e, a = expected.get(name, {}), actual.get(name, {})
            diff = [k for k in set(e) | set(a) if e.get(k) != a.get(k)]
            detail = f"fields differ: {diff}"
        check(f"{name} unchanged", same, detail)


def test_the_safety_cap_holds():
    print("\nthe cap is what stops a runaway scan buying the whole sector")
    corpus = json.loads((CORPORA / "healthcare_validated_target.json").read_text())
    install(corpus, sectors=set())      # nothing validates -> never early-stops
    s = S.scan(corpus["subject"])
    check("no ticker validated", s.displayed == [], str(s.displayed))
    # EQUALITY, not "<=". This corpus holds 349 posts and nothing validates,
    # so the cap is the only thing that can stop it -- and `<= cap` would pass
    # just as happily if the cap were shrunk to 50 or the loop exited early.
    # A LITERAL 300, not the constant. Reading S.SAFETY_CAP_TWEETS here makes
    # the assertion agree with any value the constant happens to hold, so
    # halving the cap -- which halves what every uncached scan can buy, and
    # what it can find -- would pass silently. The number is a cost ceiling
    # and part of the contract; moving it should be a deliberate edit here.
    check("the scan stops exactly at the 300-post cap",
          s.posts_seen == 300 == S.SAFETY_CAP_TWEETS,
          f"{s.posts_seen} posts, cap={S.SAFETY_CAP_TWEETS}")
    check("...and says so", s.stop_reason == "safety_cap", s.stop_reason)


def test_duplicate_posts_are_counted_once():
    """A post returned by two baskets must not vote twice.

    Mentions is the sort key that decides which ten tickers the user sees, so
    double-counting one post moves the shortlist. The duplicate is still
    BILLED -- X charged for both copies -- this only stops it being counted.
    """
    print("\na post returned by two baskets must not vote twice")
    corpus = json.loads((CORPORA / "materials_validated_target.json").read_text())
    # sectors=set() so NOTHING validates: the early stop would otherwise end
    # the scan on page one or two, before it ever sees a repeated page, and
    # the comparison would hold for the wrong reason.
    install(corpus, sectors=set()); once = S.scan(corpus["subject"])
    install(corpus, sectors=set(), repeat_pages=True); twice = S.scan(corpus["subject"])
    check("the repeat actually served the same posts again",
          twice.pages > once.pages, f"pages {once.pages} -> {twice.pages}")

    m1 = {r["Ticker"]: r["Mentions"] for r in once.rows}
    m2 = {r["Ticker"]: r["Mentions"] for r in twice.rows}
    check("every page served twice yields the same mention counts", m1 == m2,
          f"{sum(m1.values())} vs {sum(m2.values())} mentions")
    check("...and the same evidence counts",
          {r["Ticker"]: r["Evidence"] for r in once.rows}
          == {r["Ticker"]: r["Evidence"] for r in twice.rows})
    check("...and the same displayed shortlist", once.displayed == twice.displayed,
          f"{once.displayed} vs {twice.displayed}")


def test_only_single_cashtag_posts_drive_direction():
    """FinBERT scores the whole post and is never told which ticker it is about.

    "Virginia Gov's skepticism threatens $NEE's $D" is one sentiment stamped on
    two companies whose fortunes the post treats as opposed. Such posts still
    count as ATTENTION -- which is what the scan is for -- but get no vote.

    Asserted on a corpus built for it: the real fixtures happen to early-stop
    before enough multi-cashtag posts accumulate to move any published number,
    so checking them proves nothing.
    """
    print("\nFinBERT is never told which ticker the post is about")
    posts = ([{"id": f"s{i}", "text": f"$AAA is looking strong today {i}"} for i in range(6)]
             + [{"id": f"m{i}", "text": f"$AAA versus $BBB, one wins {i}"} for i in range(6)])
    corpus = {"subject": "materials", "tweets": posts}
    install(corpus)
    s_ = S.scan("materials")
    # .get, not [] -- a mutation that changes which tickers survive must be
    # REPORTED as a failed assertion, not raised as a KeyError that takes the
    # rest of the suite with it and hides which check actually caught it.
    by = {r["Ticker"]: r for r in s_.rows}
    aaa, bbb = by.get("AAA") or {}, by.get("BBB") or {}

    check("the single-subject posts voted", aaa.get("Evidence") == 6, str(aaa))
    check("the two-subject posts did NOT vote for either",
          bbb.get("Evidence") == 0, str(bbb))
    check("...but they still counted as attention",
          bbb.get("Mentions") == 6 and aaa.get("Mentions") == 12,
          f"AAA={aaa.get('Mentions')} BBB={bbb.get('Mentions')}")
    check("an unvoted ticker reads Unscored, not Neutral",
          bbb.get("Overall Sentiment") == "Unscored",
          str(bbb.get("Overall Sentiment")))


def test_the_evidence_floor():
    print("\none post is not a verdict")
    corpus = json.loads((CORPORA / "healthcare_validated_target.json").read_text())
    install(corpus)
    s = S.scan(corpus["subject"])
    labels = {0: "Unscored", 1: "Single mention", 2: "Limited signal"}
    for r in s.rows:
        want = labels.get(r["Evidence"])
        if want:
            check(f"{r['Ticker']}: {r['Evidence']} post(s) -> {want}",
                  r["Overall Sentiment"] == want, r["Overall Sentiment"])
        else:
            check(f"{r['Ticker']}: {r['Evidence']} posts -> a real call",
                  r["Overall Sentiment"] in ("Bullish", "Bearish", "Neutral"),
                  r["Overall Sentiment"])


def test_an_x_failure_keeps_what_was_already_bought():
    print("\na feed failure mid-pagination is partial, not fatal")
    corpus = json.loads((CORPORA / "tech_h2h_topic.json").read_text())
    # Page 1, not page 2: this corpus early-stops at validated_target after
    # two pages, so a failure injected any later never happens and the test
    # would assert nothing while passing.
    install(corpus, fail_page=1)
    s = S.scan(corpus["subject"])
    check("the failure is reported", bool(s.x_error), str(s.x_error))
    check("but it is not a scan error", s.error is None, str(s.error))
    check("and the posts already bought are kept", s.posts_seen > 0, str(s.posts_seen))
    check("...and still produce rows", bool(s.rows), str(len(s.rows)))


def test_metrics_are_recorded_on_every_exit():
    print("\nthe most wasteful scans are the aborted ones -- record them")
    import utils.x_metrics as XM
    corpus = json.loads((CORPORA / "materials_validated_target.json").read_text())
    install(corpus)
    calls = []
    XM.record_scan = lambda **k: calls.append(k)
    s = S.scan(corpus["subject"])
    check("recorded once on the happy path", len(calls) == 1, str(len(calls)))
    s.record_metrics([])          # the caller's finally
    check("...and the backstop cannot double-write", len(calls) == 1, str(len(calls)))
    # Guarded: when an unrelated mutation stops the scan recording at all,
    # this must report that as a failure rather than raise IndexError and take
    # the remaining tests with it.
    first = calls[0] if calls else {}
    check("it reports the stop reason",
          first.get("stop_reason") in ("validated_target", "safety_cap", "exhausted"),
          str(first.get("stop_reason")))
    check("...and what was billed", first.get("posts_billed") is not None)

    # A scan that never reached the pipeline still has to answer the call.
    empty = S.Scan(sector="x")
    check("an un-run scan's backstop is a safe no-op",
          empty.record_metrics([]) is None)


def test_scan_never_raises():
    print("\nthe caller is holding a charged credit; one shape of answer")
    import utils.finance as FIN
    corpus = json.loads((CORPORA / "materials_validated_target.json").read_text())
    install(corpus)
    FIN.get_ticker_master_list = lambda: (_ for _ in ()).throw(RuntimeError("db down"))
    s = S.scan("materials")
    check("returns instead of raising", isinstance(s, S.Scan))
    check("the failure is named", "db down" in (s.error or ""), str(s.error))
    check("and flagged as an exception", s.raised is True)

    install(corpus)
    import utils.sector_query as SQ
    SQ.build_baskets = lambda *a, **k: []
    s2 = S.scan("materials")
    check("no query is its own state, not a crash",
          s2.no_query is True and s2.raised is False, f"{s2.no_query}/{s2.raised}")


def test_ties_are_broken_reproducibly():
    print("\nthe displayed set must not reshuffle between identical replays")
    corpus = json.loads((CORPORA / "healthcare_validated_target.json").read_text())
    install(corpus); a = S.scan(corpus["subject"])
    install(corpus); b = S.scan(corpus["subject"])
    check("two replays of one corpus agree", a.displayed == b.displayed,
          f"{a.displayed} vs {b.displayed}")
    # pandas' default sort is quicksort, which is UNSTABLE -- at 200 tied rows
    # it returns a completely different order. rows_for_display sorts in Python.
    mentions = [r["Mentions"] for r in S.rows_for_display(a)]
    check("...and the shortlist is ordered by mentions, descending",
          mentions == sorted(mentions, reverse=True), str(mentions))


def test_a_cache_hit_buys_nothing():
    """The corpus cache is the reason a repeat scan is free. Prove it.

    prefetch_first_pass() goes to the network the moment BasketFetcher is
    CONSTRUCTED, so building one on a cache hit bought an entire sector -- up
    to 700 posts in finance -- and threw every one away, with posts_billed
    still reporting 0 because nothing was delivered. The guard against that
    was completely unexercised: deleting it passed every test.
    """
    print("\na cached corpus must cost nothing")
    corpus = json.loads((CORPORA / "materials_validated_target.json").read_text())
    spy: list = []
    install(corpus, cached=True, spy=spy)
    s_ = S.scan(corpus["subject"])

    check("the fetcher is never even constructed",
          "construct" not in spy and "prefetch" not in spy, str(spy))
    check("nothing is billed", not any(e[0] == "put" for e in spy if isinstance(e, tuple)),
          "a cache hit must not re-store the corpus")
    check("the scan reports it came from cache", s_.from_cache is True)
    check("...and how stale it is", s_.corpus_age_s == 1234.0, str(s_.corpus_age_s))
    check("...and still produces a shortlist", bool(s_.displayed), str(s_.displayed))

    # A replayed scan must land where the paid one landed, or a cache hit
    # silently returns a different top ten than the scan that bought it.
    install(corpus, spy=[]); live = S.scan(corpus["subject"])
    check("the replay agrees with the live scan", s_.displayed == live.displayed,
          f"cached={s_.displayed} live={live.displayed}")


def test_the_corpus_is_stored_only_on_a_clean_run():
    """A truncated corpus frozen in for six hours turns one transient X error
    into a sustained bad result for everyone who scans that sector."""
    print("\nwhat gets written back to the cache")
    corpus = json.loads((CORPORA / "tech_h2h_topic.json").read_text())

    spy: list = []
    install(corpus, spy=spy)
    S.scan(corpus["subject"])
    puts = [e for e in spy if isinstance(e, tuple) and e[0] == "put"]
    check("a clean run stores its corpus", len(puts) == 1, str(puts))
    check("...with the stop reason it actually ended on",
          puts and puts[0][1] in ("validated_target", "safety_cap", "exhausted"),
          str(puts))

    spy2: list = []
    install(corpus, fail_page=1, spy=spy2)
    s2 = S.scan(corpus["subject"])
    check("an X failure means the truncated corpus is NOT stored",
          bool(s2.x_error) and not [e for e in spy2
                                    if isinstance(e, tuple) and e[0] == "put"],
          str(spy2))

    # "This sector had no chatter" is a real answer that cost real money.
    spy3: list = []
    # An explicit master: the synthetic one is derived from the corpus, so an
    # empty corpus produces an empty master and trips the ticker_db guard long
    # before the cache write this is trying to observe.
    install({"subject": "materials", "tweets": []}, spy=spy3,
            master={"AAA": {"sector": "Health Care", "name": "AAA Inc"}})
    S.scan("materials")
    check("a genuinely empty result IS stored, so nobody pays to relearn it",
          any(isinstance(e, tuple) and e[0] == "put" and e[2] == 0 for e in spy3),
          str(spy3))


def test_an_abort_still_records_what_it_bought():
    """Streamlit stops a script with BaseException, which sails past
    `except Exception`. Those runs bought every post and used none -- the most
    wasteful outcome, and the most common, since the usual way a scan ends is
    the user clicking again. Losing exactly them biases the waste number in
    the flattering direction."""
    print("\nthe aborted scan is the one that must not go unrecorded")
    import utils.x_metrics as XM
    corpus = json.loads((CORPORA / "healthcare_validated_target.json").read_text())

    class StopLike(BaseException):
        pass

    calls: list = []
    install(corpus)
    XM.record_scan = lambda **k: calls.append(k)

    fired = {"n": 0}
    def _abort(stage_name):
        fired["n"] += 1
        if fired["n"] > 3:            # a few pages in, mid-flight
            raise StopLike("rerun")

    try:
        S.scan(corpus["subject"], on_stage=_abort)
        check("the abort propagates", False, "scan() swallowed a BaseException")
    except StopLike:
        check("the abort propagates", True)
    check("...and the buy was recorded anyway", len(calls) == 1, str(len(calls)))
    if calls:
        check("...with what it had actually bought",
              (calls[0].get("posts_billed") or 0) > 0,
              str(calls[0].get("posts_billed")))


def test_each_failure_keeps_its_own_identity():
    """The page renders a different panel and writes a different ledger reason
    for a missing key, a network failure and a dead ticker database. They were
    `except` clauses; they are error_kind now, and the distinction has to
    survive the move or a config error reports itself as an X outage."""
    print("\nfailures must not all collapse into 'something went wrong'")
    import utils.config as CFG
    import utils.finance as FIN
    import utils.sector_query as SQ
    corpus = json.loads((CORPORA / "materials_validated_target.json").read_text())

    install(corpus)
    _real_get = CFG.get
    CFG.get = lambda n, d=None: None if n == "X_BEARER_TOKEN" else _real_get(n, d)
    s1 = S.scan("materials")
    check("a missing bearer token is a credentials error",
          s1.error_kind == "credentials", str(s1.error_kind))
    CFG.get = _real_get

    install(corpus)
    FIN.get_ticker_master_list = lambda: {}
    s2 = S.scan("materials")
    check("an empty ticker master is a ticker_db error",
          s2.error_kind == "ticker_db", str(s2.error_kind))

    install(corpus)
    import requests
    SQ.build_baskets = lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("no route"))
    s3 = S.scan("materials")
    # build_baskets has its own guard, so this surfaces as no_query -- the
    # amber basket panel, which is the right one for "could not build a query".
    check("a build failure is still the no-query state",
          s3.no_query is True, f"{s3.no_query}/{s3.error_kind}")

    # Through the FETCHER, which is the only live retrieval path. _next_page's
    # direct search_x_tweets_page branch is unreachable: it needs _fetcher to
    # be None while _baskets is truthy and nothing is cached, and the code
    # builds a fetcher in exactly that case. Inherited dead code, left alone
    # here because this change is an extraction.
    install(corpus)
    SQ.build_baskets = lambda *a, **k: ["$AAA"]
    class _Boom:
        pages: list = []
        first_pass_done = True
        def __init__(self, *a, **k): pass
        def prefetch_first_pass(self, post_budget=None): pass
        def next_page(self):
            raise requests.exceptions.ConnectionError("no route")
    SQ.BasketFetcher = _Boom
    s4 = S.scan("materials")
    check("a network failure mid-fetch is a network error",
          s4.error_kind == "network", f"{s4.error_kind}: {s4.error}")
    check("...and it is reported as an exception, not a quiet empty scan",
          s4.raised is True and s4.posts_seen == 0, f"{s4.raised}/{s4.posts_seen}")


def main() -> int:
    print("=" * 74)
    print("  utils.scan: the sector scan, replayed over corpora already paid for")
    print("=" * 74)
    for t in (test_replay_matches_the_recording,
              test_a_cache_hit_buys_nothing,
              test_the_corpus_is_stored_only_on_a_clean_run,
              test_an_abort_still_records_what_it_bought,
              test_each_failure_keeps_its_own_identity,
              test_the_safety_cap_holds,
              test_duplicate_posts_are_counted_once,
              test_only_single_cashtag_posts_drive_direction,
              test_the_evidence_floor,
              test_an_x_failure_keeps_what_was_already_bought,
              test_metrics_are_recorded_on_every_exit,
              test_scan_never_raises,
              test_ties_are_broken_reproducibly):
        t()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
