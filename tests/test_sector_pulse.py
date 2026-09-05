#!/usr/bin/env python3
"""Prove the sector pulse cannot be fooled by the three things that fooled raw volume.

WHAT IS AT RISK

1. ONE NAME BECOMING A SECTOR. On 2026-08-19 healthcare "doubled" and 70% of it
   was MRNA. The pulse must call that an EVENT, name the ticker, and leave the
   sector's breadth where the other 384 names put it.
2. A CALENDAR DAY BECOMING A SIGNAL. Month-end lifts every sector's volume with
   no conviction behind it. Flat returns on heavy volume must not read as
   accumulation, and the day must carry its flag.
3. A SPIKE POISONING ITS OWN BASELINE. Against a plain rolling median, MRNA
   read as quieter than normal a week after its spike while doing 6-10x its
   pre-event volume. The robust baseline must not move when one day does.
4. BREAKING THE PRICE SYNC. run() is called after the prices are written; it
   must never raise, and a write failure must come back as a summary the
   caller can turn into its own red healthcheck.

No network. Bars are synthetic and injected through run()'s _loaders; writes
are captured through _writer. Assertions are on returned rows and captured
writes, never on log text or key names.

Usage:
    python3 tests/test_sector_pulse.py
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

os.environ.setdefault("SUPABASE_URL", "https://stub.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "stub-key")
os.environ.setdefault("SUPABASE_ANON_KEY", "stub-anon")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import sector_pulse as P  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


# ------------------------------------------------------------- fixtures --

def trading_days(n: int, end: date = date(2026, 9, 2)) -> list[str]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


DAYS = trading_days(30)
LAST = DAYS[-1]


def flat_name(price=50.0, dv=20e6):
    """A name doing the same thing every day: close 50, $20M volume, no drift."""
    return {d: (price, dv) for d in DAYS}


def build(sector_names: dict[str, dict[str, dict]], names: dict[str, str] | None = None):
    """(loaders, captured) for run(). sector_names: sector -> ticker -> series."""
    sec = {t: s for s, ts in sector_names.items() for t in ts}
    bars = {t: series for ts in sector_names.values() for t, series in ts.items()}
    nm = {t: (names or {}).get(t, f"{t} Inc") for t in sec}
    captured: dict = {}

    def load_s(base, key):
        return sec, nm

    def load_b(base, key, since):
        return bars

    def writer(base, key, rows):
        captured["rows"] = rows
    return (load_s, load_b), writer, captured


def run(sector_names, names=None, day=None, dry_run=False, writer=None):
    loaders, w, captured = build(sector_names, names)
    s = P.run(day, dry_run=dry_run, _loaders=loaders, _writer=writer or w)
    return s, captured


def rows_by_sector(summary):
    return {r["sector"]: r for r in summary["rows"]}


# ---------------------------------------------------------------- tests --

def test_event_is_named_not_spread():
    print("\n1. one name spiking is an EVENT, and the other names decide breadth")
    quiet = {f"H{i:02d}": flat_name() for i in range(30)}
    mrna = flat_name(price=60.0, dv=0.4e9)
    d1 = DAYS[-6]
    # a 90x day five sessions ago, then still 8x for the window
    mrna[d1] = (170.0, 36e9)
    for d in DAYS[-5:]:
        mrna[d] = (150.0, 3.2e9)
    quiet["MRNA"] = mrna
    s, _ = run({"healthcare": quiet}, dry_run=True)
    r = rows_by_sector(s)["healthcare"]
    check("run succeeded", s["ok"], str(s["error"]))
    check("MRNA is the top contributor by name", r["top_contrib"][0]["ticker"] == "MRNA", str(r["top_contrib"][:1]))
    check("its share of the rise is above the event threshold", r["top_contrib"][0]["share_of_rise"] >= P.EVENT_SHARE, str(r["top_contrib"][0]))
    check("its relative volume is a climax", r["top_contrib"][0]["rel_vol"] >= P.CLIMAX_REL_VOL, str(r["top_contrib"][0]))
    check("state is event", r["state"] == "event", r["state"])
    check("breadth counts one elevated name in 31, not a doubled sector", abs(r["breadth"] * 31 - 1) < 0.01, str(r["breadth"]))


def test_month_end_is_not_accumulation():
    print("\n2. every sector heavy on a month-end, flat prices: no accumulation, flag set")
    me = date(2026, 8, 31)   # Monday, last weekday of August
    days = trading_days(30, end=me)
    names = {}
    for i in range(25):
        series = {d: (40.0, 15e6) for d in days}
        series[days[-1]] = (40.0, 45e6)          # 3x volume, price unchanged
        names[f"E{i:02d}"] = series
    loaders, w, cap = build({"energy": names})
    s = P.run(me, dry_run=True, _loaders=loaders, _writer=w)
    r = rows_by_sector(s)["energy"]
    check("breadth is high (everyone is loud)", r["breadth"] == 1.0, str(r["breadth"]))
    check("but the state is not accumulating (no return, no direction)", r["state"] != "accumulating", r["state"])
    check("the day carries the month_end flag", r["calendar_flag"] == "month_end", str(r["calendar_flag"]))
    check("up/down ratio is null: flat prices produce no up or down volume", r["ud_ratio_5d"] is None, str(r["ud_ratio_5d"]))


def test_accumulation_and_its_mirror():
    print("\n3. a broad multi-day build is ACCUMULATING; the same bars inverted are DISTRIBUTING")
    def build_sector(sign):
        names = {}
        for i in range(40):
            price, series = 30.0, {}
            for k, d in enumerate(DAYS):
                if k >= len(DAYS) - 5:
                    price *= 1 + sign * 0.012           # five straight days, 1.2% each
                    dv = 22e6 * (1.6 + 0.2 * (k - (len(DAYS) - 5)))   # rising, 1.6x..2.4x
                else:
                    dv = 22e6
                series[d] = (round(price, 4), dv)
            names[f"S{i:02d}"] = series
        return names
    s_up, _ = run({"industrials": build_sector(+1)}, dry_run=True)
    r = rows_by_sector(s_up)["industrials"]
    check("up-volume dominates", r["ud_ratio_5d"] is None or r["ud_ratio_5d"] >= P.UD_ACCUMULATING, str(r["ud_ratio_5d"]))
    check("breadth is broad", r["breadth"] >= P.BREADTH_MIN, str(r["breadth"]))
    check("equal-weight 5d return is positive", r["eq_return_5d"] > 0, str(r["eq_return_5d"]))
    check("no single name dominates", r["top_contrib"][0]["share_of_rise"] < P.EVENT_SHARE, str(r["top_contrib"][0]))
    check("state is accumulating", r["state"] == "accumulating", r["state"])
    s_dn, _ = run({"industrials": build_sector(-1)}, dry_run=True)
    r2 = rows_by_sector(s_dn)["industrials"]
    check("inverted: down-volume dominates", r2["ud_ratio_5d"] is not None and r2["ud_ratio_5d"] <= P.UD_DISTRIBUTING, str(r2["ud_ratio_5d"]))
    check("inverted: state is distributing", r2["state"] == "distributing", r2["state"])
    check("inverted: return is negative", r2["eq_return_5d"] < 0, str(r2["eq_return_5d"]))


def test_robust_baseline_ignores_a_spike():
    print("\n4. the baseline does not move when one day does")
    calm = [20e6] * 20
    spiked = calm[:]
    spiked[7] = 1.8e9
    check("baseline identical with and without a 90x day", P.robust_baseline(calm) == P.robust_baseline(spiked),
          f"{P.robust_baseline(calm)} vs {P.robust_baseline(spiked)}")
    check("too little history yields no baseline", P.robust_baseline([20e6] * (P.BASELINE_MIN_PRESENT - 1)) is None)


def test_eligibility_filters():
    print("\n5. instruments, penny names and thin names are not eligible")
    names = {f"F{i:02d}": flat_name() for i in range(20)}
    names["SPAC"] = flat_name(dv=500e6)
    names["PENNY"] = flat_name(price=2.0, dv=500e6)
    names["THIN"] = flat_name(dv=1e6)
    labels = {"SPAC": "Rising Dragon Acquisition Corp"}
    s, _ = run({"finance": names}, names=labels, dry_run=True)
    r = rows_by_sector(s)["finance"]
    check("the three excluded names are not counted", r["n_eligible"] == 20, str(r["n_eligible"]))
    check("none of them can appear as a contributor", not {c["ticker"] for c in r["top_contrib"]} & {"SPAC", "PENNY", "THIN"}, str(r["top_contrib"]))


def test_states_are_the_table_vocabulary():
    print("\n6. every state the module emits is one the table CHECK accepts")
    s, _ = run({"tech": {f"T{i:02d}": flat_name() for i in range(15)},
                "utilities": {f"U{i:02d}": flat_name() for i in range(15)}}, dry_run=True)
    check("two sectors computed", len(s["rows"]) == 2, str(len(s["rows"])))
    check("all states in vocabulary", all(r["state"] in P.STATES for r in s["rows"]), str([r["state"] for r in s["rows"]]))
    check("shares are within 0..1", all(0 <= r["breadth"] <= 1 and 0 <= r["pct_up_5d"] <= 1 for r in s["rows"]))
    check("day counts are within 0..5", all(0 <= r["acc_days_5d"] <= 5 and 0 <= r["dist_days_5d"] <= 5 for r in s["rows"]))


def test_calendar_flags():
    print("\n7. calendar flags")
    check("third Friday is opex", P.calendar_flag(date(2026, 9, 18)) == "opex")
    check("second Friday is not", P.calendar_flag(date(2026, 9, 11)) is None)
    check("last weekday of September is quarter_end", P.calendar_flag(date(2026, 9, 30)) == "quarter_end")
    check("last weekday of August is month_end", P.calendar_flag(date(2026, 8, 31)) == "month_end")
    check("last weekday of July, a Friday, is month_end", P.calendar_flag(date(2026, 7, 31)) == "month_end")
    check("an ordinary Tuesday is unflagged", P.calendar_flag(date(2026, 9, 1)) is None)


def test_failure_policy_and_dry_run():
    print("\n8. failures come back as a summary; dry runs write nothing")
    names = {f"M{i:02d}": flat_name() for i in range(15)}

    def broken(base, key, rows):
        raise RuntimeError("HTTP 500 from PostgREST")
    s, _ = run({"materials": names}, writer=broken)
    check("a write failure does not raise", True)
    check("...and is reported", s["ok"] is False and "HTTP 500" in (s["error"] or ""), str(s["error"]))
    check("...with rows still computed for diagnosis", len(s["rows"]) == 1)
    s2, cap = run({"materials": names}, dry_run=True)
    check("dry run is ok", s2["ok"] is True)
    check("dry run never calls the writer", "rows" not in cap)
    check("dry run reports zero written", s2["written"] == 0)
    s3, cap3 = run({"materials": names})
    check("a real run writes exactly the computed rows", cap3.get("rows") == s3["rows"] and s3["written"] == 1)

    def boom_loader(base, key):
        raise ValueError("ticker_master unreadable")
    s4 = P.run(_loaders=(boom_loader, lambda b, k, s: {}), _writer=lambda *a: None)
    check("a loader failure does not raise and is reported", s4["ok"] is False and "ticker_master" in (s4["error"] or ""), str(s4["error"]))
    s5 = P.run(day=date(2020, 1, 1), _loaders=build({"tech": {"T": flat_name()}})[0], _writer=lambda *a: None)
    check("a day with no bar is reported, not computed", s5["ok"] is False and "no bar" in (s5["error"] or ""), str(s5["error"]))


def test_latest_reader():
    print("\n9. latest() reads through the RPC and degrades to []")
    import json as _json
    seen: dict = {}

    class Resp:
        def __init__(self, body): self._b = body
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = _json.loads(req.data)
        seen["auth"] = req.get_header("Authorization")
        return Resp(_json.dumps([{"sector": "energy", "state": "accumulating"}]).encode())
    orig = P.urllib.request.urlopen
    P.urllib.request.urlopen = fake_urlopen
    try:
        rows = P.latest(days=4)
        check("rows come back as plain dicts", rows == [{"sector": "energy", "state": "accumulating"}], str(rows))
        check("the call goes to the reader function, not the table", seen["url"].endswith(f"/rpc/{P.READER}"), seen["url"])
        check("the requested window is passed", seen["body"] == {"days": 4}, str(seen["body"]))
        check("the portal read path sends the ANON key, never the service key",
              seen["auth"] == "Bearer stub-anon", str(seen["auth"]))
        P.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(OSError("down"))
        check("a transport failure yields [] rather than raising", P.latest() == [])
    finally:
        P.urllib.request.urlopen = orig


def main() -> int:
    print("=" * 74)
    print("  sector_pulse: events named, calendars flagged, baselines robust, never raises")
    print("=" * 74)
    for t in (test_event_is_named_not_spread, test_month_end_is_not_accumulation, test_accumulation_and_its_mirror,
              test_robust_baseline_ignores_a_spike, test_eligibility_filters, test_states_are_the_table_vocabulary,
              test_calendar_flags, test_failure_policy_and_dry_run, test_latest_reader):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            FAILED.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL  {t.__name__} CRASHED  <- {type(e).__name__}: {e}")
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
