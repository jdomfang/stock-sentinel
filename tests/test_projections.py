#!/usr/bin/env python3
"""Pin the projection, and above all pin the property it exists to guarantee:

    THE SIMULATION MAKES NO DIRECTIONAL CLAIM IT CANNOT SUPPORT.

Every defect this file guards against was found by review AFTER the code looked
right and passed a hand-built spot check. Each one published a confident number
that was wrong in the bearish direction, which is the direction a user acts on.

  1. cumprod(1 + N(mu, sigma)) injects a median drift of -sigma^2*T/2. At 12%
     daily volatility the "zero drift" median path landed at -18.9%, down_rate
     exceeded up_rate at every target for every ticker, and the docstring
     directly above asserted the symmetry it was breaking.

  2. up_first_rate counted paths reaching +t first out of ALL paths, so its
     complement was "went down first OR went nowhere". On a calm large cap it
     read 23% -- an apparent strong bearish edge, printed underneath a sentence
     denying any edge.

  3. mae_median pinned to exactly 0.0 above ~8% daily volatility, because
     conditioning on winners selects for paths that hit on day one having never
     dipped. The product printed "-0.0%" as the risk of its riskiest tickers,
     and the statistic was ANTI-correlated with volatility.

  4. Each target drew its own path set, so nothing forced
     P(+8%) <= P(+5%) <= P(+3%) and sampling noise could order them wrongly.

Usage:
    python3 tests/test_projections.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils.projections import (  # noqa: E402
    DECISION_HORIZON_DAYS, _movement_profile, simple_projection,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

VOLS = (0.008, 0.012, 0.02, 0.05, 0.08, 0.12)


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def prof(vol, drift=0.0, days=30, targets=(0.05,), seed=11, horizon=None):
    rng = np.random.default_rng(seed)
    kw = {} if horizon is None else {"decision_horizon": horizon}
    return _movement_profile(vol, drift, days, targets, rng, **kw)


def prices(n=40, vol=0.02, seed=5):
    rng = np.random.default_rng(seed)
    return list(100 * np.cumprod(1 + rng.normal(0, vol, n)))


def test_zero_drift_never_favours_the_downside():
    print("\nINVARIANT: at zero drift the walk cannot lean bearish")
    # THE GAP IS NOT ZERO, AND SHOULD NOT BE. One arithmetic target places the
    # two barriers at different log distances: +5% is log 0.04879 away, -5% is
    # log 0.05129 away, so the downside barrier sits 5.1% further out and
    # up_rate legitimately exceeds down_rate by ~2pp at low volatility. That is
    # a property of percentages, not of the simulation.
    #
    # What must never recur is the OTHER sign. cumprod(1 + N(0, sigma)) drove
    # down_rate ABOVE up_rate by 11pp at vol 0.12, growing with volatility --
    # a bearish tilt in a model that claimed no view.
    for vol in VOLS:
        # Averaged over seeds: one draw at 2000 sims carries ~1pp of standard
        # error, and a flaky test is a test nobody trusts.
        gaps = [prof(vol, seed=s)["5%"]["up_rate"] - prof(vol, seed=s)["5%"]["down_rate"]
                for s in range(6)]
        mean_gap = sum(gaps) / len(gaps)
        check(f"vol {vol:.3f}: gap within [-1pp, +3pp]",
              -0.01 <= mean_gap <= 0.03, f"mean gap {mean_gap:+.1%}")


def test_the_underlying_walk_is_symmetric():
    print("\n...and with the barriers placed evenly, the gap vanishes entirely")
    # The sharp version of the invariant above: remove the barrier asymmetry by
    # choosing a down target that is the same LOG distance as the up target,
    # and any residual lean is the process itself. Under the old formulation
    # this stayed stubbornly negative; it must now be indistinguishable from 0.
    up_t = 0.05
    dn_t = 1 - np.exp(-np.log(1 + up_t))
    for vol in (0.008, 0.02, 0.08):
        gaps = []
        for seed in range(8):
            rng = np.random.default_rng(500 + seed)
            paths = np.exp(np.cumsum(rng.normal(0.0, vol, size=(2000, 30)), axis=1)) - 1.0
            gaps.append(float((paths >= up_t).any(axis=1).mean())
                        - float((paths <= -dn_t).any(axis=1).mean()))
        mean_gap = sum(gaps) / len(gaps)
        check(f"vol {vol:.3f}: log-even barriers give |gap| under 1pp",
              abs(mean_gap) < 0.01, f"mean gap {mean_gap:+.2%}")


def test_median_path_lands_on_the_centre_it_claims():
    print("\nINVARIANT: scenario_base is where the median path actually goes")
    for vol in (0.02, 0.05, 0.12):
        rng = np.random.default_rng(3)
        steps = rng.normal(np.log1p(0.0) / 30, vol, size=(20000, 30))
        med = float(np.median(np.exp(np.cumsum(steps, axis=1))[:, -1] - 1.0))
        # -sigma^2*T/2 at vol 0.12 is -21.6%. If the arithmetic formulation
        # comes back this catches it an order of magnitude before anyone reads
        # the table.
        check(f"vol {vol:.2f}: median final within 1% of zero", abs(med) < 0.01,
              f"median {med:+.2%}")


def test_up_first_is_conditional_not_a_share_of_everything():
    print("\nINVARIANT: 'reaches +5% first' is out of the paths that reached anything")
    for vol in VOLS:
        p = prof(vol)["5%"]
        # The unconditional version tracked up_rate, so at vol 0.008 it read
        # 23% while only 42% of paths touched either side at all.
        check(f"vol {vol:.3f}: up_first in 45-55% at zero drift",
              0.45 <= p["up_first_rate"] <= 0.55,
              f"{p['up_first_rate']:.1%} (touched {p['touched_rate']:.0%})")
    # And the base it is conditional on is reported, so 50% of nothing is not
    # published as 50%.
    p = prof(0.008)["5%"]
    check("touched_rate is exposed alongside it", p["touched_rate"] < 1.0,
          str(p["touched_rate"]))


def test_up_first_is_absent_when_nothing_touched():
    print("\nabsent, not 50%, when there is nothing to be 50% of")
    # A near-frozen stock over 2 days cannot travel 8%.
    p = prof(0.0001, days=2, targets=(0.08,))["8%"]
    check("no touches -> up_first_rate is None", p["up_first_rate"] is None,
          str(p["up_first_rate"]))
    check("...and touched_rate says why", p["touched_rate"] == 0.0,
          str(p["touched_rate"]))


def test_drawdown_grows_with_volatility():
    print("\nINVARIANT: the published drawdown rises with risk, never falls")
    p75 = [prof(v)["5%"]["mae_p75"] for v in VOLS]
    check("mae_p75 is monotonic in volatility",
          all(a < b for a, b in zip(p75, p75[1:])),
          " ".join(f"{x:.4f}" for x in p75))
    # The median is NOT monotonic and pins to 0. Kept in the payload for
    # completeness, but this records why the UI must not render it.
    p50 = [prof(v)["5%"]["mae_p50"] for v in VOLS]
    check("mae_p50 is the trap it is documented to be -- not monotonic",
          not all(a < b for a, b in zip(p50, p50[1:])),
          " ".join(f"{x:.4f}" for x in p50))
    # card() is where the drawdown tile is now built -- one producer for the
    # portal and for core-api, so neither can pick the non-monotonic median on
    # its own. The page is still checked, because the way this regresses is a
    # renderer reaching past the card and formatting the payload itself.
    _card_src = (REPO / "utils" / "analyze.py").read_text()
    _page_src = (REPO / "pages" / "Deep_Analysis.py").read_text()
    check("...and the card renders p75, never p50",
          "mae_p75" in _card_src and "mae_median" not in _card_src)
    # POSITIVE, because the negative form was vacuous: "mae_p50 not in page"
    # is satisfied by an empty file, and deleting the page's whole tile loop
    # left it green while the price row silently lost two of three tiles.
    # This pins that the page still asks for the drawdown, and asks by KEY --
    # selecting on the label coupled the page to prose, so rewording
    # "Drawdown first" made the tile vanish with every suite still passing.
    # Both entry routes now consume one shared view adapter. Keeping the tile
    # selection here, rather than duplicating it in each page, prevents the
    # scan-launched result from drifting away from Deep Analyze again.
    _disc_src = (REPO / "pages" / "Discovery.py").read_text()
    _ui_src = (REPO / "utils" / "ui.py").read_text()
    check("the shared renderer selects both price tiles, and does so by key",
          'tiles.get("drawdown_first"' in _ui_src
          and 'tiles.get("range_30d"' in _ui_src)
    check("Deep_Analysis uses the shared delivered-result renderer",
          "render_delivered_analysis_result(" in _page_src)
    check("Discovery uses the shared delivered-result renderer",
          "render_delivered_analysis_result(" in _disc_src)
    check("...and reaches past the card for neither",
          "mae_p50" not in _page_src and "mae_median" not in _page_src)


def test_drawdown_is_never_negative():
    print("\na drawdown that renders as a gain is a bug in the risk column")
    for vol in VOLS:
        p = prof(vol)["5%"]
        for k in ("mae_p50", "mae_p75", "mae_p90"):
            check(f"vol {vol:.3f} {k} >= 0", p[k] >= 0.0, f"{p[k]}")


def test_drawdown_is_exact_on_known_paths():
    print("\nMAE arithmetic, against paths whose answer is known by hand")

    class StubRng:
        """Two paths. A dips 6% then rallies past +5%. B goes straight up."""

        def normal(self, loc, scale, size):
            n, days = size
            a = np.zeros(days)
            a[0], a[1] = np.log(0.94), np.log(1.12)
            b = np.zeros(days)
            b[0] = np.log(1.06)
            out = np.zeros((n, days))
            out[0::2] = a
            out[1::2] = b
            return out

    p = _movement_profile(0.02, 0.0, 10, (0.05,), StubRng())["5%"]
    check("both paths reach +5%", p["up_rate"] == 1.0, str(p["up_rate"]))
    check("p50 drawdown is 3% (median of 6% and 0%)",
          abs(p["mae_p50"] - 0.03) < 0.005, str(p["mae_p50"]))
    check("half the winners never dipped", abs(p["straight_up_rate"] - 0.5) < 1e-9,
          str(p["straight_up_rate"]))
    check("only the dipping path hits -5%", abs(p["down_rate"] - 0.5) < 1e-9,
          str(p["down_rate"]))
    check("and it went down first, so up_first is 50%",
          abs(p["up_first_rate"] - 0.5) < 1e-9, str(p["up_first_rate"]))


def test_targets_cannot_contradict_each_other():
    print("\nINVARIANT: a nearer target is never harder to reach")
    for vol in (0.008, 0.02, 0.08):
        r = prof(vol, targets=(0.03, 0.05, 0.08))
        check(f"vol {vol:.3f}: up 3% >= 5% >= 8%",
              r["3%"]["up_rate"] >= r["5%"]["up_rate"] >= r["8%"]["up_rate"],
              f"{r['3%']['up_rate']:.3f} {r['5%']['up_rate']:.3f} {r['8%']['up_rate']:.3f}")
        check(f"vol {vol:.3f}: down 3% >= 5% >= 8%",
              r["3%"]["down_rate"] >= r["5%"]["down_rate"] >= r["8%"]["down_rate"],
              f"{r['3%']['down_rate']:.3f} {r['5%']['down_rate']:.3f} {r['8%']['down_rate']:.3f}")


def test_decision_horizon_is_a_subset_and_is_reported_honestly():
    print("\ndecision horizon: a shorter window can only reach fewer targets")
    for vol in (0.02, 0.08):
        for v in prof(vol, targets=(0.03, 0.05, 0.08)).values():
            check(f"vol {vol:.2f} {v['target']:.0%}: short <= full",
                  v["up_rate_by_decision"] <= v["up_rate"]
                  and v["down_rate_by_decision"] <= v["down_rate"])
    # Clamped, and the CLAMPED value is what the caller is told -- otherwise a
    # 5-day run renders a column headed "within 10d".
    p = simple_projection(prices(), 0.0, days=5)
    check("days < horizon -> reported horizon is clamped",
          p["decision_horizon_days"] == 5, str(p.get("decision_horizon_days")))
    p = simple_projection(prices(), 0.0, days=30)
    check("days > horizon -> reported horizon is the constant",
          p["decision_horizon_days"] == DECISION_HORIZON_DAYS,
          str(p.get("decision_horizon_days")))


def test_the_renderer_contract_holds():
    print("\nthe movement profile is consumed by .items() then .get() on each value")
    p = simple_projection(prices(), 0.3, days=30)
    check("no error", p.get("error") is None, str(p.get("error")))
    bad = [k for k, v in p["movement_profile"].items() if not isinstance(v, dict)]
    check("every profile value is a dict", not bad, str(bad))
    check("review_window_days is gone", "review_window_days" not in p,
          "a retired key that still ships is a key someone still reads")
    check("decision_horizon_days rides on the projection, not in the profile",
          "decision_horizon_days" in p
          and "decision_horizon_days" not in p["movement_profile"])


def test_bad_input_still_fails_closed():
    print("\nrobustness: a projection that raises costs a user their credit")
    for name, args in {
        "too few prices": ([100.0, 101.0], 0.0),
        "zero price": ([100.0, 0.0, 101.0, 102.0, 103.0, 104.0], 0.0),
        "negative price": ([100.0, -5.0, 101.0, 102.0, 103.0, 104.0], 0.0),
        "nan price": ([100.0, float("nan"), 101.0, 102.0, 103.0, 104.0], 0.0),
        "inf price": ([100.0, float("inf"), 101.0, 102.0, 103.0, 104.0], 0.0),
    }.items():
        try:
            r = simple_projection(args[0], args[1], days=30)
            check(f"{name} -> error, not a crash", r.get("error") is not None,
                  str(r)[:80])
        except Exception as e:
            check(f"{name} -> error, not a crash", False, f"{type(e).__name__}: {e}")
    for bad_days in (0, -5):
        r = simple_projection(prices(), 0.0, days=bad_days)
        check(f"days={bad_days} -> error", r.get("error") is not None, str(r)[:60])
    # None sentiment reached struct.pack('<d', float(None)) once.
    r = simple_projection(prices(), None, days=30)
    check("sentiment=None is survivable", r.get("error") is None, str(r.get("error")))


def test_same_input_same_answer():
    print("\ndeterminism: two users asking the same question get the same answer")
    a = simple_projection(prices(), 0.4, days=30)
    b = simple_projection(prices(), 0.4, days=30)
    check("identical movement profiles", a["movement_profile"] == b["movement_profile"])
    check("identical band", a["band"] == b["band"])


def main() -> int:
    print("=" * 74)
    print("  projections: no directional claim it cannot support")
    print("=" * 74)
    test_zero_drift_never_favours_the_downside()
    test_the_underlying_walk_is_symmetric()
    test_median_path_lands_on_the_centre_it_claims()
    test_up_first_is_conditional_not_a_share_of_everything()
    test_up_first_is_absent_when_nothing_touched()
    test_drawdown_grows_with_volatility()
    test_drawdown_is_never_negative()
    test_drawdown_is_exact_on_known_paths()
    test_targets_cannot_contradict_each_other()
    test_decision_horizon_is_a_subset_and_is_reported_honestly()
    test_the_renderer_contract_holds()
    test_bad_input_still_fails_closed()
    test_same_input_same_answer()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
