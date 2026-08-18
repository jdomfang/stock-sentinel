"""Was it this company, or was it the whole sector? Standard library only.

WHY THIS EXISTS

The price veto asks "does the tape contradict the bullish story about THIS
company". It has never been able to answer that, because it only sees the
ticker: a stock down 18% in a sector down 17% and a stock down 18% in a sector
that rose 3% are the same number to it, and the second is a company-specific
collapse while the first is a market a trader may want to buy into.

That is not a subtle distinction. In any broad drawdown -- and drawdowns are
when a short-term tool is used most -- the veto currently fires on essentially
every ticker at once, for a reason that has nothing to do with any of them.

WHAT IT COSTS

One extra daily-bars call per analysis, against a sector ETF, on the price
provider we already pay for. NOT an X call: the standing rule about X credits
does not apply, and nothing here touches the X API.

The ETF is shared across every ticker in a sector and its bars change once a
day, and utils.finance caches daily bars process-wide for 30 minutes, so the
benchmark is warm nearly always. A Discovery SCAN makes zero of these calls --
adjudication runs only for the one row a user opens.

The sector lookup behind it is cached here too. Deep Analyze hardcodes
sector="unknown", so every analysis fell through to an uncached Supabase GET
with an 8s timeout on the paid request path.

WHAT IT DELIBERATELY DOES NOT DO

It does not create a Buy. Relative strength is a genuine signal and it is also
completely unvalidated here, so it enters exactly where the absolute number
already entered -- as context on a veto -- and nowhere else. The one behaviour
change is documented at PRICE_MARKET_WIDE_SIGMA in utils/modules.py, and it is
bounded: below PRICE_EXEMPTION_FLOOR the veto stands whatever the sector did.

FAILURE POLICY

Never raises. No ETF, no sector, a failed fetch, a short series: all return None
and the price module falls back to the absolute reading it has always used.
"""

from __future__ import annotations

import logging
from typing import Sequence

logger = logging.getLogger(__name__)

# SPDR select-sector funds, keyed by the slug Discovery scans under. These are
# the most liquid sector proxies available and they are ordinary tickers, so the
# same price path serves them with no new provider and no new credentials.
#
SECTOR_ETF: dict[str, str] = {
    "tech": "XLK",
    "healthcare": "XLV",
    "energy": "XLE",
    "finance": "XLF",
    "utilities": "XLU",
    "real estate": "XLRE",
    "industrials": "XLI",
    "materials": "XLB",
}

# TWO SLUGS ARE DELIBERATELY ABSENT, and no benchmark is better than a wrong
# one: an unmapped sector falls back to the absolute veto, which is the
# conservative behaviour. Both omissions were one-way errors that made the
# exemption EASIER, in the regime the feature targets.
#
#   consumer       utils/sector_query.py folds Consumer Discretionary and
#                  Consumer Staples into one slug. XLY runs ~1.5-1.8x XLP's
#                  volatility and the two decouple hard in a rotation (2022:
#                  XLY -37%, XLP -3%). Benchmarking a staples name against XLY
#                  means the benchmark is systematically MORE negative in a
#                  drawdown, so a company-specific fall in PG reads as
#                  sector-wide. The slug cannot tell the two apart, so neither
#                  can we.
#
#   communication  the slug maps to Nasdaq's "Telecommunications" -- T, VZ,
#                  TMUS -- while XLC is ~45% META + GOOGL, which Nasdaq files
#                  under Technology. The benchmark's universe and the slug's do
#                  not intersect. 2022: XLC -40% against VZ -25%, so essentially
#                  ANY telco decline would have shown positive excess and been
#                  exempted permanently.
_UNMAPPED_BY_DESIGN = frozenset({"consumer", "communication"})

# Every ETF above is itself a ticker in the system. Benchmarking one against
# itself would report a 0% excess return for XLK on any run, which reads as
# "perfectly in line with its sector" when it IS the sector.
_ETF_SYMBOLS = frozenset(SECTOR_ETF.values())


