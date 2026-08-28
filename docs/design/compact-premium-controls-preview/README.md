# Compact premium controls preview

These review mockups show the proposed density pass before any production UI is changed.

## Files

- `market-scan-compact-desktop-v2.png` — final denser desktop proposal with a narrow toolbar, quiet Add credits action, and compact status strip
- `deep-analyze-compact-desktop.png` — matching denser Deep Analyze desktop proposal before a ticker is analyzed
- `market-scan-compact-desktop.png` — desktop Market Scan empty state
- `market-scan-compact-mobile-v2.png` — final responsive mobile Market Scan empty state
- `market-scan-compact-mobile.png` — first mobile draft, retained for comparison

## Product direction represented

- Preserve the current dark premium design language and signed-in navigation.
- Convert the oversized scan area into a content-driven action toolbar.
- Keep **Run scan · 1 credit** as the only solid cyan action.
- Present the credit balance as quiet utility text and **Buy 2 credits · $5** as a secondary outlined action.
- Replace the large instructional box with a compact two-line empty state.
- Keep controls at 44px on desktop and 48px on mobile for accessibility.
- Preserve all scan, credit, authentication, payment, and analysis behavior.

## Mockup prompt summary

Use case: `ui-mockup`. Generate high-fidelity desktop and mobile Stock Sentinel Market Scan screens using the current dark navy/cyan visual system. Retain the existing signed-in destinations, use precisely aligned compact controls, separate the visible one-credit cost from the current balance and purchase action, and replace the large tutorial region with a short empty state. Avoid orange, extra navigation icons, decorative imagery, oversized controls, and excessive blank space.

For Deep Analyze, use the same desktop density and visual system with a readable ticker input, one solid **Analyze · 1 credit** action, quiet balance and **Add credits** utilities, and a compact text-only pre-analysis status strip. Do not show a recommendation until the user runs the analysis.

These images are visual decision aids, not screenshots of implemented code. Exact browser font rendering and spacing may vary slightly in the production implementation.
