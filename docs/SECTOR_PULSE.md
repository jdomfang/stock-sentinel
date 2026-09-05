# Sector Pulse — what it claims, and the rule that decides whether it may claim it

The pulse ranks the ten sectors nightly by where money is entering, so a user's
first credit lands on a sector that is alive rather than on a guess. It is a
**navigation aid**, not a trade signal, and the wording on the page must stay
inside whatever the evidence below supports.

`utils/sector_pulse.py` computes it. `scripts/sector_pulse_backtest.py` tests
it. This file is the contract between them.

## The thresholds under test

Pre-registered, and chosen from three weeks of bars before a year of history
existed:

```
UD_ACCUMULATING = 1.3     up-volume / down-volume above this is money entering
UD_DISTRIBUTING = 0.8     below this, money leaving
BREADTH_MIN     = 0.20    share of names above 1.5x their robust baseline
EVENT_SHARE     = 0.40    one name explaining this much of the rise is an event
```

## THE DECISION RULE

**Written before the backtest was run. Not to be edited after seeing results —
a threshold chosen because it flattered the outcome is not evidence.**

> **Ship the ranking** if, on the pre-registered thresholds above, sector-days
> labelled `accumulating` beat sector-days labelled `quiet` on **median forward
> 5-session equal-weight return in excess of the market**, with **at least 100
> `accumulating` events** in the backfilled year.
>
> **Ship the labels without a ranking claim** if the rule is not met. The strip
> then describes what is happening — breadth, direction, who is driving it —
> and never orders sectors by implied opportunity or calls one a "candidate".
>
> **Change a threshold only if the sweep shows the pre-registered value sits on
> a cliff** — i.e. neighbouring values disagree wildly — which is evidence the
> value is arbitrary rather than evidence a different one is better. Any change
> is recorded here with the table that motivated it.

### What "beats" means, precisely

Two horizons are reported because only one of them is honest about timing:

- **`D→D+k`** — from the close of the signal day. Not achievable: the pulse is
  computed at 23:00 UTC, *after* that close.
- **`D+1→D+k`** — from the next session's close. **This is the number the rule
  uses.** It is the first price a reader of the strip could actually get.

`excess` is the sector's equal-weight return minus the median return of every
eligible name in the market over the same window.

### Known limits of this test, stated up front

- **~251 trading days.** One regime, one year. A pass is evidence the measure is
  not noise; it is not evidence it will hold.
- **Overlapping windows.** Consecutive sector-days share most of their forward
  window, so the effective sample is far smaller than the event count. Treat
  the event count as an upper bound on independence, not a sample size.
- **Ten sectors move together.** Excess-vs-market removes most of the common
  factor, not all of it.
- **The sweep is diagnostic only.** Twenty-seven threshold combinations will
  produce a best one by chance. The rule is judged on the pre-registered values;
  the sweep exists to show whether the result is robust or knife-edge.
- **Survivorship.** `ticker_master` is today's listing. Names delisted during
  the year are absent, which mildly flatters any measure computed from it.

## Result — run 2026-09-05 over 2025-09-04 .. 2026-09-03

252 trading days, 2,300 sector-days scored.

```
state counts:  accumulating 232   distributing 495   event 39   quiet 1534
```

### The rule, applied

Forward 5 sessions **from the next close** — the only horizon a reader of the
strip could actually trade:

```
state              n    median     mean   positive     p25     p75
accumulating     230    +0.37%   +0.24%      57%     -1.1%   +2.3%
distributing     468    +0.15%   +0.24%      53%     -1.2%   +1.3%
event             37    -0.25%   -0.48%      46%     -1.4%   +0.5%
quiet           1505    +0.12%   +0.14%      53%     -1.1%   +1.2%
ALL             2240    +0.14%   +0.16%      53%     -1.1%   +1.3%

edge = accumulating - quiet = +0.25 pp,  n = 230 >= 100
```

**VERDICT: RULE MET.** The strip may rank sectors.

### What survived, and what did not

**The sweep is a plateau, not a cliff.** All 27 threshold combinations produce
a positive edge, moving smoothly from +0.04 pp to +0.52 pp. The registered
point sits mid-plateau. Per the rule, **no threshold is changed** — the values
are not arbitrary, so there is nothing to fix, and choosing the strongest cell
after seeing the table is exactly the move the rule exists to forbid.

**Breadth is the load-bearing input, not U/D.** Holding U/D at 1.3, the edge
goes 0.09 → 0.25 → 0.45 pp as breadth moves 0.15 → 0.20 → 0.25. Holding
breadth at 0.20, moving U/D 1.2 → 1.5 changes it 0.25 → 0.40. Participation
carries more information than direction of volume. Worth a future pre-registered
test; not a licence to retune now.

**`event` is the strongest and most reliable finding.** It is negative at every
horizon (−0.25% to −0.68%) and positive only 38–46% of the time, against 53%
for everything else. Flagging a single-name spike as *not a sector move* is
therefore the part of this feature best supported by evidence — and it is a
warning, which is the safest kind of claim to make.

### What the numbers do NOT support

**The edge is small relative to the spread.** +0.25 pp of median edge sits
inside an interquartile range 3.4 pp wide. It is a tilt across many sector-days,
not a prediction about any one of them. **The strip must not imply a return.**

**`distributing` beats `accumulating` on the unachievable horizons** (+0.32% vs
+0.26% at 5 sessions from the signal close; +0.18% vs +0.13% at 3). The ordering
only inverts on the horizon that can actually be traded. The charitable reading
is a next-day bounce after selling that a reader cannot capture; the uncharitable
one is that the states partly measure activity rather than direction. Either way
it is a reason to describe states rather than rank aggressively.

**230 events are not 230 independent observations.** Consecutive sector-days
share most of their forward window, and an `accumulating` run is usually one
episode spanning several days. The effective sample is plausibly a few dozen
episodes. No significance test is reported because any honest one would be
dominated by that dependence.

### What the strip is therefore allowed to say

- Rank sectors, and label the state. **Permitted** — the rule was met.
- Say *"money entering / leaving"*, show breadth, direction, and the names
  driving it. **Permitted** — these are descriptions of what happened.
- Flag `event` as a single name rather than a sector. **Encouraged** — best
  supported claim here.
- Imply, in any wording, that a sector will go up, or that a state is a
  forecast. **Not permitted.** The measured tilt is +0.25 pp of median across
  hundreds of overlapping days and does not survive being read as a per-sector
  prediction.

Re-run after another quarter of bars. A single year in one regime is evidence
this is not noise; it is not evidence it will hold.
