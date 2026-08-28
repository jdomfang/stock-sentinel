import html
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
    render_evidence_check,
    render_full_analysis_expander,
    render_compact_task_hint,
    render_recommendation_panel,
    render_system_state,
)
# NOT the pipeline. This page charges a credit, draws a progress bar and
# renders a card; the analysis itself lives in core-api and is reached over
# HTTPS. utils.analyze is deliberately absent from these imports -- the day it
# comes back is the day there are two implementations again.
from utils import analyze_client as _client
from utils import billing

# Page configuration
st.set_page_config(
    page_title="Deep Analysis - Stock Sentinel",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Apply the shared theme before the authenticated workspace shell.
from utils.ui import apply_theme
from utils.auth import flush_pending_rt_save
apply_theme()
render_sidebar_navigation()
flush_pending_rt_save()
from utils.guard import require_active_account
render_top_nav(active="deep_analyze")
_profile = require_active_account(after_auth_page="Deep_Analysis")

st.markdown(
    """
    <style>
    div[data-testid="stMainBlockContainer"] {
      max-width: 1100px; margin: 0 auto;
      padding-left: clamp(16px, 4vw, 28px);
      padding-right: clamp(16px, 4vw, 28px);
      padding-top: 0.25rem;
    }
    div[data-testid="stMainBlockContainer"] > div:first-child,
    div[data-testid="stVerticalBlock"] > div:first-child {
      margin-top: 0 !important; padding-top: 0 !important;
    }
    section[data-testid="stMain"] > div { padding-top: 0 !important; }

    .da-hero { margin: 0 0 1.35rem; padding: 0; max-width: 760px; }
    .da-hero-title {
      font-size: clamp(2rem, 4vw, 2.65rem); font-weight: 760;
      letter-spacing: -0.035em; line-height: 1.08; margin: 0 0 0.35rem;
    }
    .da-hero-sub {
      color: var(--muted); font-size: clamp(15px, 1.35vw, 1.05rem);
      line-height: 1.45; margin: 0 0 0.85rem 0; max-width: 680px;
    }
    @media (max-width: 640px) {
      .da-hero { margin-bottom: 1rem; }
      .da-hero-title { font-size: clamp(2rem, 9vw, 2.5rem); }
      .da-hero-sub { font-size: 1.00rem; }
    }

    .st-key-deep_full_result_link [data-testid="stExpander"] {
      border:1px solid rgba(56,189,248,.36)!important;
      border-radius:var(--radius-control)!important;
      background:rgba(8,15,30,.58)!important;
    }
    .st-key-deep_full_result_link details > summary {
      min-height:var(--ss-control-min-height);color:var(--accent)!important;
      font-size:.9rem;font-weight:720!important;
    }
    .st-key-deep_full_result_link [data-testid="stExpander"]:hover {
      background:rgba(56,189,248,.07)!important;
    }
    </style>
    <div class="clawd-app-wrapper">
    <div class="da-hero">
      <h1 class="da-hero-title">Analyze any US stock.</h1>
      <div class="da-hero-sub">Enter a ticker and get a clear Buy, Watch, or Avoid recommendation.</div>
    </div>
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

# ── Compact scan card ──
_credits = int((_profile or {}).get("credits") or 0)
with st.container(key="da_scan_card"):
    with st.container(key="deep_control_row"):
        ticker_col, btn_col, meter_col = st.columns([1.45, 1.0, 1.1])
        with ticker_col:
            ticker = st.text_input(
                "Ticker",
                value=_prefill,
                placeholder="e.g. RCAT",
                key="da_ticker_input",
                label_visibility="visible",
                max_chars=6,
            )
        with btn_col:
            _run_clicked = st.button(
                "Analyze · 1 credit", type="primary", use_container_width=True,
                disabled=_credits <= 0,
            )

        with meter_col:
            billing.render_credit_meter(profile=_profile, key="deep")

# Auto-sector: Deep analysis can run without sector input. Default to unknown.
sector = "unknown"

if not (_run_clicked or (_autorun and _prefill)):
    render_compact_task_hint(
        title="No analysis yet",
        message="Enter a ticker and run a one-credit analysis. Buy, Watch, or Avoid appears here.",
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

            # EVERYTHING BELOW READS THE CARD -- not the Analysis, not the
            # Verdict. The card is the only thing the remote path can hand back
            # and the only thing card() guarantees agrees with the decision, so
            # identical code now draws an analysis computed in this process and
            # one computed in a container. The two cannot present the same
            # verdict differently, which is the property the whole migration is
            # for.
            _evidence = _card.get("evidence") or {}
            _movement = _card.get("movement") or {}
            price_points = _evidence.get("price_points") or 0

            ai_summary = {
                # "—" rather than None: render_recommendation_panel calls
                # .lower() on the recommendation, so a card missing it raised
                # AttributeError mid-page, before _delivered.
                "recommendation": _card.get("verdict") or "—",
                "confidence": _card.get("confidence") or "—",
                # NOT `or 0.0`. card() returns None when the fallback reported
                # no score, precisely so a renderer cannot print
                # "Neutral (+0.00)" as a finding nobody made. Discovery honours
                # that; this page must not disagree about the same card.
                "avg_sentiment": _card.get("avg_sentiment"),
                # The reason only. would_change belongs to the evidence check
                # below; duplicating it here printed "more independent voices,
                # not more posts" as a REASON for the signal.
                "rationale": _card.get("rationale") or [],
            }

            current_price = projected_gain = drawdown_first = "Unavailable"
            # Selected by KEY, not by the label's wording: rewording a label
            # would otherwise delete a tile with nothing raised anywhere.
            for _tile in (_card.get("tiles") or []):
                if _tile.get("key") == "last_price":
                    current_price = _tile.get("value", "Unavailable")
                elif _tile.get("key") == "range_30d":
                    projected_gain = _tile.get("value", "Unavailable")
                elif _tile.get("key") == "drawdown_first":
                    drawdown_first = _tile.get("value", "Unavailable")

            # UNIQUE ids. Summing mention_count across the eight angles counts
            # a post once per angle it lands in -- angle 1 is the whole corpus
            # and most others are subsets -- printing 141 for a 98-post corpus
            # in which 90 were analysed, and logging that inflated figure.
            # THE NUMBER ON THE CARD MUST BE THE NUMBER THAT DECIDED THE CALL.
            # The corpus union is ~90 of 98 posts and rendered as "90 posts
            # analysed" beside a verdict resting on 5 independent voices -- the
            # one figure a reader takes as sample size, off by ~18x. Both come
            # from the card, so this page and Discovery cannot disagree about
            # which of them is being shown.
            _independent_voices = _evidence.get("independent_voices")
            _raw_mentions = _evidence.get("mentions")
            if _independent_voices is not None:
                _shown_mentions = int(_independent_voices or 0)
                _cluster_suffix = "s" if _shown_mentions != 1 else ""
                _evidence_label = (
                    f"{_shown_mentions} independent evidence "
                    f"cluster{_cluster_suffix}"
                )
            elif _raw_mentions is not None:
                _shown_mentions = int(_raw_mentions or 0)
                _post_suffix = "s" if _shown_mentions != 1 else ""
                _evidence_label = (
                    f"{_shown_mentions} post{_post_suffix} analyzed"
                )
            else:
                _shown_mentions = 0
                _evidence_label = "Evidence count unavailable"
            _horizon_days = int(_movement.get("horizon_days") or 0)
            _day_suffix = "s" if _horizon_days != 1 else ""
            _horizon_label = (
                f"{_horizon_days} trading day{_day_suffix}"
                if _horizon_days else "Short-term horizon"
            )

            # Semantic result anchor. The current adapter does not inject
            # parent-frame scrolling JavaScript; future hosts can route/focus
            # this product state using their native navigation primitives.
            import streamlit as _st
            _st.markdown('<div id="da-results-anchor"></div>', unsafe_allow_html=True)

            render_recommendation_panel(
                ticker=_run_ticker,
                sector=sector,
                ai_summary=ai_summary,
                current_price=current_price,
                projected_gain=projected_gain,
                drawdown_first=drawdown_first,
                mentions=_shown_mentions,
                price_points=price_points,
                evidence_label=_evidence_label,
                horizon=_horizon_label,
                freshness="Analysis generated now",
                would_change=_card.get("would_change") or [],
            )

            # The recommendation panel IS the product. Once it has rendered the user
            # has what they paid for, so anything that fails after this point is a
            # presentation bug, not a delivery failure.
            _delivered = True
            # Preserve the delivered view model for the nonpaying breakdown
            # route. This is presentation state only; core-api already owns the
            # analysis and persistence, and opening the route performs no work.
            st.session_state.selected_ticker = _run_ticker
            st.session_state.selected_sector = sector
            st.session_state.deep_analysis_card = _card
            st.session_state.deep_analysis_results = analysis_results
            st.session_state["analysis_result_origin"] = "deep_analyze"

            # EVIDENCE CHECK. Which gates passed, which failed, and what would
            # change the call. Generated from cascade state, so it cannot
            # contradict the verdict above it.
            if _card.get("pillars"):
                try:
                    render_evidence_check(_card, _run_ticker)
                except Exception:
                    _da_logger.warning("evidence check render failed", exc_info=True)

            # MOVEMENT PROFILE. The part of this page that speaks directly to a
            # short-term trader -- a target and a horizon -- computed from
            # realised volatility with no forecast in it. Both directions are
            # shown together and always: volatility is symmetric, so publishing
            # "+5% in 66% of paths" alone would be read as a 66% win rate.
            try:
                _mp = _movement.get("targets") or {}
                if _mp:
                    _hz = int(_movement.get("horizon_days") or 10)
                    _parts = [
                        "<tr style='color:rgba(148,163,184,.6);font-size:0.74rem;"
                        "text-transform:uppercase;letter-spacing:.05em;'>"
                        "<td style='padding:0 14px 6px 0;'>target</td>"
                        "<td style='padding:0 14px 6px;'>reached, 30d</td>"
                        "<td style='padding:0 14px 6px;'>fallen, 30d</td>"
                        f"<td style='padding:0 0 6px 14px;'>within {_hz}d</td></tr>"
                    ]
                    for _k, _v in _mp.items():
                        _ud = _v.get("up_median_day")
                        _dd = _v.get("down_median_day")
                        _up = f"{_v['up_rate']:.0%}" + (f" &middot; day {_ud}" if _ud else "")
                        _dn = f"{_v['down_rate']:.0%}" + (f" &middot; day {_dd}" if _dd else "")
                        # The same question over the window the social evidence
                        # can actually speak to. Both directions, for the same
                        # reason the 30-day columns show both.
                        _su = _v.get("up_rate_by_decision")
                        _sd = _v.get("down_rate_by_decision")
                        _short = ("&mdash;" if _su is None or _sd is None
                                  else f"{_su:.0%} up &middot; {_sd:.0%} down")
                        _parts.append(
                            f"<tr><td style='padding:6px 14px 6px 0;'>&plusmn;{_k}</td>"
                            f"<td style='padding:6px 14px;color:rgba(56,189,248,.95);'>{_up}</td>"
                            f"<td style='padding:6px 14px;color:rgba(248,113,113,.95);'>{_dn}</td>"
                            f"<td style='padding:6px 0 6px 14px;color:rgba(148,163,184,.9);'>{_short}</td></tr>"
                        )
                    # Order matters and symmetry does not hide it: whether +5%
                    # arrives BEFORE -5% is the one directional question this
                    # simulation can answer. With no drift estimate it answers
                    # 50%, and saying so plainly is the point -- it states that
                    # the price history alone gives no edge, rather than leaving
                    # the reader to infer an edge from the up column.
                    _f5d = _mp.get("5%") or {}
                    _f5 = _f5d.get("up_first_rate")
                    _tch = _f5d.get("touched_rate")
                    # Conditional on having touched either side, and the base it
                    # is conditional on is stated. The unconditional version of
                    # this number reads 23% on a calm large cap -- an apparent
                    # bearish edge printed directly under a denial of any edge.
                    _first = ("" if _f5 is None else
                              "<div style='color:rgba(148,163,184,.8);font-size:0.82rem;"
                              "margin-top:10px;'>Of the paths that reach &plusmn;5% at all"
                              + (f" ({_tch:.0%} of them)" if _tch is not None else "")
                              + f", {_f5:.0%} reach +5% <em>first</em>. Price history alone "
                              "carries no direction; only the evidence above does.</div>")
                    _tk = html.escape(str(_run_ticker))
                    st.markdown(
                        "<div style='border:1px solid rgba(148,163,184,.22);border-radius:14px;"
                        "padding:16px 20px;margin:0.75rem 0;'>"
                        "<div style='font-weight:700;margin-bottom:2px;'>Movement profile</div>"
                        "<div style='color:rgba(148,163,184,.8);font-size:0.82rem;margin-bottom:10px;'>"
                        f"How far {_tk} normally travels, from its own recent "
                        f"volatility (&plusmn;{_movement.get('band_pct') or 0:.1f}% over 30 days). Not a "
                        "forecast &mdash; the same volatility carries it both ways.</div>"
                        f"<table style='font-size:0.9rem;'>{''.join(_parts)}</table>"
                        f"{_first}</div>",
                        unsafe_allow_html=True,
                    )
            except Exception:
                _da_logger.warning("movement profile render failed", exc_info=True)

            # EVERY piece of the page is on screen before the logging below
            # runs. Both writes are synchronous urllib POSTs, and Streamlit
            # cannot interrupt a blocked urlopen -- it only notices a rerun at
            # the next st.* call. With the expander after them, a slow Supabase
            # left the recommendation panel rendered, the full analysis simply
            # missing, and the page ignoring clicks; a click in that window
            # raises RerunException (a BaseException, so no `except Exception`
            # catches it) and the user is charged for an analysis they never
            # fully saw.
            with st.container(key="deep_full_result_link"):
                render_full_analysis_expander(
                    analysis_results,
                    key_suffix=f"_deep_{_run_ticker}",
                    label="View full breakdown",
                )
                st.caption("Already analyzed · no additional credit")

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
