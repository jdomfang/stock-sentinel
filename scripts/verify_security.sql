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

select 'no self-service credits (profiles_update_own)' as check_name,
       case when exists (select 1 from pg_policies
                          where schemaname='public' and tablename='profiles'
                            and policyname='profiles_update_own')
            then 'FAIL - a user can set their own credits and role'
            else 'PASS' end as result
union all
select 'no forged ledger rows (usage_insert_own)',
       case when exists (select 1 from pg_policies
                          where schemaname='public' and tablename='usage_events'
                            and policyname='usage_insert_own')
            then 'FAIL - a user can forge their own audit trail'
            else 'PASS' end
union all
select 'no admin ledger inserts (usage_insert_admin)',
       case when exists (select 1 from pg_policies
                          where schemaname='public' and tablename='usage_events'
                            and policyname='usage_insert_admin')
            then 'FAIL - ledger rows can be written outside the credit functions'
            else 'PASS' end
union all
select 'admin CAN still adjust other accounts (profiles_update_admin)',
       case when exists (select 1 from pg_policies
                          where schemaname='public' and tablename='profiles'
                            and policyname='profiles_update_admin')
            then 'PASS'
            else 'FAIL - this one is deliberate and must exist' end
union all
select 'signup trigger pins search_path (handle_new_user)',
       case when exists (select 1 from pg_proc
                          where proname='handle_new_user'
                            and proconfig::text like '%search_path%')
            then 'PASS'
            else 'FAIL - hijackable SECURITY DEFINER on every signup' end
union all
select 'paid work bypasses the spend cap (is_open_paid_work)',
       case when exists (select 1 from pg_proc where proname='is_open_paid_work')
            then 'PASS'
            else 'FAIL - migration 20260825040000 not applied' end
union all
select 'remember-me token store is locked down',
       case when (select relrowsecurity from pg_class where relname='remember_tokens')
             and not exists (select 1 from pg_policies where tablename='remember_tokens')
            then 'PASS'
            else 'FAIL - refresh tokens are reachable by a user JWT' end
union all
select 'credit functions are service_role only',
       case when has_function_privilege('authenticated',
              'public.consume_credit(uuid,text,jsonb,text)','EXECUTE')
             or has_function_privilege('authenticated',
              'public.grant_credits(uuid,integer,integer,text,text)','EXECUTE')
            then 'FAIL - a signed-in user can grant themselves credits'
            else 'PASS' end
order by 1;
