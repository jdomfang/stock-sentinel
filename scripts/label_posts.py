#!/usr/bin/env python3
"""Label retained posts against the locked rubric. Resumable, offline, free.

WHY THIS IS THE LAST PHASE AND THE ONE EVERYTHING WAITS ON

Every threshold in the rebuilt pipeline is provisional: 0.30 for a usable
corpus, +/-0.15 for a lean, 3 clusters for a conflict veto, subject_score >= 5
for "primary". They were placed so they separate three corpora we happen to
hold. That is a gate against known controls, not calibration, and nothing turns
it into calibration except labels.

The labels also settle a question no amount of code can: whether the automatic
proxies mean anything. "Usable evidence" is currently defined as "the target is
among at most 3 cashtags and |margin| > 0.15" -- an invention. If a human
disagrees with it, every yield figure in the design document is void.

COST: no API posts. The corpora are already on disk, already paid for.

WHAT MAKES THE OUTPUT TRUSTWORTHY

One labeller cannot measure their own consistency, so the harness measures it
for them: --recheck re-serves a sample already labelled, shuffled, and reports
agreement against the locked thresholds (85% subject, 90% eligibility, 75%
direction). Below those the rubric is too vague to use and the labels should be
discarded rather than trusted.

Clustering first means near-duplicates are labelled once: a 100-account
campaign is one judgement, not one hundred.

Usage:
    python3 scripts/label_posts.py                 # label, resumable
    python3 scripts/label_posts.py --recheck 30    # re-serve 30 for agreement
    python3 scripts/label_posts.py --report        # progress and agreement
"""

from __future__ import annotations

import glob
import json
import os
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils.evidence import assign_clusters  # noqa: E402

# In the repo, deliberately. These 298 posts were bought once; X's recent
# search is 7 days deep, so once they are gone they cannot be re-acquired at
# any price. They previously lived only in a session scratchpad under a
# directory name containing a UUID that will never exist again.
CORPUS_DIR = Path(os.environ.get("STAGE1_RAW", str(REPO / "data" / "corpora")))
OUT = REPO / "data" / "labels.jsonl"

ALIASES = {"TSLA": "Tesla", "MP": "MP Materials"}

# The rubric, as locked. Each field is one question with a closed set.
FIELDS = [
    ("subject", "Is this post about the target COMPANY?", {
        "p": "primary — the company is the main subject",
        "c": "comparison — one of 2-3 securities genuinely compared",
        "m": "mentioned_only — a list/recap, no claim specific to the target",
        "w": "wrong_entity — the ticker string means something else",
        "u": "unclear",
    }),
    ("spam", "What kind of post is it?", {
        "o": "organic", "t": "promo template", "d": "coordinated duplicate",
        "b": "bot feed", "u": "unclear",
    }),
    ("direction", "Does it express a view about the target?", {
        "b": "bullish", "r": "bearish", "n": "neutral / factual",
        "m": "mixed", "x": "no view", "u": "unclear",
    }),
    ("evidence", "What kind of evidence is it? (first that applies)", {
        "r": "risk", "c": "catalyst", "t": "trading setup",
        "s": "general sentiment", "n": "none",
    }),
    ("eligible", "Would you want this post to influence Buy/Watch/Avoid?", {
        "y": "yes", "n": "no", "m": "maybe",
    }),
]

# From the locked rubric. Below these the labels are not usable.
AGREEMENT_FLOOR = {"subject": 0.85, "eligible": 0.90, "direction": 0.75}


def load_units() -> list[dict]:
    """One unit per near-duplicate cluster per corpus, so a campaign is one call."""
    units: list[dict] = []
    for f in sorted(glob.glob(str(CORPUS_DIR / "*_[ABC]_*.json"))):
        blob = json.loads(Path(f).read_text())
        posts = blob.get("tweets") or []
        if not posts:
            continue
        clusters = assign_clusters([str(p.get("text") or "") for p in posts])
        sizes: dict[int, int] = {}
        for c in clusters:
            sizes[c] = sizes.get(c, 0) + 1
        seen: set[int] = set()
        for post, cid in zip(posts, clusters):
            if cid in seen:
                continue
            seen.add(cid)
            units.append({
                # Keyed on the representative POST id, never the cluster id.
                # Cluster ids permute with input order and shift when the
                # threshold moves -- and moving it is part of the calibration
                # this harness feeds.
                "uid": f"{blob['arm']}:{post.get('id') or cid}",
                "arm": blob["arm"], "ticker": blob["ticker"],
                "alias": ALIASES.get(blob["ticker"], ""),
                "represents": sizes[cid],
                "post_id": str(post.get("id") or ""),
                "text": str(post.get("text") or ""),
            })
    return units


