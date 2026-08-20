"""
Sentiment analysis module.
"""

import os
import re
from typing import List, Dict
import logging

# Set up logging
from utils.cache import singleton
from utils.config import get as _config

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


# Was @st.cache_resource. Same lifetime for a nullary function, and
# single-flight so two threads cannot both load ~1 GB of model.
@singleton
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


MAX_BARE_TICKERS_PER_POST = 5


def extract_tickers_detailed(text: str) -> Dict[str, any]:
    """Extract tickers AND report where each one came from.

    Same algorithm as extract_tickers has always used -- this IS that
    implementation, and extract_tickers is now a one-line wrapper over it, so
    the two cannot drift apart. What is new is that the caller can tell a
    CASHTAG match ($CAT, unambiguous) from a BARE uppercase match (CAT, which
    is also an English word).

    That distinction is the whole point. 17 of 20 ordinary words tested are
    real listed symbols -- AIR is AAR Corp, RAIL is FreightCar America, BOOM is
    DMC Global -- and all three validate as Industrials, so a post reading
    "RAIL AND AIR FREIGHT VOLUMES RISING" can produce two fake recommendations
    that are indistinguishable from real ones in the UI. Provenance is what
    makes them countable, and a symbol that NEVER appears with a $ anywhere in
    a corpus is a strong phantom suspect.

    Returns:
        cashtag           distinct $-prefixed symbols, first-seen order
        bare              distinct bare-word symbols that survived the top-N cut
        cashtag_counts    {symbol: occurrences} across the whole text
        bare_counts       {symbol: occurrences} within the returned slice
        legacy            the EXACT list extract_tickers has always returned
    """
    tickers: List[str] = []
    seen = set()
    cashtag_counts: Dict[str, int] = {}

    # Step 1: Extract $-prefixed tickers (highest priority - explicit mentions)
    #
    # ONE character minimum, not two. ticker_master holds 21 single-letter
    # symbols -- $F (Ford), $T (AT&T), $V (Visa), $C (Citi), $D (Dominion) --
    # and the old {2,5} bound made every one of them unextractable. Measured on
    # a live utilities corpus: $D was in the query we sent, X returned posts
    # mentioning it, and extraction silently dropped them. The scan paid for
    # those posts and threw them away.
    #
    # Safe to widen HERE because a cashtag is an explicit claim that the token
    # is a ticker, and validation still requires a ticker_master hit in the
    # right sector. "$5B" and "$10M" cannot match -- the character after $ is a
    # digit, so the pattern does not engage.
    #
    # NOT widened for bare words below: at one character every "A" and "I" in
    # ordinary English would become a ticker candidate.
    #
    # Upper bound stays 5. The 73 symbols longer than that are preferred shares
    # written like AHRT^A, which contain a caret and cannot be matched by an
    # [A-Z] class at any length.
    dollar_prefixed_pattern = r'\$([A-Z]{1,5})\b'
    dollar_matches = re.findall(dollar_prefixed_pattern, text)

    for match in dollar_matches:
        if match in EXCLUDED_WORDS:
            continue
        cashtag_counts[match] = cashtag_counts.get(match, 0) + 1
        if match not in seen:
            tickers.append(match)
            seen.add(match)
            logger.debug(f"💰 Found $-prefixed ticker: ${match}")

    cashtags = list(tickers)

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

    # Sort by score (higher first) and add top candidates. Stable, so equal
    # scores keep first-seen order.
    scored_tickers.sort(key=lambda x: x[1], reverse=True)

    # KNOWN DEFECT, PRESERVED DELIBERATELY.
    #
    # The scoring loop above tests `match not in seen` but never ADDS to seen,
    # so a bare word repeated in one post appears in scored_tickers repeatedly
    # and is appended here repeatedly:
    #
    #   "RAIL and AIR rising. RAIL volumes up, AIR up, RAIL again."
    #     -> ['RAIL', 'AIR', 'RAIL', 'AIR', 'RAIL']
    #
    # pages/Discovery.py does `ticker_data[t]['mentions'] += 1` per element, so
    # repetition inflates both the mention count and the sentiment sample --
    # and mentions drive the validation ranking. Repeating a bare word promotes
    # that ticker. Cashtags are immune, because Step 1 dedupes immediately, so
    # the inflation applies ONLY to the path that also generates phantoms.
    #
    # It is NOT fixed here on purpose: this change exists to MEASURE the
    # pipeline, and a measurement pass that silently alters ranking would make
    # its own baseline unreadable. bare_counts below records the duplication so
    # it is visible in the metrics, and fixing it is a separate, deliberate
    # change with its own before/after.
    bare_counts: Dict[str, int] = {}
    bare_distinct: List[str] = []
    for ticker, score in scored_tickers[:MAX_BARE_TICKERS_PER_POST]:
        tickers.append(ticker)
        seen.add(ticker)
        bare_counts[ticker] = bare_counts.get(ticker, 0) + 1
        if ticker not in bare_distinct:
            bare_distinct.append(ticker)
        logger.debug(f"📈 Added bare ticker: {ticker} (score: {score})")

    logger.debug(f"📝 Final extracted tickers: {tickers}")
    return {
        "cashtag": cashtags,
        "bare": bare_distinct,
        "cashtag_counts": cashtag_counts,
        "bare_counts": bare_counts,
        "legacy": tickers,
    }


