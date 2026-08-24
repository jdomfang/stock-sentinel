"""One sector scan, start to finish, with no UI. The last piece of the portal.

WHY THIS MODULE EXISTS

Deep Analyze was extracted to utils/analyze.py and then moved behind core-api,
and the portal stopped owning it. The scan did not move, so 745 lines of
pagination, ticker validation, sentiment attribution and telemetry stayed
interleaved with st.progress() and st.markdown() in pages/Discovery.py -- which
means it can only run inside a Streamlit script. Nothing else can call it: not
a scheduled job that warms the sector corpora overnight, not a second front
end, not a backfill over posts already paid for.

WHAT STAYS WITH THE CALLER

Credits and pixels. Charging needs a user and a session; the progress bar needs
a Streamlit runtime. Both belong to whoever calls this. `on_stage` exists so a
caller can drive its own progress chrome without this module importing
streamlit -- it is a plain callable, and passing nothing is fine.

THE ONE THING THAT IS NOT OBVIOUS

x_metrics MUST be recorded on every exit, including the aborted ones. The most
likely way a scan ends is the user clicking again mid-run, and those runs are
the MOST wasteful -- 100% of the posts bought, 0% used. Recording only the
scans that finished would bias the waste number downward in the flattering
direction. So Scan.record_metrics() is idempotent and the caller is expected to
call it from a finally, whatever happened.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

SAFETY_CAP_TWEETS = 300
PER_PAGE = 100
TARGET_VALIDATED = 10

# Posts per basket request. Small on purpose: X's minimum is 10, requests are
# free, and only returned posts are billed -- so fetching 25 four times costs
# exactly what fetching 100 once costs, while letting the loop stop the moment
# it has what it needs. This is the one number here worth tuning from real data.
BASKET_PER_PAGE = 25

# |mean margin| below this reads as Neutral. A PLACEHOLDER: nobody has labelled
# a sample of these posts yet, so this is a starting point to be tuned from
# ground truth, not a measured boundary. Deliberately not derived from the old
# 0.55 confidence threshold, which applied to a different quantity.
SENTIMENT_MARGIN = 0.15


@dataclass
class Scan:
    """Everything one sector scan produced. Serialisable, UI-free."""

    sector: str
    query: str = ""
    rows: list = field(default_factory=list)      # per-ticker aggregate, pre-cut
    displayed: list = field(default_factory=list)  # the top-N tickers, in order
    posts_seen: int = 0
    pages: int = 0
    corpus_key: str = ""
    corpus_age_s: float = 0.0
    from_cache: bool = False
    stop_reason: str = ""
    # X answered with an error mid-pagination. Distinct from `error`: the scan
    # kept whatever it had already bought and carried on to a partial answer.
    x_error: str | None = None
    # The scan could not run at all. `raised` separates a genuine exception
    # from a refusal the caller should render differently.
    error: str | None = None
    raised: bool = False
    # WHICH failure, so the caller can render the message it always did. The
    # page used to distinguish these with `except KeyError` and
    # `except requests.exceptions.RequestException`; both became unreachable
    # the moment scan() started swallowing Exception, and a missing API key
    # started reporting itself as "X data feed unavailable".
    #   credentials | network | ticker_db | pipeline
    error_kind: str | None = None
    # Set when the query could not be built. NO FALLBACK, deliberately: the
    # topic queries this replaced measured 4% precision in utilities against
    # 86% for baskets, so quietly running them would hand the user a scan we
    # know is bad without telling them.
    no_query: bool = False

    _record: Any = None        # bound by scan(); see record_metrics

    @property
    def ok(self) -> bool:
        return self.error is None and not self.no_query

    def record_metrics(self, displayed: list | None = None) -> None:
        """Write the effectiveness row. Idempotent, and safe in a finally.

        Call it from the caller's finally as well as on the happy path. A scan
        aborted by a Streamlit rerun bought every post and used none of them,
        and that is the data point most worth having.
        """
        if self._record is None:
            return
        try:
            self._record(displayed if displayed is not None else self.displayed)
        except Exception:
            logger.warning("scan: metrics record failed", exc_info=True)


def rows_for_display(s: Scan) -> list:
    """The top-N validated tickers, in display order, as plain dicts.

    NOT a DataFrame. The caller builds one if it wants one -- a pandas object
    cannot cross a network boundary, and making the service depend on pandas to
    return ten rows would be the wrong trade.

    ONE DELIBERATE DIFFERENCE FROM THE CODE THIS REPLACES. The page did
    `pd.DataFrame(rows).sort_values('Mentions', ascending=False)`, and pandas'
    default sort is QUICKSORT -- unstable. Tickers tied on mention count came
    back in an arbitrary order, so which one fell off the ten-row cut was not
    reproducible from the same corpus. list.sort is stable, so ties now hold
    insertion order (first mention wins). That is a change, and it is the right
    way round: the displayed set feeds scan_log and x_metrics, and a cohort
    that reshuffles on replay cannot be measured.
    """
    valid = [r for r in s.rows if r.get("Valid")]
    valid.sort(key=lambda r: r.get("Mentions", 0), reverse=True)
    return [{k: v for k, v in r.items() if k != "Valid"} for r in valid[:TARGET_VALIDATED]]


def scan(sector: str, *,
         event_id: str | None = None,
         on_stage: Callable[[str], None] | None = None) -> Scan:
    """Run one sector scan. NEVER RAISES; failures land in `.error`.

    The sequence is the one pages/Discovery.py performed inline, in the same
    order and with the same guards.

    The wrapper is what makes "never raises" true rather than aspirational.
    Several steps reach the network outside the pagination loop's own guard --
    the ticker master load, corpus_cache.get, and BasketFetcher's
    prefetch_first_pass, which goes out the moment it is constructed. A caller
    holding a charged credit needs one shape of answer, not two.
    """
    s = Scan(sector=sector)
    try:
        return _run(s, sector, event_id, on_stage)
    except Exception as e:
        logger.exception("scan: failed for %s", sector)
        s.error, s.raised = f"{type(e).__name__}: {e}", True
        s.error_kind = _classify(e)
        s.record_metrics([])
        return s
    except BaseException:
        # THE ABORT, and it is the whole reason record_metrics exists.
        # Streamlit stops a script by raising StopException/RerunException,
        # which derive from BaseException and sail straight past `except
        # Exception`. The caller's finally cannot help: it never received a
        # Scan to call, because this function never returned one.
        #
        # Those runs bought every post and used none of them -- the most
        # wasteful outcome there is, and the most common, since the usual way
        # a scan ends is the user clicking again. Losing exactly them biases
        # the waste number downward in the flattering direction.
        #
        # Recorded, then re-raised: swallowing it would break the abort.
        s.record_metrics([])
        raise


def _classify(e: BaseException) -> str:
    """Which of the page's error panels this failure belongs to."""
    try:
        import requests
        if isinstance(e, requests.exceptions.RequestException):
            return "network"
    except Exception:
        pass
    if isinstance(e, KeyError):
        return "credentials"
    return "pipeline"


