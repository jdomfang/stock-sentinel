"""
Sentiment analysis module.
"""

import os
import re
from typing import List, Dict
import streamlit as st
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


@st.cache_resource(show_spinner=False)
def load_sentiment_pipeline():
    """Load and cache the sentiment analysis pipeline.

    NOTE: We import transformers lazily here so the app can boot even if
    transformers/torch cannot be installed on a given platform.
    """
    # Silence torch/transformers internal noise before importing them
    import logging as _logging
    for _noisy in ("transformers", "torch", "torch.classes", "torch._classes"):
        _logging.getLogger(_noisy).setLevel(_logging.ERROR)

    try:
        from transformers import pipeline  # type: ignore
        # Also suppress transformers pipeline init chatter
        _logging.getLogger("transformers.pipelines").setLevel(_logging.ERROR)
        _logging.getLogger("transformers.modeling_utils").setLevel(_logging.ERROR)
    except Exception as e:
        raise RuntimeError(
            "Sentiment model dependencies are not available in this environment. "
            "Please ensure 'transformers' and 'torch' are installed."
        ) from e

    return pipeline(
        "sentiment-analysis",
        model="ProsusAI/finbert",
        device=-1,  # Use CPU
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


_NEUTRAL_RESULT = {
    'label': 'UNKNOWN',
    'confidence': 0.0,
    'score': 0.0,
    'sentiment': 'Neutral',
}


def _inference_config() -> tuple[str, str]:
    """(url, secret) for the inference service, or ('', '') when not configured.

    Environment first so a container can inject it; Streamlit secrets as a
    fallback so the portal works unchanged. Reading is cheap and per-call, which
    means the service can be turned on or off without a redeploy -- the property
    that makes this rollout reversible in seconds rather than a build cycle.
    """
    url = os.getenv("INFERENCE_URL", "")
    secret = os.getenv("INFERENCE_SHARED_SECRET", "")
    if not url:
        try:
            import streamlit as st
            url = str(st.secrets.get("INFERENCE_URL", "") or "")
            secret = secret or str(st.secrets.get("INFERENCE_SHARED_SECRET", "") or "")
        except Exception:
            pass
    return url.rstrip("/"), secret


def _remote_scorer():
    """Return a callable that scores texts remotely, or None if not configured.

    The callable returns None on failure rather than raising, so the caller
    decides what to do -- during the rollout that means falling back to local,
    and after torch is removed it means the scan fails and refunds.
    """
    url, secret = _inference_config()
    if not url:
        return None

    def _call(texts: List[str]):
        import json as _json
        import urllib.error
        import urllib.request

        # 30s, and one retry. 8s was too tight: measured against the deployed
        # service, the FIRST request after the container has been idle exceeds it
        # and times out, while the very next one succeeds in under a second.
        # While torch is installed that only causes a silent fall back to local
        # scoring -- the service is paid for and unused. Once torch is removed it
        # would refund a scan that was about to work.
        #
        # A scan already spends 15-30s on the X API, so 30s here does not change
        # what the user experiences; it just stops a cold container being
        # mistaken for a broken one.
        req = urllib.request.Request(
            f"{url}/score",
            data=_json.dumps({"texts": texts}).encode(),
            headers={"Content-Type": "application/json", "X-Inference-Secret": secret},
            method="POST",
        )
        last_err = None
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = _json.loads(resp.read() or b"{}")
                results = payload.get("results")
                if not isinstance(results, list):
                    logger.error("inference returned no results list")
                    return None
                return results
            except urllib.error.HTTPError as e:
                # 4xx is a real rejection -- a bad secret or an oversized batch.
                # Retrying cannot help and would double the latency of a
                # misconfiguration, so fail fast.
                logger.error("inference HTTP %s: %s", e.code, e.read()[:200])
                return None
            except Exception as e:
                last_err = e
                if attempt == 1:
                    logger.warning("inference attempt 1 failed (%s); retrying",
                                   type(e).__name__)
        logger.error("inference call failed after 2 attempts: %s: %s",
                     type(last_err).__name__, str(last_err)[:160])
        return None

    return _call


def analyze_sentiment_batch(texts: List[str], batch_size: int = 24) -> List[Dict[str, any]]:
    """Score many texts at once. Same output shape and order as analyze_sentiment.

    Discovery scored tweets one at a time -- up to ~500 sequential single-item
    forward passes per scan -- while deep_analysis already batched. Same model,
    same box. On Streamlit Cloud's ~1GB tier that is the difference between a
    scan completing and the process being killed mid-loop, which is exactly what
    happened on 2026-08-01: the log stops mid-sentiment with no traceback, the
    user's credit is spent, and nothing is delivered.

    Two savings, neither of which changes a single output value:

      * batching -- the HF pipeline takes a list and vectorizes the forward
        pass, so N texts cost far less than N separate calls;
      * dedup -- FinBERT is deterministic, so scoring an identical string twice
        is pure waste. Sector scans are full of syndicated press-release copy;
        one healthcare scan scored the same BioMedNewsBreaks post five times in
        half a second. Callers still see one result per input, so mention counts
        are unaffected -- duplicates share a forward pass, they do not merge.

    Returns exactly len(texts) dicts, in input order.
    """
    if not texts:
        return []

    # Preserve the per-call truncation so batched and unbatched scoring agree.
    prepared = [(t or "")[:512] for t in texts]

    # Unique texts, first-seen order. dict preserves insertion order.
    uniq: Dict[str, int] = {}
    for t in prepared:
        if t not in uniq:
            uniq[t] = len(uniq)
    unique_texts = list(uniq.keys())

    # Remote when configured, in-process otherwise. Both produce identical
    # results -- the service reuses the same 0.55 threshold and the same
    # truncation -- so the rollout is a config change, reversible without a
    # deploy. The memory win does NOT arrive until torch and transformers leave
    # requirements.txt; until then the portal still loads 886MB whether or not
    # it uses it.
    remote = _remote_scorer()
    if remote is not None:
        scored = remote(unique_texts)
        if scored is not None:
            if len(scored) != len(unique_texts):
                logger.error("inference returned %d results for %d texts; using neutral",
                             len(scored), len(unique_texts))
                return [dict(_NEUTRAL_RESULT) for _ in prepared]
            logger.info(
                "🧠 Sentiment batch (remote): %d texts, %d unique (%d duplicate passes avoided)",
                len(prepared), len(unique_texts), len(prepared) - len(unique_texts),
            )
            return [dict(scored[uniq[t]]) for t in prepared]
        # remote returned None: it failed and said so. Fall through to local,
        # which is correct DURING the rollout while torch is still installed.
        # Once torch is removed this path raises ImportError and the caller
        # refunds -- which is the intended end state: a failure the caller can
        # act on, rather than a silently degraded scan.
        logger.warning("inference service unavailable; scoring locally")

    scored: List[Dict[str, any]] = []
    try:
        sentiment_pipeline = load_sentiment_pipeline()
        for i in range(0, len(unique_texts), batch_size):
            chunk = unique_texts[i:i + batch_size]
            for out in sentiment_pipeline(chunk):
                label = out.get('label')
                confidence = float(out.get('score', 0.0))
                signed_score, trading_sentiment, label_norm = score_finbert_output(
                    label=label, confidence=confidence, neutral_threshold=0.55,
                )
                scored.append({
                    'label': label_norm,
                    'confidence': confidence,
                    'score': signed_score,
                    'sentiment': trading_sentiment,
                })
    except Exception as e:
        # A failed batch must not zero out the whole page. Fall back to the
        # per-text path, which has its own error handling and degrades one
        # result at a time instead of all of them.
        logger.warning(
            "batch sentiment failed (%s: %s); falling back to per-text scoring",
            type(e).__name__, str(e)[:160],
        )

        # But FIRST check the local model is actually usable. analyze_sentiment
        # swallows its own errors and returns Neutral, so without this probe the
        # combination "remote unreachable + torch not installed" produces a full
        # page of Neutral(0.00) -- a scan that charges a credit, completes
        # normally, and is indistinguishable from a sector with no real signal.
        # Silent degradation dressed as success is the exact failure this
        # codebase has spent its effort removing; scoring nothing must be an
        # ERROR the caller can refund, not a result.
        try:
            load_sentiment_pipeline()
        except Exception as local_err:
            raise RuntimeError(
                "Sentiment scoring unavailable: the inference service failed "
                f"({type(e).__name__}) and no local model is installed "
                f"({type(local_err).__name__})"
            ) from local_err

        scored = [analyze_sentiment(t) for t in unique_texts]

    # A short batch would silently misalign results with inputs. Fail closed.
    if len(scored) != len(unique_texts):
        logger.error("batch sentiment returned %d results for %d texts; using neutral",
                     len(scored), len(unique_texts))
        return [dict(_NEUTRAL_RESULT) for _ in prepared]

    # One log line per call rather than three per tweet. The old per-tweet DEBUG
    # logging flooded Streamlit Cloud's buffer and truncated away the context
    # that mattered when a scan actually died.
    logger.info(
        "🧠 Sentiment batch: %d texts, %d unique (%d duplicate passes avoided)",
        len(prepared), len(unique_texts), len(prepared) - len(unique_texts),
    )

    return [dict(scored[uniq[t]]) for t in prepared]
