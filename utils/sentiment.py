"""
Sentiment analysis module.
"""

import re
from typing import List, Dict
import streamlit as st
from transformers import pipeline
import logging

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Debug level for detailed ticker/sentiment logs


# Common words to exclude from ticker detection
EXCLUDED_WORDS = {
    # Common English words
    'AND', 'THE', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN', 'HER',
    'WAS', 'ONE', 'OUR', 'OUT', 'DAY', 'GET', 'HAS', 'HIM', 'HIS', 'HOW',
    'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'SEE', 'TWO', 'WHO', 'BOY', 'DID',
    'SHE', 'TOO', 'USE', 'BAD', 'GOOD', 'THIS', 'THAT', 'THAN', 'THEM',
    'BEEN', 'FROM', 'HAVE', 'HERE', 'MORE', 'VERY', 'WELL', 'WHAT', 'WHEN',
    'WITH', 'WILL', 'YOUR', 'ABOUT', 'AFTER', 'COULD', 'EVERY', 'FIRST',
    'GOING', 'OTHER', 'SHOULD', 'THEIR', 'THERE', 'THESE', 'THEY', 'WHICH',
    'WOULD', 'BECAUSE', 'BETWEEN', 'WITHOUT', 'HOLD', 'MAKE', 'BACK', 'TAKE',
    'EACH', 'EVEN', 'JUST', 'LIKE', 'MUCH', 'MANY', 'MOST', 'SOME', 'SUCH',
    'ONLY', 'INTO', 'OVER', 'BOTH', 'ALSO', 'THEN', 'ONCE', 'SAME', 'BEEN',
    'SAID', 'MADE', 'LONG', 'HIGH', 'COME', 'BEST', 'LAST', 'LOOK', 'DOWN',
    # Currencies (major issue for false positives)
    'USD', 'EUR', 'GBP', 'JPY', 'CNY', 'INR', 'CHF', 'AUD', 'CAD', 'NZD',
    'HKD', 'SGD', 'SEK', 'NOK', 'DKK', 'ZAR', 'MXN', 'BRL', 'RUB', 'KRW',
    # Commodities and materials
    'GOLD', 'IRON', 'COAL', 'WOOD', 'SALT', 'ZINC', 'LEAD', 'TIN', 'SAND',
    'OIL', 'GAS', 'CORN', 'RICE', 'WHEAT', 'SOYA', 'WOOL', 'SILK', 'HEMP',
    # Common trading/finance terms
    'SELL', 'LONG', 'CALL', 'PUT', 'BEAR', 'BULL', 'GAIN', 'LOSS', 'RISK',
    'RATE', 'BOND', 'DEBT', 'LOAN', 'FEES', 'FUND', 'CASH', 'CARD', 'BANK',
    # Tech/Finance abbreviations
    'AI', 'API', 'CEO', 'CFO', 'CTO', 'NFT', 'VC', 'IPO', 'ETH', 'BTC',
    'DM', 'PM', 'AM', 'IO', 'XYZ', 'APP', 'WEB', 'PDF', 'URL', 'HTTP',
    # News organizations and media
    'WSJ', 'CNN', 'BBC', 'NBC', 'ABC', 'CBS', 'NYT', 'FOX', 'CNBC', 'MSNBC',
    'ESPN', 'HBO', 'MTV', 'PBS', 'NPR',
    # Government/Organizations
    'USA', 'FBI', 'CIA', 'NSA', 'FDA', 'SEC', 'IRS', 'EPA', 'NASA', 'FEMA',
    'DOJ', 'DOD', 'HHS', 'UN', 'NATO', 'WHO', 'IMF', 'WTO', 'OECD',
    # Countries and regions
    'US', 'UK', 'EU', 'JP', 'CN', 'IN', 'DE', 'FR', 'IT', 'ES', 'CA', 'MX',
    'BR', 'RU', 'KR', 'AU', 'NZ', 'SG', 'HK', 'TW', 'PH', 'ID', 'TH', 'VN',
    # US States (common abbreviations)
    'AL', 'AK', 'AZ', 'AR', 'CO', 'CT', 'DC', 'FL', 'GA', 'HI', 'ID', 'IL',
    'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT',
    'NE', 'NV', 'NH', 'NJ', 'NM', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI',
    'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
    # Tech events and conferences
    'CES', 'MWC', 'GTC', 'AWS', 'WWDC', 'SXSW',
    # Other common abbreviations
    'CEO', 'CTO', 'COO', 'VP', 'SVP', 'EVP', 'HR', 'IT', 'PR', 'QA',
    'UI', 'UX', 'AR', 'VR', 'MR', 'XR', 'ML', 'NLP', 'IOT', 'SAS', 'ERP', 'CRM',
    'FAQ', 'FYI', 'ASAP', 'ETA', 'ROI', 'KPI', 'EBITDA', 'GDP', 'CPI', 'PPI',
    # Days and time
    'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN', 'JAN', 'FEB', 'MAR',
    'APR', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC',
    # Rare/problematic tickers to exclude
    'WNC', 'USA', 'ALLY',  # Often captured but need explicit validation
    # International market indices and exchanges
    'NSE', 'BSE', 'NIFTY', 'SENSEX', 'FTSE', 'DAX', 'CAC', 'NIKKEI', 'HSI', 'SSE',
    # Common international company tickers (Indian market examples)
    'RIL', 'JSW', 'TCS', 'INFY', 'HDFC', 'ICICI', 'LT', 'BAJAJ', 'MARUTI', 'ITC',
    'HINDALCO', 'TATA', 'ADANI', 'RELIANCE', 'WIPRO', 'TECHM', 'HCLTECH', 'KOTAK',
    'AXISBANK', 'SBI', 'PNB', 'BOB', 'CANBANK', 'IDBI', 'UNIONBANK', 'INDUSINDBK'
}


