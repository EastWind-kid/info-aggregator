"""MCP Server for Info Aggregator — exposes search tools to Claude Desktop.

Start with: python -m info_aggregator.mcp_server
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .config import load_config
from .models import SearchQuery
from .pipeline import search_all
from .sources import SourceRegistry

# ── Server setup ─────────────────────────────────────────────

server = Server("info-aggregator")


# ── Helpers ──────────────────────────────────────────────────


def _inject_config_to_source(name: str, cfg: dict) -> None:
    """Inject api_key and timeout from config into a source instance."""
    try:
        src = SourceRegistry.get(name)
    except KeyError:
        return

    sources_cfg = cfg.get("sources", {})
    sc = sources_cfg.get(name, {})

    api_key = sc.get("api_key", "")
    if api_key and hasattr(src, "api_key"):
        src.api_key = api_key
        if hasattr(src, "_client"):
            src._client = None

    timeout = sc.get("timeout")
    if timeout and hasattr(src, "timeout"):
        src.timeout = int(timeout)


def _summarise_result(r, max_snippet: int = 300) -> dict[str, Any]:
    """Convert a StandardResult to a compact dict for MCP response."""
    return {
        "title": r.title,
        "url": r.url,
        "snippet": r.snippet[:max_snippet] if r.snippet else "",
        "sources": sorted(r.sources),
        "source_count": r.source_count,
        "authority_tier": r.authority_tier.name,
        "language": r.language,
        "ai_summary": r.ai_summary,
        "highlights": r.highlights,
        "has_full_text": r.has_full_text,
    }


# ── Tool: search ─────────────────────────────────────────────


async def _search_tool(arguments: dict) -> list[TextContent]:
    query_text = arguments.get("query", "")
    mode = arguments.get("mode", "full")
    max_results = min(int(arguments.get("max_results", 10)), 20)

    if not query_text:
        return [TextContent(type="text", text="Error: 'query' is required.")]

    cfg = load_config()
    sq = SearchQuery(
        original=query_text,
        mode=mode if mode in ("budget", "full", "manual") else "full",
        specified_sources=arguments.get("sources", []),
        max_results=max_results,
    )

    output = await search_all(sq, cfg)

    # Build compact response
    results = [_summarise_result(r) for r in output.all_results[:20]]

    gaps = []
    if output.gap_analysis:
        gaps = [
            {
                "severity": f.severity.value.upper(),
                "gap_type": f.gap_type.value,
                "title": f.title,
                "description": f.description,
                "suggested_queries": f.suggested_queries[:3],
            }
            for f in output.gap_analysis.findings[:5]
        ]

    cross_source_count = 0
    if output.gap_analysis:
        cross_source_count = len(output.gap_analysis.cross_source_diffs)

    response = {
        "query": query_text,
        "mode": mode,
        "total_results": len(output.all_results),
        "total_time_ms": round(output.total_time_ms, 1),
        "credits_spent": output.credits_spent,
        "sources_used": output.sources_used,
        "results": results,
        "gaps_count": len(gaps),
        "gaps": gaps,
        "cross_source_diffs_count": cross_source_count,
        "per_source_counts": {
            name: len(res)
            for name, res in output.results_by_source.items()
        },
    }

    return [TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]


# ── Tool: list_sources ───────────────────────────────────────


async def _list_sources_tool(arguments: dict) -> list[TextContent]:
    cfg = load_config()
    sources_cfg = cfg.get("sources", {})

    out = []
    for name in SourceRegistry.list_all():
        src = SourceRegistry.get(name)
        sc = sources_cfg.get(name, {})
        out.append({
            "name": name,
            "type": src.info.type,
            "enabled": sc.get("enabled", False),
            "monthly_limit": sc.get("monthly_limit", 0),
        })

    return [TextContent(type="text", text=json.dumps(out, ensure_ascii=False, indent=2))]


# ── Tool: get_stats ──────────────────────────────────────────


async def _get_stats_tool(arguments: dict) -> list[TextContent]:
    from .budget import get_tracker

    tracker = get_tracker()
    s = tracker.stats()
    cfg = load_config()

    limits = {}
    for name, sc in cfg.get("sources", {}).items():
        limit = sc.get("monthly_limit", 0)
        if limit > 0:
            limits[name] = limit

    out = {
        "monthly_calls": s["monthly_calls"],
        "monthly_credits": s["monthly_credits"],
        "by_source": s["by_source"],
        "limits": limits,
    }

    return [TextContent(type="text", text=json.dumps(out, ensure_ascii=False, indent=2))]


# ── Tool: health ─────────────────────────────────────────────


async def _health_tool(arguments: dict) -> list[TextContent]:
    cfg = load_config()
    sources_cfg = cfg.get("sources", {})

    results = {}
    for name in SourceRegistry.list_all():
        src = SourceRegistry.get(name)
        _inject_config_to_source(name, cfg)

        try:
            h = await src.health()
        except Exception as e:
            h = {"status": "error", "error": str(e)}

        sc = sources_cfg.get(name, {})
        results[name] = {
            "type": src.info.type,
            "enabled": sc.get("enabled", False),
            **h,
        }

    return [TextContent(type="text", text=json.dumps(results, ensure_ascii=False, indent=2))]


# ── Tool registration ────────────────────────────────────────

TOOLS = [
    Tool(
        name="search",
        description="Search across multiple sources (SearXNG, Tavily, Exa, Firecrawl) for a query. Returns deduplicated results with gap analysis identifying blind spots like language imbalance or missing academic sources.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query or question",
                },
                "mode": {
                    "type": "string",
                    "enum": ["budget", "full", "manual"],
                    "description": "Search mode: budget (free local only), full (all sources), manual (specify sources)",
                    "default": "full",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results per source (1-20)",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 20,
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific source names for manual mode",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="list_sources",
        description="List all configured search sources with their type, enabled status, and monthly limits.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_stats",
        description="Get usage statistics: monthly API calls, credits consumed per source, and remaining limits.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="health",
        description="Check health status of all search sources. Returns 'ok', 'degraded', or error details per source.",
        inputSchema={"type": "object", "properties": {}},
    ),
]

ROUTER = {
    "search": _search_tool,
    "list_sources": _list_sources_tool,
    "get_stats": _get_stats_tool,
    "health": _health_tool,
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = ROUTER.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        return await handler(arguments)
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


# ── Entry point ──────────────────────────────────────────────


async def _run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run():
    """Entry point for CLI and python -m."""
    asyncio.run(_run())


if __name__ == "__main__":
    run()
