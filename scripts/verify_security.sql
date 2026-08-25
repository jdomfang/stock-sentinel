-- Post-migration security verification. READ ONLY -- changes nothing, safe any time.
--
-- WHY THIS FILE EXISTS
--
-- Everything else in this repo can be checked from a script: balances and rows
-- come back over PostgREST, function behaviour is covered by the SQL suites.
-- RLS POLICIES CANNOT BE. pg_policies lives in pg_catalog, which PostgREST does
-- not expose, so the one thing no automated check can confirm against
-- production is whether the policies that let a user set their own credits are
-- actually gone.
--
-- tests/test_bootstrap_safety.py asserts all of this against a database built
-- from the migrations. This asserts it against the database that actually
-- exists -- which is a different claim, and the one that matters.
--
-- HOW TO RUN
--   Supabase dashboard -> SQL Editor -> paste -> Run.
--   Every row should read PASS. Anything else is a live hole; the detail says
--   what it allows.

with checks(sort_key, check_name, ok, detail) as (

  -- RLS policies. Schema-qualified, so a same-named policy on another schema
  -- cannot answer for this one.
  select 1, 'no self-service credits (profiles_update_own)',
         not exists (select 1 from pg_policies
                      where schemaname='public' and tablename='profiles'
                        and policyname='profiles_update_own'),
         'a user can set their own credits and role'
  union all
  select 2, 'no forged ledger rows (usage_insert_own)',
         not exists (select 1 from pg_policies
                      where schemaname='public' and tablename='usage_events'
                        and policyname='usage_insert_own'),
         'a user can forge the audit trail that explains their balance'
  union all
  select 3, 'no admin ledger inserts (usage_insert_admin)',
         not exists (select 1 from pg_policies
                      where schemaname='public' and tablename='usage_events'
                        and policyname='usage_insert_admin'),
         'ledger rows can be written outside the credit functions'
  union all
  select 4, 'admin CAN still adjust other accounts (profiles_update_admin)',
         exists (select 1 from pg_policies
                  where schemaname='public' and tablename='profiles'
                    and policyname='profiles_update_admin'),
         'deliberate: the owner created it so an admin can adjust other accounts'

  -- SECURITY DEFINER hygiene. EVERY such function, not a list somebody keeps.
  union all
  select 5, 'every SECURITY DEFINER function pins search_path',
         not exists (
           select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.prosecdef
              and coalesce(array_to_string(p.proconfig, ','), '') not like '%search_path%'),
         'a mutable search_path lets an attacker shadow a table it writes'

  -- EXECUTE on every function that moves money or issues a credential.
  -- to_regprocedure returns NULL for a missing function, so a renamed or
  -- absent one reads as FAIL rather than silently passing.
  union all
  select 6, 'no money/credential RPC is callable by a user JWT',
         not exists (
           select 1 from unnest(array[
             'public.consume_credit(uuid,text,jsonb,text)',
             'public.refund_credit(uuid,text,uuid,text)',
             'public.grant_credits(uuid,integer,integer,text,text)',
             'public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text)',
             'public.reap_orphaned_work(interval)',
             'public.remember_issue(text,uuid,text,interval,text)',
             'public.remember_consume(text)',
             'public.remember_revoke_all(uuid)',
             'public.x_posts_billed_since(interval)',
             'public.is_open_paid_work(uuid)'
           ]) sig
            join unnest(array['anon','authenticated']) role_name on true
           where to_regprocedure(sig) is null
              or has_function_privilege(role_name, sig::regprocedure, 'EXECUTE')),
         'a signed-in user can call it, or it does not exist under that signature'
  union all
  select 7, 'service_role CAN call all of them',
         not exists (
           select 1 from unnest(array[
             'public.consume_credit(uuid,text,jsonb,text)',
             'public.refund_credit(uuid,text,uuid,text)',
             'public.grant_credits(uuid,integer,integer,text,text)',
             'public.admin_adjust_credits(uuid,uuid,integer,integer,boolean,text,text)',
             'public.remember_consume(text)',
             'public.x_posts_billed_since(interval)',
             'public.is_open_paid_work(uuid)'
           ]) sig
           where to_regprocedure(sig) is null
              or not has_function_privilege('service_role', sig::regprocedure, 'EXECUTE')),
         'the app cannot work without these'

  -- The credential store. to_regclass so a MISSING table is FAIL, not NULL.
  union all
  select 8, 'remember-me token store exists and is locked down',
         to_regclass('public.remember_tokens') is not null
         and coalesce((select relrowsecurity from pg_class
                        where oid = to_regclass('public.remember_tokens')), false)
         and not exists (select 1 from pg_policies
                          where schemaname='public' and tablename='remember_tokens')
         and not has_table_privilege('authenticated', 'public.remember_tokens', 'SELECT'),
         'refresh tokens are reachable by a user JWT'
  union all
  select 9, 'balance and ledger tables have RLS on',
         coalesce((select bool_and(relrowsecurity) from pg_class c
                    join pg_namespace n on n.oid = c.relnamespace
                   where n.nspname='public'
                     and c.relname in ('profiles','usage_events','work_runs','purchases')),
                  false),
         'a table with RLS off is readable by anyone with the anon key'
  union all
  select 10, 'the merged credits column exists with a floor',
         to_regclass('public.profiles') is not null
         and exists (select 1 from information_schema.columns
                      where table_schema='public' and table_name='profiles'
                        and column_name='credits')
         and exists (select 1 from pg_constraint
                      where conrelid = to_regclass('public.profiles')
                        and contype='c' and pg_get_constraintdef(oid) like '%credits%>=%0%'),
         'the live balance can go negative'
)
select check_name,
       case when ok then 'PASS' else 'FAIL - ' || detail end as result
  from checks
 order by sort_key;
