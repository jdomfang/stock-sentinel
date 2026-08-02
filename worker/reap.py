#!/usr/bin/env python3
"""Refund paid work that never finished. Runs on a schedule, then exits.

WHY A SEPARATE PROCESS

This exists for exactly one failure: the process doing the work died. On
2026-08-01 an OOM kill took a Discovery scan mid-run -- SIGKILL runs no handler,
so no `except` fired, no `finally` fired, and the user was charged for a blank
page. Nothing inside a dying process can report its own death, so the refund has
to come from somewhere else, alive, on a clock.

Every existing service is the wrong place:
  * the portal has no scheduler -- Streamlit only runs when someone clicks, and
    if the portal is what died it cannot reap itself;
  * payments_api and inference are request-driven;
  * the laptop crontab ran on 51% of nights, and a reaper that runs half the
    time refunds half the orphans.

WHAT IT DOES

Calls public.reap_orphaned_work(), which finds work_runs still 'running' past a
threshold, refunds each via refund_credit (idempotent), and marks them
'orphaned'. All the logic lives in SQL; this is just something that shows up on
time and shouts if it cannot.

NO DEPENDENCIES ON PURPOSE

stdlib only -- no pip install, no requirements.txt. The image is python:3.11-slim
and nothing else, so it builds in seconds and starts in milliseconds. On a cron
service that runs for about a second every five minutes, cold start IS the
runtime. It also means this job cannot be broken by an unrelated dependency
resolution, which matters for a thing whose entire value is running unattended.

EXITS NON-ZERO ON FAILURE

Railway marks the run failed and healthchecks.io goes red. A reaper that fails
silently is the same bug as the price sync that failed 43 times without anyone
noticing -- and it would be worse here, because the symptom is credits quietly
not being refunded.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

TIMEOUT = 30


def log(msg: str) -> None:
    print(msg, flush=True)


def ping(base: str, suffix: str = "") -> None:
    """Best-effort dead-man-switch ping. Never raises, never blocks for long."""
    if not base:
        return
    try:
        urllib.request.urlopen(f"{base.rstrip('/')}{suffix}", timeout=10).read()
    except Exception as e:
        log(f"WARN healthcheck ping{suffix or ' (success)'} failed: {type(e).__name__}")


def main() -> int:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    older_than = os.environ.get("REAPER_OLDER_THAN", "15 minutes")
    hc = os.environ.get("HEALTHCHECK_REAPER_URL", "")

    if not url or not key:
        log("ERROR SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
        ping(hc, "/fail")
        return 1

    ping(hc, "/start")
    log(f"reaping work_runs older than {older_than}")

    req = urllib.request.Request(
        f"{url}/rest/v1/rpc/reap_orphaned_work",
        data=json.dumps({"p_older_than": older_than}).encode(),
        headers={
            "Content-Type": "application/json",
            "apikey": key,
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode(errors="replace")
        log(f"ERROR reap_orphaned_work HTTP {e.code}: {body}")
        ping(hc, "/fail")
        return 1
    except Exception as e:
        log(f"ERROR reap_orphaned_work call failed: {type(e).__name__}: {str(e)[:200]}")
        ping(hc, "/fail")
        return 1

    if not isinstance(payload, dict) or not payload.get("ok"):
        log(f"ERROR unexpected response: {payload!r}")
        ping(hc, "/fail")
        return 1

    reaped = int(payload.get("reaped") or 0)
    failed = int(payload.get("failed") or 0)
    log(f"reaped={reaped} failed={failed}")

    # A refund that could not be applied leaves the row 'running' so the next
    # pass retries. That is the right behaviour -- closing it would strand a
    # charged user -- but it must not look like a clean run, or a permanently
    # stuck orphan would repeat forever in silence.
    if failed:
        log(f"ERROR {failed} refund(s) failed: {json.dumps(payload.get('failures'))[:500]}")
        ping(hc, "/fail")
        return 1

    if reaped:
        # Worth shouting about: every reaped row is a user who was charged and
        # got nothing. Frequent reaping means something upstream is dying.
        log(f"NOTICE refunded {reaped} orphaned run(s) -- investigate why work is not completing")

    ping(hc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
