"""Validate X influencer handles and materialize a clean list for from: queries.

Usage:
  python3 scripts/validate_influencers.py

Reads:
  data/influencers.json

Writes:
  data/influencers_validated.json  (only valid handles + ids + follower counts)
  data/influencers_invalid.json    (handles that did not resolve)

Notes:
- This makes ONE (or a few) calls to X /2/users/by?usernames=... in batches.
- Requires X_BEARER_TOKEN in Streamlit secrets (.streamlit/secrets.toml).
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Dict, List, Any

import requests

ROOT = Path(__file__).resolve().parents[1]
IN_PATH = ROOT / "data" / "influencers.json"
OUT_VALID = ROOT / "data" / "influencers_validated.json"
OUT_INVALID = ROOT / "data" / "influencers_invalid.json"

X_USERS_BY_URL = "https://api.twitter.com/2/users/by"


def load_bearer_token() -> str:
    # Prefer env var for scripting; fall back to Streamlit secrets file.
    token = os.getenv("X_BEARER_TOKEN")
    if token:
        return token

    # Streamlit secrets.toml is just TOML; we avoid adding a dependency by doing a tiny parse.
    secrets_path = ROOT / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        raise RuntimeError(
            "Missing X_BEARER_TOKEN. Set env var X_BEARER_TOKEN or add it to .streamlit/secrets.toml"
        )

    txt = secrets_path.read_text(encoding="utf-8")
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("X_BEARER_TOKEN"):
            # crude TOML key/value parse: X_BEARER_TOKEN = "..."
            _, rhs = line.split("=", 1)
            rhs = rhs.strip().strip("\"").strip("'")
            if rhs:
                return rhs
    raise RuntimeError("Could not parse X_BEARER_TOKEN from .streamlit/secrets.toml")


def batched(seq: List[str], batch_size: int) -> List[List[str]]:
    return [seq[i : i + batch_size] for i in range(0, len(seq), batch_size)]


def main() -> None:
    if not IN_PATH.exists():
        raise SystemExit(f"Missing {IN_PATH}")

    raw = json.loads(IN_PATH.read_text(encoding="utf-8"))
    buckets: Dict[str, List[str]] = raw.get("buckets", {})

    # de-dupe across buckets but preserve original bucket membership for output
    all_handles: List[str] = []
    for _, handles in buckets.items():
        all_handles.extend(handles)

    # Normalize (strip leading @, whitespace) and unique
    norm_handles = []
    seen = set()
    for h in all_handles:
        h2 = h.strip()
        if h2.startswith("@"):  # allow @foo
            h2 = h2[1:]
        if not h2:
            continue
        if h2.lower() in seen:
            continue
        seen.add(h2.lower())
        norm_handles.append(h2)

    token = load_bearer_token()

    headers = {"Authorization": f"Bearer {token}"}

    # X supports up to 100 usernames per request (documented). We’ll be conservative.
    BATCH_SIZE = 80
    users_by_username: Dict[str, Any] = {}
    invalid: List[Dict[str, Any]] = []

    for batch in batched(norm_handles, BATCH_SIZE):
        params = {
            "usernames": ",".join(batch),
            "user.fields": "public_metrics,verified,created_at",
        }
        r = requests.get(X_USERS_BY_URL, headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"X API error {r.status_code}: {r.text}")
        payload = r.json()

        for u in payload.get("data", []) or []:
            users_by_username[u["username"].lower()] = u

        for e in payload.get("errors", []) or []:
            invalid.append(e)

    # Materialize validated buckets
    validated = {"version": raw.get("version", 1), "buckets": {}, "users": {}}
    for bucket, handles in buckets.items():
        out_bucket = []
        for h in handles:
            h2 = h.strip().lstrip("@")
            u = users_by_username.get(h2.lower())
            if not u:
                continue
            out_bucket.append(u["username"])  # preserve correct casing
            validated["users"][u["username"]] = {
                "id": u.get("id"),
                "username": u.get("username"),
                "verified": u.get("verified"),
                "public_metrics": u.get("public_metrics", {}),
                "created_at": u.get("created_at"),
            }
        validated["buckets"][bucket] = out_bucket

    OUT_VALID.write_text(json.dumps(validated, indent=2, sort_keys=False), encoding="utf-8")
    OUT_INVALID.write_text(json.dumps({"errors": invalid}, indent=2), encoding="utf-8")

    print(f"Wrote: {OUT_VALID}")
    print(f"Wrote: {OUT_INVALID}")


if __name__ == "__main__":
    main()