def _run(s: "Scan", sector: str, event_id, on_stage) -> Scan:
    from utils import corpus_cache, sector_query, x_metrics
    from utils.deep_analysis import search_x_tweets_page
    from utils.finance import get_ticker_master_list
    from utils.sentiment import analyze_sentiment_batch, extract_tickers_detailed

    def stage(name: str) -> None:
        if on_stage is not None:
            try:
                on_stage(name)
            except Exception:
                # A caller's progress chrome must never take down a paid scan.
                logger.warning("scan: on_stage(%s) raised", name, exc_info=True)

    # BEFORE any work, as it was. The page read st.secrets["X_BEARER_TOKEN"]
    # as the first statement in its try, so a missing key cost nothing and
    # produced a "Configuration error" panel. Deleting it moved the discovery
    # to somewhere after the basket build and the cache read, and turned it
    # into "X data feed unavailable -- RuntimeError".
    from utils import config as _cfg
    if not _cfg.get("X_BEARER_TOKEN"):
        s.error, s.error_kind = "missing API credentials", "credentials"
        return s

    ticker_master_list = get_ticker_master_list()
    if not ticker_master_list:
        s.error, s.error_kind = "ticker database unavailable", "ticker_db"
        return s

    # Nasdaq sector strings (stored in Supabase) for strict matching.
    #
    # Imported rather than defined here. The query generator and this
    # validation step must agree on what "utilities" means, and two
    # hand-maintained copies of the same mapping would silently diverge --
    # the generator would ask about one set of tickers while validation
    # accepted another. tests/test_sector_query.py pins the contents.
    selected_nasdaq_sectors = sector_query.UI_TO_NASDAQ.get((sector or "").lower(), set())

    ticker_data: dict = defaultdict(lambda: {
        'mentions': 0,
        # P(positive) - P(negative) per scoring post. Continuous and signed,
        # so aggregating it cannot tie the way a vote over discrete labels
        # does, and it keeps the difference between a 0.48/0.44 coin flip
        # and a 0.90/0.05 conviction that a single label collapses.
        'margins': [],
        'sample_tweets': []
    })

    # Tweet ids already counted. Baskets overlap -- a post naming tickers
    # from two baskets is returned by both requests -- so without this a
    # single post inflates its tickers' mention counts, and mentions decide
    # the displayed top 10.
    _seen_post_ids: set = set()
    # (text, tickers, cashtags) for every ticker-bearing post, kept so sentiment
    # can run ONCE after the ranking rather than per page during the fetch.
    _scored_corpus: list = []

    validated_set: set = set()
    checked_set: set = set()
    company_by_ticker: dict = {}
    next_token = None
    total_posts = 0
    pages = 0

    def _try_validate_from_current_ranking():
        """Validate tickers in mention-rank order until we have 10 or run out."""
        ranking = sorted(ticker_data.items(),
                         key=lambda kv: kv[1].get('mentions', 0), reverse=True)
        for ticker, info in ranking:
            if len(validated_set) >= TARGET_VALIDATED:
                break
            if ticker in checked_set:
                continue
            checked_set.add(ticker)

            t_up = (ticker or '').upper()
            if t_up not in ticker_master_list:
                continue

            ticker_info = ticker_master_list[t_up]
            ticker_sector = (ticker_info.get('sector') or '').strip()
            if not selected_nasdaq_sectors:
                # If we can't map the UI sector to a Nasdaq sector string, be
                # strict and reject.
                continue
            # Strict match: only accept if the Nasdaq sector matches one of the
            # mapped sectors.
            if ticker_sector not in selected_nasdaq_sectors:
                continue

            validated_set.add(ticker)
            company_by_ticker[ticker] = ticker_info.get('name', ticker)

    # ── Build the query from this sector's own tickers ──────────────────────
    #
    # NO FALLBACK, DELIBERATELY. If the baskets cannot be built the caller
    # refunds and stops rather than quietly running the old topic query, which
    # was measured at a quarter of this precision. Falling back would hand the
    # user a scan we know is bad without telling them -- the exact silent
    # degradation this codebase keeps paying for.
    _baskets: list = []
    _basket_error: str | None = None
    try:
        _baskets = sector_query.build_baskets(sector, "cashtag")
    except Exception as _e:
        _basket_error = f"{type(_e).__name__}: {str(_e)[:160]}"
        logger.exception("basket generation failed for %s", sector)

    if not _baskets:
        s.no_query = True
        s.error = _basket_error or f"no live tickers for sector {sector!r}"
        logger.error("cannot build a query for %s: %s", sector, s.error)
        return s

    # One identity for the whole basket set, so the corpus cache key and the
    # telemetry's query_hash change whenever the generated query changes --
    # which happens nightly as dollar-volume rankings shift.
    query = "cashtag-baskets|" + "|".join(_baskets)
    s.query = query
    s.corpus_key = corpus_cache.make_key("sector", sector, 24, query)
    logger.info("🧺 %d basket(s), %d tickers, sector=%s",
                len(_baskets), sum(b.count("$") for b in _baskets), sector)

    # ── Shared corpus cache ─────────────────────────────────────────────────
    # X bills per POST RETURNED, so the cost of this scan is the number of
    # tweets it pulls. The result is not user-specific -- it is a function of
    # (sector, 24h window) -- and there are only ten sectors, so one fetch can
    # serve every user who scans that sector for the next six hours. Cost stops
    # scaling with users and becomes bounded by the catalogue.
    #
    # The RAW corpus is cached, not the finished table: the tweets are what cost
    # money, and re-scoring them is free. A sentiment fix can then be replayed
    # over posts already paid for.
    _cached = corpus_cache.get("sector", sector, 24, query)
    _cached_pages = None
    if _cached is not None:
        # Replay at the SAME page size the corpus was bought at, or the
        # early-stop gates fire at different points than they did live and a
        # cache hit can produce a different top-10 than the scan that paid for
        # it. Basket mode buys in BASKET_PER_PAGE chunks.
        _replay_size = BASKET_PER_PAGE if _baskets else PER_PAGE
        _cached_pages = corpus_cache.chunk_pages(_cached["tweets"], _replay_size)
    s.from_cache = _cached is not None
    s.corpus_age_s = float(_cached["age_s"]) if _cached else 0.0
    _fetched_pages: list = []

    # Effectiveness telemetry. Every number it reports is derived from posts
    # already paid for, so this adds no X spend and no API calls.
    _tally = x_metrics.ScanTally()
    _metrics_state = {"written": False}

    def _is_valid_ticker(sym: str) -> bool:
        """The SAME rule _try_validate_from_current_ranking applies.

        Passed into the tally so an uncapped validatable count can be computed.
        It must mirror the real predicate exactly, or the headline measurement
        diverges from what the scan actually accepts.
        """
        info = ticker_master_list.get((sym or "").upper())
        if not info or not selected_nasdaq_sectors:
            return False
        return (info.get("sector") or "").strip() in selected_nasdaq_sectors

    def _stop_reason() -> str:
        return ("validated_target" if len(validated_set) >= TARGET_VALIDATED
                else "safety_cap" if total_posts >= SAFETY_CAP_TWEETS
                else "exhausted")

    def _record_metrics(displayed: list) -> None:
        """One row per scan, written whichever way the scan ends.

        Called on the empty-result path too: a scan that bought 99 posts and
        displayed nothing is 100% waste, and that is precisely the data point
        worth having. Recording only successful scans would bias the waste
        number downward exactly where it matters most.
        """
        if _metrics_state["written"]:
            return          # one row per scan, whichever path got here first
        _metrics_state["written"] = True
        x_metrics.record_scan(
            event_id=event_id,
            subject=sector,
            query=query,
            tally=_tally,
            validated=validated_set,
            displayed=displayed,
            posts_billed=sum(len(p) for p in _fetched_pages),
            pages_fetched=len(_fetched_pages),
            from_cache=_cached is not None,
            # Uncapped validation, so the 10-cap cannot hide the answer.
            is_valid=_is_valid_ticker,
            stop_reason=_stop_reason(),
            corpus_key=corpus_cache.make_key("sector", sector, 24, query),
        )

    s._record = _record_metrics

    _fetcher = None

    def _next_page(token):
        """Serve page N from the cached corpus, or buy it from X.

        Replaying the corpus page by page rather than handing the loop one flat
        list keeps the early-stop, the safety cap and the per-page batching
        completely untouched -- the same tweets produce the same tickers, so a
        replayed scan stops exactly where the original did.
        """
        idx = pages - 1  # `pages` is incremented before this is called
        if _cached_pages is not None:
            if idx < len(_cached_pages):
                return {
                    "success": True,
                    "tweets": _cached_pages[idx],
                    # Synthetic: on a hit X is never called, so this token only
                    # has to be truthy enough to drive the loop.
                    "next_token": "cached" if idx + 1 < len(_cached_pages) else None,
                }
            return {"success": True, "tweets": [], "next_token": None}

        if _fetcher is not None:
            res = _fetcher.next_page()
            # The outer loop only asks "is there more work?", so hand it a
            # sentinel rather than a token belonging to one specific basket.
            return dict(res, next_token="more" if res.get("has_more") else None)

        res = search_x_tweets_page(
            query=query, max_results=PER_PAGE, timeframe="24h", next_token=token)
        if res.get("success"):
            # Record what we bought so it can be stored once the scan ends.
            _fetched_pages.append(res.get("tweets") or [])
        return res

    # NOT on a corpus-cache hit. _next_page returns cached pages and never
    # consults the fetcher, but prefetch_first_pass() goes to the network the
    # moment it is constructed -- so a replayed scan was buying an entire sector
    # (up to 700 posts in finance) and throwing it away, with posts_billed still
    # reporting 0 because nothing was delivered. The whole point of the corpus
    # cache is that a repeat scan costs nothing.
    if _baskets and _cached_pages is None:
        _fetcher = sector_query.BasketFetcher(
            _baskets,
            fetch=lambda q, n, tok: search_x_tweets_page(
                query=q, max_results=n, timeframe="24h", next_token=tok),
            per_page=BASKET_PER_PAGE,
            # Enough for one full pass over every basket plus some depth. The
            # default of 20 could not cover finance (27 baskets), consumer (22)
            # or healthcare (20) even once, so their tail tickers were
            # unreachable regardless of budget.
            max_requests=len(_baskets) + 6,
        )
        # Baskets are independent queries, so a wide sector's first pass goes
        # out concurrently. Finance has 27 baskets; serialising them is ~30s of
        # a paid scan spent inside the window where a user re-click aborts the
        # run. No-op at 3 baskets or fewer.
        _fetcher.prefetch_first_pass(post_budget=SAFETY_CAP_TWEETS)
        # The fetcher owns the pages it bought; _fetched_pages is what the
        # corpus write and posts_billed read, so point them at the same list.
        _fetched_pages = _fetcher.pages

    try:
        while total_posts < SAFETY_CAP_TWEETS:
            pages += 1
            stage("fetching")
            res = _next_page(next_token)
            if not res.get('success'):
                s.x_error = res.get('error') or 'X API request failed'
                break

            page_tweets = res.get('tweets') or []
            next_token = res.get('next_token')
            stage("filtering")

            if not page_tweets:
                # In BASKET mode an empty page is normal, not the end.
                # sector_universe sorts by dollar volume, so baskets 2..N hold
                # only quiet names -- returning zero is the expected case for
                # them. Breaking here would abandon every basket after the first
                # quiet one, which silently defeats the whole point of covering
                # the sector. Keep going while the fetcher still has work.
                #
                # An empty page does not increment total_posts, so `continue`
                # relies entirely on BasketFetcher's own max_requests bound to
                # terminate -- the `while total_posts < SAFETY_CAP_TWEETS` guard
                # cannot stop this path on its own.
                if next_token:
                    continue
                break

            # DROP POSTS ALREADY SEEN IN ANOTHER BASKET.
            #
            # A single topic query paginating with next_token could never return
            # the same post twice. Baskets can: a post naming $NVDA (basket 1)
            # and a quieter name (basket 3) matches both queries and comes back
            # from both requests. Without this, that post increments
            # ticker_data[t]['mentions'] twice and contributes its sentiment
            # twice -- and mentions is the sort key that decides which ten
            # tickers the user sees.
            #
            # The duplicate is still BILLED; X charged for both copies. This
            # only stops it being counted twice.
            _fresh = []
            for tw in page_tweets:
                tid = tw.get("id")
                if tid is not None and tid in _seen_post_ids:
                    continue
                if tid is not None:
                    _seen_post_ids.add(tid)
                _fresh.append(tw)
            if len(_fresh) != len(page_tweets):
                logger.info("deduped %d post(s) already seen in another basket",
                            len(page_tweets) - len(_fresh))
            page_tweets = _fresh
            if not page_tweets:
                if next_token:
                    continue
                break

            remaining = SAFETY_CAP_TWEETS - total_posts
            if remaining <= 0:
                break
            if len(page_tweets) > remaining:
                page_tweets = page_tweets[:remaining]

            total_posts += len(page_tweets)

            # EXTRACTION ONLY. No sentiment here.
            #
            # Ranking is by mention count and validation is a ticker_master
            # lookup -- neither needs FinBERT. Scoring inside this loop meant
            # scoring every ticker-bearing post to display ten tickers: measured
            # at 119 of 138 posts in utilities, 86 of 98 in tech, about 17 and
            # 12 seconds of inference respectively. Deferring it until the top
            # ten is known cuts that by 42-45% and removes it from the fetch
            # path entirely.
            for tweet in page_tweets:
                text = tweet.get('text', '')
                # _detailed reports WHERE each symbol came from -- $CAT is
                # unambiguous, bare CAT is also an English word. `legacy` is the
                # exact list extract_tickers has always returned.
                _d = extract_tickers_detailed(text)
                _tally.record(_d["cashtag"], _d["bare"],
                              _d["cashtag_counts"], _d["bare_counts"])
                tickers = _d["legacy"]
                if not tickers:
                    continue
                _scored_corpus.append((text, tickers, _d["cashtag"]))
                for ticker in tickers:
                    ticker_data[ticker]['mentions'] += 1
                    if len(ticker_data[ticker]['sample_tweets']) < 3:
                        short_text = text[:150] + "..." if len(text) > 150 else text
                        ticker_data[ticker]['sample_tweets'].append(short_text)

            # After each page, try to validate enough tickers.
            stage("shortlist")
            _try_validate_from_current_ranking()

            logger.info(
                "📄 Discovery pagination pages=%s posts=%s validated=%s first_pass=%s",
                pages, total_posts, len(validated_set),
                _fetcher.first_pass_done if _fetcher else True)

            # STOP ONLY AFTER EVERY BASKET HAS BEEN SAMPLED.
            #
            # Baskets are ordered by dollar volume, and dollar volume does not
            # predict chatter. Measured on utilities: four of the six
            # most-discussed tickers sat outside basket 1 -- $AVA (14 mentions,
            # basket 2) and $AWX (12, basket 4) would never have been seen by a
            # scan that stopped once basket 1 filled ten slots. Stopping on the
            # ticker target alone returns exactly the names that are NOT unusual.
            _first_pass = _fetcher.first_pass_done if _fetcher else True
            if _first_pass and len(validated_set) >= TARGET_VALIDATED:
                break

            if not next_token:
                break
    except Exception as e:
        logger.exception("scan: pagination failed for %s", sector)
        s.error = f"{type(e).__name__}: {e}"
        s.raised = True
        s.error_kind = _classify(e)
        s.posts_seen, s.pages = total_posts, pages
        s.stop_reason = _stop_reason()
        s.record_metrics([])
        return s

    s.posts_seen, s.pages = total_posts, pages
    s.stop_reason = _stop_reason()
    logger.info("🎯 Posts processed (capped): %s", total_posts)

    # Store what we bought, so the next scan of this sector is free.
    #
    # Only on a clean run: a corpus truncated by an X failure would be frozen in
    # and served to everyone for the next six hours, turning one transient error
    # into a sustained bad result. A genuinely EMPTY result is stored, though --
    # "this sector had no chatter" is a real answer that cost real money to
    # learn, and without a negative entry the next user pays to learn it again.
    if _cached is None and not s.x_error:
        try:
            corpus_cache.put("sector", sector, 24, query,
                             tweets=[t for pg in _fetched_pages for t in pg],
                             pages_fetched=len(_fetched_pages),
                             stop_reason=s.stop_reason)
        except Exception:
            logger.warning("scan: corpus_cache.put failed", exc_info=True)

    if total_posts == 0:
        # An answer, not a crash. The caller decides whether to refund: an
        # upstream failure with zero posts is not the same as a genuinely quiet
        # sector, and only x_error tells them apart.
        s.record_metrics([])
        return s

    # ── Sentiment, once, on the shortlist only ──────────────────────────────
    #
    # Ranking never used sentiment: it is mention count, and validation is a
    # table lookup. So the ten tickers are already decided here, and only their
    # posts need scoring. Measured: 119 of 138 posts scored before, 69 after
    # (utilities); 86 of 98 before, 47 after (tech) -- 42-45% less inference for
    # an identical result set.
    _shortlist = [t for t, _ in sorted(
        ((t, d['mentions']) for t, d in ticker_data.items() if t in validated_set),
        key=lambda kv: -kv[1])[:TARGET_VALIDATED]]
    _shortlist_set = set(_shortlist)

    _relevant = [(text, tks, cash) for text, tks, cash in _scored_corpus
                 if any(t in _shortlist_set for t in tks)]
    if _relevant:
        stage("scoring")
    logger.info("🧠 scoring %d of %d ticker-bearing posts (shortlist of %d)",
                len(_relevant), len(_scored_corpus), len(_shortlist))

    try:
        _sent = analyze_sentiment_batch([t for t, _, _ in _relevant]) if _relevant else []
    except Exception as e:
        logger.exception("scan: sentiment failed for %s", sector)
        s.error = f"{type(e).__name__}: {e}"
        s.raised = True
        s.error_kind = _classify(e)
        s.record_metrics([])
        return s

    # SINGLE-TICKER POSTS ONLY DRIVE DIRECTION.
    #
    # FinBERT scores the whole post and is never told which ticker the question
    # is about, so a post naming two tickers gives both the same verdict --
    # "Virginia Gov's skepticism threatens $NEE's $D" is one sentiment stamped
    # on two companies whose fortunes the post treats as opposed. Measured at
    # 13% of ticker-bearing posts. They still count as ATTENTION, which is what
    # the scan is actually for; they just do not get a vote on direction.
    for (text, tks, cash), res in zip(_relevant, _sent):
        # Attribute only when the post has exactly ONE unambiguous subject.
        #
        # Gating on the full `legacy` list was far too aggressive: it carries up
        # to 5 BARE uppercase words on top of the cashtags, so "one shortlist
        # cashtag + any capitalised token" counted as multi-ticker. Measured on
        # the cached corpora that excluded 63% of utilities and 47% of tech
        # posts -- 4x the 13% this rule is meant to catch -- and left 7 of 10
        # rows unscored.
        #
        # A cashtag is an explicit claim about a security; a bare word is a
        # guess. Exactly one distinct cashtag means exactly one subject.
        uniq_cash = {c for c in cash}
        if len(uniq_cash) != 1:
            continue
        ticker = next(iter(uniq_cash))
        if ticker in _shortlist_set:
            ticker_data[ticker]['margins'].append(float(res.get('margin') or 0.0))

    rows: list = []
    for ticker, info in ticker_data.items():
        margins = info.get('margins') or []
        n = len(margins)
        mean_margin = (sum(margins) / n) if n else 0.0

        # EVIDENCE FLOOR. The old pill was a plurality vote over discrete
        # labels, which (a) tied on corpus order -- $ET came back
        # {Neutral: 2, Bearish: 2} and the winner was whichever post arrived
        # first -- and (b) rendered the same bold badge for one post as for
        # fourteen, which was 52% of tickers. A mean margin cannot tie, and the
        # floor stops a single post being presented as a verdict.
        if n == 0:
            overall_sentiment = 'Unscored'
        elif n == 1:
            overall_sentiment = 'Single mention'
        elif n == 2:
            overall_sentiment = 'Limited signal'
        elif mean_margin >= SENTIMENT_MARGIN:
            overall_sentiment = 'Bullish'
        elif mean_margin <= -SENTIMENT_MARGIN:
            overall_sentiment = 'Bearish'
        else:
            overall_sentiment = 'Neutral'

        rows.append({
            'Ticker': ticker,
            'Mentions': info['mentions'],
            'Avg Sentiment Score': round(mean_margin, 3),
            'Evidence': n,
            'Overall Sentiment': overall_sentiment,
            'Sample Tweets': ' | '.join(info['sample_tweets']),
            'Company Name': company_by_ticker.get(ticker, 'N/A'),
            'Valid': ticker in validated_set,
        })

    s.rows = rows
    s.displayed = [str(r["Ticker"]) for r in rows_for_display(s)]

    # Classification needs the FINAL top-10: whether a post "contributed"
    # depends on which tickers actually got displayed, which is not known
    # until here.
    s.record_metrics(s.displayed)
    return s


def persist(s: Scan, *, event_id: str | None = None,
            model: str | None = None) -> None:
    """Write the per-ticker observations. Never raises into a caller.

    Every VALID ticker, not just the displayed ten. These are the observations
    that will eventually answer whether this sentiment measurement predicts
    anything -- Deep Analyze has run a handful of times in its history and
    cannot supply them, while one scan yields ~10 for posts already bought.
    """
    if not s.rows:
        return
    if model is None:
        try:
            from utils.sentiment import MODEL_NAME
            model = MODEL_NAME
        except Exception:
            model = "unknown"
    try:
        from utils import scan_log
        scan_log.record(s.sector, s.rows, s.displayed,
                        event_id=event_id, corpus_key=s.corpus_key, model=model)
    except Exception:
        logger.warning("scan: scan_log call failed", exc_info=True)