def extract_tickers(text: str) -> List[str]:
    """
    Extract potential stock tickers from text using improved filtering.
    Prioritizes $-prefixed tickers (explicit stock mentions) over bare uppercase words.
    Applies length-based filtering to prefer 3-4 letter tickers.

    Args:
        text: The text to extract tickers from (e.g., tweet text)

    Returns:
        List of potential stock ticker symbols. NOTE: may contain duplicates
        when a bare word repeats -- see the defect note in
        extract_tickers_detailed, whose `legacy` field this returns verbatim.
    """
    return extract_tickers_detailed(text)["legacy"]


def _normalise_label(label) -> str:
    """Model label -> {POSITIVE, NEGATIVE, NEUTRAL, UNKNOWN}.

    Different sentiment models name the same three classes differently.
    FinBERT says positive/neutral/negative; FinTwitBERT, trained on financial
    Twitter rather than financial news, says BULLISH/NEUTRAL/BEARISH.

    Without this, swapping MODEL_NAME to a better model would map every label
    to UNKNOWN, score every post 0.0, and make the app report "not enough clean
    evidence about this company" for every ticker on earth -- silently, with no
    error anywhere. The scores would look like an honest finding.
    """
    s = str(label).strip().upper() if label is not None else ""
    if s in ("POSITIVE", "BULLISH", "POS", "LABEL_2"):
        return "POSITIVE"
    if s in ("NEGATIVE", "BEARISH", "NEG", "LABEL_0"):
        return "NEGATIVE"
    if s in ("NEUTRAL", "NEU", "LABEL_1"):
        return "NEUTRAL"
    return "UNKNOWN"


def score_finbert_output(label: str, confidence: float, neutral_threshold: float = 0.55) -> tuple[float, str, str]:
    """Map a sentiment model's output -> (signed_score, trading_sentiment, normalized_label).

    - signed_score in [-1, 1]
    - trading_sentiment in {Bullish, Bearish, Neutral}
    - normalized_label in {POSITIVE, NEGATIVE, NEUTRAL, UNKNOWN}
    """
    label_norm = _normalise_label(label)
    conf = float(confidence or 0.0)

    if label_norm == "UNKNOWN":
        # LOUD, not silent. An unrecognised label means the model was changed
        # without teaching this function its vocabulary, and the alternative --
        # returning a confident 0.0 -- is indistinguishable from a genuinely
        # neutral corpus.
        logger.error("unrecognised sentiment label %r; scoring as UNKNOWN. "
                     "Add it to _normalise_label.", label)
        return 0.0, "Neutral", "UNKNOWN"

    if conf < neutral_threshold or label_norm == "NEUTRAL":
        return 0.0, "Neutral", "NEUTRAL"

    if label_norm == "POSITIVE":
        return conf, "Bullish", "POSITIVE"

    return -conf, "Bearish", "NEGATIVE"


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


# Part of the sentiment-cache key. A stored distribution belongs to the model
# that produced it, so swapping this must miss every cached row rather than
# serve the previous model's opinions. Matches inference/main.py's default.
MODEL_NAME = os.getenv("MODEL_NAME", "ProsusAI/finbert")