@st.cache_resource
def load_sentiment_pipeline():
    """
    Load and cache the sentiment analysis pipeline.
    Using a lightweight model for better performance.
    """
    return pipeline(
        "sentiment-analysis",
        model="ProsusAI/finbert",
        device=-1  # Use CPU
    )


def extract_tickers(text: str) -> List[str]:
    """
    Extract potential stock tickers from text using improved filtering.
    Prioritizes $-prefixed tickers (explicit stock mentions) over bare uppercase words.
    Applies length-based filtering to prefer 3-4 letter tickers.

    Args:
        text: The text to extract tickers from (e.g., tweet text)

    Returns:
        List of unique potential stock ticker symbols
    """
    tickers = []
    seen = set()

    # Step 1: Extract $-prefixed tickers (highest priority - explicit mentions)
    dollar_prefixed_pattern = r'\$([A-Z]{2,5})\b'
    dollar_matches = re.findall(dollar_prefixed_pattern, text)

    for match in dollar_matches:
        if match not in EXCLUDED_WORDS and match not in seen:
            tickers.append(match)
            seen.add(match)
            logger.debug(f"💰 Found $-prefixed ticker: ${match}")

    # Step 2: Extract bare uppercase tickers with stricter filtering
    bare_ticker_pattern = r'\b([A-Z]{2,5})\b'
    bare_matches = re.findall(bare_ticker_pattern, text)

    # Score bare tickers by desirability (prefer 3-4 letters)
    scored_tickers = []
    for match in bare_matches:
        if match not in EXCLUDED_WORDS and match not in seen:
            # Prefer 3-4 letter tickers, penalize very short (2) or long (5) ones
            length_score = 1.0 if 3 <= len(match) <= 4 else 0.5
            scored_tickers.append((match, length_score))

    # Sort by score (higher first) and add top candidates
    scored_tickers.sort(key=lambda x: x[1], reverse=True)

    # Add top bare tickers (limit to avoid too many false positives)
    for ticker, score in scored_tickers[:5]:  # Max 5 additional bare tickers
        tickers.append(ticker)
        seen.add(ticker)
        logger.debug(f"📈 Added bare ticker: {ticker} (score: {score})")

    logger.debug(f"📝 Final extracted tickers: {tickers}")
    return tickers


def score_finbert_output(label: str, confidence: float, neutral_threshold: float = 0.55) -> tuple[float, str, str]:
    """Map FinBERT output -> (signed_score, trading_sentiment, normalized_label).

    - signed_score in [-1, 1]
    - trading_sentiment in {Bullish, Bearish, Neutral}
    - normalized_label in {POSITIVE, NEGATIVE, NEUTRAL, UNKNOWN}
    """
    label_norm = str(label).strip().upper() if label is not None else "UNKNOWN"
    conf = float(confidence or 0.0)

    if conf < neutral_threshold or label_norm == "NEUTRAL":
        return 0.0, "Neutral", "NEUTRAL" if label_norm != "UNKNOWN" else "UNKNOWN"

    if label_norm == "POSITIVE":
        return conf, "Bullish", "POSITIVE"

    if label_norm == "NEGATIVE":
        return -conf, "Bearish", "NEGATIVE"

    return 0.0, "Neutral", "UNKNOWN"


def analyze_sentiment(text: str) -> Dict[str, any]:
    """
    Analyze the sentiment of text using a pre-trained model.
    
    Args:
        text: The text to analyze
        
    Returns:
        Dictionary with keys:
        - label: 'POSITIVE' or 'NEGATIVE'
        - score: confidence score (0-1)
        - sentiment: 'Bullish', 'Bearish', or 'Neutral'
    """
    try:
        # Load cached pipeline
        sentiment_pipeline = load_sentiment_pipeline()
        
        # Truncate text if too long (model has token limits)
        max_length = 512
        if len(text) > max_length:
            text = text[:max_length]
        
        # Get sentiment
        result = sentiment_pipeline(text)[0]
        
        label = result.get('label')
        confidence = float(result.get('score', 0.0))

        signed_score, trading_sentiment, label_norm = score_finbert_output(
            label=label,
            confidence=confidence,
            neutral_threshold=0.55,
        )

        logger.debug(
            f"😊 Sentiment analysis: '{text[:50]}...' -> {label}:{confidence:.3f} -> {trading_sentiment} (signed={signed_score:.3f})"
        )

        return {
            'label': label_norm,
            'confidence': confidence,
            'score': signed_score,
            'sentiment': trading_sentiment,
        }
        
    except Exception as e:
        # Return neutral sentiment on error
        return {
            'label': 'UNKNOWN',
            'confidence': 0.0,
            'score': 0.0,
            'sentiment': 'Neutral',
            'error': str(e)
        }
