# Premium admin workspace preview

High-fidelity review concepts for reorganizing the existing Admin page into a
compact operations workspace. These files are design artifacts only; no Admin
page behavior has been changed.

## Approval views

- `01-admin-inbox-desktop.png` shows the proposed default Inbox state with
  existing message triage organized as a master-detail workspace.
- `02-admin-users-desktop.png` shows the proposed Users state with the existing
  user directory and account editor combined into one master-detail workspace.

## Panel consensus

- Retain the global `Market Scan | Deep Analyze | Account | Admin` navigation
  and give Admin a real active state.
- Add local `Inbox | Users | Demo content` navigation. Do not add an Overview
  tab or dashboard charts.
- Preserve all current message, profile, credit-ledger, demo snapshot,
  authorization, database, and API behavior.
- Replace the long stacked page with focused views and progressive disclosure.
- Keep untrusted message bodies as plain text. Move UUID and browser metadata
  into a collapsed technical-details disclosure.
- Replace the bright framework dataframe with a dark semantic user table and
  keep credit/access changes visibly associated with their audit reason.
- Keep controls at least 44px high, use explicit text status in addition to
  color, preserve the cyan focus ring, and avoid nested scrolling regions.
- Put portable component rules in `stock-sentinel-components.css`; keep only
  Streamlit DOM mappings in the adapter stylesheet.

## Visual direction

The concepts reuse the current premium navy, slate, white, muted blue-gray, and
cyan system. Cyan is reserved for active state, focus, counts, and primary
actions. Emoji, raw technical metadata, white dataframes, decorative charts,
and invented operational metrics are intentionally excluded.

Generated with the built-in image-generation tool using the current Admin
screenshots as visual references.
