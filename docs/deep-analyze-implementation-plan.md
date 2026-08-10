# Deep Analyze — implementation plan

The design is locked in [deep-analyze-locked-design.md](deep-analyze-locked-design.md).
This is the build order.

**Current state: three measurement tables shipped, nothing else. The running
product is still the pre-review architecture.**

## The asset that makes this safe

298 real posts are retained on disk across three corpora with known character —
genuine TSLA discussion, a 100-account spam campaign, and Indian political news
matching `$MP`. **Every phase below is replayed against all three before it
ships.** A phase that cannot separate them is not finished, whatever the tests
say.

That replay costs nothing and takes seconds. It is the only reason this can be
built without spending X credits to find out it is wrong.

---

## Phase 1 — Ship-now fixes

No new structure. Every one is justified by an existing measurement, and each
is independently shippable.

| # | change | file | measured effect |
|---|---|---|---|
| 1.1 | drop `OR TICKER`, add company alias from `ticker_master.name` | `utils/deep_analysis.py` | frees 37–82% of retrieval spend, 0 usable posts lost |
| 1.2 | `red_flag_rate` from angle membership, not first-contributed ids | `utils/deep_analysis.py` | the Avoid-on-risk gate is currently dead code |
| 1.3 | empty angles are missing, not neutral | `utils/deep_analysis.py` | today emptiness reads as consensus and *raises* confidence |
| 1.4 | `avg_sentiment` post-level over unique posts | `utils/deep_analysis.py` | removes the angle-ordering dependence |
| 1.5 | sentiment tilt additive, `base_return = 0` | `utils/projections.py` | today max bullish takes a falling stock from −20.6% to −25.4% |
| 1.6 | surface `success_rate` + time-to-target table | `pages/Deep_Analysis.py` | computed on every run since forever, never displayed |
| 1.7 | replace "hold N days" with a review window | `pages/Deep_Analysis.py` | "hold 6 days" currently sits on a −21% forecast |

**Verify:** replay all three corpora; 1.5–1.7 checked against live TSLA.
**Ships:** immediately, one at a time.

---

## Phase 2 — The evidence ledger  ← critical path

Replaces Stages 2 and 3. Eight keyword buckets become one table of typed post
rows. **Everything after this depends on it.**

New: `utils/evidence.py`

```
build_ledger(posts, ticker, aliases, channel) -> list[EvidenceRow]
```

Per row: `post_id · channel · text · author_id · created_at · engagement ·
cashtags · cashtag_count · target_match_type · subject_score ·
target_subject_status · evidence_types · p_positive/p_negative/p_neutral ·
margin · cluster_id · cluster_share · spam_risk · evidence_eligible`

Three sub-components, each independently testable:

- **`subject_score`** — the additive point system. Measured at 55% eligible on
  the genuine corpus versus 9% on wrong-entity. Includes the per-symbol
  wrong-entity deny list for short ambiguous tickers (MP, D, ON, IT).
- **`cluster_id`** — greedy near-duplicate clustering, Jaccard ≥ 0.75 on
  normalised token sets. Collapses the 100-account spam campaign to 1.
- **`evidence_eligible`** — the deterministic gate. Never a human label.

**Verify:** the ledger for each corpus matches the measurements already
recorded in the design doc. Any drift is a regression.

---

## Phase 3 — The modules

Pure functions over the ledger. Independent of one another; build in any order,
or in parallel.

| module | key detail |
|---|---|
| `quality` | `aboutness × (0.75·integrity + 0.25·coverage)` — multiplicative so wrong-entity fails before hygiene rescues it |
| `social_direction` | **two channels**, cashtag and alias, each shrunk by clusters and quality; conflict gate needs ≥3 clusters *per side* |
| `risk` | two-stage semantic, severe/soft, explicit negation list — kills `"risk/reward"` |
| `catalyst` | keyword **plus** a specificity signal; generic business words alone are soft only |
| `price` | veto from the live 30-day call; **fails closed for Buy** |
| `newswire` | separate channel; never touches social direction; one hard item can trigger risk or satisfy catalyst |

**Verify:** each module's output on all three corpora matches the recorded
figures — quality 0.409 / 0.000 / 0.062; catalyst hard 6 vs 2; conflict True
vs False.

---

## Phase 4 — Cascade and confidence

- verdict cascade over module outputs
- confidence tiers; **High is unreachable** until labels and outcomes exist
- **the pillar readout** — which gates passed, which failed, with values, and
  what would flip the verdict. This is generated from cascade state, not
  written prose, so it can never contradict the numbers the way the current
  rationale does

**Verify:** three corpora produce their recorded verdicts. Add a regression
test asserting that spam and wrong-entity can never reach Moderate.

---

## Phase 5 — The interface

The four displays from the locked design: Buy, Avoid, Watch/Moderate,
Watch/Low.

Non-negotiables, both from measured failures:

- **the downside column always accompanies the upside** — volatility is
  symmetric, and `+5% in 69% of paths` alone reads as a win rate
- **Watch/Low must not look like Watch/Moderate** — one is a finding, the
  other is "we could not judge"; showing them identically is how spam earned
  Moderate

---

## Phase 6 — Cross-feature

`discovery_seed`: reuse a recent scan's posts about the target as a distinct,
lower-weight channel. May lift confidence Low → Moderate; may never alone
produce Buy or Avoid. Provenance disclosed in the UI.

Optional — the product works without it.

---

## Phase 7 — Calibration  *(blocked on data and your time)*

1. **Labelling harness** — ~170 posts after cluster-dedup, against the locked
   rubric, resumable, with a re-label sample to measure self-consistency
   (85/90/75 thresholds).
2. **Fit the thresholds** — every constant in Phases 3–4 is currently
   provisional. Labels turn `0.30`, `±0.15`, `≥3 clusters` and
   `subject_score ≥ 5` from guesses into measurements.
3. **The 200-post experiment** — 4 corpora × 50, one per ticker class.

**Blocked until:** labels exist. Nothing else waits on this.

---

## What is not on the critical path

Everything the panel raised in the final data round — market-relative move,
volume anomaly, earnings dates, split-revision handling. All of it needs
`price_history` to accumulate first, which happens on its own. **Revisit in
December, not now.**

## Dependency summary

```
Phase 1  ──────────────────────────────► ships independently, start here
Phase 2  ──► 3 ──► 4 ──► 5              the critical path
                          └──► 6        optional
Phase 7  ◄── your labelling time, then re-fits Phases 3–4
```

The three logs accumulate throughout and require no work.
