#!/usr/bin/env python3
"""Run every scheduled job on one cron tick, independently.

One Railway service, one schedule, several jobs. Each job pings its OWN
healthchecks.io check, because they fail for unrelated reasons: the reaper going
quiet means credits stop being refunded, while inference degrading means every
recommendation is wrong. Collapsing them into one check would make whichever
failed second invisible.

A job that raises must not stop the others -- the reaper is the safety net for
paid work and must run even if the inference probe is failing. The exit code is
non-zero if ANY job failed, so Railway records the run as failed and the
per-job healthchecks say which.
"""

from __future__ import annotations

import sys
import traceback

import probe_inference
import reap

JOBS = [("reap", reap.main), ("probe_inference", probe_inference.main)]


def main() -> int:
    worst = 0
    for name, fn in JOBS:
        print(f"--- {name} ---", flush=True)
        try:
            rc = fn()
        except Exception:
            # An unhandled crash in one job is itself a failure worth reporting,
            # but the remaining jobs still run.
            traceback.print_exc()
            rc = 1
        if rc:
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main())
