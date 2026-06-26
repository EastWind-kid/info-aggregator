"""REST API server for Info Aggregator — FastAPI application.

Start with: python -m info_aggregator.cli serve
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import load_config
from .pipeline import search_all
from .models import SearchQuery

# ── FastAPI app ─────────────────────────────────────────────

app = FastAPI(
    title="Info Aggregator API",
    description="Multi-source search with gap detection. Break out of your filter bubble.",
    version="0.2.0",
)

# Allow all origins for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models ─────────────────────────────────────────


class SourceInfoOut(BaseModel):
    name: str
    type: str
    enabled: bool
    healthy: bool | None = None
    health_detail: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Search query")
    mode: str = Field(default="budget", pattern="^(budget|full|manual)$")
    sources: list[str] = Field(default_factory=list, description="Source names (manual mode)")
    max_results: int = Field(default=10, ge=1, le=50)

    model_config = {"from_attributes": True}


class SearchResultOut(BaseModel):
    url: str
    title: str
    snippet: str = ""
    sources: list[str] = Field(default_factory=list)
    source_count: int = 0
    authority_tier: str = "UNKNOWN"
    language: str = ""
    content_type: str = "UNKNOWN"
    relevance_scores: dict[str, float] = Field(default_factory=dict)
    ai_summary: str | None = None
    highlights: list[str] = Field(default_factory=list)
    has_full_text: bool = False
    word_count: int = 0

    model_config = {"from_attributes": True}


class SourceSnippetOut(BaseModel):
    source_name: str
    snippet: str
    snippet_length: int = 0
    relevance_score: float = 0.0

    model_config = {"from_attributes": True}


class CrossSourceDiffOut(BaseModel):
    url: str
    title: str
    entries: list[SourceSnippetOut] = Field(default_factory=list)
    snippet_agreement: float = 0.0

    model_config = {"from_attributes": True}


class GapFindingOut(BaseModel):
    gap_type: str
    severity: str
    title: str
    description: str
    details: dict[str, Any] = Field(default_factory=dict)
    suggested_queries: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class GapAnalysisOut(BaseModel):
    summary: str = ""
    findings: list[GapFindingOut] = Field(default_factory=list)
    cross_source_diffs: list[CrossSourceDiffOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    query: str
    mode: str
    total_results: int
    total_time_ms: float
    credits_spent: dict[str, int] = Field(default_factory=dict)
    sources_used: list[str] = Field(default_factory=list)
    results: list[SearchResultOut] = Field(default_factory=list)
    gap_analysis: GapAnalysisOut | None = None
    per_source_counts: dict[str, int] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded" | "down"
    sources: dict[str, dict[str, Any]]


class StatsResponse(BaseModel):
    monthly_calls: int
    monthly_credits: int
    by_source: dict[str, int]
    limits: dict[str, int]


class SourcesResponse(BaseModel):
    sources: list[SourceInfoOut]


# ── Helpers ─────────────────────────────────────────────────


def _result_to_out(r) -> SearchResultOut:
    """Convert internal StandardResult to API response model."""
    return SearchResultOut(
        url=r.url,
        title=r.title,
        snippet=r.snippet,
        sources=sorted(r.sources),
        source_count=r.source_count,
        authority_tier=r.authority_tier.name,
        language=r.language,
        content_type=r.content_type.name if hasattr(r.content_type, "name") else str(r.content_type),
        relevance_scores=r.relevance_scores,
        ai_summary=r.ai_summary,
        highlights=r.highlights,
        has_full_text=r.has_full_text,
        word_count=r.word_count,
    )


def _output_to_response(output) -> SearchResponse:
    """Convert AggregatedOutput to SearchResponse."""
    results = [_result_to_out(r) for r in output.all_results]

    gap = None
    if output.gap_analysis:
        ga = output.gap_analysis
        gap = GapAnalysisOut(
            summary=ga.summary,
            findings=[
                GapFindingOut(
                    gap_type=f.gap_type.value,
                    severity=f.severity.value,
                    title=f.title,
                    description=f.description,
                    details=f.details,
                    suggested_queries=f.suggested_queries,
                )
                for f in ga.findings
            ],
            cross_source_diffs=[
                CrossSourceDiffOut(
                    url=d.url,
                    title=d.title,
                    entries=[
                        SourceSnippetOut(
                            source_name=e.source_name,
                            snippet=e.snippet,
                            snippet_length=e.snippet_length,
                            relevance_score=e.relevance_score,
                        )
                        for e in d.entries
                    ],
                    snippet_agreement=d.snippet_agreement,
                )
                for d in ga.cross_source_diffs
            ],
        )

    return SearchResponse(
        query=output.query.original,
        mode=output.query.mode,
        total_results=len(output.all_results),
        total_time_ms=output.total_time_ms,
        credits_spent=output.credits_spent,
        sources_used=output.sources_used,
        results=results,
        gap_analysis=gap,
        per_source_counts={
            name: len(res) for name, res in output.results_by_source.items()
        },
    )


# ── Routes ──────────────────────────────────────────────────


@app.get("/api/v1/health", response_model=HealthResponse)
async def health():
    """Check health of all registered search sources."""
    import asyncio
    from .sources import SourceRegistry

    cfg = load_config()
    sources_cfg = cfg.get("sources", {})

    # Inject config values before checking health
    tasks = {}
    for name in SourceRegistry.list_all():
        src = SourceRegistry.get(name)
        sc = sources_cfg.get(src.info.name, {})
        # Inject api_key from config
        api_key = sc.get("api_key", "")
        if api_key and hasattr(src, "api_key"):
            src.api_key = api_key
            if hasattr(src, "_client"):
                src._client = None
        tasks[src.info.name] = src.health()

    results = {}
    all_ok = True
    any_ok = False

    for name, coro in tasks.items():
        try:
            h = await coro
            results[name] = h
            if h.get("status") == "ok":
                any_ok = True
            else:
                all_ok = False
        except Exception as e:
            results[name] = {"status": "error", "error": str(e)}
            all_ok = False

    if all_ok:
        status = "ok"
    elif any_ok:
        status = "degraded"
    else:
        status = "down"

    return HealthResponse(status=status, sources=results)


@app.get("/api/v1/sources", response_model=SourcesResponse)
async def list_sources():
    """List all configured search sources with their status."""
    from .sources import SourceRegistry

    cfg = load_config()
    sources_cfg = cfg.get("sources", {})

    out = []
    for name in SourceRegistry.list_all():
        src = SourceRegistry.get(name)
        name = src.info.name
        src_cfg = sources_cfg.get(name, {})
        out.append(SourceInfoOut(
            name=name,
            type=src.info.type,
            enabled=src_cfg.get("enabled", False),
        ))

    return SourcesResponse(sources=out)


@app.get("/api/v1/stats", response_model=StatsResponse)
async def get_stats():
    """Get budget/usage statistics."""
    from .budget import get_tracker
    from .config import load_config as lc

    tracker = get_tracker()
    s = tracker.stats()
    cfg = lc()
    sources_cfg = cfg.get("sources", {})

    limits = {}
    for name, sc in sources_cfg.items():
        limit = sc.get("monthly_limit", 0)
        if limit > 0:
            limits[name] = limit

    return StatsResponse(
        monthly_calls=s["monthly_calls"],
        monthly_credits=s["monthly_credits"],
        by_source=s["by_source"],
        limits=limits,
    )


@app.post("/api/v1/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Run a multi-source search with gap analysis."""
    sq = SearchQuery(
        original=request.query,
        mode=request.mode,
        specified_sources=request.sources,
        max_results=request.max_results,
    )

    output = await search_all(sq)
    return _output_to_response(output)
