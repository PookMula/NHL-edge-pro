import numpy as np
import pytest

from pipeline.edges import tier_ok
from pipeline.model import Design, GameModel
from pipeline.parlays import _metrics, bucket


def test_training_weights_exclude_future_and_same_day():
    m = GameModel(Design(["A", "B"]), {"half_life_days": 100, "ridge": 10, "max_history_days": 500})
    asof = 1000
    d = np.array([400, 600, 900, 999, 1000, 1001])
    w = m.weights(d, asof)
    assert w[0] == 0 and w[4] == 0 and w[5] == 0, "games on/after the as-of date or too old must have zero weight"
    assert w[3] > w[2] > w[1] > 0
    assert w[2] == pytest.approx(0.5)


def test_tier_gate_ordering():
    assert tier_ok("EDGE_SUPPORTED", "EDGE_SUPPORTED")
    assert not tier_ok("BEATS_BASELINE", "EDGE_SUPPORTED")
    assert tier_ok("MARKET_COMPETITIVE", "BEATS_BASELINE")
    assert not tier_ok(None, "BEATS_BASELINE")


def test_parlay_metrics_independent_legs_and_push():
    S = 1000
    legs = [{"pw": np.full(S, 0.5), "pp": np.zeros(S), "decimal": 2.0},
            {"pw": np.full(S, 0.4), "pp": np.zeros(S), "decimal": 3.0},
            {"pw": np.full(S, 0.45), "pp": np.full(S, 0.1), "decimal": 2.0}]
    m = _metrics(legs)
    assert m["decimal"] == pytest.approx(12.0)
    assert m["american"] == pytest.approx(1100)
    assert m["p_joint"] == pytest.approx(0.5 * 0.4 * 0.45)
    # EV with push: E[payout] = prod(p*d + push) - 1
    assert m["ev"] == pytest.approx((0.5 * 2) * (0.4 * 3) * (0.45 * 2 + 0.1) - 1)


def test_parlay_parameter_uncertainty_correlates_legs():
    rng = np.random.default_rng(0)
    shared = rng.normal(0, 0.05, 5000)
    legs = [{"pw": 0.5 + shared, "pp": np.zeros(5000), "decimal": 2.0} for _ in range(3)]
    m = _metrics(legs)
    assert m["p_joint"] > m["p_joint_naive"]  # positively correlated legs hit together more often


def test_buckets():
    assert bucket(650) == "600-999" and bucket(1000) == "1000-1499" and bucket(3000) == "2500+" and bucket(500) is None
