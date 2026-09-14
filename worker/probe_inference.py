#!/usr/bin/env python3
"""Check the inference service is not just UP but CORRECT and FAST.

WHY LIVENESS IS NOT ENOUGH

On 2026-08-02 the inference service ran ~40x slower than it should have --
6.0s to score one text instead of 0.15s -- because torch sized its thread pool
from the visible cpu count and thrashed 8 threads over a fraction of a core.

Nothing caught it. /health answered instantly and truthfully. Sentry saw no
exception, because there was none: the service returned correct answers, just
slowly. Every scan silently paid ~25 seconds for it, and it was found by
accident while chasing an unrelated question.

That is the same shape as the price sync failing 43 times unnoticed: a
component degrading without erroring. Exception tracking cannot see it and a
liveness probe cannot either, so this asserts the two properties that actually
matter to the product:

  CORRECTNESS  a known-bullish sentence must still score Bullish, with the
               confidence it has always had. A model swap, a version bump, or a
               changed threshold would shift every recommendation in the app
               while every health check stayed green.

  LATENCY      under a ceiling. Slow-but-right is the failure that already
               happened and the one nothing else would report.

Rides the worker's 5-minute tick but runs ONCE AN HOUR (PROBE_EVERY_MINUTES).
Inference sleeps under Railway's Serverless setting -- FinBERT resident around
the clock was three quarters of the bill -- and Railway measures inactivity by
traffic, so a probe every five minutes would keep it awake and save nothing.
The reaper is untouched: it costs nothing to run and backs the "released within
15 minutes" promise the pages make.

Every probe therefore lands on a COLD container. That is fine: the warm-up call
below was already untimed for exactly that reason, and the timed measurement
happens after it, against the steady state. The warm-up duration is logged
because it IS the wake-up latency a user pays on the first scan after a quiet
spell -- the number that decides whether the client timeout is right.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

# Chosen because both candidate models are unambiguous about it and it is stable
# across the versions pinned in inference/requirements.txt.
CANARY_TEXT = "$AAPL crushing it this quarter, huge beat on services revenue"
EXPECT_SENTIMENT = "Bullish"

# PER MODEL, because the confidence is a property of the model and not of the
# sentence. A single hardcoded number meant the probe could not tell an
# INTENDED model swap from an accidental one -- it failed the worker on the
# deliberate FinBERT -> FinTwitBERT change, which is correct behaviour reported
# as an outage.
#
# Mirrors _DIRECTIONAL_MARGIN_BY_MODEL in utils/evidence.py, and for the same
# reason: every one of these is a measurement, not a preference.
_EXPECT_BY_MODEL = {
    # Measured repeatedly on the pinned versions.
    "prosusai/finbert": 0.8637,
    "stephanakkerman/fintwitbert-sentiment": 0.9807,
}

# An UNKNOWN model is a hard failure, not a pass. The whole point of this probe
# is that a silent model change shifts every recommendation in the app while
# every health check stays green -- defaulting to "no expectation" would hand
# that failure straight back.
#
# THE MODEL IS READ FROM THE SERVICE, NOT FROM THIS PROCESS'S ENVIRONMENT.
# The worker is a separate Railway service from inference, so MODEL_NAME here
# would be a THIRD copy of the same setting -- and the failure this whole
# session has been chasing is two copies of one setting drifting apart. Asking
# /health for the model it actually loaded cannot disagree with reality, and it
# means swapping models needs no change on the worker at all.

# Wide enough for float32 non-determinism across hosts (~1e-6 measured), far too
# tight for a different model or a changed threshold.
CONFIDENCE_TOLERANCE = float(os.environ.get("PROBE_CONFIDENCE_TOLERANCE", "0.01"))

# Baseline is ~0.28s warm. 5s is generous enough not to page on ordinary
# variance, and less than the 6.0s the real degradation produced.
MAX_SECONDS = float(os.environ.get("PROBE_MAX_SECONDS", "5"))

# How often to actually probe. The worker ticks every 5 minutes; the probe runs
# on the first tick of each PROBE_EVERY_MINUTES window. 60 = hourly. 5 = every
# tick, which is the old behaviour and the rollback. 360 = every six hours,
# for when the wake-up graph has been seen and trusted.
PROBE_EVERY_MINUTES = int(os.environ.get("PROBE_EVERY_MINUTES", "60"))
TICK_MINUTES = 5

# How long to wait for a SLEEPING service to answer /health. Railway documents
# that the first request may 502 while the container starts; the service is
# not broken, it is booting. Generous, because a false "unreachable" here pages
# someone at 3am about a service that is doing exactly what it was told to.
WAKE_DEADLINE_S = float(os.environ.get("PROBE_WAKE_DEADLINE_S", "90"))


def log(msg: str) -> None:
    print(msg, flush=True)


def due_now(now: time.struct_time | None = None) -> bool:
    """True on the first worker tick of each PROBE_EVERY_MINUTES window (UTC).

    Cron fires at :00, :05, ... so "first tick" means minute-of-window < 5.
    A tick that starts a few seconds late still lands inside the window; one
    delayed past it is skipped and the probe runs next window, which the
    healthchecks.io grace period absorbs.
    """
    now = now or time.gmtime()
    return ((now.tm_hour * 60 + now.tm_min) % PROBE_EVERY_MINUTES) < TICK_MINUTES


def health_with_wake(url: str) -> dict:
    """GET /health, tolerating a sleeping service. Raises on a real failure.

    5xx and connection errors mean "still booting" until WAKE_DEADLINE_S has
    passed; a 4xx is a real answer and is raised at once.
    """
    t0 = time.time()
    attempt = 0
    last: Exception | None = None
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=20) as r:
                body = json.loads(r.read() or b"{}")
            if attempt > 1:
                log(f"service woke in {time.time() - t0:.1f}s ({attempt} attempts)")
            return body
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
            last = e
        except Exception as e:  # URLError, timeout, connection reset
            last = e
        if time.time() - t0 >= WAKE_DEADLINE_S:
            raise RuntimeError(f"no answer within {WAKE_DEADLINE_S:.0f}s: "
                               f"{type(last).__name__}: {str(last)[:120]}")
        time.sleep(3)


def ping(base: str, suffix: str = "") -> None:
    if not base:
        return
    try:
        urllib.request.urlopen(f"{base.rstrip('/')}{suffix}", timeout=10).read()
    except Exception as e:
        log(f"WARN healthcheck ping{suffix or ' (success)'} failed: {type(e).__name__}")


def main() -> int:
    url = os.environ.get("INFERENCE_URL", "").rstrip("/")
    secret = os.environ.get("INFERENCE_SHARED_SECRET", "")
    hc = os.environ.get("HEALTHCHECK_INFERENCE_URL", "")

    if not url or not secret:
        # Not configured is not a failure -- the portal may still be scoring
        # locally. Say so and exit clean rather than paging about a service that
        # is not in use yet.
        log("INFERENCE_URL / INFERENCE_SHARED_SECRET not set; skipping probe")
        return 0

    if not due_now():
        # Not this tick. No healthcheck ping either: pinging without probing
        # would report a check that was never made.
        log(f"not due (every {PROBE_EVERY_MINUTES} min); skipping")
        return 0

    ping(hc, "/start")

    # Distinguish a COLD container from a DEGRADED one. They look identical to a
    # stopwatch and need opposite responses: a cold start is normal after any
    # deploy -- and, now that the service sleeps, after any quiet hour -- while
    # steady-state slowness is the bug this probe exists to catch.
    #
    # Railway redeploys every service on every push to master, so a commit
    # touching an unrelated folder restarts inference and drops its model. The
    # worker's next tick then measured a cold load against the warm ceiling and
    # marked itself CRASHED -- a false alarm that would train you to ignore it.
    #
    # /health reports whether the model is resident without loading it. If it is
    # not, spend one UNTIMED call warming it, then measure the steady state.
    try:
        _h = health_with_wake(url)
        loaded = bool(_h.get("loaded"))
        served_model = str(_h.get("model") or "").strip()
        expect_confidence = _EXPECT_BY_MODEL.get(served_model.lower())
    except Exception as e:
        log(f"ERROR /health unreachable: {type(e).__name__}: {str(e)[:160]}")
        ping(hc, "/fail")
        return 1

    if not loaded:
        log("model not resident (cold container); warming before timing")
        warm = urllib.request.Request(
            f"{url}/score",
            data=json.dumps({"texts": ["warm"]}).encode(),
            headers={"Content-Type": "application/json", "X-Inference-Secret": secret},
            method="POST",
        )
        t_warm = time.time()
        try:
            urllib.request.urlopen(warm, timeout=120).read()
        except Exception as e:
            log(f"ERROR warm-up failed: {type(e).__name__}: {str(e)[:160]}")
            ping(hc, "/fail")
            return 1
        # This is what a user pays on the first scan after a quiet spell.
        log(f"warm-up (model load) took {time.time() - t_warm:.1f}s")

    req = urllib.request.Request(
        f"{url}/score",
        data=json.dumps({"texts": [CANARY_TEXT]}).encode(),
        headers={"Content-Type": "application/json", "X-Inference-Secret": secret},
        method="POST",
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        log(f"ERROR inference HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
        ping(hc, "/fail")
        return 1
    except Exception as e:
        log(f"ERROR inference unreachable: {type(e).__name__}: {str(e)[:200]}")
        ping(hc, "/fail")
        return 1
    elapsed = time.time() - t0

    results = (payload or {}).get("results")
    if not isinstance(results, list) or len(results) != 1:
        log(f"ERROR malformed response: {str(payload)[:200]}")
        ping(hc, "/fail")
        return 1

    got = results[0]
    sentiment = got.get("sentiment")
    confidence = float(got.get("confidence") or 0.0)

    if expect_confidence is None:
        # Loud, and actionable: it prints the exact line to add.
        log(f"canary: {sentiment} {confidence:.4f} in {elapsed:.2f}s "
            f"served by {served_model!r}")
        log(f"ERROR no canary baseline for model {served_model!r}. If this "
            f"model is intended, add \"{served_model.lower()}\": "
            f"{confidence:.4f} to _EXPECT_BY_MODEL in this file, after "
            f"confirming the sentiment reads {EXPECT_SENTIMENT}.")
        ping(hc, "/fail")
        return 1

    drift = abs(confidence - expect_confidence)

    log(f"canary: {sentiment} {confidence:.4f} in {elapsed:.2f}s "
        f"(expect {EXPECT_SENTIMENT} {expect_confidence:.4f} for "
        f"{served_model}, max {MAX_SECONDS}s)")

    failures = []
    if sentiment != EXPECT_SENTIMENT:
        failures.append(f"sentiment {sentiment!r} != {EXPECT_SENTIMENT!r}")
    if drift > CONFIDENCE_TOLERANCE:
        # The model or its version changed. Every recommendation in the app
        # shifts with it, and nothing else in the stack would notice.
        failures.append(f"confidence drift {drift:.4f} > {CONFIDENCE_TOLERANCE} "
                        f"for model {served_model}")
    if elapsed > MAX_SECONDS:
        # Slow but correct -- the failure that already happened once and that
        # neither Sentry nor a liveness check can see.
        failures.append(f"latency {elapsed:.2f}s > {MAX_SECONDS}s")

    if failures:
        for f in failures:
            log(f"ERROR {f}")
        ping(hc, "/fail")
        return 1

    ping(hc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
