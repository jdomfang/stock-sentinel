-- Ensure new users with your email become admin automatically
-- Paste into Supabase SQL editor and run once.

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
declare
  admin_email text := lower('kdjulianofr@hotmail.com');
  new_role text := 'user';
begin
  if lower(new.email) = admin_email then
    new_role := 'admin';
  end if;

  insert into public.profiles (user_id, email, role)
  values (new.id, new.email, new_role)
  on conflict (user_id) do update
    set email = excluded.email;

  return new;
end;
$$;
