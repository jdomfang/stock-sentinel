"""Streamlit sidebar navigation helpers."""

from __future__ import annotations

import streamlit as st


def render_sidebar_navigation() -> None:
    """Hide Streamlit's sidebar/navigation for a cleaner, focused layout."""
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"],
        [data-testid="stSidebarNav"] {
            display: none;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )