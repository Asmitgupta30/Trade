"""The Suggestion type — every field required by the risk_config hard locks:
action, amount, confidence, plain-language rationale, estimated cost, and a
naive-baseline comparison. Suggestions NEVER execute anything."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

ACTION_BUY = "BUY"
ACTION_STRONG_BUY = "STRONG_BUY"
ACTION_HOLD = "HOLD"
ACTION_EXIT = "EXIT"
ACTION_NONE = "NO_ACTION"


@dataclass
class Suggestion:
    symbol: str
    sector: str
    action: str                      # BUY / STRONG_BUY / HOLD / EXIT / NO_ACTION
    amount_inr: float
    confidence: float                # 0..1
    rationale: str                   # plain language
    est_cost_inr: float
    baseline_comparison: str         # vs "just hold Nifty / do nothing"
    est_quantity: int = 0
    reference_price: float | None = None
    blocked_by: list[str] = field(default_factory=list)   # rule refusals, explicit
    warnings: list[str] = field(default_factory=list)
    suggestion_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_ts: datetime | None = None
    expires_at: datetime | None = None

    def with_expiry(self, now: datetime, minutes: int) -> "Suggestion":
        self.created_ts = now
        self.expires_at = now + timedelta(minutes=minutes)
        return self

    def is_expired(self, now: datetime) -> bool:
        if self.expires_at is None:
            return False
        return now > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "symbol": self.symbol,
            "sector": self.sector,
            "action": self.action,
            "amount_inr": round(self.amount_inr, 2),
            "est_quantity": self.est_quantity,
            "confidence": round(self.confidence, 4),
            "rationale": self.rationale,
            "est_cost_inr": round(self.est_cost_inr, 2),
            "baseline_comparison": self.baseline_comparison,
            "reference_price": self.reference_price,
            "blocked_by": list(self.blocked_by),
            "warnings": list(self.warnings),
            "created_ts": self.created_ts.isoformat() if self.created_ts else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class SuggestionSet:
    budget_inr: float
    horizon: str                     # "intraday" | "mutual_fund"
    phase: str                       # pre_market / midday_check / pre_close / end_of_day_summary
    generated_at: datetime
    suggestions: list[Suggestion] = field(default_factory=list)
    system_notes: list[str] = field(default_factory=list)  # banners: stale feed, pause, etc.
    cash_left_inr: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_inr": self.budget_inr,
            "horizon": self.horizon,
            "phase": self.phase,
            "generated_at": self.generated_at.isoformat(),
            "cash_left_inr": round(self.cash_left_inr, 2),
            "system_notes": list(self.system_notes),
            "suggestions": [s.to_dict() for s in self.suggestions],
        }
