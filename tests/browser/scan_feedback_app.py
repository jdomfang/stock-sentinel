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
def observe(phase):
    if phase=='debit':
        (root/'started').write_text('debit')
        deadline=time.monotonic()+30
        while not (root/'release').exists() and time.monotonic()<deadline:
            time.sleep(.05)
with offline_discovery(case,observe,native_links=True):
    page=repo/'pages/Discovery.py'
    exec(compile(page.read_text(),str(page),'exec'),{'__file__':str(page),'__name__':'__main__'})
