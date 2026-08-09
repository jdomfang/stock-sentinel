#!/usr/bin/env python3
"""Prove the score cache is keyed correctly and cannot corrupt a scan.

WHAT IS AT RISK

1. WRONG KEY = WRONG ANSWER. A cached distribution is served INSTEAD of running
   the model. If the key does not identify the exact string that was scored, the
   cache silently returns another text's opinion -- a correctness bug that looks
   like a performance win. Normalising the text before hashing would do exactly
   that.

2. POISONING. A pre-distribution inference service (and the local dev fallback)
   return zeroed probabilities. Caching those would permanently pin every
   affected text to a fabricated Neutral.

3. BREAKING A PAID SCAN. Every entry point must swallow its errors: a broken
   cache should be slower, never wrong and never fatal.

No network. Supabase is stubbed.

Usage:
    python3 tests/test_sentiment_cache.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import sentiment_cache as sc  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def test_the_key_identifies_the_exact_scored_string():
    print("\nkey: anything but the exact text is a correctness bug")
    a = sc.text_key("$NEE up 3% on the open")
    check("deterministic", a == sc.text_key("$NEE up 3% on the open"))
    check("case matters", a != sc.text_key("$nee up 3% on the open"))
    check("whitespace matters", a != sc.text_key("$NEE up 3% on the open "))
    check("punctuation matters", a != sc.text_key("$NEE up 3% on the open."))
    check("empty is stable", sc.text_key("") == sc.text_key(""))
    # If the key were normalised, these would collide and one text would be
    # served the other's distribution.
    check("no normalisation collapses distinct texts",
          len({sc.text_key(t) for t in
               ["$NEE up", "$NEE  up", "$nee up", " $NEE up"]}) == 4)


def test_a_zeroed_distribution_is_never_stored():
    print("\npoisoning: an all-zero distribution must not be cached")
    # This is exactly what an inference service deployed before top_k=None
    # returns once defaults are filled in, and what the local dev fallback
    # produces. Caching it would pin the text to a fabricated Neutral forever.
    sent = {}
    sc._endpoint = lambda: ("http://x", "k")
    original_put = sc.urllib.request.urlopen
    sc.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not have been called"))
    try:
        ok = sc.put_many({"text": {"p_positive": 0.0, "p_negative": 0.0,
                                   "p_neutral": 0.0}}, "m")
        check("all-zero row is skipped, no request made", ok is False)
    except AssertionError as e:
        check("all-zero row is skipped, no request made", False, str(e))
    finally:
        sc.urllib.request.urlopen = original_put
        sc._endpoint = _REAL_ENDPOINT


_REAL_ENDPOINT = sc._endpoint


def test_a_broken_cache_never_breaks_a_scan():
    print("\nfailure policy: slower, never wrong, never fatal")
    saved = sc._config
    sc._config = lambda name, default="": ""
    try:
        check("unconfigured get returns empty", sc.get_many(["a"], "m") == {})
        check("unconfigured put reports failure",
              sc.put_many({"a": {"p_positive": 1.0, "p_negative": 0.0,
                                 "p_neutral": 0.0}}, "m") is False)
    except Exception as e:
        check("unconfigured cache does not raise", False, f"raised {type(e).__name__}")
    finally:
        sc._config = saved

    sc._config = lambda name, default="": (
        "http://127.0.0.1:9" if name == "SUPABASE_URL" else "key")
    try:
        check("unreachable get returns empty", sc.get_many(["a"], "m") == {})
        check("unreachable put reports failure",
              sc.put_many({"a": {"p_positive": 1.0, "p_negative": 0.0,
                                 "p_neutral": 0.0}}, "m") is False)
    except Exception as e:
        check("unreachable cache does not raise", False, f"raised {type(e).__name__}")
    finally:
        sc._config = saved

    check("no texts is not a request", sc.get_many([], "m") == {})


def test_margin_is_recomputed_not_trusted():
    print("\nintegrity: margin comes from the stored probabilities")
    # The row stores p_positive/p_negative; margin and confidence are derived on
    # read. If a threshold ever changes, a stored derived value would be stale
    # while the probabilities stay true.
    sc._config = lambda name, default="": ("http://x" if name == "SUPABASE_URL" else "k")
    rows = [{"text_sha256": sc.text_key("t"), "p_positive": 0.7,
             "p_negative": 0.2, "p_neutral": 0.1,
             "label": "POSITIVE", "sentiment": "Bullish"}]

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            import json
            return json.dumps(rows).encode()

    saved = sc.urllib.request.urlopen
    sc.urllib.request.urlopen = lambda *a, **k: Resp()
    try:
        got = sc.get_many(["t"], "m")
        check("text is returned", "t" in got, str(list(got)))
        d = got.get("t", {})
        check("margin = p_pos - p_neg", abs(d.get("margin", 0) - 0.5) < 1e-9, str(d))
        check("confidence is the max probability",
              abs(d.get("confidence", 0) - 0.7) < 1e-9, str(d))
    finally:
        sc.urllib.request.urlopen = saved
        sc._config = lambda n, d="": ""


def main() -> int:
    print("=" * 74)
    print("  sentiment_cache: exact keys, no poisoning, safe failure")
    print("=" * 74)
    test_the_key_identifies_the_exact_scored_string()
    test_a_zeroed_distribution_is_never_stored()
    test_a_broken_cache_never_breaks_a_scan()
    test_margin_is_recomputed_not_trusted()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
