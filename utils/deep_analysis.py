import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any
from pathlib import Path
import json
import logging
import hashlib
import functools

from utils.config import get as _config
from utils.sentiment import analyze_sentiment_batch
from utils import corpus_cache

logger = logging.getLogger(__name__)

# L1 in front of the disk cache. It was st.session_state directly, which made
# this module unimportable outside the portal for the sake of a memo. In the
# portal the behaviour is unchanged -- same dict, same per-session lifetime.
# Elsewhere it degrades to a process-local dict, and the disk cache underneath
# is what actually carries the corpus between runs.
_PROCESS_STORE: Dict[str, Any] = {}


def _session_store():
    try:
        import streamlit as st
        return st.session_state
    except Exception:
        return _PROCESS_STORE

# ---- Prompt definitions (UI + reporting structure) ----
# NOTE: We still present 8 prompts, but we only make 4 X API calls.
ANALYSIS_PROMPTS: Dict[str, Dict[str, Any]] = {
    "Real-Time Market Sentiment": {
        "description": "Latest discussions about the stock and overall sentiment (ticker corpus)",
        "timeframe": "48h",
    },
    "Sector Narrative & Trends": {
        "description": "Emerging sector narrative gaining traction (sector corpus)",
        "timeframe": "24h",
    },
    "Track Smart Money and Influencer Moves": {
        "description": "What curated influencers are saying about the ticker/sector (from: list)",
        "timeframe": "72h",
    },
    "Momentum (High Engagement)": {
        "description": "Momentum-language tweets filtered by engagement (momentum corpus)",
        "timeframe": "24h",
    },
    "Monitor Breaking News and Catalysts": {
        "description": "Breaking news and catalysts related to the stock (bucketed from ticker corpus)",
        "timeframe": "48h",
    },
    "Gauge Retail vs. Institutional Sentiment": {
        "description": "Retail vs institutional framing (bucketed from ticker corpus)",
        "timeframe": "48h",
    },
    "Detect Early Warning Signs and Red Flags": {
        "description": "Risks, warnings, and red flags (bucketed from ticker corpus)",
        "timeframe": "48h",
    },
    "Trading Intent / Watchlist Signals": {
        "description": "Trading intent and watchlist language (bucketed from ticker corpus)",
        "timeframe": "48h",
    },
}


# ---- Influencer list helpers ----

def _load_influencer_usernames(limit: int = 40) -> List[str]:
    """Load influencer usernames from data/influencers_validated.json (preferred) or data/influencers.json.

    Returns a de-duped list of usernames without leading '@'.
    """
    root = Path(__file__).resolve().parents[1]
    validated_path = root / "data" / "influencers_validated.json"
    seed_path = root / "data" / "influencers.json"

    usernames: List[str] = []

    if validated_path.exists():
        payload = json.loads(validated_path.read_text(encoding="utf-8"))
        buckets = (payload.get("buckets") or {})
        for _, arr in buckets.items():
            usernames.extend(arr or [])
    elif seed_path.exists():
        payload = json.loads(seed_path.read_text(encoding="utf-8"))
        buckets = (payload.get("buckets") or {})
        for _, arr in buckets.items():
            usernames.extend(arr or [])

    out: List[str] = []
    seen = set()
    for u in usernames:
        u2 = (u or "").strip()
        if u2.startswith("@"):  # allow @foo
            u2 = u2[1:]
        if not u2:
            continue
        key = u2.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u2)

    return out[: max(0, int(limit))]


