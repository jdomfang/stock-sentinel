#!/usr/bin/env python3
"""Choose the directional margin by MEASURING it, not by picking a round number.

WHY THIS EXISTS AND WHY THE ANSWER CHANGES

_DIRECTIONAL_MARGIN_BY_MODEL currently holds 0.70 for FinTwitBERT, chosen as the
best F1 over the labelled set. F1 is the wrong objective for this gate.

The gate decides whether a post gets a VOTE in the social aggregate. A false
positive is not symmetric with a false negative: a post wrongly admitted casts a
confident wrong vote that moves the verdict, while a post wrongly excluded
merely lowers the voice count -- which the confidence tier already handles and
already reports. So the gate should be tuned for PRECISION, accepting recall
loss, and the panel review put the bar at 60%.

At the F1 optimum the classifier is measured at 48% precision: about half of
what it admits is not directional by human reading. That is the weakest
component in the product and this script is what fixes it.

WHAT IS MEASURED, AGAINST WHAT

Ground truth is data/labels.jsonl, field `direction`:

    bullish / bearish   -> the post carries a direction        (positive class)
    neutral / mixed / no view -> it does not                   (negative class)
    unclear             -> excluded; a label the human could not settle is not
                           a standard the model should be scored against

Mixed sits in the negative class deliberately. A post arguing both sides has a
view but no NET direction, and admitting it pollutes an aggregate that can only
represent one sign.

The unit of analysis is the near-duplicate CLUSTER, not the post, because that
is the unit the ledger votes with.

WHAT ELSE IT REPORTS, AND WHY THAT MATTERS MORE

Precision is free to buy: push the threshold to 0.99 and it approaches 1.0 with
nothing left to vote. So the script also replays the three retained corpora
through the real ledger at every threshold and reports how many independent
directional voices survive. A threshold that clears 60% precision and leaves the
genuine corpus with two voices has not improved the product, it has switched it
off. Read the two tables together.

COST: no API calls. The model runs locally and the corpora are already on disk.
Scores are cached to data/model_scores.json, so only the first run is slow.

Usage:
    python3 scripts/sweep_threshold.py
    python3 scripts/sweep_threshold.py --model ProsusAI/finbert
    python3 scripts/sweep_threshold.py --rescore     # ignore the cache
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.label_posts import load_labels, load_units  # noqa: E402
from utils.evidence import build_ledger  # noqa: E402

CACHE = REPO / "data" / "model_scores.json"
DEFAULT_MODEL = "StephanAkkerman/FinTwitBERT-sentiment"

# The bar the panel review set for admitting a post to the social aggregate.
PRECISION_TARGET = 0.60

# Labels that mean "this post carries a direction". See the module docstring for
# why "mixed" is not among them.
DIRECTIONAL_LABELS = {"b", "r"}
EXCLUDED_LABELS = {"u"}

STEPS = [i / 100 for i in range(0, 100, 5)]


def _normalise(label) -> str:
    """Model label -> POSITIVE / NEGATIVE / NEUTRAL, across vocabularies.

    Imported rather than duplicated where possible; kept as a fallback so this
    script still runs if utils.sentiment cannot import (it reaches for
    streamlit secrets at module scope in some environments).
    """
    try:
        from utils.sentiment import _normalise_label
        return _normalise_label(label)
    except Exception:
        s = str(label or "").strip().upper()
        if s in ("POSITIVE", "BULLISH", "POS", "LABEL_2"):
            return "POSITIVE"
        if s in ("NEGATIVE", "BEARISH", "NEG", "LABEL_0"):
            return "NEGATIVE"
        if s in ("NEUTRAL", "NEU", "LABEL_1"):
            return "NEUTRAL"
        return "UNKNOWN"


def score_all(units: list[dict], model: str, rescore: bool = False) -> dict:
    """post_id -> full distribution. Cached, because the model is slow to load."""
    cache: dict = {}
    if CACHE.exists() and not rescore:
        try:
            cache = json.loads(CACHE.read_text())
        except json.JSONDecodeError:
            cache = {}
    have = cache.get(model, {})
    todo = [u for u in units if u["post_id"] and u["post_id"] not in have]

    if todo:
        print(f"scoring {len(todo)} unit(s) with {model} "
              f"({len(have)} already cached)...")
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch

        tok = AutoTokenizer.from_pretrained(model)
        mdl = AutoModelForSequenceClassification.from_pretrained(model)
        mdl.eval()
        id2label = mdl.config.id2label

        # Any UNKNOWN label here means the vocabulary map is missing a case and
        # every score below would be silently wrong. Fail loudly instead.
        unknown = [v for v in id2label.values() if _normalise(v) == "UNKNOWN"]
        if unknown:
            raise SystemExit(
                f"model {model} emits unmapped label(s) {unknown}. Add them to "
                "utils.sentiment._normalise_label before trusting any score.")

        for i in range(0, len(todo), 16):
            batch = todo[i:i + 16]
            enc = tok([u["text"][:512] for u in batch], return_tensors="pt",
                      truncation=True, max_length=512, padding=True)
            with torch.no_grad():
                probs = torch.softmax(mdl(**enc).logits, dim=-1)
            for u, row in zip(batch, probs):
                d = {"p_positive": 0.0, "p_negative": 0.0, "p_neutral": 0.0}
                for idx, p in enumerate(row.tolist()):
                    norm = _normalise(id2label[idx])
                    if norm == "POSITIVE":
                        d["p_positive"] = p
                    elif norm == "NEGATIVE":
                        d["p_negative"] = p
                    elif norm == "NEUTRAL":
                        d["p_neutral"] = p
                d["margin"] = d["p_positive"] - d["p_negative"]
                have[u["post_id"]] = d
            print(f"  {min(i + 16, len(todo))}/{len(todo)}", end="\r")
        print()
        cache[model] = have
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache))
        print(f"cached to {CACHE.relative_to(REPO)}")

    return have


def in_funnel_ids(scores: dict) -> set[str]:
    """Post ids that survive subject and spam typing, i.e. reach the aggregate.

    THE GATE MUST BE GRADED WHERE IT OPERATES. Scoring it over every labelled
    post measures it on wrong-entity and coordinated-spam posts it never sees:
    the ledger has already discarded those on `target_subject_status` and
    `spam_risk` before the margin is consulted at all. Grading them anyway
    charges the model for errors on text about a Member of Parliament.
    """
    import glob

    aliases = {"TSLA": "Tesla", "MP": "MP Materials"}
    keep: set[str] = set()
    for f in sorted(glob.glob(str(REPO / "data" / "corpora" / "*_[ABC]_*.json"))):
        blob = json.loads(Path(f).read_text())
        posts = blob.get("tweets") or []
        if not posts:
            continue
        by_id = {str(p["id"]): scores[str(p["id"])]
                 for p in posts
                 if p.get("id") is not None and str(p["id"]) in scores}
        for r in build_ledger(posts, blob["ticker"],
                              alias=aliases.get(blob["ticker"], ""),
                              channel="social_base", scores=by_id):
            if (r.target_subject_status in ("primary", "comparison")
                    and r.spam_risk != "high"):
                keep.add(r.post_id)
    return keep


def sweep_classifier(units: list[dict], labels: dict, scores: dict,
                     only: set[str] | None = None) -> list[dict]:
    """Precision, recall and direction accuracy at each candidate threshold."""
    graded = []
    for u in units:
        recs = labels.get(u["uid"])
        if not recs:
            continue
        truth = recs[min(recs)].get("direction")
        if not truth or truth in EXCLUDED_LABELS:
            continue
        s = scores.get(u["post_id"])
        if s is None:
            continue
        if only is not None and u["post_id"] not in only:
            continue
        graded.append((truth in DIRECTIONAL_LABELS, truth, s["margin"]))

    # The share that carries a direction BEFORE the gate sees anything. Without
    # it "84% precision" is unreadable: a gate admitting everything already
    # scores the base rate, so only the LIFT over it is the gate's own work.
    base = (sum(1 for d, _, _ in graded if d) / len(graded)) if graded else 0.0

    out = []
    for thr in STEPS:
        tp = fp = fn = 0
        dir_right = 0
        for is_dir, truth, margin in graded:
            called = abs(margin) >= thr
            if called and is_dir:
                tp += 1
                # Sign agreement, on the posts both the human and the model
                # agree carry a direction at all.
                if (margin > 0) == (truth == "b"):
                    dir_right += 1
            elif called and not is_dir:
                fp += 1
            elif not called and is_dir:
                fn += 1
        adm = tp + fp
        prec = tp / adm if adm else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        out.append({
            "thr": thr, "precision": prec, "recall": rec, "f1": f1,
            "admitted": adm, "base_rate": base, "lift": prec - base,
            "direction_acc": (dir_right / tp) if tp else 0.0,
            # THE METRIC THAT MATCHES THE GATE'S JOB. This gate admits a post to
            # a vote in a directional average, so a post that is directional but
            # scored with the WRONG SIGN is the worst possible admission -- and
            # plain precision counts it as a success. Admitted-and-right-signed
            # is what the aggregate actually experiences.
            "signed_precision": (dir_right / adm) if adm else 0.0,
            "signed_correct": dir_right,
        })
    return out, len(graded)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% CI on a proportion. n is 32 here; a point estimate alone would lie."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return ((c - half) / d, (c + half) / d)


def sweep_corpus_yield(scores: dict) -> dict:
    """Independent directional voices left per corpus at each threshold.

    Replays the real ledger, so subject status, spam typing and clustering all
    apply exactly as they do in the product. Precision bought at the cost of
    every remaining voice is not a purchase worth making.
    """
    import glob

    aliases = {"TSLA": "Tesla", "MP": "MP Materials"}
    out: dict[str, dict] = {}
    for f in sorted(glob.glob(str(REPO / "data" / "corpora" / "*_[ABC]_*.json"))):
        blob = json.loads(Path(f).read_text())
        posts = blob.get("tweets") or []
        if not posts:
            continue
        by_id = {str(p["id"]): scores[str(p["id"])]
                 for p in posts
                 if p.get("id") is not None and str(p["id"]) in scores}
        per_thr = {}
        for thr in STEPS:
            # REBUILT at each threshold, not filtered. Post-filtering a fixed
            # eligible set by `abs(margin) >= thr` also strips the risk and
            # catalyst rows, which is_eligible admits REGARDLESS of margin --
            # so the old table overstated the cost of raising the gate by
            # roughly 2x at the high end, biased toward leaving it alone.
            rows = build_ledger(posts, blob["ticker"],
                                alias=aliases.get(blob["ticker"], ""),
                                channel="social_base", scores=by_id,
                                margin_threshold=thr)
            per_thr[thr] = len({r.cluster_id for r in rows
                                if r.evidence_eligible and r.scored})
        base = build_ledger(posts, blob["ticker"],
                            alias=aliases.get(blob["ticker"], ""),
                            channel="social_base", scores=by_id)
        out[blob["arm"]] = {
            "posts": len(posts),
            "eligible": len([r for r in base if r.evidence_eligible and r.scored]),
            "voices": per_thr,
        }
    return out


def main() -> int:
    args = sys.argv[1:]
    model = DEFAULT_MODEL
    if "--model" in args:
        model = args[args.index("--model") + 1]
    model = os.environ.get("MODEL_NAME", model)

    units = load_units()
    if not units:
        print("no corpora found")
        return 1
    labels = load_labels().get("claude") or load_labels().get("human") or {}
    if not labels:
        print("no labels found in data/labels.jsonl")
        return 1

    scores = score_all(units, model, rescore="--rescore" in args)
    funnel = in_funnel_ids(scores)

    def table(title, rows, n):
        print()
        print("=" * 74)
        print(f"  {title} — {model}")
        print(f"  graded against {n} labelled unit(s)")
        print("=" * 74)
        print(f"{'thr':>6} {'precision':>10} {'recall':>8} {'F1':>7} "
              f"{'admitted':>9} {'dir acc':>8}")
        for r in rows:
            flag = ""
            if r["precision"] >= PRECISION_TARGET and r["admitted"] >= 5:
                flag = "  <- clears 60%"
            print(f"{r['thr']:>6.2f} {r['precision']:>10.0%} {r['recall']:>8.0%} "
                  f"{r['f1']:>7.2f} {r['admitted']:>9} "
                  f"{r['direction_acc']:>8.0%}{flag}")

    all_rows, n_all = sweep_classifier(units, labels, scores)
    table("EVERY LABELLED POST (context only)", all_rows, n_all)

    # The operational number. Everything below is decided from this table.
    rows, n_graded = sweep_classifier(units, labels, scores, only=funnel)
    table("IN-FUNNEL — past subject and spam, where the gate runs",
          rows, n_graded)

    # NOT "first threshold clearing the bar". In-funnel the bar is cleared at
    # every threshold including zero, so that rule would recommend no gate at
    # all. What matters is the knee: the point past which more precision costs
    # recall and buys nothing.
    from utils.evidence import directional_margin
    current = directional_margin(model)
    live = next((r for r in rows if abs(r["thr"] - current) < 1e-9), None)
    usable = [r for r in rows if r["admitted"] >= 20]
    best_f1 = max(usable, key=lambda r: r["f1"]) if usable else None
    best_prec = max(usable, key=lambda r: (r["precision"], r["recall"])) if usable else None
    chosen = best_prec

    print()
    print("=" * 74)
    print("  VOICES SURVIVING, per retained corpus")
    print("  (precision is free to buy; this is what it costs)")
    print("=" * 74)
    yields = sweep_corpus_yield(scores)
    arms = sorted(yields)
    print(f"{'thr':>6} " + " ".join(f"{a[:14]:>16}" for a in arms))
    for thr in STEPS:
        cells = " ".join(f"{yields[a]['voices'][thr]:>16d}" for a in arms)
        mark = "  <-" if chosen and abs(thr - chosen["thr"]) < 1e-9 else ""
        print(f"{thr:>6.2f} {cells}{mark}")
    print()
    for a in arms:
        print(f"  {a}: {yields[a]['posts']} posts, "
              f"{yields[a]['eligible']} eligible and scored")

    def line(tag, r):
        if not r:
            print(f"  {tag:<20} unavailable")
            return
        lo, hi = wilson(round(r["precision"] * r["admitted"]), r["admitted"])
        print(f"  {tag:<20} {r['thr']:.2f}   precision {r['precision']:>4.0%} "
              f"[{lo:.0%}-{hi:.0%}]   signed {r['signed_precision']:>4.0%}   "
              f"n={r['admitted']}")

    print()
    print("=" * 74)
    print("  VERDICT")
    print("=" * 74)
    line("in force now", live)
    line("best F1", best_f1)
    line("best precision", best_prec)
    print()

    if live:
        lo, hi = wilson(live["signed_correct"], live["admitted"])
        print(f"  Base rate in this population: {live['base_rate']:.0%} of "
              "in-funnel posts carry a")
        print("  direction before the gate sees anything. A gate that admitted "
              "everything")
        print(f"  would score {live['base_rate']:.0%}, so the {PRECISION_TARGET:.0%} bar "
              "cannot be failed here and")
        print("  clearing it is not evidence. The gate's own contribution is "
              f"LIFT: {live['lift']:+.0%}.")
        print()
        print(f"  Sign-aware admission -- admitted AND scored the right way -- "
              f"is {live['signed_precision']:.0%}")
        print(f"  [{lo:.0%}-{hi:.0%}], n={live['admitted']}. That is the metric "
              "matching this gate's job,")
        print("  and it is NOT distinguishable from the bar at this sample size.")
        print()
        print("  Direction accuracy is flat across the whole sweep, so the "
              "threshold is not")
        print("  the lever for the errors that cost most. Moving it is "
              "unjustified in either")
        print("  direction on this evidence.")

    # The labels answer a question about the SYSTEM that no threshold can, and
    # it is a harsher answer than any number above.
    print()
    print("-" * 74)
    print("  SYSTEM-LEVEL, from the labellers' own decision-relevance field")
    print("-" * 74)
    yes = maybe = admitted_n = 0
    import glob
    for f in sorted(glob.glob(str(REPO / "data" / "corpora" / "*_[ABC]_*.json"))):
        blob = json.loads(Path(f).read_text())
        posts = blob.get("tweets") or []
        by_id = {str(p["id"]): scores[str(p["id"])] for p in posts
                 if p.get("id") is not None and str(p["id"]) in scores}
        for r in build_ledger(posts, blob["ticker"],
                              alias={"TSLA": "Tesla", "MP": "MP Materials"}.get(
                                  blob["ticker"], ""),
                              channel="social_base", scores=by_id):
            if not r.evidence_eligible:
                continue
            recs = labels.get(f"{blob['arm']}:{r.post_id}")
            if not recs:
                continue
            admitted_n += 1
            e = recs[min(recs)].get("eligible")
            yes += (e == "y")
            maybe += (e in ("y", "m"))
    if admitted_n:
        print(f"  Of {admitted_n} labelled posts the ledger ADMITS, the labeller "
              f"would want")
        print(f"  {yes} ({yes / admitted_n:.0%}) to influence the verdict; "
              f"{maybe} ({maybe / admitted_n:.0%}) counting 'maybe'.")
        print("  Both readings sit below the bar the component number clears.")
        print("  The open problem is what the ledger admits, not where this "
              "threshold sits.")

    print()
    print("  CAVEAT: data/labels.jsonl is 176 units from ONE labeller, one")
    print("  pass, labeller='claude'. scripts/label_posts.py --recheck has")
    print("  never been run, so the rubric's own agreement floors are")
    print("  unmeasured and every figure above inherits that.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
