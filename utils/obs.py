"""Request-scoped logging: one id that ties a user action to every line it caused.

WHY THIS EXISTS

On 2026-08-01 a Discovery scan died mid-run. Diagnosing it took a screenshot, a
pasted log tail, and a manual query against usage_events -- because nothing in
the logs connected "the user clicked Sentinel Scan" to the lines that followed.
With hundreds of interleaved httpx and sentiment lines and no correlation key,
the only way to read the log was by timestamp and guesswork.

After this, every line carries an 8-char id:

    23:17:50.840 [a3f9c1] INFO  utils.credits  consume_credit ok remaining=6
    23:17:51.808 [a3f9c1] INFO  utils.deep_analysis  X API 402 credits depleted
    23:17:51.940 [a3f9c1] INFO  utils.credits  refunded scan credit event=6c67799e

One grep, the whole story. The same id is written into the usage_events
metadata by consume_credit, so a ledger row leads back to the logs that produced
it and vice versa -- which is the half that was missing when a credit was spent
with nothing to show for it.

WHY A ContextVar

Streamlit runs each session's script in its own thread, so a module-level global
would leak across concurrent users. A ContextVar is naturally per-context.

The catch: a new threading.Thread starts with a FRESH context, so the id does
NOT propagate into worker threads automatically. Deep_Analysis runs its work in
a thread, so it must capture the id and call set_request_id() inside the thread
body. There is no way around this that does not involve wrapping thread
creation, which is more machinery than two call sites justify.
"""

from __future__ import annotations

import contextvars
import logging
import os
import sys
import uuid

# "-" rather than "" so unattributed lines are visibly unattributed in a grep,
# not blank-and-ambiguous.
_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "stock_sentinel_request_id", default="-"
)

LOG_FORMAT = "%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s"


def new_request_id() -> str:
    """Start a new request scope and return its id.

    8 hex chars, not a full uuid: this is a correlation key a human types into a
    grep, not a primary key. Collisions across a 24h log window are irrelevant
    because the id is only ever used with a time range.
    """
    rid = uuid.uuid4().hex[:8]
    _REQUEST_ID.set(rid)
    return rid


def set_request_id(rid: str | None) -> None:
    """Adopt an existing id. Used to carry the scope into a worker thread."""
    _REQUEST_ID.set(rid or "-")


def get_request_id() -> str:
    return _REQUEST_ID.get()


class RequestIdFilter(logging.Filter):
    """Inject request_id into every record that reaches the handler.

    Attached to the HANDLER rather than a logger: a logger-level filter only
    sees records from that logger and its children, so third-party lines (httpx,
    urllib3, transformers) would arrive without the attribute and the formatter
    would raise. On a handler it sees everything, so the format string is always
    satisfiable.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return True


_INSTALLED = False


def install(level: int | None = None) -> None:
    """Configure root logging once per process. Safe to call on every rerun.

    Streamlit re-executes the whole script on every interaction, so this must be
    idempotent -- the previous setup called logging.basicConfig(force=True) from
    inside a page, which meant whichever page loaded first won the root config
    and re-running it stacked handlers.

    Level comes from LOG_LEVEL when set, so a noisy scan can be debugged without
    a code change and a deploy.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    if level is None:
        level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # let loggers decide; the handler gates output

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RequestIdFilter())

    # Replace rather than append. Streamlit Cloud installs its own handler and
    # pages used to add more; duplicates mean every line is printed twice, which
    # is how a 1GB log buffer fills up before the interesting part arrives.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    # httpx logs one INFO line per request. Those are genuinely useful now that
    # they carry a request id -- they show the exact Supabase and X calls a scan
    # made -- so they stay. urllib3 duplicates them at a lower layer; it does not.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    _INSTALLED = True