def etf_for(sector: str) -> str:
    """Discovery slug -> sector ETF, or "" when there is no mapping."""
    s = (sector or "").strip().lower()
    if not s or s == "unknown" or s in _UNMAPPED_BY_DESIGN:
        return ""
    if s in SECTOR_ETF:
        return SECTOR_ETF[s]
    # A caller may hand us a Nasdaq name ("Basic Materials") rather than a slug.
    # utils/seed.py hit exactly this and it silently killed the channel for half
    # the sectors, so the reconciliation lives in one place and is reused.
    try:
        from utils.seed import _to_ui_slug
        slug = _to_ui_slug(sector)
        # Checked again here: the Nasdaq-name path would otherwise reintroduce
        # exactly the mappings the block above removes, e.g. "Consumer Staples"
        # resolving to the discretionary ETF.
        return "" if slug in _UNMAPPED_BY_DESIGN else SECTOR_ETF.get(slug, "")
    except Exception:
        return ""


# A ticker's sector changes approximately never, and the lookup is an uncached
# synchronous Supabase GET with an 8s timeout sitting on the paid request path.
# Deep Analyze hardcodes sector="unknown", so EVERY analysis fell through to it
# -- twice, since the seed reuse looks it up again.
_SECTOR_CACHE: dict[str, str] = {}


def sector_of(ticker: str) -> str:
    """The ticker's sector as a Discovery slug. Reuses the seed lookup, cached."""
    t = (ticker or "").strip().upper()
    if not t:
        return ""
    if t in _SECTOR_CACHE:
        return _SECTOR_CACHE[t]
    try:
        from utils.seed import sector_of as _s
        slug = _s(t)
    except Exception:
        logger.warning("relative: sector lookup failed for %s", t, exc_info=True)
        slug = ""
    # Negative results are cached too. A ticker with no sector will not acquire
    # one during a session, and retrying costs the same 8s timeout every time.
    _SECTOR_CACHE[t] = slug
    return slug


