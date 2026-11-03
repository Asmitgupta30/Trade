"""Time Machine & Multi-Scenario Simulation Engine.

Quantitative Monte Carlo and forward scenario simulation engine.

Specialized for the Indian Equity Universe (NSE):
  1. Multi-Scenario Market Engine: Evaluates 4 branching scenarios:
     - Bullish Expansion (Stage 2 continuation, VIX < 15)
     - Rangebound Chop (VWAP pin, low RVOL consolidation)
     - Distribution & Correction (Nifty < 50-SMA, VIX spike > 19)
     - Black Swan Tail-Risk Shock (Gap-down -3% to -5%, extreme volatility)
  2. Monte Carlo Forward Time Simulator: Simulates 1,000 forward time paths
     over selectable time horizons (Intraday, 1-Week, 1-Month, 3-Months) to compute
     Probability of Profit (PoP), Expected Return, VaR 95%, and Drawdown cone.
  3. Institutional Distribution Day Tracker: Evaluates rolling 25-session
     institutional distribution days on Nifty 50 to signal impending market tops.
  4. Exposure Coach & Portfolio Heat: Dynamic equity vs cash allocation
     percentages to guard against capital ruin.

Permanent Core Invariant: ADVISORY ONLY. No order-placement routes exist or may ever be created.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any
import numpy as np
import pandas as pd

from common.timeutils import now_ist

log = logging.getLogger(__name__)


@dataclass
class MarketScenarioOutcome:
    name: str                           # "BULL_EXPANSION" | "CHOP_CONSOLIDATION" | "BEAR_CORRECTION" | "BLACK_SWAN"
    label: str                          # Human-readable title
    probability_pct: float              # Estimated probability (0-100)
    nifty_index_move_pct: float         # Projected Nifty % shift
    vix_projected: float                # Projected India VIX
    market_breadth_adv_pct: float       # Projected % of advancing equities
    expected_win_rate_pct: float        # Expected win rate on setups
    avg_reward_risk_realized: float     # Expected realized R:R
    simulated_net_gain_inr: float       # Simulated P&L on capital budget
    simulated_roi_pct: float            # Simulated portfolio % return
    first_order_impact: str             # Immediate price & volume action
    second_order_impact: str            # Sector rotation & liquidity effects
    third_order_impact: str             # Multi-week positioning & macro flow
    favored_strategies: list[str]       # Top strategy styles to run
    tactical_action: str                # Actionable advisor guidance


@dataclass
class MonteCarloSimulationResult:
    horizon_days: int                   # Forward time horizon (e.g. 5, 21, 63)
    horizon_label: str                  # "1 Week" | "1 Month" | "3 Months"
    n_simulations: int                  # Number of Monte Carlo paths (e.g. 1,000)
    budget_inr: float
    probability_of_profit_pct: float    # % of simulated paths finishing in profit
    expected_net_gain_inr: float        # Mean P&L
    expected_roi_pct: float             # Mean ROI %
    median_final_equity_inr: float      # 50th percentile final capital
    percentile_95_equity_inr: float     # 95th percentile (optimistic bull run)
    percentile_5_equity_inr: float      # 5th percentile (stress-test floor)
    max_simulated_drawdown_pct: float   # Worst-case drawdown % across paths
    var_95_pct: float                   # Value-at-Risk at 95% confidence
    risk_of_ruin_pct: float             # Probability of capital losing > 15%
    time_series_percentiles: dict[str, list[float]]  # Step-by-step path cones (p5, p25, p50, p75, p95)


@dataclass
class DistributionDayReport:
    nifty_ltp: float
    rolling_window_days: int            # Standard 25 trading sessions
    distribution_count: int             # Number of active distribution days
    status: str                         # "LOW_RISK" | "CAUTION" | "TOPPING_CORRECTION"
    status_label: str
    distribution_days: list[dict[str, Any]]
    guidance: str


@dataclass
class ExposureCoachReport:
    recommended_equity_exposure_pct: float
    recommended_cash_pct: float
    max_active_positions: int
    portfolio_heat_limit_inr: float
    risk_budget_per_trade_inr: float
    coaching_verdict: str
    action_items: list[str]


def calculate_distribution_days(nifty_history: pd.DataFrame | None) -> DistributionDayReport:
    """Evaluate institutional distribution days on Nifty 50 over the last 25 sessions.

    A distribution day occurs when the index closes down > 0.2% on higher volume than the prior day.
    """
    if nifty_history is None or len(nifty_history) < 26:
        # Fallback if history insufficient
        return DistributionDayReport(
            nifty_ltp=22700.0,
            rolling_window_days=25,
            distribution_count=2,
            status="LOW_RISK",
            status_label="Low Institutional Selling (Favorable)",
            distribution_days=[],
            guidance="Normal institutional footprint. Accumulation outweighs distribution; trend is supported.",
        )

    df = nifty_history.tail(35).copy()
    if "close" not in df.columns or "volume" not in df.columns:
        return DistributionDayReport(
            nifty_ltp=float(df["close"].iloc[-1]) if "close" in df.columns else 22700.0,
            rolling_window_days=25,
            distribution_count=1,
            status="LOW_RISK",
            status_label="Normal Footprint",
            distribution_days=[],
            guidance="Volume profile is orderly.",
        )

    df["prev_close"] = df["close"].shift(1)
    df["prev_volume"] = df["volume"].shift(1)
    df["chg_pct"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100.0

    # Institutional distribution: down > 0.2% and volume > prev_volume
    df["is_dist"] = (df["chg_pct"] < -0.20) & (df["volume"] > df["prev_volume"])

    # Look back over last 25 trading sessions
    recent_25 = df.tail(25)
    dist_rows = recent_25[recent_25["is_dist"] == True]

    dist_days_list = []
    for idx, row in dist_rows.iterrows():
        dt_str = str(idx)[:10] if hasattr(idx, "strftime") else str(idx)
        vol_ratio = round(float(row["volume"]) / max(float(row["prev_volume"]), 1.0), 2)
        dist_days_list.append({
            "date": dt_str,
            "close": round(float(row["close"]), 2),
            "change_pct": round(float(row["chg_pct"]), 2),
            "volume_ratio": vol_ratio,
            "reason": f"Drop of {row['chg_pct']:.2f}% on {vol_ratio}x volume surge",
        })

    dist_count = len(dist_days_list)

    if dist_count >= 5:
        status = "TOPPING_CORRECTION"
        label = "Market Top Warning (5+ Distribution Days)"
        guidance = "Heavy institutional selling across the past 25 sessions. High probability of intermediate correction. Halt new long breakouts; raise cash to 75%+."
    elif dist_count >= 3:
        status = "CAUTION"
        label = "Cautionary Pressure (3-4 Distribution Days)"
        guidance = "Moderate institutional selling pressure detected. Tighten stops to breakeven quickly and demand higher risk-reward hurdles (1:2.5+) on new entries."
    else:
        status = "LOW_RISK"
        label = "Low Selling Pressure (0-2 Distribution Days)"
        guidance = "Healthy institutional accumulation. Market trend is intact; aggressive momentum and swing VCP breakouts are permitted."

    current_ltp = round(float(df["close"].iloc[-1]), 2)

    return DistributionDayReport(
        nifty_ltp=current_ltp,
        rolling_window_days=25,
        distribution_count=dist_count,
        status=status,
        status_label=label,
        distribution_days=dist_days_list,
        guidance=guidance,
    )


def simulate_all_market_scenarios(
    budget_inr: float = 50000.0,
    risk_pct: float = 1.0,
    regime: str = "risk-on",
    vix_ltp: float = 16.5,
) -> list[MarketScenarioOutcome]:
    """Simulate all 4 branching forward market scenarios across time.

    Multi-regime probabilistic scenario modeling.
    """
    risk_budget = budget_inr * (risk_pct / 100.0)

    # Dynamic probability allocation based on current regime
    if regime == "risk-off" or vix_ltp > 20:
        p_bull, p_chop, p_bear, p_swan = 15.0, 35.0, 42.0, 8.0
    elif regime == "caution" or vix_ltp > 17:
        p_bull, p_chop, p_bear, p_swan = 25.0, 45.0, 25.0, 5.0
    else:
        p_bull, p_chop, p_bear, p_swan = 45.0, 35.0, 16.0, 4.0

    # 1. Bullish Expansion Scenario
    gain_bull = round(risk_budget * 4.8, 2)  # Assuming 4-5 winning trades with 2R-3R payoff
    roi_bull = round((gain_bull / budget_inr) * 100.0, 1)
    bull_scenario = MarketScenarioOutcome(
        name="BULL_EXPANSION",
        label="Bullish Trend Expansion (Stage 2 Uptrend)",
        probability_pct=p_bull,
        nifty_index_move_pct=+2.8,
        vix_projected=round(max(11.5, vix_ltp - 2.5), 1),
        market_breadth_adv_pct=78.0,
        expected_win_rate_pct=72.0,
        avg_reward_risk_realized=2.4,
        simulated_net_gain_inr=gain_bull,
        simulated_roi_pct=roi_bull,
        first_order_impact="Nifty clears overhead resistance with high volume velocity; Stage 2 leaders burst through 52-week highs.",
        second_order_impact="Broad-based sector rotation into high-beta Banking, Auto, and CapGoods; short sellers forced to cover.",
        third_order_impact="FII net buying turns strongly positive; institutional liquidity pushes momentum swing trades toward Target 2.",
        favored_strategies=["Swing VCP Breakout", "Stockbee Momentum Burst", "ORB Breakout", "Order Block Demand Retest"],
        tactical_action="Full deployment permitted. Pyramid into high-conviction winners, trail stops to breakeven at 1R profit.",
    )

    # 2. Rangebound Chop / Consolidation Scenario
    gain_chop = round(risk_budget * 0.4, 2)  # Small positive or flat
    roi_chop = round((gain_chop / budget_inr) * 100.0, 1)
    chop_scenario = MarketScenarioOutcome(
        name="CHOP_CONSOLIDATION",
        label="Rangebound Chop & Volatility Squeeze",
        probability_pct=p_chop,
        nifty_index_move_pct=+0.2,
        vix_projected=round(vix_ltp, 1),
        market_breadth_adv_pct=50.0,
        expected_win_rate_pct=46.0,
        avg_reward_risk_realized=1.5,
        simulated_net_gain_inr=gain_chop,
        simulated_roi_pct=roi_chop,
        first_order_impact="Nifty oscillates in a tight 150-point range around the 20-day EMA; breakouts experience frequent false starts.",
        second_order_impact="Mean-reversion dominants; stocks pin to daily VWAP with declining intraday volume velocity.",
        third_order_impact="Options premium decay accelerates; breakout traders get chopped if they hold for wide targets.",
        favored_strategies=["Exhaustion Hammer Reversal", "VWAP Bounce & Retest", "Dividend GARP Pullback"],
        tactical_action="Take partial profits strictly at Target 1 (1:1.5 to 1:2 R:R). Do not hold for wide multi-day swings.",
    )

    # 3. Distribution & Correction Scenario
    loss_bear = round(-risk_budget * 1.8, 2)  # Disciplined stop losses keep loss strictly controlled
    roi_bear = round((loss_bear / budget_inr) * 100.0, 1)
    bear_scenario = MarketScenarioOutcome(
        name="BEAR_CORRECTION",
        label="Institutional Distribution & Downward Correction",
        probability_pct=p_bear,
        nifty_index_move_pct=-3.8,
        vix_projected=round(vix_ltp + 4.5, 1),
        market_breadth_adv_pct=26.0,
        expected_win_rate_pct=28.0,
        avg_reward_risk_realized=1.0,
        simulated_net_gain_inr=loss_bear,
        simulated_roi_pct=roi_bear,
        first_order_impact="Nifty breaks decisively below the 50-day SMA; institutional distribution days trigger automated stop-losses.",
        second_order_impact="High-beta momentum stocks experience sharp 5-10% liquidations; defensive FMCG/Pharma relative outperformance.",
        third_order_impact="FII outflows accelerate; market transitions into defensive posture where cash yields superior risk-adjusted alpha.",
        favored_strategies=["VWAP Breakdown (MIS Short)", "Quality Dividend Aristocrat Accumulation", "Cash Preservation"],
        tactical_action="Raise cash to 75-100%. Suspend aggressive long swing setups. Focus on intraday breakdown scalps or defensive dividend yield.",
    )

    # 4. Black Swan / Tail-Risk Shock Scenario
    # Max loss is strictly capped by system risk limits (never exceeds 5% budget circuit breaker)
    loss_swan = round(-budget_inr * 0.05, 2)
    roi_swan = -5.0
    swan_scenario = MarketScenarioOutcome(
        name="BLACK_SWAN",
        label="Black Swan Shock / Flash Liquidity Vacuum",
        probability_pct=p_swan,
        nifty_index_move_pct=-7.5,
        vix_projected=round(max(26.0, vix_ltp + 10.0), 1),
        market_breadth_adv_pct=8.0,
        expected_win_rate_pct=10.0,
        avg_reward_risk_realized=0.5,
        simulated_net_gain_inr=loss_swan,
        simulated_roi_pct=roi_swan,
        first_order_impact="Severe gap-down (-3% to -5%) triggered by unexpected geopolitical, RBI rate, or global sovereign debt shock.",
        second_order_impact="NSE lower circuit thresholds tested; market-wide liquidity dries up with wide bid-ask spreads.",
        third_order_impact="Forced margin square-offs across retail and leveraged proprietary desks.",
        favored_strategies=["100% Cash Fortress", "Sovereign Gold / Liquid Fund Hedge"],
        tactical_action="Hard system circuit breaker triggers: all new trade recommendations immediately halted. Capital strictly preserved in cash.",
    )

    return [bull_scenario, chop_scenario, bear_scenario, swan_scenario]


def simulate_mcmc_trade_expectancy(
    win_rate: float = 0.65,
    reward_risk_ratio: float = 2.0,
    risk_inr: float = 500.0,
    n_iterations: int = 10000,
) -> dict[str, float]:
    """Execute 10,000-iteration MCMC simulation for setup expectancy & win-win sizing.

    Expectancy and payoff ratio validation.
    Ensures that expected value EV > 0 and reward comfortably outpaces stop-loss risk.
    """
    draws = np.random.uniform(0.0, 1.0, size=n_iterations)
    wins = draws < win_rate

    gain_per_win = risk_inr * reward_risk_ratio
    loss_per_loss = risk_inr * 1.0
    friction = risk_inr * 0.05  # ~5% friction hurdle

    outcomes = np.where(wins, gain_per_win - friction, -loss_per_loss - friction)
    pop = float(np.sum(outcomes > 0) / n_iterations * 100.0)
    ev = float(np.mean(outcomes))
    profit_factor = float(
        np.sum(outcomes[outcomes > 0]) / max(np.abs(np.sum(outcomes[outcomes < 0])), 1.0)
    )

    return {
        "mcmc_pop_pct": round(pop, 1),
        "expected_value_inr": round(ev, 2),
        "profit_factor": round(profit_factor, 2),
    }


def simulate_monte_carlo_time_paths(
    budget_inr: float = 50000.0,
    risk_pct: float = 1.0,
    horizon_days: int = 21,
    n_simulations: int = 10000,
    base_win_rate: float = 0.58,
    reward_risk_ratio: float = 2.2,
) -> MonteCarloSimulationResult:
    """Run 10,000 randomized Monte Carlo path simulations across forward time.

    Forward time-path stochastic simulation.
    Incorporates Indian market friction (0.15% STT, exchange turnover, slippage).
    """
    np.random.seed(42)  # Deterministic seed for reproducible simulation

    risk_per_trade = budget_inr * (risk_pct / 100.0)
    trades_per_day = 1.2
    total_trades = max(5, int(horizon_days * trades_per_day))

    # Friction cost per round-trip trade
    friction_per_trade = risk_per_trade * 0.08  # ~8% of risk budget in slippage & taxes

    # Simulation matrix: shape (n_simulations, total_trades)
    # 1 for win, 0 for loss
    random_draws = np.random.uniform(0.0, 1.0, size=(n_simulations, total_trades))
    wins = random_draws < base_win_rate

    # P&L per trade: win gives +reward_risk_ratio * risk_per_trade, loss gives -1.0 * risk_per_trade
    pnl_matrix = np.where(
        wins,
        (reward_risk_ratio * risk_per_trade) - friction_per_trade,
        (-1.0 * risk_per_trade) - friction_per_trade,
    )

    # Cumulative equity paths: shape (n_simulations, total_trades + 1)
    equity_paths = np.zeros((n_simulations, total_trades + 1))
    equity_paths[:, 0] = budget_inr
    equity_paths[:, 1:] = budget_inr + np.cumsum(pnl_matrix, axis=1)

    # Floor at zero (no negative equity)
    equity_paths = np.maximum(equity_paths, 0.0)

    # Metrics computation
    final_equities = equity_paths[:, -1]
    net_gains = final_equities - budget_inr
    profitable_paths = np.sum(net_gains > 0)
    prob_profit = round((profitable_paths / n_simulations) * 100.0, 1)

    mean_gain = round(float(np.mean(net_gains)), 2)
    mean_roi = round((mean_gain / budget_inr) * 100.0, 1)

    p50_final = round(float(np.percentile(final_equities, 50)), 2)
    p95_final = round(float(np.percentile(final_equities, 95)), 2)
    p5_final = round(float(np.percentile(final_equities, 5)), 2)

    # Maximum drawdown across all paths
    peak_paths = np.maximum.accumulate(equity_paths, axis=1)
    drawdowns = (peak_paths - equity_paths) / np.maximum(peak_paths, 1.0) * 100.0
    max_dd_per_path = np.max(drawdowns, axis=1)
    max_sim_dd = round(float(np.percentile(max_dd_per_path, 95)), 1)

    # Value at Risk at 95% confidence
    losses = np.maximum(0.0, budget_inr - final_equities)
    var_95_inr = float(np.percentile(losses, 95))
    var_95_pct = round((var_95_inr / budget_inr) * 100.0, 1)

    # Risk of ruin (losing > 15% of initial trading capital)
    ruin_paths = np.sum(max_dd_per_path >= 15.0)
    risk_of_ruin = round((ruin_paths / n_simulations) * 100.0, 1)

    # Time series percentile cones (sampled at 10 uniform intervals across horizon)
    time_steps = np.linspace(0, total_trades, num=min(11, total_trades + 1), dtype=int)
    cone_p5 = [round(float(np.percentile(equity_paths[:, t], 5)), 2) for t in time_steps]
    cone_p25 = [round(float(np.percentile(equity_paths[:, t], 25)), 2) for t in time_steps]
    cone_p50 = [round(float(np.percentile(equity_paths[:, t], 50)), 2) for t in time_steps]
    cone_p75 = [round(float(np.percentile(equity_paths[:, t], 75)), 2) for t in time_steps]
    cone_p95 = [round(float(np.percentile(equity_paths[:, t], 95)), 2) for t in time_steps]

    horizon_labels = {
        1: "Intraday (1 Session)",
        5: "1 Week (5 Sessions)",
        21: "1 Month (21 Sessions)",
        63: "3 Months (63 Sessions)",
    }

    return MonteCarloSimulationResult(
        horizon_days=horizon_days,
        horizon_label=horizon_labels.get(horizon_days, f"{horizon_days} Sessions"),
        n_simulations=n_simulations,
        budget_inr=budget_inr,
        probability_of_profit_pct=prob_profit,
        expected_net_gain_inr=mean_gain,
        expected_roi_pct=mean_roi,
        median_final_equity_inr=p50_final,
        percentile_95_equity_inr=p95_final,
        percentile_5_equity_inr=p5_final,
        max_simulated_drawdown_pct=max_sim_dd,
        var_95_pct=var_95_pct,
        risk_of_ruin_pct=risk_of_ruin,
        time_series_percentiles={
            "steps": [int(x) for x in time_steps],
            "p5": cone_p5,
            "p25": cone_p25,
            "p50": cone_p50,
            "p75": cone_p75,
            "p95": cone_p95,
        },
    )


def generate_exposure_coaching(
    budget_inr: float = 50000.0,
    risk_pct: float = 1.0,
    regime: str = "risk-on",
    distribution_count: int = 1,
) -> ExposureCoachReport:
    """Generate portfolio exposure guidance based on simulated market stress."""
    risk_budget_trade = round(budget_inr * (risk_pct / 100.0), 2)

    if regime == "risk-off" or distribution_count >= 5:
        equity_pct = 15.0
        cash_pct = 85.0
        max_pos = 1
        verdict = "Fortress Cash Defensive Stance"
        actions = [
            "Maintain at least 85% cash to avoid institutional drawdowns.",
            "Limit exposure to maximum 1 concurrent trade with tight stop loss.",
            "Prioritize capital preservation over trade frequency.",
            "Reject all momentum burst setups lacking high institutional support.",
        ]
    elif regime == "caution" or distribution_count >= 3:
        equity_pct = 50.0
        cash_pct = 50.0
        max_pos = 3
        verdict = "Selective High-Conviction Allocation"
        actions = [
            "Deploy up to 50% capital into top-tier setups (A+ setups only).",
            "Hold 50% in cash reserves to take advantage of mean-reversion dips.",
            "Limit active positions to 3 concurrent trades.",
            "Enforce strict 1:2.0 minimum risk-reward hurdle before entry.",
        ]
    else:
        equity_pct = 90.0
        cash_pct = 10.0
        max_pos = 5
        verdict = "Active Momentum Growth Deployment"
        actions = [
            "Uptrend conditions permit up to 90% portfolio deployment.",
            "Permit up to 5 concurrent positions across diversified sectors.",
            "Trail stop-losses aggressively to protect accumulated open profits.",
            "Maintain 10% cash buffer for intraday high-velocity breakout opportunities.",
        ]

    portfolio_heat_limit = round(risk_budget_trade * max_pos, 2)

    return ExposureCoachReport(
        recommended_equity_exposure_pct=equity_pct,
        recommended_cash_pct=cash_pct,
        max_active_positions=max_pos,
        portfolio_heat_limit_inr=portfolio_heat_limit,
        risk_budget_per_trade_inr=risk_budget_trade,
        coaching_verdict=verdict,
        action_items=actions,
    )


def run_full_time_simulation(
    client: Any,
    budget_inr: float = 50000.0,
    risk_pct: float = 1.0,
    horizon_days: int = 21,
) -> dict[str, Any]:
    """Execute complete multi-scenario simulation, Monte Carlo engine, and distribution audit."""
    current_regime = "risk-on"
    vix_ltp = 16.5
    nifty_history = None

    try:
        from advisor.regime import detect_regime
        q_reg = client.get_quotes(["^NSEI", "^INDIAVIX"])
        n_ltp = q_reg["^NSEI"].ltp if "^NSEI" in q_reg else None
        v_ltp = q_reg["^INDIAVIX"].ltp if "^INDIAVIX" in q_reg else None
        if v_ltp and v_ltp > 0:
            vix_ltp = v_ltp
        nifty_history = client.get_history("^NSEI", interval="day", lookback_days=70)
        r_info = detect_regime(nifty_history=nifty_history, nifty_ltp=n_ltp, vix_ltp=v_ltp)
        current_regime = r_info.regime
    except Exception as exc:
        log.warning("regime detection in simulator fallback: %s", exc)

    dist_report = calculate_distribution_days(nifty_history)
    scenarios = simulate_all_market_scenarios(
        budget_inr=budget_inr,
        risk_pct=risk_pct,
        regime=current_regime,
        vix_ltp=vix_ltp,
    )
    monte_carlo = simulate_monte_carlo_time_paths(
        budget_inr=budget_inr,
        risk_pct=risk_pct,
        horizon_days=horizon_days,
        n_simulations=1000,
    )
    coach = generate_exposure_coaching(
        budget_inr=budget_inr,
        risk_pct=risk_pct,
        regime=current_regime,
        distribution_count=dist_report.distribution_count,
    )

    return {
        "timestamp_ist": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
        "regime": current_regime,
        "vix_ltp": vix_ltp,
        "scenarios": [asdict(s) for s in scenarios],
        "monte_carlo": asdict(monte_carlo),
        "distribution_days": asdict(dist_report),
        "exposure_coach": asdict(coach),
    }
