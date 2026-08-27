# Stock Sentinel Premium UI Product Roadmap

Status: Approved review backlog, recorded 2026-08-25.

This document is the durable project record of the fifteen improvements from the premium fintech UI review. It is intentionally limited to presentation, navigation, responsive behavior, accessibility, information architecture, and frontend structure.

## Locked constraints

- Do not rewrite the product as the first response to these issues.
- Do not change backend logic, authentication logic, payment behavior, credit behavior, database behavior, or API behavior.
- Preserve existing loading, failure, refund, checkout, payment-return, and account-state branches.
- Separate current Streamlit improvements from future frontend migration work.
- Prefer shared, structurally valid components over cross-block HTML, generated Streamlit class selectors, negative-margin compensation, or DOM mutation workarounds.

## Platform-neutral UI contract

Streamlit is the current rendering adapter, not the target product
architecture. Release work must keep the product model portable:

- Canonical tokens live in `assets/styles/stock-sentinel-tokens.css`. A future
  frontend imports that file directly; the current adapter maps it onto
  Streamlit widgets.
- Canonical destinations are Home, Market Scan, Deep Analyze, Account, and the
  admin-only route. Route names and active states must not depend on a
  Streamlit-generated sidebar.
- Market Scan rows have platform-neutral fields: ticker, company, last close,
  sentiment, attention/evidence, cost, and selected state.
- Sentiment is strictly Bullish, Bearish, or Neutral. Sparse evidence is a
  separate state. Buy, Watch, or Avoid belongs only to an analysis result.
- Paid actions expose cost before activation and become a nonpaying View result
  action after delivery. This rule is presentation behavior, not a change to
  the credit service.
- Desktop tables and mobile labelled cards are two responsive presentations of
  the same result model. No product information may exist only in a
  framework-specific DOM structure.
- Framework-specific CSS selectors are permitted only in the current adapter
  layer. Do not attach business rules, navigation state, payment state, or
  result taxonomy to those selectors.

Suggested future component mapping:

| Product component | Current adapter | Future frontend component |
|---|---|---|
| App shell | `utils/navigation.py` | `AppShell` + route-aware `PrimaryNav` |
| Design tokens | CSS asset loaded by `utils/ui.py` | Root CSS import/theme provider |
| Scan controls | keyed Discovery container | `MarketScanForm` |
| Scored result | keyed native row | `ScanResultRow` / `ScanResultCard` |
| Sparse evidence group | disclosure/expander | accessible `Disclosure` |
| Selected analysis | semantic in-page section | `AnalysisSummary` |
| Credit context | billing presentation helper | `CreditBalance` + `PurchaseReview` |

## Ranked improvements

| Rank | Improvement | Phase | Impact | Effort | Risk | Priority |
|---:|---|---|---|---|---|---|
| 1 | Rebuild stock rows and the recommendation panel as structurally valid, self-contained components | Current | High | Medium | Low | P0 |
| 2 | Replace negative-margin positioning with a stable app-shell/header spacing contract | Current | High | Medium | Medium | P0 |
| 3 | Redesign desktop results into dense 52–64 px rows and mobile results into labelled cards | Current | High | Medium | Medium | P0 |
| 4 | Simplify analysis hierarchy; add horizon, freshness, evidence size, and source context; remove unexplained bars | Current | High | Medium | Low | P0 |
| 5 | Restyle and restructure Auth for consistent fields, autofill, errors, brand navigation, and trust copy | Current | High | Medium | Low | P0 |
| 6 | Restore focus visibility, contrast, semantic headings, reduced motion, and accessible menu behavior | Current | High | Medium | Low | P0 |
| 7 | Render persistent Scan, Analyze, credits, and account navigation | Current | High | Medium | Medium | P1 |
| 8 | Resolve the disabled-demo contradiction and reduce the demo to four or five representative rows | Current | High | Low | Low | P1 |
| 9 | Put “1 credit” and remaining balance at every spend decision | Current | High | Low | Low | P1 |
| 10 | Present checkout as an explicit purchase-review state with total, quantity, expiration, and Stripe handoff | Current | High | Medium | Low | P1 |
| 11 | Standardize financial color semantics and labels without relying on color alone | Current | Medium | Low | Low | P1 |
| 12 | Consolidate tokens and shared components; remove duplicated CSS and DOM mutation workarounds | Current | Medium | Medium | Medium | P1 |
| 13 | Restructure the landing narrative and reduce hero/demo clutter | Current | Medium | Medium | Low | P2 |
| 14 | Add static Methodology, Data Sources, Privacy, and Terms surfaces to support credibility | Current | Medium | Medium | Low | P2 |
| 15 | Prepare an incremental route-level frontend migration using the same APIs and business behavior | Future | Medium | High | High | P3 |

Risk in this table means implementation/regression risk, not the risk of leaving the issue unresolved.

## Target product direction

- A restrained premium fintech aesthetic: midnight navy surfaces, cyan reserved for interaction, green/red/amber/slate reserved for financial meaning.
- Persistent application navigation with Home, Scan, Analyze, compact credit balance, and Account.
- Compact, genuinely contained result rows on desktop and labelled result cards on mobile.
- Market Scan sentiment is strictly Bullish, Bearish, or Neutral. Watch appears only as a Deep Analyze action signal after a user runs an analysis.
- Scan results render directly below the scan controls: a compact desktop results table and labelled mobile result cards. The scan results are not the right-side Deep Analyze panel.
- Each scan row has one explicit `Analyze · 1 credit` action. Do not add a separate chevron or make the entire row look like a second navigation action.
- One analysis card with a clear action signal, confidence, time horizon, freshness, evidence count, and top reasons.
- After an analysis is delivered, the primary follow-up is “View full breakdown,” not an immediate paid “Analyze again” action. A fresh rerun, if offered, is secondary and must disclose its one-credit cost.
- No decorative probability-like bars, unnecessary glow, excessive gradients, emoji-heavy interface elements, or disabled demo actions.
- Clear paid-action labels such as “Analyze · 1 credit” and a review step before Stripe checkout.
- Visible keyboard focus, WCAG-conscious contrast, semantic headings, reduced-motion behavior, and accessible mobile navigation.
- A polished hosting surface without Streamlit management chrome in customer-facing screenshots or sessions.

## First implementation boundary

The first implementation batch should touch only shared UI, navigation, Home, Discovery, Deep Analysis, Auth, and billing presentation. It must not modify the computation or persistence paths behind any of those screens.
