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
    print("\nloud when blocked, quiet otherwise -- never to persuade")
    B, st, calls = fresh()
    calls["_click"] = False
    B.render_credit_meter(profile={"credits": 8}, key="k")
    check("a healthy balance draws no primary button",
          calls["button"] == [f"+ {B.PACK_CREDITS} credits · {B.PACK_PRICE}"],
          str(calls["button"]))

    B, st, calls = fresh(); calls["_click"] = False
    B.render_credit_meter(profile={"credits": 0}, key="k")
    check("at zero the control becomes the buy action",
          calls["button"] == ["Buy credits →"], str(calls["button"]))

    B, st, calls = fresh(); calls["_click"] = False
    B.render_credit_meter(profile=None, key="k")
    check("a missing profile reads as zero, not a crash",
          calls["button"] == ["Buy credits →"], str(calls["button"]))


def test_the_meter_shows_the_same_number_everywhere():
    """The inverse of what this suite used to assert.

    The old test checked that "a page shows the credit IT spends" -- which was
    the two-bucket contract, and also a live bug: Discovery rendered the SCAN
    balance while the Deep Analyze button in its own results table charged a
    DEEP credit, so a user could read "3 scans left" and be refused by a button
    two inches away. One wallet means the number on screen is the number every
    button on the page charges, so the meter must NOT vary by page.
    """
    print("\nthe number on screen is the number every button charges")
    out = []
    for key in ("discovery", "deep", "home"):
        B, st, calls = fresh(); calls["_click"] = False
        B.render_credit_meter(profile={"credits": 7}, key=key)
        out.append(calls["button"])
    check("every page renders the identical control",
          len(set(map(tuple, out))) == 1, str(out))

    # A stale reader is the failure this catches. scan_credits/deep_credits are
    # frozen pre-merge snapshots; a meter still reading them would show a
    # confident, wrong, and usually LARGER number than the user can spend.
    B, st, calls = fresh(); calls["_click"] = False
    B.render_credit_meter(profile={"scan_credits": 9, "deep_credits": 9}, key="k")
    check("the frozen columns are not a balance",
          calls["button"] == ["Buy credits →"],
          "the meter read a pre-merge snapshot instead of `credits`")


def test_the_low_warning_is_scaled_to_the_pack():
    """The threshold has to be a function of the pack, not a taste.

    It was <= 3 while the pack was going to be ten-for-$5. Against a pack of
    two that means a user who has JUST PAID opens the app already in the
    warning state -- the buy flow's own success condition rendering as a
    problem. Derived from PACK_CREDITS now, so the two cannot drift.
    """
    print("\nthe warning must not fire on a freshly bought balance")
    import re as _re
    seen = {}
    for n in (0, 1, 2, 3, 4):
        B, st, calls = fresh(); calls["_click"] = False
        blobs = []
        st.markdown = lambda *a, **k: blobs.append(a[0] if a else "")
        B.render_credit_meter(profile={"credits": n}, key="k")
        seen[n] = "".join(blobs)
    B, st, calls = fresh()   # for B.PACK_CREDITS below
    amber = "245,158,11"
    check("a full pack is quiet", amber not in seen[B.PACK_CREDITS],
          seen[B.PACK_CREDITS][:120])
    check("3 credits is quiet", amber not in seen[3], seen[3][:120])
    check("1 credit warns", amber in seen[1], seen[1][:120])
    # Tags stripped first: the count sits inside a <b>, so "1 credit left" is
    # never a contiguous substring of the markup even when it renders correctly.
    text = {n: _re.sub(r"<[^>]+>", "", v) for n, v in seen.items()}
    check("1 credit is singular", "1 credit left" in text[1], text[1][:160])
    check("3 credits is plural", "3 credits left" in text[3], text[3][:160])
    check("0 is not amber but red", amber not in seen[0] and "248,113,113" in seen[0],
          seen[0][:160])


