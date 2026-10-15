"""Offline browser fixture. Launched only by scan_feedback.cjs."""
import sys,time,json,os
from pathlib import Path
repo=Path(os.environ['SCAN_FEEDBACK_REPO'])
sys.path.insert(0,str(repo))
sys.path.insert(0,str(repo/'tests'))
import streamlit as st
from utils import auth
from sector_pulse_feedback_harness import offline_discovery
root=Path(os.environ['SCAN_FEEDBACK_CONTROL'])
case=json.loads((root/'case.json').read_text()) if (root/'case.json').exists() else {}
st.session_state[auth.USER_KEY]={'id':'offline-browser'}
st.session_state[auth.PRODUCT_STATE_OWNER_KEY]='offline-browser'
if case.get('intent') and not st.session_state.get('fixture_intent_set'):
    # Same account-bound payload produced by Home's nonpaying navigation.
    st.session_state['_public_research_intent'] = dict(kind='scan', value=case['intent'],
        owner='offline-browser', created_at=time.time())
    st.session_state['fixture_intent_set'] = True
if case.get('delivered'):
    import pandas as pd
    st.session_state.df_valid = pd.DataFrame(case['rows'])
    st.session_state.selected_ticker = 'OGN'
    st.session_state.selected_sector = 'healthcare'
    st.session_state.deep_analysis_results = {}
    st.session_state.deep_analysis_card = {
        'ticker': 'OGN', 'verdict': 'Watch', 'confidence': 'Moderate',
        'rationale': ['Offline review example: evidence does not support a directional call.'],
        'evidence': {'mentions': 4, 'price_points': 25},
        'would_change': ['Additional independent evidence would change this assessment.'],
    }
def observe(phase):
    with (root/'events.jsonl').open('a') as events:
        events.write(json.dumps({'phase':phase})+'\n')
    if phase=='debit':
        (root/'started').write_text('debit')
        deadline=time.monotonic()+30
        while not (root/'release').exists() and time.monotonic()<deadline:
            time.sleep(.05)
with offline_discovery(case,observe,native_links=True):
    page=repo/'pages/Discovery.py'
    exec(compile(page.read_text(),str(page),'exec'),{'__file__':str(page),'__name__':'__main__'})
