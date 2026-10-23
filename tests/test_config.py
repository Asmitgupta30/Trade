import pytest

from common.config import RiskConfigError, load_risk_config


@pytest.fixture()
def cfg():
    load_risk_config.cache_clear()
    return load_risk_config()


def test_config_loads_and_locks(cfg):
    assert cfg.autonomous_execution_must_be_false is True
    assert cfg.raw["system_mode"]["autonomous_execution"] is False
    assert cfg.raw["system_mode"]["requires_human_approval"] is True


def test_paper_gate_values(cfg):
    g = cfg.paper_gate
    assert g.min_days == 28
    assert g.min_suggestions == 40
    assert g.hit_rate_min == 0.50
    assert g.sharpe_min == 1.0
    assert g.max_drawdown_max_pct == 8


def test_intraday_rules_bound(cfg):
    r = cfg.intraday
    assert r.max_positions_per_day == 3
    assert r.max_allocation_per_stock_pct == 60
    assert r.max_allocation_per_sector_pct == 70
    assert r.min_trade_size_inr == 300
    assert r.round_trip_cost_pct == 0.35
    assert r.min_confidence_buy == 0.55


def test_schedule_complete(cfg):
    assert set(cfg.schedule) >= {"pre_market", "midday_check", "pre_close", "end_of_day_summary"}


def test_mf_bands_sum_sane(cfg):
    mf = cfg.mf
    # lower bounds must be achievable simultaneously: 30+15+15+0 = 60 <= 100
    assert sum(b[0] for b in (mf.large_cap_band, mf.mid_cap_band, mf.debt_band, mf.gold_band)) <= 100
    # upper bounds must reach 100: 50+30+30+10 = 120 >= 100
    assert sum(b[1] for b in (mf.large_cap_band, mf.mid_cap_band, mf.debt_band, mf.gold_band)) >= 100


def test_hard_lock_refuses_execution(tmp_path):
    bad = tmp_path / "risk_config.yaml"
    src = open("configs/risk_config.yaml", encoding="utf-8").read()
    bad.write_text(src.replace("autonomous_execution: false", "autonomous_execution: true"), encoding="utf-8")
    load_risk_config.cache_clear()
    with pytest.raises(RiskConfigError, match="HARD LOCK"):
        load_risk_config(str(bad))
    load_risk_config.cache_clear()


def test_missing_file_raises():
    load_risk_config.cache_clear()
    with pytest.raises(RiskConfigError):
        load_risk_config("definitely/not/here.yaml")
    load_risk_config.cache_clear()
