"""Offline doubles for the real Deep Analyze page. Never use real credits."""
from types import SimpleNamespace


def install_doubles(case, observe=lambda phase: None, *, stack=None):
    import httpx
    import requests
    import urllib.request
    import streamlit as st
    from utils import auth, guard, credits, analyze_client, config, obs
    from unittest.mock import patch

    events = case.setdefault('events', [])

    def prohibit(*args, **kwargs):
        events.append(('blocked_network',))
        observe('blocked_network')
        raise AssertionError('External service access prohibited in Deep Analyze tests')

    def consume(feature, metadata):
        events.append(('debit', feature, metadata['ticker']))
        observe('debit')
        if case.get('mode') == 'debit_exception':
            raise RuntimeError('offline debit exception')
        if case.get('mode') == 'stop':
            from streamlit.runtime.scriptrunner_utils.exceptions import StopException
            raise StopException()
        return SimpleNamespace(ok=case.get('mode') != 'refused', event_id='offline-deep',
                               reason='insufficient_credits')

    def remote(ticker, sector, **kwargs):
        events.append(('remote', ticker))
        observe('remote')
        if case.get('mode') == 'exception':
            raise RuntimeError('offline service exception')
        if case.get('mode') in ('failure', 'refund_failure'):
            return analyze_client.RemoteResult(error='offline failure', retryable=True)
        card = {} if case.get('mode') == 'empty' else {
            'ticker': ticker, 'verdict': 'Watch', 'confidence': 'Moderate',
            'rationale': ['Offline test evidence.'], 'evidence': {'mentions': 4},
        }
        return analyze_client.RemoteResult(ok=True, card=card)

    def refund(*args):
        events.append(('refund',))
        observe('refund')
        return case.get('mode') != 'refund_failure'

    def complete(*args):
        events.append(('complete', args[1]))
        observe('complete')

    def test_config(key, default=None):
        # Prevent telemetry and all configuration reads from the local secrets file.
        if key == 'ADMIN_EMAIL':
            return ''
        return default

    replacements = [
        (config, 'get', test_config), (obs, 'install', lambda: None),
        (guard, 'require_login', lambda **k: auth.ensure_user_scoped_state_owner()),
        (guard, 'require_active_account', lambda **k: dict(credits=case.get('credits', 2))),
        (auth, 'flush_pending_rt_save', lambda: None),
        (auth, 'refresh_session_if_needed', lambda: True),
        (auth, 'is_logged_in', lambda: True),
        (credits, 'consume_credit', consume), (credits, 'refund_credit', refund),
        (credits, 'complete_work', complete),
        (analyze_client, 'configured', lambda: case.get('mode') != 'unconfigured'),
        (analyze_client, 'analyze_remote', remote), (analyze_client, 'scan_remote', prohibit),
        (requests.sessions.Session, 'request', prohibit),
        (httpx.Client, 'send', prohibit), (httpx.AsyncClient, 'send', prohibit),
        (urllib.request, 'urlopen', prohibit),
    ]
    for module, name, replacement in replacements:
        if stack is None:
            # Browser reruns overlap: do not restore shared-module doubles while
            # a newer run is using them. This fixture lives in its own process.
            setattr(module, name, replacement)
        else:
            stack.enter_context(patch.object(module, name, replacement))
    st.session_state[auth.USER_KEY] = {'id': 'offline-deep'}
    st.session_state[auth.PRODUCT_STATE_OWNER_KEY] = 'offline-deep'
