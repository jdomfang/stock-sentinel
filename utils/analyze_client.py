"""Call the analysis instead of importing it. The cutover.

WHY THIS EXISTS

utils/analyze.py made one implementation of Deep Analyze; core-api put it behind
HTTPS. Until something calls that endpoint the service is a second deployment of
code the portal still runs in-process, which is strictly worse than either
choice on its own. This module is the caller.

WHAT IT DELIBERATELY DOES NOT DO

Charge, refund, or render. The credit belongs to whoever owns the session, and
core-api authenticates a SERVICE, not a user -- it has no idea who is asking.
The portal debits before calling and refunds on the failures below.

THE FALLBACK RULE, and it is not "retry on any error"

signal_log has `unique (event_id, ticker, feature)`, so a duplicate write is
rejected by the database. verdict_log has no such constraint -- a second write
for the same event lands as a second row, and nothing downstream can tell it
from a genuine second analysis. So the portal may only re-run locally when the
remote provably did NOT get far enough to spend or to write:

    connection refused, DNS failure, TLS failure   -> nothing was sent
    401 / 403                                      -> refused before the handler
    422                                            -> rejected by validation
    429 at capacity                                -> refused before a slot
    any X-Core-Refused header                      -> refused before spending

A TIMEOUT IS NOT IN THAT LIST, and that is the whole point. A Deep Analyze runs
40-60 seconds; a read timeout is the most likely failure mode and the one where
the service most likely finished, bought the posts and wrote the row. Falling
back there would buy a second corpus and duplicate the verdict_log row. The
portal refunds and reports instead.

This whole fallback is SCAFFOLDING for the staged cutover. Once the remote path
is confirmed in production the in-process branch comes out of the page, and
`retryable` stops having a caller.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# A Deep Analyze paginates X up to three times, scores two corpora on a remote
# model and runs a 2000-path Monte Carlo. The service's own single-flight lock
# can add a whole analysis on top when two callers ask for one ticker at once.
DEFAULT_TIMEOUT_S = 180

# Written into the `model` column of two tables, so it is not free text.
_ROUTES = {"discovery"}
_FEATURES = {"deep_analyze", "discovery", "core_api"}


@dataclass
class RemoteResult:
    """What came back. Never raises; every failure is a field."""

    ok: bool = False
    card: dict = field(default_factory=dict)
    analysis_results: dict = field(default_factory=dict)
    # True when the service delivered a legacy fallback rather than a cascade
    # verdict. Still a product, still billed, and the card says so too.
    degraded: bool = False
    error: str | None = None
    status: int | None = None
    # How many X posts the service bought. Present on a "no usable evidence"
    # answer, because whether money was spent is what a refund decision turns on.
    posts_billed: int | None = None
    # May the caller safely run the analysis itself instead? See the module
    # docstring -- this is False whenever the remote might have spent money.
    retryable: bool = False
    elapsed_s: float | None = None


def configured() -> bool:
    """Is the portal pointed at a core-api? Unset means keep running in-process."""
    from utils import config as _c
    return bool(_c.get("CORE_API_URL") and _c.get("CORE_API_SHARED_SECRET"))


def _base() -> str:
    from utils import config as _c
    url = (_c.get("CORE_API_URL") or "").strip().rstrip("/")
    # The shared secret travels in a header. Over plaintext it travels in the
    # clear, and this secret is worth up to 400 billed X posts per request to
    # whoever reads it. Railway redirects http->https, but urllib follows the
    # redirect only AFTER the first request has already left with the header.
    if url.startswith("http://"):
        raise ValueError("CORE_API_URL must be https:// -- the shared secret "
                         "is sent as a request header")
    if not url.startswith("https://"):
        raise ValueError("CORE_API_URL must be an absolute https:// URL")
    return url


def analyze_remote(ticker: str, sector: str = "unknown", *,
                   feature: str = "deep_analyze",
                   route: str | None = None,
                   event_id: str | None = None,
                   persist: bool = True,
                   timeout: int = DEFAULT_TIMEOUT_S) -> RemoteResult:
    """One Deep Analyze, over HTTPS. Never raises."""
    from utils import config as _c

    if feature not in _FEATURES:
        raise ValueError(f"unknown feature {feature!r}")
    if route is not None and route not in _ROUTES:
        raise ValueError(f"unknown route {route!r}")

    try:
        base = _base()
    except ValueError as e:
        # Misconfiguration, not an outage. Nothing was sent, so the caller may
        # run it locally rather than refusing a paid request over a typo.
        logger.error("core-api URL rejected: %s", e)
        return RemoteResult(error=str(e), retryable=True)

    secret = _c.get("CORE_API_SHARED_SECRET") or ""
    body = json.dumps({
        "ticker": ticker, "sector": sector or "unknown",
        "feature": feature, "route": route,
        "event_id": event_id, "persist": persist,
    }).encode()
    req = urllib.request.Request(
        f"{base}/analyze", data=body, method="POST",
        headers={"content-type": "application/json",
                 "X-Core-Secret": secret})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read() or b"{}")
            status = r.status
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (e.read() or b"")[:300].decode("utf-8", "replace")
        except Exception:
            pass
        # THE HEADER, not the body. The service stamps X-Core-Refused on every
        # response it produces before doing any work. Sniffing the word
        # "missing" out of the body instead mis-classified an unset shared
        # secret -- the likeliest cutover misconfiguration -- as a spend, and
        # would have matched any platform 503 that happened to contain it.
        refused = ""
        try:
            refused = (e.headers or {}).get("X-Core-Refused") or ""
        except Exception:
            pass
        # 401/403 never reach the handler; 422 fails validation; 429 is raised
        # before a slot is taken. All four provably precede any spend.
        pre_spend = e.code in (401, 403, 422, 429) or bool(refused)
        logger.error("core-api %s for %s (refused=%s): %s",
                     e.code, ticker, refused or "-", detail[:200])
        return RemoteResult(error=f"core-api {e.code}", status=e.code,
                            retryable=pre_spend)
    except urllib.error.URLError as e:
        # Connection refused, DNS failure, TLS failure -- the request never
        # reached the handler. socket.timeout arrives here too and must NOT be
        # treated as pre-spend: by then the service may have finished.
        import socket
        timed_out = isinstance(getattr(e, "reason", None), socket.timeout)
        logger.error("core-api unreachable for %s: %s", ticker, e)
        return RemoteResult(error=f"core-api unreachable: {e.reason}",
                            retryable=not timed_out)
    except TimeoutError:
        logger.error("core-api timed out for %s after %ss", ticker, timeout)
        return RemoteResult(error="core-api timed out", retryable=False)
    except Exception as e:
        logger.exception("core-api call failed for %s", ticker)
        return RemoteResult(error=f"{type(e).__name__}: {e}", retryable=False)

    if not payload.get("ok"):
        # A real answer: the service ran and found nothing usable. The money is
        # already spent, so this is NOT retryable -- it is the result. Carry
        # posts_billed: whether money was spent is the one fact a caller's
        # refund decision turns on, and the service returns it for that reason.
        return RemoteResult(error=payload.get("error") or "no usable evidence",
                            status=status,
                            posts_billed=payload.get("posts_billed"),
                            elapsed_s=payload.get("elapsed_s"))

    results = payload.get("analysis_results")
    if not results:
        # VERSION SKEW, not an empty analysis. analysis_results is newer than
        # the card, so a service one deploy behind returns ok:true with a good
        # card and no breakdown. Treating that as "nothing came back" refunds a
        # user for an analysis the service already ran, billed and recorded --
        # and shows them a blank page. Deliver the card; the expander is the
        # only thing missing, and the caller can tell it is absent.
        logger.warning("core-api returned no analysis_results for %s -- "
                       "service may predate the field", ticker)
    return RemoteResult(ok=True, card=payload.get("card") or {},
                        analysis_results=results or {},
                        degraded=bool(payload.get("degraded")),
                        status=status, elapsed_s=payload.get("elapsed_s"))
