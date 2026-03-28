import streamlit as st
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any
from pathlib import Path
import json
import logging
import hashlib

from utils.sentiment import analyze_sentiment, load_sentiment_pipeline, score_finbert_output

logger = logging.getLogger(__name__)

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


def _build_influencer_query(usernames: List[str], ticker: str, sector: str) -> str:
    """Build a `from:` query safely under X query-length limits.

    X recent search queries have a max length (~512). We keep headroom to avoid 400 errors.

    IMPORTANT: Parentheses must be balanced; otherwise X returns 400 with
    "mismatched input '<EOF>' expecting ')'".
    """

    ticker = (ticker or "").strip().upper() or "NVDA"
    sector = (sector or "").strip().lower() or "tech"

    # Base filter we want regardless of influencer list.
    tail = f"({ticker} OR {sector}) lang:en -is:retweet"

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

        x_bearer_token = st.secrets.get("X_BEARER_TOKEN", os.getenv("X_BEARER_TOKEN"))
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
            logger.info("📡 X API response status: %s", response.status_code)
            return {"success": False, "error": f"API Error {response.status_code}", "tweets": []}

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

def _mentions_ticker(text: str, ticker: str) -> bool:
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
    return re.search(pattern, s, flags=re.IGNORECASE) is not None


def analyze_tweets_for_prompt(tweets: List[Dict[str, Any]], prompt_name: str, ticker: str) -> Dict[str, Any]:
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
        if ticker_scoped and not _mentions_ticker(text, ticker):
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

    try:
        # HF pipeline supports list input; this is much faster than per-tweet calls.
        sentiment_pipeline = load_sentiment_pipeline()
        BATCH_SIZE = 24
        batch_out_all = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch_out_all.extend(sentiment_pipeline(texts[i:i+BATCH_SIZE]))

        for tw, out in zip(filtered, batch_out_all):
            label = out.get("label")
            conf = float(out.get("score", 0.0))
            signed, _trading_sent, _label_norm = score_finbert_output(label=label, confidence=conf, neutral_threshold=0.55)

            sentiments.append(signed)

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

    except Exception:
        # Fallback to the slower, safer per-tweet sentiment path
        for tw in filtered:
            text = (tw.get("text") or "")
            sr = analyze_sentiment(text)
            sentiments.append(float(sr.get("score", 0.0)))

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


