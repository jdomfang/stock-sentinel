"""The production migration chain, discovered rather than listed.

WHY THIS EXISTS

Four SQL suites each carried their own hand-typed list of migration files, and
they had already drifted apart: only test_work_runs.py included the reaper fix
(20260824020000). test_work_runs.py's own comment names the consequence --
"this suite exercised the PREVIOUS function and passed while the fix went
untested" -- and then the next migration reintroduced it, because a curated
list is only equal to production while somebody keeps curating.

The failure is silent in the worst way. A suite missing the newest migration
applies an OLD function definition and then asserts against it. Every check
passes. The green run says nothing whatsoever about the code being shipped, and
nothing distinguishes that from a real pass.

Globbing removes the step a human has to remember. A new migration is in the
chain the moment it is written, so a suite that would not survive it fails
immediately instead of at the point of deploy.

ORDER IS LEXICOGRAPHIC, which is why the timestamp prefix is load-bearing:
20260801020000 before 20260802010000 before 20260824030000. That is production
order, and several migrations only make sense applied after their predecessor
(20260802010000 extracts consume_credit from 20260801060000 and references
public.work_runs, so applying it against a database that never created that
table installs a function pointing at nothing).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def chain() -> list[Path]:
    """Stub schema, then every production migration in filename order."""
    stub = REPO / "tests" / "sql" / "00_supabase_stub.sql"
    found = sorted((REPO / "supabase" / "migrations").glob("*.sql"))
    if not found:
        # A glob that silently returns nothing would apply the stub alone and
        # let every suite pass against a database with no credit engine in it.
        raise RuntimeError("no migrations found -- refusing to run a suite that "
                           "would pass against an empty schema")
    return [stub] + found
