#!/usr/bin/env python3
"""Report what paid X calls actually bought, grouped by query version.

    python3 scripts/x_efficiency_report.py [--days 30]

Reads public.x_call_metrics. Costs nothing -- every number was derived from
posts already paid for.

READING THE OUTPUT

  WASTE%     posts bought that produced no ticker at all. The query's fault.
             The one hand-audited scan measured ~50%.

  NOVALID%   posts that produced symbols, none of which survived validation.
             Different failure, opposite fix: the query is finding stock
             chatter about the wrong securities rather than nothing at all.
             Conflating this with WASTE is how you tune the wrong half.

  DISTINCT   distinct validated tickers per scan. This is the number that
             decides whether fewer posts is even possible: if a cleaner query
             raises precision but drops this below 10, precision was never the
             binding constraint and cutting max_results will just force a
             second page.

  PHANTOM    displayed tickers never once seen with a $ anywhere in the corpus.
             AIR (AAR Corp), RAIL (FreightCar America) and BOOM (DMC Global)
             are real Industrials symbols AND ordinary English words, so they
             validate and render exactly like CAT. Non-zero here means users
             were shown recommendations that probably are not real.

Query versions are content-addressed, so editing a query automatically starts a
new row here. Comparing two hashes is the whole point: it turns "did that edit
help?" into a lookup instead of an argument.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.x_metrics import _config  # noqa: E402


def fetch(days: int) -> list[dict]:
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
        sys.exit(2)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    qs = urllib.parse.urlencode({
        "select": "*", "kind": "eq.scan", "created_at": f"gte.{since}",
        "order": "created_at.desc",
    })
    req = urllib.request.Request(f"{base}/rest/v1/x_call_metrics?{qs}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.loads(r.read() or b"[]")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("Table public.x_call_metrics does not exist yet.")
            print("Apply supabase/migrations/20260806010000_x_call_metrics.sql "
                  "in the Supabase SQL editor.")
            sys.exit(2)
        print(f"Supabase HTTP {e.code}: {(e.read() or b'')[:200].decode(errors='replace')}")
        sys.exit(2)
    except Exception as e:
        print(f"Could not reach Supabase: {type(e).__name__}: {str(e)[:160]}")
        sys.exit(2)
    if not isinstance(rows, list):
        print(f"Unexpected response: {str(rows)[:200]}")
        sys.exit(2)
    return rows


def pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:5.1f}%" if d else "    -"


def main() -> int:
    days = 30
    if "--days" in sys.argv:
        i = sys.argv.index("--days") + 1
        if i >= len(sys.argv) or not sys.argv[i].isdigit():
            print("usage: x_efficiency_report.py [--days N]")
            return 2
        days = int(sys.argv[i])

    rows = fetch(days)
    if not rows:
        print(f"No scans recorded in the last {days} days.")
        print("Run a Discovery scan, then re-run this.")
        return 0

    # COLLAPSE CACHE REPLAYS FIRST.
    #
    # A corpus is shared for 6 hours, so five users scanning industrials write
    # five rows over ONE corpus with near-identical buckets. Pooling them would
    # weight every rate by cache popularity rather than by evidence, and inflate
    # confidence by the replay factor. One corpus, one observation.
    seen: set[str] = set()
    independent: list[dict] = []
    replays = 0
    for r in sorted(rows, key=lambda r: r["created_at"]):
        ck = r.get("corpus_key")
        if ck and ck in seen:
            replays += 1
            continue
        if ck:
            seen.add(ck)
        independent.append(r)
    if replays:
        print(f"({replays} cache replays collapsed -- {len(independent)} "
              f"independent corpora)")
    rows = independent

    by_query: dict[str, list[dict]] = {}
    for r in rows:
        by_query.setdefault(r["query_hash"], []).append(r)

    print(f"\nX call efficiency -- {len(rows)} scans, last {days} days\n")
    print(f"{'QUERY':<14}{'SCANS':>6}{'BILLED':>8}{'WASTE%':>8}{'NOVALID%':>10}"
          f"{'USED%':>8}{'DISTINCT':>10}{'PHANTOM':>9}")
    print("-" * 73)

    for qh, group in sorted(by_query.items(), key=lambda kv: -len(kv[1])):
        processed = sum(r["posts_processed"] for r in group)
        billed = sum(r["posts_billed"] for r in group)
        waste = sum(r["posts_no_candidates"] for r in group)
        novalid = sum(r["posts_no_valid_ticker"] for r in group)
        used = sum(r["posts_contributed"] for r in group)
        distinct = sum(r["distinct_validated"] for r in group) / len(group)
        phantom = sum(r["phantom_suspects"] for r in group) / len(group)
        print(f"{qh:<14}{len(group):>6}{billed:>8}{pct(waste, processed):>8}"
              f"{pct(novalid, processed):>10}{pct(used, processed):>8}"
              f"{distinct:>10.1f}{phantom:>9.1f}")

    # DECISION 1: can bare-word extraction be turned off?
    #
    # Answered from the UNCAPPED counts, never from the displayed list. The
    # displayed list is clipped at 10 by the early stop, and validation walks
    # candidates in mention-rank order -- bare-word phantoms are ordinary
    # English words, so they are the most frequent tokens and consume all ten
    # slots first. Reading "how many displayed tickers had a cashtag" would
    # therefore answer the question with data that phantoms crowded out, and it
    # would answer it in the wrong direction every time.
    print()
    n = len(rows)
    if n:
        v = sum(r.get("distinct_validatable") or 0 for r in rows) / n
        vc = sum(r.get("distinct_validatable_cashtag_only") or 0 for r in rows) / n
        print(f"Distinct tickers that WOULD validate, per scan (uncapped):")
        print(f"  all sources    {v:5.1f}")
        print(f"  cashtags only  {vc:5.1f}   <- what survives with bare-word "
              f"extraction off")
        if vc >= 10:
            print("  -> 10 slots still fill. Turning bare-word extraction off is safe.")
        elif v > 0:
            print(f"  -> below 10: the results table would shrink. Bare-word")
            print(f"     extraction is load-bearing; fix the phantoms another way.")

    censored = sum(1 for r in rows if r.get("stop_reason") == "validated_target")
    if censored:
        print(f"\n{censored}/{n} scans stopped early at 10 tickers, so their")
        print("corpora are prefixes. Treat the counts above as LOWER bounds.")

    suspects: dict[str, int] = {}
    for r in rows:
        for sym, p in (r.get("ticker_provenance") or {}).items():
            bare, cash = p.get("bare", 0), p.get("cashtag", 0)
            if p.get("displayed") and bare >= 3 and bare / max(1, bare + cash) > 0.9:
                suspects[sym] = suspects.get(sym, 0) + 1

    if suspects:
        top = sorted(suspects.items(), key=lambda kv: -kv[1])[:12]
        print("\nMost frequent phantom suspects "
              "(displayed, >90% bare evidence, >=3 mentions):")
        print("  " + ", ".join(f"{s} x{c}" for s, c in top))
        print("  -> these are candidates for a manual audit, not proof.")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
