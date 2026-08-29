"""Regression checks for the public demo snapshot social-post contract."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from utils.demo_snapshots import (
    build_public_demo_bundle,
    normalize_scan_rows,
    social_posts_value,
    snapshot_timestamp,
    validate_public_demo_bundle,
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


def test_incomplete_scan_snapshots_fail_instead_of_inventing_counts() -> None:
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


def test_social_posts_value_is_compact_and_uses_only_snapshot_data() -> None:
    assert social_posts_value({"Mentions": 0}) == "0"
    assert social_posts_value({"Mentions": 1}) == "1"
    assert social_posts_value({"Mentions": "12"}) == "12"
    assert social_posts_value({}) == "—"
    assert social_posts_value({"Mentions": "invalid"}) == "—"


def test_snapshot_timestamp_is_explicit_utc() -> None:
    value = snapshot_timestamp()
    assert value.endswith("Z")
    assert datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo


def test_public_bundle_is_one_coherent_scan_and_analysis() -> None:
    bundle = build_public_demo_bundle(
        scan_rows=[
            {
                "Ticker": "way",
                "Overall Sentiment": "Neutral",
                "Mentions": "21",
            },
            {
                "Ticker": "low",
                "Overall Sentiment": "Limited signal",
                "Mentions": 1,
            },
        ],
        scan_sector=" Tech ",
        analysis_ticker="WAY",
        analysis_summary={
            "recommendation": "Watch",
            "confidence": "Moderate",
            "rationale": ["Evidence is mixed."],
        },
    )
    assert bundle["scan"]["sector"] == "tech"
    assert [row["Ticker"] for row in bundle["scan"]["validated_rows"]] == [
        "WAY"
    ]
    assert bundle["deep_analysis"]["ticker"] == "WAY"
    assert bundle["deep_analysis"]["sector"] == "tech"
    assert bundle["deep_analysis"]["analysis_results"] == {
        "public_summary": {
            "recommendation": "Watch",
            "confidence": "Moderate",
            "rationale": ["Evidence is mixed."],
        }
    }


def test_public_bundle_rejects_analysis_outside_current_scan() -> None:
    try:
        validate_public_demo_bundle(
            {
                "scan": {
                    "sector": "tech",
                    "validated_rows": [{
                        "Ticker": "WAY",
                        "Overall Sentiment": "Neutral",
                        "Mentions": 21,
                    }],
                },
                "deep_analysis": {
                    "ticker": "NVDA",
                    "sector": "tech",
                    "analysis_results": {"recommendation": "Watch"},
                },
            }
        )
    except ValueError as exc:
        assert "current Market Scan" in str(exc)
    else:
        raise AssertionError("analysis outside the current scan was accepted")


def test_all_snapshot_publishers_keep_the_contract() -> None:
    admin = (REPO / "pages" / "Admin.py").read_text(encoding="utf-8")
    discovery = (REPO / "pages" / "Discovery.py").read_text(encoding="utf-8")
    home = (REPO / "pages" / "Home.py").read_text(encoding="utf-8")

    assert "build_public_demo_bundle(" in admin
    assert "publish_public_demo(" in admin
    assert "load_latest_public_demo(" in admin
    assert "load_latest_public_demo()" in home
    assert "st.session_state.demo_scan_sector = sector" in discovery
    assert '"Mentions", "Sample Tweets"' not in discovery
    assert "scan_latest.json\").write_text" not in admin
    assert "scan_latest.json\").write_text" not in discovery
    assert "deep_latest.json\").write_text" not in admin
    assert "deep_latest.json\").write_text" not in discovery
    assert "social_posts_value(row)" in home
    assert "Social posts" in home
    assert "Attention unavailable" not in home
    assert '<table class="ss-hero-preview-table">' in home
    assert 'scope="col"' in home and 'scope="row"' in home
    assert "attention_fallback" not in home


def main() -> int:
    print("=" * 72)
    print("  Public demo snapshot social-post contract")
    print("=" * 72)
    tests = [
        test_scan_snapshot_preserves_real_mentions,
        test_incomplete_scan_snapshots_fail_instead_of_inventing_counts,
        test_social_posts_value_is_compact_and_uses_only_snapshot_data,
        test_snapshot_timestamp_is_explicit_utc,
        test_public_bundle_is_one_coherent_scan_and_analysis,
        test_public_bundle_rejects_analysis_outside_current_scan,
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
