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

- **`social_direction` averages DIRECTIONALLY eligible posts only** — those
  carrying `directional_view` with `|margin| >= 0.15`. Neutral risk/catalyst
  posts never enter it; they route to their own modules.
  ```
  evidence_weight = min(1, sqrt(directional_cluster_count / 5))
  quality_weight  = min(1, quality / 0.40)
  social_direction = raw * evidence_weight * quality_weight
  ```

### The alias finding

*Measured, and unanticipated.* Including company-alias matches — a decision we
locked purely on **recall** grounds — materially moves **direction**:

```
12 cashtag-matched directional posts    mean margin  +0.244
 4 alias-matched  directional posts     mean margin  -0.528  (derived)
16 combined                             mean margin  +0.051  -> neutral
```

Prose coverage of a stock that had just fallen 21.7% reads far more bearish
than trader cashtag chatter about the same stock in the same window. Read as
the alias supplying true signal that cashtag-only retrieval was missing — but
it means a recall decision silently became a direction decision, and alias vs
cashtag may warrant separate treatment.

## Risk

Semantic, not lexical. Substring matching alone produced 3 "risk items" in
Arm A, **all three false positives**, which was enough to force Avoid before
direction was ever consulted.

```
risk_candidate  = text contains a risk-lexicon term
risk_confirmed  = severe term (dilution, offering, bankruptcy, delisting,
                  lawsuit, investigation, fraud, SEC, recall, downgrade,
                  guidance cut)
                  OR negative construction (concern, warning, red flag,
                  downside, pressure, miss, weak, cut, lowers, probe,
                  threatens, rejected, denied)
                  AND NOT negated (no concern, no risk, risk/reward,
                  worth the risk, de-risked, risk is priced in)

risk_high = severe_clusters >= 1
            OR (soft_clusters >= 3 AND soft_rate >= 20%)
```

*Measured:* under this rule Arm A's three hits resolve to one negation
(`"would reset risk/reward nicely"`) and two candidates with no confirming
term. `risk_high` becomes False, and the verdict moves Avoid → Watch.

## Price

No stored history exists, but **the projection already makes a live 30-day
price call** — that data is present at verdict time and is currently consumed
only afterwards. Provisional veto until history accumulates:

```
Buy is blocked to Watch if
    20d_return <= -15%  AND  volume_ratio >= 1.5  AND  no strong catalyst
```

If that live call is ever unavailable, price stays neutral and nothing
price-based prevents a Buy. The panel's position on shipping Buy under that
constraint: *"I would be uncomfortable."*

## Cross-feature evidence

`discovery_seed` — a distinct channel, never merged silently into the ticker
corpus:

- only from a recent scan; only posts carrying the target cashtag; ≤3
  cashtags; same eligibility rules; deduped by post id and text hash
- **may lift confidence Low → Moderate. May never alone produce Buy or Avoid**
  — a basket query matching any of ~55 cashtags over-selects multi-ticker list
  posts, so the channel is structurally biased against subject status
- provenance disclosed in the UI, because the same ticker would otherwise be
  adjudicated from different evidence depending on entry path

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

## What each verdict displays

Every verdict shares one spine; the difference is what sits on top.

```
ticker · price · verdict · confidence
EVIDENCE CHECK          which pillars passed, which failed, with values
MOVEMENT PROFILE        how far it travels — BOTH directions, always
PROVENANCE              posts bought, usable, discarded and why
WHAT WOULD CHANGE THIS  the specific unmet condition
```

**BUY** — names the catalyst and its source; shows both sentiment channels
agreeing; the **only** verdict whose scenario band is tilted by evidence;
carries an explicit invalidation condition and a review window.

**AVOID** — names the disqualifying item in full, with source and date, and
its severity. **No scenario band**: a tilted band under an Avoid reads as a
short-side price target the system has no basis for. States what would remove
the Avoid.

**WATCH / Moderate** — real evidence that does not line up. Shows every pillar
that passed alongside the ones that failed, and names exactly what stands
between it and a Buy.

**WATCH / Low** — a different product from the above, and must not look the
same. "Not neutral conviction — insufficient clean evidence about this
company." Leads with provenance: how many posts were bought, how many were
discarded, and why. Showing these two states identically is how the old system
gave a spam corpus Moderate confidence.

### The downside column is non-negotiable

Volatility is symmetric. `+5% reached in 69% of paths` without `-5% reached in
69% of paths` will be read as a 69% win rate. It is a statement about how far
the stock travels — a sizing input, not an edge. Surfacing the upside alone
recreates the false-confidence problem the rebuild exists to remove.

### Time-to-target

Computed from realised volatility with no directional claim, and currently
discarded: the old code calculated `success_rate` on every run and never
displayed it, showing "hold 6 days" while hiding that 25% of paths reached the
target. Measured on TSLA: +5% touched in 69% of normal paths, median day 5.

## Where the fully-corrected cascade lands today

```
A genuine TSLA   quality 0.409 ≥ 0.30 · risk FALSE · social +0.051 neutral
                 · catalyst present                        -> Watch / Moderate
B spam           quality 0.000                             -> Watch / Low
C wrong-entity   quality 0.062                             -> Watch / Low
```

Separation is real but sits on **confidence, not verdict**. An earlier
three-way split (A→Avoid) came from the lexical risk rule and did not survive
its own repair.

## Open

1. **`target_subject_status` production algorithm.** Rules given for
   wrong-entity, comparison and mentioned_only; "primary" still leans on text
   cues (`"$TSLA breaks out"`) with no concrete implementation.
2. **Catalyst severity is undefined.** Risk got a two-stage semantic rule with
   a negation list; catalyst remains "≥1 eligible item" on substring matching
   — the same false-positive shape that made risk fire on `risk/reward`.
3. **Calibration is unpriced and unaffordable as specified.** 30 corpora × ~100
   posts ≈ **3,000 X posts**, roughly 2.4× total spend to date (1,260).
4. **Newswire channel weighting**, and its actual cost — still never measured.
5. **Thin tickers on the typed-ticker path**, where nothing is known before the
   credit is spent.
6. **Confidence combination.** Tiers are defined; the mapping from module
   outputs to a tier is not.
7. **Behaviour when the live price call fails**, now that a price veto depends
   on it.

## The standing pre-ship checklist

The panel's own risk list, led by the one they raised first: **the quality
score separates the three corpora we hold and may simply have overfit them.**
