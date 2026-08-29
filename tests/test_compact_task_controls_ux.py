"""Source contracts for the compact authenticated task controls.

These checks protect the alignment and responsive rules that are difficult to
exercise reliably through Streamlit's generated DOM in unit tests.
"""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_market_scan_uses_one_compact_scoped_control_row():
    discovery = _read("pages/Discovery.py")
    adapter = _read("assets/styles/stock-sentinel-streamlit-adapter.css")

    assert 'key="discovery_command_shell"' in discovery
    assert '<h1>Market Scan</h1>' in discovery
    assert 'key="discovery_control_row"' in discovery
    assert ".st-key-discovery_scan_card" in adapter
    assert ".st-key-discovery_command_shell" in adapter
    assert "max-width: 1100px" in adapter
    assert "align-items: flex-end !important" in adapter
    assert "height: 44px !important" in adapter
    assert "height: 48px !important" in adapter
    assert "<div style='height:1.68rem'>" not in discovery
    assert "disabled=_credits <= 0" in discovery
    assert 'title="No scan run yet"' in discovery
    assert "Bullish, Bearish, or Neutral results appear below" in discovery


def test_deep_analyze_uses_one_compact_scoped_control_row():
    deep = _read("pages/Deep_Analysis.py")
    adapter = _read("assets/styles/stock-sentinel-streamlit-adapter.css")

    assert 'key="deep_command_shell"' in deep
    assert '<h1>Deep Analyze</h1>' in deep
    assert 'key="deep_control_row"' in deep
    assert ".st-key-da_scan_card" in adapter
    assert ".st-key-deep_command_shell" in adapter
    assert "max-width: 1100px" in adapter
    assert "align-items: flex-end !important" in adapter
    assert "height: 44px !important" in adapter
    assert "height: 48px !important" in adapter
    assert '[data-testid="stTextInput"] input::placeholder' in adapter
    assert "<div style='height:1.68rem'>" not in deep
    assert deep.count("billing.render_credit_meter(profile=_profile, key=\"deep\")") == 1
    assert "disabled=_credits <= 0" in deep
    assert 'title="No analysis run yet"' in deep
    assert "Your recommendation will appear below" in deep


def test_task_controls_contain_columns_and_stack_at_mobile_widths():
    adapter = _read("assets/styles/stock-sentinel-streamlit-adapter.css")

    assert "@media (max-width: 1099px)" in adapter
    assert "@media (max-width: 900px)" in adapter
    assert "@media (max-width: 720px)" in adapter
    assert "min-width: 0 !important" in adapter
    assert "max-width: 100% !important" in adapter
    assert "flex: 1 1 100% !important" in adapter


def test_task_credit_links_route_to_account_without_starting_checkout():
    billing = _read("utils/billing.py")
    meter = billing[billing.index("def render_credit_meter"):billing.index("_BUYABLE_REASONS")]

    assert meter.count('st.page_link(') == 2
    assert meter.count('"pages/Account.py"') == 2
    assert "render_buy_credits(" not in meter
    assert 'label=f"Buy {PACK_CREDITS} credits · {PACK_PRICE}"' in meter
    assert "color:#94a3b8" in meter

    adapter = _read("assets/styles/stock-sentinel-streamlit-adapter.css")
    assert ":has(.ss-credit-meter-status--empty)" in adapter
    assert "flex-direction: column !important" in adapter


def test_every_paid_task_action_disables_at_zero_credits():
    discovery = _read("pages/Discovery.py")
    deep = _read("pages/Deep_Analysis.py")

    assert discovery.count("disabled=_credits <= 0") == 2
    assert deep.count("disabled=_credits <= 0") == 1


def test_account_purchase_and_logout_controls_are_contained():
    account = _read("pages/Account.py")
    adapter = _read("assets/styles/stock-sentinel-streamlit-adapter.css")

    assert '[data-testid="stVerticalBlockBorderWrapper"] > div' not in account
    assert ".st-key-account_purchase .stButton > button" in adapter
    assert '.st-key-account_purchase [data-testid="stLinkButton"] > a' in adapter
    assert "max-width: 100% !important" in adapter
    assert ".st-key-account_header_logout" in adapter
    assert "min-height: 44px !important" in adapter
    assert 'key="account_header_logout"' in account
    assert 'key="account_session"' not in account


def test_compact_hint_is_semantic_and_not_a_second_large_card():
    ui = _read("utils/ui.py")
    components = _read("assets/styles/stock-sentinel-components.css")
    helper = ui[
        ui.index("def render_compact_task_hint"):
        ui.index("def render_footer")
    ]

    assert '<section class="ss-task-hint"' in helper
    assert "aria-labelledby" in helper
    assert "max-width: 780px" in components
    assert "min-height: 56px" in components
    assert "background: transparent" in components
    assert "grid-template-columns" not in helper


def test_desktop_command_shell_is_compact_without_changing_touch_targets():
    adapter = _read("assets/styles/stock-sentinel-streamlit-adapter.css")
    components = _read("assets/styles/stock-sentinel-components.css")
    navigation = _read("utils/navigation.py")

    assert (
        "@media (min-width: 1100px) and (hover: hover) and (pointer: fine)"
        in adapter
    )
    assert "padding: 1.15rem 1.35rem !important" in adapter
    assert "flex: 0 0 220px !important" in adapter
    assert "min-height: var(--ss-control-min-height)" in adapter
    assert ".ss-task-command-intro" in components
    assert "font-size: clamp(2.25rem, 3vw, 2.5rem)" in components
    assert "@media (min-width:1100px) and (hover:hover) and (pointer:fine)" in navigation
    assert "min-height:44px" in navigation


def main() -> int:
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    failed: list[tuple[str, str]] = []
    print("=" * 72)
    print("  Compact task controls and alignment")
    print("=" * 72)
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed.append((test.__name__, str(exc) or type(exc).__name__))
            print(f"  FAIL  {test.__name__}: {failed[-1][1]}")
        else:
            print(f"  PASS  {test.__name__}")
    print("\n" + "=" * 72)
    print(f"  {len(tests) - len(failed)} passed, {len(failed)} failed")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
