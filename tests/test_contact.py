#!/usr/bin/env python3
"""Pin the contact form. The bug class here is telling someone you have their
message when you do not.

The page it replaces wrote one line to stdout and said "Message received", so
every message ever sent through it is gone -- most likely including billing
problems from people who could not log in to report them another way.

The first attempt at fixing that reintroduced the same bug twice: a honeypot
field that `label_visibility="collapsed"` rendered VISIBLY (it hides the label,
not the widget), so a confused human filling the only unlabelled box on the page
got "Message received" and no row; and a session throttle that discarded a
legitimate follow-up while telling the sender "we already have your message".

So the tests below are mostly about the seams where a message can vanish, and
about an operator screen that renders text a stranger wrote.

No network: every write path is exercised against a stubbed transport.

Usage:
    python3 tests/test_contact.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from utils import contact as C  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


class _Resp:
    def __init__(self, status=201, body=b"[]", headers=None):
        self.status, self._b = status, body
        self.headers = headers or {}

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def with_transport(fn, *, post=None, get=None, patch=None, creds=True):
    """Run fn() with urlopen and config stubbed. Returns (result, captured)."""
    cap: dict = {}
    real_open, real_cfg = urllib.request.urlopen, C._config

    def fake(req, timeout=None):
        method = req.get_method()
        cap.setdefault("calls", []).append((method, req.full_url))
        if req.data:
            cap["body"] = json.loads(req.data.decode())
        cap["headers"] = dict(req.headers)
        h = {"POST": post, "GET": get, "PATCH": patch}[method]
        if isinstance(h, Exception):
            raise h
        return h if h is not None else _Resp()

    urllib.request.urlopen = fake
    C._config = (lambda n, d="": ("https://db.test" if "URL" in n else "key")) \
        if creds else (lambda n, d="": "")
    try:
        return fn(), cap
    finally:
        urllib.request.urlopen, C._config = real_open, real_cfg


def test_validation_says_what_is_wrong():
    print("\nvalidation: a reason the sender can act on, not 'check your entries'")
    for email, msg, want in (
        ("", "hi", "email"), ("nope", "hi", "not look right"),
        ("a@b.co", "", "write a message"),
        ("a@b.co", "x" * 5000, "too long"),
    ):
        r = C.validate(email, msg) or ""
        check(f"{email!r}/{len(msg)}ch names the problem", want in r, r)
    check("a real submission passes", C.validate("a@b.co", "hello") is None)


def test_submit_never_claims_a_message_it_did_not_store():
    print("\nTHE CONTRACT: (stored, reason) — the caller must not invent either")
    out, _ = with_transport(lambda: C.submit("Q", "bad", "hi"))
    check("validation failure -> (False, reason)",
          out[0] is False and out[1] and "not look right" in out[1], str(out))

    out, _ = with_transport(lambda: C.submit("Q", "a@b.co", "hi"), creds=False)
    check("no credentials -> (False, None), never a silent success",
          out == (False, None), str(out))

    out, _ = with_transport(lambda: C.submit("Q", "a@b.co", "hi"),
                            get=_Resp(200, b"[]", {"Content-Range": "*/0"}),
                            post=urllib.error.HTTPError(
                                "u", 400, "bad", {}, None))
    check("HTTP error -> (False, None)", out == (False, None), str(out))

    out, cap = with_transport(lambda: C.submit("Q", "a@b.co", "hello"),
                              get=_Resp(200, b"[]", {"Content-Range": "*/0"}))
    check("a stored message -> (True, None)", out == (True, None), str(out))
    check("...and the row carries the fields", cap["body"][0]["email"] == "a@b.co")


def test_the_rate_limit_is_server_side_and_fails_open():
    print("\nrate limit: session_state stopped humans and no scripts")
    out, _ = with_transport(lambda: C.submit("Q", "a@b.co", "hi"),
                            get=_Resp(200, b"[]", {"Content-Range": "*/99"}))
    check("over the hourly limit -> refused, with a reason",
          out[0] is False and out[1] and "several messages" in out[1], str(out))
    # Failing to COUNT must never block a message.
    out, _ = with_transport(lambda: C.submit("Q", "a@b.co", "hi"),
                            get=urllib.error.HTTPError("u", 500, "x", {}, None))
    check("an unknown count fails OPEN, so a real message still lands",
          out == (True, None), str(out))


def test_untrusted_text_cannot_render_on_the_admin_screen():
    print("\nthe admin page renders markdown; two of its fields come from strangers")
    for payload in ("![](https://evil.example/p.png)",
                    "[Refund approved](https://evil.example)",
                    "**bold**", "`code`"):
        out = C.md_escape(payload)
        check(f"{payload[:26]!r} is neutralised",
              not any(out.count(c) and f"\\{c}" not in out for c in "[]()!*`"),
              out)
    # The validator does NOT stop these, which is why escaping is required.
    check("the email validator permits a markdown payload (hence md_escape)",
          C.validate("![](https://e/x.png)@e.co", "hi") is None)
    admin = (REPO / "pages" / "Admin.py").read_text()
    check("the expander label is escaped", "md_escape(_m.get('email'" in admin
          or "md_escape(_m.get(\"email\"" in admin)
    check("the body is st.text, never markdown", "st.text(_m.get(" in admin)


def test_a_failed_read_is_not_an_empty_inbox():
    print("\nan operator must never be shown a clean queue that is not clean")
    out, _ = with_transport(lambda: C.recent(10), creds=False)
    check("no credentials -> None, not []", out is None, str(out))
    out, _ = with_transport(lambda: C.recent(10),
                            get=urllib.error.HTTPError("u", 500, "x", {}, None))
    check("a failed read -> None, not []", out is None, str(out))
    out, _ = with_transport(lambda: C.recent(10), get=_Resp(200, b'[{"id":"1"}]'))
    check("a good read -> the rows", out == [{"id": "1"}], str(out))
    admin = (REPO / "pages" / "Admin.py").read_text()
    check("...and the page distinguishes the two",
          "_messages is None" in admin and "may not be empty" in admin)


def test_marking_handled_reports_the_truth():
    print("\nset_handled: a PATCH matching zero rows answers 204")
    out, cap = with_transport(lambda: C.set_handled("abc", True, "done"),
                              patch=_Resp(200, b"[]"))
    check("no row matched -> False", out is False, str(out))
    check("...because it asks for the row back",
          "return=representation" in str(cap.get("headers", {})))
    out, cap = with_transport(lambda: C.set_handled("abc", True, "done"),
                              patch=_Resp(200, b'[{"id":"abc"}]'))
    check("a real update -> True", out is True)
    check("the note is stored", cap["body"]["handled_note"] == "done")
    out, cap = with_transport(lambda: C.set_handled("abc", False),
                              patch=_Resp(200, b'[{"id":"abc"}]'))
    check("reopening nulls both columns",
          cap["body"] == {"handled_at": None, "handled_note": None}, str(cap["body"]))


def test_the_page_cannot_lose_what_someone_typed():
    print("\nthe form: a failure must not erase the message")
    page = (REPO / "pages" / "Contact.py").read_text()
    check("clear_on_submit is OFF", "clear_on_submit=False" in page)
    check("...and the fields are cleared only on success",
          "st.session_state.pop" in page)
    check("the honeypot is gone", "website" not in page.split("HONEYPOT")[0]
          or "st.text_input(\"Website" not in page)
    check("the session throttle is gone", "_too_fast" not in page)
    check("success is spoken only when the row landed",
          "ok, why = _contact.submit" in page and "if ok:" in page)
    check("a failed write points at the support address",
          "could not save your message" in page)
    check("no login is required to reach support",
          "require_active_account" not in page)


def test_nul_bytes_cannot_lose_a_message():
    print("\ncontrol characters are legal JSON and illegal in a postgres text column")
    out, cap = with_transport(
        lambda: C.submit("Q", "a@b.co", "hel\x00lo\nworld"),
        get=_Resp(200, b"[]", {"Content-Range": "*/0"}))
    check("stored", out == (True, None), str(out))
    check("the NUL is stripped", "\x00" not in cap["body"][0]["message"])
    check("...but the newline survives", "\n" in cap["body"][0]["message"])


def main() -> int:
    print("=" * 74)
    print("  contact: never tell someone you have their message when you do not")
    print("=" * 74)
    test_validation_says_what_is_wrong()
    test_submit_never_claims_a_message_it_did_not_store()
    test_the_rate_limit_is_server_side_and_fails_open()
    test_untrusted_text_cannot_render_on_the_admin_screen()
    test_a_failed_read_is_not_an_empty_inbox()
    test_marking_handled_reports_the_truth()
    test_the_page_cannot_lose_what_someone_typed()
    test_nul_bytes_cannot_lose_a_message()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
