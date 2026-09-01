"""Regression checks for the public demo snapshot social-post contract."""

from __future__ import annotations

from datetime import datetime
from copy import deepcopy
from pathlib import Path
import sys


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from utils.demo_snapshots import (
    build_demo_source_payload,
    build_public_demo_bundle,
    normalize_scan_rows,
    social_posts_value,
    snapshot_timestamp,
    validate_demo_publication,
    validate_public_demo_bundle,
)


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED.append(name) if condition else FAILED.append((name, detail)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")


def _card(ticker: str = "WAY") -> dict:
    return {
        "ticker": ticker,
        "sector": "tech",
        "verdict": "Watch",
        "confidence": "Moderate",
        "adjudicator": "ledger",
        "headline": "Hold — monitor closely",
        "confidence_note": "Useful evidence with uncertainty",
        "avg_sentiment": 0.06,
        "rationale": ["Evidence is mixed."],
        "reason": "Evidence is mixed.",
        "would_change": ["More independent confirmation"],
        "confidence_notes": ["One risk remains"],
        "pillars": [{"name": "quality", "passed": True}],
        "tiles": [
            {"key": "last_price", "label": "Last Price", "value": "$24.17"},
            {"key": "range_30d", "label": "30d range (vol)", "value": "-8.0% to 9.0%"},
        ],
        "evidence": {
            "independent_voices": 2,
            "own_voices": 3,
            "quality": 0.72,
            "quality_tier": "usable",
            "mentions": 21,
            "price_points": 25,
        },
        "movement": {
            "band_pct": 8.5,
            "horizon_days": 10,
            "targets": {"5%": {"probability": 0.42}},
        },
    }


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
                "Company Name": "Waystar Holding Corp.",
                "Overall Sentiment": "Neutral",
                "Mentions": "21",
                "Evidence": 2,
                "Avg Sentiment Score": 0.06,
                "Sample Tweets": ["raw post stays private"],
            },
            {
                "Ticker": "low",
                "Overall Sentiment": "Limited signal",
                "Mentions": 1,
            },
        ],
        scan_sector=" Tech ",
        analysis_ticker="WAY",
        analysis_card=_card(),
    )
    assert bundle["scan"]["sector"] == "tech"
    assert bundle["scan"]["total_results"] == 2
    assert [row["Ticker"] for row in bundle["scan"]["validated_rows"]] == [
        "WAY", "LOW"
    ]
    assert bundle["scan"]["validated_rows"][1]["Evidence State"] == (
        "Needs more evidence"
    )
    assert bundle["deep_analysis"]["ticker"] == "WAY"
    assert bundle["deep_analysis"]["sector"] == "tech"
    assert "Sample Tweets" not in bundle["scan"]["validated_rows"][0]
    assert all(
        "Sample Tweets" not in row
        for row in bundle["scan"]["validated_rows"]
    )
    public_card = bundle["deep_analysis"]["public_card"]
    assert public_card["verdict"] == "Watch"
    assert public_card["evidence"]["price_points"] == 25
    assert public_card["movement"]["horizon_days"] == 10
    assert "pillars" not in public_card


def test_legacy_v2_publication_without_total_results_remains_valid() -> None:
    rows = [
        {
            "Ticker": "WAY",
            "Overall Sentiment": "Neutral",
            "Mentions": 21,
            "Evidence": 2,
            "Avg Sentiment Score": 0.06,
        },
        {
            "Ticker": "LOW",
            "Overall Sentiment": "Limited signal",
            "Mentions": 1,
        },
    ]
    public = build_public_demo_bundle(rows, "tech", "WAY", _card())
    legacy_public = deepcopy(public)
    legacy_public["scan"].pop("total_results")
    legacy_public["scan"]["validated_rows"] = [
        row for row in legacy_public["scan"]["validated_rows"]
        if row["Overall Sentiment"] == "Neutral"
    ]
    source = build_demo_source_payload(
        rows,
        "tech",
        "WAY",
        _card(),
        {"Real-Time Market Sentiment": {"status": "complete"}},
        scan_generated_at=public["scan"]["generated_at"],
        analysis_generated_at=public["deep_analysis"]["generated_at"],
    )

    normalized, _ = validate_demo_publication(legacy_public, source)
    assert normalized["scan"]["total_results"] == 1


