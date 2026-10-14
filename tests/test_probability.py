import numpy as np
import pytest

from pipeline.probability import DEFAULT_AUX, distributions, score_matrices
from pipeline.simulate import simulate_game, summarize

AUX = dict(DEFAULT_AUX, phi=0.04, theta=0.15, p_so=0.4, q_so=0.48, ot_base=0.53, kappa=0.8)


def test_score_matrix_sums_to_one_and_poisson_means():
    M = score_matrices([3.1], [2.7], 0.0)[0]
    assert M.sum() == pytest.approx(1.0, abs=1e-9)
    i = np.arange(M.shape[0])
    assert (M.sum(1) * i).sum() == pytest.approx(3.1, abs=1e-3)
    assert (M.sum(0) * i).sum() == pytest.approx(2.7, abs=1e-3)


def test_gamma_mixture_preserves_mean_and_adds_correlation():
    M = score_matrices([3.0], [3.0], 0.08)[0]
    i = np.arange(M.shape[0])
    eh, ea = (M.sum(1) * i).sum(), (M.sum(0) * i).sum()
    assert eh == pytest.approx(3.0, abs=2e-3)
    cov = (M * np.outer(i, i)).sum() - eh * ea
    assert cov > 0.3  # shared pace -> positive correlation (cov = lam^2 * phi = 0.72)


def test_distribution_consistency():
    d = distributions([3.2], [2.6], AUX)[0]
    assert d.margin.sum() == pytest.approx(1.0, abs=1e-9)
    assert d.total.sum() == pytest.approx(1.0, abs=1e-9)
    assert d.margin[d.moff] == pytest.approx(0.0, abs=1e-12), "a final can never be tied"
    assert d.p_ml_home + d.p_ml_away == pytest.approx(1.0)
    assert d.p_reg_home + d.p_tie + d.p_reg_away == pytest.approx(1.0)
    assert d.p_ot + d.p_so == pytest.approx(d.p_tie)
    # moneyline = regulation win + share of ties
    assert d.p_ml_home == pytest.approx(d.p_reg_home + d.p_tie * d.q_tie)
    # puck lines are complements
    hm, _ = d.spread("home", -1.5)
    ap, _ = d.spread("away", 1.5)
    assert hm + ap == pytest.approx(1.0)
    # OT/SO wins are by exactly one goal, so P(win by 2+) < P(regulation win)
    assert hm < d.p_reg_home


def test_whole_number_total_push():
    d = distributions([3.0], [3.0], AUX)[0]
    o, push = d.total_market("over", 6.0)
    u, push2 = d.total_market("under", 6.0)
    assert push == push2 > 0.05
    assert o + u + push == pytest.approx(1.0)
    o55, p55 = d.total_market("over", 5.5)
    assert p55 == 0.0
    assert o55 == pytest.approx(o + push)


def test_playoff_has_no_shootout():
    d = distributions([3.0], [3.0], AUX, playoff=[True])[0]
    assert d.p_so == 0.0 and d.p_ot == pytest.approx(d.p_tie)


def test_monte_carlo_matches_analytic():
    d = distributions([3.3], [2.8], AUX)[0]
    s = summarize(simulate_game(3.3, 2.8, AUX, n=200_000, seed=1))
    se = lambda p: 4 * np.sqrt(p * (1 - p) / 200_000)
    assert s["p_home_ml"] == pytest.approx(d.p_ml_home, abs=se(d.p_ml_home))
    assert s["p_reg_tie"] == pytest.approx(d.p_tie, abs=se(d.p_tie))
    assert s["p_home_m15"] == pytest.approx(d.spread("home", -1.5)[0], abs=se(0.3))
    assert s["over"]["5.5"] == pytest.approx(d.total_market("over", 5.5)[0], abs=se(0.5))
    assert s["exp_total"] == pytest.approx(d.expected_total(), abs=0.02)


def test_simulation_reproducible():
    a = simulate_game(3.0, 3.0, AUX, n=5000, seed=99)
    b = simulate_game(3.0, 3.0, AUX, n=5000, seed=99)
    c = simulate_game(3.0, 3.0, AUX, n=5000, seed=100)
    assert np.array_equal(a["home"], b["home"]) and not np.array_equal(a["home"], c["home"])
