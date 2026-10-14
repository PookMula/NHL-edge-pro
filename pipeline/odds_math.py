"""Odds conversion, no-vig, expected value, Kelly. Pure functions, fully unit-tested.

Conventions
-----------
* ``p_win``/``p_push``/``p_loss`` always sum to 1.
* Expected ROI is profit per 1 unit staked: p_win*(decimal-1) - p_loss (a push returns the stake).
* Probability edge is in percentage POINTS (model prob - break-even prob); ROI is in PERCENT of stake.
"""
import math


def american_to_decimal(american):
    a = float(american)
    if -100 < a < 100:
        raise ValueError(f"invalid American odds: {american}")
    return 1.0 + a / 100.0 if a > 0 else 1.0 + 100.0 / abs(a)


def decimal_to_american(decimal):
    d = float(decimal)
    if d <= 1.0:
        raise ValueError(f"invalid decimal odds: {decimal}")
    return (d - 1.0) * 100.0 if d >= 2.0 else -100.0 / (d - 1.0)


def implied_probability(american):
    """Raw implied probability (includes the bookmaker margin)."""
    return 1.0 / american_to_decimal(american)


def prob_to_decimal(p):
    if not 0.0 < p < 1.0:
        raise ValueError(f"probability must be in (0,1): {p}")
    return 1.0 / p


def prob_to_american(p):
    return decimal_to_american(prob_to_decimal(p))


def fair_american_with_push(p_win, p_push=0.0):
    """Fair price for a market that can push: the price where EV = 0."""
    p_loss = 1.0 - p_win - p_push
    if p_win <= 0 or p_loss <= 0:
        raise ValueError("fair odds undefined for degenerate probabilities")
    return decimal_to_american(1.0 + p_loss / p_win)


def format_american(a):
    a = round(a)
    return f"+{a}" if a > 0 else str(a)


def overround(american_prices):
    return sum(implied_probability(a) for a in american_prices) - 1.0


def no_vig_multiplicative(american_prices):
    """Proportional (multiplicative) margin removal over the COMPLETE market."""
    raw = [implied_probability(a) for a in american_prices]
    s = sum(raw)
    return [r / s for r in raw]


def no_vig_power(american_prices, tol=1e-12):
    """Power method: find k with sum(p_i^k) = 1. Handles favourite/longshot bias better."""
    raw = [implied_probability(a) for a in american_prices]
    if abs(sum(raw) - 1.0) < tol:
        return raw
    lo, hi = 0.5, 3.0
    for _ in range(200):
        k = 0.5 * (lo + hi)
        s = sum(r ** k for r in raw)
        if s > 1.0:
            lo = k
        else:
            hi = k
        if hi - lo < tol:
            break
    k = 0.5 * (lo + hi)
    out = [r ** k for r in raw]
    s = sum(out)
    return [o / s for o in out]


def expected_roi(p_win, decimal, p_push=0.0):
    p_loss = 1.0 - p_win - p_push
    if p_loss < -1e-12:
        raise ValueError("p_win + p_push > 1")
    return p_win * (decimal - 1.0) - p_loss


def break_even_probability(decimal, p_push=0.0):
    """Win probability needed for EV = 0, given the push probability."""
    return (1.0 - p_push) / decimal


def kelly_fraction(p_win, decimal, p_push=0.0):
    """Full-Kelly stake fraction (log-utility optimum), push-aware. Never negative."""
    b = decimal - 1.0
    p_loss = 1.0 - p_win - p_push
    if b <= 0 or (p_win + p_loss) <= 0:
        return 0.0
    f = (b * p_win - p_loss) / (b * (p_win + p_loss))
    return max(0.0, f)


def parlay_decimal(decimals):
    out = 1.0
    for d in decimals:
        out *= d
    return out


def log_loss_binary(p, y, eps=1e-12):
    p = min(max(p, eps), 1 - eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))
