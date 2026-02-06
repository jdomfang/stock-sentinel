import streamlit as st
import streamlit.components.v1 as components
import requests
import json
import pandas as pd
from collections import defaultdict
import logging
from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page
from utils.sentiment import extract_tickers, analyze_sentiment
from utils.finance import get_ticker_master_list, get_stock_data, get_last_close_prices_best_effort
from utils.projections import simple_projection
from utils.deep_analysis import ANALYSIS_PROMPTS, run_deep_analysis, generate_ai_summary

# Set up logging - ensure it shows in Streamlit console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True  # Override any existing configuration
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Sidebar navigation
render_sidebar_navigation()
render_top_nav()
apply_theme()

from utils.guard import require_active_account
from utils.credits import consume_credit

_profile = require_active_account()

st.markdown(
    """
    <style>
    /* Discovery page styling; global theme comes from utils.ui.apply_theme() */

    /* Main container spacing */
    div[data-testid="stMainBlockContainer"] {
      max-width: 100%;
      padding-left: 2rem;
      padding-right: 2rem;
      padding-top: 0rem;
    }

    /* Remove extra top whitespace so hero sits closer to the sticky top nav */
    div[data-testid="stMainBlockContainer"] > div:first-child {
      margin-top: 0 !important;
      padding-top: 0 !important;
    }

    .discovery-wrapper {
      max-width: 1400px;
      margin: 0 auto;
      padding: 0 1rem;
    }

    /* Titles */
    .discovery-title {
      font-size: 2.0rem;
      font-weight: 750;
      letter-spacing: -0.02em;
      margin: 0;
      line-height: 1.15;
    }
    .discovery-subtitle {
      color: var(--muted);
      margin-top: 0.25rem;
      margin-bottom: 1.0rem;
      font-size: 0.98rem;
    }

    /* Hero (no box) */
    .hero {
      margin: -22px 0 5px 0;
      padding: 0;
    }
    .hero-eyebrow {
      color: rgba(56,189,248,.95);
      font-weight: 750;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      font-size: 0.78rem;
      margin-bottom: 10px;
    }
    .hero-title {
      font-size: 2.05rem;
      font-weight: 850;
      letter-spacing: -0.03em;
      line-height: 1.1;
      margin: 0 0 10px 0;
    }
    .hero-subtitle {
      color: var(--muted);
      font-size: 1.05rem;
      line-height: 1.5;
      margin: 0 0 12px 0;
      max-width: 980px;
    }
    .hero-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 12px 0 10px 0;
    }
    .chip {
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 7px 11px;
      background: rgba(2,6,23,.30);
      color: rgba(229,231,235,.92);
      font-size: 0.92rem;
      backdrop-filter: blur(6px);
    }
    .chip b { color: rgba(229,231,235,.98); }

    /* Subtle label color-coding (keeps values neutral; avoids implying outcomes) */
    .hero-chips .chip:nth-child(1) b { color: rgba(56,189,248,.95); }  /* Signal (accent) */
    .hero-chips .chip:nth-child(2) b { color: rgba(34,197,94,.92); }   /* Projected gain (good) */
    .hero-chips .chip:nth-child(3) b { color: rgba(245,158,11,.92); }  /* Volatility (warn) */
    .hero-chips .chip:nth-child(4) b { color: rgba(148,163,184,.95); } /* Suggested hold (muted) */
    .hero-caveat {
      color: rgba(229,231,235,.70);
      font-size: 0.92rem;
      margin-top: 4px;
    }

    /* Generic card */
    .card {
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(15,23,42,.92), rgba(15,23,42,.75));
      border-radius: 14px;
      padding: 16px;
    }

    /* Control row */
    .control-hint {
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 0.25rem;
    }

    /* Metrics row tweaks */
    [data-testid="stMetric"] {
      border: 1px solid var(--border);
      background: rgba(15,23,42,.65);
      border-radius: 14px;
      padding: 12px 14px;
    }
    [data-testid="stMetric"] label {
      color: var(--muted) !important;
    }

    /* Inputs */
    [data-baseweb="select"] > div,
    [data-baseweb="input"] > div {
      background-color: rgba(2,6,23,.55) !important;
      border-color: var(--border) !important;
      color: var(--text) !important;
    }

    /* Select dropdown menu (Streamlit/Browser differences: BaseWeb + native fallbacks) */
    [data-baseweb="popover"] { z-index: 9999; }

    /* BaseWeb list surfaces */
    [data-baseweb="popover"] [data-baseweb="menu"],
    [data-baseweb="popover"] ul[role="listbox"],
    [data-baseweb="popover"] div[role="listbox"],
    ul[role="listbox"],
    div[role="listbox"],
    [role="listbox"],
    [role="list"],
    [role="menu"] {
      background-color: #0F172A !important;
      border: 1px solid var(--border) !important;
      border-radius: 14px !important;
      overflow: hidden;
      box-shadow: 0 16px 40px rgba(0,0,0,.45) !important;
    }

    /* BaseWeb option rows */
    [role="option"],
    [role="menuitem"] {
      background-color: transparent !important;
      color: #E5E7EB !important;
      opacity: 1 !important;
    }
    [role="option"]:hover,
    [role="menuitem"]:hover {
      background-color: rgba(56,189,248,.16) !important;
    }
    [role="option"][aria-selected="true"] {
      background-color: rgba(56,189,248,.22) !important;
    }

    /* Streamlit selectbox virtual dropdown (this is what you're seeing) */
    /* Streamlit selectbox virtual dropdown (this is what you're seeing) */
    ul[data-testid="stSelectboxVirtualDropdown"],
    [data-testid="stSelectboxVirtualDropdown"] {
      background: #0F172A !important;
      background-color: #0F172A !important;
      border: 1px solid var(--border) !important;
      border-radius: 14px !important;
      box-shadow: 0 16px 40px rgba(0,0,0,.45) !important;
    }

    /* Ensure list items inherit dark background */
    ul[data-testid="stSelectboxVirtualDropdown"] li {
      background: transparent !important;
      background-color: transparent !important;
      color: #E5E7EB !important;
      opacity: 1 !important;
    }
    ul[data-testid="stSelectboxVirtualDropdown"] li:hover {
      background: rgba(56,189,248,.16) !important;
      background-color: rgba(56,189,248,.16) !important;
    }

    /* Force text within options */
    ul[data-testid="stSelectboxVirtualDropdown"] li *,
    ul[data-testid="stSelectboxVirtualDropdown"] * {
      color: #E5E7EB !important;
      opacity: 1 !important;
    }

    /* Native <select> fallback (Windows light theme can force pale options) */
    select {
      background-color: rgba(2,6,23,.55) !important;
      color: #E5E7EB !important;
      border-color: var(--border) !important;
    }
    select option {
      background-color: #0F172A !important;
      color: #E5E7EB !important;
    }

    /* Buttons */
    .stButton > button {
      border-radius: 12px;
      border: 1px solid rgba(56,189,248,0.28);
      background: rgba(15, 23, 42, 0.85);
      background-color: rgba(15, 23, 42, 0.85);
      color: #E5E7EB;
      font-weight: 650;
      opacity: 1;
      filter: none;
    }
    .stButton > button:hover {
      border-color: rgba(56, 189, 248, 0.55);
      background: rgba(15, 23, 42, 1.0);
      background-color: rgba(15, 23, 42, 1.0);
    }

    /* Secondary buttons (e.g., Deep Analyze) */
    button[data-testid="stBaseButton-secondary"],
    .stButton > button[kind="secondary"] {
      background: rgba(15, 23, 42, 0.85) !important;
      background-color: rgba(15, 23, 42, 0.85) !important;
      color: #E5E7EB !important;
      border: 1px solid rgba(56,189,248,0.28) !important;
      opacity: 1 !important;
    }
    button[data-testid="stBaseButton-secondary"]:hover,
    .stButton > button[kind="secondary"]:hover {
      background: rgba(15, 23, 42, 1.0) !important;
      background-color: rgba(15, 23, 42, 1.0) !important;
      border-color: rgba(56,189,248,0.55) !important;
    }

    /* Primary CTA */
    /* Primary buttons (Scan X) — must override the generic button rule */
    button[data-testid="stBaseButton-primary"],
    .stButton > button[kind="primary"] {
      background: linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95)) !important;
      background-color: transparent !important;
      border: 1px solid rgba(56,189,248,.45) !important;
      color: #001018 !important;
      font-weight: 650 !important;
    }

    /* Disabled state readability (fix Deep Analyze looking "invisible") */
    .stButton > button:disabled {
      background: rgba(15, 23, 42, 0.55) !important;
      color: rgba(229, 231, 235, 0.70) !important;
      border-color: rgba(56,189,248,0.20) !important;
      opacity: 1 !important;
      filter: none !important;
    }

    /* Dataframe */
    .stDataFrame { width: 100%; }

    /* Validated ticker rows */
    .ticker-row {
      padding: 0.85rem 1rem;
      border: 1px solid var(--border);
      border-radius: 14px;
      margin-bottom: 0.65rem;
      background: rgba(15, 23, 42, 0.60);
      transition: background 0.18s ease, border 0.18s ease, transform 0.18s ease;
    }
    .ticker-row:hover {
      background: rgba(15, 23, 42, 0.85);
      border-color: rgba(56, 189, 248, 0.40);
      transform: translateY(-1px);
    }

    /* Hide Streamlit "Made with" footer */
    footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)

# JS-only UI fix: Streamlit selectbox dropdown can render with forced white background on some builds.
# This mutation observer applies a dark background + readable text whenever the dropdown menu appears.
components.html(
    """
    <script>
    (function () {
      const APPLY_TO = (doc) => {
        // Fix selectbox dropdown
        const ul = doc.querySelector('ul[data-testid="stSelectboxVirtualDropdown"]');
        if (ul) {
          ul.style.setProperty('background-color', '#0F172A', 'important');
          ul.style.setProperty('color', '#E5E7EB', 'important');
          ul.querySelectorAll('li, li *').forEach((el) => {
            el.style.setProperty('color', '#E5E7EB', 'important');
            el.style.setProperty('opacity', '1', 'important');
          });
        }

        // Fix secondary buttons (e.g. Deep Analyze) that get forced white by Streamlit theme.
        // We also install explicit hover handlers because Streamlit's styles can override CSS :hover.
        doc.querySelectorAll('button[data-testid="stBaseButton-secondary"]').forEach((btn) => {
          const base = () => {
            btn.style.setProperty('background-image', 'none', 'important');
            btn.style.setProperty('background-color', 'rgba(15, 23, 42, 0.92)', 'important');
            btn.style.setProperty('border', '1px solid rgba(56,189,248,0.28)', 'important');
            btn.style.setProperty('color', '#E5E7EB', 'important');
            btn.style.setProperty('opacity', '1', 'important');
            btn.style.setProperty('filter', 'none', 'important');

            // Streamlit renders button text as <p> inside the button
            btn.querySelectorAll('p, span').forEach((t) => {
              t.style.setProperty('color', '#E5E7EB', 'important');
            });
          };

          const hover = () => {
            btn.style.setProperty('background-image', 'linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95))', 'important');
            btn.style.setProperty('background-color', 'transparent', 'important');
            btn.style.setProperty('border', '1px solid rgba(56,189,248,.45)', 'important');
            btn.style.setProperty('color', '#001018', 'important');
            btn.style.setProperty('opacity', '1', 'important');
            btn.style.setProperty('filter', 'none', 'important');

            btn.querySelectorAll('p, span').forEach((t) => {
              t.style.setProperty('color', '#001018', 'important');
            });
          };

          // Apply style each pass without stomping hover
          if (btn.matches(':hover') || btn.matches(':focus')) hover();
          else base();

          // Install hover handlers once
          if (!btn.dataset.clawdHoverBound) {
            btn.dataset.clawdHoverBound = '1';
            btn.addEventListener('mouseenter', hover);
            btn.addEventListener('mouseleave', base);
            btn.addEventListener('focus', hover);
            btn.addEventListener('blur', base);
          }
        });

        // Restore primary button (Scan X) gradient
        doc.querySelectorAll('button[data-testid="stBaseButton-primary"], button[kind="primary"]').forEach((btn) => {
          btn.style.setProperty('background-image', 'linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95))', 'important');
          btn.style.setProperty('background-color', 'transparent', 'important');
          btn.style.setProperty('border', '1px solid rgba(56,189,248,.45)', 'important');
          btn.style.setProperty('color', '#001018', 'important');
          btn.style.setProperty('font-weight', '650', 'important');
          btn.style.setProperty('opacity', '1', 'important');
        });
      };

      const APPLY = () => {
        // Always apply to current document
        APPLY_TO(document);

        // Also try to apply to parent if accessible
        try {
          if (window.parent && window.parent.document) APPLY_TO(window.parent.document);
        } catch (e) {}
      };

      const obs = new MutationObserver(() => APPLY());
      obs.observe(document.documentElement, { childList: true, subtree: true });
      window.addEventListener('load', APPLY);
      setTimeout(APPLY, 250);
      setTimeout(APPLY, 1000);
      setInterval(APPLY, 750);
    })();
    </script>
    """,
    height=0,
)

st.markdown('<div class="clawd-app-wrapper discovery-wrapper">', unsafe_allow_html=True)

st.markdown(
    """
    <div class="hero">
      <div class="hero-eyebrow">Stock Sentinel</div>
      <div class="hero-title">Finding short‑term opportunities shouldn’t feel like a full‑time job.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None
if "selected_sector" not in st.session_state:
    st.session_state.selected_sector = None
if "deep_analysis_results" not in st.session_state:
    st.session_state.deep_analysis_results = None
if "df_valid" not in st.session_state:
    st.session_state.df_valid = None
if "df_unvalidated" not in st.session_state:
    st.session_state.df_unvalidated = None

# Input form (UI/UX only — functionality unchanged)
# Brief primer (keeps UI clean; replaces the old "How it works" expander)
# Streamlit widgets can't truly be wrapped by an HTML <div>.
# Left-align controls (dashboard feel) while keeping a sane max width.
_main, _spacer = st.columns([2.8, 1.2])

with _main:
    st.markdown(
        """
        <div style="
          font-size: 1.05rem;
          line-height: 1.35;
          font-weight: 520;
          color: rgba(229, 231, 235, 0.95);
          margin: 0.2rem 0 0.6rem 0;
          padding: 0.45rem 0.65rem;
          border-left: 3px solid rgba(56, 189, 248, 0.65);
          border-radius: 0.65rem;
          background: rgba(15, 23, 42, 0.35);
        ">
          <span style="font-weight: 750; color: rgba(229, 231, 235, 1);">Pick a sector</span>
          <span> and we identify <b>US stocks</b> gaining unusual attention in your selected sector—fast.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Controls (make sector field less wide by adding a right-side spacer column)
    ctrl_left, ctrl_right, _ctrl_pad = st.columns([0.78, 0.55, 2.67])

    with ctrl_left:
        sector = st.selectbox(
            "Sector",
            options=[
                "tech",
                "healthcare",
                "energy",
                "finance",
                "consumer",
                "utilities",
                "real estate",
                "industrials",
                "materials",
                "communication",
            ],
            index=0,
        )

    # Keep the existing behavior (max_results=100) as a code-only setting
    max_results = 100

    with ctrl_right:
        st.markdown("<div style='height: 1.65rem;'></div>", unsafe_allow_html=True)
        scan_clicked = st.button(
            "Sentinel Scan",
            type="primary",
            use_container_width=True,
            disabled=False,
        )

    with _ctrl_pad:
        # intentional blank space to prevent full-width stretching
        st.markdown("")


# Scan button
if scan_clicked:
    # Must be logged in to scan.
    if not st.session_state.get("auth.user"):
        st.error("Please log in to scan.")
        st.stop()

    ok, err = consume_credit("scan")
    if not ok:
        st.error(err)
        st.stop()

    try:
        # Load X Bearer Token from secrets
        x_bearer_token = st.secrets["X_BEARER_TOKEN"]
        
        # Construct search query with sector-specific keywords (Free-tier compatible)
        # Note: Advanced operators like min_faves, filter:, and since: require Basic tier or higher
        # Using only basic operators: Boolean, lang, and -is:retweet for Free tier

        # Add sector-specific keywords to improve relevance
        sector_keywords = {
            'tech': 'technology OR software OR AI OR chip OR semiconductor OR cloud OR internet',
            'healthcare': 'healthcare OR medical OR pharma OR biotechnology OR drug OR clinical OR FDA',
            'energy': 'energy OR oil OR gas OR renewable OR solar OR wind OR fossil OR petroleum',
            'finance': 'finance OR bank OR financial OR investment OR lending OR credit OR wealth',
            'consumer': 'consumer OR retail OR e-commerce OR shopping OR consumer goods OR discretionary',
            'utilities': 'utilities OR electric OR power OR water OR gas OR infrastructure OR telecom',
            'real estate': 'real estate OR property OR REIT OR housing OR commercial OR residential',
            'industrials': 'industrials OR manufacturing OR industrial OR aerospace OR defense OR construction',
            'materials': 'materials OR mining OR chemical OR steel OR cement OR commodity OR metals',
            'communication': 'communication OR telecom OR media OR entertainment OR broadcasting OR wireless'
        }

        sector_terms = sector_keywords.get(sector.lower(), sector)
        query = f"({sector} OR {sector_terms}) stock (bullish OR opportunity OR catalyst OR growth OR earnings) -bearish lang:en -is:retweet"
        
        # API endpoint
        url = "https://api.twitter.com/2/tweets/search/recent"
        
        # Headers
        headers = {
            "Authorization": f"Bearer {x_bearer_token}"
        }
        
        # Parameters
        params = {
            "query": query,
            "max_results": max_results,
            "tweet.fields": "text,created_at,public_metrics"
        }
        
        logger.info(f"🔍 Starting X search for sector: {sector}")
        logger.info(f"📝 Search query: {query}")
        logger.info(f"📊 Max results requested: {max_results}")

        # Show loading spinner
        with st.spinner("Searching X for emerging stocks..."):
            # Make API request
            response = requests.get(url, headers=headers, params=params)

        logger.info(f"📡 X API response status: {response.status_code}")

        # Handle different response codes
        if response.status_code == 200:
            # Success - display results
            data = response.json()
            tweets = data.get('data', [])
            logger.info(f"📄 Raw tweets from X API: {len(tweets)}")

            # Filter tweets for sector relevance
            sector_relevant_tweets = []
            for tweet in tweets:
                text = tweet.get('text', '').lower()
                # Check if tweet contains sector-specific keywords
                if any(keyword.lower() in text for keyword in sector_terms.split(' OR ')) or sector.lower() in text:
                    sector_relevant_tweets.append(tweet)

            tweets = sector_relevant_tweets
            logger.info(f"🎯 Sector-relevant tweets after filtering: {len(tweets)}")
            st.success(f"✅ Found {len(tweets)} sector-relevant posts!")
            
            # Process tweets for ticker extraction and sentiment analysis
            if tweets:
                with st.spinner("Analyzing tickers and sentiment..."):
                    # Aggregate data by ticker
                    ticker_data = defaultdict(lambda: {
                        'mentions': 0,
                        'sentiment_scores': [],
                        'sentiments': [],
                        'sample_tweets': []
                    })
                    
                    # Process each tweet
                    for tweet in tweets:
                        text = tweet.get('text', '')
                        
                        # Extract tickers
                        tickers = extract_tickers(text)
                        
                        # Analyze sentiment
                        sentiment_result = analyze_sentiment(text)
                        
                        # Aggregate by ticker
                        for ticker in tickers:
                            ticker_data[ticker]['mentions'] += 1
                            # Store the actual score and label for debugging
                            ticker_data[ticker]['sentiment_scores'].append(sentiment_result['score'])
                            ticker_data[ticker]['sentiments'].append(sentiment_result['sentiment'])
                            # Also store raw label for verification
                            if 'raw_labels' not in ticker_data[ticker]:
                                ticker_data[ticker]['raw_labels'] = []
                            ticker_data[ticker]['raw_labels'].append(f"{sentiment_result['label']}:{sentiment_result['score']:.3f}")
                            
                            # Keep up to 3 sample tweets
                            if len(ticker_data[ticker]['sample_tweets']) < 3:
                                # Truncate long tweets
                                short_text = text[:150] + "..." if len(text) > 150 else text
                                ticker_data[ticker]['sample_tweets'].append(short_text)
                    
                    # Convert to DataFrame
                    if ticker_data:
                        rows = []
                        for ticker, info in ticker_data.items():
                            avg_sentiment = sum(info['sentiment_scores']) / len(info['sentiment_scores'])
                            # Determine overall sentiment
                            sentiment_counts = {}
                            for s in info['sentiments']:
                                sentiment_counts[s] = sentiment_counts.get(s, 0) + 1
                            overall_sentiment = max(sentiment_counts, key=sentiment_counts.get)
                            
                            rows.append({
                                'Ticker': ticker,
                                'Mentions': info['mentions'],
                                'Avg Sentiment Score': round(avg_sentiment, 3),
                                'Overall Sentiment': overall_sentiment,
                                'Sample Tweets': ' | '.join(info['sample_tweets'])
                            })
                        
                        # Sort by mentions (descending)
                        df = pd.DataFrame(rows).sort_values('Mentions', ascending=False)

                        logger.info(f"📊 Ticker analysis complete:")
                        logger.info(f"   • Total unique tickers found: {len(df)}")
                        logger.info(f"   • Top 5 by mentions: {df.head(5)[['Ticker', 'Mentions', 'Avg Sentiment Score', 'Overall Sentiment']].to_dict('records')}")
                        logger.info(f"   • Top tickers for validation: {df.head(10)['Ticker'].tolist()}")

                        # Load local ticker database for fast validation
                        st.info("📈 Validating tickers...")

                        # Load comprehensive ticker database
                        ticker_master_list = get_ticker_master_list()

                        if not ticker_master_list:
                            st.error("❌ Could not load ticker database. Please check the data directory.")
                        else:
                            top_tickers = df.head(10)['Ticker'].tolist()  # Check top 10

                            # Add placeholder columns
                            df['Valid'] = False
                            df['Company Name'] = 'N/A'

                            validated_count = 0
                            validation_errors = []

                            logger.info(f"🔍 Starting validation of top {len(top_tickers)} tickers for sector '{sector}'")

                            with st.spinner(f"Validating up to {len(top_tickers)} top tickers..."):
                                for idx, row in df.iterrows():
                                    ticker = row['Ticker']

                                    # Only process top tickers to avoid rate limits
                                    if ticker not in top_tickers:
                                        continue

                                    logger.info(f"🔎 Validating ticker: {ticker} (mentions: {row['Mentions']}, sentiment: {row['Avg Sentiment Score']:.3f})")

                                    # Check if ticker exists in our local database (no API call needed!)
                                    ticker_upper = ticker.upper()
                                    if ticker_upper in ticker_master_list:
                                        ticker_info = ticker_master_list[ticker_upper]

                                        # Check if ticker sector matches selected sector
                                        ticker_sector = ticker_info.get('sector', '').lower() if ticker_info.get('sector') else ''

                                        # Map Polygon sectors to our UI sectors for comparison
                                        sector_mapping = {
                                            'technology': 'tech',
                                            'healthcare': 'healthcare',
                                            'energy': 'energy',
                                            'financial services': 'finance',
                                            'consumer cyclical': 'consumer',
                                            'utilities': 'utilities',
                                            'real estate': 'real estate',
                                            'industrials': 'industrials',
                                            'basic materials': 'materials',
                                            'communication services': 'communication'
                                        }

                                        mapped_sector = sector_mapping.get(ticker_sector, ticker_sector)

                                        # Allow tickers to pass validation if sector is unknown (empty)
                                        # Since sector filtering happens at tweet search level, we only reject
                                        # when sector explicitly doesn't match
                                        sector_matches = (mapped_sector == sector.lower()) or (mapped_sector == "")

                                        if sector_matches:
                                            # Sector matches or is unknown - mark as valid and get company info from local data
                                            df.at[idx, 'Valid'] = True
                                            df.at[idx, 'Company Name'] = ticker_info.get('name', ticker)
                                            validated_count += 1
                                            if mapped_sector == "":
                                                logger.info(f"✅ {ticker}: VALID - {ticker_info.get('name', ticker)} (sector: unknown)")
                                            else:
                                                logger.info(f"✅ {ticker}: VALID - {ticker_info.get('name', ticker)} (sector: {mapped_sector})")

                                            # Price will be shown in Deep Analyze instead
                                            pass
                        # Filter to show only validated tickers (with financial data)
                        df_valid = df[df['Valid'] == True].copy()
                        df_valid = df_valid.drop(columns=['Valid'])

                        # Also show unvalidated tickers separately
                        df_unvalidated = df[df['Valid'] == False].copy()
                        df_unvalidated = df_unvalidated.drop(columns=['Valid', 'Company Name'])

                        st.session_state.df_valid = df_valid
                        st.session_state.df_unvalidated = df_unvalidated
                        st.session_state.selected_sector = sector
                        st.session_state.selected_ticker = None
                        st.session_state.deep_analysis_results = None
                        
                        # Show unvalidated tickers in expander - HIDDEN FROM UI
                        # if len(df_unvalidated) > 0:
                        #     with st.expander(f"⚠️ Other Mentions ({len(df_unvalidated)}) - May include non-stock terms"):
                        #         st.dataframe(
                        #             df_unvalidated,
                        #             column_config={
                        #                 "Ticker": st.column_config.TextColumn("Ticker", width="small"),
                        #                 "Mentions": st.column_config.NumberColumn("Mentions", width="small"),
                        #                 "Avg Sentiment Score": st.column_config.NumberColumn(
                        #                     "Avg Sentiment Score",
                        #                     format="%.3f",
                        #                     width="small"
                        #                 ),
                        #                 "Overall Sentiment": st.column_config.TextColumn("Overall Sentiment", width="small"),
                        #                 "Sample Tweets": st.column_config.TextColumn("Sample Tweets", width="large")
                        #             },
                        #             hide_index=True,
                        #             use_container_width=True
                        #         )
                        #         st.caption("These items couldn't be validated as stocks (may be abbreviations, news orgs, etc.)")
                        
                        # Show validation errors if any - HIDDEN FROM UI
                        # if validation_errors:
                        #     with st.expander("🔍 Validation Details"):
                        #         st.caption("Why some tickers couldn't be validated:")
                        #         for error in validation_errors:
                        #             st.text(f"• {error}")
                    else:
                        st.warning("⚠️ No stock tickers found in the tweets. Try a different search query.")
                
                # Show raw data in expander - HIDDEN FROM UI
                # with st.expander("📄 View Raw API Response"):
                #     st.json(data)
            else:
                st.warning("No tweets found matching your search criteria.")

        elif response.status_code == 401:
            # Unauthorized
            st.error("❌ Authentication failed (401): Invalid X Bearer Token. Please check your credentials in .streamlit/secrets.toml")

        elif response.status_code == 429:
            # Rate limit exceeded
            st.error("⚠️ Rate limit exceeded (429): Too many requests. Please wait a few minutes before trying again.")
            st.info("X API has rate limits. Consider reducing the frequency of requests.")

        else:
            # Other errors
            # Hide raw provider errors from users
            st.error("Something went wrong. Please try again later.")

    except KeyError:
        st.error("❌ Missing X_BEARER_TOKEN in .streamlit/secrets.toml")
        st.info("Please add your X Bearer Token to the secrets file.")

    except requests.exceptions.RequestException:
        st.error("Something went wrong. Please try again later.")
        st.info("Please check your internet connection and try again.")

    except Exception:
        logger.exception("Discovery scan failed")
        st.error("Something went wrong. Please try again later.")

# --- Admin-only: Save demo snapshots for Home (local only; no API calls) ---
# These buttons save the *current* results to data/education/ so Home can show
# a "clean" educational snapshot without running any paid API calls.

ADMIN_MODE = bool(st.secrets.get("ADMIN_MODE", False))

if ADMIN_MODE:
    with st.expander("🛠 Admin: Demo snapshot tools", expanded=False):
        # Save Scan snapshot (validated table)
        if st.session_state.get("df_valid") is not None and len(st.session_state.df_valid) > 0:
            if st.button("💾 Save Scan Snapshot for Home (demo)"):
                try:
                    from pathlib import Path
                    import json

                    out_dir = Path(__file__).resolve().parents[1] / "data" / "education"
                    out_dir.mkdir(parents=True, exist_ok=True)

                    df_out = st.session_state.df_valid.drop(columns=["Valid", "Mentions", "Sample Tweets"], errors="ignore")

                    payload = {
                        "sector": st.session_state.get("selected_sector") or "",
                        "generated_at": "snapshot",
                        "validated_rows": df_out.to_dict(orient="records"),
                    }
                    (out_dir / "scan_latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    st.success("Saved: data/education/scan_latest.json")
                except Exception:
                    st.error("Something went wrong. Please try again later.")
        else:
            st.caption("Run a scan to enable saving scan snapshot.")

        # Save Deep Analyze snapshot
        if st.session_state.get("selected_ticker") and st.session_state.get("deep_analysis_results"):
            if st.button("💾 Save Deep Analyze Snapshot for Home (demo)"):
                try:
                    from pathlib import Path
                    import json

                    out_dir = Path(__file__).resolve().parents[1] / "data" / "education"
                    out_dir.mkdir(parents=True, exist_ok=True)

                    payload = {
                        "ticker": st.session_state.get("selected_ticker") or "",
                        "sector": st.session_state.get("selected_sector") or "",
                        "generated_at": "snapshot",
                        "analysis_results": st.session_state.get("deep_analysis_results") or {},
                    }
                    (out_dir / "deep_latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    st.success("Saved: data/education/deep_latest.json")
                except Exception:
                    st.error("Something went wrong. Please try again later.")
        else:
            st.caption("Run Deep Analyze to enable saving deep snapshot.")

# KPI strip (UI only)
if st.session_state.df_valid is not None:
    try:
        dfv = st.session_state.df_valid
        dfn = st.session_state.df_unvalidated

        total_valid = int(len(dfv)) if dfv is not None else 0
        total_other = int(len(dfn)) if dfn is not None else 0
        total_unique = total_valid + total_other
        avg_sent = float(dfv["Avg Sentiment Score"].mean()) if (dfv is not None and "Avg Sentiment Score" in dfv.columns and len(dfv) > 0) else 0.0

        k1, k2, k3 = st.columns(3)
        k1.metric("Validated stocks", total_valid)
        k2.metric("Other mentions", total_other)
        k3.metric("Avg sentiment", f"{avg_sent:.3f}")
        st.markdown("", unsafe_allow_html=True)
    except Exception:
        # Never let UI extras break the page
        pass

if st.session_state.df_valid is not None:
    df_valid_display = st.session_state.df_valid.drop(columns=["Mentions", "Sample Tweets"], errors="ignore")

    if len(df_valid_display) > 0:
        st.subheader("✅ Stocks Found with Market Sentiment")
        header_cols = st.columns([0.9, 1.5, 1.1, 0.95, 0.9])
        # Load last close prices (cache → Polygon on miss → cache)
        tickers_for_prices = [str(t) for t in df_valid_display["Ticker"].tolist()]
        last_close_map = {}
        try:
            last_close_map = get_last_close_prices_best_effort(tickers_for_prices)
        except Exception as e:
            logger.exception("Last close price lookup failed")
            # Don't break the scan UI; show a small hint for debugging.
            st.caption(f"Price lookup failed: {str(e)[:120]}")
            last_close_map = {}

        header_labels = [
            "Ticker",
            "Company",
            "Last Close",
            "Overall",
            "Deep Analyze"
        ]
        for col, label in zip(header_cols, header_labels):
            col.markdown(f"**{label}**")

        for _, row in df_valid_display.iterrows():
            ticker_symbol = row["Ticker"]
            company_name = row["Company Name"]
            overall_sentiment = row["Overall Sentiment"]
            last_close = last_close_map.get(str(ticker_symbol).upper())
            last_close_display = "N/A" if last_close is None else f"${float(last_close):.2f}"

            st.markdown("<div class='ticker-row'>", unsafe_allow_html=True)
            col1, col2, col3, col4, col5 = st.columns(
                [0.9, 1.5, 1.1, 0.95, 0.9]
            )
            with col1:
                st.markdown(f"**{ticker_symbol}**")
            with col2:
                st.markdown(company_name)
            with col3:
                st.markdown(last_close_display)
            with col4:
                st.markdown(overall_sentiment)
            with col5:
                if st.button("Deep Analyze", key=f"deep_analyze_{ticker_symbol}"):
                    st.session_state.selected_ticker = ticker_symbol
                    with st.spinner(f"Running deep analysis for {ticker_symbol}..."):
                        st.session_state.deep_analysis_results = run_deep_analysis(
                            ticker_symbol,
                            st.session_state.selected_sector,
                        )
            st.markdown("</div>", unsafe_allow_html=True)

        st.success(f"📊 {len(df_valid_display)} validated stock(s) found. Click 'Deep Analyze' for complete financial insights.")
    else:
        st.warning("⚠️ No valid stock tickers found with financial data.")

if st.session_state.selected_ticker and st.session_state.deep_analysis_results:
    st.markdown("---")
    st.subheader(
        f"🧠 Deep Analysis for {st.session_state.selected_ticker} ({st.session_state.selected_sector})"
    )
    ai_summary = generate_ai_summary(st.session_state.deep_analysis_results)

    # --- Headline summary (always show a complete block) ---
    col1, col2, col3 = st.columns([1.2, 1, 2])
    with col1:
        st.metric("Recommendation", ai_summary["recommendation"])
    with col2:
        st.metric("Confidence", ai_summary["confidence"])
    with col3:
        st.metric("Weighted Sentiment", f"{ai_summary['avg_sentiment']:.3f}")

    # Financial metrics (best-effort; always displayed)
    ticker = st.session_state.selected_ticker
    price_display = "Unavailable"
    price_reason = "Not fetched"
    proj_display = "Unavailable"
    proj_reason = "Need price data"
    hold_display = "Unavailable"
    hold_reason = "Need projection"
    price_points = 0

    try:
        stock_data = get_stock_data(ticker)
        if stock_data.get("error") is None and stock_data.get("prices"):
            prices = stock_data.get("prices") or []
            price_points = len(prices)
            last_px = prices[-1]
            if isinstance(last_px, (int, float)):
                price_display = f"${last_px:.2f}"
                price_reason = ""
            else:
                price_reason = "Invalid price"

            # Projection (uses avg sentiment from AI summary)
            projection = simple_projection(prices, ai_summary["avg_sentiment"], days=30)
            if projection.get("error") is None:
                p10 = projection.get("gain_p10")
                p90 = projection.get("gain_p90")
                if p10 is not None and p90 is not None:
                    proj_display = f"{p10:.1f}–{p90:.1f}%"
                else:
                    proj_display = f"{float(projection.get('avg_gain', 0.0)):.1f}%"
                proj_reason = ""

                hold_display = f"{int(projection.get('suggested_hold_days', 0))} days"
                hold_reason = ""
            else:
                proj_reason = projection.get("error") or "Projection failed"
                hold_reason = "Projection failed"
        else:
            # Hide raw provider errors from users
            price_reason = "Data unavailable"
            proj_reason = "Data unavailable"
            hold_reason = "Data unavailable"
    except Exception:
        # Hide raw exception details from users; full trace should be in server logs
        price_reason = "Data unavailable"
        proj_reason = "Data unavailable"
        hold_reason = "Data unavailable"

    f1, f2, f3 = st.columns([1.2, 1, 2])
    with f1:
        st.metric("Current Price", price_display)
        if price_display == "Unavailable":
            st.caption(f"Reason: {price_reason}")
    with f2:
        st.metric("Projected Gain (30d)", proj_display)
        if proj_display == "Unavailable":
            st.caption(f"Reason: {proj_reason}")
    with f3:
        st.metric("Hold Period", hold_display)
        if hold_display == "Unavailable":
            st.caption(f"Reason: {hold_reason}")

    # Data quality line
    try:
        _uids = set()
        for _r in (st.session_state.deep_analysis_results or {}).values():
            for _tid in (_r.get("tweet_ids") or []):
                _uids.add(_tid)
        _mentions_ct = len(_uids)
    except Exception:
        _mentions_ct = 0

    st.caption(f"Data quality: {_mentions_ct} mentions • {price_points} price points")

    st.markdown("**📋 Rationale:**")
    for bullet in ai_summary["rationale"]:
        st.markdown(f"- {bullet}")

    # Append a finance bullet to rationale for clarity
    if price_display != "Unavailable" and proj_display != "Unavailable" and hold_display != "Unavailable":
        st.markdown(f"- Price {price_display}; projected {proj_display} over 30d; suggested hold {hold_display}.")
    elif price_display == "Unavailable":
        st.markdown("- Price/projection unavailable.")

    with st.expander("📦 Full Analysis Details", expanded=False):
        # --- Coverage / data-quality table (lean, non-insight) ---
        coverage_rows = []
        for prompt_name, result in (st.session_state.deep_analysis_results or {}).items():
            timeframe = (ANALYSIS_PROMPTS.get(prompt_name, {}) or {}).get("timeframe", "")
            evidence = int(result.get("mention_count", 0) or 0)
            overall = (result.get("overall_sentiment") or "").lower()

            # Strength (quantity). Do NOT conflate with bullish/bearish.
            if overall == "error":
                strength = "Unavailable"
            elif evidence == 0:
                strength = "No Signal"
            elif evidence <= 5:
                strength = "Weak"
            else:
                strength = "Strong"

            # Tilt (direction)
            if overall == "error":
                tilt = "Unavailable"
            elif evidence == 0:
                tilt = "Neutral"
            else:
                tilt = overall.title() if overall in ("bullish", "bearish", "neutral") else "Neutral"

            coverage_rows.append({
                "Analysis Type": prompt_name,
                "Timeframe": timeframe,
                "Evidence Count": evidence,
                "Signal Strength": strength,
                "Sentiment Tilt": tilt,
            })

        df_cov = pd.DataFrame(coverage_rows)

        if not df_cov.empty:
            st.dataframe(
                df_cov,
                column_config={
                    "Analysis Type": st.column_config.TextColumn("Analysis Type", width="large"),
                    "Timeframe": st.column_config.TextColumn("Timeframe", width="small"),
                    "Evidence Count": st.column_config.NumberColumn("Evidence Count", width="small"),
                    "Signal Strength": st.column_config.TextColumn("Signal Strength", width="small"),
                    "Sentiment Tilt": st.column_config.TextColumn("Sentiment Tilt", width="small"),
                },
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption("No coverage data available.")

        st.subheader("📋 Detailed Analysis")
        for prompt_name, config in ANALYSIS_PROMPTS.items():
            st.markdown(f"### 🔍 {prompt_name}")
            st.markdown(f"**Description:** {config['description']}")
            st.markdown(f"**Timeframe:** {config['timeframe']}")

            if prompt_name in st.session_state.deep_analysis_results:
                result = st.session_state.deep_analysis_results[prompt_name]

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Sentiment Score", f"{result['sentiment_score']:.3f}")
                with col2:
                    st.metric("Overall Sentiment", result['overall_sentiment'].title())
                with col3:
                    st.metric("Mentions Found", result['mention_count'])

                st.markdown(f"**Key Insights:** {result['insights']}")

                if result['key_themes']:
                    st.markdown(f"**Key Themes:** {', '.join(result['key_themes'])}")

                if result['sample_tweets']:
                    st.markdown("**Sample Tweets:**")
                    for i, tweet in enumerate(result['sample_tweets'], 1):
                        st.text(f"{i}. {tweet}")
            else:
                st.error("Analysis failed for this prompt.")

            st.markdown("---")

# Performance statistics (show in expander) - HIDDEN FROM UI
# with st.expander("📊 Performance & Database Stats"):
#     from utils.finance import get_cache_stats, get_ticker_master_list

#     # Show ticker database stats
#     ticker_db = get_ticker_master_list()
#     db_size = len(ticker_db) if ticker_db else 0

#     col1, col2 = st.columns(2)

#     with col1:
#         st.metric("US Stock Database", f"{db_size} tickers")
#         st.caption("Comprehensive US stock database")

#     with col2:
#         cache_stats = get_cache_stats()
#         st.metric("Price Data Cache", f"{cache_stats['stock_data_cache']['entries']} entries")
#         st.caption("30-minute cache for price data")

#     st.success("✅ **Optimized Performance**: Local database validation eliminates most API calls!")
#     st.info("• Ticker validation: Instant (local database lookup)")
#     st.info("• Price data: Cached for 30 minutes")
#     st.info("• Only price analysis requires API calls")

close_page()

# Late-injected CSS to override Streamlit's selectbox dropdown (ensures readability on Windows)
st.markdown(
    """
    <style>
    body ul.st-cx.st-al.st-c1[data-testid="stSelectboxVirtualDropdown"],
    body ul.st-cx.st-al[data-testid="stSelectboxVirtualDropdown"],
    body ul[data-testid="stSelectboxVirtualDropdown"].st-cx,
    body ul.st-cx[data-testid="stSelectboxVirtualDropdown"],
    body ul[data-testid="stSelectboxVirtualDropdown"] {
      background: #0F172A !important;
      background-color: #0F172A !important;
      background-image: none !important;
    }
    body ul[data-testid="stSelectboxVirtualDropdown"] li {
      background: transparent !important;
      background-color: transparent !important;
      color: #E5E7EB !important;
      opacity: 1 !important;
    }
    body ul[data-testid="stSelectboxVirtualDropdown"] li:hover {
      background: rgba(56,189,248,.16) !important;
      background-color: rgba(56,189,248,.16) !important;
    }
    body ul[data-testid="stSelectboxVirtualDropdown"] li * {
      color: #E5E7EB !important;
      opacity: 1 !important;
    }

    /* Force Streamlit secondary buttons (Deep Analyze) to match dark theme */
    html body button[data-testid="stBaseButton-secondary"],
    div.stButton > button[kind="secondary"][data-testid="stBaseButton-secondary"],
    .stButton > button[kind="secondary"][data-testid="stBaseButton-secondary"],
    button[kind="secondary"][data-testid="stBaseButton-secondary"] {
      background-color: rgba(15, 23, 42, 0.92) !important;
      color: #E5E7EB !important;
      border: 1px solid rgba(56,189,248,0.28) !important;
      opacity: 1 !important;
      filter: none !important;
      text-shadow: none !important;
    }
    html body button[data-testid="stBaseButton-secondary"]:hover,
    div.stButton > button[kind="secondary"][data-testid="stBaseButton-secondary"]:hover,
    .stButton > button[kind="secondary"][data-testid="stBaseButton-secondary"]:hover,
    button[kind="secondary"][data-testid="stBaseButton-secondary"]:hover {
      /* On hover, match Scan X gradient so it's obviously clickable */
      background-image: linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95)) !important;
      background-color: transparent !important;
      border-color: rgba(56,189,248,.45) !important;
      color: #001018 !important;
    }

    html body button[data-testid="stBaseButton-secondary"]:hover p,
    html body button[data-testid="stBaseButton-secondary"]:hover span {
      color: #001018 !important;
    }

    /* Force Scan X primary button back to gradient */
    html body button[data-testid="stBaseButton-primary"],
    html body .stButton > button[kind="primary"][data-testid="stBaseButton-primary"] {
      background-image: linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95)) !important;
      background-color: transparent !important;
      border: 1px solid rgba(56,189,248,.45) !important;
      color: #001018 !important;
      font-weight: 650 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
