"""Source base classes and registry.

To add a new source:
1. Subclass SearchSource, implement search() and health()
2. Set the `info` attribute with SourceInfo
3. Call SourceRegistry.register(instance) at module level
4. Add the import below in discover_sources()
"""

from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import SearchQuery, StandardResult


@dataclass
class SourceInfo:
    """Registration metadata for a search source."""

    name: str
    type: str  # "cloud" | "local"
    cost_per_call: int | None  # None = free


class SearchSource(ABC):
    """Abstract base for all search sources."""

    info: SourceInfo

    @abstractmethod
    async def search(self, query: "SearchQuery") -> list["StandardResult"]:
        """Execute a search and return standardized results."""

    @abstractmethod
    async def health(self) -> dict:
        """Check source health. Returns {status, remaining_quota, error}."""


class SourceRegistry:
    """Plugin registry for search sources."""

    _sources: dict[str, SearchSource] = {}

    @classmethod
    def register(cls, source: SearchSource) -> None:
        """Register (or replace) a search source."""
        cls._sources[source.info.name] = source

    @classmethod
    def get(cls, name: str) -> SearchSource:
        """Get a registered source by name."""
        if name not in cls._sources:
            raise KeyError(f"Source not registered: {name}")
        return cls._sources[name]

    @classmethod
    def list_all(cls) -> list[str]:
        """List all registered source names."""
        return list(cls._sources.keys())

    @classmethod
    def clear(cls) -> None:
        """Clear all registered sources (for testing)."""
        cls._sources.clear()


def discover_sources() -> None:
    """Import all source adapter modules to trigger registration.

    Each module calls SourceRegistry.register() at import time.
    """
    _package_dir = Path(__file__).parent
    for _, name, is_pkg in pkgutil.iter_modules([str(_package_dir)]):
        if not is_pkg and name not in ("__init__",):
            try:
                importlib.import_module(f"info_aggregator.sources.{name}")
            except ImportError:
                pass


# Auto-discover at import time
discover_sources()
