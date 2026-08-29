"""Persistent repository for the public illustrative demo.

No Streamlit imports live here. A future frontend or API can publish and load
the same Supabase-backed bundle without inheriting portal session state.
"""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from utils.demo_snapshots import validate_public_demo_bundle
from utils.supabase_client import get_client


TABLE = "public_demo_snapshots"
SCHEMA_VERSION = 1


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
    row["bundle"] = validate_public_demo_bundle(row.get("bundle") or {})
    return row


def publish_public_demo(
    bundle: Mapping[str, Any],
    published_by: str,
    client=None,
) -> dict[str, Any]:
    """Append one validated publication and return its persisted metadata."""
    if client is None:
        raise ValueError("Publishing requires an explicit server-side client")
    actor = str(UUID(str(published_by)))
    normalized = validate_public_demo_bundle(bundle)
    response = (
        _client(client)
        .table(TABLE)
        .insert(
            {
                "schema_version": SCHEMA_VERSION,
                "bundle": normalized,
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
    return row
