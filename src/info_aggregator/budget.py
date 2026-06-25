"""Budget tracker for API usage — per-source credit accounting.

Tracks consumption in a simple JSON file. Enforces monthly limits.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UsageEntry:
    """A single API call record."""

    source: str
    credits: int
    timestamp: float = field(default_factory=time.time)
    query: str = ""


class BudgetTracker:
    """Tracks per-source API credit consumption.

    Usage:
        tracker = BudgetTracker("~/.cc-search/usage.json")
        tracker.record("tavily", 2, query="AI safety")
        if not tracker.can_use("tavily", monthly_limit=3000):
            print("Monthly limit exceeded!")
    """

    def __init__(self, db_path: str | Path = "~/.cc-search/usage.json"):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[UsageEntry] = []
        self._load()

    def _load(self) -> None:
        """Load usage history from disk."""
        if self.db_path.exists():
            try:
                with open(self.db_path) as f:
                    data = json.load(f)
                self._entries = [UsageEntry(**e) for e in data]
            except (json.JSONDecodeError, TypeError):
                self._entries = []

    def _save(self) -> None:
        """Persist usage history to disk."""
        data = [
            {"source": e.source, "credits": e.credits, "timestamp": e.timestamp, "query": e.query}
            for e in self._entries
        ]
        with open(self.db_path, "w") as f:
            json.dump(data, f, indent=2)

    def record(self, source: str, credits: int, query: str = "") -> None:
        """Record a credit expenditure."""
        if credits <= 0:
            return
        self._entries.append(UsageEntry(source=source, credits=credits, query=query))
        self._save()

    def used_this_month(self, source: str) -> int:
        """Total credits used this calendar month for a source."""
        now = time.localtime()
        month_start = time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, 0))

        return sum(
            e.credits for e in self._entries
            if e.source == source and e.timestamp >= month_start
        )

    def can_use(self, source: str, monthly_limit: int) -> bool:
        """Check if source still has remaining monthly quota."""
        if monthly_limit <= 0:
            return True  # No limit
        return self.used_this_month(source) < monthly_limit

    def remaining(self, source: str, monthly_limit: int) -> int:
        """Remaining credits this month."""
        return max(0, monthly_limit - self.used_this_month(source))

    def stats(self) -> dict[str, Any]:
        """Get usage statistics for all sources this month."""
        now = time.localtime()
        month_start = time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, 0))

        monthly = [e for e in self._entries if e.timestamp >= month_start]

        by_source: dict[str, int] = {}
        for e in monthly:
            by_source[e.source] = by_source.get(e.source, 0) + e.credits

        total_calls = len(monthly)
        total_credits = sum(e.credits for e in monthly)

        return {
            "monthly_calls": total_calls,
            "monthly_credits": total_credits,
            "by_source": by_source,
        }

    def clear(self) -> None:
        """Clear all history (for testing)."""
        self._entries = []
        self._save()


# Global singleton
_tracker: BudgetTracker | None = None


def get_tracker() -> BudgetTracker:
    global _tracker
    if _tracker is None:
        _tracker = BudgetTracker()
    return _tracker
