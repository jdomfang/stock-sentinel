#!/usr/bin/env python3
"""Replay stored corpora through extraction variants. Costs nothing.

    python3 scripts/x_variant_sweep.py

WHY THIS IS FREE

utils/corpus_cache.py stores the RAW posts of every scan, so the tweets are
already bought and permanently ours. Changing how we READ them costs nothing;
only changing which posts we ASK X FOR costs money. Every variant below is a
reading change, so this sweeps them all across every stored corpus for zero
posts and can be re-run as often as we like.

The dividing line matters and is easy to get wrong:

    extraction / validation changes   ->  free, replayable here
    query changes (has:cashtags, ...) ->  need a new fetch, NOT testable here

WHAT IT ANSWERS

The blocking question is whether bare-uppercase ticker extraction can be turned
off. It reads ordinary English words as tickers, and 17 of 20 common words
tested are real listed symbols -- DOW is Dow Inc., and "the Dow" almost never
means Dow Inc. But turning it off may leave too few tickers to fill ten slots.

Measured on the first three scans: cashtags-only yields 25 validatable tickers
in tech (no loss) but only 7 in industrials and 7 in utilities, against a target
of 10. So the real choice is not on/off -- it is which middle option keeps the
tickers while dropping the fabrications. That is what this sweeps.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.sentiment import EXCLUDED_WORDS  # noqa: E402
from utils.x_metrics import _config  # noqa: E402

TARGET_VALIDATED = 10

# Copied from pages/Discovery.py. Must stay in step with it, or this harness
# validates by a different rule than the product and its answers are fiction.
UI_TO_NASDAQ = {
    "tech": {"Technology"}, "healthcare": {"Health Care"}, "energy": {"Energy"},
    "finance": {"Finance"},
    "consumer": {"Consumer Discretionary", "Consumer Staples"},
    "utilities": {"Utilities"}, "real estate": {"Real Estate"},
    "industrials": {"Industrials"}, "materials": {"Basic Materials"},
    "communication": {"Telecommunications"},
}

# Ordinary English words that are also real listed symbols. Confirmed against
# ticker_master, and every one of them can be minted as a recommendation by
# prose that never mentioned the company.
COLLISION_WORDS = {
    "AIR", "RAIL", "SHIP", "BOOM", "JOB", "PMI", "ISM", "ITA", "OPEN", "LIVE",
    "NOW", "CARS", "TECH", "REAL", "MOVE", "NEXT", "LNG", "EPS", "TOP", "PUMP",
    "DOW", "CC", "WRAP", "NI", "BEP", "PLAY", "KEY", "ALL", "GO", "CAR", "IT",
}


def api(path: str):
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set"); sys.exit(2)
    req = urllib.request.Request(f"{base}/rest/v1/{path}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"[]")
    except urllib.error.HTTPError as e:
        print(f"Supabase HTTP {e.code}: {(e.read() or b'')[:200].decode(errors='replace')}")
        sys.exit(2)


def ticker_master() -> dict[str, str]:
    out, off = {}, 0
    while True:
        rows = api(f"ticker_master?select=symbol,sector&offset={off}&limit=1000")
        for r in rows:
            if r.get("symbol"):
                out[r["symbol"].upper()] = (r.get("sector") or "").strip()
        if len(rows) < 1000:
            return out
        off += 1000


# ── extraction variants ──────────────────────────────────────────────────────
#
# Each returns (cashtag_symbols, bare_symbols_with_repeats) for ONE post.
# bare keeps repeats because the live pipeline counts them as separate mentions
# (the preserved duplicate defect), and mention count drives the ranking -- a
# variant scored without that would not be the variant we would ship.

def _cashtags(text: str, blocklist) -> list[str]:
    out, seen = [], set()
    for m in re.findall(r'\$([A-Z]{2,5})\b', text):
        if m not in blocklist and m not in seen:
            out.append(m); seen.add(m)
    return out


def _bare(text: str, blocklist, cap: int, claimed: set) -> list[str]:
    scored = []
    for m in re.findall(r'\b([A-Z]{2,5})\b', text):
        if m not in blocklist and m not in claimed:
            scored.append((m, 1.0 if 3 <= len(m) <= 4 else 0.5))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[:cap]]


def make_variant(cap: int, extra_blocked: set | None = None):
    blocklist = EXCLUDED_WORDS | (extra_blocked or set())

    def run(text: str):
        cash = _cashtags(text, blocklist)
        bare = _bare(text, blocklist, cap, set(cash))
        return cash, bare
    return run


VARIANTS = {
    "baseline (cash + 5 bare)": make_variant(5),
    "bare cap 1":               make_variant(1),
    "blocklist +31 words":      make_variant(5, COLLISION_WORDS),
    "blocklist + cap 1":        make_variant(1, COLLISION_WORDS),
    "cashtags only":            make_variant(0),
}


def evaluate(posts, variant, valid) -> dict:
    """Replay one corpus through one variant, mirroring the live pipeline."""
    mentions: dict[str, int] = {}
    cash_n: dict[str, int] = {}
    bare_n: dict[str, int] = {}
    empty = no_valid = contributed = 0
    per_post = []

    for tw in posts:
        cash, bare = variant((tw.get("text") or "")[:512])
        syms = cash + bare                       # the live pipeline's mention unit
        per_post.append(set(s.upper() for s in syms))
        if not syms:
            empty += 1
        for s in syms:
            mentions[s] = mentions.get(s, 0) + 1
        for s in set(cash):
            cash_n[s] = cash_n.get(s, 0) + 1
        for s in bare:
            bare_n[s] = bare_n.get(s, 0) + 1

    validatable = sorted((s for s in mentions if valid(s)),
                         key=lambda s: -mentions[s])
    shown = validatable[:TARGET_VALIDATED]
    shown_set = set(shown)

    for syms in per_post:
        if not syms:
            continue
        if syms & shown_set:
            contributed += 1
        elif not any(valid(s) for s in syms):
            no_valid += 1

    suspects = [s for s in shown
                if bare_n.get(s, 0) >= 1 and cash_n.get(s, 0) == 0]

    return {
        "processed": len(posts), "empty": empty, "no_valid": no_valid,
        "contributed": contributed, "validatable": len(validatable),
        "displayed": len(shown), "suspects": suspects,
        "top": [(s, mentions[s], cash_n.get(s, 0), bare_n.get(s, 0)) for s in shown],
    }


def main() -> int:
    corpora = api("x_corpus_cache?select=subject,tweets,tweet_count&kind=eq.sector")
    if not corpora:
        print("No stored corpora. Run a Discovery scan first.")
        return 0

    master = ticker_master()
    print(f"\n{len(corpora)} corpora, {sum(c['tweet_count'] for c in corpora)} posts "
          f"-- replayed at zero cost\n")

    for c in corpora:
        sector = c["subject"]
        allowed = UI_TO_NASDAQ.get(sector, set())
        if not allowed:
            print(f"!! no sector mapping for {sector!r}; skipping")
            continue

        def valid(sym, _a=allowed):
            return master.get(sym.upper()) in _a

        posts = c["tweets"] or []
        print(f"── {sector}  ({len(posts)} posts) " + "─" * (46 - len(sector)))
        print(f"  {'variant':<26}{'EMPTY':>7}{'NOVALID':>9}{'USED':>7}"
              f"{'VALIDATABLE':>13}{'SHOWN':>7}{'SUSPECT':>9}")
        for name, fn in VARIANTS.items():
            r = evaluate(posts, fn, valid)
            n = r["processed"]
            print(f"  {name:<26}{100*r['empty']/n:>6.0f}%{100*r['no_valid']/n:>8.0f}%"
                  f"{100*r['contributed']/n:>6.0f}%{r['validatable']:>13}"
                  f"{r['displayed']:>7}{len(r['suspects']):>9}"
                  + ("  " + ",".join(r["suspects"][:4]) if r["suspects"] else ""))
        print()

    print("Reading it:")
    print("  VALIDATABLE  distinct in-sector tickers the variant could find, uncapped.")
    print("               Below 10 means the results table shrinks.")
    print("  SUSPECT      shown tickers with bare-only evidence -- a review queue,")
    print("               not a verdict. Genuine low-mention tickers land here too.")
    print("  Query changes (has:cashtags) are NOT testable here: they change which")
    print("  posts X returns, so they need a real fetch.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
