"""One place to read configuration. Standard library only, no streamlit import.

WHY THIS EXISTS

Ten modules carried a byte-identical private `_config` whose only job was
"environment first, then st.secrets if streamlit happens to exist". Ten copies
of a function that reaches into the UI framework is what makes ~3,100 lines of
otherwise-portable analysis code un-runnable outside the Streamlit process --
and moving it into core-api is the whole point of this migration.

The lookup order is deliberate and unchanged from what those copies did:

  1. os.environ          containers set config this way, and it must win so a
                         Railway service is never overridden by a secrets file
                         that happened to ship in the image
  2. st.secrets          the portal's own store, read lazily and only if
                         streamlit is actually importable
  3. the caller's default

STREAMLIT IS NEVER IMPORTED AT MODULE SCOPE. It is attempted once, lazily, and
the outcome cached -- so a process without it pays one failed import for the
life of the run rather than one per call, and this module stays importable in a
worker, a script, or the service this code is migrating into.

THE FAILURE THIS GUARDS AGAINST is not hypothetical. utils/evidence.py had the
same shape -- a lazy import inside a try, whose ImportError was swallowed -- and
in any process without streamlit it silently changed a decision threshold by
3.3x. A missing credential must look like a missing credential.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# None = not yet attempted. False = attempted and unavailable, do not retry.
_SECRETS: Any = None


def _secrets() -> Any:
    """st.secrets, or False. Attempted once per process."""
    global _SECRETS
    if _SECRETS is not None:
        return _SECRETS
    try:
        import streamlit as st
        _SECRETS = st.secrets
    except Exception:
        # Ordinary outside the portal, so INFO rather than a warning: a worker
        # or a service is expected to configure itself from the environment.
        logger.info("config: streamlit secrets unavailable; using environment only")
        _SECRETS = False
    return _SECRETS


def get(name: str, default: str = "") -> str:
    """Config value as a string. Never raises, never imports streamlit eagerly."""
    v = os.getenv(name, "")
    if v:
        return v
    s = _secrets()
    if s is False:
        return default
    try:
        return str(s.get(name, "") or "") or default
    except Exception:
        # A secrets file that exists but cannot be read is worth saying out
        # loud -- silently falling through to the default is how a service runs
        # against the wrong database.
        logger.warning("config: %s unreadable from st.secrets", name)
        return default


def require(name: str) -> str:
    """Config value, or a clear failure. For values with no safe default.

    A credential that resolves to "" and then produces an HTTP 401 three call
    frames away is the same bug as one that raises here, discovered later and
    with less to go on.
    """
    v = get(name)
    if not v:
        raise RuntimeError(
            f"{name} is not set. Provide it in the environment, or in "
            f".streamlit/secrets.toml when running the portal.")
    return v


def reset_cache() -> None:
    """Forget whether streamlit was importable. Tests only."""
    global _SECRETS
    _SECRETS = None
