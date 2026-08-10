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


def _movement_profile(daily_vol: float, drift_per_day: float, days: int,
                      targets, rng) -> Dict:
    """How often this stock reaches a target, and how soon. Both directions.

    This answers the question a short-term trader actually asks -- "what target
    and what horizon" -- and it answers it from realised volatility, with no
    forecast involved. The old code computed exactly this and threw it away:
    success_rate was calculated on every run and never displayed, so the UI
    showed "hold 6 days" while concealing that only 25% of simulated paths ever
    reached the target.

    The DOWN column is not optional. Volatility is symmetric, so a target
    reached in 69% of paths upward is reached in roughly 69% downward too.
    Publishing the up column alone would be read as a 69% win rate, which is
    the precise species of false confidence this rebuild exists to remove.
    """
    n_sims = 2000
    out = {}
    for tgt in targets:
        up_days, down_days = [], []
        for _ in range(n_sims):
            px, hit_up, hit_dn = 1.0, None, None
            for d in range(1, days + 1):
                px *= (1.0 + rng.normal(drift_per_day, daily_vol))
                chg = px - 1.0
                if hit_up is None and chg >= tgt:
                    hit_up = d
                if hit_dn is None and chg <= -tgt:
                    hit_dn = d
                if hit_up is not None and hit_dn is not None:
                    break
            up_days.append(hit_up)
            down_days.append(hit_dn)
        up = [d for d in up_days if d]
        dn = [d for d in down_days if d]
        out[f"{tgt:.0%}"] = {
            "target": tgt,
            "up_rate": len(up) / n_sims,
            "up_median_day": int(np.median(up)) if up else None,
            "down_rate": len(dn) / n_sims,
            "down_median_day": int(np.median(dn)) if dn else None,
        }
    return out


def simple_projection(
    prices: List[float],
    sentiment_score: float,
    days: int = 30,
    seed: Optional[int] = None,
    quality_ok: bool = True,
    targets=(0.03, 0.05, 0.08),
) -> Dict:
    """A volatility scenario range and a movement profile. NOT a price target.

    WHAT CHANGED AND WHY

    base_return is ZERO. The previous version centred the simulation on the
    recent trend, which is a claim that trends persist. Two reasons not to make
    it: drift cannot be estimated from ~20 observations (the noise dwarfs the
    signal), and at a one-month horizon short-term reversal is at least as well
    documented as continuation. Measured consequence of the old behaviour: TSLA
    had fallen 21.7%, and the projection duly forecast a further ~20% fall.

    The sentiment adjustment is ADDITIVE, not multiplicative. It used to be
    `mean_return * (1 + 0.25 * sentiment)`, which SCALES the existing trend
    rather than tilting it -- so on a falling stock, bullish sentiment made the
    forecast worse. Measured: maximum bullish sentiment moved TSLA's projection
    from -20.6% to -25.4% and cut its chance of reaching +5% from 28% to 16%.
    Additive means bullish evidence lifts the centre regardless of which way the
    stock has been going, which is what the feature was always supposed to do.

    quality_ok gates the tilt. Phase 1 passes an evidence-count proxy; the
    quality module replaces it without changing this signature.

    Returns the legacy keys (avg_gain, gain_p10, gain_p90, suggested_hold_days,
    success_rate) so existing callers keep working, plus the scenario band and
    the movement profile.
    """
    try:
        if len(prices) < 5:
            return {
                'avg_gain': 0.0, 'suggested_hold_days': 0, 'success_rate': 0.0,
                'error': 'Insufficient price data',
            }

        arr = np.asarray(prices, dtype=float)
        # A zero or non-finite close used to raise ZeroDivisionError from the
        # Python loop and land in the handler below. NumPy instead propagates
        # NaN with error=None, so the page rendered "nan% to nan%" under a
        # confident label and verdict_log stored NaN into the projection fields.
        if not np.all(np.isfinite(arr)) or np.any(arr <= 0):
            return {'avg_gain': 0.0, 'suggested_hold_days': 0, 'success_rate': 0.0,
                    'error': 'Invalid price data'}
        if int(days) <= 0:
            return {'avg_gain': 0.0, 'suggested_hold_days': 0, 'success_rate': 0.0,
                    'error': 'Invalid horizon'}
        returns = np.diff(arr) / arr[:-1]

        # Winsorise, then cap. A single gap or bad print would otherwise set the
        # width of the whole band.
        lo, hi = np.percentile(returns, [2, 98])
        returns = np.clip(returns, lo, hi)
        daily_vol = min(float(np.std(returns)), 0.12)
        if not np.isfinite(daily_vol):
            return {'avg_gain': 0.0, 'suggested_hold_days': 0,
                    'success_rate': 0.0, 'error': 'Invalid price data'}

        # Volatility scales with the SQUARE ROOT of time. A stock moving 1% a
        # day does not move 21% over 21 days; it moves about 4.6%.
        band = daily_vol * float(np.sqrt(days))

        s = float(max(-1.0, min(1.0, sentiment_score or 0.0)))
        # Normalised BEFORE seeding: the raw value may be None, and
        # struct.pack('<d', float(None)) raises inside _derive_seed.
        sentiment_score = s
        tilt = (0.25 * s * band) if quality_ok else 0.0
        centre = 0.0 + tilt

        logger.info(
            "🔮 band=%.1f%% centre=%+.1f%% (tilt %+.2f%%, sentiment %+.3f, quality_ok=%s)",
            band * 100, centre * 100, tilt * 100, s, quality_ok,
        )

        rng = np.random.default_rng(
            seed if seed is not None else _derive_seed(prices, sentiment_score, days)
        )
        profile = _movement_profile(daily_vol, centre / days, days, targets, rng)

        # Degrading to 0.0 here would print "+5% reached in 0% of paths"
        # as a confident statement rather than an absent measurement.
        five = profile.get("5%") or (next(iter(profile.values()), {}))
        return {
            # Scenario range. Provisional, and explicitly not a price target.
            'band': round(band * 100, 2),
            'scenario_bear': round((centre - band) * 100, 2),
            'scenario_base': round(centre * 100, 2),
            'scenario_bull': round((centre + band) * 100, 2),
            'sentiment_tilt': round(tilt * 100, 2),
            'quality_gated': not quality_ok,

            'movement_profile': profile,

            # Legacy keys, now honest.
            'avg_gain': round(centre * 100, 2),
            # The SAME +/-1 sigma band the interface renders. These were
            # +/-1.2816 sigma, so verdict_log stored -- and any future scoring of
            # verdicts would be measured against -- a range wider than the one
            # the user was ever shown.
            'gain_p10': round((centre - band) * 100, 2),
            'gain_p90': round((centre + band) * 100, 2),
            'success_rate': round(five.get('up_rate', 0.0) * 100, 1),
            'suggested_hold_days': five.get('up_median_day') or days,
            'review_window_days': (14, 28),
            'error': None,
        }

    except Exception as e:
        return {
            'avg_gain': 0.0, 'suggested_hold_days': 0, 'success_rate': 0.0,
            'error': f"Projection error: {str(e)}",
        }
