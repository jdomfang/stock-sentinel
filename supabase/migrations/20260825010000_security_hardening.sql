-- Close what the bootstrap left open, on databases that already ran it.
--
-- Three RLS policies and one unpinned SECURITY DEFINER function. All four came
-- from scripts/supabase_schema.sql -- the documented setup step -- so following
-- the instructions was enough to install them. The bootstrap no longer creates
-- any of them; this migration repairs a database that already ran the old one.
--
-- 1..3  THE POLICIES. 20260801020000_credit_integrity.sql already drops these,
--       so on the current production database they are almost certainly gone.
--       Repeated here because "almost certainly" is the wrong confidence level
--       for a policy that lets a user set their own credits, and because a
--       `drop policy if exists` costs nothing when it is already true.
--
--         profiles_update_own   RLS is ROW-level and cannot restrict columns,
--                               so "a user may edit their own profile" is
--                               exactly "a user may set their own credits and
--                               promote themselves to admin".
--         usage_insert_own      a user who can write ledger rows can forge the
--                               audit trail that explains a stolen balance --
--                               and then reconciliation AGREES with the theft,
--                               because the ledger is the thing being checked.
--         usage_insert_admin    same hole, admin JWT. Admins adjust credits
--                               through admin_adjust_credits, which moves the
--                               balance and writes the row in one transaction.
--
-- profiles_update_admin is deliberately LEFT IN PLACE. The owner created it so
-- an admin can adjust other accounts' credits. Do not drop it.
drop policy if exists "profiles_update_own"  on public.profiles;
drop policy if exists "usage_insert_own"     on public.usage_events;
drop policy if exists "usage_insert_admin"   on public.usage_events;

-- 4  THE TRIGGER. handle_new_user is SECURITY DEFINER and had no pinned
--    search_path -- the only such function in the database; every credit
--    function pins one. It runs as its owner on EVERY signup, so anything that
--    could create an object earlier on the resolved search path could shadow
--    public.profiles and have the insert land there as the owner.
--
--    The body is unchanged and already schema-qualifies its references, so an
--    empty search_path is safe. Found by tests/test_bootstrap_safety.py, which
--    asserts the property for every SECURITY DEFINER function rather than for a
--    list somebody has to maintain.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (user_id, email)
  values (new.id, new.email)
  on conflict (user_id) do update set email = excluded.email;
  return new;
end;
$$;

comment on function public.handle_new_user() is
    'Signup trigger. SECURITY DEFINER with a pinned empty search_path -- see '
    '20260825010000_security_hardening.sql.';
