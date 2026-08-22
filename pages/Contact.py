import logging

import streamlit as st

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page


LOG = logging.getLogger(__name__)

st.set_page_config(
    page_title="Stock Sentinel - Contact",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

render_sidebar_navigation()
render_top_nav()
apply_theme()

from utils.contact import TOPICS as _contact_topics_tuple


def _contact_topics():
    return _contact_topics_tuple

# NO LOGIN GUARD, deliberately. Whoever is locked out of their account is
# exactly who needs to reach support, so this page stays open and the write
# happens server-side with the service-role key -- the browser never holds a
# credential that can touch the table.

st.markdown('<div class="clawd-app-wrapper">', unsafe_allow_html=True)

st.markdown(
    """
    <div style="margin: -22px 0 8px 0;">
      <div style="color: rgba(56,189,248,.95); font-weight: 750; letter-spacing: 0.06em; text-transform: uppercase; font-size: 0.78rem; margin-bottom: 10px;">Support</div>
      <div style="font-size: 2.05rem; font-weight: 850; letter-spacing: -0.03em; line-height: 1.1; margin: 0 0 6px 0;">Contact</div>
      <div style="color: rgba(148,163,184,.95); font-size: 1.02rem; line-height: 1.5; margin: 0 0 10px 0; max-width: 980px;">Questions, billing issues, or a bug report? Send a message—include screenshots and the ticker/sector if relevant.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
      .contact-card {
        border: 1px solid var(--border);
        background: linear-gradient(180deg, rgba(15,23,42,.92), rgba(15,23,42,.72));
        border-radius: 14px;
        padding: 14px 14px;
      }
      .contact-muted { color: rgba(229,231,235,.72); font-size: 0.92rem; line-height: 1.45; }
      @media (max-width: 640px) { .contact-card { padding: 12px; } }
    </style>
    """,
    unsafe_allow_html=True,
)

left, right = st.columns([1.2, 1.0])

with left:
    st.markdown("<div class='contact-card'>", unsafe_allow_html=True)
    st.markdown("### Send a message")

    # clear_on_submit=False, and the fields are cleared by hand on success only.
    # Streamlit resets every widget in a form after ANY submit press, whatever
    # the script then decides -- so with it on, a typo'd email erased the
    # message the sender had just written, and the Supabase-down branch wiped
    # the text while telling them to paste it into an email instead.
    with st.form("contact_form", clear_on_submit=False):
        topic = st.selectbox(
            "Topic",
            # From utils.contact so the page and the store cannot drift apart.
            list(_contact_topics()),
            index=0,
        )
        email = st.text_input("Your email", placeholder="you@example.com",
                              key="contact_email", max_chars=254)
        message = st.text_area(
            "Message", key="contact_message", max_chars=4000,
            placeholder="What happened / what do you need? If this is a bug, include steps to reproduce.",
            height=160,
        )
        include_device = st.checkbox("Include device/browser details (recommended)", value=True)


        submitted = st.form_submit_button("Submit", type="primary")

    if submitted:
        # THE OLD BEHAVIOUR WROTE ONE LOG LINE AND SAID "Message received".
        # Streamlit Cloud keeps a rolling buffer, so every message ever sent
        # through this form is gone. The success line is now spoken only when a
        # row is durably stored, because the alternative is telling someone with
        # a billing problem that you have it when you do not.
        from utils import contact as _contact

        problem = _contact.validate(email, message)
        # NO HONEYPOT, NO SESSION THROTTLE. Both were removed after review.
        #
        # The honeypot was worse than useless: label_visibility="collapsed"
        # hides the LABEL, not the widget, so it rendered as the only unlabelled
        # box on the page -- exactly what a confused human or a password manager
        # fills in -- and filling it made the page say "Message received" and
        # store nothing. That is the precise bug this whole change exists to
        # remove. It also defended against an attacker that cannot exist: a
        # Streamlit form is not an HTML POST, and generic form-filling bots
        # cannot reach it over the app's websocket at all.
        #
        # The session throttle was keyed on st.session_state, so it stopped a
        # human sending a legitimate follow-up ("sorry, forgot my order
        # number") and cost a script nothing, since reconnecting is a fresh
        # session. The real limit lives in utils.contact, server-side.
        if problem:
            st.error(problem)
        else:
            _uid = None
            try:
                from utils.auth import get_user
                _u = get_user() or {}
                _uid = _u.get("id") if isinstance(_u, dict) else getattr(_u, "id", None)
            except Exception:
                # Not logged in is the ordinary case on this page.
                pass
            _ua = None
            if include_device:
                try:
                    _ua = st.context.headers.get("User-Agent")
                except Exception:
                    _ua = None

            ok, why = _contact.submit(topic, email, message,
                                      user_agent=_ua, user_id=_uid)
            if ok:
                # Only now. The form deliberately does not clear itself.
                for _k in ("contact_email", "contact_message"):
                    st.session_state.pop(_k, None)
                st.success("Message received — we typically reply within "
                           "1–2 business days.")
            elif why:
                st.warning(why)
            else:
                # NO EMAIL FALLBACK. The address it named, support@stocksentinel.ai,
                # was a hardcoded literal appearing nowhere else in the repo, on a
                # domain this project does not use -- so in the one moment a
                # message failed to reach us, the page invited the sender to post
                # their billing details to a stranger's mailbox.
                #
                # "Try again" is a real instruction now: the form no longer clears
                # itself on failure, so their text is still in the box.
                st.error(
                    "Sorry — we could not save your message just now. "
                    "Your text is still here — please press Submit again in a "
                    "moment.")

    st.markdown("</div>", unsafe_allow_html=True)

with right:
    st.markdown("<div class='contact-card'>", unsafe_allow_html=True)
    st.markdown("### What to include")
    st.markdown(
        """
- Ticker + sector (if relevant)
- What you expected vs what happened
- Screenshot (best)
- Approx time it occurred

**Response time:** typically within 1–2 business days.

**Reminder:** Stock Sentinel is informational and not financial advice.
        """
    )
    st.markdown("</div>", unsafe_allow_html=True)

close_page()
