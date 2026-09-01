"""Portable data contracts for durable illustrative demo snapshots.

Schema v2 stores a complete, private source payload for administrators and a
strictly allowlisted public projection for the unauthenticated Home preview.
The public projection is derived from the same canonical Scan and Deep Analyze
objects; it never exposes raw social-post text or internal evidence records.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping


_PUBLIC_SENTIMENTS = {"bullish", "bearish", "neutral"}
_VERDICTS = {"buy", "watch", "avoid"}
_CONFIDENCE = {"low", "moderate", "high"}
_PUBLIC_SCAN_FIELDS = (
    "Ticker",
    "Company Name",
    "Mentions",
    "Evidence",
    "Avg Sentiment Score",
    "Overall Sentiment",
)
_PUBLIC_TILE_KEYS = {
    "last_price",
    "range_30d",
    "drawdown_first",
}


def snapshot_timestamp() -> str:
    """Return an explicit UTC timestamp suitable for persisted snapshot data."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _whole_number(value: Any, label: str, *, minimum: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{label} must be an integer")
    if number < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return number


def _finite_number(value: Any, label: str, *, minimum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return number


def _json_value(value: Any) -> Any:
    """Convert dataframe/scientific values into strict JSON-compatible data."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        return _json_value(item())
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    raise TypeError(f"Unsupported demo snapshot value: {type(value).__name__}")


def json_safe(value: Any) -> Any:
    """Return strict JSON data suitable for JSONB and a future HTTP API."""
    return _json_value(value)


def normalize_scan_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize complete scan rows while retaining every available field."""
    normalized: list[dict[str, Any]] = []
    for source in rows:
        if not isinstance(source, Mapping):
            raise ValueError("Every Market Scan result must be an object")
        row = json_safe(dict(source))
        ticker = str(row.get("Ticker") or "").strip().upper()
        if not ticker:
            raise ValueError("Scan snapshot row is missing Ticker")
        if "Mentions" not in row:
            raise ValueError(f"Scan snapshot row {ticker} is missing Mentions")
        row["Ticker"] = ticker
        row["Mentions"] = _whole_number(
            row["Mentions"], f"Scan snapshot row {ticker} Mentions"
        )
        normalized.append(row)
    if not normalized:
        raise ValueError("Market Scan results are missing")
    return normalized


def _public_scan_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    for row in normalize_scan_rows(rows):
        sentiment = str(row.get("Overall Sentiment") or "").strip().lower()
        if sentiment not in _PUBLIC_SENTIMENTS:
            continue
        ticker = row["Ticker"]
        score = _finite_number(
            row.get("Avg Sentiment Score", 0.0),
            f"Scan snapshot row {ticker} Avg Sentiment Score",
        )
        evidence = _whole_number(
            row.get("Evidence", row["Mentions"]),
            f"Scan snapshot row {ticker} Evidence",
        )
        projection = {key: row.get(key) for key in _PUBLIC_SCAN_FIELDS}
        projection.update(
            {
                "Ticker": ticker,
                "Company Name": str(row.get("Company Name") or "").strip(),
                "Mentions": row["Mentions"],
                "Evidence": evidence,
                "Avg Sentiment Score": score,
                "Overall Sentiment": sentiment.title(),
            }
        )
        public.append(projection)
    if not public:
        raise ValueError("Market Scan has no public sentiment signals")
    return public


def social_posts_value(row: Mapping[str, Any]) -> str:
    """Render a compact saved social-post count for the public preview."""
    try:
        return str(_whole_number(row.get("Mentions"), "Mentions"))
    except ValueError:
        return "—"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_public_card(
    source: Mapping[str, Any], *, expected_ticker: str
) -> dict[str, Any]:
    """Build the safe Home projection from the canonical analysis card."""
    if not isinstance(source, Mapping) or not source:
        raise ValueError("Canonical Deep Analyze card is missing")
    ticker = str(source.get("ticker") or "").strip().upper()
    if ticker != expected_ticker:
        raise ValueError("Deep Analyze card ticker does not match the snapshot")
    verdict = str(source.get("verdict") or "").strip().lower()
    confidence = str(source.get("confidence") or "").strip().lower()
    if verdict not in _VERDICTS:
        raise ValueError("Deep Analyze verdict must be Buy, Watch, or Avoid")
    if confidence not in _CONFIDENCE:
        raise ValueError("Deep Analyze confidence must be Low, Moderate, or High")

    evidence_source = source.get("evidence")
    if not isinstance(evidence_source, Mapping):
        evidence_source = {}
    movement_source = source.get("movement")
    if not isinstance(movement_source, Mapping):
        movement_source = {}
    price_points = _whole_number(
        evidence_source.get("price_points"), "Deep Analyze price points", minimum=5
    )
    horizon_days = _whole_number(
        movement_source.get("horizon_days"),
        "Deep Analyze signal horizon",
        minimum=1,
    )
    band_pct = _finite_number(
        movement_source.get("band_pct"),
        "Deep Analyze risk range",
        minimum=0,
    )
    independent = evidence_source.get("independent_voices")
    if independent is not None:
        independent = _whole_number(
            independent, "Deep Analyze independent evidence"
        )

    tiles: list[dict[str, str]] = []
    for tile in source.get("tiles") or []:
        if not isinstance(tile, Mapping):
            continue
        key = str(tile.get("key") or "").strip()
        if key not in _PUBLIC_TILE_KEYS:
            continue
        tiles.append(
            {
                "key": key,
                "label": str(tile.get("label") or "").strip(),
                "value": str(tile.get("value") or "").strip(),
                "helper": str(tile.get("helper") or "").strip(),
            }
        )
    range_tile = next((tile for tile in tiles if tile["key"] == "range_30d"), None)
    if not range_tile or not range_tile["value"] or range_tile["value"].lower() == "unavailable":
        raise ValueError("Deep Analyze public card requires a 30D range")

    rationale = _string_list(source.get("rationale"))
    reason = str(source.get("reason") or "").strip()
    if not reason and rationale:
        reason = rationale[0]
    if not reason:
        raise ValueError("Deep Analyze recommendation reason is missing")

    card = {
        "ticker": ticker,
        "sector": str(source.get("sector") or "").strip().lower(),
        "verdict": verdict.title(),
        "confidence": confidence.title(),
        "adjudicator": str(source.get("adjudicator") or "").strip(),
        "headline": str(source.get("headline") or "").strip(),
        "confidence_note": str(source.get("confidence_note") or "").strip(),
        "avg_sentiment": _finite_number(
            source.get("avg_sentiment", 0.0), "Deep Analyze sentiment score"
        ),
        "rationale": rationale,
        "reason": reason,
        "would_change": _string_list(source.get("would_change")),
        "confidence_notes": _string_list(source.get("confidence_notes")),
        "tiles": tiles,
        "evidence": {
            "independent_voices": independent,
            "quality": str(evidence_source.get("quality") or "").strip(),
            "quality_tier": str(evidence_source.get("quality_tier") or "").strip(),
            "mentions": _whole_number(
                evidence_source.get("mentions", 0), "Deep Analyze mentions"
            ),
            "price_points": price_points,
        },
        "movement": {
            "band_pct": band_pct,
            "horizon_days": horizon_days,
        },
    }
    return json_safe(card)


def validate_public_demo_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize the durable public-demo schema v2 bundle."""
    if not isinstance(bundle, Mapping):
        raise ValueError("Demo bundle must be an object")
    scan = bundle.get("scan")
    analysis = bundle.get("deep_analysis")
    if not isinstance(scan, Mapping) or not isinstance(analysis, Mapping):
        raise ValueError("Demo bundle requires scan and deep_analysis")
    sector = str(scan.get("sector") or "").strip().lower()
    analysis_sector = str(analysis.get("sector") or "").strip().lower()
    ticker = str(analysis.get("ticker") or "").strip().upper()
    if not sector or analysis_sector != sector:
        raise ValueError("Market Scan and Deep Analyze sectors do not match")
    if not ticker:
        raise ValueError("Deep Analyze ticker is missing")
    rows = _public_scan_rows(scan.get("validated_rows") or [])
    total_results = _whole_number(
        scan.get("total_results", len(rows)),
        "Market Scan total results",
        minimum=len(rows),
    )
    if ticker not in {row["Ticker"] for row in rows}:
        raise ValueError("Deep Analyze ticker must belong to the current Market Scan")
    card = normalize_public_card(
        analysis.get("public_card") or {}, expected_ticker=ticker
    )
    if card["sector"] and card["sector"] != sector:
        raise ValueError("Canonical Deep Analyze card sector does not match the scan")
    card["sector"] = sector
    return json_safe(
        {
            "scan": {
                "sector": sector,
                "generated_at": str(scan.get("generated_at") or snapshot_timestamp()),
                "total_results": total_results,
                "validated_rows": rows,
            },
            "deep_analysis": {
                "ticker": ticker,
                "sector": sector,
                "generated_at": str(
                    analysis.get("generated_at") or snapshot_timestamp()
                ),
                "public_card": card,
            },
        }
    )


def validate_demo_source_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the private, complete publisher payload retained for admins."""
    if not isinstance(payload, Mapping):
        raise ValueError("Demo source payload must be an object")
    scan = payload.get("scan")
    analysis = payload.get("deep_analysis")
    if not isinstance(scan, Mapping) or not isinstance(analysis, Mapping):
        raise ValueError("Demo source requires scan and deep_analysis")
    sector = str(scan.get("sector") or "").strip().lower()
    analysis_sector = str(analysis.get("sector") or "").strip().lower()
    ticker = str(analysis.get("ticker") or "").strip().upper()
    rows = normalize_scan_rows(scan.get("rows") or [])
    if not sector or analysis_sector != sector:
        raise ValueError("Private Scan and Deep Analyze sectors do not match")
    if ticker not in {row["Ticker"] for row in rows}:
        raise ValueError("Private Deep Analyze ticker must belong to the scan")
    card = analysis.get("card")
    results = analysis.get("analysis_results")
    if not isinstance(card, Mapping) or not card:
        raise ValueError("Private canonical Deep Analyze card is missing")
    if str(card.get("ticker") or "").strip().upper() != ticker:
        raise ValueError("Private Deep Analyze card ticker does not match")
    if not isinstance(results, Mapping) or not results:
        raise ValueError("Private Deep Analyze results are missing")
    return json_safe(
        {
            "scan": {
                "sector": sector,
                "generated_at": str(scan.get("generated_at") or snapshot_timestamp()),
                "rows": rows,
                "metadata": dict(scan.get("metadata") or {}),
            },
            "deep_analysis": {
                "ticker": ticker,
                "sector": sector,
                "generated_at": str(
                    analysis.get("generated_at") or snapshot_timestamp()
                ),
                "card": dict(card),
                "analysis_results": dict(results),
                "metadata": dict(analysis.get("metadata") or {}),
            },
        }
    )


def build_public_demo_bundle(
    scan_rows: Iterable[Mapping[str, Any]],
    scan_sector: str,
    analysis_ticker: str,
    analysis_card: Mapping[str, Any],
    *,
    scan_generated_at: str | None = None,
    analysis_generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the safe public projection from one coherent real workflow."""
    now = snapshot_timestamp()
    scan_rows = list(scan_rows)
    return validate_public_demo_bundle(
        {
            "scan": {
                "sector": scan_sector,
                "generated_at": scan_generated_at or now,
                "total_results": len(scan_rows),
                "validated_rows": scan_rows,
            },
            "deep_analysis": {
                "ticker": analysis_ticker,
                "sector": scan_sector,
                "generated_at": analysis_generated_at or now,
                "public_card": dict(analysis_card),
            },
        }
    )


def build_demo_source_payload(
    scan_rows: Iterable[Mapping[str, Any]],
    scan_sector: str,
    analysis_ticker: str,
    analysis_card: Mapping[str, Any],
    analysis_results: Mapping[str, Any],
    *,
    scan_generated_at: str | None = None,
    analysis_generated_at: str | None = None,
    scan_metadata: Mapping[str, Any] | None = None,
    analysis_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the complete private source snapshot retained by Admin."""
    now = snapshot_timestamp()
    return validate_demo_source_payload(
        {
            "scan": {
                "sector": scan_sector,
                "generated_at": scan_generated_at or now,
                "rows": list(scan_rows),
                "metadata": dict(scan_metadata or {}),
            },
            "deep_analysis": {
                "ticker": analysis_ticker,
                "sector": scan_sector,
                "generated_at": analysis_generated_at or now,
                "card": dict(analysis_card),
                "analysis_results": dict(analysis_results),
                "metadata": dict(analysis_metadata or {}),
            },
        }
    )


def validate_demo_publication(
    bundle: Mapping[str, Any], source_payload: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Prove that the public view is exactly derived from the private source."""
    scan_bundle = bundle.get("scan") if isinstance(bundle, Mapping) else None
    legacy_without_total = (
        isinstance(scan_bundle, Mapping) and "total_results" not in scan_bundle
    )
    public = validate_public_demo_bundle(bundle)
    source = validate_demo_source_payload(source_payload)
    scan = source["scan"]
    analysis = source["deep_analysis"]
    expected = build_public_demo_bundle(
        scan_rows=scan["rows"],
        scan_sector=scan["sector"],
        analysis_ticker=analysis["ticker"],
        analysis_card=analysis["card"],
        scan_generated_at=scan["generated_at"],
        analysis_generated_at=analysis["generated_at"],
    )
    # total_results is an additive v2 field. Publications written before it
    # existed stored only the allowlisted result rows, so retain exact v2
    # equality for those durable snapshots while every new publish carries the
    # complete saved-result count.
    if legacy_without_total:
        expected["scan"]["total_results"] = len(
            expected["scan"]["validated_rows"]
        )
    if public != expected:
        raise ValueError(
            "Public demo projection does not match its private source payload"
        )
    return public, source
