#!/usr/bin/env python3
"""The migration oracle: exactly what this system produces today, frozen.

WHY THIS EXISTS

core-api moves ~5,500 lines out of the Streamlit process. The safe way to do
that is to run old and new side by side in production and compare -- and that
was explicitly traded away for speed. So the comparison moves offline: this file
records what every module produces for fixed inputs BEFORE anything moves, and
then asserts, at every step of the migration, that not one byte has changed.

Any diff from here on is either a bug or a decision someone makes on purpose.
There is no third option, and that is the whole point.

WHY THE INPUTS ARE WHAT THEY ARE

  corpora   the three retained arms, 298 real posts, already on disk. They were
            bought once and X's index is 7 days deep, so they cannot be
            reacquired -- which is exactly what makes them a good fixture.

  scores    DERIVED FROM THE POST ID, not from a model. data/model_scores.json
            is gitignored and covers only 176 of the 298 posts, so depending on
            it would make the oracle unreproducible on a fresh clone and
            unstable the day someone reruns the sweep. A sha256 of the post id
            gives the same distribution forever, on any machine, with no model
            and no network. Realism is not the job here; DETERMINISM is.

  prices    fixed series chosen to reach every branch of the price module --
            flat, rising, falling on heavy volume, too-short, and a sector
            benchmark that exercises both sides of the market_wide exemption.

WHAT IS CAPTURED

Everything a caller can observe: every field of every ledger row, all six module
outputs, the full verdict including pillars and remediation, the projection, the
newswire measurement, and the exact row signal_log would POST. If a refactor
changes any of it, this fails.

Usage:
    python3 tests/test_golden.py --record     # freeze current behaviour
    python3 tests/test_golden.py              # assert nothing moved
"""

from __future__ import annotations

import dataclasses
import glob
import hashlib
import json
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

GOLDEN = Path(__file__).resolve().parent / "golden"
ALIASES = {"TSLA": "Tesla", "MP": "MP Materials"}

# PINNED, and passed explicitly to build_ledger. Left to resolve itself,
# directional_margin() follows MODEL_NAME *and* whether streamlit imports --
# so the oracle recorded under FinBERT's 0.15 failed under FinTwitBERT's 0.70
# with 829 lines of diff that read exactly like a migration regression, and
# failed again in any process without streamlit (gate silently 0.50). The
# oracle must pin BEHAVIOUR, never the environment it happened to run in.
GATE = 0.15

# Deterministic price fixtures. Each reaches a different price-module branch.
def _walk(total, n=24, vol=0.018, seed=7):
    """A drifting series with REAL dispersion, deterministic from `seed`.

    The first version alternated +/-0.4% like a metronome. Against a benchmark
    built the same way, the pair's tracking dispersion came out at 0.0000 --
    which made `max(-(k*noise), CAP)` collapse to -0.0, so NO excess could ever
    clear it and `market_wide` was unreachable from both fixtures that claimed
    to test it. Every constant governing the sector exemption -- the one family
    in this codebase that makes the product issue MORE Buys -- was 0% pinned.
    """
    import random
    rng = random.Random(seed)
    step = (1 + total) ** (1 / (n - 1))
    out = [100.0]
    for _ in range(1, n):
        out.append(out[-1] * step * (1 + rng.gauss(0, vol)))
    return [round(x, 6) for x in out]


# Volume is chosen INDEPENDENTLY of direction. Previously every falling series
# carried a 9x spike and every flat one carried 1.0x, so `r20 <= RETURN` and
# `vratio >= VOLUME` were perfectly collinear across all 140 cases and the
# volume conjunct could be deleted from the veto undetected.
_HEAVY = [1.0] * 23 + [9.0]
_QUIET = [1.0] * 24

