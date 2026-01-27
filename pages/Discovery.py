import streamlit as st
import streamlit.components.v1 as components
import requests
import json
import pandas as pd
from collections import defaultdict
import logging
from utils.navigation import render_sidebar_navigation
from utils.sentiment import extract_tickers, analyze_sentiment
from utils.finance import get_stock_data, get_ticker_master_list
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

st.markdown(
    """
    <style>
    /* --- Global dark theme (TradingView-lite) --- */
    :root {
      --bg: #0B1220;
      --panel: #0F172A;
      --panel2: rgba(15, 23, 42, 0.55);
      --border: rgba(148, 163, 184, 0.18);
      --text: #E5E7EB;
      --muted: #94A3B8;
      --accent: #38BDF8;
      --good: #22C55E;
      --bad: #EF4444;
      --warn: #F59E0B;
    }

    /* Ensure text stays readable on dark background */
    h1, h2, h3, h4, h5, h6, p, span, div, label {
      color: var(--text);
    }
    .stCaption, [data-testid="stCaptionContainer"] {
      color: var(--muted) !important;
    }

    /* Page background */
    [data-testid="stAppViewContainer"] {
      background: radial-gradient(1200px 500px at 20% 0%, rgba(56,189,248,.12), transparent 50%),
                  radial-gradient(900px 400px at 80% 10%, rgba(34,197,94,.10), transparent 45%),
                  var(--bg);
      color: var(--text);
    }

    /* Streamlit sometimes renders select popovers inside the sidebar layer.
       Make sidebar visually neutral/dark so dropdown menus remain readable. */
    section.stSidebar,
    .stSidebar,
    [data-testid="stSidebar"] {
      background-color: #0B1220 !important;
      background: #0B1220 !important;
    }

    /* Hide the top-left sidebar toggle / arrow (collapsed control) */
    [data-testid="collapsedControl"],
    button[title="Open sidebar"],
    button[title="Close sidebar"],
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="stSidebarNavCollapseButton"],
    [data-testid="stSidebarNavExpandButton"] {
      display: none !important;
    }

    /* If Streamlit portals the dropdown into the sidebar, force its surfaces dark */
    .stSidebar ul,
    .stSidebar [role="list"],
    .stSidebar [role="listbox"],
    .stSidebar [data-baseweb="menu"],
    [data-testid="stSidebar"] ul,
    [data-testid="stSidebar"] [role="list"],
    [data-testid="stSidebar"] [role="listbox"],
    [data-testid="stSidebar"] [data-baseweb="menu"] {
      background-color: #0F172A !important;
      color: #E5E7EB !important;
    }

    .stSidebar li,
    .stSidebar li *,
    [data-testid="stSidebar"] li,
    [data-testid="stSidebar"] li * {
      color: #E5E7EB !important;
      opacity: 1 !important;
    }

    /* Main container spacing */
    div[data-testid="stMainBlockContainer"] {
      max-width: 100%;
      padding-left: 2rem;
      padding-right: 2rem;
      padding-top: 0.75rem;
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
      margin: 4px 0 16px 0;
      padding: 6px 2px 2px 2px;
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

st.markdown('<div class="discovery-wrapper">', unsafe_allow_html=True)

st.markdown(
    """
    <div class="hero">
      <div class="hero-eyebrow">Stock Sentinel</div>
      <div class="hero-title">Finding short‑term opportunities shouldn’t feel like a full‑time job.</div>
      <div class="hero-subtitle">We turn noise into signals by analyzing <b>social media sentiment</b> and using <b>AI‑driven market data analysis</b> to validate real momentum.</div>

      <div class="hero-chips">
        <span class="chip"><b>Signal:</b> Buy / Watch / Avoid</span>
        <span class="chip"><b>Projected gain:</b> Estimated range</span>
        <span class="chip"><b>Volatility:</b> Risk level</span>
        <span class="chip"><b>Suggested hold:</b> Days to hold</span>
      </div>

      <div class="hero-caveat">AI‑driven guidance, not guarantees — markets are unpredictable. Always manage risk.</div>
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
st.markdown(
    """
    <div style="
      font-size: 1.05rem;
      line-height: 1.35;
      font-weight: 520;
      color: rgba(229, 231, 235, 0.95);
      margin: 0.25rem 0 0.75rem 0;
      padding: 0.65rem 0.85rem;
      border-left: 3px solid rgba(56, 189, 248, 0.65);
      border-radius: 0.65rem;
      background: rgba(15, 23, 42, 0.35);
    ">
      <span style="font-weight: 750; color: rgba(229, 231, 235, 1);">Pick a sector</span>
      <span> and we turn real‑time market buzz into ranked ticker signals—fast.</span>
    </div>
    """,
    unsafe_allow_html=True,
)

ctrl_left, ctrl_right = st.columns([3, 1.3])

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
    # Align button vertically with the selectbox by adding top padding
    st.markdown("<div style='height: 1.65rem;'></div>", unsafe_allow_html=True)
    scan_clicked = st.button("Sentinel Scan", type="primary", use_container_width=True)

# Scan button
if scan_clicked:
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
                        st.info("📈 Validating tickers using local database and fetching financial data...")

                        # Load comprehensive ticker database
                        ticker_master_list = get_ticker_master_list()

                        if not ticker_master_list:
                            st.error("❌ Could not load ticker database. Please check the data directory.")
                        else:
                            top_tickers = df.head(10)['Ticker'].tolist()  # Check top 10

                            # Add placeholder columns
                            df['Valid'] = False
                            df['Company Name'] = 'N/A'
                            df['Volatility (%)'] = 'N/A'
                            df['Projected Gain (%)'] = 'N/A'
                            df['Current Price ($)'] = 'N/A'
                            df['Suggested Hold (days)'] = 'N/A'

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

                                            # Now make API call only for price data (the expensive part)
                                            logger.info(f"💰 Fetching financial data for {ticker}...")
                                            stock_data = get_stock_data(ticker)

                                            if stock_data['error'] is None and stock_data['prices']:
                                                # Update volatility and current price (show partial data even if projections fail)
                                                df.at[idx, 'Volatility (%)'] = stock_data['volatility']
                                                # Extract the most recent closing price
                                                current_price = stock_data['prices'][-1] if stock_data['prices'] else 'N/A'
                                                df.at[idx, 'Current Price ($)'] = f"{current_price:.2f}" if isinstance(current_price, (int, float)) else 'N/A'
                                                logger.info(f"📈 {ticker}: Volatility calculated - {stock_data['volatility']:.2f}% from {len(stock_data['prices'])} price points, current price: ${current_price:.2f}")

                                                # Try to run projection
                                                avg_sentiment_score = row['Avg Sentiment Score']
                                                logger.info(f"🔮 Running projection for {ticker} (sentiment: {avg_sentiment_score:.3f})...")
                                                try:
                                                    projection = simple_projection(
                                                        stock_data['prices'],
                                                        avg_sentiment_score,
                                                        days=30
                                                    )

                                                    if projection['error'] is None:
                                                        df.at[idx, 'Projected Gain (%)'] = projection['avg_gain']
                                                        df.at[idx, 'Suggested Hold (days)'] = projection['suggested_hold_days']
                                                        logger.info(f"🎯 {ticker}: Projection complete - {projection['avg_gain']:.1f}% gain, hold {projection['suggested_hold_days']} days")
                                                    else:
                                                        # Partial data: we have volatility but projections failed
                                                        validation_errors.append(f"{ticker}: Projection failed - {projection.get('error', 'Unknown')}")
                                                        logger.warning(f"⚠️ {ticker}: Projection failed - {projection.get('error', 'Unknown')}")
                                                except Exception as proj_error:
                                                    # Keep the ticker valid but note projection issue
                                                    validation_errors.append(f"{ticker}: Projection error - {str(proj_error)[:50]}")
                                                    logger.warning(f"⚠️ {ticker}: Projection error - {str(proj_error)[:50]}")
                        # Filter to show only validated tickers (with financial data)
                        df_valid = df[df['Valid'] == True].copy()
                        df_valid = df_valid.drop(columns=['Valid'])

                        # Also show unvalidated tickers separately
                        df_unvalidated = df[df['Valid'] == False].copy()
                        df_unvalidated = df_unvalidated.drop(columns=['Valid', 'Company Name', 'Volatility (%)', 'Projected Gain (%)', 'Current Price ($)', 'Suggested Hold (days)'])

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
            st.error(f"❌ API Error ({response.status_code}): {response.text}")

    except KeyError:
        st.error("❌ Missing X_BEARER_TOKEN in .streamlit/secrets.toml")
        st.info("Please add your X Bearer Token to the secrets file.")

    except requests.exceptions.RequestException as e:
        st.error(f"❌ Network Error: {str(e)}")
        st.info("Please check your internet connection and try again.")

    except Exception as e:
        st.error(f"❌ Unexpected Error: {str(e)}")

# KPI strip (UI only)
if st.session_state.df_valid is not None:
    try:
        dfv = st.session_state.df_valid
        dfn = st.session_state.df_unvalidated

        total_valid = int(len(dfv)) if dfv is not None else 0
        total_other = int(len(dfn)) if dfn is not None else 0
        total_unique = total_valid + total_other
        tweets_analyzed = int(dfv["Mentions"].sum()) if (dfv is not None and "Mentions" in dfv.columns) else 0
        avg_sent = float(dfv["Avg Sentiment Score"].mean()) if (dfv is not None and "Avg Sentiment Score" in dfv.columns and len(dfv) > 0) else 0.0

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Tweets analyzed", tweets_analyzed)
        k2.metric("Unique tickers", total_unique)
        k3.metric("Validated", total_valid)
        k4.metric("Avg sentiment", f"{avg_sent:.3f}")
        st.markdown("", unsafe_allow_html=True)
    except Exception:
        # Never let UI extras break the page
        pass

if st.session_state.df_valid is not None:
    df_valid_display = st.session_state.df_valid.drop(columns=["Mentions", "Sample Tweets"], errors="ignore")

    if len(df_valid_display) > 0:
        st.subheader("✅ Validated Stocks with Financial Data")
        header_cols = st.columns([1.1, 1.6, 1.2, 1.1, 1.1, 1.1, 1.2, 1.0, 1.0])
        header_labels = [
            "Ticker",
            "Company",
            "Avg Sentiment",
            "Overall",
            "Volatility",
            "Projected Gain",
            "Current Price",
            "Hold (days)",
            "Deep Analyze"
        ]
        for col, label in zip(header_cols, header_labels):
            col.markdown(f"**{label}**")

        for _, row in df_valid_display.iterrows():
            ticker_symbol = row["Ticker"]
            company_name = row["Company Name"]
            sentiment_score = row["Avg Sentiment Score"]
            overall_sentiment = row["Overall Sentiment"]
            volatility = row["Volatility (%)"]
            projected_gain = row["Projected Gain (%)"]
            current_price = row["Current Price ($)"]
            hold_days = row["Suggested Hold (days)"]

            st.markdown("<div class='ticker-row'>", unsafe_allow_html=True)
            col1, col2, col3, col4, col5, col6, col7, col8, col9 = st.columns(
                [1.1, 1.6, 1.2, 1.1, 1.1, 1.1, 1.2, 1.0, 1.0]
            )
            with col1:
                st.markdown(f"**{ticker_symbol}**")
            with col2:
                st.markdown(company_name)
            with col3:
                st.markdown(sentiment_score)
            with col4:
                st.markdown(overall_sentiment)
            with col5:
                st.markdown(volatility)
            with col6:
                st.markdown(projected_gain)
            with col7:
                st.markdown(current_price)
            with col8:
                st.markdown(hold_days)
            with col9:
                if st.button("Deep Analyze", key=f"deep_analyze_{ticker_symbol}"):
                    st.session_state.selected_ticker = ticker_symbol
                    with st.spinner(f"Running deep analysis for {ticker_symbol}..."):
                        st.session_state.deep_analysis_results = run_deep_analysis(
                            ticker_symbol,
                            st.session_state.selected_sector,
                        )
            st.markdown("</div>", unsafe_allow_html=True)

        st.success(f"📊 {len(df_valid_display)} validated stock(s) with complete financial analysis")
    else:
        st.warning("⚠️ No valid stock tickers found with financial data.")

if st.session_state.selected_ticker and st.session_state.deep_analysis_results:
    st.markdown("---")
    st.subheader(
        f"🧠 Deep Analysis for {st.session_state.selected_ticker} ({st.session_state.selected_sector})"
    )
    ai_summary = generate_ai_summary(st.session_state.deep_analysis_results)

    col1, col2, col3 = st.columns([1.2, 1, 2])
    with col1:
        st.metric("Recommendation", ai_summary["recommendation"])
    with col2:
        st.metric("Confidence", ai_summary["confidence"])
    with col3:
        st.metric("Weighted Sentiment", f"{ai_summary['avg_sentiment']:.3f}")

    st.markdown("**Rationale:**")
    for bullet in ai_summary["rationale"]:
        st.markdown(f"- {bullet}")

    with st.expander("📦 Full Analysis Details", expanded=False):
        summary_rows = []
        for prompt_name, result in st.session_state.deep_analysis_results.items():
            summary_rows.append({
                "Analysis Type": prompt_name,
                "Sentiment Score": result["sentiment_score"],
                "Overall Sentiment": result["overall_sentiment"],
                "Mentions": result["mention_count"],
                "Key Themes": ", ".join(result["key_themes"]) if result["key_themes"] else "None",
                "Catalysts": "Check insights below",
                "Risks": "Check insights below"
            })

        df_summary = pd.DataFrame(summary_rows)
        st.dataframe(
            df_summary,
            column_config={
                "Analysis Type": st.column_config.TextColumn("Analysis Type", width="medium"),
                "Sentiment Score": st.column_config.NumberColumn("Sentiment Score", format="%.3f"),
                "Overall Sentiment": st.column_config.TextColumn("Overall Sentiment", width="small"),
                "Mentions": st.column_config.NumberColumn("Mentions", width="small"),
                "Key Themes": st.column_config.TextColumn("Key Themes", width="medium"),
                "Catalysts": st.column_config.TextColumn("Catalysts", width="medium"),
                "Risks": st.column_config.TextColumn("Risks", width="medium")
            },
            hide_index=True,
            use_container_width=True
        )

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

st.markdown("</div>", unsafe_allow_html=True)

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
