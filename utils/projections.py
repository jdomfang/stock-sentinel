"""
Stock projections module.
"""

import numpy as np
from typing import Dict, List
import logging

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def simple_projection(prices: List[float], sentiment_score: float, days: int = 30) -> Dict:
    """
    Run a simple Monte Carlo simulation to project stock performance.
    
    Args:
        prices: List of historical closing prices
        sentiment_score: Sentiment score from 0-1 (used to adjust expected returns)
        days: Number of days to project forward (default 30)
        
    Returns:
        Dictionary with keys:
        - avg_gain: Average projected gain as percentage
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
        
        # Calculate historical statistics
        mean_return = np.mean(returns)
        std_return = np.std(returns)

        logger.debug(f"📊 Historical stats: {len(prices)} prices, mean_return={mean_return:.6f}, std_return={std_return:.6f}")

        # Adjust mean return based on sentiment
        # sentiment_score ranges from 0-1, we map to multiplier 0.5-1.5
        sentiment_multiplier = 0.5 + sentiment_score
        adjusted_mean = mean_return * sentiment_multiplier

        logger.debug(f"🎭 Sentiment adjustment: score={sentiment_score:.3f}, multiplier={sentiment_multiplier:.3f}, adjusted_mean={adjusted_mean:.6f}")
        
        # Run Monte Carlo simulations
        num_simulations = 100
        target_gain = 0.05  # 5% target
        
        final_gains = []
        days_to_target = []
        
        for _ in range(num_simulations):
            # Start at last known price
            current_price = prices[-1]
            
            # Simulate daily price movements
            reached_target = False
            for day in range(1, days + 1):
                # Generate random return from normal distribution
                daily_return = np.random.normal(adjusted_mean, std_return)
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
        avg_gain = np.mean(final_gains) * 100  # Convert to percentage
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
