#!/usr/bin/env python3
"""Run every suite and report one number.

WHY THIS EXISTS

There was no way to run the tests except by invoking each file by hand, and
nothing ran them automatically -- .github/workflows/ contained only a keepalive
ping. So a suite could rot indefinitely and the only signal was somebody
remembering to check.

Each suite is a standalone script that prints "N passed, M failed" and exits
non-zero on failure; this collects them. Exit code is what CI keys on.

    python3 tests/run_all.py            # everything
    python3 tests/run_all.py --no-db    # skip suites that need Postgres

The five SQL suites need the throwaway database:
    docker compose -f docker-compose.test.yml up -d
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"

# Suites that need the disposable Postgres on 127.0.0.1:5433. Detected rather
# than listed would be better, but "does it import psycopg2" is exactly the
# heuristic that would silently drop a suite the day someone imports it for an
# unrelated reason -- so it is explicit, and run_all fails loudly on a suite it
# has never seen.
NEEDS_DB = {
    "test_credit_integrity.py", "test_work_runs.py", "test_admin_adjust.py",
    "test_grant_credits.py", "test_corpus_cache.py", "test_credit_merge.py",
    "test_bootstrap_safety.py", "test_remember_me.py", "test_spend_budget.py",
    "test_public_demo_snapshots.py",
}

SUMMARY = re.compile(r"(\d+) passed, (\d+) failed")


def _output_text(value: str | bytes | None) -> str:
    """Normalize TimeoutExpired output across supported Python versions."""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def main() -> int:
    no_db = "--no-db" in sys.argv
    files = sorted(TESTS.glob("test_*.py"))
    if not files:
        print("no test files found -- refusing to report success")
        return 2

    total_p = total_f = 0
    failed_suites: list[str] = []
    skipped: list[str] = []

    for f in files:
        if no_db and f.name in NEEDS_DB:
            skipped.append(f.name)
            continue
        command = [sys.executable, str(f)]
        try:
            r = subprocess.run(command, cwd=REPO, capture_output=True,
                               text=True, timeout=900)
        except subprocess.TimeoutExpired as exc:
            r = subprocess.CompletedProcess(
                command,
                124,
                stdout=_output_text(exc.stdout),
                stderr=_output_text(exc.stderr) + "\nsuite timed out after 900 seconds",
            )
        m = SUMMARY.search(r.stdout)
        if m:
            p, fl = int(m.group(1)), int(m.group(2))
            total_p += p
            total_f += fl
            status = "ok" if fl == 0 and r.returncode == 0 else "FAIL"
        elif r.returncode == 0:
            # A suite with its own reporting format (the golden-file oracle).
            # Trust the exit code, count nothing.
            status = "ok"
        else:
            status = "FAIL"
        if status == "FAIL":
            failed_suites.append(f.name)
            # Show why, or a red CI run says nothing.
            print(f"\n{'=' * 70}\n{f.name}\n{'=' * 70}")
            print(r.stdout[-4000:])
            if r.stderr.strip():
                print("--- stderr ---")
                print(r.stderr[-2000:])
            # GitHub's public check API exposes annotations even when the
            # repository's full Actions log needs an authenticated browser.
            # Name the suite here so a red integration run is diagnosable from
            # the check itself instead of collapsing to the unhelpful
            # "Process completed with exit code 1" annotation.
            if os.environ.get("GITHUB_ACTIONS") == "true":
                details = []
                if r.stdout.strip():
                    details.append("stdout:\n" + r.stdout.strip()[-1000:])
                if r.stderr.strip():
                    details.append("stderr:\n" + r.stderr.strip()[-1000:])
                detail = "\n".join(details) or "suite exited non-zero without output"
                # Workflow commands are line-oriented. Keep the useful tail on
                # one line and encode the characters GitHub reserves.
                detail = detail[-1500:].replace("%", "%25")
                detail = detail.replace("\r", "%0D").replace("\n", "%0A")
                print(f"::error file=tests/{f.name},title=Failed test suite::{detail}")
        print(f"  {status:<4}  {f.name:<34} "
              f"{m.group(1) + ' passed, ' + m.group(2) + ' failed' if m else '(own format)'}")

    print("\n" + "=" * 70)
    print(f"  {total_p} passed, {total_f} failed across {len(files) - len(skipped)} suites")
    if skipped:
        # Named, because a silent skip is how a database suite stops running and
        # nobody notices for a month.
        print(f"  SKIPPED (no database): {', '.join(skipped)}")
    if failed_suites:
        print(f"  failing suites: {', '.join(failed_suites)}")
    print("=" * 70)
    return 1 if failed_suites else 0


if __name__ == "__main__":
    sys.exit(main())
