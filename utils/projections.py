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


# The window over which the social evidence is claimed to say anything. The
# 30-day band is a volatility range and stands on its own, but chatter and news
# effects are documented to decay within one to two trading days, so a
# DIRECTIONAL reading must not be published over a horizon the evidence cannot
# reach. Reported alongside the full-horizon numbers, never instead of them.
DECISION_HORIZON_DAYS = 10

_N_SIMS = 2000


def _movement_profile(daily_vol: float, drift_per_day: float, days: int,
                      targets, rng, decision_horizon: int = DECISION_HORIZON_DAYS) -> Dict:
    """How often this stock reaches a target, how soon, and what it costs to wait.

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

    ONE PATH SET FOR EVERY TARGET. The previous loop drew fresh paths per
    target, so +3%, +5% and +8% were measured in three unrelated universes and
    nothing forced P(+8%) <= P(+5%) <= P(+3%). Sampling noise could and did
    order them wrongly, which reads as a arithmetic error to anyone who checks.
    Drawing once and thresholding the same paths makes the ordering structural.

    THREE FIGURES THE OLD PROFILE COULD NOT PRODUCE:

    up_first_rate -- P(+t arrives BEFORE -t | one of them arrived). Unlike
        up_rate this is order-dependent and therefore genuinely directional. At
        zero drift it is 50%, and printing that 50% is the honest statement that
        the system claims no edge.

        CONDITIONAL, and it must be. The unconditional share of ALL paths that
        reach +t first has a complement of "went down first OR went nowhere",
        so on a calm large cap at 1% daily vol it reads 23% -- a number that
        looks like a strong bearish edge sitting directly beneath a sentence
        denying any edge. Only paths that touched something can speak to which
        came first.

    mae_p75 -- of the paths that DID reach +t, the drawdown 3 in 4 of them
        stayed within on the way. This is the number a stop is placed from, and
        nothing in the product produced it.

        NOT THE MEDIAN. Conditioning on winners selects for paths that hit the
        target immediately, and a day-one hit has zero observed drawdown by
        construction. Past roughly 8% daily volatility more than half the
        winners have never dipped, so the median pins to exactly 0.0 and the
        product prints "-0.0%" as the risk of its most volatile tickers. The
        median is anti-correlated with volatility; p75 is monotonic in it.

    up_rate_by_decision -- the same touch rate restricted to the decision
        horizon, so the directional claim can be read over the window the
        evidence actually covers.
    """
    n = _N_SIMS
    days = int(days)
    # LOG returns, exponentiated. The obvious formulation -- cumprod(1 + N(mu,
    # sigma)) -- silently injects a median drift of -sigma^2*T/2: at 5% daily
    # volatility the median path lands at -3.4% while `centre` claims 0.0%, and
    # down_rate then exceeds up_rate at every target for every ticker. That is
    # precisely the symmetry this function's own docstring asserts, and the old
    # scalar loop violated it too. Here `drift_per_day` is a LOG drift, so the
    # median path lands exactly on the centre the caller asked for.
    steps = rng.normal(drift_per_day, daily_vol, size=(n, days))
    paths = np.exp(np.cumsum(steps, axis=1)) - 1.0
    # Worst level seen up to and including each day, for the drawdown figure.
    running_min = np.minimum.accumulate(paths, axis=1)

    horizon = max(1, min(int(decision_horizon), days))
    out = {}
    for tgt in targets:
        up_mask = paths >= tgt
        dn_mask = paths <= -tgt
        up_any = up_mask.any(axis=1)
        dn_any = dn_mask.any(axis=1)
        # argmax on a boolean row gives the first True; meaningless where the
        # row is all False, which is why every use is masked by *_any.
        up_day = up_mask.argmax(axis=1) + 1
        dn_day = dn_mask.argmax(axis=1) + 1

        # Reached the upside FIRST: either the downside never came, or it came
        # later. One close cannot be both >= t and <= -t for t > 0, so these two
        # partition the paths that touched anything.
        up_first = up_any & (~dn_any | (up_day < dn_day))
        dn_first = dn_any & (~up_any | (dn_day < up_day))
        touched = int(up_first.sum() + dn_first.sum())

        rows = np.flatnonzero(up_any)
        if rows.size:
            # The drawdown suffered before the target was reached. Positive
            # number = how far against you it went first; a path that never
            # dipped contributes 0, not a negative "gain".
            depth = np.maximum(-running_min[rows, up_day[rows] - 1], 0.0)
            mae_p50 = float(np.percentile(depth, 50))
            mae_p75 = float(np.percentile(depth, 75))
            mae_p90 = float(np.percentile(depth, 90))
            # How much of the winning set never dipped at all. When this is
            # large the p50 above is 0 for a real reason, and saying so is
            # better than publishing the 0.
            straight_up = float((depth <= 0.0).mean())
        else:
            mae_p50 = mae_p75 = mae_p90 = straight_up = None

        out[f"{tgt:.0%}"] = {
            "target": tgt,
            "up_rate": float(up_any.mean()),
            "up_median_day": int(np.median(up_day[up_any])) if up_any.any() else None,
            "down_rate": float(dn_any.mean()),
            "down_median_day": int(np.median(dn_day[dn_any])) if dn_any.any() else None,
            # Directional. None rather than 0.5 when nothing touched either
            # side, because "no path moved 8% in a month" is an absent
            # measurement, not a balanced one.
            "up_first_rate": (float(up_first.sum()) / touched) if touched else None,
            "touched_rate": touched / n,
            # What reaching the target costs on the way.
            "mae_p50": mae_p50,
            "mae_p75": mae_p75,
            "mae_p90": mae_p90,
            "straight_up_rate": straight_up,
            # The same question asked over the window the evidence covers.
            "up_rate_by_decision": float((up_any & (up_day <= horizon)).mean()),
            "down_rate_by_decision": float((dn_any & (dn_day <= horizon)).mean()),
        }
    # NOT stored in `out`. Callers iterate profile.items() and call .get() on
    # every value; a scalar smuggled in beside the per-target dicts crashes the
    # renderer. The horizon travels on the projection instead.
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
        # log1p, not centre/days. _movement_profile exponentiates its steps, so
        # the drift it wants is a LOG drift; handing it the arithmetic one puts
        # the median path off the centre by a compounding error.
        profile = _movement_profile(daily_vol, float(np.log1p(centre)) / days,
                                    days, targets, rng)
        # The horizon actually used, after clamping to the simulated span. The
        # constant would mislabel the column "within 10d" on a 5-day run.
        effective_horizon = max(1, min(DECISION_HORIZON_DAYS, int(days)))

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
            # The window the DIRECTIONAL reading is claimed over. The band above
            # is a 30-day volatility range and is not affected by this.
            'decision_horizon_days': effective_horizon,

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
            # review_window_days is GONE. It was the constant (14, 28) rendered
            # as "2-4 weeks" under a "Review window" heading on every analysis of
            # every ticker -- a number derived from nothing, carrying no measured
            # signal, and lending the output a precision it does not have.
            'error': None,
        }

    except Exception as e:
        return {
            'avg_gain': 0.0, 'suggested_hold_days': 0, 'success_rate': 0.0,
            'error': f"Projection error: {str(e)}",
        }
