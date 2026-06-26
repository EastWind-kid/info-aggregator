"""Data models for standardized search results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .gap_detector import GapAnalysis


class AuthorityTier(Enum):
    """Content authority level. Lower = more authoritative."""

    TIER_1 = 1  # Academic papers, official docs, government
    TIER_2 = 2  # Reputable media, industry standards
    TIER_3 = 3  # Tech blogs, company blogs
    TIER_4 = 4  # Personal blogs, social media
    UNKNOWN = 5  # Could not determine


class ContentType(Enum):
    """Nature of the result."""

    ACADEMIC = "academic"
    NEWS = "news"
    DOCUMENTATION = "documentation"
    BLOG = "blog"
    DISCUSSION = "discussion"
    UNKNOWN = "unknown"


class SourceType(Enum):
    """Billing model of the source."""

    CLOUD = "cloud"  # Paid API
    LOCAL = "local"  # Self-hosted, free


@dataclass
class StandardResult:
    """Normalized representation of a single search result."""

    # Core identifiers
    url: str
    title: str
    snippet: str = ""

    # Full content (from Firecrawl or extract-capable sources)
    full_content: str | None = None

    # Source provenance
    source_name: str = ""
    sources: set[str] = field(default_factory=set)

    # Metadata
    language: str = ""
    published_date: datetime | None = None
    authority_tier: AuthorityTier = AuthorityTier.UNKNOWN
    content_type: ContentType = ContentType.UNKNOWN
    word_count: int = 0

    # Source-level relevance scores (source_name -> score)
    relevance_scores: dict[str, float] = field(default_factory=dict)

    # AI-generated content from sources
    ai_summary: str | None = None  # Tavily answer
    highlights: list[str] = field(default_factory=list)  # Exa highlights

    # Raw data for debugging
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def source_count(self) -> int:
        """How many different sources returned this result."""
        return len(self.sources)

    @property
    def has_full_text(self) -> bool:
        """Whether we have the complete page content."""
        return self.full_content is not None and len(self.full_content) > 0


@dataclass
class SearchQuery:
    """A search query with its parameters."""

    original: str  # The user's original query
    rewritten: dict[str, str] = field(default_factory=dict)  # lang -> rewritten query
    mode: str = "budget"  # full | budget | manual
    specified_sources: list[str] = field(default_factory=list)
    max_results: int = 10


@dataclass
class AggregatedOutput:
    """The complete aggregated output layer structure."""

    query: SearchQuery

    # Layer 1: Top-down synthesis
    synthesis: str | None = None  # AI-generated summary (future)
    consensus_points: list[str] = field(default_factory=list)
    unique_perspectives: list[str] = field(default_factory=list)

    # Layer 2: Multi-angle view
    news_angle: list[StandardResult] = field(default_factory=list)
    technical_angle: list[StandardResult] = field(default_factory=list)
    ai_angle: list[StandardResult] = field(default_factory=list)
    full_text_angle: list[StandardResult] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)  # Identified information gaps

    # Gap analysis (populated by pipeline via GapDetector)
    gap_analysis: object | None = None  # GapAnalysis | None (object avoids circular import)

    # Layer 3: Raw results
    all_results: list[StandardResult] = field(default_factory=list)
    results_by_source: dict[str, list[StandardResult]] = field(default_factory=dict)

    # Metadata
    sources_used: list[str] = field(default_factory=list)
    credits_spent: dict[str, int] = field(default_factory=dict)
    total_time_ms: float = 0.0
