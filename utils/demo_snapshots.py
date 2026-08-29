"""Portable data-contract helpers for public demo snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


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