def load_labels() -> dict[str, dict]:
    if not OUT.exists():
        return {}
    out: dict[str, dict] = {}
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Later lines win, so a recheck pass does not destroy the first answer:
        # both are kept in the file and compared by `pass` number.
        out.setdefault(rec["uid"], {})[rec.get("pass", 1)] = rec
    return out


def append(rec: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def ask(unit: dict, pass_no: int) -> dict | None:
    print("\n" + "=" * 78)
    print(f"  {unit['ticker']}  (alias: {unit['alias'] or 'none'})"
          f"   ·  represents {unit['represents']} near-identical post(s)")
    print("=" * 78)
    print(unit["text"][:900])
    print("-" * 78)
    rec = {"uid": unit["uid"], "post_id": unit["post_id"], "arm": unit["arm"],
           "ticker": unit["ticker"], "represents": unit["represents"],
           "pass": pass_no}
    for key, question, options in FIELDS:
        opts = "  ".join(f"[{k}] {v.split(' — ')[0]}" for k, v in options.items())
        while True:
            print(f"\n{question}")
            print(f"  {opts}")
            for k, v in options.items():
                if " — " in v:
                    print(f"     {k} = {v}")
            try:
                a = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  (stopping — answers already given are saved)")
                return None
            if a in ("q", "quit"):
                return None
            if a in options:
                rec[key] = a
                break
            print("  (unrecognised — enter one of the letters, or q to stop)")
    return rec


def report() -> None:
    units = {u["uid"]: u for u in load_units()}
    labels = load_labels()
    done = len(labels)
    print(f"\nlabelled {done}/{len(units)} units "
          f"({sum(units[u]['represents'] for u in labels if u in units)} posts covered)")

    pairs = {u: p for u, p in labels.items() if len(p) >= 2}
    if not pairs:
        print("no recheck pass yet — run with --recheck N to measure consistency")
        return
    print(f"\nself-consistency over {len(pairs)} re-labelled units:")
    for field, floor in AGREEMENT_FLOOR.items():
        agree = sum(1 for p in pairs.values()
                    if p[min(p)].get(field) == p[max(p)].get(field))
        rate = agree / len(pairs)
        verdict = "OK" if rate >= floor else "TOO LOW — rubric is too vague to use"
        print(f"  {field:<10} {rate:.0%}  (floor {floor:.0%})  {verdict}")


def main() -> int:
    args = sys.argv[1:]
    if "--report" in args:
        report()
        return 0

    units = load_units()
    if not units:
        print(f"no corpora found in {CORPUS_DIR}")
        return 1
    labels = load_labels()

    if "--recheck" in args:
        n = 30
        _i = args.index("--recheck") + 1
        if len(args) > _i:
            try:
                n = max(1, int(args[_i]))
            except ValueError:
                print(f"  (--recheck {args[_i]!r} is not a number; using {n})")
        pool = [u for u in units if u["uid"] in labels and len(labels[u["uid"]]) == 1]
        # Shuffled and re-served without showing the first answer: an agreement
        # rate measured against a visible prior is not a measurement.
        random.shuffle(pool)
        todo, pass_no = pool[:n], 2
        print(f"recheck: {len(todo)} unit(s), shuffled. Your earlier answers are not shown.")
    else:
        todo = [u for u in units if u["uid"] not in labels]
        pass_no = 1
        covered = sum(u["represents"] for u in units)
        print(f"{len(todo)} unit(s) left of {len(units)} — covering {covered} posts.")
        print("Near-duplicates are collapsed, so a campaign is one judgement.")
        print("Answers save as you go. 'q' stops; rerun to continue.")

    for i, unit in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}]", end="")
        rec = ask(unit, pass_no)
        if rec is None:
            print("\nstopped — progress saved.")
            break
        append(rec)
    else:
        print("\ndone.")
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
