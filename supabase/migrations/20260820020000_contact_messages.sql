-- Somewhere for a contact message to actually go.
--
-- pages/Contact.py collected a topic, an email and a message, wrote ONE LINE to
-- stdout, and told the user "Message received." Streamlit Cloud keeps a rolling
-- log buffer, so every message anyone has ever sent through that form is gone --
-- including, potentially, billing problems from people who could not log in to
-- report them any other way. The form was not unfinished; it was misleading.
--
-- PUBLIC WRITE, OPERATOR READ. The page has no login guard and should not: the
-- people most likely to need support are the ones locked out of their account.
-- So the write happens server-side with the service-role key, exactly as
-- verdict_log and signal_log do, and the browser never holds a credential that
-- can touch this table.
--
-- RLS enabled with NO policies: service-role only, matching every other
-- operator table in this schema.

create table if not exists public.contact_messages (
    id          uuid primary key default gen_random_uuid(),
    created_at  timestamptz not null default now(),

    topic       text not null,
    email       text not null,
    message     text not null,

    -- Captured only when the sender opted in. A bug report without the browser
    -- is usually unactionable, and asking afterwards costs a round trip to
    -- someone already annoyed enough to write in.
    user_agent  text,
    -- Present when the sender happened to be logged in, so a billing complaint
    -- can be tied to an account without asking them to prove who they are.
    -- Deliberately NOT a foreign key: a message must survive the account being
    -- deleted, which is precisely when someone writes in.
    user_id     uuid,

    -- The queue. NULL means nobody has dealt with it yet, which is the only
    -- state the admin page needs to sort on.
    handled_at   timestamptz,
    handled_note text
);

-- The only two reads that matter: the unhandled queue, and one sender's history.
create index if not exists contact_messages_unhandled_idx
    on public.contact_messages (created_at desc) where handled_at is null;
create index if not exists contact_messages_email_idx
    on public.contact_messages (lower(email), created_at desc);

alter table public.contact_messages enable row level security;

-- Cheap now, and the difference between a queue and a junk drawer. Lengths are
-- generous for a person and hostile to a script pasting a payload.
do $$
begin
    if not exists (select 1 from pg_constraint
                   where conname = 'contact_messages_len_chk') then
        alter table public.contact_messages add constraint contact_messages_len_chk
            check (char_length(email) between 3 and 254
                   and char_length(message) between 1 and 4000
                   and char_length(topic) <= 64
                   and char_length(coalesce(user_agent, '')) <= 512);
    end if;
end $$;
