"""Regression checks for the public demo snapshot attention contract."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from utils.demo_snapshots import (
    mention_label,
    normalize_scan_rows,
    snapshot_timestamp,
)


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED.append(name) if condition else FAILED.append((name, detail)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")


def test_scan_snapshot_preserves_real_mentions() -> None:
    rows = normalize_scan_rows([
        {
            "Ticker": " nvda ",
            "Mentions": "16",
            "Overall Sentiment": "Neutral",
        }
    ])
    assert rows == [{
        "Ticker": "NVDA",
        "Mentions": 16,
        "Overall Sentiment": "Neutral",
    }]


def test_incomplete_scan_snapshots_fail_instead_of_inventing_attention() -> None:
    rows = [
        {"Ticker": "NVDA"},
        {"Ticker": "NVDA", "Mentions": "unknown"},
        {"Ticker": "NVDA", "Mentions": -1},
        {"Ticker": "", "Mentions": 4},
    ]
    for row in rows:
        try:
            normalize_scan_rows([row])
        except ValueError:
            continue
        raise AssertionError(f"invalid row was accepted: {row!r}")


def test_attention_label_uses_only_snapshot_data() -> None:
    assert mention_label({"Mentions": 0}) == "0 mentions"
    assert mention_label({"Mentions": 1}) == "1 mention"
    assert mention_label({"Mentions": "12"}) == "12 mentions"
    assert mention_label({}) == "Attention unavailable"
    assert mention_label({"Mentions": "invalid"}) == "Attention unavailable"


def test_snapshot_timestamp_is_explicit_utc() -> None:
    value = snapshot_timestamp()
    assert value.endswith("Z")
    assert datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo


def test_all_snapshot_publishers_keep_the_contract() -> None:
    admin = (REPO / "pages" / "Admin.py").read_text(encoding="utf-8")
    discovery = (REPO / "pages" / "Discovery.py").read_text(encoding="utf-8")
    home = (REPO / "pages" / "Home.py").read_text(encoding="utf-8")

    assert "normalize_scan_rows(validated_rows)" in admin
    assert "normalize_scan_rows(" in discovery
    assert '"Mentions", "Sample Tweets"' not in discovery
    assert "snapshot_timestamp()" in admin
    assert "snapshot_timestamp()" in discovery
    assert "mention_label(row)" in home
    assert "attention_fallback" not in home


def main() -> int:
    print("=" * 72)
    print("  Public demo snapshot attention contract")
    print("=" * 72)
    tests = [
        test_scan_snapshot_preserves_real_mentions,
        test_incomplete_scan_snapshots_fail_instead_of_inventing_attention,
        test_attention_label_uses_only_snapshot_data,
        test_snapshot_timestamp_is_explicit_utc,
        test_all_snapshot_publishers_keep_the_contract,
    ]
    for test in tests:
        try:
            test()
        except Exception as exc:
            check(test.__name__, False, str(exc))
        else:
            check(test.__name__, True)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for name, detail in FAILED:
        print(f"  FAIL  {name}: {detail}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
