"""Measure what every paid X call actually bought. Standard library only.

WHY

X bills per POST RETURNED, so a scan's cost is the number of posts it pulls.
One hand-audited production scan returned 99 posts, 50 of which produced no
ticker at all. That number had never been measured before and there was no way
to tell whether a query edit moved it.

Separately, the pipeline reads BARE uppercase words as tickers, and 17 of 20
ordinary English words tested are real listed symbols (AIR = AAR Corp,
RAIL = FreightCar America, BOOM = DMC Global -- all Industrials). So the product
could not distinguish a real recommendation from a fabricated one, and neither
could anyone reading the logs.

Everything here is derived from posts ALREADY PAID FOR. Recording it adds no X
spend and no API calls.

FAILURE POLICY

Nothing in this module may raise into a caller. Instrumentation that can break
a paid scan is worse than no instrumentation -- the user loses the product to
gain a metric. Errors are logged at WARNING and swallowed.

Stdlib-only for the same reason as utils/prices.py and utils/corpus_cache.py:
one implementation that both the Streamlit portal and a bare container can
import, rather than a second copy that drifts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

TABLE = "x_call_metrics"


def _config(name: str, default: str = "") -> str:
    v = os.getenv(name, "")
    if v:
        return v
    try:
        import streamlit as st
        return str(st.secrets.get(name, "") or "") or default
    except Exception:
        return default


def query_hash(query: str) -> str:
    """Same content-addressed scheme as utils.corpus_cache.

    Keying metrics on the query's CONTENT rather than a hand-maintained version
    string is what turns this table into a feedback loop: every edit
    automatically starts its own track record, and no one has to remember to
    bump anything for the comparison to stay honest.
    """
    return hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:12]


class ScanTally:
    """Accumulates per-post provenance during a scan, then classifies it.

    Used across the pagination loop: record() per post as it is extracted, then
    finalize() once the validated set and the displayed list are known -- the
    classification cannot happen earlier, because whether a post "contributed"
    depends on the final top-10.
    """

    def __init__(self) -> None:
        self.posts: list[tuple[list[str], list[str]]] = []   # (cashtags, bares)
        self.cashtag_totals: dict[str, int] = {}
        self.bare_totals: dict[str, int] = {}

    def record(self, cashtags: list[str], bares: list[str],
               cashtag_counts: dict[str, int] | None = None,
               bare_counts: dict[str, int] | None = None) -> None:
        """Accumulate one post.

        cashtag_totals counts POSTS, not occurrences, and that asymmetry with
        bare_totals is deliberate -- it makes the two directly comparable.

        The pipeline's mention arithmetic is asymmetric: extract_tickers dedupes
        cashtags within a post (so "$CAT ... $CAT ... $CAT" contributes exactly
        1 mention) but does NOT dedupe bare words (so "CAT ... CAT ... CAT"
        contributes 3). bare_counts already arrives as that mention
        contribution. Counting raw $-occurrences here would put the two halves
        of ticker_provenance in different units, and every comparison between
        them -- the phantom ratio, the cashtags-only ranking -- would be
        silently wrong.
        """
        self.posts.append((list(cashtags), list(bares)))
        # Presence, not frequency: one post contributes one cashtag mention.
        for sym in set(cashtags):
            self.cashtag_totals[sym] = self.cashtag_totals.get(sym, 0) + 1
        for sym, n in (bare_counts or {}).items():
            self.bare_totals[sym] = self.bare_totals.get(sym, 0) + n

    def validatable(self, is_valid) -> tuple[int, int]:
        """Count distinct symbols that WOULD validate, ignoring the 10-cap.

        This is the measurement the whole exercise depends on, and it cannot be
        taken from validated_set. Validation stops at TARGET_VALIDATED=10 and
        walks candidates in mention-rank order -- and bare-word phantoms are
        ordinary English words, the most frequent tokens in any corpus, further
        inflated by the duplicate defect in extract_tickers. They therefore
        consume the 10 slots FIRST, and real cashtag symbols are never checked.

        Measuring the phantom problem with a metric phantoms crowd out would
        answer "can we drop bare-word extraction?" with a confident NO on
        evidence that could never have said anything else.

        `is_valid(symbol) -> bool` is the caller's own validation predicate, so
        this cannot drift from what the scan actually accepts. Pure in-memory
        dict lookups: no API calls, no X spend.

        Returns (all_sources, cashtag_only).
        """
        cash = {s.upper() for s in self.cashtag_totals}
        every = cash | {s.upper() for s in self.bare_totals}
        try:
            return (sum(1 for s in every if is_valid(s)),
                    sum(1 for s in cash if is_valid(s)))
        except Exception as e:
            logger.warning("x_metrics: validatable() predicate failed: %s",
                           type(e).__name__)
            return (0, 0)

    def finalize(self, validated: set[str], displayed: list[str]) -> dict[str, Any]:
        """Partition every processed post into exactly one bucket.

        The four buckets answer different questions and have opposite fixes:

          no_candidates      the query bought a post with no symbol in it at all
                             -- pure waste, fix the QUERY
          no_valid_ticker    symbols found, none survived validation -- the
                             query is finding the wrong securities
          validated_hidden   produced a real in-sector ticker that ranked below
                             the top 10 -- not waste, just more than we needed
          contributed        a displayed ticker came from it

        Conflating the middle two is how you end up tuning the wrong half.
        """
        validated = {t.upper() for t in validated}
        shown = [t.upper() for t in displayed]
        shown_set = set(shown)

        contributed = hidden = no_valid = none_at_all = with_valid_cashtag = 0

        for cashtags, bares in self.posts:
            syms = {s.upper() for s in cashtags} | {s.upper() for s in bares}
            if not syms:
                none_at_all += 1
                continue
            if any(s in validated for s in {c.upper() for c in cashtags}):
                with_valid_cashtag += 1
            valid_here = syms & validated
            if not valid_here:
                no_valid += 1
            elif valid_here & shown_set:
                contributed += 1
            else:
                hidden += 1

        provenance: dict[str, dict[str, Any]] = {}
        for sym in sorted(set(self.cashtag_totals) | set(self.bare_totals)):
            up = sym.upper()
            if up not in validated:
                continue  # keep the blob small: only symbols that survived
            provenance[up] = {
                "cashtag": self.cashtag_totals.get(sym, 0),
                "bare": self.bare_totals.get(sym, 0),
                "displayed": up in shown_set,
            }

        # PHANTOM SUSPECTS. This is a REVIEW QUEUE, not a verdict -- tuned for
        # recall, because a fabricated recommendation shown to a paying user
        # costs more than a analyst-minute spent clearing a false alarm.
        #
        # CALIBRATED AGAINST REAL DATA, after a first attempt got it wrong.
        # The original rule required bare >= 3, tuned against a hypothetical
        # "RAIL x40" case. The first three production scans showed real mention
        # counts are 1 or 2 -- so that threshold could essentially never fire,
        # and it reported zero suspects while DOW sat in the industrials table
        # with cashtag=0, bare=2. "The Dow" is ubiquitous in market chatter and
        # virtually never means Dow Inc. A threshold no reviewer could have
        # checked, because only the data knew the distribution.
        #
        # Two rules now:
        #   no cashtag at all   -- nobody wrote $SYM anywhere in the corpus, so
        #                          there is no independent confirmation that the
        #                          text was about the security. At m=1-2 this
        #                          does flag some genuine tickers; that is the
        #                          intended trade for a queue.
        #   overwhelmingly bare -- one stray $SYM must not whitewash heavy bare
        #                          inflation, which is what the duplicate defect
        #                          amplifies hardest.
        suspects = []
        for t in shown:
            p = provenance.get(t)
            if not p:
                continue  # absent entry is an internal inconsistency, not a phantom
            bare, cash = p.get("bare", 0), p.get("cashtag", 0)
            if bare >= 1 and cash == 0:
                suspects.append(t)
            elif bare >= 3 and bare / max(1, bare + cash) > 0.9:
                suspects.append(t)

        return {
            "posts_processed": len(self.posts),
            "posts_contributed": contributed,
            "posts_validated_hidden": hidden,
            "posts_no_valid_ticker": no_valid,
            "posts_no_candidates": none_at_all,
            "posts_with_valid_cashtag": with_valid_cashtag,
            "distinct_validated": len(validated),
            "displayed": len(shown),
            "phantom_suspects": len(suspects),
            "ticker_provenance": provenance,
            "_suspect_symbols": suspects,   # logged, not stored as its own column
        }


def record_scan(
    event_id: str | None,
    subject: str,
    query: str,
    tally: ScanTally,
    validated: set[str],
    displayed: list[str],
    posts_billed: int,
    pages_fetched: int,
    from_cache: bool,
    is_valid=None,
    stop_reason: str | None = None,
    corpus_key: str | None = None,
) -> bool:
    """Write one row. Returns True on success.

    NEVER RAISES -- the entire body is guarded, not just the network call.

    An earlier version wrapped only finalize() and urlopen(), leaving the
    config read, the row construction and Request() outside any try. A
    SUPABASE_URL missing its scheme raises ValueError there, and because this
    is called from inside the scan's try/finally, that exception would refund
    the credit, show an error banner and discard a results table the user had
    already earned. Telemetry that can destroy the thing it measures is worse
    than no telemetry.
    """
    try:
        return _record_scan(event_id, subject, query, tally, validated,
                            displayed, posts_billed, pages_fetched, from_cache,
                            is_valid, stop_reason, corpus_key)
    except Exception as e:
        logger.warning("x_metrics: record_scan failed: %s: %s",
                       type(e).__name__, str(e)[:200])
        return False


def _record_scan(
    event_id, subject, query, tally, validated, displayed,
    posts_billed, pages_fetched, from_cache, is_valid, stop_reason, corpus_key,
) -> bool:
    summary = tally.finalize(validated, displayed)
    suspects = summary.pop("_suspect_symbols", [])

    uncapped, cashtag_only = tally.validatable(is_valid) if is_valid else (0, 0)
    summary["distinct_validatable"] = uncapped
    summary["distinct_validatable_cashtag_only"] = cashtag_only
    logger.info(
        "📊 scan efficiency: %d processed | %d contributed, %d no-valid, "
        "%d EMPTY | %d displayed | validatable %d (%d cashtag-only) | "
        "%d phantom suspects%s",
        summary["posts_processed"], summary["posts_contributed"],
        summary["posts_no_valid_ticker"], summary["posts_no_candidates"],
        summary["displayed"], uncapped, cashtag_only,
        summary["phantom_suspects"],
        f" ({', '.join(suspects)})" if suspects else "",
    )

    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        logger.warning("x_metrics: Supabase not configured; metrics not persisted")
        return False

    row = {
        "event_id": event_id,
        "kind": "scan",
        "subject": (subject or "").strip().lower(),
        "query_hash": query_hash(query),
        "posts_billed": int(posts_billed),
        "pages_fetched": int(pages_fetched),
        "from_cache": bool(from_cache),
        "stop_reason": stop_reason,
        "corpus_key": corpus_key,
        **summary,
    }

    req = urllib.request.Request(
        f"{base}/rest/v1/{TABLE}",
        data=json.dumps(row).encode(),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status not in (200, 201, 204):
                logger.warning("x_metrics: HTTP %s", r.status)
                return False
    except urllib.error.HTTPError as e:
        logger.warning("x_metrics: HTTP %s: %s", e.code,
                       (e.read() or b"")[:200].decode(errors="replace"))
        return False
    except Exception as e:
        logger.warning("x_metrics: write failed: %s: %s", type(e).__name__, str(e)[:200])
        return False

    return True
