# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Info Aggregator — a multi-source search tool that queries 5 search backends in parallel, deduplicates by URL, and presents results in a unified multi-angle view. Designed to break filter bubbles by surfacing content from diverse sources (Chinese/English, academic/social, free/paid) without personalized ranking.

## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Run CLI (use python -m because cc-search entry point may not be on PATH on Windows/Git Bash)
PYTHONIOENCODING=utf-8 python -m info_aggregator.cli "your query"

# Modes
python -m info_aggregator.cli "query"                        # budget (free local sources only)
python -m info_aggregator.cli "query" -m full                # all enabled sources, cloud included
python -m info_aggregator.cli "query" -m manual -s searxng,exa  # only specified sources

# View usage statistics and remaining quotas
python -m info_aggregator.cli --stats

# Verbose mode shows per-source status table
python -m info_aggregator.cli "query" -v

# Run tests (when added)
pytest
```

## Architecture

### Pipeline flow

```
CLI (click) → SearchQuery → search_all() → [parallel: Source.search() for each enabled source]
                                            ↓
                                       dedup by URL
                                            ↓
                                       AggregatedOutput
                                            ↓
                                       _render_results() with Rich Text API
```

`pipeline.py:search_all()` is the core orchestrator. It determines active sources based on mode, checks budget limits for cloud sources, fires all `source.search()` calls via `asyncio.gather` in parallel, deduplicates by URL (merging snippets, keeping best content), and returns an `AggregatedOutput`. Cloud sources are only charged credits if they return at least one valid result (URL present).

### Plugin source system

All sources live in `src/info_aggregator/sources/`. To add a new source:

1. Create a file in `sources/` (e.g., `brave.py`)
2. Subclass `SearchSource`, set the `info` class attribute with `SourceInfo`, implement `async search(query) → list[StandardResult]` and `async health() → dict`
3. Call `SourceRegistry.register(YourSource())` at module level
4. The `discover_sources()` function in `__init__.py` auto-imports all modules in `sources/` at startup — no manual import needed

**Cloud sources** read API keys from environment variables with a fallback parameter (e.g., `self.api_key = api_key or os.environ.get("TAVILY_API_KEY", "")`). If the key is missing, `search()` returns an error `StandardResult` with an empty URL — this ensures graceful degradation without crashing the pipeline.

### Key data models (`models.py`)

- `StandardResult` — the normalized output format. `sources` (set) tracks which backends returned this URL. `relevance_scores` maps source_name→score. `full_content` is populated by Firecrawl sources (content extraction). `ai_summary` / `highlights` store Tavily/Exa unique outputs.
- `SearchQuery` — wraps the original query string with mode, source filters, max_results. `rewritten` dict supports future multi-language query expansion.
- `AggregatedOutput` — the complete output with three layers: synthesis (future), multi-angle views (news/technical), raw results.

### Budget tracking (`budget.py`)

Persists to `~/.cc-search/usage.json`. Only cloud sources consume credits. Monthly limits come from `config.yaml` `sources.<name>.monthly_limit`. The pipeline checks `tracker.can_use()` before dispatching and `tracker.record()` only on successful calls. `cc-search --stats` shows per-source consumption vs limits.

### Rendering (`cli.py`)

Uses Rich's `Text` API (not markup strings) to avoid markup injection from URL titles containing `[` characters. Resourceful Rich markup usage would break on real-world content. The `_render_results()` function builds Text objects with explicit style parameters. Windows Git Bash requires `PYTHONIOENCODING=utf-8` due to GBK encoding issues.

### Config (`config.yaml`)

Per-source: `enabled`, `type` (cloud/local), `monthly_limit`, `per_search_limit` (credits consumed per call), `timeout`, `base_url` (for local sources). Modes define which source categories to use (`use_cloud`, `use_local`). Authority tier rules are embedded in each source adapter's `_classify_authority()` method (not yet config-driven from the YAML authority section).

### Docker services (required for local sources)

```
searxng            → http://localhost:8080   (Bing via cn.bing.com)
firecrawl-api-1    → http://localhost:3002   (POST /v1/scrape)
```

Both are managed via docker-compose in the parent directories `searxng/` and (root-level) respectively. Ensure Docker Desktop is running before using `budget` or `full` mode.