_NEUTRAL_RESULT = {
    'label': 'UNKNOWN',
    'confidence': 0.0,
    'score': 0.0,
    'sentiment': 'Neutral',
    'p_positive': 0.0,
    'p_negative': 0.0,
    'p_neutral': 0.0,
    'margin': 0.0,
}

# Every result carries the full class distribution. Callers that aggregate
# should prefer `margin` (P_positive - P_negative) over `sentiment`: it is
# continuous, so it cannot tie, and it distinguishes a 0.48/0.44 coin-flip from
# a 0.90/0.05 conviction that the discrete label collapses into one bucket.
#
# Defaulted rather than required, because an inference service deployed before
# this change returns only {label, confidence, score, sentiment}. A missing
# distribution degrades to margin 0.0 (reads as Neutral) instead of a KeyError
# mid-scan.
_DIST_KEYS = ('p_positive', 'p_negative', 'p_neutral', 'margin')


def _with_distribution(d: dict) -> dict:
    out = dict(d)
    for k in _DIST_KEYS:
        out.setdefault(k, 0.0)
    return out


def _inference_config() -> tuple[str, str]:
    """(url, secret) for the inference service, or ('', '') when not configured.

    Environment first so a container can inject it; Streamlit secrets as a
    fallback so the portal works unchanged. Reading is cheap and per-call, which
    means the service can be turned on or off without a redeploy -- the property
    that makes this rollout reversible in seconds rather than a build cycle.
    """
    # _config already does env-then-secrets, so the hand-rolled two-step here
    # was redundant -- and its `except Exception: pass` was the same swallowed
    # import that silently changed a decision threshold elsewhere in this repo.
    #
    # ONE BEHAVIOUR CHANGE, and it is a fix. At HEAD the shared secret was read
    # from the environment only, and from st.secrets ONLY IF the URL was also
    # missing from the environment. So a deployment with the URL in env and the
    # secret in secrets.toml sent an empty secret and got a 401. Both are now
    # read independently.
    url = _config("INFERENCE_URL")
    secret = _config("INFERENCE_SHARED_SECRET")
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

    # ALREADY-SCORED TEXT IS NOT RE-SCORED.
    #
    # FinBERT is deterministic, so a stored distribution for the identical
    # string at the identical model version IS the answer. Dedup above only
    # covers repeats within this one call; this covers repeats across pages,
    # across scans, and across Deep Analyze's keyword buckets. A repeat sector
    # scan inside the corpus-cache window previously cost zero X posts and still
    # paid the full inference bill re-scoring the posts it had just replayed.
    #
    # Never raises: an unreachable cache returns {} and everything is scored.
    from utils import sentiment_cache as _sc
    _cached = _sc.get_many(unique_texts, MODEL_NAME)
    _to_score = [t for t in unique_texts if t not in _cached]

    # Remote when configured, in-process otherwise. Both produce identical
    # results -- the service reuses the same 0.55 threshold and the same
    # truncation -- so the rollout is a config change, reversible without a
    # deploy.
    remote = _remote_scorer()
    if remote is not None:
        scored = remote(_to_score) if _to_score else []
        if scored is not None:
            if len(scored) != len(_to_score):
                logger.error("inference returned %d results for %d texts; using neutral",
                             len(scored), len(_to_score))
                return [dict(_NEUTRAL_RESULT) for _ in prepared]
            fresh = {t: d for t, d in zip(_to_score, scored)}
            if fresh:
                _sc.put_many(fresh, MODEL_NAME)
            by_text = {**_cached, **fresh}
            logger.info(
                "🧠 Sentiment batch (remote): %d texts, %d unique, %d cached, %d scored",
                len(prepared), len(unique_texts), len(_cached), len(_to_score),
            )
            return [_with_distribution(by_text[t]) for t in prepared]
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
                # The local path exists only for a torch-installed dev box; it
                # has no distribution to report, so callers see margin 0.0 and
                # fall back to the discrete label. Not worth widening: the
                # portal has no torch, so this never runs in production.
                scored.append(_with_distribution({
                    'label': label_norm,
                    'confidence': confidence,
                    'score': signed_score,
                    'sentiment': trading_sentiment,
                }))
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
