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

    def __init__(self, baskets: list[str], fetch, per_page: int = 25,
                 max_requests: int = 20):
        self.baskets = list(baskets)
        self.fetch = fetch
        self.per_page = per_page
        # Requests are free in billing terms, but each is a serial round trip
        # inside a window where a user re-click aborts the scan. Bounded so a
        # wide sector cannot stretch that window indefinitely.
        self.max_requests = max_requests
        self.requests = 0
        self.pages: list[list[dict]] = []
        self._queue = deque((i, None) for i in range(len(self.baskets)))

    @property
    def exhausted(self) -> bool:
        return not self._queue or self.requests >= self.max_requests

    def next_page(self) -> dict:
        """Fetch one page. Returns the raw result plus a `has_more` flag."""
        if self.exhausted:
            return {"success": True, "tweets": [], "has_more": False}

        idx, token = self._queue.popleft()
        self.requests += 1
        res = self.fetch(self.baskets[idx], self.per_page, token)
        if not res.get("success"):
            return dict(res, has_more=False)

        tweets = res.get("tweets") or []
        self.pages.append(tweets)
        nt = res.get("next_token")
        if nt:
            self._queue.append((idx, nt))

        logger.info("🧺 basket %d/%d: %d posts, more=%s, remaining=%d",
                    idx + 1, len(self.baskets), len(tweets), bool(nt), len(self._queue))
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
