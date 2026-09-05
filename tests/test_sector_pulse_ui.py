#!/usr/bin/env python3
"""Pulse presentation and account handoffs; all clients are offline doubles."""
import ast
from contextlib import nullcontext
from pathlib import Path
import sys
import time
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import ui, scan_intent, auth


def row(sector="healthcare", state="event", day="2026-09-03", **fields):
    return dict(sector=sector, state=state, trade_date=day,
                **dict(dict(breadth=.08, acc_days_5d=4, ud_ratio_5d=None,
                            n_eligible=380, calendar_flag=None,
                            top_contrib=[dict(ticker="MRNA", share_of_rise=.5925, rel_vol=8.97)]), **fields))


class FakeUI:
    def __init__(self):
        self.session_state = {}
        self.query_params = {}
        self.buttons = []
        self.text = []
        self.clicked = None
        self.destination = None
    def container(self, **kwargs): return nullcontext()
    def columns(self, spec, **kwargs): return [nullcontext() for _ in (range(spec) if isinstance(spec, int) else spec)]
    def expander(self, label, **kwargs): return nullcontext()
    def html(self, text): self.text.append(text)
    def caption(self, text): self.text.append(text)
    def button(self, label, **kwargs):
        self.buttons.append((label, kwargs))
        # Navigation should run in the script, not a swallowed callback rerun.
        return self.clicked is not None and kwargs.get("key") == self.clicked
    def switch_page(self, path): self.destination = path
    def page_link(self, *args, **kwargs): pass
    def selectbox(self, label, *, options, key): return self.session_state.get(key, options[0])


def page_statements(page, first_assignment, last_assignment):
    """Execute the real page's input/trigger logic without its paid pipeline."""
    path = Path(__file__).resolve().parents[1] / "pages" / page
    body = ast.parse(path.read_text()).body
    def assigns(node, name):
        return isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    start = next(i for i, n in enumerate(body) if assigns(n, first_assignment))
    end = next(i for i, n in enumerate(body) if assigns(n, last_assignment))
    return compile(ast.Module(body=body[start:end+1], type_ignores=[]), str(path), "exec")


