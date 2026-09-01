"""Persistent repository for the public illustrative demo.

No Streamlit imports live here. A future frontend or API can publish and load
the same Supabase-backed bundle without inheriting portal session state.
"""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from utils.demo_snapshots import (
    validate_demo_publication,
    validate_public_demo_bundle,
)
from utils.supabase_client import get_client


TABLE = "public_demo_snapshots"
SCHEMA_VERSION = 2


def _client(client=None):
    # Loading intentionally public preview content uses a narrow SECURITY
    # DEFINER RPC, so it never needs a service-role key. Publishing still
    # requires an explicitly injected server-side admin client.
    return client if client is not None else get_client()


def load_latest_public_demo(client=None) -> dict[str, Any] | None:
    """Return the newest valid publication, or None when none exists."""
    response = _client(client).rpc("get_latest_public_demo").execute()
    rows = getattr(response, "data", None) or []
    if not rows:
        return None
    row = dict(rows[0])
    if int(row.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ValueError("Unsupported public demo schema version")
    raw_bundle = row.get("bundle") or {}
    raw_scan = raw_bundle.get("scan") if isinstance(raw_bundle, Mapping) else None
    row["total_results_complete"] = bool(
        isinstance(raw_scan, Mapping) and "total_results" in raw_scan
    )
    row["bundle"] = validate_public_demo_bundle(raw_bundle)
    return row


def load_latest_demo_publication(client=None) -> dict[str, Any] | None:
    """Return the newest complete publication to an explicit admin client."""
    if client is None:
        raise ValueError("Private demo loading requires a server-side client")
    response = (
        _client(client)
        .table(TABLE)
        .select(
            "id,schema_version,bundle,source_payload,published_at,published_by"
        )
        .eq("schema_version", SCHEMA_VERSION)
        .order("published_at", desc=True)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        return None
    row = dict(rows[0])
    row["bundle"], row["source_payload"] = validate_demo_publication(
        row.get("bundle") or {}, row.get("source_payload") or {}
    )
    return row


def publish_public_demo(
    bundle: Mapping[str, Any],
    source_payload: Mapping[str, Any],
    published_by: str,
    client=None,
) -> dict[str, Any]:
    """Append one public projection plus its private canonical source."""
    if client is None:
        raise ValueError("Publishing requires an explicit server-side client")
    actor = str(UUID(str(published_by)))
    normalized, normalized_source = validate_demo_publication(
        bundle, source_payload
    )
    response = (
        _client(client)
        .table(TABLE)
        .insert(
            {
                "schema_version": SCHEMA_VERSION,
                "bundle": normalized,
                "source_payload": normalized_source,
                "published_by": actor,
            }
        )
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        raise RuntimeError("Supabase did not return the published demo")
    row = dict(rows[0])
    row["bundle"] = validate_public_demo_bundle(row.get("bundle") or {})
    row["total_results_complete"] = True
    return row
