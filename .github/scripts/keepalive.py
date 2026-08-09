#!/usr/bin/env python3
"""Keep the Streamlit Community Cloud deployment awake, and wake it if it slept.

WHY A BROWSER AND NOT curl

Community Cloud sleeps any app with no traffic for 12 hours. Both the sleep
state and the wake action live in JavaScript: the edge serves an identical
~9 KB React shell whether the container is running or not, and /_stcore/health
returns that same shell rather than "ok". A plain HTTP request therefore cannot
tell asleep from awake, and almost certainly never reaches the app container --
so it is not the traffic that resets the timer.

That gate is deliberate. If a GET woke a sleeping app, every crawler and
link-preview bot touching a .streamlit.app URL would spin up a container
Streamlit pays for and nobody looks at. The only way through is to be a real
browser doing what a real visitor does.

WHAT THIS COSTS THE OWNER

Nothing. app.py makes no network calls and redirects to pages/Home.py, whose
only Supabase call sits behind the logged-in branch. An anonymous visitor
renders demo data: no X posts, no Polygon calls, no credits.

Run locally with:  APP_URL=https://your-app.streamlit.app/ python3 keepalive.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

APP_URL = (os.environ.get("APP_URL") or "").strip()

# The sleep screen's button. Matched on its visible text rather than a class or
# test id: Streamlit owns this page and ships it without our involvement, so
# prose is the most stable handle we have. If they reword it this stops
# matching -- which is why an unrecognised page is a FAILURE below and uploads a
# screenshot, instead of quietly passing and letting the app sleep for weeks.
WAKE_TEXT = re.compile(r"get this app back up", re.I)

# Rendered by Streamlit once the script runs -- but NOT in the top document.
# Community Cloud serves its own chrome at the top level and runs the app in a
# child frame (.../~/+/Home); the top document carries exactly one data-testid,
# "appCreatorAvatar". A first live run against this app looked for stApp at the
# top level, found nothing after 180 seconds, and reported failure while the
# screenshot showed the app rendered and working. Always search the frames.
APP_READY = '[data-testid="stApp"]'

# stApp appears early in Streamlit's boot, before any content. Requiring some
# text as well stops a half-booted frame being called awake.
MIN_TEXT = 50

# Streamlit's own crash screen.
ERROR_TEXT = re.compile(r"Oh no\.|Error running app", re.I)

CLASSIFY_TIMEOUT = 45     # seconds to decide asleep vs awake
WAKE_TIMEOUT = 180        # cold container start can be slow
DWELL = 8                 # stay connected so the session counts as real traffic
SHOT = Path("keepalive-page.png")


def log(msg: str) -> None:
    print(msg, flush=True)


def summary(msg: str) -> None:
    """Append to the GitHub Actions run summary, if we are in one."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")


def rendered_text(page) -> str | None:
    """Text of the app frame once it has actually rendered, else None.

    Scans every frame rather than pinning the app's iframe path, so a change to
    Community Cloud's URL scheme degrades to a slower match, not a false alarm.
    """
    for frame in page.frames:
        try:
            if frame.locator(APP_READY).count() == 0:
                continue
            text = frame.locator("body").inner_text(timeout=5_000)
        except PWError:
            continue  # detached or cross-origin (the status-page embed)
        if len(text.strip()) >= MIN_TEXT:
            return text
    return None


def classify(page, timeout: int) -> str:
    """Poll until the SPA reveals which screen it settled on.

    Returns 'awake', 'asleep', 'error' or 'unknown'. Polling rather than
    wait_for_selector because we are racing several possible outcomes and want
    whichever lands first, not a specific one.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = rendered_text(page)
            if text is not None:
                return "error" if ERROR_TEXT.search(text) else "awake"
            # The sleep screen is top-level: there is no app frame yet.
            if page.get_by_text(WAKE_TEXT).count() > 0:
                return "asleep"
        except PWError:
            pass  # mid-navigation; the DOM will settle
        page.wait_for_timeout(1_000)
    return "unknown"


def visit(page) -> str:
    """One full attempt: load, classify, wake if needed, confirm."""
    page.goto(APP_URL, wait_until="domcontentloaded", timeout=60_000)
    state = classify(page, CLASSIFY_TIMEOUT)
    log(f"  page settled as: {state}")

    if state != "asleep":
        return state

    log("  app is asleep -- clicking the wake button")
    page.get_by_text(WAKE_TEXT).first.click()

    # Waking rebuilds the container: minutes, not seconds. Reuse classify so the
    # post-click wait looks in the frames exactly like the pre-click one -- the
    # two drifting apart is what produced the false failure in the first place.
    state = classify(page, WAKE_TIMEOUT)
    if state == "awake":
        return "woke"
    if state == "unknown":
        log(f"  clicked, but nothing rendered within {WAKE_TIMEOUT}s")
    return state


def main() -> int:
    if not APP_URL:
        log("APP_URL is not set")
        return 1

    log(f"target: {APP_URL}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        state = "unknown"
        # Two passes: a cold container occasionally serves one stale shell
        # before it is ready, and a reload clears it. Cheap insurance against a
        # false alarm email at 00:17.
        for attempt in (1, 2):
            log(f"attempt {attempt}")
            try:
                state = visit(page)
            except (PWTimeout, PWError) as e:
                log(f"  {type(e).__name__}: {str(e).splitlines()[0][:160]}")
                state = "unknown"
            if state in ("awake", "woke"):
                break
            if attempt == 1:
                time.sleep(10)

        if state in ("awake", "woke"):
            # Hold the websocket open briefly. A load that disconnects
            # instantly is weaker evidence of a visit than one that lingers.
            log(f"  holding the session open for {DWELL}s")
            page.wait_for_timeout(DWELL * 1_000)

        try:
            page.screenshot(path=str(SHOT), full_page=False)
        except PWError:
            pass

        context.close()
        browser.close()

    verdict = {
        "awake": ("✅", "Already awake -- the visit reset the 12h timer.", 0),
        "woke":  ("🔔", "Was asleep. Clicked the button; the app is back up.", 0),
        "error": ("🔥", "The app is RUNNING but showing Streamlit's error "
                        "screen. The keepalive worked; your deploy is broken.", 1),
        "unknown": ("❓", "Could not recognise the page. Either Streamlit "
                          "reworded the wake button or the app is unreachable. "
                          "See the uploaded screenshot.", 1),
    }[state]

    icon, text, code = verdict
    log(f"{icon} {text}")
    summary(f"### {icon} keepalive\n\n{text}\n\n`{APP_URL}`")
    return code


if __name__ == "__main__":
    sys.exit(main())
