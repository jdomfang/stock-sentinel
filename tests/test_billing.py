#!/usr/bin/env python3
"""Buying credits: the paths that cost money or hide a failure.

WHY THIS EXISTS

The purchase surface had three defects at once, and each was invisible:

  * the only buy control appeared when consume_credit REFUSED, so a user had
    to spend everything before they could buy more;
  * `_get_checkout_url` swallowed every error and rendered a DISABLED button,
    so a broken payments service looked identical to a user not clicking;
  * a Checkout Session was created on every Streamlit rerun while the modal
    was open, littering Stripe with abandoned sessions.

And the redirect Stripe sends a buyer back to was read by nothing at all.

No network. requests and streamlit are stubbed.

Usage:
    python3 tests/test_billing.py
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name, cond, detail=""):
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")


# --------------------------------------------------------------- the harness

class _Secrets(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def fresh(secrets=None):
    """A billing module with streamlit stubbed. Returns (module, state)."""
    for m in [k for k in list(sys.modules) if k.startswith("utils.billing")]:
        del sys.modules[m]
    st = types.ModuleType("streamlit")
    st.session_state = {"auth.user": {"id": "u1"}}
    st.secrets = _Secrets(secrets if secrets is not None else {
        "PAYMENTS_API_BASE_URL": "https://pay.test",
        "PAYMENTS_API_SHARED_SECRET": "s3cret",
    })
    st.query_params = _Secrets()
    st.query_params.clear = lambda: dict.clear(st.query_params)
    calls: dict = {"error": [], "success": [], "info": [], "markdown": 0,
                   "link_button": [], "button": [], "rerun": 0, "caption": 0}
    st.error = lambda m, **k: calls["error"].append(m)
    st.success = lambda m, **k: calls["success"].append(m)
    st.info = lambda m, **k: calls["info"].append(m)
    st.caption = lambda *a, **k: calls.__setitem__("caption", calls["caption"] + 1)
    st.markdown = lambda *a, **k: calls.__setitem__("markdown", calls["markdown"] + 1)
    st.link_button = lambda label, url, **k: calls["link_button"].append((label, url))
    st.rerun = lambda: calls.__setitem__("rerun", calls["rerun"] + 1)

    class _Ctx:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    st.container = lambda **k: _Ctx()
    st.spinner = lambda *a, **k: _Ctx()
    st.columns = lambda spec, **k: [_Ctx() for _ in
                                    (spec if isinstance(spec, (list, tuple))
                                     else range(spec))]
    # Buttons return whatever the test queues up.
    st.button = lambda label, **k: (calls["button"].append(label)
                                    or calls.get("_click", False))
    sys.modules["streamlit"] = st
    import utils.billing as B
    return B, st, calls


def stub_requests(status=200, payload=None, raises=None):
    req = types.ModuleType("requests")

    class _R:
        status_code = status
        text = "body"
        def json(self): return payload or {}

    def _post(*a, **k):
        if raises:
            raise raises
        return _R()
    req.post = _post
    sys.modules["requests"] = req


# ------------------------------------------------ errors must reach the user

def test_a_broken_payments_service_is_visible():
    """The version this replaces rendered a DISABLED button and said nothing.

    A user facing an outage saw a dead control with no explanation, and an
    operator saw no log line at all -- indistinguishable from nobody clicking.
    """
    print("\\na checkout failure must say something, to someone")
    for label, status, raises, expect in (
            ("unreachable", None, ConnectionError("no route"), "reach checkout"),
            ("401 misconfigured", 401, None, "misconfigured"),
            ("500 upstream", 500, None, "unavailable"),
            ("200 with no url", 200, None, "did not return a link")):
        B, st, calls = fresh()
        stub_requests(status=status or 200, payload={}, raises=raises)
        url, err = B.get_checkout_url("u1")
        check(f"{label}: no url", url is None, str(url))
        check(f"{label}: names a reason", bool(err) and expect in err.lower(),
              str(err))


def test_unconfigured_payments_does_not_pretend():
    print("\\nno payments config is a stated reason, not a dead button")
    B, st, calls = fresh(secrets={})
    stub_requests()
    url, err = B.get_checkout_url("u1")
    check("no url", url is None)
    check("says it is not configured", "not configured" in (err or "").lower(),
          str(err))
    B, st, calls = fresh()
    url, err = B.get_checkout_url("")
    check("a signed-out user is told to sign in",
          url is None and "sign in" in (err or "").lower(), str(err))


# ------------------------------------------- one session, not one per rerun

def test_a_session_is_created_on_click_not_on_render():
    """Discovery.py called _get_checkout_url unconditionally inside the modal
    body, so Streamlit's rerun-on-every-interaction minted a new Stripe
    Checkout Session each time -- which is what the abandoned
    checkout.session.expired events in the sandbox actually were."""
    print("\\nrendering the control must not create a Stripe session")
    B, st, calls = fresh()
    posted = {"n": 0}

    def _count(*a, **k):
        posted["n"] += 1
        raise ConnectionError("should not be called")
    req = types.ModuleType("requests"); req.post = _count
    sys.modules["requests"] = req

    calls["_click"] = False
    for _ in range(5):                      # five reruns, nobody clicking
        B.render_buy_credits(key="t")
    check("five renders create zero sessions", posted["n"] == 0, str(posted["n"]))
    check("...and the button was drawn each time", len(calls["button"]) == 5,
          str(len(calls["button"])))


def test_a_fetched_url_is_reused_then_forgotten():
    print("\\na paid session stays payable ~24h; it must not linger on screen")
    B, st, calls = fresh()
    stub_requests(payload={"checkout_url": "https://checkout.test/abc"})
    calls["_click"] = True
    B.render_buy_credits(key="t")
    check("the click fetched a url",
          (st.session_state.get("billing.url") or {}).get("url")
          == "https://checkout.test/abc", str(st.session_state))
    check("...and reran to render the link", calls["rerun"] == 1, str(calls["rerun"]))

    calls["_click"] = False
    B.render_buy_credits(key="t")
    check("the second render shows a real link, not the fetch button",
          len(calls["link_button"]) == 1, str(calls["link_button"]))

    B.clear_pending_url()
    check("returning from Stripe forgets it -- a second click would pay twice",
          "billing.url" not in st.session_state, str(st.session_state))


def test_the_url_is_scoped_to_the_user_who_fetched_it():
    print("\\none browser session, two accounts: never serve the wrong link")
    import time
    B, st, calls = fresh()
    st.session_state["auth.user"] = {"id": "userA"}
    st.session_state["billing.url"] = {"uid": "userA", "url": "https://a",
                                       "at": time.time()}
    check("A gets A's link", B._pending_url("userA") == "https://a")
    check("B does not get A's link", B._pending_url("userB") is None)
    st.session_state["billing.url"]["at"] = time.time() - (B._URL_TTL_S + 5)
    check("an expired link is not reused", B._pending_url("userA") is None)


# ------------------------------------------------------- the redirect loop

def test_the_payment_return_is_read_where_it_can_be_read():
    """app.py ends with an unconditional st.switch_page, which raises AND
    clears query params -- so Home can never see ?payment=. Reading it on the
    landing page fails silently, which is what the app did: nothing at all."""
    print("\\nthe ?payment= redirect must be captured before the page switch")
    B, st, calls = fresh()
    st.query_params["payment"] = "success"
    got = B.consume_payment_return()
    check("success is captured", got == "success", str(got))
    check("...stashed where the next page can read it",
          st.session_state.get("billing.return") == "success")
    check("...and the param is cleared so a refresh does not replay it",
          "payment" not in st.query_params, str(dict(st.query_params)))

    # A PAID session must stop being clickable the moment the buyer returns.
    # Stripe Sessions stay payable for ~24h, so a link left on screen after a
    # purchase is a second charge waiting for a second click. Asserted on the
    # RETURN path, not just on clear_pending_url() -- calling the helper
    # directly proves the helper works, not that anything calls it.
    import time
    B, st, calls = fresh()
    st.session_state["billing.url"] = {"uid": "u1", "url": "https://x",
                                       "at": time.time()}
    st.query_params["payment"] = "success"
    B.consume_payment_return()
    check("returning from a successful payment drops the checkout link",
          "billing.url" not in st.session_state, str(st.session_state))

    # Cancel does NOT: the user chose not to pay and may click again.
    B, st, calls = fresh()
    st.session_state["billing.url"] = {"uid": "u1", "url": "https://x",
                                       "at": time.time()}
    st.query_params["payment"] = "cancel"
    check("cancel is captured too", B.consume_payment_return() == "cancel")
    check("...and keeps the link, since nothing was paid",
          "billing.url" in st.session_state, str(st.session_state))
    B, st, calls = fresh()
    st.query_params["payment"] = "../etc"
    check("anything else is ignored", B.consume_payment_return() is None)
    B, st, calls = fresh()
    check("no param is not a return", B.consume_payment_return() is None)


def test_success_never_claims_credits_arrived():
    """Credits are granted by an asynchronous webhook that can lag or fail, so
    the balance on screen may still be the old one. Asserting success we
    cannot see is how a working purchase gets charged back."""
    print("\\nsuccess must not assert a balance it has not read")
    B, st, calls = fresh()
    st.session_state["billing.return"] = "success"
    B.render_payment_return()
    msg = " ".join(calls["success"]).lower()
    check("it acknowledges the payment", "payment received" in msg, msg)
    check("...without claiming credits are already added",
          "credits added" not in msg and "credits have been" not in msg, msg)
    check("...and tells the user what to do if the number lags",
          "refresh" in msg, msg)
    check("the flag is consumed, so a rerun does not repeat it",
          "billing.return" not in st.session_state)

    B, st, calls = fresh()
    st.session_state["billing.return"] = "cancel"
    B.render_payment_return()
    check("cancel says no charge was made",
          "not been charged" in " ".join(calls["info"]).lower(),
          str(calls["info"]))


# ---------------------------------------------------------------- the meter

def test_the_meter_escalates_only_when_blocked():
    print("\\nloud when blocked, quiet otherwise -- never to persuade")
    B, st, calls = fresh()
    calls["_click"] = False
    B.render_credit_meter(kind="scan", profile={"scan_credits": 4}, key="k")
    check("a healthy balance draws no primary button",
          calls["button"] == ["+ Buy credits"], str(calls["button"]))

    B, st, calls = fresh(); calls["_click"] = False
    B.render_credit_meter(kind="scan", profile={"scan_credits": 0}, key="k")
    check("at zero the control becomes the buy action",
          calls["button"] == ["Buy credits →"], str(calls["button"]))

    B, st, calls = fresh(); calls["_click"] = False
    B.render_credit_meter(kind="deep", profile={"deep_credits": 2}, key="k")
    check("a page shows the credit IT spends", calls["markdown"] >= 1)
    B, st, calls = fresh(); calls["_click"] = False
    B.render_credit_meter(kind="deep", profile=None, key="k")
    check("a missing profile reads as zero, not a crash",
          calls["button"] == ["Buy credits →"], str(calls["button"]))


def test_every_page_uses_one_implementation():
    """_get_checkout_url and _upgrade_modal were page-private in Discovery,
    which is exactly why Deep_Analysis had no buy option and dead-ended on a
    bare st.error()."""
    print("\\nthree call sites, one implementation")
    def code_only(path):
        """Source with comments stripped.

        Both assertions below failed against comments first: app.py explains
        why the call precedes st.switch_page (mentioning it), and Home carries
        a comment quoting the `cursor:not-allowed` / "Coming soon" strings it
        says were removed. A comment is not code and must not answer for it.
        """
        import io, tokenize
        toks = [t for t in tokenize.generate_tokens(
            io.StringIO(path.read_text()).readline) if t.type != tokenize.COMMENT]
        return tokenize.untokenize(toks)

    disc = code_only(REPO / "pages" / "Discovery.py")
    deep = code_only(REPO / "pages" / "Deep_Analysis.py")
    home = code_only(REPO / "pages" / "Home.py")
    app = code_only(REPO / "app.py")

    check("Discovery defines no private checkout helper",
          "def _get_checkout_url" not in disc and "def _upgrade_modal" not in disc,
          "the copy that blocked Deep_Analysis came back")
    for name, src in (("Discovery", disc), ("Deep_Analysis", deep), ("Home", home)):
        check(f"{name} uses utils.billing", "billing." in src, name)
    check("Deep_Analysis offers a way to buy when refused",
          "render_upgrade_modal" in deep, "it dead-ends on st.error again")
    check("Home's inert placeholder is gone",
          "Coming soon" not in home and "cursor:not-allowed" not in home,
          "the disabled span is back")
    check("the payment return is consumed in app.py, before the page switch",
          "consume_payment_return()" in app
          and app.index("consume_payment_return()") < app.index("st.switch_page"),
          "after switch_page the query params are already gone")
    # Both spend pages show the balance beside the button that spends it.
    for name, src in (("Discovery", disc), ("Deep_Analysis", deep)):
        check(f"{name} renders the credit meter", "render_credit_meter" in src, name)


def main() -> int:
    print("=" * 74)
    print("  billing: buying credits without having to run out first")
    print("=" * 74)
    for t in (test_a_broken_payments_service_is_visible,
              test_unconfigured_payments_does_not_pretend,
              test_a_session_is_created_on_click_not_on_render,
              test_a_fetched_url_is_reused_then_forgotten,
              test_the_url_is_scoped_to_the_user_who_fetched_it,
              test_the_payment_return_is_read_where_it_can_be_read,
              test_success_never_claims_credits_arrived,
              test_the_meter_escalates_only_when_blocked,
              test_every_page_uses_one_implementation):
        t()
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