def benchmark_prices(ticker: str, sector: str = "") -> tuple[str, dict | None]:
    """(etf_symbol, {"prices", "bar_date"}) for `ticker`'s sector, or ("", None).

    Returns the SYMBOL as well so the row can record which benchmark was used.
    A comparison whose benchmark is unknown cannot be audited later, and the
    map above is expected to change.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return "", None
    # An ETF is its own sector; comparing it to itself is meaningless.
    if t in _ETF_SYMBOLS:
        return "", None

    slug = (sector or "").strip().lower()
    if not slug or slug == "unknown" or slug not in SECTOR_ETF:
        slug = sector_of(t) or slug
    etf = etf_for(slug)
    if not etf:
        logger.info("relative: no sector ETF for %s (sector=%r)", t, sector)
        return "", None

    try:
        from utils.finance import get_stock_data
        data = get_stock_data(etf)
        if data.get("error") is not None:
            logger.info("relative: %s bars unavailable: %s", etf, data.get("error"))
            return etf, None
        prices = data.get("prices") or None
        # The bar date rides along so the caller can refuse a comparison whose
        # two series do not end on the same session.
        return etf, ({"prices": prices, "bar_date": data.get("last_bar_date")}
                     if prices else None)
    except Exception:
        logger.warning("relative: benchmark fetch failed for %s", etf, exc_info=True)
        return etf, None


def excess_return(prices: Sequence[float] | None,
                  benchmark: Sequence[float] | None,
                  window: int = 21,
                  *,
                  ticker_bar_date: str | None = None,
                  benchmark_bar_date: str | None = None) -> dict:
    """Excess return over a window BOTH series actually cover.

    Returns a dict; every field is None when the comparison was refused, and
    refusal falls back to the absolute veto, which is the safe direction.

    TWO ALIGNMENT RULES, BOTH LEARNED THE HARD WAY.

    SAME LAST SESSION. These are bare lists of closes with no dates attached,
    so equal length is not equal dates. A halted ticker whose series ends three
    sessions early, compared against an ETF ending today, slides the benchmark
    window forward into sessions the ticker never traded -- and in a selloff
    that makes the benchmark look worse and inflates excess toward exemption.
    Reproduced: a ticker down 16% over the 18 sessions it actually traded, with
    its sector flat over those same sessions, reported "sector-wide" and cleared
    a veto that should have stood. A halted stock is the last case that should
    get an automatic pass, so the bar dates must match.

    SAME WINDOW LENGTH. Taking n from whichever series is shorter let a
    SEVEN-session benchmark comparison cancel a twenty-one-session, 9x-volume
    collapse. The comparison must span what the veto spans, or it is not
    evidence about the veto.

    AND THE BOUNDARY IS MEASURED, NOT INVENTED. `noise` is the standard
    deviation of the daily return differences over the window, scaled by
    sqrt(n) -- the ordinary dispersion of THIS stock against THIS benchmark. A
    fixed -5% boundary is roughly one sigma for a mega-cap and under half a
    sigma for a small cap, so it holds the small caps that need the veto most to
    the strictest standard. Deriving it from the two series in hand scales
    automatically and adds no constant to the codebase.
    """
    import math

    def _clean(xs):
        return [float(p) for p in (xs or [])
                if isinstance(p, (int, float)) and math.isfinite(float(p))
                and float(p) > 0]

    out = {"sector_return": None, "excess": None, "window": None,
           "noise": None, "refused": "no benchmark"}

    a, b = _clean(prices), _clean(benchmark)
    n = min(len(a), int(window))
    if n < 5:
        return {**out, "refused": "ticker series too short"}
    if len(b) < n:
        # Refused, NOT silently shortened to the benchmark's length.
        return {**out, "refused": f"benchmark covers {len(b)} of {n} sessions"}
    # BOTH DATES ARE REQUIRED, not merely compared when present. These are bare
    # lists of closes: matching COUNTS does not prove matching DATES, because a
    # thinly-traded name's 21 bars can span 24 calendar sessions while the ETF's
    # 21 span 21. Without the dates that misalignment is undetectable, and it
    # biases in the permissive direction. Both series come from get_stock_data,
    # which supplies the date, so demanding it costs nothing real -- and when it
    # is absent, falling back to the absolute veto is the safe direction for the
    # one change in this codebase that makes the product say Buy more often.
    if not ticker_bar_date or not benchmark_bar_date:
        return {**out, "refused": "bar dates unavailable, cannot align"}
    if ticker_bar_date != benchmark_bar_date:
        return {**out, "refused":
                f"series end on different sessions ({ticker_bar_date} vs "
                f"{benchmark_bar_date})"}

    aw, bw = a[-n:], b[-n:]
    try:
        r_t = (aw[-1] - aw[0]) / aw[0]
        r_b = (bw[-1] - bw[0]) / bw[0]
    except ZeroDivisionError:
        return {**out, "refused": "zero price in series"}
    if not (math.isfinite(r_t) and math.isfinite(r_b)):
        return {**out, "refused": "non-finite return"}

    # Daily return differences -> the dispersion of this pair specifically.
    diffs = []
    for i in range(1, n):
        if aw[i - 1] > 0 and bw[i - 1] > 0:
            diffs.append((aw[i] - aw[i - 1]) / aw[i - 1]
                         - (bw[i] - bw[i - 1]) / bw[i - 1])
    noise = None
    if len(diffs) >= 4:
        mean = sum(diffs) / len(diffs)
        var = sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)
        if math.isfinite(var) and var >= 0:
            noise = math.sqrt(var) * math.sqrt(len(diffs))

    return {"sector_return": round(r_b, 4), "excess": round(r_t - r_b, 4),
            "window": n, "noise": (round(noise, 4) if noise is not None else None),
            "refused": None}
