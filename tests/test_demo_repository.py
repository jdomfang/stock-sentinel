"""Unit checks for the durable public-demo repository boundary."""

from __future__ import annotations

from pathlib import Path
import sys
from uuid import UUID, uuid4


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from utils.demo_repository import (
    SCHEMA_VERSION,
    TABLE,
    load_latest_demo_publication,
    load_latest_public_demo,
    publish_public_demo,
)


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED.append(name) if condition else FAILED.append((name, detail)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")


def _bundle() -> dict:
    return {
        "scan": {
            "sector": "tech",
            "generated_at": "2026-08-28T01:00:00Z",
            "validated_rows": [{
                "Ticker": "WAY",
                "Overall Sentiment": "Neutral",
                "Mentions": 21,
                "Evidence": 2,
                "Avg Sentiment Score": 0.06,
            }],
        },
        "deep_analysis": {
            "ticker": "WAY",
            "sector": "tech",
            "generated_at": "2026-08-28T01:01:00Z",
            "public_card": {
                "ticker": "WAY",
                "sector": "tech",
                "verdict": "Watch",
                "confidence": "Moderate",
                "avg_sentiment": 0.06,
                "reason": "Evidence is mixed.",
                "tiles": [{
                    "key": "range_30d",
                    "label": "30d range (vol)",
                    "value": "-8.0% to 9.0%",
                }],
                "evidence": {
                    "independent_voices": 2,
                    "mentions": 21,
                    "price_points": 25,
                },
                "movement": {"band_pct": 8.5, "horizon_days": 10},
            },
        },
    }


def _source() -> dict:
    return {
        "scan": {
            "sector": "tech",
            "generated_at": "2026-08-28T01:00:00Z",
            "rows": [{
                "Ticker": "WAY",
                "Overall Sentiment": "Neutral",
                "Mentions": 21,
                "Evidence": 2,
                "Avg Sentiment Score": 0.06,
                "Sample Tweets": ["private post"],
            }],
            "metadata": {"posts_seen": 42},
        },
        "deep_analysis": {
            "ticker": "WAY",
            "sector": "tech",
            "generated_at": "2026-08-28T01:01:00Z",
            "card": {
                "ticker": "WAY",
                "sector": "tech",
                "verdict": "Watch",
                "confidence": "Moderate",
                "avg_sentiment": 0.06,
                "reason": "Evidence is mixed.",
                "pillars": [{"name": "quality", "passed": True}],
                "tiles": [{
                    "key": "range_30d",
                    "label": "30d range (vol)",
                    "value": "-8.0% to 9.0%",
                }],
                "evidence": {
                    "independent_voices": 2,
                    "mentions": 21,
                    "price_points": 25,
                },
                "movement": {"band_pct": 8.5, "horizon_days": 10},
            },
            "analysis_results": {"raw": {"tweet_ids": ["123"]}},
            "metadata": {"elapsed_s": 3.2},
        },
    }


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, response_data):
        self.response_data = response_data
        self.calls: list[tuple] = []
        self.inserted = None

    def select(self, columns):
        self.calls.append(("select", columns))
        return self

    def order(self, column, desc=False):
        self.calls.append(("order", column, desc))
        return self

    def eq(self, column, value):
        self.calls.append(("eq", column, value))
        return self

    def limit(self, count):
        self.calls.append(("limit", count))
        return self

    def insert(self, payload):
        self.calls.append(("insert",))
        self.inserted = payload
        return self

    def execute(self):
        self.calls.append(("execute",))
        if self.inserted is not None:
            return _Response([{
                "id": str(uuid4()),
                "schema_version": self.inserted["schema_version"],
                "bundle": self.inserted["bundle"],
                "source_payload": self.inserted["source_payload"],
                "published_by": self.inserted["published_by"],
                "published_at": "2026-08-28T01:02:00Z",
            }])
        return _Response(self.response_data)


class _Client:
    def __init__(self, response_data):
        self.query = _Query(response_data)
        self.table_name = None

    def table(self, table_name):
        self.table_name = table_name
        return self.query

    def rpc(self, function_name):
        self.table_name = f"rpc:{function_name}"
        return self.query


def test_loads_only_latest_valid_bundle() -> None:
    row = {
        "id": str(uuid4()),
        "schema_version": SCHEMA_VERSION,
        "bundle": _bundle(),
        "published_at": "2026-08-28T01:02:00Z",
        "published_by": str(uuid4()),
    }
    client = _Client([row])
    loaded = load_latest_public_demo(client)
    assert client.table_name == "rpc:get_latest_public_demo"
    assert loaded and loaded["bundle"]["scan"]["validated_rows"][0][
        "Mentions"
    ] == 21
    assert loaded["total_results_complete"] is False


def test_empty_repository_is_an_expected_state() -> None:
    assert load_latest_public_demo(_Client([])) is None


def test_private_loader_requires_admin_client_and_returns_source() -> None:
    row = {
        "id": str(uuid4()),
        "schema_version": SCHEMA_VERSION,
        "bundle": _bundle(),
        "source_payload": _source(),
        "published_at": "2026-08-28T01:02:00Z",
        "published_by": str(uuid4()),
    }
    client = _Client([row])
    loaded = load_latest_demo_publication(client)
    assert client.table_name == TABLE
    assert loaded and loaded["source_payload"]["scan"]["rows"][0][
        "Sample Tweets"
    ] == ["private post"]
    try:
        load_latest_demo_publication()
    except ValueError as exc:
        assert "server-side client" in str(exc)
    else:
        raise AssertionError("private loader constructed a privileged client")


def test_publisher_validates_actor_and_payload_before_insert() -> None:
    actor = str(uuid4())
    client = _Client([])
    saved = publish_public_demo(_bundle(), _source(), actor, client)
    assert client.table_name == TABLE
    assert client.query.inserted["schema_version"] == SCHEMA_VERSION
    assert client.query.inserted["published_by"] == str(UUID(actor))
    assert client.query.inserted["source_payload"]["scan"]["rows"][0][
        "Sample Tweets"
    ] == ["private post"]
    assert saved["bundle"]["deep_analysis"]["ticker"] == "WAY"
    assert saved["total_results_complete"] is True

    invalid_client = _Client([])
    try:
        publish_public_demo(_bundle(), _source(), "not-a-uuid", invalid_client)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid actor UUID was accepted")
    assert invalid_client.query.inserted is None

    try:
        publish_public_demo(_bundle(), _source(), actor)
    except ValueError as exc:
        assert "explicit server-side client" in str(exc)
    else:
        raise AssertionError("publisher constructed a privileged client")


def test_repository_has_no_portal_dependency() -> None:
    source = (REPO / "utils" / "demo_repository.py").read_text(
        encoding="utf-8"
    )
    assert "import streamlit" not in source
    assert "st.session_state" not in source


def main() -> int:
    print("=" * 72)
    print("  Durable public-demo repository")
    print("=" * 72)
    tests = [
        test_loads_only_latest_valid_bundle,
        test_empty_repository_is_an_expected_state,
        test_private_loader_requires_admin_client_and_returns_source,
        test_publisher_validates_actor_and_payload_before_insert,
        test_repository_has_no_portal_dependency,
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