PRICE_CASES = {
    # status: neutral
    "flat":             (_walk(0.0, seed=1),   _QUIET, None, ""),
    "rising_heavy":     (_walk(0.56, seed=2),  _HEAVY, None, ""),   # up on volume
    # status: caution -- material decline WITH a volume spike
    "falling_heavy":    (_walk(-0.22, seed=3), _HEAVY, None, ""),
    # status: declining -- material decline on ORDINARY volume
    "falling_quiet":    (_walk(-0.22, seed=1), _QUIET, None, ""),
    # status: unconfirmed -- material decline, volume unavailable
    "falling_novol":    (_walk(-0.22, seed=5), None,   None, ""),
    # status: missing
    "no_prices":        (None,                 None,   None, ""),
    "too_short":        ([100.0, 101.0],       [1.0, 1.0], None, ""),
    # status: market_wide -- the sector explains the fall
    "falling_sector":   (_walk(-0.20, seed=6), _HEAVY, _walk(-0.19, seed=1), "XLK"),
    # NOT market_wide -- the stock fell alone
    "falling_alone":    (_walk(-0.20, seed=6), _HEAVY, _walk(0.02, seed=8),  "XLK"),
    # NOT market_wide -- past the absolute floor, whatever the sector did
    "falling_past_floor": (_walk(-0.35, seed=6), _HEAVY, _walk(-0.34, seed=1), "XLK"),
    # relative refuses: benchmark too short, and bar dates disagree
    "bench_too_short":  (_walk(-0.20, seed=6), _HEAVY, _walk(-0.19, seed=1)[:7], "XLK"),
    "bench_stale":      (_walk(-0.20, seed=6), _HEAVY, _walk(-0.19, seed=1), "XLK"),

    # ---- BOUNDARY BRACKETS ----
    #
    # A threshold with no fixture near it can be moved freely. Mutation testing
    # showed 8 of 13 constant changes surviving the oracle for exactly this
    # reason. Each pair below straddles one constant, so loosening OR
    # tightening it changes a recorded status.
    #
    # PRICE_CAUTION_RETURN = -0.15
    "ret_just_above":   (_walk(-0.13, seed=1), _HEAVY, None, ""),   # neutral
    "ret_just_below":   (_walk(-0.17, seed=1), _HEAVY, None, ""),   # caution
    # PRICE_CAUTION_VOLUME = 1.5
    "vol_just_under":   (_walk(-0.22, seed=1), [1.0] * 23 + [1.4], None, ""),
    "vol_just_over":    (_walk(-0.22, seed=1), [1.0] * 23 + [1.6], None, ""),
    # PRICE_SECTOR_SHARE = 0.5 -- the sector must explain at least half the fall
    "sector_explains_most": (_walk(-0.20, seed=6), _HEAVY, _walk(-0.16, seed=1), "XLK"),
    "sector_explains_little": (_walk(-0.20, seed=6), _HEAVY, _walk(-0.06, seed=1), "XLK"),
    # PRICE_EXCESS_CAP = -0.05 -- a wide spread must not buy unlimited slack
    "excess_past_cap":  (_walk(-0.20, seed=6), _HEAVY, _walk(-0.14, seed=1), "XLK"),
}

# The bar date the BENCHMARK claims. One case deliberately disagrees with the
# ticker's, which is the halted-stock refusal path in utils/relative.py.
BENCH_BAR_DATE = {"bench_stale": "2026-08-17"}
BAR_DATE = "2026-08-14"


def stub_scores(posts) -> dict:
    """post_id -> distribution, derived from the id. Stable on any machine.

    Not a model, and deliberately not one: see the module docstring.
    """
    out = {}
    for p in posts:
        pid = p.get("id")
        if pid is None:
            continue
        h = hashlib.sha256(str(pid).encode()).digest()
        a, b = struct.unpack("<II", h[:8])
        # A PROPER SIMPLEX, not pos/(pos+neg+0.5). The earlier form capped
        # |margin| at 0.6628, so NO post could ever clear the 0.70 gate
        # FinTwitBERT uses or the 0.85 stocktwits uses -- the oracle pinned a
        # system whose directional path was unreachable for two of the four
        # supported models. Now the full range 0..1 is spanned.
        neu = (a % 10_001) / 10_000                    # 0.00 .. 1.00
        split = (b % 10_001) / 10_000
        pos = (1.0 - neu) * split
        neg = (1.0 - neu) * (1.0 - split)
        out[str(pid)] = {
            "p_positive": round(pos, 6),
            "p_negative": round(neg, 6),
            "p_neutral": round(neu, 6),
        }
    return out


