# Mobile display review

These captures use the real Home and Discovery pages with offline fixture data.
No live scans, checkout, credit ledger updates, or live market data were used.
Browser fixtures block external HTTP traffic and replace paid service calls.
They are not current market observations.

## Changes

- Mobile scan results use a two-column metric grid; company and action span its width.
  Streamlit's negative Markdown margin is reset only inside mobile result rows.
- Home says **Explore Healthcare** for navigation. Discovery displays **Healthcare
  selected** and **Run Healthcare scan · 1 credit** using the existing callback.
  Selecting a sector does not start a paid scan.
- The authenticated **Open Market Scan** link has a clear mobile action surface.
- Mobile Participation values have a label; admin navigation shares one row;
  footer links use two columns; long Deep Analyze links wrap within the viewport.
- Existing scan progress, completion navigation, credits, refunds, authentication,
  and Sector Pulse calculations remain unchanged.

## Evidence

- `mobile-scan.mp4` (also available as `mobile-scan.webm`): offline selected-sector → progress → automatic results walkthrough.

- `chromium-home-390-true.png`: authenticated Home, including admin navigation.
- `chromium-results-390-low.png`: mobile result view with low-evidence companies.
- `chromium-card-390-low.png`: isolated result card.
- `mobile-before-390.png` and `mobile-after-390.png`: same offline company
  rendered with baseline and updated code, visibly reproducing and resolving overlap.
- `desktop-before-1440.png`, `desktop-after-1440.png`, `desktop-comparison.json`:
  real-page baseline comparison. All measured result-row and cell dimensions and
  relative positions are identical at 1440px.

## Automated checks

- `tests/run_all.py --no-db`: 1,831 passed, zero failed across 41 suites.
  Database suites were explicitly skipped; no database schema or ledger logic changed.
- Existing `tests/browser/scan_feedback.cjs`: success, failure, empty, refused,
  displaced-navigation and disabled-button scenarios passed.
- Chromium and WebKit passed all seven widths.
- Saved Deep Analyze results passed Chromium and WebKit at 390 and 1440px with no debit.
  The selected result keeps its View result action without offering a second paid analysis.
- `tests/browser/mobile_layout.cjs`: checks actual rendered geometry, expected
  OGN/HALO cards, long names, enlarged text, signed-in/admin and signed-out Home,
  actual Home-to-Discovery handoff, no debit on navigation, exactly one mocked
  debit and remote invocation per scan, progress shielding, and result focus.
  Widths: 320, 375, 390, 430, 720, 1024 and 1440px.

Run browser checks with a local Playwright installation and the repository's Python
requirements, for example:

```sh
NODE_PATH=/path/to/node_modules BROWSERS=chromium,webkit PYTHON=python3 node tests/browser/mobile_layout.cjs
```

Optional `WIDTHS`, `PORT`, `ARTIFACTS`, `DELIVERED=1`, `ONLY_DELIVERED=1`,
and `RECORD=1` settings support targeted
reviews. Recording applies to the 390px low-evidence scan. All browser traffic
except loopback is blocked; Python service calls use the offline harness.

## Review limits

WebKit emulation is not a physical iPhone Safari test. The original recording was
examined through a full one-frame-per-second timeline and denser native frames
around navigation; touch events cannot be inferred reliably from pixels alone.
The two-stage Home/Discovery flow was reproduced, rather than claiming that every
apparent repeated touch in the recording represents a failed event.

No production deployment is part of this review. Existing unrelated working-tree
changes were preserved.
