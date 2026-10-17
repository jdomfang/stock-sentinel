# Deep Analyze single-tap review

The standalone ticker input could send a blur rerun immediately before the
Analyze button event, swallowing the first tap. The controls now use a
borderless Streamlit form, so the edited ticker and explicit submission arrive
together. Return does not submit the paid action.

A centered progress surface appears before `consume_credit` begins, uses the
existing processing states, intercepts repeat pointer taps, and clears when
work exits. The existing charged-work `finally` block is unchanged. No ledger,
refund policy, remote API, or Sector Pulse computation was changed.

## Verification

- `tests/test_deep_submit.py`: 12 offline AppTest cases, including successful
  work, refused debit, unavailable service, failures, refunds, interruption,
  zero credits, prefilled ticker, and existing autorun behavior.
- `tests/run_all.py --no-db`: 1,845 passed, zero failed across 42 suites.
  Database-dependent suites were skipped.
- `tests/browser/deep_submit.cjs`: real-page browser checks with all credit and
  analysis operations mocked and external network blocked. Fresh ticker edit
  immediately followed by one tap; progress verified while debit is held;
  completion and failure cleanup checked. Chromium and WebKit each passed all 14 scenarios,
  including 390, 820, 1024 and 1440 pixel widths.
- The same browser regression against the pre-fix page fails with
  `Missing debit event`, demonstrating that it detects the original defect.
- Two independent code reviewers found no blocking regression.

No real credits or paid analysis requests were used. Browser viewport and touch
emulation do not replace a final check on physical iPad Safari. Changes are
local pending review; this report does not imply deployment.

## Screenshots

- [iPad-width progress](ipad-width-progress.png)
- [iPad-width result](ipad-width-result.png)
- [WebKit iPad-width progress](webkit-ipad-width-progress.png)
- [Desktop controls](desktop-controls.png)

The browser script accepts `PLAYWRIGHT_MODULE`, `PYTHON`, `BROWSER`,
`BROWSER_EXECUTABLE`, `PORT`, and `ARTIFACTS` environment variables for local
test runtimes. Run it with `node tests/browser/deep_submit.cjs`.
