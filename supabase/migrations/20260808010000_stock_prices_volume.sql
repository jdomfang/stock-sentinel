-- Store the daily VOLUME we already receive and currently throw away.
--
-- DEPLOY ORDER
--   1..9  (applied)
--   10. THIS FILE
--   11. Deploy. The next nightly sync populates it for ~6,000 tickers.
--
-- Safe in either order: the column is nullable, and until the app writes it
-- every row simply has volume = null.
--
-- NOTE ON stock_prices ITSELF
--
-- This table predates the migration discipline in this folder -- it was created
-- by hand and has no migration of its own, which is why the columns are
-- documented here rather than defined here. Live schema before this change:
--
--     ticker        text  primary key
--     close_price   numeric
--     last_updated  timestamptz   -- the WRITE time, not the bar's date
--     currency      text
--
-- WHY VOLUME
--
-- The scan's search query is currently ten hand-written blocks of sector jargon
-- ("machinery OR freight OR backlog OR PMI ..."), and it does not work: the
-- utilities query returned 24 Technology cashtags against 11 Utilities ones,
-- and only 6.6% of all paid posts reached a displayed ticker.
--
-- The replacement is a query GENERATED from data -- the sector's own cashtags,
-- taken from ticker_master joined to this table:
--
--     ($NEE OR $SO OR $DUK OR $AEP OR ...) lang:en -is:retweet
--
-- Roughly 55-58 cashtags fit in X's 512-character query limit, so something has
-- to choose which. Ranking needs a liquidity measure, and until now the answer
-- was "we don't have one" -- ticker_master carries no market cap and no volume,
-- which is why an earlier review rejected this whole approach as unbuildable.
--
-- But the Polygon grouped-daily response ALREADY contains volume in its `v`
-- field, in the same single call that fetches every US ticker's close. We were
-- parsing `c` and discarding `v`. This column is the entire fix: one field we
-- already receive, from a call we already make.
--
-- WHY VOLUME AND NOT MARKET CAP
--
-- Volume measures whether people are TRADING a name, which correlates with
-- whether they are TALKING about it. Market cap measures size, which does not:
-- a large sleepy utility generates less chatter than a small volatile one. The
-- goal is to pick tickers likely to appear in 24 hours of X posts.
--
-- Combined with close_price it also gives dollar volume (close_price * volume),
-- which is the better liquidity proxy -- raw share count flatters penny stocks,
-- where ten million shares can be a few hundred thousand dollars.
--
-- Nullable on purpose: rows written before this change, and any written by the
-- interactive per-ticker path when Polygon omits the field, legitimately have
-- no volume. A selector must treat null as "unknown", never as zero.

alter table public.stock_prices
    add column if not exists volume bigint;

comment on column public.stock_prices.volume is
    'Daily share volume from the Polygon grouped-daily bar. Null means unknown, not zero. Use close_price * volume for dollar volume when ranking.';

-- Ranking candidates for a sector query is "the live, in-sector names with the
-- most dollar volume", so the selector filters on volume being present and
-- orders by it.
create index if not exists stock_prices_volume_idx
    on public.stock_prices (volume desc nulls last);
