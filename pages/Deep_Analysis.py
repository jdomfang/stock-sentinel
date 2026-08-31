import logging
import re
import streamlit as st

# PROJECT ROOT ON sys.path, BEFORE THE FIRST `utils` IMPORT.
#
# Streamlit Cloud can resolve `utils` to an installed site-packages module
# instead of this repo's package. The symptom is not a clean ImportError -- it
# is `KeyError: 'utils'` raised from deep inside the import machinery
# (_find_and_load_unlocked), because the parent package disappears from
# sys.modules midway through loading a submodule.
#
# pages/Discovery.py has carried this guard for exactly that reason; every other
# entrypoint was left exposed, so whichever page a user happened to land on
# first decided whether the app worked. Home is the landing page, so it is the
# one that fails.
from pathlib import Path as _Path
import sys as _sys
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))


from utils.obs import install as _install_logging, new_request_id, set_request_id as _set_request_id

_install_logging()
_da_logger = logging.getLogger(__name__)

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import (
    close_page,
    processing_state_html,
    render_compact_task_hint,
    render_delivered_analysis_result,
    render_system_state,
)
# NOT the pipeline. This page charges a credit, draws a progress bar and
# renders a card; the analysis itself lives in core-api and is reached over
# HTTPS. utils.analyze is deliberately absent from these imports -- the day it
# comes back is the day there are two implementations again.
from utils import analyze_client as _client
from utils import billing
from utils.demo_snapshots import snapshot_timestamp