def test_the_advertised_pack_matches_what_stripe_grants():
    """LITERALS on both sides, and a cross-service equality check.

    Found by the review, not by me: setting utils.billing.PACK_CREDITS to 10
    and PACK_PRICE to "$19" left all 1530 assertions green. Every meter test
    read B.PACK_CREDITS on both sides of its assertion, so they moved with the
    code instead of pinning it -- the same self-referential trap that hid a
    pack-size drift in test_payments until it was pinned to a literal there.

    What that permits is the worst kind of billing bug: the button says
    "+ 10 credits · $19", Stripe charges $5, and the balance moves by 2. The
    portal advertises a price and a quantity it does not control -- payments_api
    derives the grant from the amount Stripe reports -- so the portal can only
    ever be right or lying.
    """
    print("\nthe price on the button is the price Stripe charges")
    B, st, calls = fresh()
    check("PACK_CREDITS is 2", B.PACK_CREDITS == 2, str(B.PACK_CREDITS))
    check("PACK_PRICE is $5", B.PACK_PRICE == "$5", str(B.PACK_PRICE))

    # The two services cannot import each other, so nothing but a test can hold
    # them together. Read payments_api's constants from source rather than
    # importing it -- importing pulls in stripe/fastapi and this suite is a
    # no-dependency stub.
    import re
    src = (REPO / "payments_api" / "main.py").read_text()
    m = re.search(r"^PACK_CREDITS = (\d+)", src, re.M)
    check("payments_api declares a pack size", bool(m), "PACK_CREDITS not found")
    if m:
        check(f"portal advertises what payments_api grants ({m.group(1)})",
              int(m.group(1)) == B.PACK_CREDITS,
              f"portal says {B.PACK_CREDITS}, payments_api grants {m.group(1)}")
    amt = re.search(r'pack_currency, pack_amount = "usd", (\d+)', src)
    check("payments_api charges an amount", bool(amt), "pack_amount not found")
    if amt:
        check(f"portal advertises what Stripe charges (${int(amt.group(1))/100:.0f})",
              B.PACK_PRICE == f"${int(amt.group(1)) // 100}",
              f"portal says {B.PACK_PRICE}, Stripe charges {amt.group(1)} cents")

    # And the copy actually renders those, rather than a hardcoded duplicate.
    calls["_click"] = False
    B.render_buy_credits(key="k")
    check("the button states the real offer",
          calls["button"] == [f"+ {B.PACK_CREDITS} credits · {B.PACK_PRICE}"],
          str(calls["button"]))


def test_no_page_reads_the_frozen_columns_for_a_balance():
    """The engine is well covered; the surfaces that show a user a number were not.

    Also found by the review: reverting utils/profile.py, pages/Home.py and
    pages/Admin.py to select scan_credits/deep_credits left the whole suite
    green. Those three are the only places a balance reaches a human, and Home
    and Admin do not use render_credit_meter -- they carry their own queries, so
    the existing "every page uses one implementation" grep cannot see them.

    The Admin case is the expensive one: admin_adjust_credits is an ABSOLUTE set
    of the merged balance taken from whatever the page rendered. A page reading
    the frozen snapshot and saving any unrelated field writes the pre-merge
    number over the live balance -- with an actor, a reason, and a ledger row,
    so reconciliation agrees with it.
    """
    print("\na balance shown to a user must come from `credits`")
    import io, re as _re, tokenize

    def code_only(path):
        toks = [t for t in tokenize.generate_tokens(
            io.StringIO(path.read_text()).readline) if t.type != tokenize.COMMENT]
        return tokenize.untokenize(toks)

    for rel in ("utils/profile.py", "pages/Home.py", "pages/Admin.py"):
        src = code_only(REPO / rel)
        # The tell is a SELECT list naming the frozen columns -- not any mention
        # of them. Admin.py legitimately carries the string
        # "deep_credits_retired" (the reason code for a stale caller), and a
        # substring match reports that as a violation. Pull the argument of each
        # .select(...) and look only in there.
        selects = _re.findall(r'\.select\(\s*((?:"[^"]*"\s*)+)', src)
        if not selects:
            # No query of its own. That is the BETTER shape -- Home was moved to
            # utils.profile.fetch_credits precisely because a second copy of the
            # query meant a second copy of the fallback to forget. Nothing to
            # check here beyond it not reading the frozen columns some other way.
            check(f"{rel} does not touch the frozen columns at all",
                  "scan_credits" not in src and "deep_credits" not in src,
                  "reads a pre-merge snapshot without going through a .select")
            continue

        # THE FIRST select is the one that must be right. utils/profile.py
        # deliberately carries a SECOND, guarded select of the frozen columns --
        # the deploy-order fallback for the window before the merge migration is
        # applied by hand. Forbidding every mention would fail that on purpose;
        # forbidding only the primary read is the actual rule.
        first = selects[0]
        for col in ("scan_credits", "deep_credits"):
            check(f"{rel} does not lead with profiles.{col}", col not in first,
                  f"a frozen pre-merge snapshot is not a spendable balance: {first[:120]}")
        check(f"{rel} leads with credits", "credits" in first,
              f"no merged balance read: {first[:120]}")

        # And any frozen read that does exist must be behind an except.
        for extra in selects[1:]:
            if "scan_credits" in extra or "deep_credits" in extra:
                check(f"{rel}'s frozen-column read is a guarded fallback",
                      "except" in src,
                      "an unguarded second read of the pre-merge snapshot")


