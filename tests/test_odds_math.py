import pytest

from pipeline import odds_math as om


def test_american_decimal_roundtrip():
    assert om.american_to_decimal(120) == pytest.approx(2.20)
    assert om.american_to_decimal(-110) == pytest.approx(1.909090909)
    assert om.american_to_decimal(100) == pytest.approx(2.0)
    for a in (-500, -150, -101, 100, 101, 150, 650, 2500):
        assert om.decimal_to_american(om.american_to_decimal(a)) == pytest.approx(a)


def test_invalid_odds_rejected():
    for a in (0, 50, -99):
        with pytest.raises(ValueError):
            om.american_to_decimal(a)
    with pytest.raises(ValueError):
        om.decimal_to_american(1.0)


def test_spec_example_ev():
    # +120 at 50% -> +10% expected ROI (spec section 10)
    assert om.expected_roi(0.50, om.american_to_decimal(120)) == pytest.approx(0.10)


def test_spec_parlay_example():
    # +650 parlay at 16% -> +20% (spec 22.4); break-even 13.33%
    d = om.american_to_decimal(650)
    assert om.expected_roi(0.16, d) == pytest.approx(0.20)
    assert om.break_even_probability(d) == pytest.approx(0.13333, abs=1e-4)
    d = om.american_to_decimal(750)
    assert om.expected_roi(0.145, d) == pytest.approx(0.2325)
    assert om.break_even_probability(d) == pytest.approx(0.11765, abs=1e-4)


def test_fair_odds_example():
    assert om.prob_to_american(0.582) == pytest.approx(-139.2, abs=0.1)
    assert om.prob_to_american(0.5) == pytest.approx(100)


def test_no_vig_methods_sum_to_one_and_symmetric():
    p = om.no_vig_multiplicative([-110, -110])
    assert p == pytest.approx([0.5, 0.5])
    q = om.no_vig_power([-200, 170])
    assert sum(q) == pytest.approx(1.0)
    assert q[0] > 0.5
    # power method shifts more margin onto the longshot than multiplicative
    m = om.no_vig_multiplicative([-200, 170])
    assert q[1] < m[1]


def test_push_aware_ev_and_kelly():
    d = om.american_to_decimal(-110)
    # whole-number total: 45% win, 10% push, 45% loss
    ev = om.expected_roi(0.45, d, p_push=0.10)
    assert ev == pytest.approx(0.45 * (d - 1) - 0.45)
    assert om.break_even_probability(d, 0.10) == pytest.approx(0.9 / d)
    # EV exactly zero at break-even
    be = om.break_even_probability(d, 0.10)
    assert om.expected_roi(be, d, 0.10) == pytest.approx(0.0, abs=1e-12)
    assert om.kelly_fraction(0.40, d) == 0.0
    f = om.kelly_fraction(0.55, 2.0)
    assert f == pytest.approx(0.10)


def test_fair_american_with_push():
    a = om.fair_american_with_push(0.45, 0.10)
    d = om.american_to_decimal(a)
    assert om.expected_roi(0.45, d, 0.10) == pytest.approx(0.0, abs=1e-9)
