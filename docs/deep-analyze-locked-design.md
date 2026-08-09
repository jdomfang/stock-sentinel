# Deep Analyze — locked design decisions

Decisions taken across four expert-panel review rounds and a measured
experiment (298 X posts, retained and replayed offline many times at no
further cost). Locked means settled: implement to this, do not re-litigate
without new evidence.

Every claim marked **measured** was verified on our own corpora. Claims marked
**provisional** are reasoned but uncalibrated, and are recorded so that when
they turn out wrong we know what to look at.

## Scope note

**Discovery and Deep Analyze are independent in data, connected in
navigation.**

Deep Analyze never consumes a scan's corpus. Given a ticker it runs its own
retrieval and fetches whatever evidence it needs. There is no data dependency
in either direction.

There are two entry paths, and they differ in what is known beforehand:

| path | what we know before the credit is spent |
|---|---|
| user types a ticker | nothing — unscreened across ~7,000 symbols |
| **Deep Analyze** button on a scan row ([Discovery.py:1921](../pages/Discovery.py#L1921)) | Discovery's own cashtag mention count for that ticker |

So a readiness signal is possible on the second path only. Discovery already
records per-ticker provenance — the last materials scan showed its top ticker
at 7 cashtag mentions across the entire sector — which is exactly the number
that predicts whether Deep Analyze can adjudicate. Nothing equivalent exists
for free-text entry.

## The evidence base

Three single-page arms, 100 max_results each, same 48h window, same minute.

| | posts | passes ticker filter | usable yield | outcome |
|---|---|---|---|---|
| A TSLA, production query | 98 | 89% | 12% | genuine discussion |
| B TSLA, `$TSLA` alone | 100 | 100% | 0% | one spam campaign, 100 accounts |
| C MP, production query | 100 | 83% | 3% | 69% Members of Parliament |

Arms A and B shared **zero** posts out of 198. All three originally produced
the identical verdict: Watch / Moderate.

## Retrieval

- **Remove the bare-symbol arm `OR TICKER`.** *Measured:* zero usable posts in
  the bare-only group of both corpora, consuming 37% of Arm A's spend and 82%
  of Arm C's.
- **Add company aliases** from `ticker_master.name`. *Measured:* 10 TSLA and 4
  MP posts matched the company name with no cashtag present.
- **Keep the finance OR-list alongside the alias.** Our retained corpus only
  samples posts that already passed that filter, so a query without it is
  untested — not rejected, untested.
- **Subject rule is "target among at most 3 cashtags"**, not sole cashtag.
  *Measured:* 43 qualifying posts instead of 27, and mean signal rises from
  +0.072 to +0.095.
- **The newswire corpus stays, as a separate channel.** The account list
  (Reuters, Bloomberg, FirstSquawk, WSJmarkets, unusual_whales, spotgamma…)
  is a newswire/flow feed, not influencers. It feeds the catalyst and risk
  modules and **never the directional sentiment average**. Its real cost has
  never been measured; per-post billing means a `from:`-list query that
  matches nothing is free.

## Evidence representation

- **An evidence ledger**, not eight keyword buckets: one row per post carrying
  channel, match type, subject status, evidence types, full FinBERT
  probabilities, cluster id, spam risk, and a computed `evidence_eligible`.
- **`target_subject_status` must be computed, not labelled**, in production —
  deterministic rules first, refined with text cues.
- **Neutral risk/catalyst posts are evidence.** They route to the risk and
  catalyst modules and must never enter the directional mean. *Measured:*
  averaging them in moved Arm A from +0.244 to +0.175, flipping Buy to Watch.

## Quality gating

```
aboutness = f(target precision, eligible yield, subject match, bare-only penalty)
integrity = 1 - largest_cluster_share
coverage  = evidence types populated

quality   = aboutness * (0.75 * integrity + 0.25 * coverage)
```

*Measured* on the three arms: A 0.409, B 0.000, C 0.062 — A−B +0.409,
A−C +0.348, both clearing the 0.25 separation bar. An earlier additive form
failed at A−C +0.122 because low spam-share and evidence diversity rescued
organic wrong-entity content. **Multiplicative gating is the point:**
wrong-entity must fail aboutness before hygiene can save it.

Tiers, **provisional** and rescaled to the observed distribution:

```
< 0.10   reject / Low only
< 0.30   usable, Low
< 0.55   Moderate eligible
>= 0.55  High eligible — but High is NOT displayed until labels and
         outcome data exist
```

Set from a single genuine corpus. Treat as a gate that separates known
controls, not as calibrated truth.

## Scoring and direction

- **Aggregate on margin (`p_positive − p_negative`), not the zeroed score.**
  *Measured:* 78–100% of posts score exactly 0.0 under the 0.55 argmax cut,
  which collapses any average toward zero.
- **`social_direction` is shrunk by evidence and quality**, not a raw corpus
  mean:
  ```
  evidence_weight = min(1, sqrt(eligible_cluster_count / 10))
  quality_weight  = clamp(quality / 0.40, 0, 1)
  social_direction = raw * evidence_weight * quality_weight
  ```
  **Open:** whether `raw` averages all eligible posts or only directionally
  eligible ones. *Measured* difference on Arm A: **+0.037 vs +0.244**, which
  straddles the ±0.15 decision gate.

## Projection

- **`base_return = 0`.** No momentum and no reversal claim until 252 trading
  days are stored. The prior `0.35 × recent_20d_return` was an unjustified
  continuation assumption at a horizon where reversal is the better-documented
  effect.
- **Social tilt is additive, not multiplicative**, and gated on quality.
  *Measured:* the old multiplicative form made maximum bullish sentiment move
  a falling stock's forecast from −20.6% to **−25.4%**, and halved its chance
  of reaching +5%.
- **Present a scenario range, explicitly provisional. Never a price target.**
- **Replace "suggested hold: N days" with a review window**, and always
  display `success_rate`. *Measured:* on live TSLA the interface would show
  "hold 6 days" attached to a −21% forecast, with 25% success hidden.

## Confidence

Not post volume. Quality, independent evidence clusters, cross-channel
agreement. **High is unavailable until labels and outcomes exist.**

## Measurement — both shipped in `ab73a2b`

- **`verdict_log`** — every verdict with its inputs and `price_at_verdict`.
  Nothing was ever recorded before; X's index is 7 days deep and cannot be
  backfilled, so an unlogged verdict can never be scored.
- **`price_history`** — daily close and volume by trade date, written by the
  existing grouped-daily sync. 90 trading days minimum, 252 preferred, and the
  clock starts with the table.
- **The labelling rubric** as written, including its 85/90/75 self-consistency
  thresholds.

## Explicitly not adopted

| | why |
|---|---|
| splitting retrieval into base/risk/catalyst/trading queries | *measured* ~37% billing surcharge; those queries are subsets of the base query and overlapping posts bill twice. Payoff untested |
| a 50-post cap on social spend | confounds the proof-of-value test it is meant to enable |
| alias query without the finance OR-list | untestable offline — our corpus is pre-filtered |
| cutting the newswire corpus | reversed; see above |
| — | — |

## Reopened

**"Deep Analyze readiness" on scan rows.** Briefly moved here on the mistaken
belief that no path connects the two features. A per-row Deep Analyze button
exists, Discovery already holds the mention counts, and the signal costs
nothing — so it applies to that path. It does nothing for free-text entry,
which is the harder half.

## Open, blocking

1. `social_direction` — all eligible posts, or directionally eligible only?
2. `risk_high = ≥2 items` fires on substring matches; "great risk/reward"
   counts as a risk item. *Measured:* 3 items in Arm A produced **Avoid**
   before direction was consulted. Risks replacing "always Watch" with
   "always Avoid".
3. With `price_volume` neutral by default, nothing stops a Buy on a stock that
   just fell 21.7%.
4. Thin tickers, now that input is unscreened across ~7,000 symbols.

## The standing pre-ship checklist

The panel's own risk list, led by the one they raised first: **the quality
score separates the three corpora we hold and may simply have overfit them.**
