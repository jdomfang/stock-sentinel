"""Shared page shell and canonical URLs for the public Education area."""
from html import escape
from pathlib import Path

from utils.education_content import DEFAULT_PATHS, PAGE_DESCRIPTIONS, PAGE_TITLES, page_body

# Match the existing site's production URL convention; never derive canonicals
# from an untrusted request Host or the shared payments API environment.
PRODUCTION_ORIGIN = "https://thestocksentinel.com"
PATHS = {key: DEFAULT_PATHS[key] for key in PAGE_TITLES}
NATIVE_PAGES = {
    "education": "pages/Education.py",
    "ai-ed-shorts": "pages/AI_Ed_Shorts.py",
    "privacy": "pages/AI_Ed_Shorts_Privacy.py",
}
NATIVE_PATHS = {key: "/" + Path(value).stem for key, value in NATIVE_PAGES.items()}
CSS_FILES = ("stock-sentinel-tokens.css", "stock-sentinel-components.css", "education.css")
ROOT = Path(__file__).resolve().parents[1]
FOOTER_LINKS = (
    ("FAQ", "/FAQ"), ("How it works", "/How_It_Works"),
    ("Education", PATHS["education"]), ("Contact", "/Contact"),
    ("Trust Center", "/Trust_Center"),
    ("Privacy", "https://about.thestocksentinel.com/privacy/"),
    ("Terms", "https://about.thestocksentinel.com/terms/"),
)


def public_routes_enabled():
    from utils.config import get
    return str(get("SS_EDUCATION_PUBLIC_ROUTES", "")).lower() == "1"


def education_footer_destination():
    return PATHS["education"] if public_routes_enabled() else NATIVE_PAGES["education"]


def render_document(page):
    """Return crawlable initial HTML; no app session, credentials or data calls."""
    title = escape(PAGE_TITLES[page], quote=True)
    description = escape(PAGE_DESCRIPTIONS[page], quote=True)
    canonical = PRODUCTION_ORIGIN + PATHS[page]
    styles = ''.join(f'<link rel="stylesheet" href="/education-assets/{name}">' for name in CSS_FILES)
    footer = ''.join(f'<a href="{escape(url, quote=True)}">{label}</a>' for label, url in FOOTER_LINKS)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><meta name="description" content="{description}">
<link rel="canonical" href="{canonical}"><meta name="robots" content="index, follow">
<meta property="og:type" content="website"><meta property="og:site_name" content="The Stock Sentinel">
<meta property="og:title" content="{title}"><meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}"><meta name="twitter:card" content="summary">
{styles}</head><body class="ed-public">
<a class="ed-skip" href="#education-main">Skip to content</a>
<div class="ed-shell"><header class="ed-header"><a class="ed-brand" href="/Home">STOCK SENTINEL</a>
<nav aria-label="Main"><a href="/Auth">Log in</a><a class="ed-button" href="/Home">Start free</a></nav></header>
<main id="education-main" tabindex="-1">{page_body(page)}</main>
<footer class="ed-footer"><nav aria-label="Footer">{footer}</nav><p>Disclaimer: Not financial advice.</p></footer>
</div></body></html>'''


def sitemap_xml():
    entries = ''.join(f'<url><loc>{PRODUCTION_ORIGIN}{path}</loc></url>' for path in PATHS.values())
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + entries + '</urlset>\n'


def render_native_page(page):
    """Community Cloud preview: same copy, with the existing app shell."""
    import streamlit as st
    from utils.navigation import render_sidebar_navigation, render_top_nav
    from utils.ui import apply_theme, close_page
    st.set_page_config(page_title=PAGE_TITLES[page], page_icon="📚", layout="wide",
                       initial_sidebar_state="collapsed")
    apply_theme()
    render_sidebar_navigation()
    render_top_nav()
    st.html('<style>' + (ROOT / "assets/styles/education.css").read_text() + '</style>')
    st.html(page_body(page, PATHS if public_routes_enabled() else NATIVE_PATHS))
    close_page()