def test_a_refusal_only_offers_a_purchase_when_buying_would_help():
    """consume_credit refuses for six reasons; one of them is about money.

    Handing all six to the upgrade panel shows "You're out of credits" and a
    primary Buy button to a suspended account -- which can then pay, because
    grant_credits has no `disabled` guard while consume_credit does -- and to
    everyone at once during a Supabase blip.
    """
    print("\nonly a balance problem gets a Buy button")

    class R:
        def __init__(self, reason, message):
            self.ok, self.reason, self.message = False, reason, message

    B, st, calls = fresh(); calls["_click"] = False
    B.render_credit_refusal(R("no_credits", "No credits remaining"), "offer", key="k")
    check("out of credits offers the pack",
          calls["button"] == ["Buy credits →"], str(calls["button"]))
    check("...and does not show a bare error", calls["error"] == [], str(calls["error"]))

    for reason, msg in (("account_disabled", "Account disabled"),
                        ("rpc_error", "Could not verify your credits."),
                        ("bad_response", "Unexpected response."),
                        ("not_logged_in", "Please sign in."),
                        ("profile_not_found", "Profile not found")):
        B, st, calls = fresh(); calls["_click"] = False
        B.render_credit_refusal(R(reason, msg), "offer", key="k")
        check(f"{reason}: no Buy button", calls["button"] == [], str(calls["button"]))
        check(f"{reason}: says what actually happened", msg in calls["error"],
              str(calls["error"]))

    # The two transient ones must not read as a permanent state.
    B, st, calls = fresh(); calls["_click"] = False
    B.render_credit_refusal(R("rpc_error", "Could not verify your credits."), "o", key="k")
    check("a transient failure says so", calls["caption"] >= 1,
          "no reassurance that the credit was not taken")


def test_the_buy_button_does_not_depend_on_reading_the_balance():
    """Not knowing the number is a reason to SHOW the button, not to hide it.

    Home carries its own credits query, separate from utils.profile, and both
    the balance pill and the Buy control lived inside `if credits_c is not
    None`. So when the read failed -- which it does for every user until the
    merge migration is applied by hand -- the page rendered no balance AND no
    way to buy. The control had been an inert <span> reading "Coming soon" for
    months; gating it on a failed read reproduced that dead end by another
    route, and the owner saw exactly that on the live site.

    AST, not a grep: the question is whether the call is NESTED inside that
    conditional, and a substring search cannot see nesting.
    """
    print("\nthe Buy control survives a balance we cannot read")
    import ast
    tree = ast.parse((REPO / "pages" / "Home.py").read_text())

    def calls_buy(node):
        return any(isinstance(n, ast.Attribute) and n.attr == "render_buy_credits"
                   for n in ast.walk(node))

    gated = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.dump(node.test)
        if "credits_c" not in test_src:
            continue
        for stmt in node.body:
            if calls_buy(stmt):
                gated.append(node.lineno)
    check("Buy is not nested inside the balance-read conditional", not gated,
          f"render_buy_credits is inside `if credits_c ...` at line(s) {gated}")
    check("...and Home still renders it at all", calls_buy(tree),
          "the buy control disappeared from Home entirely")


def test_home_reads_survive_the_deploy_window():
    """Home must not carry its OWN copy of the balance query.

    It did, which is precisely why making utils/profile.py tolerant of the
    pre-migration window did nothing for the landing page: Home queried
    `profiles` directly, got 42703, returned None, and rendered neither the
    balance nor the Buy button. Duplicating a fallback is how one copy silently
    goes stale.

    The behaviour of the shared helper is asserted in tests/test_profile_fallback.py,
    by running it. This only checks that Home routes through it.
    """
    print("\nHome delegates the balance read instead of duplicating it")
    import ast
    src = (REPO / "pages" / "Home.py").read_text()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_get_credits"), None)
    check("Home has a _get_credits", fn is not None)
    if fn is None:
        return
    body = ast.dump(fn)
    check("it calls the shared fetch_credits", "fetch_credits" in body,
          "Home is querying profiles itself again")
    check("...and does not query the table directly",
          '"profiles"' not in ast.get_source_segment(src, fn),
          "a second copy of the query is a second copy of the fallback to forget")


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
    # Either entry point counts: render_credit_refusal is the one a spend page
    # should use (it decides whether buying would help), and it calls the modal.
    check("Deep_Analysis offers a way to buy when refused",
          "render_credit_refusal" in deep or "render_upgrade_modal" in deep,
          "it dead-ends on st.error again")
    check("both spend pages route refusals through the dispatcher",
          "render_credit_refusal" in deep and "render_credit_refusal" in disc,
          "a raw upgrade modal shows 'out of credits' for a Supabase outage")
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
    # DISCOVERED, not listed. This runner carried a hand-typed tuple, and two
    # tests added in the same commit were simply left out of it -- they never
    # ran, and the suite reported green. Found by mutation testing: breaking the
    # low-balance threshold changed nothing, because the test for it was never
    # called. A list of tests is one more thing that has to be kept in step.
    for name, t in [(k, v) for k, v in sorted(globals().items())
                    if k.startswith("test_") and callable(v)]:
        try:
            t()
        except Exception as e:
            # A test that RAISES is a failure, not the end of the run.
            check(f"{name} raised", False, f"{type(e).__name__}: {e}")
    print("\n" + "=" * 74)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for n, d in FAILED:
        print(f"    - {n}: {d}")
    print("=" * 74)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
