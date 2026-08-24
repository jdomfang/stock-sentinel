-- The reaper treated "already refunded" as a failed refund, and looped forever.
--
-- WHAT HAPPENED, 2026-08-23. A scan failed with kind=ticker_db. The page
-- refunded the credit explicitly, then its finally called refund_credit again
-- -- idempotent, returns already_refunded -- and complete_work. One work_run
-- was nonetheless left 'running'. The reaper found it, called refund_credit,
-- got {ok:false, reason:'already_refunded'}, counted that as a FAILURE, left
-- the row 'running' so the next pass would retry, and worker/reap.py exited 1.
--
-- Railway showed the worker Crashed every five minutes, indefinitely, over one
-- row whose user had already been made whole. The reason can never change, so
-- "retry next pass" was a loop with no exit.
--
-- utils/credits.py:221 has always had this right -- "already_refunded is the
-- idempotent path, not a failure", and it returns True. Only this function
-- disagreed. One event, two verdicts, and the disagreement was invisible until
-- something depended on the exit code.
--
-- THE DISTINCTION THIS PRESERVES. Leaving a row 'running' when a refund
-- genuinely could not be applied is correct and stays: closing it would strand
-- a user who was charged and never refunded, which is the outcome this
-- function exists to prevent. already_refunded is the opposite case -- the
-- money is already back, and the only thing left is the lifecycle row.
--
-- Counted as `closed`, not `reaped`. They are different facts and reap.py says
-- so: a reaped row is a user who was charged and got nothing, and is worth
-- investigating; a closed row is bookkeeping behind a refund that already
-- happened. Folding them together would make every one of these look like a
-- symptom of work dying upstream.

create or replace function public.reap_orphaned_work(
  p_older_than interval default '15 minutes'
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  r          record;
  v_refund   jsonb;
  v_reason   text;
  v_reaped   integer := 0;
  v_closed   integer := 0;
  v_failed   integer := 0;
  v_details  jsonb := '[]'::jsonb;
begin
  for r in
    select w.id, w.user_id, w.event_id, w.kind, w.started_at
      from public.work_runs w
     where w.status = 'running'
       and w.started_at < now() - p_older_than
     order by w.started_at
     -- Skip rows another reaper instance already holds, rather than queueing
     -- behind it. Two overlapping runs then do disjoint work instead of
     -- double-refunding or blocking.
     for update skip locked
  loop
    v_refund := public.refund_credit(
      r.user_id, r.kind, r.event_id,
      'orphaned: no completion after ' || p_older_than::text
    );
    v_reason := coalesce(v_refund ->> 'reason', '');

    if coalesce((v_refund ->> 'ok')::boolean, false) then
      update public.work_runs
         set status = 'orphaned', finished_at = now(),
             detail = 'auto-refunded by reaper: ' || v_reason
       where id = r.id;
      v_reaped := v_reaped + 1;

    elsif v_reason = 'already_refunded' then
      -- The credit is back. Nothing is owed and nothing can change on a
      -- retry, so the row is closed rather than re-found every five minutes.
      update public.work_runs
         set status = 'orphaned', finished_at = now(),
             detail = 'closed by reaper: credit was already refunded'
       where id = r.id;
      v_closed := v_closed + 1;

    else
      -- Leave status='running' so the next pass retries. Marking it closed
      -- would strand a user who was charged and never refunded -- the exact
      -- outcome this function exists to prevent.
      v_failed := v_failed + 1;
      v_details := v_details || jsonb_build_object(
        'event_id', r.event_id, 'reason', v_reason);
    end if;
  end loop;

  return jsonb_build_object(
    'ok', true, 'reaped', v_reaped, 'closed', v_closed, 'failed', v_failed,
    'failures', v_details, 'older_than', p_older_than::text
  );
end;
$$;

comment on function public.reap_orphaned_work(interval) is
    'Refund work_runs stuck in ''running''. Returns reaped (refunded now), '
    'closed (credit already back; row tidied), failed (retry next pass). '
    'already_refunded is idempotent success, not a failure -- see '
    '20260824020000_reaper_already_refunded.sql.';
