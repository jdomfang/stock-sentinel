import streamlit as st
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any

from utils.sentiment import extract_tickers, analyze_sentiment

ANALYSIS_PROMPTS = {
    "Real-Time Market Sentiment": {
        "description": "Latest discussions about the stock and overall sentiment (24-48h)",
        "query_builder": lambda t, s: f"{t} (bullish OR bearish OR sentiment) lang:en -is:retweet",
        "timeframe": "48h",
        "max_results": 50
    },
    "Emerging Trends Before Wall Street": {
        "description": "Emerging trends in the sector gaining traction",
        "query_builder": lambda t, s: f"{s} (emerging OR trend OR gaining OR traction) lang:en -is:retweet",
        "timeframe": "24h",
        "max_results": 40
    },
    "Track Smart Money and Influencer Moves": {
        "description": "Activity from notable investors and analysts",
        "query_builder": lambda t, s: f"({t} OR {s}) (from:CathieWood OR from:chamath OR analyst OR investor) lang:en -is:retweet",
        "timeframe": "72h",
        "max_results": 30
    },
    "Identify Stocks with Viral Momentum": {
        "description": "Stocks gaining viral momentum with high engagement",
        "query_builder": lambda t, s: f"({t} OR {s}) min_faves:10 min_retweets:5 lang:en -is:retweet",
        "timeframe": "24h",
        "max_results": 35
    },
    "Monitor Breaking News and Catalysts": {
        "description": "Breaking news and catalysts related to the stock",
        "query_builder": lambda t, s: f"{t} (breaking OR news OR catalyst OR earnings OR launch OR partnership) lang:en -is:retweet",
        "timeframe": "24h",
        "max_results": 45
    },
    "Gauge Retail vs. Institutional Sentiment": {
        "description": "Difference between retail and institutional sentiment",
        "query_builder": lambda t, s: f"{t} (retail OR institutional OR FinTwit OR #FinTwit) lang:en -is:retweet",
        "timeframe": "48h",
        "max_results": 40
    },
    "Detect Early Warning Signs and Red Flags": {
        "description": "Red flags, concerns, and negative sentiment",
        "query_builder": lambda t, s: f"{t} (bearish OR warning OR risk OR concern OR red OR flag) -bullish lang:en -is:retweet",
        "timeframe": "72h",
        "max_results": 35
    },
    "Create a Real-Time Watchlist Strategy": {
        "description": "Monitor stocks for trading opportunities and strategy",
        "query_builder": lambda t, s: f"({t} OR {s}) (#stocks OR watchlist OR trading OR opportunity) lang:en -is:retweet",
        "timeframe": "24h",
        "max_results": 30
    }
}

def search_x_tweets(query: str, max_results: int = 30, timeframe: str = "24h") -> Dict:
    """Search X (Twitter) for tweets matching the query."""
    try:
        x_bearer_token = st.secrets["X_BEARER_TOKEN"]

        since_date = None
        if timeframe:
            hours = int(timeframe.replace('h', ''))
            since_date = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')

        url = "https://api.twitter.com/2/tweets/search/recent"
        headers = {"Authorization": f"Bearer {x_bearer_token}"}
        params = {
            "query": query,
            "max_results": min(max_results, 100),
            "tweet.fields": "text,created_at,public_metrics,author_id"
        }

        if since_date:
            params["start_time"] = since_date

        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "tweets": data.get('data', []),
                "meta": data.get('meta', {})
            }
        return {
            "success": False,
            "error": f"API Error {response.status_code}: {response.text}",
            "tweets": []
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "tweets": []
        }


def analyze_tweets_for_prompt(tweets: List[Dict], prompt_name: str, ticker: str) -> Dict:
    """Analyze tweets for a specific prompt and return insights."""
    if not tweets:
        return {
            "sentiment_score": 0.0,
            "overall_sentiment": "neutral",
            "key_themes": [],
            "insights": "No tweets found for analysis.",
            "sample_tweets": [],
            "mention_count": 0
        }

    sentiments = []
    themes = []
    sample_tweets = []

    for tweet in tweets:
        text = tweet.get('text', '')

        tickers = extract_tickers(text)
        if ticker.upper() not in [t.upper() for t in tickers]:
            continue

        sentiment_result = analyze_sentiment(text)
        sentiments.append(sentiment_result['score'])

        text_lower = text.lower()
        if prompt_name == "Real-Time Market Sentiment":
            if any(word in text_lower for word in ['bullish', 'buy', 'moon', 'rocket']):
                themes.append('bullish')
            if any(word in text_lower for word in ['bearish', 'sell', 'crash', 'dump']):
                themes.append('bearish')
            if any(word in text_lower for word in ['earnings', 'news', 'catalyst']):
                themes.append('news/catalyst')

        elif prompt_name == "Emerging Trends Before Wall Street":
            if any(word in text_lower for word in ['trend', 'emerging', 'gaining', 'traction']):
                themes.append('emerging')
            if any(word in text_lower for word in ['small', 'mid', 'large']):
                themes.append('market_cap')

        elif prompt_name == "Track Smart Money and Influencer Moves":
            if 'from:' in text_lower or any(word in text_lower for word in ['cathiewood', 'chamath', 'analyst', 'investor']):
                themes.append('smart_money')

        elif prompt_name == "Identify Stocks with Viral Momentum":
            metrics = tweet.get('public_metrics', {})
            if metrics.get('like_count', 0) > 10 or metrics.get('retweet_count', 0) > 5:
                themes.append('viral')

        if len(sample_tweets) < 3:
            short_text = text[:100] + "..." if len(text) > 100 else text
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
        "key_themes": list(set(themes))[:5],
        "insights": f"Found {len(sentiments)} relevant tweets with {overall_sentiment} sentiment.",
        "sample_tweets": sample_tweets,
        "mention_count": len(sentiments)
    }


def run_deep_analysis(ticker: str, sector: str) -> Dict[str, Dict]:
    """Run all 8 analysis prompts in parallel."""
    results = {}

    def analyze_prompt(prompt_name: str, config: Dict) -> tuple:
        query = config["query_builder"](ticker, sector)
        search_result = search_x_tweets(
            query=query,
            max_results=config["max_results"],
            timeframe=config["timeframe"]
        )

        if search_result["success"]:
            analysis = analyze_tweets_for_prompt(search_result["tweets"], prompt_name, ticker)
        else:
            analysis = {
                "sentiment_score": 0.0,
                "overall_sentiment": "error",
                "key_themes": [],
                "insights": f"Search failed: {search_result.get('error', 'Unknown error')}",
                "sample_tweets": [],
                "mention_count": 0
            }

        return prompt_name, analysis

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(analyze_prompt, name, config)
            for name, config in ANALYSIS_PROMPTS.items()
        ]

        for future in as_completed(futures):
            prompt_name, analysis = future.result()
            results[prompt_name] = analysis

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
    elif bearish_count > bullish_count:
        rationale.append("Bearish signals outweigh bullish signals — caution advised.")

    if total_mentions < 10:
        rationale.append("Low mention volume suggests weak consensus; wait for clarity.")

    return {
        "recommendation": recommendation,
        "confidence": confidence,
        "avg_sentiment": round(avg_sentiment, 3),
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "neutral_count": neutral_count,
        "error_count": error_count,
        "total_mentions": total_mentions,
        "rationale": rationale
    }