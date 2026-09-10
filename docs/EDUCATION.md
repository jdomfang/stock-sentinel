# Education and AI Ed Shorts

The shared footer opens Education. The hub introduces Markets & Investing and
AI & Technology; AI Ed Shorts is the available resource. Publisher and privacy
pages use **Contact The Stock Sentinel**, linked to the existing public `/Contact`
form. No dedicated email address or email placeholder is required.

## Review and production routes

| Page | Community Cloud develop preview | Railway production target |
| --- | --- | --- |
| Education | `/Education` | `/education` |
| AI Ed Shorts | `/AI_Ed_Shorts` | `/education/ai-ed-shorts` |
| Publisher privacy | `/AI_Ed_Shorts_Privacy` | `/privacy/ai-ed-shorts` |

Develop is https://stock-sentinel-dev.streamlit.app. Production is
https://thestocksentinel.com. Production targets become available after the
normal reviewed merge and Railway deployment, not merely a develop push.

`utils/education_content.py` provides the same approved content to both renderers.
`assets/styles/education.css` scopes the layout to Education and uses the existing
Stock Sentinel design tokens. Native previews reuse the shared navigation and
footer. The static header retains the existing anonymous navigation labels;
Start free returns to Home, where the existing signup button starts registration.
No auth flow was changed.

## Hosting and discovery

`portal/run.py` starts the normal Streamlit CLI after adding exact public handlers
to its existing Tornado application. `portal/education_routes.py` serves the three
public pages, three allowlisted stylesheets, `/robots.txt`, and `/sitemap.xml`.
GET and HEAD are supported. Existing Streamlit handlers, websocket connections,
and server settings remain owned by Streamlit. This uses a private server hook
and deliberately requires pinned Streamlit 1.40.0: verify HTTP and websocket tests
before upgrading that version.

The Docker CMD uses `python -m portal.run run app.py`. If Railway has an explicit
Start Command override, it must use this launcher as well; no Railway settings
were changed by this implementation. Community Cloud keeps `app.py` and uses
native page routes automatically.

Public HTML includes titles, descriptions, canonical URLs, Open Graph metadata,
and crawlable content without requiring JavaScript or login. The apex sitemap
lists these three pages. The existing about-site sitemap and nightly pulse
publishing remain separate.

Education was added to existing static-site footers and the pulse-page generator
footer template so future pages retain it. The existing about-site Terms page
contains the AI Ed Shorts provisions. Coordinate the about-site and Railway
production releases: about-site links point to the production Education route,
which requires the updated Railway launcher to be deployed.

## Scope and content review

This change publishes informational pages; it does not implement a YouTube
publisher, OAuth integration, token storage, or deletion service. Before a
production policy submission, the owner should verify that the supplied privacy
and publishing descriptions match the actual publisher's scopes, providers,
retention, revocation, and human approval behavior. Publication does not itself
establish Google verification or approval.

## Validation

Run `python3 tests/run_all.py` with the disposable Postgres from
`docker-compose.test.yml` available. New standalone suites cover public HTTP/HEAD,
route ownership, assets, metadata, shared content, native page rendering,
contact destinations, and generated footer consistency.

Browser checks in `tests/browser/education.cjs` exercise public pages at 390,
768, and 1440 pixels; keyboard skip navigation; the Education/publisher/privacy
journey; the actual Home websocket and footer handoff; and native preview pages.
Start local servers on 18520 using the portal launcher and 18521 using standard
Streamlit, then run with Playwright available in NODE_PATH and, if needed,
CHROMIUM_EXECUTABLE set to the local Chromium binary.

Build with `docker build -f portal/Dockerfile .`. Checks use local test services
and do not run paid scans, checkout, workers, or production database mutations.