# Page configuration
st.set_page_config(
    page_title="Deep Analysis - Stock Sentinel",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Apply the shared theme before the authenticated workspace shell.
from utils.ui import apply_theme
from utils.auth import ensure_user_scoped_state_owner, flush_pending_rt_save
apply_theme()
render_sidebar_navigation()
flush_pending_rt_save()
from utils.guard import require_active_account, require_login
require_login(after_auth_page="Deep_Analysis")
render_top_nav(active="deep_analyze")
_profile = require_active_account(after_auth_page="Deep_Analysis")

st.markdown(
    """
    <div class="clawd-app-wrapper">
    """,
    unsafe_allow_html=True,
)

from utils.credits import consume_credit, refund_credit, complete_work
from utils.scan_intent import get_query_params


def _bail() -> None:
    """Stop the script WITHOUT leaving the page half-drawn.

    st.stop() raises StopException, which unwinds past the close_page() at the
    bottom of this module -- so the <div class="clawd-app-wrapper"> opened in
    the hero above stays unclosed and the footer never renders. That was rare
    when the only early exit was "out of credits"; the cutover made bailing a
    routine outcome (core-api unconfigured, unreachable, or answering with no
    card), so the chrome has to survive it.
    """
    close_page()
    st.stop()

# If we arrived via Home → Auth redirect, the ticker may be in query params.
_qp = get_query_params()
_qp_ticker = (_qp.get("ticker") or "").strip().upper()
# From a URL, so it bypasses the text input's max_chars and reaches
# several unsafe_allow_html interpolations. Restrict at the source.
if not re.fullmatch(r"[A-Z0-9.\-]{1,6}", _qp_ticker or ""):
    _qp_ticker = ""
if _qp_ticker and not st.session_state.get("prefill_deep_ticker"):
    st.session_state["prefill_deep_ticker"] = _qp_ticker

_prefill = (st.session_state.pop("prefill_deep_ticker", None) or "").strip().upper()
_autorun = bool(st.session_state.pop("_autorun_deep_analysis", False))

# One task command: desktop uses the approved split panel; narrower viewports
# retain the established stacked introduction and compact toolbar.
_credits = int((_profile or {}).get("credits") or 0)
with st.container(key="deep_command_shell"):
    intro_col, task_col = st.columns([0.72, 1.28], gap="large")
    with intro_col:
        st.html(
            """
            <header class="ss-task-command-intro">
              <h1>Deep Analyze</h1>
              <p>Get a Buy, Watch, or Avoid recommendation for one ticker.</p>
            </header>
            """
        )

    with task_col:
        with st.container(key="da_scan_card"):
            with st.container(key="deep_control_row"):
                ticker_col, btn_col, meter_col = st.columns([1.45, 1.0, 1.1])
                with ticker_col:
                    ticker = st.text_input(
                        "Ticker",
                        value=_prefill,
                        placeholder="e.g. TSLA",
                        key="da_ticker_input",
                        label_visibility="visible",
                        max_chars=6,
                    )
                with btn_col:
                    _run_clicked = st.button(
                        "Analyze · 1 credit", type="primary",
                        use_container_width=True,
                        disabled=_credits <= 0,
                    )

                with meter_col:
                    billing.render_credit_meter(profile=_profile, key="deep")

# Auto-sector: Deep analysis can run without sector input. Default to unknown.
sector = "unknown"

if not (_run_clicked or (_autorun and _prefill)):
    render_compact_task_hint(
        title="No analysis run yet",
        message="Your recommendation will appear below.",
    )

# Main analysis button — or auto-triggered from Home
if _run_clicked or (_autorun and _prefill):
    # Validate BEFORE charging. This previously debited a credit and only then
    # checked whether a ticker had been entered, so submitting an empty field
    # cost the user a credit and delivered nothing.
    if not (ticker or _prefill).strip():
        st.error("Please enter a stock ticker.")
    else:
        _run_ticker = (ticker or _prefill).strip().upper()

        # Open the request scope before the charge so debit, work and refund
        # share one id in the logs and in the usage_events row.
        _rid = new_request_id()
        _da_logger.info("deep_analyze requested ticker=%s", _run_ticker)

        # NOTHING TO CALL, SO NOTHING TO CHARGE. The in-process pipeline is
        # gone from this page: Deep Analyze is core-api or it is nothing. That
        # check belongs BEFORE the debit -- a misconfiguration must not cost a
        # credit and then be refunded, it must never take one.
        if not _client.configured():
            _da_logger.error("deep_analyze unavailable: core-api not configured")
            render_system_state(
                kind="error",
                title="Deep Analyze is temporarily unavailable",
                message="The analysis service is not available right now.",
                meta="No credit has been used.",
            )
            _bail()

        _credit = consume_credit("deep_analyze", {"ticker": _run_ticker, "page": "deep_analysis"})
        if not _credit.ok:
            # THE MODAL, not a bare error. This was a dead end: st.error() and
            # nothing to click, on the page whose whole purpose is the thing
            # the user just ran out of.
            _da_logger.info("deep_analyze refused reason=%s", _credit.reason)
            billing.render_credit_refusal(
                _credit, "A deep analysis costs 1 credit.", key="page")
            _bail()
        # Everything from here is charged work. It must be inside try/finally,
        # not try/except: Streamlit aborts a running script with StopException /
        # RerunException, both of which derive from BaseException, so an
        # `except Exception` never sees them. The abort fires whenever the user
        # clicks again, edits the ticker, or navigates away mid-analysis -- the
        # most common way this page fails -- and previously left the credit spent
        # with nothing rendered and no refund.
        _delivered = False
        try:
            # Multi-step progress display so user knows work is happening
            _da_progress = st.progress(0)
            _da_status = st.empty()
            _da_status.markdown(
                processing_state_html(
                    f"Gathering market discussion for {_run_ticker}…"
                ),
                unsafe_allow_html=True,
            )
            _da_progress.progress(12)

            import threading, time as _time

            _result_holder: dict = {}
            _done_flag = threading.Event()

            def _run():
                # A new thread starts with a FRESH context, so the ContextVar holding
                # the request id reverts to its default here. Without this line every
                # log record produced by the actual analysis -- the majority of them --
                # would be stamped "-" and the id would correlate nothing.
                _set_request_id(_rid)
                try:
                    # ONE PATH. The in-process branch that used to sit here was
                    # scaffolding for the cutover, and it was hiding the thing
                    # it was meant to de-risk: a fallback that fires silently
                    # makes "did this use the container?" unanswerable, and
                    # every misconfiguration looks like success. Removing it
                    # means a broken core-api is loud and immediate.
                    _r = _client.analyze_remote(
                        _run_ticker, sector, feature="deep_analyze",
                        event_id=getattr(_credit, "event_id", None))
                    if _r.ok:
                        _da_logger.info(
                            "deep_analyze served by CORE-API in %.1fs "
                            "(degraded=%s) ticker=%s",
                            _r.elapsed_s or -1, _r.degraded, _run_ticker)
                        _result_holder["card"] = _r.card
                        _result_holder["analysis_results"] = _r.analysis_results
                        _result_holder["metadata"] = {
                            "degraded": _r.degraded,
                            "status": _r.status,
                            "posts_billed": _r.posts_billed,
                            "elapsed_s": _r.elapsed_s,
                            "route": "deep_analyze",
                        }
                    else:
                        _result_holder["error"] = _r.error
                        if _r.posts_billed:
                            # The one fact a refund conversation turns on. The
                            # service returns it for that reason and nothing
                            # was reading it.
                            _da_logger.warning(
                                "core-api spent %s X posts on a failed "
                                "deep_analyze ticker=%s", _r.posts_billed,
                                _run_ticker)
                        # Kept, though nothing falls back on it any more: it
                        # still says whether the service could have spent, and
                        # that decides whether "try again" is honest advice or
                        # an invitation to buy the same corpus twice.
                        _result_holder["pre_spend"] = _r.retryable
                except Exception as _e:
                    _da_logger.exception("deep_analyze failed ticker=%s", _run_ticker)
                    _result_holder["error"] = str(_e)
                finally:
                    _done_flag.set()

            _t = threading.Thread(target=_run, daemon=True)
            _t.start()

            _steps = [
                (20, "Reading what traders are saying…"),
                (35, "Weighing bullish and bearish signals…"),
                (50, "Cross-referencing sentiment over time…"),
                (65, "Running price projection models…"),
                (78, "Measuring signal strength…"),
                (88, "Building your recommendation…"),
            ]
            _step_idx = 0
            _start = _time.time()
            # DELIBERATELY only six st.* calls, then silence. Streamlit
            # notices an abort -- another click, an edited ticker, navigating
            # away -- only at an st.* call, so ticking every 1.5s here would
            # make the whole tail abortable. That sounds like an improvement
            # and is not: the X posts are already billed, X's index is 7 days
            # deep, and an abort between the last tick and delivery
            # destroys the only record the analysis ever happened. Widening
            # the abort window trades unrecoverable rows for a faster cancel.
            # The right fix is to write the row off the main thread; until
            # then this stays as it is.
            while not _done_flag.wait(timeout=1.5):
                if _step_idx < len(_steps):
                    prog, msg = _steps[_step_idx]
                    _da_progress.progress(prog)
                    _da_status.markdown(
                        processing_state_html(msg),
                        unsafe_allow_html=True,
                    )
                    _step_idx += 1

            _da_progress.progress(100)
            _da_status.empty()
            _da_progress.empty()

            def _fail_panel(headline: str, refunded: bool,
                            retry_ok: bool = True) -> None:
                """ONE panel for every failure. The same paid outcome used to
                render as a red box on one path and a blank page on the other
                -- two products for one failure."""
                # TWO INDEPENDENT FACTS: whether the credit came back and
                # whether retrying is safe. Keep both explicit in one shared,
                # platform-neutral state component.
                _credit_message = (
                    "Your credit was not used."
                    if refunded else
                    "If your credit was not returned, it will be released "
                    "automatically within 15 minutes."
                )
                # Do not invite a second debit while the first credit still
                # appears spent. Retry guidance is safe only when the refund
                # landed and the service reports that work had not begun.
                _retry_message = (
                    "Try again in a moment." if refunded and retry_ok else ""
                )
                render_system_state(
                    kind="error",
                    title=headline,
                    message=_credit_message,
                    meta=_retry_message,
                )

            if "error" in _result_holder:
                # The credit was taken before the work began. The work failed, so
                # give it back rather than charging for an upstream outage.
                _refunded = refund_credit("deep_analyze", _credit.event_id,
                                          f"analysis failed: {_result_holder['error'][:120]}")
                # "Try again" is only honest when the service provably did not
                # spend. Otherwise a retry buys the same corpus a second time,
                # so the wording stops short of inviting one.
                _fail_panel("Analysis failed", _refunded,
                            retry_ok=bool(_result_holder.get("pre_spend")))
                _bail()

            _card = _result_holder.get("card") or {}
            # OPTIONAL, and deliberately so. analysis_results feeds the "Full
            # breakdown" expander and nothing else. A core-api one deploy
            # behind returns a perfectly good card without it; refusing to
            # deliver then would refund a user for an analysis the service had
            # already run, billed and recorded, and show them a blank page.
            analysis_results = _result_holder.get("analysis_results") or {}

            # THE CARD IS THE PRODUCT. Gating on it -- rather than on the
            # breakdown, or on card()'s error stub -- is the same condition
            # persist() uses to decide a row is owed, so the page cannot refuse
            # to render something the database was told to record.
            if not _card:
                _da_logger.error("no card produced for %s", _run_ticker)
                _refunded = refund_credit("deep_analyze", _credit.event_id,
                                          "no summary could be produced")
                # NOT retryable. This branch is now reachable only when
                # core-api answered ok:true with an unusable card -- by which
                # point it has bought the corpus and written both rows under
                # this event_id. It was retry-safe when the in-process path
                # could produce it; it is not any more.
                _fail_panel("No analysis could be produced", _refunded,
                            retry_ok=False)
                _bail()

            # Preserve the canonical result before rendering. If Streamlit
            # notices navigation after the paid summary is visible, the next
            # run can still restore that delivered product rather than losing
            # it between the summary and an optional supplement.
            ensure_user_scoped_state_owner()
            st.session_state.selected_ticker = _run_ticker
            st.session_state.analysis_sector = sector
            st.session_state.deep_analysis_card = _card
            st.session_state.deep_analysis_results = analysis_results
            st.session_state.deep_analysis_completed_at = snapshot_timestamp()
            st.session_state.deep_analysis_metadata = (
                _result_holder.get("metadata") or {}
            )
            st.session_state["analysis_result_origin"] = "deep_analyze"

            def _mark_summary_delivered() -> None:
                # The helper invokes this immediately after the canonical paid
                # recommendation renders and before any optional Streamlit UI.
                global _delivered
                _delivered = True

            # Both entry paths render one complete product surface from this
            # canonical card. Route context may differ; paid-result hierarchy,
            # metrics, evidence, movement profile and disclosure may not.
            render_delivered_analysis_result(
                card=_card,
                analysis_results=analysis_results,
                ticker=_run_ticker,
                sector=sector,
                key_suffix=f"_deep_{_run_ticker}",
                element_id="da-results-anchor",
                freshness="Analysis generated now",
                on_summary_delivered=_mark_summary_delivered,
            )

            # Written AFTER delivery, and unable to affect it. This is the only
            # record that this call was ever made: X's index is 7 days deep and
            # cannot be backfilled, so a verdict not written now can never be
            # scored against what the stock actually did.
            #
            # NO WRITE HERE, and there must never be one again. core-api
            # persisted both rows before it answered -- it was handed
            # feature="deep_analyze" and this credit's event_id precisely so
            # the row is indistinguishable from one this page used to write.
            # A write here would duplicate it: signal_log's unique
            # (event_id, ticker, feature) rejects the second, but verdict_log
            # has no such constraint and would simply gain a row.

        finally:
            # Backstop for every path the except blocks above cannot reach,
            # including the Streamlit abort and any failure in the render
            # calls, which sit outside the worker's try. Overlaps
            # safely with the explicit refunds: refund_credit is idempotent, so a
            # second attempt returns already_refunded and the more specific reason
            # recorded earlier wins. Does NOT cover an OOM kill -- SIGKILL runs no
            # finally either; that stays the orphan reaper's job.
            if _delivered:
                complete_work(_credit.event_id, "completed", f"ticker={_run_ticker}")
            else:
                # Close the run ONLY if the refund actually landed.
                # refund_credit returns False without raising when its RPC
                # fails, and reap_orphaned_work scans status='running' alone --
                # so closing the row as 'failed' after a failed refund deletes
                # the credit and switches off the one backstop designed to
                # return it. Discovery already makes this choice; the two pages
                # charge from the same ledger and must not differ here.
                if refund_credit("deep_analyze", _credit.event_id,
                                 "deep analysis did not complete"):
                    complete_work(_credit.event_id, "failed", "aborted or errored")
                else:
                    _da_logger.error(
                        "refund failed for event %s; leaving work_run open so "
                        "the reaper retries", _credit.event_id)

close_page()
