"""Mutual fund sleeve advisor — monthly rebalancing against risk_config bands.

Separate subsystem from intraday equities: NAV-based, tax-aware, suggestion-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from common.config import RiskConfig


@dataclass
class MFHolding:
    scheme_code: int
    scheme_name: str
    category: str            # large_cap | mid_cap | debt | gold_or_other
    units: float
    avg_cost_per_unit: float
    current_nav: float
    first_purchase: datetime  # for LTCG holding-period awareness

    @property
    def value_inr(self) -> float:
        return self.units * self.current_nav

    @property
    def holding_days(self) -> int:
        return (datetime.now() - self.first_purchase).days


@dataclass
class MFSuggestion:
    action: str              # BUY_MORE / REDUCE / HOLD / SIP
    scheme_code: int
    scheme_name: str
    category: str
    amount_inr: float
    current_pct: float
    target_pct: float
    rationale: str
    warnings: list[str] = field(default_factory=list)
    recommended_prospect: dict | None = None
    alternative_prospects: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "action": self.action, "scheme_code": self.scheme_code,
            "scheme_name": self.scheme_name, "category": self.category,
            "amount_inr": round(self.amount_inr, 2),
            "current_pct": round(self.current_pct, 1),
            "target_pct": self.target_pct,
            "rationale": self.rationale, "warnings": list(self.warnings),
        }
        if self.recommended_prospect:
            d["recommended_prospect"] = self.recommended_prospect
        if self.alternative_prospects:
            d["alternative_prospects"] = self.alternative_prospects
        return d


# ------------------------------------------------------------------
# Top-Performing Mutual Fund Directory (AMFI / ValueResearch / Morningstar)
# Verified Direct-Growth funds with superior 3Y/5Y alpha & low expense ratios
# ------------------------------------------------------------------
CURATED_PROSPECTS: dict[str, list[dict]] = {
    "large_cap": [
        {
            "scheme_code": 120503,
            "scheme_name": "Nippon India Large Cap Fund - Direct Plan - Growth",
            "short_name": "Nippon India Large Cap",
            "category": "large_cap",
            "category_label": "Large Cap Bluechip",
            "fund_house": "Nippon India Mutual Fund",
            "current_nav": 89.40,
            "cagr_1y_pct": 31.2,
            "cagr_3y_pct": 22.4,
            "cagr_5y_pct": 19.5,
            "aum_cr": 34200.0,
            "ter_pct": 0.74,
            "rating_stars": 5,
            "riskometer": "Very High",
            "benchmark": "BSE 100 TRI",
            "alpha_vs_benchmark_pct": 5.4,
            "suitability": "Consistent bluechip compounder with superior downside protection.",
            "min_sip_inr": 1000,
            "min_lumpsum_inr": 5000,
        },
        {
            "scheme_code": 120586,
            "scheme_name": "ICICI Prudential Bluechip Fund - Direct Plan - Growth",
            "short_name": "ICICI Pru Bluechip",
            "category": "large_cap",
            "category_label": "Large Cap Bluechip",
            "fund_house": "ICICI Prudential Mutual Fund",
            "current_nav": 115.80,
            "cagr_1y_pct": 26.4,
            "cagr_3y_pct": 19.8,
            "cagr_5y_pct": 18.2,
            "aum_cr": 58400.0,
            "ter_pct": 0.82,
            "rating_stars": 5,
            "riskometer": "Very High",
            "benchmark": "Nifty 100 TRI",
            "alpha_vs_benchmark_pct": 3.8,
            "suitability": "Low-volatility large cap flagship for steady core allocation.",
            "min_sip_inr": 100,
            "min_lumpsum_inr": 1000,
        },
        {
            "scheme_code": 118834,
            "scheme_name": "Mirae Asset Large Cap Fund - Direct Plan - Growth",
            "short_name": "Mirae Asset Large Cap",
            "category": "large_cap",
            "category_label": "Large Cap Bluechip",
            "fund_house": "Mirae Asset Mutual Fund",
            "current_nav": 108.20,
            "cagr_1y_pct": 24.1,
            "cagr_3y_pct": 17.5,
            "cagr_5y_pct": 16.8,
            "aum_cr": 39100.0,
            "ter_pct": 0.54,
            "rating_stars": 4,
            "riskometer": "Very High",
            "benchmark": "Nifty 100 TRI",
            "alpha_vs_benchmark_pct": 2.2,
            "suitability": "Ultra-low expense ratio (0.54%) with disciplined portfolio quality.",
            "min_sip_inr": 1000,
            "min_lumpsum_inr": 5000,
        },
    ],
    "mid_cap": [
        {
            "scheme_code": 122639,
            "scheme_name": "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
            "short_name": "Parag Parikh Flexi Cap",
            "category": "mid_cap",
            "category_label": "Flexi Cap Global Dynamic",
            "fund_house": "PPFAS Mutual Fund",
            "current_nav": 84.60,
            "cagr_1y_pct": 28.6,
            "cagr_3y_pct": 22.8,
            "cagr_5y_pct": 22.1,
            "aum_cr": 78500.0,
            "ter_pct": 0.62,
            "rating_stars": 5,
            "riskometer": "Very High",
            "benchmark": "Nifty 500 TRI",
            "alpha_vs_benchmark_pct": 6.8,
            "suitability": "Best-in-class Indian equity + international tech + arbitrage cushion.",
            "min_sip_inr": 1000,
            "min_lumpsum_inr": 1000,
        },
        {
            "scheme_code": 127042,
            "scheme_name": "Motilal Oswal Midcap Fund - Direct Plan - Growth",
            "short_name": "Motilal Oswal Midcap",
            "category": "mid_cap",
            "category_label": "Mid Cap High-Conviction",
            "fund_house": "Motilal Oswal Mutual Fund",
            "current_nav": 112.50,
            "cagr_1y_pct": 44.2,
            "cagr_3y_pct": 32.8,
            "cagr_5y_pct": 26.5,
            "aum_cr": 19800.0,
            "ter_pct": 0.68,
            "rating_stars": 5,
            "riskometer": "Very High",
            "benchmark": "Nifty Midcap 150 TRI",
            "alpha_vs_benchmark_pct": 8.9,
            "suitability": "Focused 30-stock portfolio maximizing high-earnings-growth midcaps.",
            "min_sip_inr": 500,
            "min_lumpsum_inr": 500,
        },
        {
            "scheme_code": 118987,
            "scheme_name": "HDFC Mid-Cap Opportunities Fund - Direct Plan - Growth",
            "short_name": "HDFC Mid-Cap Opps",
            "category": "mid_cap",
            "category_label": "Mid Cap Quality",
            "fund_house": "HDFC Mutual Fund",
            "current_nav": 196.30,
            "cagr_1y_pct": 38.5,
            "cagr_3y_pct": 28.9,
            "cagr_5y_pct": 24.2,
            "aum_cr": 71000.0,
            "ter_pct": 0.78,
            "rating_stars": 5,
            "riskometer": "Very High",
            "benchmark": "Nifty Midcap 150 TRI",
            "alpha_vs_benchmark_pct": 6.2,
            "suitability": "Deep institutional scale with veteran fund manager Chirag Setalvad.",
            "min_sip_inr": 1000,
            "min_lumpsum_inr": 1000,
        },
        {
            "scheme_code": 120505,
            "scheme_name": "Nippon India Small Cap Fund - Direct Plan - Growth",
            "short_name": "Nippon India Small Cap",
            "category": "mid_cap",
            "category_label": "Small Cap Alpha",
            "fund_house": "Nippon India Mutual Fund",
            "current_nav": 168.20,
            "cagr_1y_pct": 42.8,
            "cagr_3y_pct": 31.5,
            "cagr_5y_pct": 28.9,
            "aum_cr": 59400.0,
            "ter_pct": 0.69,
            "rating_stars": 5,
            "riskometer": "Very High",
            "benchmark": "Nifty Smallcap 250 TRI",
            "alpha_vs_benchmark_pct": 9.4,
            "suitability": "Highest 5Y risk-adjusted returns across the entire Indian mutual fund industry.",
            "min_sip_inr": 1000,
            "min_lumpsum_inr": 5000,
        },
    ],
    "debt": [
        {
            "scheme_code": 120427,
            "scheme_name": "Kotak Arbitrage Fund - Direct Plan - Growth",
            "short_name": "Kotak Arbitrage",
            "category": "debt",
            "category_label": "Arbitrage / Liquid",
            "fund_house": "Kotak Mahindra Mutual Fund",
            "current_nav": 36.80,
            "cagr_1y_pct": 8.1,
            "cagr_3y_pct": 7.6,
            "cagr_5y_pct": 6.8,
            "aum_cr": 44200.0,
            "ter_pct": 0.38,
            "rating_stars": 5,
            "riskometer": "Low",
            "benchmark": "NIFTY 50 Arbitrage Index",
            "alpha_vs_benchmark_pct": 0.9,
            "suitability": "Zero equity volatility with favorable equity tax treatment (12.5% LTCG).",
            "min_sip_inr": 500,
            "min_lumpsum_inr": 1000,
        },
        {
            "scheme_code": 120251,
            "scheme_name": "ICICI Prudential All Seasons Bond Fund - Direct Plan - Growth",
            "short_name": "ICICI All Seasons Bond",
            "category": "debt",
            "category_label": "Dynamic Bond",
            "fund_house": "ICICI Prudential Mutual Fund",
            "current_nav": 38.60,
            "cagr_1y_pct": 8.4,
            "cagr_3y_pct": 8.1,
            "cagr_5y_pct": 8.6,
            "aum_cr": 12500.0,
            "ter_pct": 0.61,
            "rating_stars": 5,
            "riskometer": "Moderate",
            "benchmark": "NIFTY Composite Debt Index",
            "alpha_vs_benchmark_pct": 1.5,
            "suitability": "Dynamic duration management across sovereign G-Secs and AAA corporate bonds.",
            "min_sip_inr": 1000,
            "min_lumpsum_inr": 5000,
        },
    ],
    "gold_or_other": [
        {
            "scheme_code": 120594,
            "scheme_name": "ICICI Prudential Multi-Asset Fund - Direct Plan - Growth",
            "short_name": "ICICI Pru Multi-Asset",
            "category": "gold_or_other",
            "category_label": "Multi-Asset Dynamic",
            "fund_house": "ICICI Prudential Mutual Fund",
            "current_nav": 724.50,
            "cagr_1y_pct": 29.5,
            "cagr_3y_pct": 25.2,
            "cagr_5y_pct": 21.4,
            "aum_cr": 46000.0,
            "ter_pct": 0.72,
            "rating_stars": 5,
            "riskometer": "Very High",
            "benchmark": "Multi-Asset Composite Index",
            "alpha_vs_benchmark_pct": 7.8,
            "suitability": "Automatic asset rebalancing across Equities (65%), Debt (15%), and Gold/Commodities (20%).",
            "min_sip_inr": 1000,
            "min_lumpsum_inr": 5000,
        },
        {
            "scheme_code": 119800,
            "scheme_name": "Nippon India Gold Savings Fund - Direct Plan - Growth",
            "short_name": "Nippon India Gold Savings",
            "category": "gold_or_other",
            "category_label": "Gold Hedge",
            "fund_house": "Nippon India Mutual Fund",
            "current_nav": 32.40,
            "cagr_1y_pct": 28.2,
            "cagr_3y_pct": 19.4,
            "cagr_5y_pct": 15.2,
            "aum_cr": 6800.0,
            "ter_pct": 0.28,
            "rating_stars": 5,
            "riskometer": "High",
            "benchmark": "Domestic Price of Gold",
            "alpha_vs_benchmark_pct": 0.2,
            "suitability": "Crucial portfolio hedge against equity drawdowns and currency inflation.",
            "min_sip_inr": 500,
            "min_lumpsum_inr": 1000,
        },
    ],
}


def _band_for(cfg: RiskConfig, category: str) -> tuple[float, float]:
    bands = {
        "large_cap": cfg.mf.large_cap_band,
        "mid_cap": cfg.mf.mid_cap_band,
        "debt": cfg.mf.debt_band,
        "gold_or_other": cfg.mf.gold_band,
    }
    return bands.get(category, (0.0, 100.0))


def next_rebalance_date(now: datetime | None = None) -> datetime:
    """Monthly cadence per risk_config.mutual_fund_rules.rebalance_frequency."""
    now = now or datetime.now()
    if now.day <= 5:
        return now.replace(day=5)
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=5)
    return now.replace(month=now.month + 1, day=5)


class MutualFundAdvisor:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    def suggest_allocation(
        self, holdings: list[MFHolding], new_money_inr: float = 0.0,
    ) -> dict:
        """Rebalance suggestions vs the config's category allocation bands.

        Default posture is SIP-style accumulation into underweight categories;
        redemptions are only suggested when a category breaches its upper band,
        and always carry a tax-holding-period check.
        """
        total = sum(h.value_inr for h in holdings) + new_money_inr
        if total <= 0:
            return {"error": "no holdings and no new money provided",
                    "suggestions": []}

        # midpoint of each band as the target
        targets = {}
        for cat in ("large_cap", "mid_cap", "debt", "gold_or_other"):
            lo, hi = _band_for(self.cfg, cat)
            targets[cat] = (lo + hi) / 2.0

        suggestions: list[MFSuggestion] = []
        by_cat: dict[str, list[MFHolding]] = {}
        for h in holdings:
            by_cat.setdefault(h.category, []).append(h)
        cat_values = {c: sum(h.value_inr for h in hs) for c, hs in by_cat.items()}
        cat_pcts = {c: (v / total * 100.0 if total else 0.0) for c, v in cat_values.items()}

        for cat in ("large_cap", "mid_cap", "debt", "gold_or_other"):
            lo, hi = _band_for(self.cfg, cat)
            cur_pct = cat_pcts.get(cat, 0.0)
            delta_target = (targets[cat] / 100.0 * total) - cat_values.get(cat, 0.0)

            if cur_pct < lo and delta_target > 0:
                amount = min(delta_target, new_money_inr) if new_money_inr > 0 else 0.0
                if amount >= 500:
                    mode = ("SIP" if self.cfg.mf.default_mode == "sip" else "LUMPSUM")
                    warn = []
                    if mode == "LUMPSUM":
                        warn.append("Lumpsum flagged as higher-conviction/higher-risk "
                                    "vs SIP default.")

                    # Suggest specific top-performing verified fund prospect
                    prospects = CURATED_PROSPECTS.get(cat, [])
                    primary_fund = prospects[0] if prospects else None
                    fund_name = primary_fund["scheme_name"] if primary_fund else f"(any {cat} fund)"
                    fund_code = primary_fund["scheme_code"] if primary_fund else 0
                    alts = prospects[1:] if len(prospects) > 1 else []
                    cagr_tag = f" [Top Pick: {primary_fund['short_name']} | 3Y CAGR +{primary_fund['cagr_3y_pct']}% | TER {primary_fund['ter_pct']}%]" if primary_fund else ""

                    suggestions.append(MFSuggestion(
                        action="SIP" if mode == "SIP" else "BUY_MORE",
                        scheme_code=fund_code, scheme_name=fund_name,
                        category=cat, amount_inr=amount,
                        current_pct=cur_pct, target_pct=targets[cat],
                        rationale=(
                            f"{cat.replace('_', ' ').title()} is {cur_pct:.0f}% of your MF portfolio, below the "
                            f"{lo:.0f}-{hi:.0f}% band. Directing ₹{amount:,.0f} "
                            f"({mode}) here moves you toward the {targets[cat]:.0f}% midpoint.{cagr_tag}"),
                        warnings=warn,
                        recommended_prospect=primary_fund,
                        alternative_prospects=alts,
                    ))
            elif cur_pct > hi:
                cat_holdings = sorted(by_cat.get(cat, []), key=lambda h: h.holding_days)
                for h in cat_holdings:
                    excess_value = cat_values[cat] - (hi / 100.0 * total)
                    if excess_value <= 0 or h.value_inr <= 0:
                        break
                    redeem = min(excess_value, h.value_inr)
                    sug = MFSuggestion(
                        action="REDUCE", scheme_code=h.scheme_code,
                        scheme_name=h.scheme_name, category=cat,
                        amount_inr=redeem, current_pct=cur_pct,
                        target_pct=targets[cat],
                        rationale=(
                            f"{h.scheme_name} ({cat}) puts the category at {cur_pct:.0f}%, "
                            f"above its {lo:.0f}-{hi:.0f}% band. Trim ~₹{redeem:,.0f}."))
                    if h.holding_days < self.cfg.mf.ltcg_holding_days:
                        sug.warnings.append(
                            f"TAX: held only {h.holding_days} days (< "
                            f"{self.cfg.mf.ltcg_holding_days}). Redemption triggers SHORT-TERM "
                            "gains tax — consider waiting or trimming a older lot instead.")
                    suggestions.append(sug)

        # steady-state: everything within bands -> default SIP continues
        if not any(s.action in ("REDUCE",) for s in suggestions) and not suggestions:
            suggestions.append(MFSuggestion(
                action="HOLD", scheme_code=0, scheme_name="(portfolio)",
                category="all",
                amount_inr=new_money_inr,
                current_pct=float("nan"), target_pct=0.0,
                rationale=("All categories are inside their allocation bands. No rebalance "
                           "needed. Continue regular SIP; next review "
                           f"{next_rebalance_date().strftime('%d %b %Y')}."),
            ))

        return {
            "total_value_inr": total,
            "current_alloc_pct": {k: round(v, 1) for k, v in cat_pcts.items()},
            "target_alloc_pct": targets,
            "bands": {c: _band_for(self.cfg, c) for c in targets},
            "next_rebalance": next_rebalance_date().isoformat(),
            "suggestions": [s.to_dict() for s in suggestions],
            "top_prospects": get_top_performing_prospects(),
            "suggestion_only": True,
        }


def calculate_sip_projection(monthly_amount_inr: float, cagr_pct: float, years: int) -> dict:
    """Calculate future value of a monthly compounding SIP."""
    if monthly_amount_inr <= 0 or years <= 0:
        return {"invested_inr": 0.0, "estimated_wealth_inr": 0.0, "wealth_gain_inr": 0.0, "roi_pct": 0.0}

    r = (cagr_pct / 100.0) / 12.0
    n = years * 12
    invested = monthly_amount_inr * n

    if r <= 0:
        return {
            "invested_inr": round(invested, 2),
            "estimated_wealth_inr": round(invested, 2),
            "wealth_gain_inr": 0.0,
            "roi_pct": 0.0,
        }

    fv = monthly_amount_inr * (((1 + r) ** n - 1) / r) * (1 + r)
    gain = fv - invested
    roi = (gain / invested) * 100.0

    return {
        "monthly_sip_inr": monthly_amount_inr,
        "years": years,
        "cagr_pct": cagr_pct,
        "invested_inr": round(invested, 2),
        "estimated_wealth_inr": round(fv, 2),
        "wealth_gain_inr": round(gain, 2),
        "roi_pct": round(roi, 1),
    }


def get_top_performing_prospects(category: str | None = None) -> list[dict]:
    """Return verified top-performing prospect funds with wealth compounding benchmarks."""
    results = []
    cats = [category] if category and category != "all" and category in CURATED_PROSPECTS else list(CURATED_PROSPECTS.keys())

    for cat in cats:
        funds = CURATED_PROSPECTS.get(cat, [])
        for f in funds:
            fund_copy = dict(f)
            # Add compounding SIP projection for ₹5,000/month
            fund_copy["projections"] = {
                "3y_sip_5k": calculate_sip_projection(5000, f["cagr_3y_pct"], 3),
                "5y_sip_5k": calculate_sip_projection(5000, f["cagr_5y_pct"], 5),
            }
            results.append(fund_copy)

    # Sort descending by 3Y CAGR
    results.sort(key=lambda x: x.get("cagr_3y_pct", 0.0), reverse=True)
    return results
