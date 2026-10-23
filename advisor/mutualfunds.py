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

    def to_dict(self) -> dict:
        return {
            "action": self.action, "scheme_code": self.scheme_code,
            "scheme_name": self.scheme_name, "category": self.category,
            "amount_inr": round(self.amount_inr, 2),
            "current_pct": round(self.current_pct, 1),
            "target_pct": self.target_pct,
            "rationale": self.rationale, "warnings": list(self.warnings),
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
                    suggestions.append(MFSuggestion(
                        action="SIP" if mode == "SIP" else "BUY_MORE",
                        scheme_code=0, scheme_name=f"(any {cat} fund)",
                        category=cat, amount_inr=amount,
                        current_pct=cur_pct, target_pct=targets[cat],
                        rationale=(
                            f"{cat} is {cur_pct:.0f}% of your MF portfolio, below the "
                            f"{lo:.0f}-{hi:.0f}% band. Directing ₹{amount:,.0f} "
                            f"({mode}) here moves you toward the {targets[cat]:.0f}% midpoint."),
                        warnings=warn))
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
            "suggestion_only": True,
        }
