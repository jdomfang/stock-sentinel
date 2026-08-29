"""Portable data-contract helpers for public demo snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Iterable, Mapping


_PUBLIC_SENTIMENTS = {"bullish", "bearish", "neutral"}


def snapshot_timestamp() -> str:
    """Return an explicit UTC timestamp suitable for persisted snapshot data."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def normalize_scan_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and normalize fields the public preview depends on.

    ``Mentions`` is the social-post count produced by Market Scan. A publisher
    must fail rather than write a snapshot that would force the public page to
    invent a count.
    """
    normalized: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        ticker = str(row.get("Ticker") or "").strip().upper()
        if not ticker:
            raise ValueError("Scan snapshot row is missing Ticker")
        if "Mentions" not in row:
            raise ValueError(f"Scan snapshot row {ticker} is missing Mentions")
        try:
            mentions = int(row["Mentions"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Scan snapshot row {ticker} has invalid Mentions"
            ) from exc
        if mentions < 0:
            raise ValueError(
                f"Scan snapshot row {ticker} has negative Mentions"
            )
        row["Ticker"] = ticker
        row["Mentions"] = mentions
        normalized.append(row)
    return normalized


def social_posts_value(row: Mapping[str, Any]) -> str:
    """Render a compact saved social-post count for the public preview."""
    if "Mentions" not in row:
        return "—"
    try:
        mentions = int(row["Mentions"])
    except (TypeError, ValueError):
        return "—"
    if mentions < 0:
        return "—"
    return str(mentions)


def _json_default(value: Any) -> Any:
    """Convert common dataframe/scientific scalar values without hiding bugs."""
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    raise TypeError(f"Unsupported demo snapshot value: {type(value).__name__}")


def json_safe(value: Any) -> Any:
    """Return strict JSON data suitable for JSONB and a future HTTP API."""
    return json.loads(
        json.dumps(value, default=_json_default, allow_nan=False)
    )


def validate_public_demo_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the durable public-demo schema v1 bundle."""
    if not isinstance(bundle, Mapping):
        raise ValueError("Demo bundle must be an object")

    scan = bundle.get("scan")
    analysis = bundle.get("deep_analysis")
    if not isinstance(scan, Mapping) or not isinstance(analysis, Mapping):
        raise ValueError("Demo bundle requires scan and deep_analysis")

    sector = str(scan.get("sector") or "").strip().lower()
    analysis_sector = str(analysis.get("sector") or "").strip().lower()
    ticker = str(analysis.get("ticker") or "").strip().upper()
    results = analysis.get("analysis_results")
    if not sector:
        raise ValueError("Market Scan sector is missing")
    if not analysis_sector or analysis_sector != sector:
        raise ValueError("Market Scan and Deep Analyze sectors do not match")
    if not ticker:
        raise ValueError("Deep Analyze ticker is missing")
    if not isinstance(results, Mapping) or not results:
        raise ValueError("Deep Analyze results are missing")

    source_rows = scan.get("validated_rows")
    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError("Market Scan results are missing")
    rows = normalize_scan_rows(source_rows)
    for row in rows:
        sentiment = str(row.get("Overall Sentiment") or "").strip().lower()
        if sentiment not in _PUBLIC_SENTIMENTS:
            raise ValueError(
                f"Scan snapshot row {row['Ticker']} has no public sentiment signal"
            )
    if ticker not in {str(row["Ticker"]).upper() for row in rows}:
        raise ValueError(
            "Analyze a Bullish, Bearish, or Neutral ticker from the current "
            "Market Scan"
        )

    normalized = {
        "scan": {
            "sector": sector,
            "generated_at": str(
                scan.get("generated_at") or snapshot_timestamp()
            ),
            "validated_rows": rows,
        },
        "deep_analysis": {
            "ticker": ticker,
            "sector": sector,
            "generated_at": str(
                analysis.get("generated_at") or snapshot_timestamp()
            ),
            "analysis_results": dict(results),
        },
    }
    return json_safe(normalized)


def build_public_demo_bundle(
    scan_rows: Iterable[Mapping[str, Any]],
    scan_sector: str,
    analysis_ticker: str,
    analysis_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one coherent, intentionally public preview publication.

    Raw Deep Analyze results can contain post text and internal evidence detail.
    The public RPC needs only the decision summary shown by Home, so the bundle
    deliberately excludes the raw analysis payload.
    """
    public_rows = [
        dict(row)
        for row in scan_rows
        if str(row.get("Overall Sentiment") or "").strip().lower()
        in _PUBLIC_SENTIMENTS
    ]
    now = snapshot_timestamp()
    return validate_public_demo_bundle(
        {
            "scan": {
                "sector": scan_sector,
                "generated_at": now,
                "validated_rows": public_rows,
            },
            "deep_analysis": {
                "ticker": analysis_ticker,
                # A standalone analysis route does not know its scan sector.
                # Membership in the current scan is the coherence check, so the
                # scan's sector is authoritative for the published bundle.
                "sector": scan_sector,
                "generated_at": now,
                "analysis_results": {
                    "public_summary": dict(analysis_summary),
                },
            },
        }
    )