class PulseTests(unittest.TestCase):
    def setUp(self):
        self.st = FakeUI()
        for module in (ui, scan_intent, auth):
            p = patch.object(module, "st", self.st); p.start(); self.addCleanup(p.stop)

    def test_orders_state_then_participation_not_return(self):
        snapshot = ui.pulse_snapshot([
            row("tech", "quiet", breadth=.9, eq_return_5d=.5),
            row("healthcare", "event"), row("energy", "accumulating", breadth=.26),
            row("finance", "accumulating", breadth=.23), row("materials", "distributing", breadth=.99)])
        self.assertEqual([r["sector"] for r in snapshot["rows"]], ["energy", "finance", "healthcare", "tech", "materials"])

    def test_current_date_never_backfilled_with_old_sector(self):
        data = ui.pulse_snapshot([row("tech", day="2026-09-03", breadth=.2),
                                  row("tech", day="2026-08-31", breadth=.15),
                                  row("energy", day="2026-09-02")])
        self.assertEqual(len(data["rows"]), 1)
        self.assertIn("energy", data["missing"])
        self.assertEqual(data["rows"][0]["previous_date"], "2026-08-31")
        self.assertAlmostEqual(data["rows"][0]["change_pp"], 5)

    def test_empty_invalid_and_null_never_become_zero(self):
        for values in ([], None, [dict(sector="bad", trade_date="bad")]):
            self.assertFalse(ui.pulse_snapshot(values)["rows"])
        for value in (None, float("nan"), float("inf"), -1, 2, "0.2", True):
            data = ui.pulse_snapshot([row(breadth=value)])
            self.assertIsNone(data["rows"][0]["breadth"])
        self.assertIn("—", ui.pulse_explanation(row(state="distributing")))
        self.assertNotIn("0.00", ui.pulse_explanation(row(state="distributing")))

    def test_explanation_changes_with_new_nightly_fields(self):
        self.assertEqual(ui.pulse_explanation(row()), "MRNA drove 59% of the positive volume increase.")
        changed = row(top_contrib=[dict(ticker="CYTK", share_of_rise=.71, rel_vol=4)])
        self.assertEqual(ui.pulse_explanation(changed), "CYTK drove 71% of the positive volume increase.")
        self.assertEqual(ui.pulse_explanation(row(state="accumulating", acc_days_5d=3)), "3 of 5 sessions up on heavier volume")
        # Formatting respects the returned state even if a metric looks unusual.
        self.assertIn("companies", ui.pulse_explanation(row(state="quiet", breadth=.8)))

    def test_calendar_and_event_warning_visible_with_details_closed(self):
        output = ui.pulse_evidence_html(row(calendar_flag="opex"))
        self.assertIn("One company, not a sector move.", output.split("<details>")[0])
        self.assertIn("Options-expiration", output.split("<details>")[0])
        self.assertIn("380 eligible companies", output)
        self.assertIn("8.97×", output)
        malformed = row(top_contrib=[dict(ticker='<img>', share_of_rise=.6)])
        self.assertIsNone(ui.pulse_contributor(malformed))
        self.assertNotIn('<img>', ui.pulse_evidence_html(malformed))

    def test_dynamic_event_keeps_sector_and_routes_actual_ticker(self):
        data = ui.pulse_snapshot([row(top_contrib=[dict(ticker="CYTK", share_of_rise=.71)])])
        self.st.clicked = "pulse_home_healthcare_deep"
        ui.render_sector_pulse(data, surface="home")
        self.assertIn("Healthcare", ''.join(self.st.text))
        self.assertIn("Explore CYTK →", [b[0] for b in self.st.buttons])
        self.assertEqual(self.st.destination, "pages/Auth.py")
        self.assertEqual(scan_intent.public_research_intent(), dict(kind="deep", value="CYTK"))
        self.assertFalse(any("on_click" in opts for _, opts in self.st.buttons))

    def test_scan_callback_captures_sector_not_table_index(self):
        ui.render_sector_pulse(ui.pulse_snapshot([row("energy", "accumulating")]), surface="discovery", on_scan=scan_intent.queue_pulse_scan, credits=2)
        _, opts = self.st.buttons[0]
        self.assertEqual(opts["args"], ("energy",))
        self.st.session_state[auth.USER_KEY] = dict(id="a")
        opts["on_click"](*opts["args"])
        self.assertEqual(scan_intent.take_pulse_scan(), "energy")
        self.assertIsNone(scan_intent.take_pulse_scan())

    def test_zero_credits_disable_every_paid_pulse_scan(self):
        ui.render_sector_pulse(ui.pulse_snapshot([row(), row("energy", "accumulating")]), surface="discovery", on_scan=scan_intent.queue_pulse_scan, credits=0)
        paid = [opts for _, opts in self.st.buttons if "on_click" in opts]
        self.assertEqual(len(paid), 2)
        self.assertTrue(all(opts["disabled"] for opts in paid))

    def test_public_choice_survives_auth_but_never_autoruns(self):
        self.st.session_state.update(_autorun_deep_analysis=True, _autostart_discovery_scan=True)
        self.st.query_params.update(autostart="1", ticker="OLD", sector="tech")
        scan_intent.open_research("scan", "energy")
        self.assertEqual(self.st.destination, "pages/Auth.py")
        auth._establish_authenticated_state({}, dict(id="new-user"))
        self.assertEqual(scan_intent.take_research_intent("scan"), "energy")
        self.assertIsNone(scan_intent.take_research_intent("scan"))
        self.assertNotIn("_autorun_deep_analysis", self.st.session_state)
        self.assertNotIn("_autostart_discovery_scan", self.st.session_state)
        self.assertNotIn("autostart", self.st.query_params)

    def test_different_account_cannot_replay_paid_or_public_choice(self):
        self.st.session_state[auth.USER_KEY] = dict(id="a")
        scan_intent.open_research("scan", "energy")
        scan_intent.queue_pulse_scan("energy")
        self.st.session_state[auth.USER_KEY] = dict(id="b")
        self.assertIsNone(scan_intent.public_research_intent())
        self.assertIsNone(scan_intent.take_pulse_scan())
        self.assertIn("_pulse_scan_request", auth.USER_SCOPED_SESSION_KEYS)

    def test_expired_invalid_or_future_public_choice_is_discarded(self):
        for age in (1801, -1, float("nan")):
            self.st.session_state['_public_research_intent'] = dict(kind="scan", value="energy", created_at=time.time()-age, owner="")
            self.assertIsNone(scan_intent.public_research_intent())
        scan_intent.open_research("deep", "<script>")
        self.assertIsNone(scan_intent.public_research_intent())

    def test_loader_uses_only_latest_and_refreshes_after_cache_invalidation(self):
        from utils import sector_pulse
        ui.load_sector_pulse.clear()
        with patch.object(sector_pulse, "latest", return_value=[row()]) as latest:
            self.assertEqual(ui.load_sector_pulse()["date"], "2026-09-03")
            latest.assert_called_once_with(days=6)
        ui.load_sector_pulse.clear()
        with patch.object(sector_pulse, "latest", return_value=[row(day="2026-09-04")]):
            self.assertEqual(ui.load_sector_pulse()["date"], "2026-09-04")
        ui.load_sector_pulse.clear()

    def test_deep_page_replaces_previous_input_without_auto_execution(self):
        self.st.session_state[auth.USER_KEY] = dict(id="a")
        self.st.session_state.update(da_ticker_input="OLD", prefill_deep_ticker="OLD")
        scan_intent.open_research("deep", "MRNA")
        namespace = dict(st=self.st, re=re, get_query_params=scan_intent.get_query_params,
                         patch_query_params=scan_intent.patch_query_params,
                         take_research_intent=scan_intent.take_research_intent)
        exec(page_statements("Deep_Analysis.py", "_qp", "_autorun"), namespace)
        self.assertEqual(self.st.session_state["da_ticker_input"], "MRNA")
        self.assertFalse(namespace["_autorun"])
        # A later rerun must retain a manual edit rather than replay the prefill.
        self.st.session_state["da_ticker_input"] = "CYTK"
        exec(page_statements("Deep_Analysis.py", "_qp", "_autorun"), namespace)
        self.assertEqual(self.st.session_state["da_ticker_input"], "CYTK")

    def test_discovery_consumes_clicked_sector_even_when_data_reorders(self):
        self.st.session_state[auth.USER_KEY] = dict(id="a")
        scan_intent.queue_pulse_scan("energy")
        namespace = dict(st=self.st, _profile={"credits":2}, _autostart_scan=False,
                         billing=SimpleNamespace(render_credit_meter=lambda **k: None),
                         take_research_intent=scan_intent.take_research_intent,
                         take_pulse_scan=scan_intent.take_pulse_scan,
                         queue_pulse_scan=scan_intent.queue_pulse_scan,
                         patch_query_params=scan_intent.patch_query_params,
                         PULSE_SECTORS=ui.PULSE_SECTORS, render_sector_pulse=ui.render_sector_pulse,
                         load_sector_pulse=lambda:ui.pulse_snapshot([row("utilities", "quiet")]))
        logic = page_statements("Discovery.py", "_research_sector", "scan_triggered")
        exec(logic, namespace)
        self.assertEqual(namespace["sector"], "energy")
        self.assertTrue(namespace["scan_triggered"])
        exec(logic, namespace)
        self.assertFalse(namespace["scan_triggered"])

    def test_auth_routes_public_selection_to_its_protected_destination(self):
        path = Path(__file__).resolve().parents[1] / "pages" / "Auth.py"
        function = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "_switch_to_next_page")
        namespace = {"st":self.st}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        scan_intent.open_research("deep", "MRNA")
        auth._establish_authenticated_state({}, dict(id="new-user"))
        namespace["_switch_to_next_page"]()
        self.assertEqual(self.st.destination, "pages/Deep_Analysis.py")



if __name__ == "__main__":
    unittest.main()