def run_deep_analysis(ticker: str, sector: str) -> Dict[str, Dict[str, Any]]:
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
    s = (sector or "").strip().lower()

    # ---- Cache helpers (session + disk) ----
    import os
    import time

    CACHE_VERSION = "v3"  # bump to invalidate old cached schemas/logic
    CACHE_TTL_S = 45 * 60

    def _cache_key() -> str:
        return f"deep_analysis:{CACHE_VERSION}:{t}:{s}:48h"

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
            if key in st.session_state:
                blob = st.session_state.get(key) or {}
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
            if blob.get("ticker") != t or blob.get("sector") != s:
                return None
            return blob.get("results")
        except Exception:
            return None

    def _cache_set(results: Dict[str, Dict[str, Any]]) -> None:
        key = _cache_key()
        blob = {
            "ts": time.time(),
            "ticker": t,
            "sector": s,
            "cache_version": CACHE_VERSION,
            "results": results,
        }
        try:
            st.session_state[key] = blob
        except Exception:
            pass
        try:
            _cache_path().write_text(json.dumps(blob), encoding="utf-8")
        except Exception:
            pass

    cached = _cache_get()
    if cached:
        logger.info("🧠 Deep Analyze cache hit: %s", _cache_key())
        return cached

    # ---- Call definitions ----
    ticker_query = (
        f"(${t} OR {t}) (stock OR stocks OR shares OR price OR chart OR earnings OR news OR catalyst "
        f"OR bullish OR bearish OR risk OR watchlist OR trading OR options OR #FinTwit) lang:en -is:retweet"
    )

    influencer_usernames = _load_influencer_usernames(limit=12)
    influencer_query = _build_influencer_query(influencer_usernames, t, s) if influencer_usernames else None

    corpuses: Dict[str, List[Dict[str, Any]]] = {"ticker": [], "influencers": []}
    errors: Dict[str, str] = {}

    # ---- Fetch influencer corpus (single call) in parallel with ticker pagination ----
    def _fetch_influencers() -> Dict[str, Any]:
        if not influencer_query:
            return {"success": True, "tweets": []}
        return search_x_tweets(query=influencer_query, timeframe="72h", max_results=100)

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
        risk_keywords_gate = [
            "risk",
            "warning",
            "concern",
            "red flag",
            "dilution",
            "offering",
            "bankruptcy",
            "delisting",
            "investigation",
            "fraud",
            "lawsuit",
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

        while len(core) < SAFETY_CAP_TWEETS:
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

        corpuses["ticker"] = core

        # Influencers
        infl_res = infl_future.result() if infl_future else {"success": True, "tweets": []}
        if infl_res.get("success"):
            corpuses["influencers"] = infl_res.get("tweets", []) or []
        else:
            corpuses["influencers"] = []
            errors["influencers"] = infl_res.get("error", "Unknown error")

    core = corpuses["ticker"]
    influencers = corpuses["influencers"]

    logger.info("🧾 Deep Analyze corpora summary: ticker=%s influencer=%s", len(core), len(influencers))

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
    risk_keywords = [
        "risk",
        "warning",
        "concern",
        "red flag",
        "dilution",
        "offering",
        "bankruptcy",
        "delisting",
        "investigation",
        "fraud",
        "lawsuit",
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

        results[prompt_name] = analyze_tweets_for_prompt(tweets, prompt_name, ticker)

    # Cache the final derived results (so future runs can re-render without new X calls)
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

    total_weighted_sentiment = 0.0
    total_weight = 0

    bullish_prompts = 0
    bearish_prompts = 0
    neutral_prompts = 0
    error_prompts = 0

    # Dedupe across prompts to avoid confidence inflation.
    unique_ids: set[str] = set()

    red_flag_sentiment = 0.0
    red_flag_mentions = 0

    # For a simple disagreement proxy
    prompt_sentiments: List[float] = []

    for prompt_name, result in (analysis_results or {}).items():
        tweet_ids = result.get("tweet_ids", []) or []
        unique_mentions = 0
        for tid in tweet_ids:
            if tid not in unique_ids:
                unique_ids.add(tid)
                unique_mentions += 1

        sentiment = float(result.get("sentiment_score", 0.0) or 0.0)
        overall = (result.get("overall_sentiment") or "neutral").lower()

        weight = max(unique_mentions, 1)
        total_weighted_sentiment += sentiment * weight
        total_weight += weight

        if overall == "bullish":
            bullish_prompts += 1
        elif overall == "bearish":
            bearish_prompts += 1
        elif overall == "neutral":
            neutral_prompts += 1
        else:
            error_prompts += 1

        # Only count prompt sentiment in disagreement if it has some evidence.
        if int(result.get("mention_count", 0) or 0) > 0 and overall != "error":
            prompt_sentiments.append(sentiment)

        if "Red Flags" in prompt_name:
            red_flag_sentiment = sentiment
            red_flag_mentions = unique_mentions

    total_mentions = len(unique_ids)
    avg_sentiment = (total_weighted_sentiment / total_weight) if total_weight else 0.0

    red_flag_rate = (red_flag_mentions / total_mentions) if total_mentions else 1.0

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
        rationale.append(f"Signals are neutral across all {neutral_prompts} analysis types.")

    # Evidence volume
    if total_mentions >= 50:
        rationale.append(f"Strong evidence base: {total_mentions} unique mentions analysed.")
    elif total_mentions >= 20:
        rationale.append(f"Moderate evidence base: {total_mentions} unique mentions analysed.")
    else:
        rationale.append(f"Limited evidence: only {total_mentions} mentions found. Treat this signal with caution.")

    # Risk
    if red_flag_rate == 0:
        rationale.append("No red flags or warning signals detected in recent posts.")
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
        rationale.append("Signals are consistent across analysis types — conviction is higher.")

    return {
        "recommendation": recommendation,
        "confidence": confidence,
        "avg_sentiment": avg_sentiment,
        "rationale": rationale,
    }
