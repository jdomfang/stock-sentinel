"""Offline real-page fixture for deep_submit.cjs."""
import json
import os
from pathlib import Path
import sys
import time

repo = Path(os.environ['DEEP_REVIEW_REPO'])
root = Path(os.environ['DEEP_REVIEW_CONTROL'])
sys.path[:0] = [str(repo), str(repo / 'tests')]
from deep_analysis_harness import install_doubles
import streamlit as st

case = json.loads((root / 'case.json').read_text())


def observe(phase):
    with (root / 'events.jsonl').open('a') as log:
        log.write(json.dumps({'phase': phase}) + '\n')
    if phase in ('debit', 'remote'):
        deadline = time.monotonic() + 20
        while not (root / ('release-' + phase)).exists() and time.monotonic() < deadline:
            time.sleep(.05)


install_doubles(case, observe)
if not st.session_state.get('fixture_seeded'):
    st.session_state['fixture_seeded'] = True
    if case.get('prefill'):
        st.session_state['prefill_deep_ticker'] = case['prefill']
page = repo / 'pages/Deep_Analysis.py'
exec(compile(page.read_text(), str(page), 'exec'), {'__file__': str(page), '__name__': '__main__'})
