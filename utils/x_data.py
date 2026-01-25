"""
X (Twitter) data handling module.
"""

import streamlit as st


def check_api_keys():
    """
    Check if required API keys are present in Streamlit secrets.
    
    Returns:
        bool: True if both X_BEARER_TOKEN and POLYGON_API_KEY exist, False otherwise
    """
    try:
        x_token = st.secrets.get("X_BEARER_TOKEN", "")
        polygon_key = st.secrets.get("POLYGON_API_KEY", "")
        
        # Check that both keys exist and are not empty
        return bool(x_token and polygon_key)
    except Exception:
        return False
