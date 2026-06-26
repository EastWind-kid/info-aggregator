"""Search pipeline orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from .budget import get_tracker
from .config import get_source_config, load_config
from .models import AggregatedOutput, SearchQuery, StandardResult
from .sources import SearchSource, SourceRegistry

logger = logging.getLogger(__name__)


async def _search_one(
    source: SearchSource,
    query: SearchQuery,
) -> tuple[str, list[StandardResult], float]:
    """Run a single source search, return (name, results, latency_ms)."""
    t0 = time.monotonic()
    try:
        results = await source.search(query)
    except Exception as exc:
        results = [
            StandardResult(
                url="",
                title=f"[{source.info.name}] Error: {exc}",
                source_name=source.info.name,
                sources={source.info.name},
            )
        ]
    latency = (time.monotonic() - t0) * 1000
    return source.info.name, results, latency


async def search_all(
    query: SearchQuery,
    config: dict | None = None,
) -> AggregatedOutput:
    """Run search across all active sources in parallel, aggregate results.

    Enforces budget limits on cloud sources.
    """
    t0 = time.monotonic()

    if config is None:
        config = load_config()

    tracker = get_tracker()

    # Determine active sources
    active_names: list[str] = []
    if query.mode == "manual" and query.specified_sources:
        active_names = query.specified_sources
    else:
        mode_config = config.get("modes", {}).get(query.mode, {})
        sources_config = config.get("sources", {})

        for name, cfg in sources_config.items():
            if not cfg.get("enabled", False):
                continue
            if query.mode == "budget" and cfg.get("type") == "cloud" and not mode_config.get("use_cloud"):
                continue
            active_names.append(name)

    # Budget check: filter out cloud sources over monthly limit
    skipped_over_budget: list[str] = []
    sources_config = config.get("sources", {})
    final_sources: list[str] = []

    for name in active_names:
        cfg = sources_config.get(name, {})
        if cfg.get("type") == "cloud":
            monthly = cfg.get("monthly_limit", 0)
            if monthly > 0 and not tracker.can_use(name, monthly):
                skipped_over_budget.append(name)
                continue
        final_sources.append(name)

    # Resolve sources and inject config
    tasks = []
    for name in final_sources:
        try:
            src = SourceRegistry.get(name)
            # Inject api_key from config if available
            cfg = sources_config.get(name, {})
            api_key = cfg.get("api_key", "")
            if api_key and hasattr(src, "api_key"):
                src.api_key = api_key
                # Reset cached client so it picks up the new key
                if hasattr(src, "_client"):
                    src._client = None
            # Inject timeout from config if available
            timeout = cfg.get("timeout")
            if timeout and hasattr(src, "timeout"):
                src.timeout = int(timeout)
            tasks.append(_search_one(src, query))
        except KeyError:
            continue

    # Run all in parallel
    results_by_source: dict[str, list[StandardResult]] = {}
    credits: dict[str, int] = defaultdict(int)

    if tasks:
        outcomes = await asyncio.gather(*tasks)
        for name, results, latency in outcomes:
            results_by_source[name] = results

            # Record credit consumption only for successful calls
            src_cfg = get_source_config(name, config)
            cost = src_cfg.get("per_search_limit") or src_cfg.get("cost_per_call", 0)
            if cost and src_cfg.get("type") == "cloud":
                # Only charge if at least one result has a real URL
                has_valid = any(r.url for r in results)
                if has_valid:
                    credits[name] = int(cost)
                    tracker.record(name, int(cost), query.original[:80])

    # Deduplicate by URL
    seen_urls: dict[str, StandardResult] = {}
    for name, results in results_by_source.items():
        for r in results:
            if r.url and r.url in seen_urls:
                existing = seen_urls[r.url]
                existing.sources.add(name)
                existing.relevance_scores[name] = r.relevance_scores.get(name, 0)
                if len(r.snippet) > len(existing.snippet):
                    existing.snippet = r.snippet
                if r.full_content and not existing.full_content:
                    existing.full_content = r.full_content
            elif r.url:
                r.sources.add(name)
                r.relevance_scores[name] = 1.0
                seen_urls[r.url] = r

    all_results = list(seen_urls.values())
    # Also include error results (no URL)
    for name, results in results_by_source.items():
        for r in results:
            if not r.url:
                all_results.append(r)

    # Merge sources_used
    all_sources_used = final_sources + skipped_over_budget

    # Categorize by angle
    news_results: list[StandardResult] = []
    tech_results: list[StandardResult] = []
    for r in all_results:
        if r.content_type.name == "NEWS":
            news_results.append(r)
        else:
            tech_results.append(r)

    total_time = (time.monotonic() - t0) * 1000

    output = AggregatedOutput(
        query=query,
        all_results=all_results,
        results_by_source=results_by_source,
        sources_used=all_sources_used,
        credits_spent=dict(credits),
        total_time_ms=total_time,
        news_angle=news_results,
        technical_angle=tech_results,
    )

    # Run gap detection
    from .gap_detector import GapDetector

    detector = GapDetector(config)
    output.gap_analysis = detector.analyze(output)
    output.gaps = [f.title for f in output.gap_analysis.findings]

    return output
