# Sector Pulse UI implementation

The approved Home composition is integrated into the Streamlit application.
The existing Source Sans Pro typography, shared colors, navigation, controls,
and responsive shell remain the site's design system.

## Six phases

1. **Contract and scope:** preserve the reader and all computation, thresholds,
   nightly jobs, database migrations, credit execution, and refund behavior.
   The authorized UI extensions cover shared CSS, navigation, Auth handoff,
   Deep Analyze prefill, and regression tests.
2. **Shared Pulse presentation:** Home and Market Scan use the same component.
   Current date, participation, comparison date, evidence, calendar context,
   and event contributor are formatted from returned observations.
3. **Home composition:** retain the approved headline and signup CTA. Place
   current Sector Pulse above the clearly separate saved product example.
   Preserve the published demo's actual scan and analysis values. Move FAQ
   and How it works to the footer alongside Contact and Trust Center.
4. **Research actions:** public actions select a sector/ticker through Auth
   without automatic execution. Authenticated scan buttons use the existing
   paid path. Event rows retain the sector heading and offer the returned
   contributor in Deep Analyze. Manual scanning remains available if Pulse
   is unavailable or incomplete; complete observations replace the duplicate
   sector selector. Standalone Deep Analyze remains in navigation.
5. **Verification and preview:** offline behavioral tests, the complete test
   suite against disposable Postgres, and desktop/mobile browser checks.
   Push develop and verify its CI and Streamlit preview before release.
6. **Production release:** create develop → master PR, require green CI on
   its current head, merge through GitHub, then verify Railway's public UI.
   Never push the changes directly to master.

## How nightly wording changes

`load_sector_pulse()` calls only `sector_pulse.latest(days=6)` and caches the
result for five minutes. A visit or Streamlit rerun after the cache expires
reads the latest published date. An idle open tab does not stream updates;
refreshing the page loads observations subject to that same short cache.
The displayed market date always comes from the data, never the browser clock.

The sentences are deterministic templates; the ticker and numbers are data.
For example, an `event` row whose leading contributor is MRNA with
`share_of_rise=0.5925` produces “MRNA drove 59% of the positive volume
increase.” If a later row names CYTK with a share of 0.71, the same renderer
produces “CYTK drove 71% of the positive volume increase.” Its action also
opens CYTK. Neither ticker is hard-coded in application presentation code.

The reader's `state` selects the wording; the UI never computes a replacement
state. Ordering follows the contract: accumulating, event, quiet, distributing,
then participation descending. A day with no accumulation says so. A partial
date is identified and never filled with older sector observations. Missing
ratios and participation remain unavailable rather than becoming zero.

## Execution and identity boundaries

A public research choice is short-lived, validated, and separate from private
results. It can survive anonymous signup but is rejected if it belongs to a
different authenticated account. The destination consumes it as a prefill.
No Pulse navigation requests automatic paid work.

A signed-in scan callback captures the clicked sector and account before the
page reruns. The page consumes that request once and passes the sector to the
existing execution block. Refreshing or reordering observations cannot change
which sector was clicked. Account changes clear the pending paid request.

## Verification record

- Complete local suite: 2,204 counted assertions passed across 50 suites,
  including the database migration/credit/refund suites against disposable
  Postgres. The runner also passed its standalone golden-file suite.
- Sector Pulse suite: 15 behavioral tests cover ordering, changing nightly
  contributor text/actions, current/partial dates, nulls, calendar context,
  zero-credit actions, account isolation, one-time scan requests, Auth routing,
  and Deep Analyze prefill replacing a previous ticker without autorun.
- Actual local Home: real reader returned all ten sectors through September 3,
  2026. No accumulating sectors were present; the UI reported that accurately.
- The durable saved demo continued to show PLTR, Neutral, four social posts,
  Watch, Moderate confidence, seven evidence clusters, ten trading days, and
  25 daily closes. The full-example disclosure preserves the remaining fields.
- Browser checks: desktop and 390px mobile Home; anonymous sector-to-signup
  navigation; signed-in Market Scan and repeated event-to-Deep Analyze handoff
  using an isolated offline account harness. No scan or analysis was purchased.
- No changes to the pulse engine, backtest, sync, migrations, payment service,
  credit/refund pipeline, or deployment infrastructure.

Develop and production deployment status must be verified separately; local
verification alone does not constitute a production release.
