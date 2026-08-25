#!/usr/bin/env python3
"""The migration itself: what happens to balances that already existed.

WHY THIS SUITE EXISTS

Every other SQL suite drops the schema, applies the full chain, and then creates
its users. So every profile they test was born AFTER the merge, and the one line
that touches real money on migration day -- the backfill that converts two
balances into one -- is never executed by any of them.

That is not a gap in coverage so much as a blind spot in shape. Mutating the
conversion to `greatest(scan, deep)` -- which silently destroys credits a user
paid for -- left all four suites green: 55/55 and 47/47. A change that takes a
user from 8 spendable actions to 5 produced no failing assertion anywhere.

So this suite works the other way round. It applies the chain up to but NOT
including the merge, writes profiles in the OLD two-column shape, and only then
applies the merge -- which is the exact sequence that will run against
production, and the only one where the backfill does anything at all.

Usage:
    docker compose -f docker-compose.test.yml up -d
    python3 tests/test_credit_merge.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("psycopg2 is required:  pip install psycopg2-binary")
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.migrations import chain as _chain  # noqa: E402

DSN = "host=127.0.0.1 port=5433 dbname=sentinel_test user=supabase_admin password=postgres"
MERGE = "20260824030000_merge_credit_buckets.sql"

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def conn():
    c = psycopg2.connect(DSN)
    c.autocommit = True
    return c


def apply_before_merge(cur) -> None:
    """The world as it stands the moment before the migration is pasted in."""
    cur.execute("drop schema if exists public cascade; create schema public;"
                " drop schema if exists auth cascade;")
    for p in _chain():
        if p.name == MERGE:
            break
        cur.execute(p.read_text())


def apply_merge(cur) -> None:
    """The merge, then EVERYTHING AFTER IT.

    Stopping at the merge would freeze this suite at 2026-08-24: the moment any
    later migration lands, the tests below would assert against a stale
    consume_credit and pass -- the exact silent-old-definition failure
    tests/migrations.py exists to remove, reintroduced by the suite that proves
    the removal. Resuming the chain keeps `after` meaning after.
    """
    files = _chain()
    start = next(i for i, p in enumerate(files) if p.name == MERGE)
    for p in files[start:]:
        cur.execute(p.read_text())


def old_profile(cur, scan: int, deep: int) -> str:
    """A profile in the two-column shape, as production holds it today."""
    uid = str(uuid.uuid4())
    cur.execute("insert into auth.users (id,email) values (%s,%s)", (uid, f"{uid[:8]}@t.test"))
    cur.execute(
        "insert into public.profiles (user_id,email,role,disabled,scan_credits,deep_credits)"
        " values (%s,%s,'user',false,%s,%s)", (uid, f"{uid[:8]}@t.test", scan, deep))
    return uid


def credits_of(cur, uid) -> int:
    cur.execute("select credits from public.profiles where user_id=%s", (uid,))
    return cur.fetchone()[0]


def frozen_of(cur, uid):
    cur.execute("select scan_credits, deep_credits from public.profiles where user_id=%s", (uid,))
    return cur.fetchone()


# ─────────────────────────────────────────────────────────── the conversion

def test_nobody_loses_an_action(cur):
    """Summation is the ONLY conversion that preserves what a user can do.

    Before the merge a holder of (scan, deep) could perform at most scan+deep
    metered actions. After it they must be able to perform exactly that many,
    of whichever kind they choose. Any rule that is not the sum -- max, min, a
    scale factor, a floor -- takes something away from somebody.

    The live balances on migration day are included by name, because those are
    the three people this actually happens to.
    """
    print("\nconversion: spendable actions before == spendable actions after")
    apply_before_merge(cur)
    cases = [
        (5, 3, "the owner's own account"),
        (3, 3, "the second live account"),
        (0, 1, "deep-only -- can now scan, could not before"),
        (7, 0, "scan-only"),
        (0, 0, "empty stays empty"),
        (9, 11, "unbalanced"),
    ]
    uids = [(old_profile(cur, s, d), s, d, why) for s, d, why in cases]
    apply_merge(cur)

    for uid, s, d, why in uids:
        got = credits_of(cur, uid)
        check(f"({s},{d}) -> {s + d}  [{why}]", got == s + d, f"got {got}")

    # The specific way this goes wrong quietly.
    check("a deep-heavy holder is not truncated to their larger bucket",
          credits_of(cur, uids[5][0]) == 20,
          "greatest(scan,deep) would give 11 here and destroy 9 paid actions")


def test_the_old_columns_are_left_frozen(cur):
    """They are the only perfect snapshot of the pre-merge world.

    `credits = 8` cannot be decomposed back into (5,3) once a single credit is
    spent, so rollback depends entirely on these two columns still holding what
    they held. A migration that helpfully zeroed them would make the rollback
    path -- restore the old function bodies -- silently restore everyone to a
    balance of zero.
    """
    print("\nrollback: the pre-merge balances survive the migration untouched")
    apply_before_merge(cur)
    uid = old_profile(cur, 5, 3)
    apply_merge(cur)
    check("scan_credits/deep_credits unchanged", frozen_of(cur, uid) == (5, 3),
          str(frozen_of(cur, uid)))
    check("and credits is the sum", credits_of(cur, uid) == 8)


def test_a_second_apply_changes_nothing(cur):
    """This project applies SQL by hand in the Supabase editor.

    A nervous re-paste is a normal event, and an unguarded
    `update profiles set credits = scan_credits + deep_credits` run a second
    time would restore every credit spent since the first run -- with no ledger
    row, no error, and no way to notice.
    """
    print("\nidempotency: re-pasting the migration is a no-op")
    apply_before_merge(cur)
    uid = old_profile(cur, 5, 3)
    apply_merge(cur)

    # Spend two, the way a user would between the two applications.
    for _ in range(2):
        cur.execute("select public.consume_credit(%s,'scan','{}'::jsonb,%s)",
                    (uid, str(uuid.uuid4())))
    spent = credits_of(cur, uid)
    check("two credits spent", spent == 6, f"got {spent}")

    apply_merge(cur)
    check("re-applying does NOT restore the spent credits",
          credits_of(cur, uid) == 6,
          f"got {credits_of(cur, uid)} -- the backfill ran again and reset the balance")


def test_the_signup_default_still_grants_two(cur):
    """A new account gets what it got before: one scan + one deep = 2 actions.

    handle_new_user inserts only (user_id, email), so the signup grant is not
    code -- it is the column default. A merged column defaulting to 0 would end
    the free trial silently, and the first anyone would know is a funnel that
    stopped converting.
    """
    print("\nsignup: the free trial is a column default, and must survive")
    apply_before_merge(cur)
    apply_merge(cur)
    uid = str(uuid.uuid4())
    cur.execute("insert into auth.users (id,email) values (%s,%s)", (uid, f"{uid[:8]}@t.test"))
    cur.execute("insert into public.profiles (user_id,email) values (%s,%s)",
                (uid, f"{uid[:8]}@t.test"))
    check("a fresh profile opens with 2 credits", credits_of(cur, uid) == 2,
          f"got {credits_of(cur, uid)}")


def test_the_floor_applies_to_the_new_column(cur):
    """profiles_credits_non_negative constrains only the two frozen columns.

    grant_credits clamps a revocation at zero and its comment justifies that by
    pointing at a CHECK constraint. If the merged column carries no constraint
    of its own, that reasoning is quietly false and the clamp is the only thing
    standing between a revocation and a negative balance.
    """
    print("\nfloor: the live balance cannot go negative")
    apply_before_merge(cur)
    uid = old_profile(cur, 5, 3)
    apply_merge(cur)
    rejected = False
    try:
        cur.execute("update public.profiles set credits = -1 where user_id=%s", (uid,))
    except psycopg2.errors.CheckViolation:
        rejected = True
    check("a negative merged balance is rejected by the database", rejected,
          "credits has no CHECK of its own")


def test_pre_merge_purchases_keep_their_grant_size(cur):
    """A chargeback must revoke what the purchase GRANTED, not today's pack.

    payments_api reads purchases.credits_granted with a fallback to the two
    legacy columns. The backfill is what makes the authoritative path answer
    correctly for a purchase made before the merge -- without it the fallback
    reaches the amount-derived branch and revokes the CURRENT pack size against
    an old payment.
    """
    print("\npurchases: an old $5 purchase still says it granted 2")
    apply_before_merge(cur)
    uid = old_profile(cur, 5, 3)
    cur.execute(
        "insert into public.purchases (user_id, checkout_session_id, payment_intent_id,"
        " amount_total, currency, status, scan_credits_granted, deep_credits_granted)"
        " values (%s,'cs_old','pi_old',500,'usd','paid',1,1)", (uid,))
    apply_merge(cur)
    cur.execute("select credits_granted, scan_credits_granted, deep_credits_granted"
                " from public.purchases where payment_intent_id='pi_old'")
    row = cur.fetchone()
    check("credits_granted backfilled to 2", row[0] == 2, str(row))
    check("the legacy columns are left intact for audit", row[1:] == (1, 1), str(row))


def test_a_pre_merge_debit_can_still_be_refunded(cur):
    """The design's central claim, and nothing asserted it.

    usage_events was deliberately left alone so that refund_credit's "was the
    original actually a debit" test -- which reads the PER-KIND cost column --
    keeps working against rows written before the merge. If that breaks, a job
    in flight at cutover is charged and never refunded, the reaper takes its
    failure branch, leaves the row 'running', and worker/reap.py exits 1 every
    five minutes forever. That is the exact crash loop 20260824020000 was
    written to end, re-entered through a door that fix does not cover.

    So: debit BEFORE the migration, refund AFTER it.
    """
    print("\nin-flight: a debit taken before the merge is refundable after it")
    apply_before_merge(cur)
    uid = old_profile(cur, 5, 3)
    cur.execute("select public.consume_credit(%s,'deep_analyze','{}'::jsonb,%s)",
                (uid, str(uuid.uuid4())))
    ev = cur.fetchone()[0]["event_id"]

    apply_merge(cur)
    check("the pre-merge debit converted with the balance", credits_of(cur, uid) == 7,
          f"got {credits_of(cur, uid)}")

    cur.execute("select public.refund_credit(%s,'deep_analyze',%s,%s)",
                (uid, ev, "refunded after the merge"))
    out = cur.fetchone()[0]
    check("the refund is accepted", out.get("ok") is True, str(out))
    check("...and returns the credit", credits_of(cur, uid) == 8,
          f"got {credits_of(cur, uid)}")

    # And the reaper, which is the path that actually crashes if this regresses.
    apply_before_merge(cur)
    uid2 = old_profile(cur, 5, 3)
    cur.execute("select public.consume_credit(%s,'scan','{}'::jsonb,%s)",
                (uid2, str(uuid.uuid4())))
    ev2 = cur.fetchone()[0]["event_id"]
    apply_merge(cur)
    cur.execute("update public.work_runs set started_at = now() - interval '1 hour'"
                " where event_id=%s", (ev2,))
    cur.execute("select public.reap_orphaned_work('15 minutes')")
    reap = cur.fetchone()[0]
    check("the reaper refunds a pre-merge orphan", reap.get("reaped") == 1, str(reap))
    check("...with no failures to retry forever", reap.get("failed") == 0, str(reap))
    check("...and the credit is back", credits_of(cur, uid2) == 8,
          f"got {credits_of(cur, uid2)}")


def test_the_invariant_holds_on_a_profile_with_real_history(cur):
    """balance == -(sum of BOTH cost columns), on a MIGRATED profile.

    The migration's header argues that summation beat max() because it makes the
    merged invariant an algebraic consequence rather than something to
    re-establish. Every profile in the tests above is created by a bare INSERT
    with no ledger history at all, so that argument was asserted nowhere.

    This builds a user the way production built one -- an opening balance row,
    then real debits and a refund -- migrates them, and checks the arithmetic
    actually closes.
    """
    print("\ninvariant: a migrated profile with a real ledger still reconciles")
    apply_before_merge(cur)
    uid = old_profile(cur, 0, 0)

    # Opening balance THROUGH the ledger, as 20260801040000 does for production.
    cur.execute("select public.grant_credits(%s,%s,%s,%s,%s)",
                (uid, 4, 4, "test opening balance", f"seed_{uid[:8]}"))
    # Fetch INSIDE the loop. A cursor's result set does not survive the next
    # execute, so reading it afterwards returns None and the event id is lost.
    last = None
    for kind in ("scan", "scan", "deep_analyze"):
        cur.execute("select public.consume_credit(%s,%s,'{}'::jsonb,%s)",
                    (uid, kind, str(uuid.uuid4())))
        last = cur.fetchone()[0]["event_id"]
    cur.execute("select public.refund_credit(%s,'deep_analyze',%s,'test')", (uid, last))

    def reconciles():
        # Read the sum into a variable BEFORE calling credits_of, which issues
        # its own query on the same cursor and discards this result set. Written
        # inline as `credits_of(...) == -cur.fetchone()[0]` it evaluates left to
        # right, so credits_of runs first and fetchone then returns None.
        cur.execute("select coalesce(sum(cost_scan_credits),0)"
                    " + coalesce(sum(cost_deep_credits),0)"
                    " from public.usage_events where user_id=%s", (uid,))
        ledger = cur.fetchone()[0]
        return credits_of(cur, uid) == -ledger

    apply_merge(cur)
    check("balance == -(sum of both cost columns) after migrating", reconciles(),
          f"credits={credits_of(cur, uid)}")

    # And it keeps holding as the merged functions are exercised.
    cur.execute("select public.consume_credit(%s,'scan','{}'::jsonb,%s)",
                (uid, str(uuid.uuid4())))
    check("...and still holds after a post-merge debit", reconciles(),
          f"credits={credits_of(cur, uid)}")
    cur.execute("select public.grant_credits(%s,2,0,'purchase',%s)", (uid, "evt_post"))
    check("...and after a post-merge purchase", reconciles(),
          f"credits={credits_of(cur, uid)}")


def test_no_suite_hardcodes_a_migration_list(cur):
    """A sixth hand-typed chain must not be able to appear quietly.

    Five suites carried their own lists and they had already drifted apart. Four
    were converted and the fifth -- test_corpus_cache.py -- was missed by the
    sweep and found in review, still installing the OLD credit functions and
    passing 31/31 while proving nothing about them.
    """
    print("\nno suite may pin its own migration chain")
    import re
    bad = []
    for f in sorted((Path(__file__).resolve().parent).glob("test_*.py")):
        src = f.read_text()
        if re.search(r'"supabase/migrations/\d+_', src) or \
           re.search(r"'supabase/migrations/\d+_", src):
            bad.append(f.name)
    check("every suite discovers the chain instead of listing it",
          not bad, f"hardcoded migration paths in: {bad}")


def main() -> int:
    print("=" * 74)
    print("  credit merge -- the one migration path no other suite executes")
    print("=" * 74)
    c = conn()
    with c.cursor() as cur:
        for t in (test_nobody_loses_an_action,
                  test_a_pre_merge_debit_can_still_be_refunded,
                  test_the_invariant_holds_on_a_profile_with_real_history,
                  test_no_suite_hardcodes_a_migration_list,
                  test_the_old_columns_are_left_frozen,
                  test_a_second_apply_changes_nothing,
                  test_the_signup_default_still_grants_two,
                  test_the_floor_applies_to_the_new_column,
                  test_pre_merge_purchases_keep_their_grant_size):
            try:
                t(cur)
            except Exception as e:
                FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
                print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")
    c.close()

    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\n  DO NOT APPLY THE MIGRATION:")
        for n, d in FAILED:
            print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
