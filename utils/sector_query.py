"""Generate X search queries for a sector from data, not hand-written jargon.

WHY

The scan's query is ten hand-written blocks of sector vocabulary:

    ("industrials sector" OR industrial OR manufacturing OR factory OR
     aerospace OR machinery OR logistics OR freight OR "supply chain" OR
     "backlog" OR PMI OR ISM OR XLI ...) (stock OR stocks OR shares)

Measured against three real production scans, it does not work. Only 6.6% of
the posts it buys reach a displayed ticker, and the failure is specific: the
UTILITIES query returned 24 Technology cashtags against 11 Utilities ones. Words
like "orders", "shipping", "power" and "freight" appear in ANY market
commentary, so the query retrieves general chatter that happens to contain a
topic word rather than chatter about the sector.

The fix is to stop describing a sector and start naming it:

    ($NEE OR $CEG OR $VST OR $DUK OR $D OR $LNG OR ...) lang:en -is:retweet

Every returned post then contains an in-sector security by construction. Both
measured failure modes -- posts with no ticker at all (68-78%), and cashtags
belonging to other sectors (41-63% in thin sectors) -- become impossible rather
than merely less likely.

WHY ASKING ABOUT EVERY TICKER IS AFFORDABLE

X bills per POST RETURNED, not per request. A ticker nobody is discussing
returns nothing and therefore costs nothing. So the question is not "which 55
tickers can we afford to ask about" but "how few requests can carry all of
them" -- roughly 55-58 cashtags fit X's 512-character limit, so 4 requests
cover all 182 live utilities tickers and 11 cover all 615 industrials.

The cost of a wrong entry in a basket is zero. The cost of a MISSING entry is a
ticker the user never discovers. That asymmetry says: fill every basket to the
character limit, and cover the whole sector.

WHY VOLUME-DESCENDING RATHER THAN INTERLEAVED

Sorting by dollar volume concentrates the loud names into basket 1, which looks
like a downside and is actually the point. A capped request returns whatever is
loudest inside it, so mixing a mega-cap into every basket would let it crowd out
the quiet names in ALL of them. Sorting quarantines the crowding into one basket
that can then be capped deliberately, leaving baskets 2..N containing only quiet
names whose posts spread across many tickers -- which is where a discovery
product's value actually is.

It also clusters correlated names, so a post naming two of them usually matches
one basket rather than two, and is billed once instead of twice.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# X recent-search query length cap, with headroom.
MAX_QUERY_LEN = 512
DEFAULT_TAIL = " lang:en -is:retweet"

# The UI sector -> Nasdaq sector strings used in ticker_master.
#
# SINGLE SOURCE OF TRUTH. pages/Discovery.py imports this rather than keeping
# its own copy: the validation step and the query generator must agree about
# what "utilities" means, and two hand-maintained copies of a mapping are
# exactly the drift that put 387 tickers starting with "A" into the price cache.
UI_TO_NASDAQ: dict[str, set[str]] = {
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

# Corporate-form noise. Stripped so "Howmet Aerospace Inc. Common Stock"
# becomes "Howmet Aerospace", which is what a person actually writes.
_SUFFIX = re.compile(
    r"\b(inc|incorporated|corp|corporation|company|co|ltd|limited|plc|lp|llc|"
    r"partnership|trust|fund|holdings?|group|the|common|stock|ordinary|shares?|"
    r"class\s+[a-z]|new|sa|nv|ag)\b\.?",
    re.I,
)

# "L.P." and "N.V." must lose their internal dots BEFORE suffix stripping, or
# \blp\b never matches them and the boilerplate survives into a live query --
# "Brookfield Renewable Partners L.P. Limited Partnership" was normalising to
# "Brookfield Renewable Partners L P Partnership", which would then be searched
# for verbatim and match nothing. Only single letters followed by a dot are
# collapsed, so "Inc." is untouched.
_INITIALS = re.compile(r"\b([A-Za-z])\.")

_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL_S = 15 * 60


def _config(name: str, default: str = "") -> str:
    v = os.getenv(name, "")
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets.get(name, "") or "") or default
    except Exception:
        return default


# Single words that are ordinary English or generic finance vocabulary, and so
# cannot be used as a search term even inside a finance-worded query.
#
# The naive rule is "multi-word aliases only", and it is too blunt: 22% of the
# ~7,600 ticker universe normalises to one word, and the great majority of
# those are coined and perfectly safe -- AerCap, Affirm, Alector, Altimmune,
# AudioEye, Adecoagro. A blanket rule discards all of them, and it discards
# "Tesla", which is the alias measured to change TSLA's direction reading.
#
# So the exclusion is by vocabulary, not by word count. The list is deliberately
# short: the query retains its finance OR-list conjunct, which already filters
# most ambient usage, so only words that are common IN FINANCE TEXT need to be
# here. "Southern" and "Visa" do; "Tesla" does not.
_GENERIC_SINGLE_WORD = frozenset({
    "american", "atlantic", "capital", "central", "century", "coastal",
    "commerce", "community", "consumer", "continental", "core", "eastern",
    "empire", "energy", "enterprise", "equity", "federal", "financial",
    "first", "fortune", "freedom", "frontier", "gap", "general", "global",
    "group", "growth", "heritage", "holdings", "horizon", "independence",
    "industrial", "international", "investors", "legacy", "liberty", "market",
    "meridian", "metro", "mid", "momentum", "mutual", "national", "northern",
    "open", "pacific", "partners", "peak", "pioneer", "premier", "prime",
    "progress", "prospect", "range", "regional", "republic", "reserve",
    "resources", "select", "sentinel", "shell", "signature", "southern",
    "standard", "sterling", "summit", "sun", "target", "trust", "union",
    "united", "universal", "value", "vision", "visa", "western",
})


def company_alias(name: str) -> str:
    """A phrase safe to search for, or "" when the name cannot carry a query.

    Returns "" rather than a risky term on purpose: a bad alias does not merely
    add noise, it adds noise that the ticker-attribution filter then certifies
    as valid evidence -- the failure that put 69 posts about Members of
    Parliament into an MP Materials corpus.
    """
    n = normalize_company_name(name)
    if not n:
        return ""
    if len(n.split()) >= 2:
        return n
    # Single word: long enough to be distinctive, and not ordinary vocabulary.
    return "" if (len(n) < 4 or n.lower() in _GENERIC_SINGLE_WORD) else n


def normalize_company_name(name: str) -> str:
    """Strip corporate boilerplate down to what a person would type."""
    n = re.sub(r"\(.*?\)", "", name or "")
    n = _INITIALS.sub(r"\1", n)
    n = _SUFFIX.sub("", n)
    n = re.sub(r"[^A-Za-z0-9&\s]", " ", n)
    return " ".join(n.split()).strip()


def _fetch(path: str) -> list[dict]:
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured")
    out: list[dict] = []
    offset = 0
    while True:
        sep = "&" if "?" in path else "?"
        req = urllib.request.Request(
            f"{base}/rest/v1/{path}{sep}offset={offset}&limit=1000",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read() or b"[]")
        out.extend(rows)
        if len(rows) < 1000:
            return out
        offset += 1000


def sector_universe(sector: str) -> list[dict]:
    """Live, in-sector tickers ordered by dollar volume, most traded first.

    "Live" means it has a row in stock_prices WITH a volume, which is only true
    if Polygon's grouped feed returned it for the last trading day. That filter
    does double duty: it proves the security actually traded, and it strips the
    warrants, units and preferred notes that never appear in conversation. A
    delisted symbol lingering in ticker_master cannot pass it.

    Dollar volume (close * volume) rather than raw share count, because share
    count flatters penny stocks -- ten million shares of a $0.05 name is
    $500,000 of interest, not a heavily-watched company.
    """
    key = (sector or "").strip().lower()
    allowed = UI_TO_NASDAQ.get(key)
    if not allowed:
        logger.warning("sector_query: no Nasdaq mapping for %r", sector)
        return []

    cached = _CACHE.get(key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_S:
        return cached[1]

    tm = _fetch("ticker_master?select=symbol,name,sector&order=symbol")
    prices = {
        (r.get("ticker") or "").upper(): r
        for r in _fetch("stock_prices?select=ticker,close_price,volume&order=ticker")
    }

    out: list[dict] = []
    for t in tm:
        if (t.get("sector") or "").strip() not in allowed:
            continue
        sym = (t.get("symbol") or "").upper()
        p = prices.get(sym)
        if not p or p.get("volume") in (None, 0) or not p.get("close_price"):
            continue
        out.append({
            "symbol": sym,
            "name": t.get("name") or "",
            "dollar_volume": float(p["close_price"]) * float(p["volume"]),
        })

    out.sort(key=lambda r: -r["dollar_volume"])
    _CACHE[key] = (time.time(), out)
    logger.info("sector_query: %s universe = %d live tickers", key, len(out))
    return out


def _pack(terms: list[str], tail: str) -> list[str]:
    """Greedily fill queries to the character limit, in the order given."""
    baskets: list[str] = []
    cur: list[str] = []
    for term in terms:
        # A term too long to fit even alone would otherwise be emitted as an
        # over-length query and 400 at fetch time. Drop it: one unsearchable
        # company name is a smaller loss than a basket that cannot be sent.
        if len("(" + term + ")" + tail) > MAX_QUERY_LEN:
            logger.warning("sector_query: dropping oversized term %s", term[:60])
            continue
        candidate = "(" + " OR ".join(cur + [term]) + ")" + tail
        if cur and len(candidate) > MAX_QUERY_LEN:
            baskets.append("(" + " OR ".join(cur) + ")" + tail)
            cur = [term]
        else:
            cur.append(term)
    if cur:
        baskets.append("(" + " OR ".join(cur) + ")" + tail)
    return baskets


class BasketFetcher:
    """Walk a set of basket queries BREADTH-FIRST, one page at a time.

    Extracted from pages/Discovery.py so the loop that spends money is
    testable. It was previously a closure inside module-level Streamlit script
    code, which meant the only way to exercise it was to run a paid scan.

    BREADTH BEFORE DEPTH. Every basket gets its first page before any basket
    gets a second. Paginating basket 1 buys more posts about tickers it already
    covered; fetching basket 2 covers tickers nobody has asked about, and only
    the second can surface a ticker we do not have. A basket with more results
    goes to the back of the queue, so depth happens only after breadth is done.

    AN EMPTY BASKET IS NORMAL, NOT A STOPPING CONDITION. sector_universe sorts
    by dollar volume, so baskets 2..N hold only quiet names -- the ones most
    likely to return nothing. Treating that as "the sector is exhausted" would
    abandon every remaining basket, which is exactly the bug this class was
    extracted to make visible.
    """

    # Above this many baskets, the first pass is fetched CONCURRENTLY. Baskets
    # are independent queries, so serialising them buys nothing but latency --
    # and finance has 27, which at ~1s each is half a minute of a paid scan
    # spent where a user re-click aborts the run.
    PARALLEL_ABOVE = 3

    def __init__(self, baskets: list[str], fetch, per_page: int = 25,
                 max_requests: int = 20, max_workers: int = 6):
        self.baskets = list(baskets)
        self.fetch = fetch
        self.per_page = per_page
        # Requests are free in billing terms, but each is a round trip inside a
        # window where a user re-click aborts the scan. Bounded so a wide sector
        # cannot stretch that window indefinitely.
        self.max_requests = max_requests
        self.max_workers = max_workers
        self.requests = 0
        self.pages: list[list[dict]] = []
        self._queue = deque((i, None) for i in range(len(self.baskets)))
        self._ready: deque = deque()          # pages already fetched, undelivered
        # Basket indices whose results have been DELIVERED to the caller. Not
        # "fetched": a prefetched page sitting in _ready has been paid for but
        # the caller has not seen its tickers yet, so treating it as sampled
        # lets the caller stop before processing it -- which made the whole
        # full-pass guarantee a no-op for every sector wide enough to trigger
        # the prefetch, i.e. 8 of 10.
        self._delivered: set = set()

    @property
    def exhausted(self) -> bool:
        if self._ready:
            return False
        return not self._queue or self.requests >= self.max_requests

    @property
    def first_pass_done(self) -> bool:
        """True once EVERY basket has been sampled at least once.

        The caller must not stop on a ticker target before this: baskets are
        ordered by dollar volume, and dollar volume turned out not to predict
        chatter. Measured on utilities, four of the six most-discussed tickers
        were outside basket 1 -- $AVA (14 mentions, basket 2) and $AWX (12,
        basket 4) would never have been seen by a scan that stopped after the
        first basket filled its ten slots.
        """
        return len(self._delivered) >= len(self.baskets)

    def prefetch_first_pass(self, post_budget: int | None = None) -> None:
        """Fetch page 1 of every basket concurrently. No-op for small sectors.

        Results are queued and delivered one page at a time by next_page(), so
        the caller's loop shape is unchanged -- it still processes a page,
        validates, and decides whether to continue.
        """
        if len(self.baskets) <= self.PARALLEL_ABOVE or self.requests:
            return
        n = min(len(self.baskets), self.max_requests)
        # Never buy more than the caller can process. Requests here are issued
        # BEFORE the caller's own post cap is consulted, so without this a wide
        # sector bills far past it -- finance is 28 baskets x 25 posts = 700
        # against a 300-post cap.
        if post_budget is not None:
            n = min(n, max(1, -(-post_budget // self.per_page)))
        logger.info("🧺 prefetching %d baskets concurrently (%d workers)",
                    n, self.max_workers)
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {ex.submit(self.fetch, self.baskets[i], self.per_page, None): i
                       for i in range(n)}
            for fut in as_completed(futures):
                i = futures[fut]
                self.requests += 1
                try:
                    res = fut.result()
                except Exception as e:
                    logger.warning("basket %d fetch raised: %s", i + 1, type(e).__name__)
                    res = {"success": False, "error": f"{type(e).__name__}"}
                self._ready.append((i, res))
        # Drop only the first-page entries we actually issued. Filtering on
        # `token is not None` dropped ALL of them, orphaning baskets n..len-1
        # whenever n was truncated -- they could then never be fetched and
        # first_pass_done could never become true.
        self._queue = deque(item for item in self._queue if item[0] >= n)

    def next_page(self) -> dict:
        """Fetch or deliver one page. Returns the result plus a `has_more` flag."""
        if self._ready:
            idx, res = self._ready.popleft()
            return self._accept(idx, res, counted=True)

        if self.exhausted:
            return {"success": True, "tweets": [], "has_more": False}

        idx, token = self._queue.popleft()
        self.requests += 1
        try:
            res = self.fetch(self.baskets[idx], self.per_page, token)
        except Exception as e:
            logger.warning("basket %d fetch raised: %s", idx + 1, type(e).__name__)
            return {"success": False, "error": f"{type(e).__name__}", "has_more": False}
        return self._accept(idx, res, counted=True)

    def _accept(self, idx: int, res: dict, counted: bool) -> dict:
        # Sampled means DELIVERED. Counting a failed page keeps first_pass_done
        # honest about coverage attempted rather than stalling forever.
        self._delivered.add(idx)
        if not res.get("success"):
            # Salvage: other prefetched pages are already bought and paid for.
            # Reporting has_more lets the caller record the error and keep
            # processing them instead of discarding a whole sector because one
            # concurrent request came back 429 first.
            return dict(res, has_more=bool(self._ready))
        tweets = res.get("tweets") or []
        self.pages.append(tweets)
        nt = res.get("next_token")
        if nt:
            self._queue.append((idx, nt))
        logger.info("🧺 basket %d/%d: %d posts, more=%s, pending=%d",
                    idx + 1, len(self.baskets), len(tweets), bool(nt),
                    len(self._ready) + len(self._queue))
        return dict(res, tweets=tweets, has_more=not self.exhausted)


def build_baskets(sector: str, kind: str = "cashtag",
                  tail: str = DEFAULT_TAIL) -> list[str]:
    """Query strings covering every live ticker in the sector.

    kind="cashtag" -- ($NEE OR $CEG OR ...). Cheap in characters, so a handful
        of baskets covers a whole sector.

    kind="name" -- ("NextEra Energy" OR "Duke Energy" OR ...). Catches posts
        that name a company without a cashtag, which are invisible to the
        pipeline today: "Honeywell Aerospace stock plunges 25%" yields nothing
        at present. MULTI-WORD names only -- single-word ones include real
        companies called INNOVATE and Outdoor, which match ordinary prose and
        would recreate the phantom problem at the name layer.

    Deliberately no topic words and no `(stock OR stocks OR shares)` conjunct.
    A cashtag already means someone is discussing a security; the conjunct only
    discards valid posts.
    """
    universe = sector_universe(sector)
    if not universe:
        return []

    if kind == "cashtag":
        terms = [f"${t['symbol']}" for t in universe]
    elif kind == "name":
        terms = []
        for t in universe:
            n = normalize_company_name(t["name"])
            if " " in n and len(n) >= 5:
                terms.append(f'"{n}"')
    else:
        raise ValueError(f"unknown basket kind: {kind!r}")

    baskets = _pack(terms, tail)
    logger.info("sector_query: %s -> %d %s basket(s) covering %d terms",
                sector, len(baskets), kind, len(terms))
    return baskets
