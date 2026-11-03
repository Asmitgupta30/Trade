"""Unit tests for the 3-Year Historical Valuation Comparison Engine (advisor/valuation.py)."""

import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock

from advisor.valuation import (
    ValuationMetric,
    calculate_valuation_zscore,
    calculate_percentile_rank,
    calculate_graham_iv,
    evaluate_quality_safety,
    compute_3y_distribution,
    screen_valuation_universe,
)


def test_pe_zscore_calculation():
    # Mean = 25.0, Std = 5.0, Current = 15.0 -> Z = (15 - 25) / 5 = -2.0
    z = calculate_valuation_zscore(current_pe=15.0, mean_pe=25.0, std_pe=5.0)
    assert pytest.approx(z, 0.01) == -2.0

    # Zero standard deviation guard
    z_zero_std = calculate_valuation_zscore(current_pe=15.0, mean_pe=15.0, std_pe=0.0)
    assert z_zero_std == 0.0


def test_percentile_rank_calculation():
    series = np.array([10.0, 15.0, 20.0, 25.0, 30.0])
    # 15.0 is >= 2 out of 5 elements -> 40th percentile
    pct = calculate_percentile_rank(current_val=15.0, distribution=series)
    assert pytest.approx(pct, 0.1) == 40.0

    # Value lower than all historical values -> 0.0
    assert calculate_percentile_rank(current_val=5.0, distribution=series) == 0.0


def test_graham_intrinsic_value():
    # Graham IV = sqrt(22.5 * EPS * BVPS)
    # EPS = 20.0, BVPS = 50.0 -> sqrt(22.5 * 1000) = sqrt(22500) = 150.0
    iv = calculate_graham_iv(eps=20.0, bvps=50.0)
    assert pytest.approx(iv, 0.01) == 150.0

    # Negative EPS or BVPS should return None (not complex numbers)
    assert calculate_graham_iv(eps=-5.0, bvps=50.0) is None
    assert calculate_graham_iv(eps=10.0, bvps=-10.0) is None


def test_quality_filter_banking_vs_non_banking():
    # Non-banking stock: requires ROE > 12% AND Debt/Equity < 1.5
    # Tech stock: ROE=18%, D/E=0.2 -> PASS
    assert evaluate_quality_safety(sector="Information Technology", roe=18.0, debt_to_equity=0.2, pb=3.5) is True
    # Capital-heavy overleveraged industrial: ROE=14%, D/E=2.5 -> FAIL
    assert evaluate_quality_safety(sector="Automobile", roe=14.0, debt_to_equity=2.5, pb=2.0) is False

    # Banking / NBFC stock: high leverage by definition (D/E > 5x is standard)
    # Bank with ROE=15%, D/E=6.5, PB=1.2 -> PASS (exempt from D/E < 1.5)
    assert evaluate_quality_safety(sector="Financial Services", roe=15.0, debt_to_equity=6.5, pb=1.2) is True
    # Deteriorated bank: ROE=5.0%, PB=0.3 -> FAIL
    assert evaluate_quality_safety(sector="Banking", roe=5.0, debt_to_equity=7.0, pb=0.3) is False


def test_negative_earnings_handling():
    # Negative earnings (loss making company)
    # Cannot calculate P/E Z-score meaningfully
    z = calculate_valuation_zscore(current_pe=None, mean_pe=20.0, std_pe=4.0)
    assert z is None


def test_compute_3y_distribution():
    # Generate 750 daily close prices around mean 100 with eps 5.0 -> PE mean 20.0
    np.random.seed(42)
    prices = pd.Series(np.random.normal(loc=100.0, scale=10.0, size=750))
    dist = compute_3y_distribution(daily_closes=prices, trailing_eps=5.0, book_value_per_share=25.0)
    
    assert dist["pe_mean"] > 18.0 and dist["pe_mean"] < 22.0
    assert dist["pe_std"] > 0
    assert dist["pe_median"] > 0
    assert dist["pb_mean"] > 0
