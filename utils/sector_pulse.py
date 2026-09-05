"""Nightly sector pulse: where is money entering, from bars we already have. Standard library only.

WHY THIS EXISTS

Discovery asks the user to pick one of ten sectors and spend a credit before it
shows any evidence about which sector is alive. Everything needed to answer
that -- the whole market's daily close and volume, and each ticker's sector --
is already in price_history and ticker_master. Nothing computed it. This module
does, once a night, and writes ten rows to public.sector_pulse.

WHAT IT MEASURES, AND WHAT IT REFUSES TO

Raw dollar volume is not a ranking input. Measured 2026-08-19: healthcare
"doubled" and 70% of the increase was MRNA (+177%, 90x its own volume). Every
measure here is equal-weight or a count, so one name cannot become a sector:

  ud_ratio_5d    $ volume on up days / $ volume on down days, last 5 sessions,
                 summed across names. Money entering (>1.3) or leaving (<0.8).
  breadth        share of eligible names whose day's $ volume is > 1.5x their
                 ROBUST baseline. Participation, not size.
  acc/dist days  sessions in the last 5 where the equal-weight sector return
                 was positive (negative) on volume heavier than the day before.
  eq_return_5d   mean 5-session return across names; pct_up_5d the share up.
  top_contrib    the three names contributing most of the volume INCREASE,
                 with their share -- so an event is named, not just flagged.

THE BASELINE IS SPIKE-ROBUST. A rolling median that contains a name's own
event forgets what normal was exactly when it matters: against a 10-day
median, MRNA read as quieter than normal a week after its spike while doing
6-10x its pre-event volume. The baseline here is the median of the trailing
20 sessions with that name's top three days removed.

STATES are provisional. The thresholds below are the first guess from three
weeks of bars; scripts/sector_pulse_backtest.py over a year of history is what
turns them into numbers -- change them THERE first, then here.

FAILURE POLICY

run() never raises. The sync calls it after the price write has already
succeeded; a broken pulse must cost the pulse, not the prices. Every swallowed
error is logged at WARNING with its type, and run() returns a summary the
caller turns into its own exit code and its own healthcheck ping -- so a red
pulse and a green sync are distinguishable, which is the repo's rule for jobs
that fail for unrelated reasons.

Stdlib-only for the same reason as utils/prices.py: one implementation that
both the Streamlit portal and the bare python:3.11-slim sync container import,
rather than a second copy that drifts.
"""

from __future__ import annotations

import json
import logging
import statistics
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from utils.config import get as _config
from utils.sector_query import UI_TO_NASDAQ

logger = logging.getLogger(__name__)

TABLE = "sector_pulse"
READER = "get_sector_pulse_recent"

# ---- provisional thresholds: see scripts/sector_pulse_backtest.py ----------
UD_ACCUMULATING = 1.3       # up-volume / down-volume above this reads as money entering
UD_DISTRIBUTING = 0.8       # below this, money leaving
BREADTH_MIN = 0.20          # share of names elevated for a sector to count as participating
EVENT_SHARE = 0.40          # one name explaining this much of the rise is an event, not a sector
CLIMAX_REL_VOL = 5.0        # a name above this multiple of its baseline is at a climax
ELEVATED_REL_VOL = 1.5      # "elevated" for breadth

# ---- eligibility -----------------------------------------------------------
MIN_PRICE = 5.0
MIN_BASELINE_DOLLAR_VOL = 5e6
BASELINE_SESSIONS = 20
BASELINE_DROP_TOP = 3
BASELINE_MIN_PRESENT = 15
WINDOW = 5
# Names that are instruments rather than companies. The same filter the
# sector universe applies by requiring volume, expressed on the name because a
# SPAC trades.
JUNK_NAME_WORDS = ("acquisition", "warrant", "unit", "right", "preferred",
                   "depositary", "trust", "fund", "etf")

STATES = ("accumulating", "distributing", "event", "quiet")

_NASDAQ_TO_UI = {n: u for u, ns in UI_TO_NASDAQ.items() for n in ns}


