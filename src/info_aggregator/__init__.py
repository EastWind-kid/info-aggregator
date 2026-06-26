"""Info Aggregator - Multi-source information gathering tool."""

from .gap_detector import GapAnalysis, GapDetector, GapFinding, GapType, Severity
from .models import AggregatedOutput, SearchQuery, StandardResult
from .server import app

__version__ = "0.3.0"

__all__ = [
    "GapDetector",
    "GapAnalysis",
    "GapFinding",
    "GapType",
    "Severity",
    "AggregatedOutput",
    "StandardResult",
    "SearchQuery",
    "app",
]
