"""
Financial data processing module.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
import requests
from polygon import RESTClient

# --- Price cache (Supabase stock_prices) helpers ---
from utils.supabase_client import get_admin_client, get_client
from utils.cache import ttl_cache
import numpy as np
import logging
import time
import random
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Cache configurations
VALIDATION_CACHE_DURATION = 7 * 24 * 60 * 60  # 7 days in seconds
STOCK_DATA_CACHE_DURATION = 30 * 60  # 30 minutes in seconds

# Global caches
VALIDATION_CACHE = {}
STOCK_DATA_CACHE = {}

def _get_cache_key(ticker: str, operation: str) -> str:
    """Generate cache key for ticker and operation."""
    return f"{ticker.upper()}_{operation}"

def _is_cache_valid(cache_entry: dict) -> bool:
    """Check if cache entry is still valid."""
    return time.time() < cache_entry.get('expires_at', 0)

def _get_cached_result(cache: dict, key: str):
    """Get result from cache if valid."""
    if key in cache and _is_cache_valid(cache[key]):
        logger.info(f"Cache hit for {key}")
        return cache[key]['result']
    elif key in cache:
        # Cache expired, remove it
        logger.info(f"Cache expired for {key}, removing")
        del cache[key]
    return None

def _set_cached_result(cache: dict, key: str, result: dict, duration: int):
    """Store result in cache with expiry."""
    cache[key] = {
        'result': result,
        'timestamp': time.time(),
        'expires_at': time.time() + duration
    }
    logger.info(f"Cached result for {key}, expires in {duration/3600:.1f} hours")

def _cleanup_expired_cache(cache: dict):
    """Remove expired entries from cache."""
    expired_keys = [k for k, v in cache.items() if not _is_cache_valid(v)]
    for key in expired_keys:
        del cache[key]
    if expired_keys:
        logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")

def get_cache_stats():
    """Get cache statistics for monitoring."""
    _cleanup_expired_cache(VALIDATION_CACHE)
    _cleanup_expired_cache(STOCK_DATA_CACHE)

    return {
        'validation_cache': {
            'entries': len(VALIDATION_CACHE),
            'total_size': sum(len(str(v)) for v in VALIDATION_CACHE.values())
        },
        'stock_data_cache': {
            'entries': len(STOCK_DATA_CACHE),
            'total_size': sum(len(str(v)) for v in STOCK_DATA_CACHE.values())
        }
    }

# Bulk ticker download and storage
TICKER_MASTER_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'tickers.json')

# Optional: persist the master list in Supabase Storage so it survives app restarts
TICKER_MASTER_BUCKET = os.getenv("TICKER_MASTER_BUCKET", "cache")
TICKER_MASTER_OBJECT = os.getenv("TICKER_MASTER_OBJECT", "tickers/tickers.json")

def download_ticker_master_list() -> Dict[str, Dict]:
    """
    Download complete list of active US tickers from Polygon and save to local file.

    Returns:
        Dictionary mapping ticker symbols to ticker data, or None if failed
    """
    try:
        # Get API key from secrets
        api_key = _config("POLYGON_API_KEY")
        if not api_key:
            raise RuntimeError("POLYGON_API_KEY not found in secrets or env")

        # Initialize Polygon client
        client = RESTClient(api_key)

        # Prepare data structure
        tickers_data = {
            'metadata': {
                'downloaded_at': time.time(),
                'source': 'polygon_bulk_api',
                'version': '1.0'
            },
            'tickers': {}
        }

        logger.info("Starting bulk ticker download from Polygon...")

        # Monkey patch the client's _get method to add rate limiting
        original_get = client._get
        def rate_limited_get(*args, **kwargs):
            _rate_limit(15.0)  # 15 seconds between API calls for bulk downloads
            return original_get(*args, **kwargs)

        client._get = rate_limited_get

        try:
            # Use the standard iterator but with rate limiting
            ticker_count = 0
            for ticker in client.list_tickers(
                market='stocks',      # Only stocks, not crypto/forex
                active=True,          # Only actively traded
                limit=1000,           # Maximum allowed by API (1000 per page)
                sort='ticker'         # Sort for consistency
            ):
                # Extract relevant fields from ticker object
                ticker_info = {
                    'name': getattr(ticker, 'name', ''),
                    'exchange': getattr(ticker, 'primary_exchange', ''),
                    'market': getattr(ticker, 'market', ''),
                    'active': getattr(ticker, 'active', True),
                    'type': getattr(ticker, 'type', ''),
                    'sector': getattr(ticker, 'sic_description', None),
                    'industry': getattr(ticker, 'sic_code', None),
                    'locale': getattr(ticker, 'locale', ''),
                    'currency': getattr(ticker, 'currency_name', ''),
                    'last_updated': getattr(ticker, 'last_updated_utc', None)
                }

                # Store by uppercase ticker symbol
                tickers_data['tickers'][ticker.ticker.upper()] = ticker_info
                ticker_count += 1

                # Progress logging every 1000 tickers
                if ticker_count % 1000 == 0:
                    logger.info(f"Downloaded {ticker_count} tickers...")

        finally:
            # Restore original _get method
            client._get = original_get

        logger.info(f"Completed bulk download: {ticker_count} total tickers")

        # Save to local JSON file (atomic write)
        os.makedirs(os.path.dirname(TICKER_MASTER_FILE), exist_ok=True)
        tmp_path = TICKER_MASTER_FILE + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(tickers_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, TICKER_MASTER_FILE)

        logger.info(f"Saved ticker data to {TICKER_MASTER_FILE}")

        return tickers_data['tickers']

    except Exception as e:
        logger.error(f"Bulk ticker download failed: {str(e)}")
        return None

def _load_ticker_master_from_supabase() -> Optional[dict]:
    """Best-effort: download ticker master JSON from Supabase Storage."""
    try:
        sb = get_admin_client()
        data_bytes = sb.storage.from_(TICKER_MASTER_BUCKET).download(TICKER_MASTER_OBJECT)
        if not data_bytes:
            return None
        if isinstance(data_bytes, str):
            # some clients may return a string
            data_bytes = data_bytes.encode("utf-8")
        return json.loads(data_bytes.decode("utf-8"))
    except Exception as e:
        logger.info(f"Supabase ticker master load skipped/failed: {type(e).__name__}: {str(e)[:160]}")
        return None


def _save_ticker_master_to_supabase(data: dict) -> None:
    """Best-effort: upload ticker master JSON to Supabase Storage."""
    try:
        sb = get_admin_client()
        payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")

        # Upload new version. If already exists, try update.
        try:
            sb.storage.from_(TICKER_MASTER_BUCKET).upload(
                TICKER_MASTER_OBJECT,
                payload,
                file_options={"content-type": "application/json", "upsert": "true"},
            )
        except Exception:
            # Some supabase-py versions prefer update() for overwrite
            sb.storage.from_(TICKER_MASTER_BUCKET).update(
                TICKER_MASTER_OBJECT,
                payload,
                file_options={"content-type": "application/json"},
            )

        logger.info(f"Saved ticker master list to Supabase Storage: {TICKER_MASTER_BUCKET}/{TICKER_MASTER_OBJECT}")
    except Exception as e:
        logger.info(f"Supabase ticker master save skipped/failed: {type(e).__name__}: {str(e)[:160]}")


def _load_ticker_master_from_supabase_table() -> Dict[str, Dict]:
    """Load ticker master from Supabase table `ticker_master`.

    Expected row fields: symbol, name, sector, industry, country, exchange.
    Returns a dict keyed by symbol.
    """
    sb = get_client()

    out: Dict[str, Dict] = {}
    page = 0
    page_size = 1000

    while True:
        start = page * page_size
        end = start + page_size - 1
        resp = (
            sb.table("ticker_master")
            .select("symbol,name,sector,industry,country,exchange")
            .range(start, end)
            .execute()
        )

        rows = getattr(resp, "data", None) or []
        if not rows:
            break

        for r in rows:
            sym = (r.get("symbol") or "").strip().upper()
            if not sym:
                continue
            out[sym] = {
                "name": r.get("name"),
                "sector": r.get("sector"),
                "industry": r.get("industry"),
                "country": r.get("country"),
                "exchange": r.get("exchange"),
            }

        page += 1

    return out


# MODULE SCOPE, and that is the whole point. Was @st.cache_data(ttl=6*3600) on
# a function NESTED inside get_ticker_master_list. Streamlit's cache keys on
# (module, qualname), so re-creating the nested function each call still hit one
# global entry -- ttl_cache keeps its state in a closure, so re-decorating built
# a fresh empty cache every time and the memo was dead. Measured: five calls
# produced five full loads of the ~7,600-row ticker_master table, and
# pages/Discovery.py calls this on every scan, on every Streamlit rerun.
@ttl_cache(6 * 3600)
def _cached_ticker_master() -> Dict[str, Dict]:
    return _load_ticker_master_from_supabase_table()


def get_ticker_master_list() -> Dict[str, Dict]:
    """Get ticker master list from Supabase (Nasdaq sectors/industries).

    This replaces the old Polygon bulk JSON cache path.

    Returns:
        Dict mapping ticker symbols to {name, sector, industry, country, exchange}
    """
    try:
        data = _cached_ticker_master() or {}
        if data:
            logger.info(f"Loaded {len(data)} tickers from Supabase table ticker_master")
        else:
            logger.info("Supabase table ticker_master returned 0 rows")
        return data

    except Exception as e:
        logger.error(f"Failed to load ticker master from Supabase table: {type(e).__name__}: {str(e)[:180]}")
        return {}

# Rate limiting: minimum seconds between API calls
API_RATE_LIMIT = 1.0  # 1 second between requests
last_api_call = 0

def _rate_limit(rate_limit_seconds=None):
    """Enforce rate limiting between API calls."""
    global last_api_call, API_RATE_LIMIT
    # Allow temporary override of rate limit
    effective_limit = rate_limit_seconds if rate_limit_seconds is not None else API_RATE_LIMIT

    elapsed = time.time() - last_api_call
    if elapsed < effective_limit:
        sleep_time = effective_limit - elapsed + random.uniform(0.1, 0.5)  # Add jitter
        time.sleep(sleep_time)
    last_api_call = time.time()

def _retry_with_backoff(func, max_retries=3, base_delay=2):
    """Retry a function with exponential backoff on 429 errors."""
    for attempt in range(max_retries):
        try:
            _rate_limit()  # Apply rate limiting
            return func()
        except Exception as e:
            error_msg = str(e).lower()
            if ("429" in error_msg or "rate" in error_msg or "too many" in error_msg) and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0.5, 1.5)  # Exponential backoff with jitter
                logger.warning(f"Rate limit hit, retrying in {delay:.1f} seconds (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            else:
                raise e


def validate_ticker(ticker: str) -> Dict:
    """
    Validate if a ticker is a legitimate US stock using Polygon ticker details API.
    Filters by market type, exchange, and active status to exclude:
    - Currencies (forex)
    - Commodities
    - OTC/Pink Sheet stocks
    - Non-US stocks
    - Delisted companies

    Uses caching to reduce API calls (7-day cache duration).

    Args:
        ticker: Stock ticker symbol to validate

    Returns:
        Dictionary with keys:
        - valid: Boolean indicating if ticker is valid US stock
        - name: Company name if available
        - exchange: Primary exchange (e.g., XNYS, XNAS)
        - market: Market type (should be 'stocks')
        - sector: Industry sector (e.g., 'Technology', 'Healthcare')
        - industry: Industry classification
        - error: Error message if any, None otherwise
    """
    # Check cache first
    cache_key = _get_cache_key(ticker, 'validation')
    cached_result = _get_cached_result(VALIDATION_CACHE, cache_key)
    if cached_result:
        return cached_result

    try:
        # Get API key from secrets
        api_key = _config("POLYGON_API_KEY")
        if not api_key:
            raise RuntimeError("POLYGON_API_KEY not found in secrets or env")
        
        # Initialize Polygon client
        client = RESTClient(api_key)
        
        # Get ticker details for comprehensive validation
        try:
            def _get_ticker_details():
                return client.get_ticker_details(ticker.upper())

            details = _retry_with_backoff(_get_ticker_details)
            
            # Check if we got valid data
            if not details:
                return {
                    'valid': False,
                    'name': None,
                    'exchange': None,
                    'market': None,
                    'sector': None,
                    'industry': None,
                    'error': f"No data available for {ticker}"
                }
            
            # Extract key attributes
            market = getattr(details, 'market', None)
            primary_exchange = getattr(details, 'primary_exchange', None)
            active = getattr(details, 'active', False)
            ticker_type = getattr(details, 'type', None)
            company_name = getattr(details, 'name', ticker.upper())
            sector = getattr(details, 'sector', None)
            industry = getattr(details, 'industry', None)
            
            # Validation checks
            # 1. Must be in stocks market (not fx, crypto, commodities)
            if market != 'stocks':
                return {
                    'valid': False,
                    'name': company_name,
                    'exchange': primary_exchange,
                    'market': market,
                    'sector': sector,
                    'industry': industry,
                    'error': f"Not a stock (market: {market or 'unknown'})"
                }
            
            # 2. Must be on major US exchanges (NYSE, NASDAQ, AMEX)
            # Exchange codes: XNYS (NYSE), XNAS (NASDAQ), XASE (AMEX/NYSE American)
            valid_exchanges = {'XNYS', 'XNAS', 'XASE'}
            if primary_exchange not in valid_exchanges:
                return {
                    'valid': False,
                    'name': company_name,
                    'exchange': primary_exchange,
                    'market': market,
                    'sector': sector,
                    'industry': industry,
                    'error': f"Not on major US exchange (exchange: {primary_exchange or 'unknown'})"
                }
            
            # 3. Must be actively traded
            if not active:
                return {
                    'valid': False,
                    'name': company_name,
                    'exchange': primary_exchange,
                    'market': market,
                    'sector': sector,
                    'industry': industry,
                    'error': "Not actively traded (possibly delisted)"
                }
            
            # 4. Prefer common stocks (CS), but allow some other types
            # Exclude warrants, rights, units, etc.
            excluded_types = {'WARRANT', 'RIGHT', 'UNIT', 'ETN', 'ETF'}
            if ticker_type in excluded_types:
                return {
                    'valid': False,
                    'name': company_name,
                    'exchange': primary_exchange,
                    'market': market,
                    'sector': sector,
                    'industry': industry,
                    'error': f"Not a common stock (type: {ticker_type})"
                }
            
            # All checks passed
            result = {
                'valid': True,
                'name': company_name,
                'exchange': primary_exchange,
                'market': market,
                'sector': sector,
                'industry': industry,
                'error': None
            }
            logger.info(f"Validated {ticker}: {company_name} on {primary_exchange}")
            _set_cached_result(VALIDATION_CACHE, cache_key, result, VALIDATION_CACHE_DURATION)
            return result
            
        except Exception as api_error:
            error_msg = str(api_error)
            logger.warning(f"Validation failed for {ticker}: {error_msg}")
            
            # Categorize errors for better user feedback
            if "NOT_FOUND" in error_msg.upper() or "404" in error_msg:
                return {
                    'valid': False,
                    'name': None,
                    'exchange': None,
                    'market': None,
                    'sector': None,
                    'industry': None,
                    'error': f"Ticker not found (invalid or non-US)"
                }
            elif "FORBIDDEN" in error_msg.upper() or "403" in error_msg:
                return {
                    'valid': False,
                    'name': None,
                    'exchange': None,
                    'market': None,
                    'sector': None,
                    'industry': None,
                    'error': "Access denied (check API tier)"
                }
            elif "RATE_LIMIT" in error_msg.upper() or "429" in error_msg:
                return {
                    'valid': False,
                    'name': None,
                    'exchange': None,
                    'market': None,
                    'sector': None,
                    'industry': None,
                    'error': "Rate limit exceeded"
                }
            else:
                return {
                    'valid': False,
                    'name': None,
                    'exchange': None,
                    'market': None,
                    'sector': None,
                    'industry': None,
                    'error': f"API error: {error_msg[:50]}"
                }
                
    except KeyError:
        return {
            'valid': False,
            'name': None,
            'exchange': None,
            'market': None,
            'sector': None,
            'industry': None,
            'error': "POLYGON_API_KEY not found in secrets"
        }
    except Exception as e:
        logger.error(f"Unexpected error validating {ticker}: {str(e)}")
        return {
            'valid': False,
            'name': None,
            'exchange': None,
            'market': None,
            'sector': None,
            'industry': None,
            'error': f"Validation error: {str(e)[:50]}"
        }


def get_stock_data(ticker: str, days: int = 30) -> Dict:
    """
    Fetch stock data from Polygon API and calculate volatility.
    Now with robust error handling for non-US tickers and API issues.

    Uses caching to reduce API calls (30-minute cache duration).

    Args:
        ticker: Stock ticker symbol (e.g., 'AAPL')
        days: Number of days of historical data to fetch (default 30)

    Returns:
        Dictionary with keys:
        - prices: List of closing prices
        - volatility: Volatility as percentage (std dev of daily returns)
        - error: Error message if any, None otherwise
    """
    # Check cache first
    cache_key = _get_cache_key(ticker, f'stock_data_{days}')
    cached_result = _get_cached_result(STOCK_DATA_CACHE, cache_key)
    if cached_result:
        return cached_result

    try:
        # Get API key from secrets
        api_key = _config("POLYGON_API_KEY")
        if not api_key:
            raise RuntimeError("POLYGON_API_KEY not found in secrets or env")
        
        # Initialize Polygon client
        client = RESTClient(api_key)
        
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 5)  # Extra days to ensure we get enough data
        
        # Format dates for API
        from_date = start_date.strftime('%Y-%m-%d')
        to_date = end_date.strftime('%Y-%m-%d')
        
        # Fetch aggregate bars (daily OHLC data)
        aggs = []
        try:
            def _get_aggs():
                result = []
                for agg in client.list_aggs(
                    ticker=ticker.upper(),
                    multiplier=1,
                    timespan="day",
                    from_=from_date,
                    to=to_date,
                    limit=50
                ):
                    result.append(agg)
                return result

            aggs = _retry_with_backoff(_get_aggs)
        except Exception as api_error:
            error_msg = str(api_error)
            logger.warning(f"Failed to fetch data for {ticker}: {error_msg}")
            
            # Provide specific error messages for common issues
            if "NOT_FOUND" in error_msg.upper() or "404" in error_msg:
                return {
                    'prices': [],
                    'volatility': 0.0,
                    'error': f"Ticker not found (possibly non-US stock)"
                }
            elif "FORBIDDEN" in error_msg.upper() or "403" in error_msg:
                return {
                    'prices': [],
                    'volatility': 0.0,
                    'error': "Access denied (check API tier)"
                }
            elif "RATE_LIMIT" in error_msg.upper() or "429" in error_msg:
                return {
                    'prices': [],
                    'volatility': 0.0,
                    'error': "Rate limit exceeded"
                }
            else:
                return {
                    'prices': [],
                    'volatility': 0.0,
                    'error': f"API error: {error_msg[:50]}"
                }
        
        # Check if we got data
        if not aggs or len(aggs) < 5:
            logger.info(f"Insufficient data for {ticker} (got {len(aggs)} data points)")
            return {
                'prices': [],
                'volatility': 0.0,
                'error': f"Insufficient data (need at least 5 days, got {len(aggs)})"
            }
        
        # Extract closing prices
        prices = [float(agg.close) for agg in aggs]

        # VOLUME, which the same bars have always carried and this function
        # always discarded. Without it the price veto is structurally dead: it
        # requires BOTH a decline and a volume spike, so a permanently absent
        # volume ratio meant `caution` could never be True. The pillar then
        # printed a green "Price not contradicting" beside "-43.5% over 21
        # sessions" -- the exact contradiction the readout exists to prevent.
        volumes = []
        for agg in aggs:
            v = getattr(agg, "volume", None)
            volumes.append(float(v) if isinstance(v, (int, float)) else 0.0)

        # THE BAR'S DATE, which every caller needs and none could get. The
        # timestamp was read off the same aggs and dropped, so a recorded price
        # could not be tied to a trading session: a verdict issued on a Saturday
        # or at 02:00 UTC carries Friday's close under a Saturday timestamp, and
        # any join of `created_at::date` to a daily bar table silently finds no
        # row -- discarding roughly a third of all observations with no error.
        # Polygon aggs carry `timestamp` as epoch MILLISECONDS, UTC.
        last_bar_date = None
        try:
            ts = getattr(aggs[-1], "timestamp", None)
            if isinstance(ts, (int, float)) and ts > 0:
                last_bar_date = datetime.fromtimestamp(
                    ts / 1000.0, tz=timezone.utc).date().isoformat()
        except Exception:
            logger.warning("could not read bar date for %s", ticker, exc_info=True)
        
        # Calculate daily returns
        returns = []
        for i in range(1, len(prices)):
            daily_return = (prices[i] - prices[i-1]) / prices[i-1]
            returns.append(daily_return)
        
        # Calculate volatility (annualized standard deviation of returns)
        if len(returns) > 1:
            volatility = np.std(returns) * np.sqrt(252) * 100  # Annualized, as percentage
        else:
            volatility = 0.0
        
        result = {
            'prices': prices,
            'volumes': volumes,
            # ISO date of the LAST bar, i.e. the session `prices[-1]` closed in.
            # None when the aggregate carried no usable timestamp.
            'last_bar_date': last_bar_date,
            'volatility': round(volatility, 2),
            'error': None
        }
        logger.info(f"Successfully fetched data for {ticker}: {len(prices)} prices, volatility {volatility:.2f}%")
        _set_cached_result(STOCK_DATA_CACHE, cache_key, result, STOCK_DATA_CACHE_DURATION)
        return result
        
    except KeyError:
        return {
            'prices': [],
            'volatility': 0.0,
            'error': "POLYGON_API_KEY not found in secrets"
        }
    except Exception as e:
        logger.error(f"Unexpected error fetching data for {ticker}: {str(e)}")
        return {
            'prices': [],
            'volatility': 0.0,
            'error': f"Error: {str(e)[:50]}"
        }


def get_stock_data_batch(tickers: List[str], days: int = 30, max_workers: int = 5) -> Dict[str, Dict]:
    """
    Fetch stock data for multiple tickers in parallel (much faster than sequential).
    
    Uses ThreadPoolExecutor to fetch up to max_workers tickers concurrently.
    Respects rate limiting and caching for each ticker.
    
    Args:
        tickers: List of ticker symbols to fetch
        days: Number of days of historical data (default 30)
        max_workers: Max concurrent requests (default 5, max 10)
    
    Returns:
        Dictionary mapping ticker -> stock data result
        Example: {'AAPL': {...}, 'TSLA': {...}, ...}
    """
    # Limit workers to avoid overwhelming the API
    max_workers = min(max(1, max_workers), 10)
    
    results = {}
    
    # Try cache first (very fast)
    cached_tickers = []
    uncached_tickers = []
    
    for ticker in tickers:
        cache_key = _get_cache_key(ticker, f'stock_data_{days}')
        cached_result = _get_cached_result(STOCK_DATA_CACHE, cache_key)
        if cached_result:
            results[ticker] = cached_result
            cached_tickers.append(ticker)
        else:
            uncached_tickers.append(ticker)
    
    logger.info(f"Batch fetch: {len(cached_tickers)} cached, {len(uncached_tickers)} need API calls")
    
    # Fetch uncached tickers in parallel
    if uncached_tickers:
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_ticker = {
                executor.submit(get_stock_data, ticker, days): ticker 
                for ticker in uncached_tickers
            }
            
            # Collect results as they complete
            completed = 0
            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                try:
                    result = future.result()
                    results[ticker] = result
                    completed += 1
                except Exception as e:
                    logger.error(f"Batch fetch failed for {ticker}: {e}")
                    results[ticker] = {
                        'prices': [],
                        'volatility': 0.0,
                        'error': f"Batch fetch error: {str(e)[:50]}"
                    }
        
        elapsed = time.time() - start_time
        logger.info(f"Batch fetched {len(uncached_tickers)} tickers in {elapsed:.1f}s ({len(uncached_tickers)/elapsed:.1f} tickers/sec)")
    
    return results


# ==============================
# Price cache (last close prices)
# ==============================


# Was a private copy of the same twelve lines in ten modules. Reaching into
# streamlit from analysis code is what kept this file inside the portal.
from utils.config import get as _config  # noqa: E402

def _get_polygon_api_key() -> str:
    key = _config("POLYGON_API_KEY")
    if not key:
        raise RuntimeError(
            "Missing POLYGON_API_KEY (set the env var or add it to "
            ".streamlit/secrets.toml)"
        )
    return key


def get_cached_last_close_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch cached last close prices from Supabase stock_prices.

    Returns a dict of {TICKER: close_price} for any tickers found.
    """
    tickers_u = [t.upper().strip() for t in tickers if t]
    tickers_u = list(dict.fromkeys(tickers_u))
    if not tickers_u:
        return {}

    sb = get_admin_client()

    # Supabase has a max URL length; chunk to be safe.
    out: dict[str, float] = {}
    chunk_size = 200
    for i in range(0, len(tickers_u), chunk_size):
        chunk = tickers_u[i : i + chunk_size]
        resp = (
            sb.table("stock_prices")
            .select("ticker,close_price")
            .in_("ticker", chunk)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        for r in rows:
            t = (r.get("ticker") or "").upper()
            cp = r.get("close_price")
            if t and isinstance(cp, (int, float)):
                out[t] = float(cp)

    return out


# Moved to utils.prices so the worker container can import it without Streamlit,
# numpy or the Polygon SDK -- none of which this function ever needed.
# Re-exported here because Discovery and scripts/ already import it from finance.
# One implementation, imported twice: the six-month sync outage was caused by a
# SECOND copy of this logic that upserted a non-existent column.
from utils.prices import fetch_and_cache_last_close_prices  # noqa: E402,F401

def get_last_close_prices_best_effort(tickers: list[str]) -> dict[str, float | None]:
    """Best-effort last close prices.

    1) Read cache from Supabase.
    2) For missing tickers, fetch from Polygon and upsert to cache.

    Returns dict mapping each input ticker (uppercased) to a price or None.
    """
    tickers_u = [t.upper().strip() for t in tickers if t]
    tickers_u = list(dict.fromkeys(tickers_u))
    try:
        cached = get_cached_last_close_prices(tickers_u)
    except Exception as e:
        logger.warning(f"Price cache read failed; continuing without cache: {str(e)[:160]}")
        cached = {}

    missing = [t for t in tickers_u if t not in cached]
    fetched: dict[str, float] = {}
    if missing:
        fetched = fetch_and_cache_last_close_prices(missing)

    merged: dict[str, float | None] = {}
    for t in tickers_u:
        if t in cached:
            merged[t] = cached[t]
        elif t in fetched:
            merged[t] = fetched[t]
        else:
            merged[t] = None
    return merged


def get_top_500_tickers() -> list[str]:
    """Return a deterministic list of 500 US tickers from the local master list."""
    try:
        tickers = get_ticker_master_list()  # dict ticker -> metadata
        keys = list(tickers.keys())
        return [k.upper() for k in keys[:500]]
    except Exception as e:
        logger.warning(f"Failed to load ticker master list for top 500: {str(e)[:160]}")
        return []


def _get_stock_prices_count(sb) -> int | None:
    """Best-effort count of rows in stock_prices."""
    try:
        # supabase-py supports count='exact'
        resp = sb.table("stock_prices").select("ticker", count="exact").limit(1).execute()
        cnt = getattr(resp, "count", None)
        if cnt is None and isinstance(resp, dict):
            cnt = resp.get("count")
        return int(cnt) if cnt is not None else None
    except Exception as e:
        logger.warning(f"Could not count stock_prices: {str(e)[:160]}")
        return None


def warm_prices_cache_top_500(progress_cb=None) -> tuple[bool, str]:
    """Populate stock_prices cache for top 500 tickers.

    Uses daily aggregates per ticker (more entitlement-friendly than snapshot).

    progress_cb: optional fn(done:int,total:int) for UI progress.
    """
    sb = get_admin_client()
    current_count = _get_stock_prices_count(sb)
    if current_count is not None and current_count >= 450:
        return True, "Cache already warm"

    tickers = get_top_500_tickers()
    if not tickers:
        return False, "Could not load top 500 tickers"

    # Fail fast if key missing
    api_key = _get_polygon_api_key()
    client = RESTClient(api_key=api_key)

    # Fetch last close for each ticker using aggs.
    end = datetime.utcnow().date()
    start = end - timedelta(days=10)

    out_rows: list[dict] = []
    total = len(tickers)
    successes = 0
    failures = 0
    first_error: str | None = None

    for i, t in enumerate(tickers, start=1):
        try:
            resp = client.get_aggs(
                ticker=t,
                multiplier=1,
                timespan="day",
                from_=start.isoformat(),
                to=end.isoformat(),
                limit=5,
                sort="desc",
            )
            results = getattr(resp, "results", None) or []
            if results:
                close = getattr(results[0], "close", None)
                if isinstance(close, (int, float)):
                    out_rows.append(
                        {
                            "ticker": t,
                            "close_price": float(close),
                            "last_updated": datetime.utcnow().isoformat(),
                            "currency": "USD",
                        }
                    )
                    successes += 1
                else:
                    failures += 1
            else:
                failures += 1
        except Exception as e:
            failures += 1
            if first_error is None:
                first_error = f"{type(e).__name__}: {str(e)[:200]}"
            logger.warning(f"Warm cache aggs failed for {t}: {type(e).__name__}: {str(e)[:200]}")

        # If everything is failing early, stop and show a useful message
        if i >= 15 and successes == 0:
            return False, f"Polygon aggs failed for first 15 tickers. Example error: {first_error or 'unknown'}"

        if progress_cb:
            progress_cb(i, total)

        # Upsert in chunks to avoid huge payloads
        if len(out_rows) >= 200:
            try:
                sb.table("stock_prices").upsert(out_rows).execute()
                out_rows = []
            except Exception as e:
                return False, f"Supabase upsert failed: {type(e).__name__}: {str(e)[:200]}"

    if out_rows:
        try:
            sb.table("stock_prices").upsert(out_rows).execute()
        except Exception as e:
            return False, f"Supabase upsert failed: {type(e).__name__}: {str(e)[:200]}"

    final_count = _get_stock_prices_count(sb)
    if successes < 50:
        return False, f"Warmup fetched too few prices ({successes}/{total}). Example error: {first_error or 'none'}"

    return True, f"Warm complete (fetched={successes}, failed={failures}, stock_prices count={final_count})"