# ---------------------------------------------------------------- transport --

def _endpoint() -> tuple[str, str] | None:
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    return (base, key) if base and key else None


def _headers(key: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if extra:
        h.update(extra)
    return h


def _get_paged(base: str, key: str, path: str, page: int = 1000) -> list[dict]:
    """Every row behind `path`, paging at PostgREST's max-rows cap."""
    out: list[dict] = []
    offset = 0
    sep = "&" if "?" in path else "?"
    while True:
        req = urllib.request.Request(f"{base}/rest/v1/{path}{sep}limit={page}&offset={offset}",
                                     headers=_headers(key))
        with urllib.request.urlopen(req, timeout=60) as r:
            chunk = json.loads(r.read() or b"[]")
        out.extend(chunk)
        if len(chunk) < page:
            return out
        offset += page


# ------------------------------------------------------------------ loading --

def load_sectors(base: str, key: str) -> tuple[dict[str, str], dict[str, str]]:
    """ticker -> UI sector slug, and ticker -> name, for every mapped ticker."""
    rows = _get_paged(base, key, "ticker_master?select=symbol,name,sector&order=symbol")
    sec: dict[str, str] = {}
    names: dict[str, str] = {}
    for r in rows:
        sym = (r.get("symbol") or "").upper()
        ui = _NASDAQ_TO_UI.get((r.get("sector") or "").strip())
        if sym and ui:
            sec[sym] = ui
            names[sym] = r.get("name") or ""
    return sec, names


def load_bars(base: str, key: str, since: date) -> dict[str, dict[str, tuple[float, float]]]:
    """ticker -> {trade_date: (close, dollar_volume)} for bars on or after `since`."""
    rows = _get_paged(base, key,
                      f"price_history?select=ticker,trade_date,close,volume"
                      f"&trade_date=gte.{since.isoformat()}&order=trade_date.asc,ticker.asc")
    bars: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for r in rows:
        c, v = r.get("close"), r.get("volume")
        if c is None or not v:
            continue
        bars[(r.get("ticker") or "").upper()][r["trade_date"]] = (float(c), float(c) * float(v))
    return bars


# ------------------------------------------------------------------- maths --

def robust_baseline(dollar_vols: list[float]) -> float | None:
    """Median of the trailing sessions with the top BASELINE_DROP_TOP removed.

    A name's own spike must not move its own baseline; dropping the loudest
    few days is what keeps 'normal' meaning what it meant before the event.
    """
    if len(dollar_vols) < BASELINE_MIN_PRESENT:
        return None
    kept = sorted(dollar_vols)[:-BASELINE_DROP_TOP] if len(dollar_vols) > BASELINE_DROP_TOP else dollar_vols
    return statistics.median(kept) if kept else None


def is_junk_name(name: str) -> bool:
    n = (name or "").lower()
    return any(w in n for w in JUNK_NAME_WORDS)


def calendar_flag(day: date) -> str | None:
    """Days when every sector's volume jumps for reasons that are not conviction."""
    # Third Friday: monthly options expiry.
    if day.weekday() == 4 and 15 <= day.day <= 21:
        return "opex"
    # Last weekday of the month (holiday-agnostic on purpose: a flag, not a calendar).
    nxt = day + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    if nxt.month != day.month:
        return "quarter_end" if day.month in (3, 6, 9, 12) else "month_end"
    return None


def compute_day(sector: str, day: str, dates: list[str], bars: dict, sec: dict, names: dict) -> dict | None:
    """One sector's row for `day`. `dates` is the ascending list of trading dates loaded."""
    i = dates.index(day)
    if i < WINDOW:
        return None
    base_days = dates[max(0, i - BASELINE_SESSIONS):i]
    win = dates[i - WINDOW + 1:i + 1]          # the 5 sessions ending on `day`
    prev5 = dates[i - WINDOW]                   # the close 5 sessions before `day`

    eligible: list[str] = []
    baseline: dict[str, float] = {}
    for t, s in sec.items():
        if s != sector or is_junk_name(names.get(t, "")):
            continue
        series = bars.get(t)
        if not series or day not in series or prev5 not in series:
            continue
        if series[day][0] < MIN_PRICE:
            continue
        hist = [series[d][1] for d in base_days if d in series]
        b = robust_baseline(hist)
        if b is None or b < MIN_BASELINE_DOLLAR_VOL:
            continue
        if any(d not in series for d in win) or dates[i - WINDOW] not in series:
            continue
        eligible.append(t)
        baseline[t] = b
    if not eligible:
        return None

    def ret(t: str, d: str) -> float:
        p = dates[dates.index(d) - 1]
        return bars[t][d][0] / bars[t][p][0] - 1.0

    # up/down dollar volume over the window
    up = dn = 0.0
    for t in eligible:
        for d in win:
            r = ret(t, d)
            if r > 0:
                up += bars[t][d][1]
            elif r < 0:
                dn += bars[t][d][1]
    ud = (up / dn) if dn > 0 else None

    # breadth on the day
    elevated = sum(1 for t in eligible if bars[t][day][1] > ELEVATED_REL_VOL * baseline[t])
    breadth = elevated / len(eligible)

    # accumulation / distribution days
    acc = dist = 0
    for d in win:
        p = dates[dates.index(d) - 1]
        eq = statistics.mean(ret(t, d) for t in eligible)
        vol_d = sum(bars[t][d][1] for t in eligible)
        vol_p = sum(bars[t][p][1] for t in eligible if p in bars[t])
        if vol_d > vol_p:
            acc += eq > 0
            dist += eq < 0

    eq5 = statistics.mean(bars[t][day][0] / bars[t][prev5][0] - 1.0 for t in eligible)
    pct_up = sum(1 for t in eligible if bars[t][day][0] > bars[t][prev5][0]) / len(eligible)

    # who drove the day's volume increase
    inc = {t: bars[t][day][1] - baseline[t] for t in eligible}
    pos_total = sum(v for v in inc.values() if v > 0)
    top = sorted(inc.items(), key=lambda kv: -kv[1])[:3]
    top_contrib = [
        {"ticker": t, "share_of_rise": round(v / pos_total, 4) if pos_total > 0 and v > 0 else 0.0,
         "ret_1d": round(ret(t, day), 4), "rel_vol": round(bars[t][day][1] / baseline[t], 2)}
        for t, v in top
    ]

    return {
        "sector": sector, "trade_date": day, "n_eligible": len(eligible),
        "ud_ratio_5d": round(ud, 4) if ud is not None else None,
        "breadth": round(breadth, 4), "acc_days_5d": acc, "dist_days_5d": dist,
        "eq_return_5d": round(eq5, 5), "pct_up_5d": round(pct_up, 4),
        "top_contrib": top_contrib,
        "calendar_flag": calendar_flag(datetime.strptime(day, "%Y-%m-%d").date()),
    }


def classify(row: dict, breadth_two_days_ago: float | None) -> str:
    """The state vocabulary the table CHECKs. Order matters: an event is named before anything else."""
    top = row["top_contrib"][0] if row["top_contrib"] else None
    if top and top["share_of_rise"] >= EVENT_SHARE and top["rel_vol"] >= CLIMAX_REL_VOL:
        return "event"
    ud = row["ud_ratio_5d"]
    rising = breadth_two_days_ago is None or row["breadth"] >= breadth_two_days_ago
    if (ud is None or ud >= UD_ACCUMULATING) and row["breadth"] >= BREADTH_MIN \
            and (row["eq_return_5d"] or 0) > 0 and rising:
        return "accumulating"
    if ud is not None and ud <= UD_DISTRIBUTING and row["dist_days_5d"] >= row["acc_days_5d"]:
        return "distributing"
    return "quiet"


# ---------------------------------------------------------------- writing --

def write_rows(base: str, key: str, rows: list[dict]) -> None:
    """Upsert on (sector, trade_date). Raises; run() is the one that swallows."""
    req = urllib.request.Request(
        f"{base}/rest/v1/{TABLE}",
        data=json.dumps(rows).encode(),
        headers=_headers(key, {"Content-Type": "application/json",
                               "Prefer": "resolution=merge-duplicates,return=minimal"}),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status not in (200, 201, 204):
            raise RuntimeError(f"{TABLE} upsert HTTP {r.status}")


# -------------------------------------------------------------------- run --

def run(day: date | None = None, *, dry_run: bool = False,
        _loaders: tuple[Callable, Callable] | None = None,
        _writer: Callable | None = None) -> dict[str, Any]:
    """Compute every sector for the latest bar (or `day`) and upsert. Never raises.

    Returns {ok, trade_date, rows: [...], written, error}. `_loaders`/`_writer`
    exist for tests; production callers pass nothing.
    """
    summary: dict[str, Any] = {"ok": False, "trade_date": None, "rows": [], "written": 0, "error": None}
    try:
        ep = _endpoint()
        if ep is None:
            summary["error"] = "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured"
            logger.warning("sector_pulse: %s", summary["error"])
            return summary
        base, key = ep
        load_s, load_b = _loaders or (load_sectors, load_bars)
        sec, names = load_s(base, key)
        # 20 baseline sessions + a 5-day window + 2 days of breadth history,
        # in calendar days with weekends and a holiday or two.
        # UTC, not date.today(): trade_date is a market date, and a local
        # date in the Americas is a day behind for part of every evening.
        since = (day or datetime.now(timezone.utc).date()) - timedelta(days=50)
        bars = load_b(base, key, since)
        dates = sorted({d for s in bars.values() for d in s})
        if not dates:
            summary["error"] = "no bars loaded"
            logger.warning("sector_pulse: no bars since %s", since)
            return summary
        target = day.isoformat() if day else dates[-1]
        if target not in dates:
            summary["error"] = f"no bar for {target}; latest is {dates[-1]}"
            logger.warning("sector_pulse: %s", summary["error"])
            return summary
        i = dates.index(target)
        rows: list[dict] = []
        for sector in UI_TO_NASDAQ:
            row = compute_day(sector, target, dates, bars, sec, names)
            if row is None:
                logger.info("sector_pulse: %s has no eligible names on %s", sector, target)
                continue
            b2 = None
            if i >= 2:
                prior = compute_day(sector, dates[i - 2], dates, bars, sec, names)
                b2 = prior["breadth"] if prior else None
            row["state"] = classify(row, b2)
            rows.append(row)
        summary.update(trade_date=target, rows=rows)
        if dry_run:
            summary["ok"] = True
            return summary
        (_writer or write_rows)(base, key, rows)
        summary.update(ok=True, written=len(rows))
        logger.info("sector_pulse: wrote %d rows for %s", len(rows), target)
        return summary
    except Exception as e:  # noqa: BLE001
        summary["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning("sector_pulse: run failed: %s", summary["error"], exc_info=True)
        return summary


# ------------------------------------------------------------------- read --

def latest(days: int = 6, *, key: str | None = None) -> list[dict]:
    """Every sector for the most recent `days` dates, via the narrow reader. [] on any failure.

    ANON KEY ONLY, on purpose. The reader is a SECURITY DEFINER RPC that anon
    may execute, so the portal never needs the service key for this feature
    and the blast radius of a browser-side compromise does not grow by one
    table. Plain dicts, newest first -- the caller builds a DataFrame if it
    wants one.
    """
    base = _config("SUPABASE_URL").rstrip("/")
    k = key or _config("SUPABASE_ANON_KEY")
    if not base or not k:
        return []
    req = urllib.request.Request(
        f"{base}/rest/v1/rpc/{READER}",
        data=json.dumps({"days": int(days)}).encode(),
        headers=_headers(k, {"Content-Type": "application/json"}),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            rows = json.loads(r.read() or b"[]")
        return rows if isinstance(rows, list) else []
    except Exception as e:  # noqa: BLE001
        logger.warning("sector_pulse: latest() failed: %s: %s", type(e).__name__, str(e)[:160])
        return []
