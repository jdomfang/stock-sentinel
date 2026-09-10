"""Railway entrypoint: add exact public pages, then run the normal Streamlit CLI.

Usage: python -m portal.run run app.py --server.port 8501
Community Cloud retains its standard app.py entrypoint and native page fallbacks.
"""
import os

import streamlit
from streamlit.web.server.server import Server

from portal.education_routes import install_routes


SUPPORTED_STREAMLIT_VERSION = "1.40.0"


def install_public_routes():
    # This hook is private: upgrades must verify routes and websocket behavior.
    if streamlit.__version__ != SUPPORTED_STREAMLIT_VERSION:
        raise RuntimeError(
            "Education routes require Streamlit " + SUPPORTED_STREAMLIT_VERSION
            + "; verify the server hook before changing the supported version."
        )
    original = Server._create_app
    if getattr(original, "_stock_sentinel_education", False):
        return

    def create_app(server):
        return install_routes(original(server))

    create_app._stock_sentinel_education = True
    Server._create_app = create_app


def main():
    install_public_routes()
    os.environ["SS_EDUCATION_PUBLIC_ROUTES"] = "1"
    from streamlit.web.cli import main as streamlit_main
    streamlit_main()


if __name__ == "__main__":
    main()
