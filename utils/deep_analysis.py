import streamlit as st
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any
from pathlib import Path
import json

from utils.sentiment import analyze_sentiment

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
    from_clause = " OR ".join([f"from:{u}" for u in usernames])
    # Require relevance: mention ticker symbol or sector.
    return f"(({from_clause})) ({ticker} OR {sector}) lang:en -is:retweet"


# ---- X API search ----

def search_x_tweets(query: str, max_results: int = 100, timeframe: str = "24h") -> Dict[str, Any]:
    """Search X (Twitter) Recent Search for tweets matching the query."""
    try:
        x_bearer_token = st.secrets["X_BEARER_TOKEN"]

        since_date = None
        if timeframe:
            hours = int(timeframe.replace("h", ""))
            since_date = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        url = "https://api.twitter.com/2/tweets/search/recent"
        headers = {"Authorization": f"Bearer {x_bearer_token}"}
        params = {
            "query": query,
            "max_results": min(int(max_results), 100),
            "tweet.fields": "text,created_at,public_metrics,author_id",
        }
        if since_date:
            params["start_time"] = since_date

        response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "tweets": data.get("data", []) or [],
                "meta": data.get("meta", {}) or {},
            }

        return {
            "success": False,
            "error": f"API Error {response.status_code}: {response.text}",
            "tweets": [],
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "tweets": [],
        }


# ---- Bucketing + analysis ----

def _mentions_ticker(text: str, ticker: str) -> bool:
    t = (ticker or "").strip().upper()
    if not t:
        return False
    s = (text or "")
    su = s.upper()
    # match either $TICKER or plain TICKER token
    return (f"${t}" in su) or (t in su)


def analyze_tweets_for_prompt(tweets: List[Dict[str, Any]], prompt_name: str, ticker: str) -> Dict[str, Any]:
    """Analyze tweets for a specific prompt and return insights.

    Notes:
    - We filter by ticker mention for ticker-scoped prompts.
    - We do NOT filter by ticker for the sector trends prompt.
    """
    if not tweets:
        return {
            "sentiment_score": 0.0,
            "overall_sentiment": "neutral",
            "key_themes": [],
            "insights": "No tweets found for analysis.",
            "sample_tweets": [],
            "mention_count": 0,
        }

    ticker_scoped = prompt_name != "Sector Narrative & Trends"

    sentiments: List[float] = []
    themes: List[str] = []
    sample_tweets: List[str] = []

    for tweet in tweets:
        text = (tweet.get("text") or "")
        if ticker_scoped and not _mentions_ticker(text, ticker):
            continue

        sentiment_result = analyze_sentiment(text)
        sentiments.append(sentiment_result["score"])

        text_lower = text.lower()

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

        if len(sample_tweets) < 3:
            short_text = text[:160] + "..." if len(text) > 160 else text
            sample_tweets.append(short_text)

    avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

    if avg_sentiment > 0.1:
        overall_sentiment = "bullish"
    elif avg_sentiment < -0.1:
        overall_sentiment = "bearish"
    else:
        overall_sentiment = "neutral"

    return {
        "sentiment_score": round(avg_sentiment, 3),
        "overall_sentiment": overall_sentiment,
        "key_themes": list(dict.fromkeys(themes))[:5],
        "insights": f"Found {len(sentiments)} relevant tweets with {overall_sentiment} sentiment.",
        "sample_tweets": sample_tweets,
        "mention_count": len(sentiments),
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

    influencer_usernames = _load_influencer_usernames(limit=40)
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
    total_mentions = 0
    red_flag_sentiment = 0.0
    red_flag_mentions = 0

    for prompt_name, result in analysis_results.items():
        mentions = result.get("mention_count", 0)
        sentiment = result.get("sentiment_score", 0.0)
        overall = result.get("overall_sentiment", "neutral")

        weight = max(mentions, 1)
        total_weighted_sentiment += sentiment * weight
        total_weight += weight
        total_mentions += mentions

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
            red_flag_mentions = mentions

    avg_sentiment = total_weighted_sentiment / total_weight if total_weight else 0.0

    if avg_sentiment >= 0.25 and bullish_count >= 5 and red_flag_sentiment > -0.1:
        recommendation = "Buy"
    elif avg_sentiment <= -0.1 or bearish_count >= 4 or red_flag_sentiment < -0.2:
        recommendation = "Avoid"
    else:
        recommendation = "Watch"

    if abs(avg_sentiment) >= 0.35 and total_mentions >= 30:
        confidence = "High conviction"
    elif abs(avg_sentiment) >= 0.2 or total_mentions >= 15:
        confidence = "Moderate conviction"
    else:
        confidence = "Low conviction"

    rationale = [
        f"{bullish_count}/8 analyses bullish; weighted sentiment {avg_sentiment:.2f}.",
        f"Total mentions analyzed: {total_mentions}.",
    ]

    if red_flag_mentions > 0:
        if red_flag_sentiment < -0.1:
            rationale.append("Red-flag channel shows elevated risk signals.")
        else:
            rationale.append("Red-flag channel does not show strong risk clusters.")

    if bullish_count - bearish_count >= 3:
        rationale.append("Bullish signals outweigh bearish signals across prompts.")

    return {
        "recommendation": recommendation,
        "confidence": confidence,
        "avg_sentiment": avg_sentiment,
        "rationale": rationale,
    }
