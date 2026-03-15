import streamlit as st
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any
from pathlib import Path
import json
import logging

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

def search_x_tweets(query: str, max_results: int = 100, timeframe: str = "24h") -> Dict[str, Any]:
    """Search X (Twitter) Recent Search for tweets matching the query.

    Pagination notes:
    - X Recent Search supports up to 100 results per request.
    - If `max_results` > 100, we paginate using `meta.next_token` until we reach
      `max_results` or a safety cap.
    - Default behavior (max_results=100) remains a single call.
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

        # X API: max_results must be between 10 and 100 per request.
        per_page = 100
        target_total = max(0, int(max_results))
        params = {
            "query": query,
            "max_results": min(max(target_total or 100, 10), per_page),
            "tweet.fields": "text,created_at,public_metrics,author_id",
        }
        if since_date:
            params["start_time"] = since_date

        # Safety cap: even if X has more, do not paginate forever.
        # With per_page=100, max_pages=5 caps this call at ~500 tweets.
        max_pages = 5

        all_tweets: List[Dict[str, Any]] = []
        last_meta: Dict[str, Any] = {}
        next_token = None

        # If caller only asked for <=100 results, do a single request.
        pages_to_fetch = 1 if target_total <= per_page else max_pages

        for page in range(1, pages_to_fetch + 1):
            if next_token:
                params["next_token"] = next_token
            else:
                params.pop("next_token", None)

            response = requests.get(url, headers=headers, params=params, timeout=30)

            if response.status_code != 200:
                # Never leak raw provider response text to the UI.
                logger.info("📡 X API response status: %s", response.status_code)
                return {
                    "success": False,
                    "error": f"API Error {response.status_code}",
                    "tweets": [],
                }

            data = response.json() or {}
            tweets = data.get("data", []) or []
            meta = data.get("meta", {}) or {}
            last_meta = meta

            all_tweets.extend(tweets)
            next_token = meta.get("next_token")

            # Log pagination state before stop/continue decisions.
            logger.info(
                "📄 X page=%s got=%s total=%s has_next=%s",
                page,
                len(tweets),
                len(all_tweets),
                bool(next_token),
            )

            if not next_token:
                logger.info("✅ X pagination finished at page=%s (no next_token)", page)
                break

            if target_total and len(all_tweets) >= target_total:
                logger.info("🛑 X pagination stop at page=%s (hit target_total=%s)", page, target_total)
                break

            if page >= max_pages:
                logger.info("🛑 X pagination stop at page=%s (hit max_pages=%s)", page, max_pages)
                break

        # Trim to requested size (if max_results was set > 0)
        out = all_tweets[:target_total] if target_total else all_tweets

        return {
            "success": True,
            "tweets": out,
            "meta": last_meta,
        }

    except Exception:
        # Keep internal error generic; full trace should be logged by callers.
        return {
            "success": False,
            "error": "Request failed",
            "tweets": [],
        }


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
    """Deep analyze with 4 X API calls, then derive 8 prompt outputs locally."""

    # ---- Call definitions (fixed defaults per your request) ----
    core_query = (
        f"{ticker} (stock OR shares OR price OR chart OR earnings OR news OR catalyst OR bullish OR bearish OR risk OR watchlist OR trading OR options OR #FinTwit) "
        f"lang:en -is:retweet"
    )
    trends_query = f"{sector} (emerging OR trend OR gaining OR traction OR rotation OR narrative) lang:en -is:retweet"
    momentum_query = f"({ticker} OR {sector}) (viral OR trending OR momentum OR breakout OR squeeze) lang:en -is:retweet"

    # Cap influencers to keep the `from:` query under X query length limits.
    influencer_usernames = _load_influencer_usernames(limit=12)
    influencer_query = (
        _build_influencer_query(influencer_usernames, ticker, sector)
        if influencer_usernames
        else None
    )

    # ---- Run the 4 calls in parallel ----
    def _fetch(name: str, query: str, timeframe: str) -> tuple:
        res = search_x_tweets(query=query, timeframe=timeframe, max_results=100)
        return name, res

    corpuses: Dict[str, List[Dict[str, Any]]] = {
        "core": [],
        "trends": [],
        "momentum": [],
        "influencers": [],
    }

    errors: Dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [
            ex.submit(_fetch, "core", core_query, "48h"),
            ex.submit(_fetch, "trends", trends_query, "24h"),
            ex.submit(_fetch, "momentum", momentum_query, "24h"),
        ]
        if influencer_query:
            futures.append(ex.submit(_fetch, "influencers", influencer_query, "72h"))

        for fut in as_completed(futures):
            name, res = fut.result()
            if res.get("success"):
                corpuses[name] = res.get("tweets", []) or []
            else:
                corpuses[name] = []
                errors[name] = res.get("error", "Unknown error")

    # ---- Build buckets for the 8 prompts ----
    core = corpuses["core"]
    trends = corpuses["trends"]
    momentum = corpuses["momentum"]
    influencers = corpuses["influencers"]

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

    buckets: Dict[str, List[Dict[str, Any]]] = {
        "Real-Time Market Sentiment": core,
        "Sector Narrative & Trends": trends,
        "Track Smart Money and Influencer Moves": influencers,
        "Momentum (High Engagement)": _engagement_filter(momentum, min_likes=10, min_retweets=5),
        "Monitor Breaking News and Catalysts": _keyword_bucket(core, catalyst_keywords),
        "Gauge Retail vs. Institutional Sentiment": _keyword_bucket(core, retail_keywords),
        "Detect Early Warning Signs and Red Flags": _keyword_bucket(core, risk_keywords),
        "Trading Intent / Watchlist Signals": _keyword_bucket(core, watchlist_keywords),
    }

    # ---- Analyze each bucket into the expected per-prompt result schema ----
    results: Dict[str, Dict[str, Any]] = {}

    for prompt_name in ANALYSIS_PROMPTS.keys():
        tweets = buckets.get(prompt_name, [])

        # If the underlying corpus call failed, surface that as an error insight.
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
        if prompt_name == "Real-Time Market Sentiment" and "core" in errors:
            results[prompt_name] = {
                "sentiment_score": 0.0,
                "overall_sentiment": "error",
                "key_themes": [],
                "insights": f"Search failed: {errors['core']}",
                "sample_tweets": [],
                "mention_count": 0,
            }
            continue
        if prompt_name == "Sector Narrative & Trends" and "trends" in errors:
            results[prompt_name] = {
                "sentiment_score": 0.0,
                "overall_sentiment": "error",
                "key_themes": [],
                "insights": f"Search failed: {errors['trends']}",
                "sample_tweets": [],
                "mention_count": 0,
            }
            continue
        if prompt_name == "Momentum (High Engagement)" and "momentum" in errors:
            results[prompt_name] = {
                "sentiment_score": 0.0,
                "overall_sentiment": "error",
                "key_themes": [],
                "insights": f"Search failed: {errors['momentum']}",
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

    return results


def generate_ai_summary(analysis_results: Dict[str, Dict]) -> Dict[str, Any]:
    """Generate a single AI-powered recommendation with rationale."""
    total_weighted_sentiment = 0.0
    total_weight = 0
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    error_count = 0
    # Dedupe across prompts to avoid "confidence inflation" when the same tweets appear in multiple buckets.
    unique_ids = set()

    red_flag_sentiment = 0.0
    red_flag_mentions = 0

    for prompt_name, result in analysis_results.items():
        tweet_ids = result.get("tweet_ids", []) or []
        unique_mentions = 0
        for tid in tweet_ids:
            if tid not in unique_ids:
                unique_ids.add(tid)
                unique_mentions += 1

        sentiment = result.get("sentiment_score", 0.0)
        overall = result.get("overall_sentiment", "neutral")

        weight = max(unique_mentions, 1)
        total_weighted_sentiment += sentiment * weight
        total_weight += weight

        if overall == "bullish":
            bullish_count += 1
        elif overall == "bearish":
            bearish_count += 1
        elif overall == "neutral":
            neutral_count += 1
        else:
            error_count += 1

        if "Red Flags" in prompt_name:
            red_flag_sentiment = sentiment
            red_flag_mentions = unique_mentions

    total_mentions = len(unique_ids)
    avg_sentiment = total_weighted_sentiment / total_weight if total_weight else 0.0

    # Sanity guard: avoid strong calls on thin evidence
    if total_mentions < 8:
        recommendation = "Watch"
    elif avg_sentiment >= 0.25 and bullish_count >= 5 and red_flag_sentiment > -0.1:
        recommendation = "Buy"
    elif avg_sentiment <= -0.1 or bearish_count >= 4 or red_flag_sentiment < -0.2:
        recommendation = "Avoid"
    else:
        recommendation = "Watch"

    # Sanity guard: if evidence is thin, force low confidence.
    if total_mentions < 8:
        confidence = "Low"
    elif abs(avg_sentiment) >= 0.35 and total_mentions >= 30:
        confidence = "High"
    elif abs(avg_sentiment) >= 0.2 or total_mentions >= 15:
        confidence = "Moderate"
    else:
        confidence = "Low"

    # Build comprehensive rationale with critical insights
    rationale = []
    
    # 1. Consensus breakdown
    consensus_text = f"{bullish_count} bullish, {bearish_count} bearish, {neutral_count} neutral"
    if bullish_count + bearish_count == 0:
        consensus_level = "No clear consensus"
    elif max(bullish_count, bearish_count) == 0:
        consensus_level = "Neutral consensus"
    elif max(bullish_count, bearish_count) / max(bullish_count + bearish_count, 1) >= 0.67:
        consensus_level = "Strong consensus"
    else:
        consensus_level = "Mixed signals"
    
    rationale.append(f"{consensus_text} ({consensus_level})")
    
    # 2. Sentiment strength
    if abs(avg_sentiment) >= 0.3:
        sentiment_level = "Strong"
    elif abs(avg_sentiment) >= 0.15:
        sentiment_level = "Moderate"
    else:
        sentiment_level = "Weak"
    
    rationale.append(f"Sentiment: {sentiment_level} {'bullish' if avg_sentiment > 0 else 'bearish' if avg_sentiment < 0 else 'neutral'} ({avg_sentiment:.3f})")
    
    # 3. Evidence quality
    if total_mentions >= 50:
        evidence_text = f"Strong evidence base ({total_mentions} unique mentions)"
    elif total_mentions >= 20:
        evidence_text = f"Moderate evidence ({total_mentions} unique mentions)"
    else:
        evidence_text = f"Limited evidence ({total_mentions} mentions)"
    
    rationale.append(evidence_text)
    
    # 4. Red flags
    if red_flag_mentions > 0:
        if red_flag_sentiment < -0.2:
            rationale.append("⚠️ Strong risk signals detected - caution advised")
        elif red_flag_sentiment < -0.05:
            rationale.append("⚠️ Moderate risk signals present")
        else:
            rationale.append("✓ Red-flag analysis shows limited concerns")
    
    # 5. Momentum
    if bullish_count - bearish_count >= 3:
        rationale.append("✓ Bullish momentum outweighs bearish pressure")
    elif bearish_count - bullish_count >= 3:
        rationale.append("✗ Bearish momentum outweighs bullish pressure")

    return {
        "recommendation": recommendation,
        "confidence": confidence,
        "avg_sentiment": avg_sentiment,
        "rationale": rationale,
    }