@functools.lru_cache(maxsize=512)
def _company_alias(ticker: str) -> str:
    """Query-safe company name for a ticker, or "" if none is usable.

    Never raises and never blocks a scan: an unreachable lookup degrades to
    cashtag-only retrieval, which is exactly what ran before this existed.

    MEMOISED, and that is a correctness property rather than a speed one. The
    corpus cache keys on a hash of the query TEXT, so a lookup that succeeds on
    one call and fails on the next flips the query between
    `($TSLA OR "Tesla") ...` and `$TSLA ...` -- two different hashes, a
    guaranteed cache miss, and up to 300 billed posts re-bought for nothing.
    Answering identically for the life of the process is worth more here than
    recovering from a transient failure mid-run.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return ""
    try:
        import urllib.parse
        import urllib.request

        from utils.sector_query import _config as _sq_config
        from utils.sector_query import company_alias as _alias_of

        base = _sq_config("SUPABASE_URL").rstrip("/")
        key = _sq_config("SUPABASE_SERVICE_ROLE_KEY")
        if not base or not key:
            return ""
        qs = urllib.parse.urlencode({"select": "name", "symbol": f"eq.{t}", "limit": 1})
        req = urllib.request.Request(
            f"{base}/rest/v1/ticker_master?{qs}",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            rows = json.loads(r.read() or b"[]")
        return _alias_of(rows[0].get("name", "")) if rows else ""
    except Exception as e:
        logger.warning("company alias lookup failed for %s: %s: %s",
                       t, type(e).__name__, str(e)[:120])
        return ""


def _build_influencer_query(usernames: List[str], ticker: str, alias: str = "") -> str:
    """Build a `from:` query safely under X query-length limits.

    X recent search queries have a max length (~512). We keep headroom to avoid 400 errors.

    IMPORTANT: Parentheses must be balanced; otherwise X returns 400 with
    "mismatched input '<EOF>' expecting ')'".

    WHY THERE IS NO SECTOR TERM ANY MORE

    The tail used to be `({ticker} OR {sector})`. That OR meant a post qualified
    if one of the twelve accounts merely said the SECTOR WORD -- so from
    Discovery the query degenerated to "did any macro wire account say 'tech' in
    72 hours", which for accounts that post dozens of times a day is close to
    always, returning a full page of posts about nothing in particular. X bills
    per POST RETURNED, so that page was billed in full to populate one of eight
    panels. From pages/Deep_Analysis.py, which hardcodes sector="unknown", the
    same line searched for the literal word "unknown".

    The panel claims to show what smart money is saying about THIS TICKER. The
    OR made most of its contents about neither the ticker nor smart money.

    Removing it also makes the whole analysis sector-independent -- both corpora
    now derive from the ticker alone -- which is what lets the cache key drop
    sector so the two call sites stop invalidating each other's entries.
    """

    ticker = (ticker or "").strip().upper() or "NVDA"

    # Matches the ticker corpus's own form: cashtag or company name, never the
    # bare token. The risk here is not lower than in the ticker corpus, it is
    # higher -- this list is Reuters, Bloomberg and WSJ Markets, general
    # newswires that genuinely do file about Members of Parliament. And the
    # alias matters more on this channel than on any other, because newswire
    # copy is prose: "Tesla reports Q3 deliveries" carries no cashtag at all.
    _subj = f"(${ticker} OR \"{alias}\")" if alias else f"${ticker}"
    tail = f"{_subj} lang:en -is:retweet"

    parts: List[str] = []
    MAX_LEN = 500  # leave headroom under 512

    for u in usernames:
        u = (u or "").strip().lstrip("@")
        if not u:
            continue

        candidate = f"from:{u}"
        next_parts = parts + [candidate]
        from_clause = " OR ".join(next_parts)

        # Fully balanced query
        q = f"(({from_clause}) {tail})"
        if len(q) > MAX_LEN:
            break
        parts = next_parts

    if not parts:
        return tail

    from_clause = " OR ".join(parts)
    return f"(({from_clause}) {tail})"


# ---- X API search ----

def _x_api_error_message(response: requests.Response) -> str:
    """Return a user-safe reason for an X API failure."""
    status = response.status_code

    try:
        payload = response.json() or {}
    except Exception:
        payload = {}

    title = str(payload.get("title") or "").strip()
    detail = str(payload.get("detail") or "").strip()
    problem_type = str(payload.get("type") or "").strip()

    if status == 402 and ("credits-depleted" in problem_type or "credits depleted" in detail.lower()):
        return "X API credits depleted. Add credits or upgrade the X developer plan for the token used by X_BEARER_TOKEN."

    parts = [f"X API Error {status}"]
    if title:
        parts.append(title)
    if detail:
        parts.append(detail)
    return ": ".join(parts)

def search_x_tweets_page(
    query: str,
    max_results: int = 100,
    timeframe: str = "24h",
    next_token: str | None = None,
) -> Dict[str, Any]:
    """Fetch a *single* page from X Recent Search.

    - `max_results` is clamped to [10, 100] per X API requirements.
    - Pass `next_token` from the prior call to fetch the next page.

    Returns: {success, tweets, next_token}
    """
    try:
        import os

        x_bearer_token = _config("X_BEARER_TOKEN")
        if not x_bearer_token:
            raise RuntimeError("Missing X_BEARER_TOKEN (set in .streamlit/secrets.toml or env var)")

        since_date = None
        if timeframe:
            hours = int(timeframe.replace("h", ""))
            since_date = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        url = "https://api.twitter.com/2/tweets/search/recent"
        headers = {"Authorization": f"Bearer {x_bearer_token}"}

        per_page = 100
        n = min(max(int(max_results or per_page), 10), per_page)
        params = {
            "query": query,
            "max_results": n,
            "tweet.fields": "text,created_at,public_metrics,author_id",
        }
        if since_date:
            params["start_time"] = since_date
        if next_token:
            params["next_token"] = next_token

        response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code != 200:
            error_message = _x_api_error_message(response)
            logger.info("📡 X API response status: %s error=%s", response.status_code, error_message)
            return {"success": False, "error": error_message, "tweets": []}

        data = response.json() or {}
        tweets = data.get("data", []) or []
        meta = data.get("meta", {}) or {}
        out_next_token = meta.get("next_token")

        logger.info(
            "📄 X page got=%s has_next=%s",
            len(tweets),
            bool(out_next_token),
        )

        return {"success": True, "tweets": tweets, "next_token": out_next_token, "meta": meta}

    except Exception:
        return {"success": False, "error": "Request failed", "tweets": []}


def search_x_tweets(query: str, max_results: int = 100, timeframe: str = "24h") -> Dict[str, Any]:
    """Search X (Twitter) Recent Search for tweets matching the query.

    Pagination notes:
    - X Recent Search supports up to 100 results per request.
    - If `max_results` > 100, we paginate using `meta.next_token` until we reach
      `max_results` or a safety cap.
    """
    try:
        # Safety cap for this app: never fetch more than ~300 tweets.
        per_page = 100
        safety_cap_total = 300
        target_total = max(0, int(max_results))
        target_total = min(target_total or per_page, safety_cap_total)

        all_tweets: List[Dict[str, Any]] = []
        last_meta: Dict[str, Any] = {}
        next_token = None

        import time
        t0 = time.time()
        stop_reason: str | None = None
        pages_fetched = 0

        max_pages = (target_total + per_page - 1) // per_page  # 1..3
        for page in range(1, max_pages + 1):
            pages_fetched = page
            res = search_x_tweets_page(
                query=query,
                max_results=min(per_page, target_total - len(all_tweets)),
                timeframe=timeframe,
                next_token=next_token,
            )
            if not res.get("success"):
                return {"success": False, "error": res.get("error") or "X API request failed", "tweets": []}

            tweets = res.get("tweets") or []
            next_token = res.get("next_token")
            last_meta = res.get("meta") or {}

            all_tweets.extend(tweets)

            if not next_token:
                stop_reason = "no_next_token"
                break

            if len(all_tweets) >= target_total:
                stop_reason = "hit_target_total"
                break

        out = all_tweets[:target_total]

        elapsed_s = time.time() - t0
        if stop_reason is None:
            stop_reason = "single_page" if pages_fetched == 1 else "exhausted_pages"
        logger.info(
            "📈 X pagination summary pages=%s total=%s elapsed_s=%.2f stop_reason=%s",
            pages_fetched,
            len(out),
            elapsed_s,
            stop_reason,
        )

        return {"success": True, "tweets": out, "meta": last_meta}

    except Exception:
        return {"success": False, "error": "Request failed", "tweets": []}


# ---- Bucketing + analysis ----

def _mentions_ticker(text: str, ticker: str, alias: str = "") -> bool:
    """Return True if the tweet text mentions the ticker as a token (optionally $-prefixed).

    Avoids substring false-positives (e.g., ticker "IN" matching "INTO").
    """
    import re

    t = (ticker or "").strip().upper()
    if not t:
        return False

    s = (text or "")
    # Token boundary match: (^|\W)\$?TICKER(\W|$)
    pattern = rf"(^|\W)\$?{re.escape(t)}(\W|$)"
    if re.search(pattern, s, flags=re.IGNORECASE) is not None:
        return True

    # The company name counts as a mention of the company.
    #
    # Without this the alias arm added to retrieval is pure cost: we pay for
    # "Tesla reports Q3 deliveries", then discard it here because it carries no
    # $TSLA. Seven of the eight angles are ticker-scoped, so alias-only posts --
    # the entire reason the alias was added, and the whole yield of the newswire
    # channel, whose copy is prose -- would be bought and thrown away.
    #
    # The bug hides from casual testing: "MP Materials" contains the token "MP"
    # and "Meta Platforms" matches META case-insensitively, so the aliases one
    # reaches for first pass anyway. Tesla/TSLA, Apple/AAPL, Amazon/AMZN do not.
    if alias:
        a = alias.strip()
        if a:
            return re.search(rf"(^|\W){re.escape(a)}(\W|$)", s,
                             flags=re.IGNORECASE) is not None
    return False


def analyze_tweets_for_prompt(tweets: List[Dict[str, Any]], prompt_name: str, ticker: str,
                              alias: str = "") -> Dict[str, Any]:
    """Analyze tweets for a specific prompt and return insights.

    Notes:
    - We filter by ticker mention for ticker-scoped prompts.
    - We do NOT filter by ticker for the sector trends prompt.

    ML/Signal notes:
    - Uses FinBERT (via utils.sentiment.analyze_sentiment).
    - Uses a SIGNED score in [-1, 1] so averages are meaningful.
    - For Momentum, sample tweets are chosen by engagement.
    """
    if not tweets:
        return {
            "sentiment_score": 0.0,
            "overall_sentiment": "neutral",
            "key_themes": [],
            "insights": "No tweets found for analysis.",
            "sample_tweets": [],
            "mention_count": 0,
            "tweet_ids": [],
        }

    ticker_scoped = prompt_name != "Sector Narrative & Trends"

    # Filter to relevant tweets first
    filtered: List[Dict[str, Any]] = []
    for tw in tweets:
        text = (tw.get("text") or "")
        if ticker_scoped and not _mentions_ticker(text, ticker, alias):
            continue
        filtered.append(tw)

    if not filtered:
        return {
            "sentiment_score": 0.0,
            "overall_sentiment": "neutral",
            "key_themes": [],
            "insights": "No tweets found for analysis.",
            "sample_tweets": [],
            "mention_count": 0,
            "tweet_ids": [],
        }

    # --- Batch sentiment inference (perf) ---
    texts = [(tw.get("text") or "")[:512] for tw in filtered]

    sentiments: List[float] = []
    themes: List[str] = []

    # Scoring failures must PROPAGATE. analyze_sentiment_batch already falls back
    # remote -> local internally and raises only when NEITHER is available; that
    # has to reach the caller so the try/finally refunds the credit.
    #
    # This used to be wrapped in `except Exception:` with a per-tweet
    # analyze_sentiment() fallback. That function swallows its own ImportError
    # and returns Neutral, so once torch is removed from requirements.txt a
    # deep analysis with the inference service down would have charged a credit
    # and rendered a full page of Neutral(0.00) -- indistinguishable from a
    # ticker with genuinely no signal. Same silent-degradation bug that was
    # fixed inside analyze_sentiment_batch, still live one layer up.
    #
    # Theme extraction below is pure string matching on the tweet text and
    # cannot fail in a way worth catching.
    scored = analyze_sentiment_batch(texts)

    for tw, out in zip(filtered, scored):
        sentiments.append(float(out.get("score", 0.0)))

        text_lower = (tw.get("text") or "").lower()

        if prompt_name == "Real-Time Market Sentiment":
            if any(w in text_lower for w in ["bullish", "buy", "moon", "rocket", "long"]):
                themes.append("bullish")
            if any(w in text_lower for w in ["bearish", "sell", "crash", "dump", "short"]):
                themes.append("bearish")

        elif prompt_name == "Sector Narrative & Trends":
            if any(w in text_lower for w in ["trend", "emerging", "gaining", "traction", "rotation", "narrative"]):
                themes.append("trend")

        elif prompt_name == "Track Smart Money and Influencer Moves":
            themes.append("influencer")

        elif prompt_name == "Momentum (High Engagement)":
            themes.append("momentum")

        elif prompt_name == "Monitor Breaking News and Catalysts":
            themes.append("catalyst")

        elif prompt_name == "Gauge Retail vs. Institutional Sentiment":
            themes.append("positioning")

        elif prompt_name == "Detect Early Warning Signs and Red Flags":
            themes.append("risk")

        elif prompt_name == "Trading Intent / Watchlist Signals":
            themes.append("trading_intent")

    # --- Sample tweet selection ---
    def _shorten(s: str) -> str:
        return s[:160] + "..." if len(s) > 160 else s

    if prompt_name == "Momentum (High Engagement)":
        # Pick the top engagement tweets for a more convincing demo.
        def score_eng(tw: Dict[str, Any]) -> int:
            m = (tw.get("public_metrics") or {})
            return int(m.get("like_count", 0)) + 2 * int(m.get("retweet_count", 0)) + int(m.get("quote_count", 0))

        ranked = sorted(filtered, key=score_eng, reverse=True)
        sample_tweets = [_shorten((tw.get("text") or "")) for tw in ranked[:3]]
    else:
        sample_tweets = [_shorten((tw.get("text") or "")) for tw in filtered[:3]]

    avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

    if avg_sentiment > 0.1:
        overall_sentiment = "bullish"
    elif avg_sentiment < -0.1:
        overall_sentiment = "bearish"
    else:
        overall_sentiment = "neutral"

    tweet_ids: List[str] = []
    for tw in filtered:
        tid = tw.get("id")
        if tid is not None:
            tweet_ids.append(str(tid))
        else:
            # Fallback to a stable hash if id is missing
            tweet_ids.append(hashlib.sha1((tw.get("text") or "").encode("utf-8")).hexdigest())

    return {
        "sentiment_score": round(avg_sentiment, 3),
        "overall_sentiment": overall_sentiment,
        "key_themes": list(dict.fromkeys(themes))[:5],
        "insights": f"Found {len(sentiments)} relevant tweets with {overall_sentiment} sentiment.",
        "sample_tweets": sample_tweets,
        "mention_count": len(sentiments),
        "tweet_ids": tweet_ids,
        # PER-POST scores, so the adjudicator can average posts rather than
        # angle means. These were computed here and discarded; reconstructing a
        # post-level mean from angle means is impossible, which is why the first
        # attempt at it silently reduced to "angle 1's mean" -- angle 1 is the
        # whole corpus and is iterated first, so every subset angle became a
        # no-op and the result still depended on declaration order.
        "post_scores": dict(zip(tweet_ids, sentiments)),
    }


def _keyword_bucket(tweets: List[Dict[str, Any]], keywords: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for tw in tweets:
        text = (tw.get("text") or "").lower()
        if any(k in text for k in keywords):
            out.append(tw)
    return out


def _engagement_filter(tweets: List[Dict[str, Any]], min_likes: int = 10, min_retweets: int = 5) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for tw in tweets:
        m = (tw.get("public_metrics") or {})
        if int(m.get("like_count", 0)) >= min_likes and int(m.get("retweet_count", 0)) >= min_retweets:
            out.append(tw)
    return out


def run_deep_analysis(ticker: str, sector: str,
                      sink: Dict[str, Any] | None = None) -> Dict[str, Dict[str, Any]]:
    """Deep analyze with low X API usage and early-stop + caching.

    Changes vs original:
    - Discovery-style pagination: fetch page 1, check "good enough", then stop early.
    - Safety cap: 300 tweets max for ticker corpus.
    - Caching: session + disk TTL to avoid repeat paid calls.

    NOTE: We still present 8 prompts, but we only make 2 X corpora fetches:
    - ticker corpus (paginated, early-stop)
    - influencer corpus (single call)
    """

    t = (ticker or "").strip().upper()

    # `sector` is still accepted so both call sites keep working, but it no
    # longer influences anything: the ticker corpus never referenced it, and the
    # influencer query stopped referencing it when the `OR {sector}` tail was
    # removed. Keeping it in the cache key was actively harmful -- see below.

    # ---- Cache helpers (session + disk) ----
    import os
    import time

    # v4: the influencer query changed (the `OR {sector}` tail is gone), so
    # every v3 blob holds a panel built from a different, noisier corpus.
    #
    # Having to remember this bump is exactly why the new corpus cache hashes
    # the query into its key instead. Forget it here and every user is served
    # results from the old query until the TTL expires; there is no such
    # failure mode in utils/corpus_cache.py.
    CACHE_VERSION = "v4"
    CACHE_TTL_S = 45 * 60

    def _cache_key() -> str:
        # Sector deliberately absent.
        #
        # It used to be part of this key while the DISK PATH below was
        # {TICKER}_{VERSION}.json with no sector in it. So both call sites wrote
        # the same file and then rejected each other's contents on read:
        # pages/Deep_Analysis.py passes sector="unknown" (hardcoded) and
        # pages/Discovery.py passes the real sector. Analysing NVDA from one
        # page and then the other was a guaranteed miss in both directions --
        # each read overwrote the file it had just failed to use.
        return f"deep_analysis:{CACHE_VERSION}:{t}:48h"

    def _cache_dir() -> Path:
        root = Path(__file__).resolve().parents[1]
        d = root / "data" / "cache" / "deep_analysis"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _cache_path() -> Path:
        safe_t = "".join(ch for ch in t if ch.isalnum() or ch in ("-", "_")) or "TICKER"
        return _cache_dir() / f"{safe_t}_{CACHE_VERSION}.json"

    def _cache_get() -> Dict[str, Dict[str, Any]] | None:
        key = _cache_key()
        # session cache
        try:
            _mem = _session_store()
            if key in _mem:
                blob = _mem.get(key) or {}
                ts = float(blob.get("ts", 0))
                if ts and (time.time() - ts) <= CACHE_TTL_S:
                    return blob.get("results")
        except Exception:
            pass

        # disk cache
        try:
            p = _cache_path()
            if not p.exists():
                return None
            blob = json.loads(p.read_text(encoding="utf-8"))
            ts = float(blob.get("ts", 0))
            if not ts or (time.time() - ts) > CACHE_TTL_S:
                return None
            if blob.get("ticker") != t:
                return None
            return blob.get("results")
        except Exception:
            return None

    def _cache_set(results: Dict[str, Dict[str, Any]]) -> None:
        key = _cache_key()
        blob = {
            "ts": time.time(),
            "ticker": t,
            "cache_version": CACHE_VERSION,
            "results": results,
        }
        try:
            _session_store()[key] = blob
        except Exception:
            pass
        try:
            _cache_path().write_text(json.dumps(blob), encoding="utf-8")
        except Exception:
            pass

    # A caller that wants the ledger needs POSTS, which the result cache does
    # not hold. Serving it a cached summary silently downgrades that run to the
    # legacy adjudicator -- so the cache is bypassed when a sink is supplied.
    cached = None if sink is not None else _cache_get()
    if cached:
        logger.info("🧠 Deep Analyze cache hit: %s", _cache_key())
        return cached

    # ---- Call definitions ----
    #
    # The bare-symbol arm is gone. Measured on two retained corpora: posts that
    # matched ONLY the bare token contributed ZERO usable evidence in both,
    # while consuming 37% of one arm's spend and 82% of the other's. In the MP
    # Materials corpus those 69 posts were about Members of Parliament, the
    # Indian state of Madhya Pradesh, and an unrelated bot's label -- and every
    # one of them passed the downstream ticker-attribution filter as valid.
    #
    # The company name replaces it. That is not merely a recall change: 10 TSLA
    # and 4 MP posts matched the name with no cashtag present, and the TSLA ones
    # averaged -0.53 against +0.24 for the cashtag posts. Press prose and trader
    # chatter sample different populations, so the alias buys evidence the
    # cashtag query structurally cannot reach.
    #
    # The finance OR-list stays. Every post we have retained passed through it,
    # so a query without it is untested rather than rejected -- and the one arm
    # we ran without it was captured by a 100-account spam campaign.
    _alias = _company_alias(t)
    _subject = f"(${t} OR \"{_alias}\")" if _alias else f"${t}"
    ticker_query = (
        f"{_subject} (stock OR stocks OR shares OR price OR chart OR earnings OR news OR catalyst "
        f"OR bullish OR bearish OR risk OR watchlist OR trading OR options OR #FinTwit) lang:en -is:retweet"
    )
    logger.info("🔎 ticker query subject=%s alias=%s", _subject, _alias or "(none)")

    influencer_usernames = _load_influencer_usernames(limit=12)
    influencer_query = _build_influencer_query(influencer_usernames, t, _alias) if influencer_usernames else None

    corpuses: Dict[str, List[Dict[str, Any]]] = {"ticker": [], "influencers": []}
    errors: Dict[str, str] = {}
    # Bound here so the sink write below cannot depend on which branches ran.
    _wire_state: str = "unknown"
    _wire_billed: int = 0
    # What the TICKER corpus cost. wire_billed covers only the influencer
    # corpus, so without this the analysis reports at most a quarter of what it
    # actually bought -- and core-api's spend budget, which sums recorded spend,
    # cannot see a deep analysis at all.
    _ticker_billed: int = 0
    # 0 = bought fresh on this run. Set from the cache entry on a hit.
    _corpus_age_s: float | None = 0.0

    # ---- Fetch influencer corpus (single call) in parallel with ticker pagination ----
    def _fetch_influencers() -> Dict[str, Any]:
        # `wire_state` travels with the result because an EMPTY LIST HAS FOUR
        # DIFFERENT MEANINGS and the channel is about to be judged on how often
        # it is empty. Never configured, query errored, cache served nothing,
        # and genuinely returned nothing are the same `[]` downstream -- so a
        # month of rate limiting would read as "these accounts return nothing"
        # and get a working channel cut.
        if not influencer_query:
            return {"success": True, "tweets": [], "wire_state": "not_configured",
                    "wire_billed": 0}

        hit = corpus_cache.get("influencer", t, 72, influencer_query)
        if hit is not None:
            # Billed ZERO. The corpus cache serves across users for 6h, so
            # counting these posts as cost overstates the bill several-fold --
            # and overstates it precisely on the tickers where the wire returns
            # something, which are the only rows that carry any signal.
            return {"success": True, "tweets": hit["tweets"],
                    "wire_state": "cache_hit", "wire_billed": 0}

        res = search_x_tweets(query=influencer_query, timeframe="72h", max_results=100)
        res["wire_billed"] = len(res.get("tweets") or []) if res.get("success") else 0
        res["wire_state"] = "fetched" if res.get("success") else "error"
        if res.get("success"):
            # Stored even when empty. The ACHR run returned zero posts, and
            # without a negative entry the next user pays again to learn the
            # same nothing -- which for this query is the common outcome, since
            # a top-twelve macro account naming one mid-cap in 72h is rare.
            corpus_cache.put(
                "influencer", t, 72, influencer_query,
                tweets=res.get("tweets") or [], pages_fetched=1,
            )
        return res

    logger.info("🧪 Deep Analyze: fetching corpora (ticker paginated + influencers)")
    logger.info("   • ticker_corpus: timeframe=48h per_page=100 safety_cap=300 (early-stop enabled)")
    logger.info("   • influencer_corpus: timeframe=72h max_results=100")

    infl_future = None
    with ThreadPoolExecutor(max_workers=2) as ex:
        infl_future = ex.submit(_fetch_influencers)

        # ---- Ticker corpus pagination (Discovery-style) ----
        SAFETY_CAP_TWEETS = 300
        PER_PAGE = 100
        next_token = None
        pages = 0
        core: List[Dict[str, Any]] = []

        # Early-stop thresholds (page-1 gate)
        GOOD_ENOUGH_MIN_MENTIONS = 20
        GOOD_ENOUGH_MIN_ABS_SENT = 0.12
        GOOD_ENOUGH_MAX_RED_FLAG_RATE = 0.25
        GOOD_ENOUGH_MIN_MOMENTUM = 3
        GOOD_ENOUGH_MIN_INTENT = 5

        def _is_good_enough(analysis_results: Dict[str, Dict[str, Any]]) -> bool:
            # Use the same recommendation inputs: unique ids, avg sentiment, red flags, and trade-ish evidence.
            summary = generate_ai_summary(analysis_results)

            # Extract unique mentions and red flags from the same loop generate_ai_summary uses
            # (avoid recomputing everything here; we can read back from analysis_results)
            unique_ids = set()
            for _, r in (analysis_results or {}).items():
                for tid in (r.get("tweet_ids") or []):
                    unique_ids.add(tid)
            total_mentions = len(unique_ids)

            red = analysis_results.get("Detect Early Warning Signs and Red Flags", {}) or {}
            red_mentions = int(red.get("mention_count", 0) or 0)
            red_flag_rate = (red_mentions / total_mentions) if total_mentions else 1.0

            mom = analysis_results.get("Momentum (High Engagement)", {}) or {}
            intent = analysis_results.get("Trading Intent / Watchlist Signals", {}) or {}
            mom_n = int(mom.get("mention_count", 0) or 0)
            intent_n = int(intent.get("mention_count", 0) or 0)

            return (
                total_mentions >= GOOD_ENOUGH_MIN_MENTIONS
                and abs(float(summary.get("avg_sentiment", 0.0))) >= GOOD_ENOUGH_MIN_ABS_SENT
                and red_flag_rate <= GOOD_ENOUGH_MAX_RED_FLAG_RATE
                and (mom_n >= GOOD_ENOUGH_MIN_MOMENTUM or intent_n >= GOOD_ENOUGH_MIN_INTENT)
            )

        # For gating, analyze only the buckets that matter for "good enough".
        gating_prompts = [
            "Real-Time Market Sentiment",
            "Detect Early Warning Signs and Red Flags",
            "Momentum (High Engagement)",
            "Trading Intent / Watchlist Signals",
        ]

        # Reuse the same keyword lists as the full run (defined below), but keep local copies here.
        # These are used for cheap (non-FinBERT) gating.
        # Same exclusion as the scored bucket: "risk" is a retrieval term, so
        # counting it here measures the query rather than the stock.
        risk_keywords_gate = [
            "red flag",
            "dilution",
            "bankruptcy",
            "delisting",
            "investigation",
            "fraud",
            "lawsuit",
            "downgrade",
            "guidance cut",
        ]
        momentum_keywords_gate = ["viral", "trending", "momentum", "breakout", "squeeze", "runner", "rip", "ripping"]
        watchlist_keywords_gate = [
            "watchlist",
            "setup",
            "breakout",
            "entry",
            "support",
            "resistance",
            "levels",
            "trade",
            "trading",
            "swing",
            "scalp",
            "calls",
            "puts",
            "options",
        ]

        def _cheap_good_enough(core_tweets: List[Dict[str, Any]]) -> bool:
            """Cheap gate used after page 1: no FinBERT, just evidence + bucket coverage."""
            n_core = len(core_tweets)
            if n_core < 120:
                return False

            # How many tweets actually mention the ticker token?
            mention_n = 0
            for tw in core_tweets:
                if _mentions_ticker((tw.get("text") or ""), t):
                    mention_n += 1
            if mention_n < 40:
                return False

            risk_n = len(_keyword_bucket(core_tweets, risk_keywords_gate))
            intent_n = len(_keyword_bucket(core_tweets, watchlist_keywords_gate))
            mom_n = len(_engagement_filter(_keyword_bucket(core_tweets, momentum_keywords_gate), min_likes=10, min_retweets=5))

            # Require at least one "trade-ish" bucket to have content.
            if not (intent_n >= 15 or mom_n >= 5):
                return False

            # Avoid stopping early if risk chatter dominates.
            risk_rate = risk_n / max(1, n_core)
            if risk_rate >= 0.35:
                return False

            return True

        # A cached corpus skips pagination entirely. There is nothing to gate:
        # the early-stop logic below exists only to decide whether to buy ANOTHER
        # page, and a hit means every page this ticker needed was already bought.
        _ticker_cached = corpus_cache.get("ticker", t, 48, ticker_query)
        if _ticker_cached is not None:
            core = list(_ticker_cached["tweets"])
            # How stale the evidence is. A cached corpus can be hours old, and a
            # verdict's write time is not the time the market was speaking.
            _corpus_age_s = _ticker_cached.get("age_s")
            logger.info(
                "🧠 Deep Analyze ticker corpus from cache: %d posts, age %.0fs -- 0 posts billed",
                len(core), _ticker_cached["age_s"],
            )

        while _ticker_cached is None and len(core) < SAFETY_CAP_TWEETS:
            pages += 1
            remaining = SAFETY_CAP_TWEETS - len(core)

            # X recent search enforces max_results in [10, 100]. If we're under 10 remaining,
            # we stop to avoid overshooting the safety cap.
            if remaining < 10:
                break

            res = search_x_tweets_page(
                query=ticker_query,
                max_results=min(PER_PAGE, remaining),
                timeframe="48h",
                next_token=next_token,
            )
            if not res.get("success"):
                errors["ticker"] = res.get("error") or "X API request failed"
                break

            page_tweets = res.get("tweets") or []
            # BILLED IS THE RAW PAGE LENGTH, counted before any filtering or
            # dedup below. X bills per post RETURNED, so a post we fetch and
            # then discard was still paid for.
            _ticker_billed += len(page_tweets)
            next_token = res.get("next_token")

            if not page_tweets:
                break

            core.extend(page_tweets)

            logger.info(
                "📄 Deep Analyze pagination pages=%s core=%s has_next=%s",
                pages,
                len(core),
                bool(next_token),
            )

            # Page 1: allow a sentiment-aware gate (FinBERT runs once here).
            # After page 1: ONLY use cheap gating (no FinBERT) to avoid repeated inference.
            if pages == 1:
                buckets_for_gate: Dict[str, List[Dict[str, Any]]] = {}
                buckets_for_gate["Real-Time Market Sentiment"] = core
                buckets_for_gate["Detect Early Warning Signs and Red Flags"] = _keyword_bucket(core, risk_keywords_gate)
                buckets_for_gate["Momentum (High Engagement)"] = _engagement_filter(
                    _keyword_bucket(core, momentum_keywords_gate),
                    min_likes=10,
                    min_retweets=5,
                )
                buckets_for_gate["Trading Intent / Watchlist Signals"] = _keyword_bucket(core, watchlist_keywords_gate)

                gating_results: Dict[str, Dict[str, Any]] = {}
                for pn in gating_prompts:
                    gating_results[pn] = analyze_tweets_for_prompt(buckets_for_gate.get(pn, []), pn, ticker)

                if _is_good_enough(gating_results):
                    logger.info("✅ Deep Analyze early-stop: page-1 sentiment gate met (tweets=%s)", len(core))
                    break
            else:
                if _cheap_good_enough(core):
                    logger.info("✅ Deep Analyze early-stop: cheap gate met (pages=%s tweets=%s)", pages, len(core))
                    break

            if not next_token:
                break

        # Store only a corpus bought cleanly. One truncated by an X failure
        # would be frozen in and replayed to everyone until it expired, turning
        # a transient error into a sustained thin analysis.
        if _ticker_cached is None and not errors.get("ticker") and core:
            corpus_cache.put(
                "ticker", t, 48, ticker_query,
                tweets=core, pages_fetched=pages,
                stop_reason="early_stop" if len(core) < SAFETY_CAP_TWEETS else "safety_cap",
            )

        corpuses["ticker"] = core

        # Influencers
        infl_res = infl_future.result() if infl_future else {
            "success": True, "tweets": [], "wire_state": "not_configured",
            "wire_billed": 0}
        _wire_state = infl_res.get("wire_state") or "unknown"
        _wire_billed = infl_res.get("wire_billed") or 0
        if infl_res.get("success"):
            corpuses["influencers"] = infl_res.get("tweets", []) or []
        else:
            corpuses["influencers"] = []
            errors["influencers"] = infl_res.get("error", "Unknown error")

    core = corpuses["ticker"]
    influencers = corpuses["influencers"]

    logger.info("🧾 Deep Analyze corpora summary: ticker=%s influencer=%s", len(core), len(influencers))

    # Hand the RAW corpora to any caller that asks for them, so the evidence
    # ledger can be built from posts rather than reconstructed from the eight
    # angle summaries -- which is impossible, since the angles discard the
    # per-post detail the ledger exists to record.
    #
    # An optional out-parameter rather than an extra return value or a new key
    # in `results`: every existing caller keeps working untouched, and a stray
    # key in `results` would be read as a ninth angle by generate_ai_summary.
    if sink is not None:
        sink["ticker_corpus"] = core
        sink["influencer_corpus"] = influencers
        sink["alias"] = _alias
        # WHY the wire corpus is the size it is, and what it actually cost.
        # Without these, "0 posts" cannot be told from "the query failed" or
        # "served free from cache" -- and the decision this feeds is whether to
        # keep paying for the channel at all.
        sink["wire_state"] = _wire_state
        sink["wire_billed"] = _wire_billed
        sink["ticker_billed"] = _ticker_billed
        # THE TOTAL, which is the number that matters for a spend budget and for
        # deciding whether a failed run is owed a refund. A cached ticker corpus
        # contributes 0, which is the point.
        sink["posts_billed"] = _ticker_billed + _wire_billed
        # How old the evidence was. created_at is the WRITE time; a corpus can
        # be hours old, and a forward return anchored to the wrong moment is the
        # thing signal_log exists to make impossible.
        sink["corpus_age_s"] = _corpus_age_s
        # Which corpus this analysis was built from. signal_log stores it so a
        # disputed call can be reconstructed against the exact posts, rather
        # than against whatever the query returns when someone looks later --
        # X's index is 7 days deep, so "run it again" is not available.
        try:
            sink["corpus_key"] = _cache_key()
        except Exception:
            logger.warning("could not derive corpus_key for sink", exc_info=True)

    catalyst_keywords = [
        "breaking",
        "news",
        "catalyst",
        "earnings",
        "guidance",
        "partnership",
        "launch",
        "acquisition",
        "merger",
        "fda",
        "sec",
        "upgrade",
        "downgrade",
        "price target",
        "pt ",
    ]
    retail_keywords = ["retail", "institutional", "fintwit", "#fintwit", "fund", "hedge", "whale"]
    # BARE "risk" IS NOT A RED FLAG.
    #
    # The retrieval query itself contains `OR risk` as a search term, so the
    # corpus is deliberately seeded with posts containing that word -- and this
    # bucket then counted every one of them as a warning sign. Measured on the
    # genuine TSLA corpus, all three "risk items" were false positives, the
    # first being the literal phrase "would reset risk/reward nicely".
    #
    # That was harmless while red_flag_rate was structurally 0.0 and the
    # Avoid-on-risk rule could never fire. Repairing the rate makes the rule
    # live, so a corpus of ordinary risk-management chatter would have crossed
    # the 0.35 threshold and returned Avoid with no negative sentiment anywhere.
    # The threshold itself has never been calibrated against a non-zero metric.
    risk_keywords = [
        "red flag",
        "dilution",
        "bankruptcy",
        "delisting",
        "investigation",
        "fraud",
        "lawsuit",
        "downgrade",
        "guidance cut",
        "recall",
        "probe",
    ]
    watchlist_keywords = [
        "watchlist",
        "setup",
        "breakout",
        "entry",
        "support",
        "resistance",
        "levels",
        "trade",
        "trading",
        "swing",
        "scalp",
        "calls",
        "puts",
        "options",
    ]

    # Local-scrubbed buckets from the ticker corpus (core) + influencer corpus.
    narrative_keywords = ["emerging", "trend", "trending", "gaining", "traction", "rotation", "narrative", "macro", "cycle", "tailwind", "headwind"]
    momentum_keywords = ["viral", "trending", "momentum", "breakout", "squeeze", "runner", "rip", "ripping"]

    buckets: Dict[str, List[Dict[str, Any]]] = {
        "Real-Time Market Sentiment": core,
        "Sector Narrative & Trends": _keyword_bucket(core, narrative_keywords),
        "Track Smart Money and Influencer Moves": influencers,
        "Momentum (High Engagement)": _engagement_filter(_keyword_bucket(core, momentum_keywords), min_likes=10, min_retweets=5),
        "Monitor Breaking News and Catalysts": _keyword_bucket(core, catalyst_keywords),
        "Gauge Retail vs. Institutional Sentiment": _keyword_bucket(core, retail_keywords),
        "Detect Early Warning Signs and Red Flags": _keyword_bucket(core, risk_keywords),
        "Trading Intent / Watchlist Signals": _keyword_bucket(core, watchlist_keywords),
    }

    # ---- Analyze each bucket into the expected per-prompt result schema ----
    results: Dict[str, Dict[str, Any]] = {}

    for prompt_name in ANALYSIS_PROMPTS.keys():
        tweets = buckets.get(prompt_name, [])

        # If the underlying influencer corpus call is unavailable, surface that as an error insight.
        if prompt_name == "Track Smart Money and Influencer Moves" and not influencer_query:
            results[prompt_name] = {
                "sentiment_score": 0.0,
                "overall_sentiment": "error",
                "key_themes": [],
                "insights": "Influencer list not found. Add data/influencers.json and run scripts/validate_influencers.py.",
                "sample_tweets": [],
                "mention_count": 0,
            }
            continue

        # Add helpful error context if that corpus failed
        if prompt_name == "Real-Time Market Sentiment" and "ticker" in errors:
            results[prompt_name] = {
                "sentiment_score": 0.0,
                "overall_sentiment": "error",
                "key_themes": [],
                "insights": f"Search failed: {errors['ticker']}",
                "sample_tweets": [],
                "mention_count": 0,
            }
            continue
        if prompt_name == "Sector Narrative & Trends" and "ticker" in errors:
            results[prompt_name] = {
                "sentiment_score": 0.0,
                "overall_sentiment": "error",
                "key_themes": [],
                "insights": f"Search failed: {errors['ticker']}",
                "sample_tweets": [],
                "mention_count": 0,
            }
            continue
        if prompt_name == "Momentum (High Engagement)" and "ticker" in errors:
            results[prompt_name] = {
                "sentiment_score": 0.0,
                "overall_sentiment": "error",
                "key_themes": [],
                "insights": f"Search failed: {errors['ticker']}",
                "sample_tweets": [],
                "mention_count": 0,
            }
            continue
        if prompt_name == "Track Smart Money and Influencer Moves" and "influencers" in errors:
            results[prompt_name] = {
                "sentiment_score": 0.0,
                "overall_sentiment": "error",
                "key_themes": [],
                "insights": f"Search failed: {errors['influencers']}",
                "sample_tweets": [],
                "mention_count": 0,
            }
            continue

        results[prompt_name] = analyze_tweets_for_prompt(tweets, prompt_name, ticker, _alias)

    # Cache only successful derived results. External API failures such as depleted X
    # credits should recover immediately after the billing/token issue is fixed.
    has_errors = any((r.get("overall_sentiment") or "").lower() == "error" for r in results.values())
    if not has_errors:
        try:
            _cache_set(results)
        except Exception:
            pass

    return results


def generate_ai_summary(analysis_results: Dict[str, Dict]) -> Dict[str, Any]:
    """Generate a Buy/Watch/Avoid recommendation from analysis_results (no extra AI calls).

    Key principles:
    - Prefer deduped evidence (unique tweet ids) over "how many prompts are bullish".
    - Use risk concentration (red_flag_rate) to avoid bullish calls during risk-heavy chatter.
    - Downweight mixed/unstable reads via a simple disagreement proxy.
    """

    bullish_prompts = 0
    bearish_prompts = 0
    neutral_prompts = 0
    error_prompts = 0

    unique_ids: set[str] = set()

    red_flag_sentiment = 0.0
    red_flag_ids: set[str] = set()

    # For a simple disagreement proxy
    prompt_sentiments: List[float] = []

    # Post-level sentiment, replacing the angle-weighted mean.
    #
    # The old weighting was first-contribution dedupe: an angle's weight was the
    # number of ids it was the FIRST to claim. Angle 1 is the entire corpus and
    # is iterated first, so it claimed every id and every later angle -- all
    # subsets of it -- got weight max(0,1) = 1. The result depended on
    # declaration order, and the seven remaining angles contributed ~1/N each
    # while appearing to be co-equal signals.
    #
    # Averaging each unique post once removes the ordering dependence entirely
    # and keeps the double-count protection the dedupe was there to provide.
    post_scores: Dict[str, float] = {}

    for prompt_name, result in (analysis_results or {}).items():
        tweet_ids = result.get("tweet_ids", []) or []
        ids = {str(tid) for tid in tweet_ids}
        unique_ids |= ids

        sentiment = float(result.get("sentiment_score", 0.0) or 0.0)
        overall = (result.get("overall_sentiment") or "neutral").lower()
        mention_count = int(result.get("mention_count", 0) or 0)
        has_evidence = mention_count > 0 and overall != "error"

        # True per-post scores when the angle supplies them. Identical posts
        # appearing in several angles resolve to the same value, so the order
        # of assignment is irrelevant -- which is the property the previous
        # setdefault-over-angle-means version only appeared to have.
        per_post = result.get("post_scores") or {}
        if per_post:
            post_scores.update({str(k): float(v) for k, v in per_post.items()})
        else:
            # Legacy/fixture results without per-post detail: fall back to the
            # angle mean, but never overwrite a real per-post value.
            for tid in ids:
                post_scores.setdefault(tid, sentiment)

        # EMPTY IS MISSING, NOT NEUTRAL. An angle with no posts previously
        # returned overall_sentiment="neutral", indistinguishable from a
        # genuinely balanced one -- so six empty angles rendered as "signals are
        # neutral across all 8 analysis types" and, because emptiness cannot
        # disagree, "conviction is higher". A spam corpus earned Moderate
        # confidence that way.
        if not has_evidence:
            continue

        if overall == "bullish":
            bullish_prompts += 1
        elif overall == "bearish":
            bearish_prompts += 1
        elif overall == "neutral":
            neutral_prompts += 1
        else:
            error_prompts += 1

        prompt_sentiments.append(sentiment)

        if "Red Flags" in prompt_name:
            red_flag_sentiment = sentiment
            red_flag_ids = ids

    # How many angles actually carried evidence. Without this the three
    # reassurance lines below fire on an EMPTY corpus -- measured, with all
    # eight angles empty: "Signals are neutral across all 0 analysis types",
    # "No red flags or warning signals detected", and "conviction is higher".
    # That is emptiness inflating confidence, which 1.3 fixed in the counters
    # and not in the prose the user actually reads.
    evidenced_prompts = bullish_prompts + bearish_prompts + neutral_prompts

    total_mentions = len(unique_ids)
    avg_sentiment = (sum(post_scores.values()) / len(post_scores)) if post_scores else 0.0

    # MEMBERSHIP, not first-contribution. The numerator was previously the ids
    # the risk angle claimed FIRST, which -- being a subset of angle 1, already
    # iterated -- was always zero. red_flag_rate was therefore structurally 0.0
    # on every run ever made, the Avoid-on-risk rule could never fire on it, and
    # all three retained corpora rendered "No red flags detected" including the
    # one whose risk angle held nine posts.
    red_flag_rate = (len(red_flag_ids) / total_mentions) if total_mentions else 0.0

    # Disagreement proxy: range across prompt-level signed sentiments
    if prompt_sentiments:
        disagreement = max(prompt_sentiments) - min(prompt_sentiments)
    else:
        disagreement = 0.0

    # ---- Recommendation rules ----
    # Thin evidence => Watch
    if total_mentions < 8:
        recommendation = "Watch"
    # Risk-heavy chatter => Avoid
    elif red_flag_rate >= 0.35 or red_flag_sentiment <= -0.2:
        recommendation = "Avoid"
    # Strong bearish tilt => Avoid
    elif avg_sentiment <= -0.12:
        recommendation = "Avoid"
    # Strong bullish tilt, not risk-heavy, not wildly mixed => Buy
    elif avg_sentiment >= 0.22 and red_flag_rate <= 0.20 and disagreement <= 0.55 and total_mentions >= 20:
        recommendation = "Buy"
    else:
        recommendation = "Watch"

    # ---- Confidence rules ----
    # Evidence + strength, penalized by risk + disagreement.
    if total_mentions < 8:
        confidence = "Low"
    else:
        base = 0
        if total_mentions >= 40:
            base += 2
        elif total_mentions >= 20:
            base += 1

        if abs(avg_sentiment) >= 0.25:
            base += 2
        elif abs(avg_sentiment) >= 0.15:
            base += 1

        if red_flag_rate >= 0.25:
            base -= 1
        if disagreement >= 0.65:
            base -= 1

        if base >= 3:
            confidence = "High"
        elif base >= 1:
            confidence = "Moderate"
        else:
            confidence = "Low"

    # ---- Rationale (human-readable sentences) ----
    rationale: List[str] = []

    # Sentiment direction + strength
    if abs(avg_sentiment) >= 0.30:
        strength_word = "strongly"
    elif abs(avg_sentiment) >= 0.15:
        strength_word = "moderately"
    else:
        strength_word = "slightly"

    direction = "bullish" if avg_sentiment > 0 else "bearish" if avg_sentiment < 0 else "neutral"
    if avg_sentiment == 0 or abs(avg_sentiment) < 0.05:
        rationale.append(f"Social sentiment is largely neutral with no clear directional bias.")
    else:
        rationale.append(f"Social sentiment is {strength_word} {direction} with a score of {avg_sentiment:.2f}.")

    # Signal agreement
    if bullish_prompts > 0 and bearish_prompts == 0:
        rationale.append(f"All {bullish_prompts} analysis signals point bullish — no conflicting bearish signals detected.")
    elif bearish_prompts > 0 and bullish_prompts == 0:
        rationale.append(f"All {bearish_prompts} analysis signals point bearish — no conflicting bullish signals detected.")
    elif bullish_prompts > 0 and bearish_prompts > 0:
        rationale.append(f"Mixed signals: {bullish_prompts} bullish vs {bearish_prompts} bearish across analysis types.")
    else:
        if evidenced_prompts:
            rationale.append(f"Signals are neutral across all {neutral_prompts} analysis types.")
        else:
            rationale.append("No analysis angle returned any usable evidence.")

    # Evidence volume
    if total_mentions >= 50:
        rationale.append(f"Strong evidence base: {total_mentions} unique mentions analysed.")
    elif total_mentions >= 20:
        rationale.append(f"Moderate evidence base: {total_mentions} unique mentions analysed.")
    else:
        rationale.append(f"Limited evidence: only {total_mentions} mentions found. Treat this signal with caution.")

    # Risk
    if red_flag_rate == 0:
        rationale.append(
            "No red flags or warning signals detected in recent posts."
            if evidenced_prompts else
            "Not enough evidence to say whether risk signals are present."
        )
    elif red_flag_rate <= 0.10:
        rationale.append(f"Low red-flag rate ({red_flag_rate:.0%}) — minimal warning signals in posts.")
    else:
        rationale.append(f"Elevated red-flag rate ({red_flag_rate:.0%}) — some warning signals detected.")

    # Consistency
    if disagreement >= 0.65:
        rationale.append("Signals are inconsistent — high disagreement between analysis types.")
    elif disagreement >= 0.40:
        rationale.append("Some disagreement between analysis types — moderate conviction only.")
    else:
        rationale.append(
            "Signals are consistent across analysis types — conviction is higher."
            if evidenced_prompts >= 2 else
            "Too few populated angles to judge consistency."
        )

    return {
        "recommendation": recommendation,
        "confidence": confidence,
        "avg_sentiment": avg_sentiment,
        "rationale": rationale,
        # Both were computed above and then discarded. The legacy verdict_log
        # row stored NULL in these two columns -- "not measured" -- for
        # readings that existed three frames down this same function.
        "red_flag_rate": red_flag_rate,
        "disagreement": disagreement,
    }
