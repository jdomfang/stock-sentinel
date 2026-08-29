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


def _tag_sentry(rid: str) -> None:
    """Mirror the request id onto Sentry, so an issue links to the ledger row.

    Best-effort by design: observability must never be the thing that breaks a
    scan. Sentry not installed, not configured, or failing all degrade to plain
    logging rather than raising into the user's request.
    """
    try:
        import sentry_sdk

        if sentry_sdk.get_client().is_active():
            sentry_sdk.set_tag("request_id", rid)
    except Exception:
        pass


def new_request_id() -> str:
    """Start a new request scope and return its id.

    8 hex chars, not a full uuid: this is a correlation key a human types into a
    grep, not a primary key. Collisions across a 24h log window are irrelevant
    because the id is only ever used with a time range.
    """
    rid = uuid.uuid4().hex[:8]
    _REQUEST_ID.set(rid)
    _tag_sentry(rid)
    return rid


def set_request_id(rid: str | None) -> None:
    """Adopt an existing id. Used to carry the scope into a worker thread."""
    _REQUEST_ID.set(rid or "-")
    _tag_sentry(rid or "-")


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

    _init_sentry()

    _INSTALLED = True


def _secret(name: str) -> str:
    """Read config from the environment, falling back to Streamlit secrets.

    Environment first so scripts and containers work without Streamlit, and so
    Phase 1 can inject config the way a container expects. The st.secrets import
    is guarded because this module must stay importable from cron scripts and
    tests, where there is no Streamlit runtime.
    """
    # Delegates to utils.config, which does exactly this and is the ONLY module
    # allowed to touch st.secrets. This function was already correct; keeping a
    # second correct copy is how the two drift.
    from utils import config as _config
    return _config.get(name, "")


# Query-parameter names whose VALUE must never leave this process. Matched
# case-insensitively against the parameter name, so `apiKey`, `APIKEY` and
# `api_key` are all covered.
_SENSITIVE_PARAMS = frozenset({
    "apikey", "api_key", "key", "token", "access_token", "refresh_token",
    "password", "secret", "authorization", "bearer", "signature", "sig",
    # Not a credential, but send_default_pii=False promises we do not ship it,
    # and PostgREST filters put it straight in the query string as email=eq.x.
    "email",
})

# Config names whose literal values get redacted wherever they appear, even
# outside a query string -- an exception message that embeds a full request URL
# would otherwise carry the key past the parameter-name check.
_SECRET_CONFIG_NAMES = (
    "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY", "POLYGON_API_KEY",
    "X_BEARER_TOKEN", "ANTHROPIC_API_KEY", "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET", "PAYMENTS_API_SHARED_SECRET",
    "HEALTHCHECK_PRICE_SYNC_URL", "SENTRY_DSN",
)

REDACTED = "[redacted]"


def _scrub_query(qs: str) -> str:
    """Redact sensitive values in a urlencoded query string, keep the shape."""
    from urllib.parse import parse_qsl, urlencode

    try:
        pairs = parse_qsl(qs, keep_blank_values=True)
    except Exception:
        return REDACTED
    return urlencode([
        (k, REDACTED if k.lower() in _SENSITIVE_PARAMS else v) for k, v in pairs
    ])


def _scrub_url(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    try:
        p = urlsplit(url)
        if not p.query:
            return url
        return urlunsplit((p.scheme, p.netloc, p.path, _scrub_query(p.query), p.fragment))
    except Exception:
        return REDACTED


def _known_secret_values() -> list[str]:
    """Literal secret values to strip from anywhere in an outbound event.

    Only values of 8+ chars: shorter ones would match common substrings and
    redact half the payload.
    """
    out = []
    for name in _SECRET_CONFIG_NAMES:
        v = _secret(name)
        if v and len(v) >= 8:
            out.append(v)
    return out


def _scrub_obj(obj, secrets: list[str], depth: int = 0):
    """Walk an event and redact URLs, query strings and literal secret values."""
    if depth > 12:
        return obj
    if isinstance(obj, str):
        s = obj
        for sec in secrets:
            if sec in s:
                s = s.replace(sec, REDACTED)
        if "://" in s and "?" in s:
            s = _scrub_url(s)
        return s
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in _SENSITIVE_PARAMS or lk in ("http.query", "query_string"):
                out[k] = _scrub_query(v) if lk in ("http.query", "query_string") and isinstance(v, str) else REDACTED
            else:
                out[k] = _scrub_obj(v, secrets, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return type(obj)(_scrub_obj(v, secrets, depth + 1) for v in obj)
    return obj


def _make_scrubbers():
    """Build before_send / before_breadcrumb hooks.

    send_default_pii=False does NOT sanitize outbound URL query strings. Sentry's
    stdlib integration records every HTTP call as a breadcrumb, so without this
    an unrelated exception anywhere in the app flushes the last 100 breadcrumbs
    to sentry.io -- including utils.finance's Polygon calls, which put the API
    key in the query string, and PostgREST calls, which put user emails there.
    Verified before this existed: the key appeared verbatim in the event payload.
    """
    secrets = _known_secret_values()

    def before_breadcrumb(crumb, hint):
        try:
            return _scrub_obj(crumb, secrets)
        except Exception:
            return None  # drop rather than risk leaking an unscrubbed crumb

    def before_send(event, hint):
        try:
            return _scrub_obj(event, secrets)
        except Exception:
            return None  # drop rather than send something unscrubbed

    return before_send, before_breadcrumb


def _init_sentry() -> None:
    """Start Sentry if a DSN is configured. Never raises.

    Sentry catches what THREW. It will not catch the failure that actually cost
    a credit on 2026-08-01: an OOM kill delivers SIGKILL, which runs no handler,
    so there is no exception to capture. That gap is the orphan reaper's job.
    What this does catch is everything with a stack trace -- including the
    ImportError that took the Discovery page down after a deploy, which would
    have arrived as an alert instead of being found by loading the page.
    """
    dsn = _secret("SENTRY_DSN")
    if not dsn:
        logging.getLogger(__name__).info("Sentry not configured (no SENTRY_DSN)")
        return

    try:
        import sentry_sdk

        before_send, before_breadcrumb = _make_scrubbers()
        sentry_sdk.init(
            dsn=dsn,
            environment=_secret("SENTRY_ENVIRONMENT") or "production",
            # No performance tracing: the free tier's budget is better spent on
            # errors, and a 1GB box does not need the extra instrumentation.
            traces_sample_rate=0.0,
            # Necessary but NOT sufficient -- it does not touch query strings.
            # The scrubbers below are what actually keeps credentials in.
            send_default_pii=False,
            before_send=before_send,
            before_breadcrumb=before_breadcrumb,
        )
        logging.getLogger(__name__).info("Sentry initialised")
    except Exception as e:
        # A misconfigured DSN must not take the app down. Losing error
        # reporting is bad; refusing to serve users because error reporting is
        # broken is worse.
        logging.getLogger(__name__).warning(
            "Sentry init failed (%s); continuing without it", type(e).__name__
        )