def _plain(obj):
    """Dataclasses, tuples and sets to something JSON compares stably."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, set):
        return sorted(_plain(v) for v in obj)
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            # json.dumps emits bare NaN, and json.loads(NaN) != NaN -- the
            # oracle would then fail against its OWN freshly recorded file,
            # forever, which is how a safety net gets switched off.
            raise AssertionError(f"non-finite float reached the oracle: {obj!r}")
        # Guards against -0.0 vs 0.0 and platform repr drift.
        return round(obj, 9) + 0.0
    if isinstance(obj, bool):
        # Before int: True == 1 in Python, so a bool turning into an int would
        # compare equal and slip through.
        return {"__bool__": obj}
    return obj


def capture_corpus(path: Path) -> dict:
    from utils.evidence import build_ledger
    from utils import modules as M
    from utils.verdict import adjudicate
    from utils.projections import simple_projection
    from utils import newswire as NW

    blob = json.loads(path.read_text())
    posts = blob.get("tweets") or []
    ticker = blob["ticker"]
    alias = ALIASES.get(ticker, "")
    scores = stub_scores(posts)

    # Both channels, so newswire and seed paths are exercised too. The split is
    # positional and therefore stable.
    main, wire = posts[: max(1, len(posts) - 10)], posts[max(1, len(posts) - 10):]
    ledger = build_ledger(main, ticker, alias=alias, channel="social_base",
                          scores=scores, margin_threshold=GATE)
    seen = {r.post_id for r in ledger}
    ledger += [r for r in build_ledger(wire, ticker, alias=alias,
                                       channel="newswire", scores=scores,
                                       margin_threshold=GATE)
               if r.post_id not in seen]

    out = {
        "arm": blob["arm"], "ticker": ticker, "alias": alias,
        "n_posts": len(posts),
        "ledger": [_plain(r) for r in ledger],
        "modules": {
            "quality": _plain(M.quality(ledger)),
            "social": _plain(M.social(ledger, M.quality(ledger).score)),
            "risk": _plain(M.risk(ledger)),
            "catalyst": _plain(M.catalyst(ledger)),
            "newswire": _plain(M.newswire(ledger)),
        },
        "cases": {},
    }

    for name, (prices, vols, bench, sym) in PRICE_CASES.items():
        bp = ({"prices": bench,
               "bar_date": BENCH_BAR_DATE.get(name, BAR_DATE)} if bench else None)
        v = adjudicate(ledger, prices, vols, benchmark_prices=bp,
                       benchmark=sym, bar_date=BAR_DATE)
        proj = simple_projection(prices or [], v.social.direction, days=30,
                                 quality_ok=(v.quality.tier in ("moderate", "high")
                                             and v.recommendation == "Buy"))
        wire_m = NW.measure(ledger, wire_posts=wire, main_posts=main,
                            wire_state="fetched", wire_billed=len(wire),
                            prices=prices, volumes=vols,
                            benchmark_prices=bp, benchmark=sym, bar_date=BAR_DATE)
        out["cases"][name] = {
            "price_module": _plain(v.price),
            "verdict": {
                "recommendation": v.recommendation, "confidence": v.confidence,
                "earned_confidence": v.earned_confidence, "branch": v.branch,
                "reason": v.reason, "would_change": list(v.would_change),
                "confidence_notes": list(v.confidence_notes),
                "own_clusters": v.own_clusters, "seed_only": v.seed_only,
                "pillars": [_plain(p) for p in v.pillars],
            },
            "projection": _plain(proj),
            "newswire_measure": _plain(wire_m),
            "signal_log_row": _plain(signal_row(
                ticker, v, ledger, proj, wire_m, wire_posts=wire,
                main_posts=main, prices=prices, volumes=vols,
                benchmark_prices=bp, benchmark=sym)),
        }
    return out


def signal_row(ticker, verdict, ledger, proj, wire_m, *, wire_posts=None,
               main_posts=None, prices=None, volumes=None,
               benchmark_prices=None, benchmark="") -> dict:
    """The exact dict signal_log.record would POST, without the network."""
    import urllib.request
    from utils import signal_log as SL
    captured = {}

    def fake(req, timeout=None):
        captured["row"] = json.loads(req.data.decode())[0]
        raise RuntimeError("stop")

    real_open, real_cfg = urllib.request.urlopen, SL._config
    urllib.request.urlopen = fake
    SL._config = lambda n, d="": "https://x.invalid" if "URL" in n else "k"
    try:
        # EVERY kwarg the real call site passes (pages/Deep_Analysis.py). The
        # earlier version omitted wire/price/benchmark, so every recorded row
        # had null newswire columns and a verdict_without_wire computed
        # price-blind -- pinning the exact configuration newswire's own
        # docstring says was measured and fixed.
        SL.record(ticker, verdict, feature="deep_analyze", price_at_decision=100.0,
                  decision_trade_date=BAR_DATE, sector=None, model="golden",
                  event_id=None, corpus_key="golden", ledger=ledger,
                  projection=proj, evidence_age_s=60,
                  wire_posts=wire_posts, main_posts=main_posts,
                  wire_state="fetched",
                  wire_billed=(len(wire_posts) if wire_posts else 0),
                  prices=prices, volumes=volumes,
                  benchmark_prices=benchmark_prices, benchmark=benchmark)
    except Exception:
        pass
    finally:
        urllib.request.urlopen, SL._config = real_open, real_cfg
    row = captured.get("row")
    # A silently empty row would freeze "signal_log asserts nothing" forever.
    assert row, "signal_log capture broke — the oracle would pin an empty dict"
    # Volatile by construction; the oracle pins behaviour, not identity.
    row.pop("adjudicator_version", None)
    return row


# ---------------------------------------------------------------- branches --
#
# The three corpora are real, and between them they reach FOUR cascade branches,
# all of them Watch. That is an honest reflection of what genuine chatter looks
# like and a poor oracle: a refactor could break the Buy route, the Avoid route,
# the conflict veto or the seed guard and every corpus would still match.
#
# So a second family of fixtures, built to reach every branch by construction.
# Synthetic rows, but the SAME modules, adjudicator, projection and log payload
# run over them -- which is what the oracle is protecting.

def _row(i, **kw):
    from utils.evidence import EvidenceRow
    d = dict(post_id=str(i), channel="social_base", text=f"post {i}",
             author_id=f"a{i}", target_match_type="cashtag",
             target_subject_status="primary", evidence_types=("directional_view",),
             margin=0.6, scored=True, cluster_id=i, spam_risk="low",
             evidence_eligible=True)
    d.update(kw)
    return EvidenceRow(**d)


def branch_fixtures() -> dict:
    hard = dict(catalyst_severity="hard",
                evidence_types=("catalyst", "directional_view"))
    return {
        "empty":                    [],
        "quality_reject":           [_row(i, cluster_id=0, spam_risk="high",
                                          evidence_eligible=False) for i in range(20)],
        "too_few_sources":          [_row(i) for i in range(2)],
        "seed_only":                ([_row(i) for i in range(3)]
                                     + [_row(100 + i, channel="discovery_seed")
                                        for i in range(9)]),
        "risk_high_severe":         [_row(i, risk_severity="severe",
                                          evidence_types=("risk",)) for i in range(6)],
        "risk_high_soft":           [_row(i, risk_severity="soft",
                                          evidence_types=("risk",)) for i in range(6)],
        "sentiment_negative":       [_row(i, margin=-0.7) for i in range(8)],
        "bearish_event":            ([_row(i, margin=-0.6, **hard) for i in range(2)]
                                     + [_row(10 + i) for i in range(10)]),
        "channel_conflict":         ([_row(i, margin=0.6) for i in range(12)]
                                     + [_row(50 + i, target_match_type="alias",
                                            margin=-0.7) for i in range(4)]),
        "buy_sentiment":            [_row(i) for i in range(8)],
        "buy_catalyst":             [_row(i, margin=0.6, **hard) for i in range(6)],
        "catalyst_unscored":        [_row(i, margin=0.0, scored=False,
                                          catalyst_severity="hard",
                                          evidence_types=("catalyst",))
                                     for i in range(6)],
        "catalyst_flat":            [_row(i, margin=0.0, **hard) for i in range(6)],
        "no_alignment":             [_row(i, margin=0.05) for i in range(8)],
        "spam_campaign":            [_row(i, cluster_id=0, spam_risk="high",
                                          evidence_eligible=False, margin=0.6)
                                     for i in range(100)],
        "newswire_only":            [_row(i, channel="newswire") for i in range(8)],
        # THE NEAREST MISS: reaches the Buy branch with every blocking pillar
        # passed, then the confidence rules take it back. Unpinned until now.
        "buy_downgraded_low_conf":  ([_row(i, cluster_id=0, margin=0.6)
                                      for i in range(6)]
                                     + [_row(50 + i, margin=0.6) for i in range(8)]),
        "quality_below_bar":        [_row(i, target_subject_status="mentioned_only",
                                          evidence_types=("risk",),
                                          risk_severity="soft") for i in range(8)],
        # Straddles CONFLICT_MIN_CLUSTERS: two voices per channel pointing
        # opposite ways is BELOW the bar, so it must not veto. Loosening the
        # constant turns this into a conflict and the oracle sees it.
        "conflict_below_bar":       ([_row(i, margin=0.6) for i in range(2)]
                                     + [_row(50 + i, target_match_type="alias",
                                            margin=-0.7) for i in range(2)]
                                     + [_row(70 + i, margin=0.6) for i in range(8)]),
        "mixed_channels":           ([_row(i) for i in range(6)]
                                     + [_row(20 + i, channel="newswire", **hard)
                                        for i in range(2)]
                                     + [_row(40 + i, channel="discovery_seed")
                                        for i in range(3)]),
    }


def capture_branches() -> dict:
    from utils import modules as M
    from utils.verdict import adjudicate
    from utils.projections import simple_projection
    from utils import newswire as NW

    out = {}
    for name, ledger in branch_fixtures().items():
        cases = {}
        for pname, (prices, vols, bench, sym) in PRICE_CASES.items():
            bp = ({"prices": bench,
                   "bar_date": BENCH_BAR_DATE.get(pname, BAR_DATE)} if bench else None)
            v = adjudicate(ledger, prices, vols, benchmark_prices=bp,
                           benchmark=sym, bar_date=BAR_DATE)
            proj = simple_projection(prices or [], v.social.direction, days=30,
                                     quality_ok=(v.quality.tier in ("moderate", "high")
                                                 and v.recommendation == "Buy"))
            wm = NW.measure(ledger, wire_posts=[], main_posts=[],
                            wire_state="fetched", wire_billed=0,
                            prices=prices, volumes=vols, benchmark_prices=bp,
                            benchmark=sym, bar_date=BAR_DATE)
            cases[pname] = {
                "verdict": {
                    "recommendation": v.recommendation, "confidence": v.confidence,
                    "earned_confidence": v.earned_confidence, "branch": v.branch,
                    "reason": v.reason, "would_change": list(v.would_change),
                    "confidence_notes": list(v.confidence_notes),
                    "own_clusters": v.own_clusters, "seed_only": v.seed_only,
                    "pillars": [_plain(p) for p in v.pillars],
                },
                "modules": {k: _plain(getattr(v, k)) for k in
                            ("quality", "social", "risk", "catalyst", "price", "newswire")},
                "projection": _plain(proj),
                "newswire_measure": _plain(wm),
                "signal_log_row": _plain(signal_row(
                    "TEST", v, ledger, proj, wm, prices=prices, volumes=vols,
                    benchmark_prices=bp, benchmark=sym)),
            }
        out[name] = {"n_rows": len(ledger), "cases": cases}
    return out


def fingerprint() -> dict:
    """What the oracle depends on besides the code. Recorded and asserted.

    Every entry here caused, or could cause, a failure that reads like a
    migration regression and is not one.
    """
    import numpy
    corpora = {}
    for f in sorted(glob.glob(str(REPO / "data" / "corpora" / "*_[ABC]_*.json"))):
        corpora[Path(f).name] = hashlib.sha256(Path(f).read_bytes()).hexdigest()[:16]
    return {
        # PINNED explicitly, so this must never follow the environment.
        "gate": GATE,
        # Generator streams are not guaranteed stable across numpy majors
        # (NEP 19 covers only legacy RandomState), and the projection is a
        # 2000-path Monte Carlo. A bump here is a real, diagnosable break.
        "numpy": numpy.__version__.split(".")[0],
        "corpora_sha256": corpora,
    }


def build() -> dict:
    files = sorted(glob.glob(str(REPO / "data" / "corpora" / "*_[ABC]_*.json")))
    if not files:
        raise SystemExit("no corpora on disk — cannot record the oracle")
    out = {Path(f).name.split("_", 1)[1].replace(".json", ""): capture_corpus(Path(f))
           for f in files}
    out["_branches"] = capture_branches()
    out["_fingerprint"] = fingerprint()
    return out


def main() -> int:
    record = "--record" in sys.argv
    GOLDEN.mkdir(parents=True, exist_ok=True)
    current = build()

    if record:
        for name, data in current.items():
            (GOLDEN / f"{name}.json").write_text(
                json.dumps(data, indent=1, sort_keys=True) + "\n")
        (GOLDEN / "_fingerprint.json").write_text(
            json.dumps(current.pop("_fingerprint"), indent=1, sort_keys=True) + "\n")
        n = sum(len(d["ledger"]) for k, d in current.items() if k != "_branches")
        nb = len(current["_branches"])
        print(f"recorded {len(current) - 1} corpora ({n} ledger rows) + "
              f"{nb} branch fixtures, {len(PRICE_CASES)} price cases each")
        seen = {c["verdict"]["branch"]
                for d in current["_branches"].values() for c in d["cases"].values()}
        print(f"cascade branches covered: {len(seen)} -> {sorted(seen)}")
        return 0

    print("=" * 74)
    print("  ORACLE: nothing about the analysis may change during the migration")
    print("=" * 74)
    failed = 0
    current.pop("_fingerprint", None)
    orphans = sorted({p.stem for p in GOLDEN.glob("*.json")}
                     - set(current) - {"_fingerprint"})
    for o in orphans:
        # main() used to iterate only what was found on disk, so removing a
        # corpus printed "3 of 3 unchanged" and exited 0 -- dropping the only
        # fixture pinning an entire cascade branch.
        print(f"  FAIL  {o} — recorded, but nothing produced it this run "
              "(missing corpus?)")
        failed += 1

    fp = fingerprint()
    want_fp = {}
    if (GOLDEN / "_fingerprint.json").exists():
        want_fp = json.loads((GOLDEN / "_fingerprint.json").read_text())
    if want_fp and want_fp != fp:
        print("  ENVIRONMENT DIFFERS from the recording — diffs below may not "
              "be code changes:")
        for line in _diff(want_fp, fp):
            print(f"          {line}")

    for name, data in current.items():
        path = GOLDEN / f"{name}.json"
        if not path.exists():
            print(f"  MISSING  {name} — run with --record first")
            failed += 1
            continue
        want = json.loads(path.read_text())
        if want == data:
            n = (f"{len(data)} fixtures" if name == "_branches"
                 else f"{len(data['ledger'])} rows, {len(data['cases'])} price cases")
            print(f"  PASS  {name}  ({n})")
            continue
        failed += 1
        lines = _diff(want, data)
        # VERDICT LINES FIRST. Alphabetical order put `.cases.*.projection`
        # ahead of `.verdict.*`, so a 12-line budget was spent entirely on
        # Monte Carlo noise while the recommendation flip below never printed.
        rank = lambda L: (0 if any(k in L for k in
                          ("recommendation", "branch", "confidence", ".reason"))
                          else 1 if ".ledger" in L else 2)
        lines.sort(key=rank)
        print(f"  FAIL  {name}  ({len(lines)} differences)")
        for line in lines[:25]:
            print(f"          {line}")
        if len(lines) > 25:
            print(f"          ... and {len(lines) - 25} more")
    print("=" * 74)
    print(f"  {len(current) - failed} of {len(current)} corpora unchanged")
    print("=" * 74)
    return 1 if failed else 0


def _diff(a, b, path="") -> list[str]:
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: ADDED = {b[k]!r:.60}")
            elif k not in b:
                out.append(f"{path}.{k}: REMOVED")
            else:
                out += _diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} -> {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            out += _diff(x, y, f"{path}[{i}]")
    elif a != b:
        sa, sb = repr(a), repr(b)
        if len(sa) > 46 or len(sb) > 46:
            # Truncating at a fixed offset rendered two long strings that
            # differ at char 60 as identical.
            i = next((k for k in range(min(len(sa), len(sb))) if sa[k] != sb[k]),
                     min(len(sa), len(sb)))
            lo = max(0, i - 12)
            sa, sb = "..." + sa[lo:i + 28], "..." + sb[lo:i + 28]
        out.append(f"{path}: {sa} -> {sb}")
    return out


if __name__ == "__main__":
    sys.exit(main())
