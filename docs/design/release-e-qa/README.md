# Release E visual QA

Release E was exercised against a local build with the host theme deliberately
set to coral (`#FF4B4B`). Product controls and links must continue to resolve
through Stock Sentinel's portable tokens rather than that host default.

## Viewports

- 390 × 844 — mobile
- 768 × 1024 — tablet
- 1024 × 768 — compact desktop
- 1440 × 900 — desktop

At every viewport, computed-style checks covered Home, Auth, and Contact. The
checks found no horizontal document overflow, no host-coral leakage, no tested
interactive target below 44 CSS pixels, and the expected tokenized cyan primary
gradient. Desktop and mobile route QA also activated all 64 safe public/internal
links and navigation actions without triggering a scan, analysis, or checkout.

## Captures

- `home-mobile.png`
- `home-tablet.png`
- `home-compact-desktop.png`
- `home-desktop.png`
- `auth-mobile.png`
- `auth-desktop.png`

The captures are evidence for the current adapter, not a Streamlit-specific
product specification. The durable product contract lives in the portable token
and component styles; a future host replaces only the renderer adapter.
