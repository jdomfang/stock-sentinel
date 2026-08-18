#!/usr/bin/env python3
"""Pin the sector benchmark — the ONLY change in this project that issues Buys.

Every other phase added measurement. This one removes a guardrail: a decline the
sector explains no longer vetoes a sentiment-led Buy. So it is the one place
where a defect costs a user money rather than costing us a column of data, and
two independent reviews each reproduced a Buy on a double-digit drawdown before
these guards existed.

WHAT EACH GUARD EXISTS TO STOP, all of them reproduced:

  1. A 7-session benchmark cancelled a 21-session, 9x-volume collapse, because
     the window was taken from whichever series was shorter.

  2. A halted ticker whose bars ended three sessions early was compared against
     an ETF ending today. The benchmark window slid forward into sessions the
     ticker never traded, and in a selloff that made the benchmark look worse
     and inflated excess toward exemption. A halted stock is the last thing
     that should get an automatic pass.

  3. A flat -5% excess bar meant roughly one sigma for a mega-cap and under half
     a sigma for a small cap -- holding the names that need the veto most to the
     strictest standard while waving through ~83% of mega-cap declines.

  4. `unconfirmed` -- a material decline with NO volume data -- was exemptible,
     so having less data made the system less careful.

  5. A stale or wrong benchmark reading flat could manufacture a pass out of
     nothing.

  6. Nothing bounded the depth: a stock down 30% in a sector down 28% reached
     Buy on sentiment alone, with "price not contradicting" in the reason line
     and an EMPTY would_change.

Usage:
    python3 tests/test_relative.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import modules as M  # noqa: E402
from utils import relative as R  # noqa: E402
from utils.evidence import EvidenceRow  # noqa: E402
from utils.verdict import adjudicate  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

DATE = "2026-08-14"
HEAVY = [1] * 20 + [9]


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


def row(i, **kw):
    d = dict(post_id=str(i), channel="social_base", text=f"p{i}", author_id=f"a{i}",
             target_match_type="cashtag", target_subject_status="primary",
             evidence_types=("directional_view",), margin=0.6, scored=True,
             cluster_id=i, spam_risk="low", evidence_eligible=True)
    d.update(kw)
    return EvidenceRow(**d)


def paths(total_t, total_b, resid_vol=0.02, n=21, seed=1):
    """A stock that is its sector plus idiosyncratic noise, ending at total_t."""
    rng = np.random.default_rng(seed)
    b_steps = rng.normal(np.log1p(total_b) / (n - 1), 0.012, n - 1)
    t_steps = b_steps + rng.normal(0, resid_vol, n - 1)
    t_steps += (np.log1p(total_t) - t_steps.sum()) / (n - 1)
    b = 100 * np.exp(np.concatenate([[0], np.cumsum(b_steps)]))
    t = 100 * np.exp(np.concatenate([[0], np.cumsum(t_steps)]))
    return list(t), list(b)


def px(t, b, *, vols=HEAVY, bd=DATE, tbd=DATE):
    return M.price(t, vols,
                   benchmark_prices=({"prices": b, "bar_date": bd} if b else None),
                   benchmark="XLI", bar_date=tbd)


def test_a_short_benchmark_cannot_cancel_a_long_veto():
    print("\nGUARD 1: the comparison must span what the veto spans")
    t, b = paths(-0.18, -0.005)
    p = px(t, b[:7])
    check("a 7-session benchmark is refused", p.status == "caution", p.status)
    check("...and the refusal says why", "7 of 21" in p.detail, p.detail)
    check("...and no excess is recorded", p.excess_return_20d is None,
          str(p.excess_return_20d))
    r = R.excess_return(t, b[:7], window=21)
    check("excess_return refuses rather than shortening",
          r["refused"] is not None and r["excess"] is None, str(r))


def test_series_must_end_on_the_same_session():
    print("\nGUARD 2: a halted stock does not get an automatic pass")
    t, b = paths(-0.16, -0.13, n=18)
    _, b21 = paths(-0.16, -0.13, n=21)
    p = px(t, b21, bd="2026-08-17", tbd="2026-08-14")
    check("mismatched bar dates are refused", p.status != "market_wide", p.status)
    check("...and the refusal names both dates",
          "different sessions" in p.detail, p.detail)
    # Matching dates and lengths still work.
    p = px(*paths(-0.20, -0.19))
    check("matching dates are compared normally", p.excess_return_20d is not None)
    # Dates are REQUIRED, not merely compared when present: equal bar COUNTS do
    # not prove equal dates, since a thin name's 21 bars can span 24 calendar
    # sessions while the ETF's 21 span 21. Undetectable without dates, and it
    # biases toward exemption -- so no dates means no exemption.
    r = R.excess_return(t, b21[:18], window=21)
    check("no dates supplied -> refused outright", r["refused"] is not None, str(r))
    check("...and it says the dates were the reason",
          "align" in (r["refused"] or ""), str(r["refused"]))
    r = R.excess_return(t, b21[:18], window=21, ticker_bar_date=DATE)
    check("one date is not enough either", r["refused"] is not None, str(r))


def test_the_boundary_scales_with_the_stock():
    print("\nGUARD 3: the bar is this pair's own dispersion, not a flat 5%")
    quiet = px(*paths(-0.20, -0.19, resid_vol=0.010))
    vol = px(*paths(-0.20, -0.19, resid_vol=0.035))
    check("a quiet name has a narrow measured spread",
          quiet.excess_noise is not None and quiet.excess_noise < 0.09,
          str(quiet.excess_noise))
    check("a volatile name has a wide one",
          vol.excess_noise is not None and vol.excess_noise > 0.12,
          str(vol.excess_noise))
    check("both are exempted when they track their sector",
          quiet.status == "market_wide" and vol.status == "market_wide",
          f"{quiet.status} / {vol.status}")
    # And a real divergence is NOT exempted, at either volatility.
    diverged = px(*paths(-0.25, -0.12, resid_vol=0.020))
    check("a 13-point shortfall beyond the spread still vetoes",
          diverged.status == "caution", f"{diverged.status} {diverged.detail}")
    check("the spread is reported on the card",
          "usual spread" in quiet.detail, quiet.detail)


def test_missing_volume_is_never_exempted():
    print("\nGUARD 4: less data must not make the system less careful")
    t, b = paths(-0.18, -0.17)
    p = px(t, b, vols=None)
    check("no volume -> unconfirmed, never market_wide", p.status == "unconfirmed",
          p.status)
    check("...even though the excess would have cleared the bar",
          p.excess_return_20d is not None and p.excess_noise is not None
          and p.excess_return_20d >= -p.excess_noise,
          f"excess={p.excess_return_20d} noise={p.excess_noise}")


def test_the_sector_must_actually_have_fallen():
    print("\nGUARD 5: a flat benchmark cannot manufacture a pass")
    t, b = paths(-0.18, 0.00)
    p = px(t, b)
    check("stock down 18%, sector flat -> veto stands", p.status == "caution",
          f"{p.status} {p.detail}")
    t, b = paths(-0.18, 0.11)
    check("stock down 18%, sector UP -> veto stands", px(t, b).status == "caution")


def test_there_is_a_floor():
    print("\nGUARD 6: attribution stops being the only question at some depth")
    p = px(*paths(-0.28, -0.27))
    check("stock -28% in a sector -27% is NOT exempted", p.status == "caution",
          f"{p.status} {p.detail}")
    check("...even though the excess is tiny",
          p.excess_return_20d is not None and abs(p.excess_return_20d) < 0.05,
          str(p.excess_return_20d))
    p = px(*paths(-0.20, -0.19))
    check("above the floor it still works", p.status == "market_wide", p.status)
    check("the floor is a named constant",
          M.PRICE_EXEMPTION_FLOOR == -0.25, str(M.PRICE_EXEMPTION_FLOOR))


def test_a_market_wide_buy_explains_itself():
    print("\nthe card cannot say 'price not contradicting' over a double-digit fall")
    t, b = paths(-0.20, -0.19)
    v = adjudicate([row(i) for i in range(8)], t, HEAVY,
                   benchmark_prices={"prices": b, "bar_date": DATE},
                   benchmark="XLI", bar_date=DATE)
    if v.recommendation == "Buy":
        check("the reason names the actual fall",
              "%" in v.reason and "price not contradicting" not in v.reason,
              v.reason)
        check("the reason attributes it to the sector",
              "sector" in v.reason.lower(), v.reason)
        check("would_change is NOT empty on a market_wide Buy",
              bool(v.would_change), str(v.would_change))
        check("...and names the sector ceasing to explain it",
              any("sector" in w.lower() for w in v.would_change),
              str(v.would_change))
    else:
        check("market_wide reached a Buy for this fixture", False,
              f"{v.recommendation} via {v.branch}")


def test_caution_stays_true_for_the_telemetry():
    print("\nthe fact that the veto WOULD have fired must survive into the log")
    p = px(*paths(-0.20, -0.19))
    check("status carries the exemption", p.status == "market_wide", p.status)
    check("caution still records that the veto would have fired",
          p.caution is True, str(p.caution))
    check("the window is recorded, so 7- and 21-session excesses do not pool",
          p.excess_window == 21, str(p.excess_window))


def test_wrong_benchmarks_are_refused_not_approximated():
    print("\nno benchmark beats a wrong one")
    for slug in ("consumer", "communication"):
        check(f"{slug!r} has no ETF", R.etf_for(slug) == "", R.etf_for(slug))
    check("'Consumer Staples' does not resolve via the Nasdaq name either",
          R.etf_for("Consumer Staples") == "", R.etf_for("Consumer Staples"))
    for slug, etf in (("tech", "XLK"), ("materials", "XLB"),
                      ("real estate", "XLRE"), ("Basic Materials", "XLB")):
        check(f"{slug!r} -> {etf}", R.etf_for(slug) == etf, R.etf_for(slug))
    for junk in ("", "unknown", "nonsense", None):
        check(f"{junk!r} -> no ETF", R.etf_for(junk) == "")
    check("an ETF is not benchmarked against itself",
          R.benchmark_prices("XLK", "tech") == ("", None))


def test_no_benchmark_falls_back_to_the_absolute_veto():
    print("\nevery failure path leaves the old behaviour intact")
    t, _ = paths(-0.18, -0.17)
    check("no benchmark at all -> caution", px(t, None).status == "caution")
    for bad in ([], ["x", None], [0, -5, 3], [100.0]):
        p = px(t, bad)
        check(f"benchmark {str(bad)[:18]} -> caution", p.status == "caution", p.status)
    check("a rising tape is still neutral",
          px(*paths(0.05, 0.02)).status == "neutral")


def test_it_never_raises():
    print("\nrobustness: this runs inside the paid path")
    for name, args in {
        "None/None": (None, None),
        "empty": ([], []),
        "strings": (["a", "b"], ["c"]),
        "zeros": ([0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0]),
        "inf": ([float("inf")] * 6, [1.0] * 6),
        "nan": ([float("nan")] * 6, [1.0] * 6),
        "one point": ([100.0], [100.0]),
    }.items():
        try:
            r = R.excess_return(*args)
            check(f"excess_return({name}) -> dict, no raise", isinstance(r, dict))
        except Exception as e:
            check(f"excess_return({name}) -> dict, no raise", False,
                  f"{type(e).__name__}: {e}")
    try:
        M.price(None, None, benchmark_prices={"prices": "junk", "bar_date": 5})
        M.price([100.0] * 8, None, benchmark_prices="not a dict")
        check("price() survives junk benchmarks", True)
    except Exception as e:
        check("price() survives junk benchmarks", False, f"{type(e).__name__}: {e}")


def test_no_import_time_side_effects():
    print("\nno network, no secrets, nothing heavy at import")
    src = (REPO / "utils" / "relative.py").read_text()
    head = src.split("logger = logging.getLogger")[0]
    for banned in ("import streamlit", "from utils.finance", "from utils.seed",
                   "requests", "urllib"):
        check(f"{banned!r} is not at module scope", banned not in head)
    check("the sector lookup is cached", "_SECTOR_CACHE" in src)
    check("negative lookups are cached too",
          "_SECTOR_CACHE[t] = slug" in src)


def main() -> int:
    print("=" * 74)
    print("  relative: the one change that makes the product say Buy more often")
    print("=" * 74)
    test_a_short_benchmark_cannot_cancel_a_long_veto()
    test_series_must_end_on_the_same_session()
    test_the_boundary_scales_with_the_stock()
    test_missing_volume_is_never_exempted()
    test_the_sector_must_actually_have_fallen()
    test_there_is_a_floor()
    test_a_market_wide_buy_explains_itself()
    test_caution_stays_true_for_the_telemetry()
    test_wrong_benchmarks_are_refused_not_approximated()
    test_no_benchmark_falls_back_to_the_absolute_veto()
    test_it_never_raises()
    test_no_import_time_side_effects()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
