"""
Stock projections module.
"""

import hashlib
import struct

import numpy as np
from typing import Dict, List, Optional
import logging

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _derive_seed(prices: List[float], sentiment_score: float, days: int) -> int:
    """Derive a stable seed from the simulation inputs.

    A constant seed would make every ticker draw the identical random path,
    correlating projections that should be independent. Hashing the inputs keeps
    each ticker on its own stream while still giving the same answer for the same
    question. hashlib rather than hash() because str/bytes hashing is salted per
    process, which would reintroduce the very nondeterminism this removes.
    """
    h = hashlib.sha256()
    for p in prices:
        h.update(struct.pack("<d", float(p)))
    h.update(struct.pack("<d", float(sentiment_score)))
    h.update(struct.pack("<i", int(days)))
    return int.from_bytes(h.digest()[:8], "little")


def simple_projection(
    prices: List[float],
    sentiment_score: float,
    days: int = 30,
    seed: Optional[int] = None,
) -> Dict:
    """
    Run a simple Monte Carlo simulation to project stock performance.

    Args:
        prices: List of historical closing prices
        sentiment_score: Signed sentiment score in [-1, 1] (used to adjust expected returns)
        days: Number of days to project forward (default 30)
        seed: Explicit RNG seed. When None the seed is derived from the inputs, so
            the same ticker with the same prices and sentiment always projects the
            same numbers. This used to draw from the global np.random with no seed,
            which meant Streamlit's rerun-per-interaction model showed the user a
            different forecast every time they touched the page without changing
            any input. Pass an explicit seed in tests.

    Returns:
        Dictionary with keys:
        - avg_gain: Average projected gain as percentage
        - gain_p10: 10th percentile projected gain (%)
        - gain_p90: 90th percentile projected gain (%)
        - suggested_hold_days: Average days to reach +5% target
        - success_rate: Percentage of simulations reaching +5% within timeframe
    """
    try:
        # Need at least 5 data points for meaningful projection
        if len(prices) < 5:
            return {
                'avg_gain': 0.0,
                'suggested_hold_days': 0,
                'success_rate': 0.0,
                'error': 'Insufficient price data'
            }
        
        # Calculate historical returns
        returns = []
        for i in range(1, len(prices)):
            daily_return = (prices[i] - prices[i-1]) / prices[i-1]
            returns.append(daily_return)
        
        # Calculate historical statistics (winsorize returns to reduce outlier blowups)
        returns = np.array(returns, dtype=float)
        lo, hi = np.percentile(returns, [2, 98])
        returns = np.clip(returns, lo, hi)

        mean_return = float(np.mean(returns))
        std_return = float(np.std(returns))
        # Hard cap volatility to avoid absurd simulations on noisy tickers
        std_return = min(std_return, 0.12)  # ~12% daily std cap

        logger.debug(f"📊 Historical stats: {len(prices)} prices, mean_return={mean_return:.6f}, std_return={std_return:.6f}")

        # Adjust mean return based on sentiment.
        # sentiment_score is expected to be in [-1, 1] (negative=bearish, positive=bullish).
        # Keep this effect small to avoid absurd projections.
        sentiment_score = float(max(-1.0, min(1.0, sentiment_score)))
        sentiment_multiplier = 1.0 + (0.25 * sentiment_score)  # +/- 25% drift adjustment
        adjusted_mean = mean_return * sentiment_multiplier

        logger.debug(
            f"🎭 Sentiment adjustment: score={sentiment_score:.3f}, multiplier={sentiment_multiplier:.3f}, adjusted_mean={adjusted_mean:.6f}"
        )
        
        # Run Monte Carlo simulations
        num_simulations = 100
        target_gain = 0.05  # 5% target

        # Local Generator, not the global np.random: seeding the global RNG would
        # silently change the draw sequence of every other caller in the process.
        rng = np.random.default_rng(
            seed if seed is not None else _derive_seed(prices, sentiment_score, days)
        )

        final_gains = []
        days_to_target = []

        for _ in range(num_simulations):
            # Start at last known price
            current_price = prices[-1]

            # Simulate daily price movements
            reached_target = False
            for day in range(1, days + 1):
                # Generate random return from normal distribution
                daily_return = rng.normal(adjusted_mean, std_return)
                current_price *= (1 + daily_return)
                
                # Check if we've reached target
                gain = (current_price - prices[-1]) / prices[-1]
                if not reached_target and gain >= target_gain:
                    days_to_target.append(day)
                    reached_target = True
            
            # Record final gain for this simulation
            final_gain = (current_price - prices[-1]) / prices[-1]
            final_gains.append(final_gain)
        
        # Calculate statistics
        avg_gain = float(np.mean(final_gains)) * 100  # Convert to percentage
        gain_p10 = float(np.percentile(final_gains, 10)) * 100
        gain_p90 = float(np.percentile(final_gains, 90)) * 100
        success_rate = (len(days_to_target) / num_simulations) * 100
        
        # Calculate suggested hold time
        if days_to_target:
            suggested_hold = int(np.mean(days_to_target))
        else:
            # If target not reached, suggest full period
            suggested_hold = days
        
        logger.info(f"🔮 Projection results: avg_gain={avg_gain:.1f}%, hold_days={suggested_hold}, success_rate={success_rate:.1f}% ({len(days_to_target)}/{num_simulations} simulations)")

        return {
            'avg_gain': round(avg_gain, 2),
            'gain_p10': round(gain_p10, 2),
            'gain_p90': round(gain_p90, 2),
            'suggested_hold_days': suggested_hold,
            'success_rate': round(success_rate, 1),
            'error': None
        }
        
    except Exception as e:
        return {
            'avg_gain': 0.0,
            'suggested_hold_days': 0,
            'success_rate': 0.0,
            'error': f"Projection error: {str(e)}"
        }
