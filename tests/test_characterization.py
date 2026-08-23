#!/usr/bin/env python3
"""Pin the current behaviour of the pure scoring functions.

These are CHARACTERIZATION tests, not correctness tests. They assert what the
code does today, not what it ought to do. Several assertions below encode
behaviour that is arguably wrong -- they are marked QUIRK where that is the
case, and they are still asserted, deliberately.

The point is Phase 1. The migration moves FinBERT out of the Streamlit process
into a separate `inference` service, and moves scan/analysis orchestration into
`core-api`. Both rewrites will re-implement the code paths exercised here. With
no pinned baseline, a refactor that quietly changes a threshold, a tie-break, or
a dedup rule ships silently and every recommendation shifts underneath the user.
With one, that shows up as a failing assertion.

So: if a change here fails, the question is not "is the new value better" but
"did I mean to change this". A deliberate change updates the expectation in the
same commit, which is what makes the change reviewable.

All four functions under test are pure -- no network, no model load, no
database. The suite runs in well under a second and needs no fixtures.

Usage:
    python3 tests/test_characterization.py

Exit code 0 = behaviour unchanged. Nonzero = something moved; read the diff.
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# utils.sentiment logs every extraction at DEBUG; silence it so the report reads.
logging.disable(logging.DEBUG)

from utils.deep_analysis import generate_ai_summary          # noqa: E402
from utils.projections import simple_projection              # noqa: E402
from utils.sentiment import extract_tickers, score_finbert_output  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, got, want) -> None:
    if got == want:
        PASSED.append(name)
        print(f"  ok    {name}")
    else:
        FAILED.append((name, f"got {got!r}, want {want!r}"))
        print(f"  FAIL  {name}\n          got  {got!r}\n          want {want!r}")


def close(name: str, got: float, want: float, tol: float = 1e-9) -> None:
    if abs(float(got) - float(want)) <= tol:
        PASSED.append(name)
        print(f"  ok    {name}")
    else:
        FAILED.append((name, f"got {got!r}, want {want!r} (tol {tol})"))
        print(f"  FAIL  {name}\n          got  {got!r}\n          want {want!r}")


# ── score_finbert_output ─────────────────────────────────────────────────────
# (label, confidence) -> (signed_score, trading_sentiment, normalized_label)

def test_score_finbert_output() -> None:
    print("\nscore_finbert_output -- FinBERT label mapping")

    # Case is normalized, so the pipeline's lowercase output works unchanged.
    check("positive lowercase", score_finbert_output("positive", 0.90),
          (0.90, "Bullish", "POSITIVE"))
    check("negative lowercase", score_finbert_output("negative", 0.83),
          (-0.83, "Bearish", "NEGATIVE"))

    # The neutral threshold is INCLUSIVE at 0.55: exactly 0.55 is a live signal.
    # This is the single most load-bearing constant in the product -- it decides
    # whether a tweet counts at all -- so both sides of the boundary are pinned.
    check("threshold 0.55 is bullish", score_finbert_output("POSITIVE", 0.55),
          (0.55, "Bullish", "POSITIVE"))
    check("just under threshold is neutral", score_finbert_output("POSITIVE", 0.5499),
          (0.0, "Neutral", "NEUTRAL"))

    # QUIRK: below threshold, a POSITIVE input is reported as normalized label
    # "NEUTRAL" -- the original model verdict is discarded, not merely zeroed.
    # Anything downstream that counts NEUTRAL labels is also counting
    # low-confidence directional reads.
    check("QUIRK low-confidence POSITIVE reports NEUTRAL label",
          score_finbert_output("POSITIVE", 0.10)[2], "NEUTRAL")

    # A confident NEUTRAL is still neutral: the threshold gates magnitude, the
    # label gates direction.
    check("confident neutral", score_finbert_output("neutral", 0.99),
          (0.0, "Neutral", "NEUTRAL"))

    # Unrecognized and missing labels fail closed to UNKNOWN rather than
    # guessing a direction.
    check("unknown label", score_finbert_output("garbage", 0.90),
          (0.0, "Neutral", "UNKNOWN"))
    check("None label", score_finbert_output(None, 0.90),
          (0.0, "Neutral", "UNKNOWN"))

    # QUIRK: zero confidence with a POSITIVE label degrades to NEUTRAL, not
    # UNKNOWN -- the label was recognized, only the confidence was absent.
    check("zero confidence", score_finbert_output("POSITIVE", 0.0),
          (0.0, "Neutral", "NEUTRAL"))


# ── extract_tickers ──────────────────────────────────────────────────────────

def test_extract_tickers() -> None:
    print("\nextract_tickers -- ticker candidate extraction")

    check("dollar-prefixed", extract_tickers("$RGNT Advances Regenerative Solutions"),
          ["RGNT"])
    check("dedupes repeats", extract_tickers("$AAPL $AAPL $MSFT duplicate check"),
          ["AAPL", "MSFT"])
    check("no tickers", extract_tickers("no tickers here at all"), [])

    # Length bounds: 1..5 uppercase for CASHTAGS. A 13-letter run still matches
    # nothing because \b cannot fall inside it.
    #
    # CHANGED DELIBERATELY. The lower bound was 2, which made all 21
    # single-letter symbols in ticker_master unextractable -- $T (AT&T), $F
    # (Ford), $V (Visa), $C (Citigroup), $D (Dominion). Measured on a live
    # utilities corpus: $D was in the query we sent, X returned posts mentioning
    # it, and extraction silently discarded posts we had already paid for. In
    # Telecommunications $T is plausibly the most-discussed name in the sector
    # and was invisible to every scan ever run.
    #
    # Only the cashtag pattern moved. Bare words stay at 2+, because at one
    # character every "A" and "I" in ordinary English becomes a candidate.
    check("length bounds", extract_tickers("$TOOLONGTICKER and $A and $AB"), ["A", "AB"])

    # QUIRK, and an expensive one. Bare uppercase words are treated as ticker
    # candidates, and the 366-entry exclusion list does not contain common
    # all-caps chatter. Every word returned here becomes a FinBERT inference
    # during a Discovery scan, so this is not just noisy output -- it is the
    # dominant cost driver on a memory-constrained box.
    #
    # Note the ORDER: results come back ranked by a desirability score that
    # prefers 3-4 letter words, NOT in source order (FRAUD appears before LIES
    # in the input and last in the output). Any reimplementation that preserves
    # source order changes which tickers survive downstream truncation.
    check("QUIRK all-caps chatter extracted as tickers",
          extract_tickers("WTF this is DEAD, total FRAUD and LIES. STOP"),
          ["WTF", "DEAD", "LIES", "STOP", "FRAUD"])

    # Genuine tickers and excluded words in one line: CEO and USA are excluded,
    # BIG is not.
    check("QUIRK BIG survives while CEO/USA are excluded",
          extract_tickers("AAPL and MSFT are up, CEO said USA growth is BIG"),
          ["AAPL", "MSFT", "BIG"])


# ── generate_ai_summary ──────────────────────────────────────────────────────

def test_generate_ai_summary() -> None:
    print("\ngenerate_ai_summary -- Buy/Watch/Avoid recommendation")

    # Fails safe on missing input rather than raising into the page.
    for label, arg in (("empty dict", {}), ("None", None)):
        out = generate_ai_summary(arg)
        check(f"{label} -> Watch/Low/0.0",
              (out["recommendation"], out["confidence"], out["avg_sentiment"]),
              ("Watch", "Low", 0.0))

    # THE load-bearing behaviour: prompts are weighted by how many tweet ids
    # they contribute that no earlier prompt already used. The same tweet
    # surfacing under several prompts must not inflate confidence.
    #
    # disjoint: bullish weight 3, bearish weight 2
    #           (0.7*3 + -0.2*2) / 5 = 0.34
    # overlap:  bearish shares id "3", so it contributes 1 new id, weight 1
    #           (0.7*3 + -0.2*1) / 4 = 0.475
    #
    # Overlap scoring HIGHER is the whole point: the bearish read was partly a
    # duplicate, so it earns less say. Lose this and duplicate press-release
    # spam starts driving recommendations.
    disjoint = {
        "a": {"tweet_ids": ["1", "2", "3"], "sentiment_score": 0.7, "overall_sentiment": "bullish", "mention_count": 3},
        "b": {"tweet_ids": ["4", "5"], "sentiment_score": -0.2, "overall_sentiment": "bearish", "mention_count": 2},
    }
    overlap = {
        "a": {"tweet_ids": ["1", "2", "3"], "sentiment_score": 0.7, "overall_sentiment": "bullish", "mention_count": 3},
        "b": {"tweet_ids": ["3", "4"], "sentiment_score": -0.2, "overall_sentiment": "bearish", "mention_count": 2},
    }
    close("dedup: disjoint ids weight 3/2", generate_ai_summary(disjoint)["avg_sentiment"], 0.34)
    close("dedup: shared id drops weight to 1", generate_ai_summary(overlap)["avg_sentiment"], 0.475)

    # Thresholds for the actual verdict. Four prompts, ten unique tweets each.
    strong_bull = {
        f"p{i}": {"tweet_ids": [str(j) for j in range(i * 10, i * 10 + 10)],
                  "sentiment_score": 0.85, "overall_sentiment": "bullish", "mention_count": 10}
        for i in range(4)
    }
    out = generate_ai_summary(strong_bull)
    check("strong bullish -> Buy/High",
          (out["recommendation"], out["confidence"]), ("Buy", "High"))
    close("strong bullish avg", out["avg_sentiment"], 0.85)

    strong_bear = {
        f"p{i}": {"tweet_ids": [str(j) for j in range(i * 10, i * 10 + 10)],
                  "sentiment_score": -0.8, "overall_sentiment": "bearish", "mention_count": 10}
        for i in range(4)
    }
    out = generate_ai_summary(strong_bear)
    check("strong bearish -> Avoid/High",
          (out["recommendation"], out["confidence"]), ("Avoid", "High"))
    close("strong bearish avg", out["avg_sentiment"], -0.80)

    # A confident score on thin evidence must NOT read as high confidence.
    thin = {"a": {"tweet_ids": ["1"], "sentiment_score": 0.7, "overall_sentiment": "bullish", "mention_count": 1}}
    check("thin evidence is not High confidence",
          generate_ai_summary(thin)["confidence"] != "High", True)

    # DELIBERATELY WIDENED, 2026-08-22. red_flag_rate and disagreement were
    # already computed inside this function and thrown away, so the legacy
    # verdict_log row stored NULL -- "not measured" -- for two real readings.
    # They are returned now. The lock is kept exact rather than loosened to a
    # subset: the next key to appear should have to justify itself here too.
    check("returns the documented keys",
          sorted(generate_ai_summary(disjoint).keys()),
          ["avg_sentiment", "confidence", "disagreement", "rationale",
           "recommendation", "red_flag_rate"])
    # The two new keys must be readings, not placeholders.
    _rf = generate_ai_summary(disjoint)
    check("red_flag_rate is a rate", 0.0 <= _rf["red_flag_rate"] <= 1.0, True)
    check("disagreement is a spread", _rf["disagreement"] >= 0.0, True)


# ── simple_projection ────────────────────────────────────────────────────────

def test_simple_projection_determinism() -> None:
    print("\nsimple_projection -- Monte Carlo determinism")

    prices = [100.0, 101.5, 99.8, 102.3, 103.1, 101.9, 104.0, 105.2, 103.7, 106.1]

    # Same question, same answer. This drew from an unseeded global np.random
    # until 2026-08-01, so the projected gain changed on every Streamlit rerun
    # with no input change -- a number that looked measured but was a coin flip.
    runs = [simple_projection(prices, 0.35) for _ in range(3)]
    check("identical inputs give identical output", runs[0] == runs[1] == runs[2], True)

    # Different tickers must not replay one shared random path. A constant seed
    # would pass the test above and silently correlate every projection.
    #
    # Probed by changing the SHAPE of the series. Scaling every close by a
    # constant no longer changes anything, and that is now correct rather than
    # broken: the model reads volatility, and 1.07 * every price leaves the
    # return series bit-identical. A stock is no more volatile at $107 than at
    # $100. The old version centred on recent drift, which is why level used to
    # leak into the answer.
    other = simple_projection([p * (1.0 + 0.02 * (i % 3)) for i, p in enumerate(prices)], 0.35)
    check("different price SHAPE gives different output",
          other["band"] != runs[0]["band"], True)
    scaled = simple_projection([p * 1.07 for p in prices], 0.35)
    check("uniform scaling is deliberately invariant",
          scaled["band"] == runs[0]["band"], True)

    # Sentiment still has to move the drift, or the seeding broke the model.
    bear = simple_projection(prices, -1.0)
    bull = simple_projection(prices, 1.0)
    check("bullish projects above bearish", bull["avg_gain"] > bear["avg_gain"], True)

    # An explicit seed overrides the derived one, for tests that need a fixed path.
    check("explicit seed is reproducible",
          simple_projection(prices, 0.35, seed=7) == simple_projection(prices, 0.35, seed=7),
          True)
    check("different seeds diverge",
          simple_projection(prices, 0.35, seed=7) != simple_projection(prices, 0.35, seed=8),
          True)

    # Guard rail from the original implementation: too few points, no projection.
    check("insufficient data returns an error",
          simple_projection([100.0, 101.0], 0.5).get("error"), "Insufficient price data")


def test_label_vocabularies():
    """Swapping the sentiment model must not silently zero every score.

    FinBERT emits positive/neutral/negative; FinTwitBERT, trained on financial
    Twitter rather than financial news, emits BULLISH/NEUTRAL/BEARISH. Mapping
    an unrecognised label to a confident 0.0 would make the app report "not
    enough clean evidence about this company" for every ticker on earth, with
    no error anywhere -- and the output would read as an honest finding.
    """
    from utils.evidence import directional_margin
    from utils.sentiment import score_finbert_output as sco

    print("\nlabel vocabularies -- two models, three classes, different names")
    for lab in ("positive", "POSITIVE", "BULLISH", "bullish"):
        check(f"{lab} -> Bullish", sco(lab, 0.9)[1], "Bullish")
    for lab in ("negative", "BEARISH", "bearish"):
        check(f"{lab} -> Bearish", sco(lab, 0.9)[1], "Bearish")
    for lab in ("neutral", "NEUTRAL"):
        check(f"{lab} -> Neutral", sco(lab, 0.9)[1], "Neutral")
    # Distinguishable from a genuine neutral by anything that inspects it.
    check("an unrecognised label is UNKNOWN", sco("banana", 0.9)[2], "UNKNOWN")

    # The per-post direction threshold is a property of the MODEL. One
    # hardcoded number cannot serve two models whose confidence distributions
    # differ by more than 4x.
    check("finbert threshold", directional_margin("ProsusAI/finbert"), 0.15)
    check("fintwitbert threshold",
          directional_margin("StephanAkkerman/FinTwitBERT-sentiment"), 0.70)
    check("unknown model gets a conservative default",
          directional_margin("who/knows"), 0.50)


def main() -> int:
    print("=" * 74)
    print("  Characterization suite -- pins behaviour ahead of the Phase 1 rewrite")
    print("=" * 74)

    for t in (test_score_finbert_output, test_extract_tickers,
              test_generate_ai_summary, test_simple_projection_determinism,
              test_label_vocabularies):
        try:
            t()
        except Exception as e:  # a crashing test is a failing test
            FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")

    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("\n  Behaviour changed. If that was deliberate, update the")
        print("  expectation in the same commit as the change:")
        for name, detail in FAILED:
            print(f"    - {name}: {detail}")
    else:
        print("\n  Behaviour unchanged.")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
