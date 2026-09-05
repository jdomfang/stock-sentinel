# Sector Pulse — the UI contract

For whoever builds the strip. The backend is finished, deployed, and filled with
real rows; this file is the whole interface. Read
[SECTOR_PULSE.md](SECTOR_PULSE.md) for what the numbers mean and what they are
not allowed to claim.

## The boundary

**Yours to change**

```
pages/Discovery.py     the strip above the sector picker
pages/Home.py          a read-only strip for anonymous visitors
utils/ui.py            styling
```

**Not yours. Do not edit.**

```
utils/sector_pulse.py            the computation and latest()
scripts/sector_pulse_backtest.py the validation
sync/                            the nightly job
supabase/migrations/             the table and its reader
docs/SECTOR_PULSE.md             the decision rule and result
```

If the strip needs a field the reader does not return, say so rather than
reaching into the table or recomputing beside it. There is exactly one path to
this data and it is the function below.

## The one call

```python
from utils import sector_pulse

rows = sector_pulse.latest(days=6)     # list[dict], newest date first, [] on failure
```

* Returns **10 rows per date** — every sector, every date, flat. Group by
  `trade_date` for the current strip and by `sector` for a sparkline.
* Uses the **anon** key through a `SECURITY DEFINER` RPC, so it works for
  signed-out visitors on Home with no extra wiring.
* **Never raises.** A transport failure returns `[]`. Render nothing, or a
  quiet "pulse unavailable" — never an exception page.
* Cheap: one HTTP call, ~60 small rows. Safe to call on every render, though
  caching it for a few minutes is kind.

## One real row, verbatim

```json
{
  "sector": "healthcare",
  "trade_date": "2026-08-28",
  "n_eligible": 380,
  "ud_ratio_5d": 0.698,
  "breadth": 0.0842,
  "acc_days_5d": 1,
  "dist_days_5d": 0,
  "eq_return_5d": -0.01527,
  "pct_up_5d": 0.3026,
  "state": "event",
  "top_contrib": [
    {"ticker": "MRNA", "share_of_rise": 0.5925, "rel_vol": 8.97, "ret_1d": -0.0335},
    {"ticker": "CYTK", "share_of_rise": 0.0361, "rel_vol": 2.58, "ret_1d": -0.0746},
    {"ticker": "IQV",  "share_of_rise": 0.0360, "rel_vol": 1.66, "ret_1d": -0.0024}
  ],
  "calendar_flag": null,
  "computed_at": "2026-09-05T01:35:08.960913+00:00"
}
```

| field | meaning |
|---|---|
| `state` | `accumulating` · `distributing` · `event` · `quiet` — the four are exhaustive |
| `breadth` | 0–1. Share of the sector's names trading above 1.5× their own normal volume. **Participation** |
| `ud_ratio_5d` | $ volume on up days ÷ down days over 5 sessions. `null` when no down-day volume exists — render as "—", not 0 |
| `eq_return_5d` | equal-weight 5-session return, as a fraction. **The sparkline series** |
| `pct_up_5d` | 0–1, share of names above their 5-sessions-ago close |
| `acc_days_5d` / `dist_days_5d` | 0–5. Sessions up (down) on heavier volume |
| `top_contrib` | up to 3 names driving the volume increase. `share_of_rise` 0–1; `rel_vol` is a multiple |
| `calendar_flag` | `month_end` · `quarter_end` · `opex` · `null`. When set, say so — volume rose for a calendar reason |
| `n_eligible` | how many names the state was computed from. Useful in a tooltip |

## Ordering

`accumulating` → `event` → `quiet` → `distributing`, and within a state by
`breadth` descending. Not by `eq_return_5d`: the strip ranks **participation**,
not performance, and sorting by return is the first step toward implying one.

## Wording — the rule, not a preference

This is in [AGENTS.md](../AGENTS.md) and it is load-bearing. The measured edge is
+0.25 percentage points of median 5-session excess return inside an
interquartile range 3.4 points wide, across hundreds of overlapping sector-days.
It supports description. It does not support a forecast.

| ❌ never | ✅ instead |
|---|---|
| "Top opportunity", "#1 pick" | "Highest participation" |
| "Energy will outperform" | "Energy — money entering, 26% of names above their normal volume" |
| "Best sector to trade" | "Best sector to **scan**" |
| "Buy signal", "expected to rise" | "5 sessions up, volume 1.3× normal, no single name dominating" |

**`event` is the exception where firm language is earned** — it is the one state
reliably negative at every horizon tested. Say it plainly:

> **Healthcare · event** — MRNA is 59% of the move at 9× its normal volume.
> That is one company, not the sector.

And it should steer the reader to Deep Analyze on that ticker rather than a
sector scan.

## Suggested shape — not binding

```
SECTOR PULSE · through Thu 03 Sep

● accumulating   Energy         ▃▂▅▇▇▅   26% of names active · 5 sessions up   [Run scan · 1 credit]
◆ event          Healthcare     ▅▂▂▁▂▂   MRNA is 59% of the move — one company [Deep Analyze MRNA]
○ quiet          Tech           ▆▃▅▂▂▁   13% active · nothing unusual          [Run scan · 1 credit]
▼ distributing   Materials      ▇▂▁▁▁▁   6% active · selling on volume         [Run scan · 1 credit]
```

`eq_return_5d` across the returned dates gives the sparkline. The scan button is
the one that already exists — the strip's whole job is to make that click
better informed, not to replace it.

## State today

Real rows for **20 trading days** through 2026-09-03, covering all four states
(`accumulating` 12, `distributing` 49, `event` 9, `quiet` 130). `latest(days=6)`
returns 60 rows with 6 sparkline points per sector, verified through the anon
key. The nightly sync adds one date per day at 23:00 UTC.

You can build against live data immediately; nothing here is a fixture.
