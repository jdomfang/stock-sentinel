"""Single submission and cleanup contracts for the real Deep Analyze page."""
from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from streamlit.testing.v1 import AppTest
import streamlit as st


class DeepSubmitTests(unittest.TestCase):
    def run_case(self, case, *, ticker='GME', submit=True, prefill=None, autorun=False):
        with ExitStack() as stack:
            # Doubles installed in a running script context own session state.
            if case.get('mode') == 'render_stop':
                from streamlit.runtime.scriptrunner_utils.exceptions import StopException
                stack.enter_context(patch('utils.ui.render_delivered_analysis_result', side_effect=StopException))
            stack.enter_context(patch.object(st, 'page_link', lambda *a, **k: None))
            # AppTest's script globals do not accept injected names; compile a
            # tiny bridge module for the duration of this local test only.
            import types
            bridge = types.ModuleType('_offline_deep_case')
            bridge.case, bridge.stack = case, stack
            bridge.observe = lambda phase: None
            sys.modules['_offline_deep_case'] = bridge
            stack.callback(sys.modules.pop, '_offline_deep_case', None)
            app = AppTest.from_string(
                'from _offline_deep_case import case, observe, stack\n'
                'from deep_analysis_harness import install_doubles\n'
                'install_doubles(case, observe, stack=stack)\n'
                f'exec(compile(open({str(ROOT / "pages/Deep_Analysis.py")!r}).read(), '
                f'{str(ROOT / "pages/Deep_Analysis.py")!r}, "exec"), '
                f'{{"__file__": {str(ROOT / "pages/Deep_Analysis.py")!r}, "__name__": "__main__"}})',
            )
            if prefill:
                app.session_state['prefill_deep_ticker'] = prefill
            if autorun:
                app.session_state['_autorun_deep_analysis'] = True
            app.run(timeout=15)
            if submit:
                app.text_input(key='da_ticker_input').set_value(ticker)
                next(b for b in app.button if b.label == 'Analyze · 1 credit').click()
                app.run(timeout=15)
            self.assertFalse(any(e[0] == 'blocked_network' for e in case['events']))
            self.assertNotIn('Preparing secure analysis…', '\n'.join(x.value for x in app.markdown))
            return app, None

    def test_success_one_debit_one_remote_and_completion(self):
        case = {}
        app, _ = self.run_case(case, ticker='gme')
        self.assertFalse(list(app.exception))
        self.assertEqual(case['events'], [('debit', 'deep_analyze', 'GME'), ('remote', 'GME'), ('complete', 'completed')])

    def test_blank_never_charges(self):
        case = {}
        app, _ = self.run_case(case, ticker='  ')
        self.assertFalse(case['events'])
        self.assertIn('Please enter a stock ticker.', [e.value for e in app.error])

    def test_zero_balance_is_disabled(self):
        case = {'credits': 0}
        app, _ = self.run_case(case, submit=False)
        self.assertTrue(next(b for b in app.button if b.label == 'Analyze · 1 credit').disabled)
        self.assertFalse(case['events'])

    def test_unconfigured_never_charges(self):
        case = {'mode': 'unconfigured'}
        app, _ = self.run_case(case)
        self.assertFalse(list(app.exception))
        self.assertFalse(case['events'])

    def test_refused_does_not_start_remote(self):
        case = {'mode': 'refused'}
        app, _ = self.run_case(case)
        self.assertFalse(list(app.exception))
        self.assertEqual(case['events'], [('debit', 'deep_analyze', 'GME')])

    def test_service_failures_refund_and_close(self):
        for mode in ('failure', 'empty', 'exception'):
            with self.subTest(mode=mode):
                case = {'mode': mode}
                app, _ = self.run_case(case)
                self.assertFalse(list(app.exception))
                self.assertEqual(sum(e[0] == 'debit' for e in case['events']), 1)
                self.assertEqual(sum(e[0] == 'remote' for e in case['events']), 1)
                self.assertIn(('refund',), case['events'])
                self.assertEqual(case['events'][-1], ('complete', 'failed'))

    def test_failed_refund_leaves_work_open(self):
        case = {'mode': 'refund_failure'}
        app, _ = self.run_case(case)
        self.assertFalse(list(app.exception))
        self.assertNotIn('complete', [e[0] for e in case['events']])

    def test_debit_exception_clears_surface(self):
        case = {'mode': 'debit_exception'}
        app, _ = self.run_case(case)
        self.assertEqual(len(app.exception), 1)
        self.assertEqual(case['events'], [('debit', 'deep_analyze', 'GME')])

    def test_stop_clears_surface(self):
        case = {'mode': 'stop'}
        app, _ = self.run_case(case)
        self.assertFalse(list(app.exception))
        self.assertEqual(case['events'], [('debit', 'deep_analyze', 'GME')])

    def test_abort_after_debit_refunds_before_clearing_feedback(self):
        case = {'mode': 'render_stop'}
        app, _ = self.run_case(case)
        self.assertFalse(list(app.exception))
        self.assertEqual(case['events'], [('debit', 'deep_analyze', 'GME'),
                         ('remote', 'GME'), ('refund',), ('complete', 'failed')])

    def test_prefill_does_not_autorun(self):
        case = {}
        app, _ = self.run_case(case, prefill='GME', submit=False)
        self.assertEqual(app.text_input(key='da_ticker_input').value, 'GME')
        self.assertFalse(case['events'])

    def test_existing_autorun_is_preserved(self):
        case = {}
        app, _ = self.run_case(case, prefill='GME', autorun=True, submit=False)
        self.assertFalse(list(app.exception))
        self.assertEqual(sum(e[0] == 'debit' for e in case['events']), 1)
        self.assertIn(('complete', 'completed'), case['events'])


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DeepSubmitTests)
    result = unittest.TextTestRunner().run(suite)
    failed = len({test.id() for test, _ in result.failures + result.errors})
    print(f'{result.testsRun - failed} passed, {failed} failed')
    sys.exit(not result.wasSuccessful())
