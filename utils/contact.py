"""Contact messages: write one, read the queue, mark it handled. Stdlib only.

WHY THE RETURN VALUE MATTERS HERE, UNLIKE THE OTHER WRITERS

utils/signal_log.py and utils/verdict_log.py swallow their failures on purpose:
the user has already been served, and a lost telemetry row costs a data point.
This module looks the same and is not. A lost message is a person who believes
they contacted you and did not -- most often about billing, from an account they
cannot log into to try again.

So `submit()` returns a bool and the caller MUST branch on it. The page may only
say "Message received" when a row actually landed.

PUBLIC WRITE, SERVICE-ROLE CREDENTIAL. pages/Contact.py has no login guard, and
should not: whoever is locked out is exactly who needs to reach you. The write
therefore happens here, server-side, with the service-role key; the browser
never holds anything that can touch the table.

VALIDATION IS PART OF THE CONTRACT, not decoration. The form previously accepted
an empty email and an empty message and reported success.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from utils.config import get as _config

logger = logging.getLogger(__name__)

TABLE = "contact_messages"
TIMEOUT_S = 10

# Deliberately permissive. This is a "did you mean to type an address" check,
# not an RFC 5322 parser -- rejecting a real, unusual address is worse than
# accepting a fake one, because a fake one merely wastes a row while a rejected
# one loses a customer who then has nowhere to go.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

MAX_MESSAGE = 4000
MAX_EMAIL = 254
MAX_UA = 512

TOPICS = ("Question", "Bug report", "Billing", "Feature request", "Partnership")


def validate(email: str, message: str) -> str | None:
    """None when the submission is sendable, else the reason to show the user.

    Returns the REASON rather than a bool so the page can say what is wrong.
    "Please check your entries" is the kind of message people give up on.
    """
    email = (email or "").strip()
    message = (message or "").strip()
    if not email:
        return "Please add an email address so we can reply."
    if len(email) > MAX_EMAIL or not _EMAIL.match(email):
        return "That email address does not look right — please check it."
    if not message:
        return "Please write a message."
    if len(message) > MAX_MESSAGE:
        return f"Message is too long ({len(message)} characters, limit {MAX_MESSAGE})."
    return None


def _clean(v: str) -> str:
    return "".join(ch for ch in (v or "").strip()
                   if ch >= " " or ch in "\n\t")


def _headers(key: str, prefer: str = "return=minimal") -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": prefer}


# Server-side, because the only limit that existed was st.session_state --
# which stopped a human sending a legitimate follow-up and cost a script
# nothing, since reconnecting is a fresh session. The risk being managed is not
# really cost; it is that a few hundred junk rows bury every genuine older
# message in a queue the admin page shows newest-first.
MAX_PER_EMAIL_PER_HOUR = 5


def _recent_count_for(email: str) -> int:
    """How many this address has sent in the last hour. -1 when unknown."""
    from datetime import datetime, timedelta, timezone
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        return -1
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    qs = urllib.parse.urlencode({
        "select": "id", "email": f"eq.{email}", "created_at": f"gte.{since}"})
    try:
        req = urllib.request.Request(
            f"{base}/rest/v1/{TABLE}?{qs}",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Prefer": "count=exact", "Range": "0-0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            rng = r.headers.get("Content-Range", "")
        return int(rng.split("/")[-1]) if "/" in rng else -1
    except Exception:
        # Unknown is NOT "too many": failing to count must never block a message.
        return -1


def submit(topic: str, email: str, message: str, *,
           user_agent: str | None = None, user_id: str | None = None
           ) -> tuple[bool, str | None]:
    """Store one message. Returns (stored, reason_to_show).

    A TUPLE, not a bool, because the caller must never invent the reason. The
    page previously said "Message received" on a path that stored nothing, and
    "we already have your message" on one that had discarded it.

    Never raises -- a traceback on a contact page helps nobody -- but a False
    return means the caller must NOT tell the sender it arrived.
    """
    problem = validate(email, message)
    if problem is not None:
        return False, problem

    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        logger.error("contact: no Supabase credentials; message NOT saved")
        return False, None

    email = email.strip()
    seen = _recent_count_for(email)
    if seen >= MAX_PER_EMAIL_PER_HOUR:
        logger.warning("contact: rate limit hit for %s (%d in the last hour)",
                       email, seen)
        return False, ("We already have several messages from this address in "
                       "the last hour — we will reply to those.")

    row = {
        "topic": (str(topic or "Question").strip() or "Question")[:64],
        "email": email.strip()[:MAX_EMAIL],
        # Control characters are valid JSON and invalid in a Postgres `text`
        # column; a literal NUL would 400 the insert and lose the message.
        "message": _clean(message)[:MAX_MESSAGE],
        "user_agent": (str(user_agent)[:MAX_UA] if user_agent else None),
        "user_id": user_id or None,
    }
    req = urllib.request.Request(
        f"{base}/rest/v1/{TABLE}", data=json.dumps([row]).encode(),
        headers=_headers(key), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            if r.status not in (200, 201, 204):
                logger.error("contact: HTTP %s; message NOT saved", r.status)
                return False, None
    except urllib.error.HTTPError as e:
        body = (e.read() or b"")[:300].decode(errors="replace")
        logger.error("contact: HTTP %s; message NOT saved: %s", e.code, body)
        return False, None
    except Exception as e:
        logger.error("contact: %s; message NOT saved: %s",
                     type(e).__name__, str(e)[:200])
        return False, None

    # The BODY is not logged -- that is where the billing details are, and the
    # admin page is the place to read it. The address is, so a failed reply or
    # a missing row can be traced to a person.
    logger.info("📬 contact: %s from %s (%d chars)",
                row["topic"], row["email"], len(row["message"]))
    return True, None


def md_escape(v: str) -> str:
    """Neutralise markdown in untrusted text before an operator screen shows it.

    st.caption and st.expander labels both render GitHub-flavoured Markdown,
    and two of the fields they display -- `email` and `user_agent` -- arrive
    from an unauthenticated form. A User-Agent of
    `![](https://attacker.example/p.png)` renders a remote image the moment an
    admin opens the queue: a beacon confirming the page was read and leaking
    the admin's IP. `[Refund approved](https://...)` renders a live link on the
    one screen an operator trusts. The email validator does not stop either --
    its local part accepts brackets and parentheses.
    """
    out = str(v or "")
    for ch in "\\`*_[]()#+-!<>|~":
        out = out.replace(ch, "\\" + ch)
    return out


def recent(limit: int = 100, unhandled_only: bool = False) -> list[dict] | None:
    """Newest first, or None when the read FAILED.

    None rather than [] on failure, because a dashboard that renders an empty
    inbox for a broken query tells the operator there is nothing to do while
    the queue is full -- the same class of silent failure that lost every
    message this table exists to keep.
    """
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        return None
    params = {"select": "*", "order": "created_at.desc",
              "limit": str(max(1, min(int(limit), 500)))}
    if unhandled_only:
        params["handled_at"] = "is.null"
    try:
        req = urllib.request.Request(
            f"{base}/rest/v1/{TABLE}?{urllib.parse.urlencode(params)}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read() or b"[]")
    except Exception as e:
        logger.warning("contact: read failed: %s: %s", type(e).__name__, str(e)[:160])
        return None


def unhandled_count() -> int | None:
    """How many are open. A HEAD count, not a second full fetch of every body."""
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        return None
    qs = urllib.parse.urlencode({"select": "id", "handled_at": "is.null"})
    try:
        req = urllib.request.Request(
            f"{base}/rest/v1/{TABLE}?{qs}",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Prefer": "count=exact", "Range": "0-0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            rng = r.headers.get("Content-Range", "")
        return int(rng.split("/")[-1]) if "/" in rng else None
    except Exception:
        return None


def set_handled(message_id: str, handled: bool = True,
                note: str | None = None) -> bool:
    """Mark one message done, or reopen it. True only if a row actually moved."""
    base = _config("SUPABASE_URL").rstrip("/")
    key = _config("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key or not message_id:
        return False
    payload: dict[str, Any] = {
        "handled_at": ("now()" if handled else None),
        "handled_note": ((note or "").strip()[:1000] or None) if handled else None,
    }
    # PostgREST will not evaluate now() from a JSON body; send a real timestamp.
    if handled:
        from datetime import datetime, timezone
        payload["handled_at"] = datetime.now(timezone.utc).isoformat()
    try:
        req = urllib.request.Request(
            f"{base}/rest/v1/{TABLE}?id=eq.{urllib.parse.quote(str(message_id))}",
            data=json.dumps(payload).encode(),
            # return=representation, so a mistyped or deleted id reports
            # failure. With return=minimal a PATCH matching ZERO rows still
            # answers 204 and the page would say it worked.
            headers=_headers(key, "return=representation"), method="PATCH")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return bool(json.loads(r.read() or b"[]"))
    except Exception as e:
        logger.warning("contact: update failed: %s: %s", type(e).__name__, str(e)[:160])
        return False