def test_public_bundle_can_analyze_a_real_inconclusive_scan_row() -> None:
    bundle = build_public_demo_bundle(
        scan_rows=[{
            "Ticker": "LOW",
            "Company Name": "Low Evidence Corp.",
            "Overall Sentiment": "Single mention",
            "Mentions": 1,
            "Evidence": 1,
            "Avg Sentiment Score": 0.02,
            "Sample Tweets": ["private post"],
        }],
        scan_sector="tech",
        analysis_ticker="LOW",
        analysis_card=_card("LOW"),
    )
    row = bundle["scan"]["validated_rows"][0]
    assert row["Ticker"] == "LOW"
    assert row["Evidence State"] == "Needs more evidence"
    assert "Sample Tweets" not in row


def test_public_bundle_rejects_total_below_public_result_count() -> None:
    bundle = build_public_demo_bundle(
        [{
            "Ticker": "WAY",
            "Overall Sentiment": "Neutral",
            "Mentions": 21,
            "Evidence": 2,
            "Avg Sentiment Score": 0.06,
        }],
        "tech",
        "WAY",
        _card(),
    )
    bundle["scan"]["total_results"] = 0
    try:
        validate_public_demo_bundle(bundle)
    except ValueError as exc:
        assert "total results" in str(exc)
    else:
        raise AssertionError("an impossible saved-result total was accepted")


def test_private_source_retains_complete_scan_and_analysis() -> None:
    card = _card()
    source = build_demo_source_payload(
        scan_rows=[{
            "Ticker": "WAY",
            "Company Name": "Waystar Holding Corp.",
            "Overall Sentiment": "Neutral",
            "Mentions": 21,
            "Sample Tweets": ["complete raw post"],
        }],
        scan_sector="tech",
        analysis_ticker="WAY",
        analysis_card=card,
        analysis_results={
            "Real-Time Market Sentiment": {
                "sample_tweets": ["complete evidence post"],
                "tweet_ids": ["123"],
            }
        },
        scan_metadata={"posts_seen": 74, "from_cache": False},
        analysis_metadata={"elapsed_s": 7.2, "degraded": False},
    )
    assert source["scan"]["rows"][0]["Sample Tweets"] == [
        "complete raw post"
    ]
    assert source["deep_analysis"]["card"]["pillars"]
    assert source["deep_analysis"]["card"]["movement"]["targets"]
    assert source["deep_analysis"]["analysis_results"][
        "Real-Time Market Sentiment"
    ]["sample_tweets"] == ["complete evidence post"]
    assert source["scan"]["metadata"]["posts_seen"] == 74


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
                        "Evidence": 2,
                        "Avg Sentiment Score": 0.0,
                    }],
                },
                "deep_analysis": {
                    "ticker": "NVDA",
                    "sector": "tech",
                    "public_card": _card("NVDA"),
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
    assert "build_demo_source_payload(" in admin
    assert "publish_public_demo(" in admin
    assert "Refresh preview from saved snapshot" in admin
    assert "This does not run a scan or analysis" in admin
    assert "load_latest_demo_publication(" in admin
    assert "load_latest_public_demo()" in home
    assert "st.session_state.demo_scan_sector = sector" in discovery
    assert '"Mentions", "Sample Tweets"' not in discovery
    assert "scan_latest.json\").write_text" not in admin
    assert "scan_latest.json\").write_text" not in discovery
    assert "deep_latest.json\").write_text" not in admin
    assert "deep_latest.json\").write_text" not in discovery
    assert "social_posts_value(row)" in home
    assert "social posts" in home.lower()
    assert "Attention unavailable" not in home
    assert 'class="ss-b5-scan-grid items-' in home
    assert 'role="listitem"' in home
    assert "Decision Workspace" in home
    assert "Independent evidence" in home
    assert "Recent volatility range" in home
    assert "not probability of return" in home
    assert "stocks scanned" in home
    assert "Saved example from an actual run" in home
    assert "total_results_complete" in home
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
        test_legacy_v2_publication_without_total_results_remains_valid,
        test_public_bundle_can_analyze_a_real_inconclusive_scan_row,
        test_public_bundle_rejects_total_below_public_result_count,
        test_private_source_retains_complete_scan_and_analysis,
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
