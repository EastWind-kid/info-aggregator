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

# Debug mode — save raw per-source JSON to dev.raw_output_dir
PYTHONIOENCODING=utf-8 python -m info_aggregator.cli "query" -m full --debug

# REST API server
PYTHONIOENCODING=utf-8 python -m info_aggregator.cli serve --port 8000
# Then open http://localhost:8000/docs for interactive Swagger UI

# MCP server (for Claude Desktop integration)
PYTHONIOENCODING=utf-8 python -m info_aggregator.cli mcp

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

Per-source: `enabled`, `type` (cloud/local), `monthly_limit`, `per_search_limit` (credits consumed per call), `timeout`, `base_url` (for local sources), `api_key` (for cloud sources — read from here, not just env vars). Modes define which source categories to use (`use_cloud`, `use_local`). Authority tier rules are embedded in each source adapter's `_classify_authority()` method (not yet config-driven from the YAML authority section).

**IMPORTANT**: `config.yaml` contains real API keys and is git-ignored. Use `config.example.yaml` as the template when cloning on a new machine — copy it to `config.yaml` and fill in keys.

### Docker services (required for local sources)

```
searxng            → http://localhost:8080   (Bing via cn.bing.com)
firecrawl-api-1    → http://localhost:3002   (POST /v1/scrape)
```

Both are managed via docker-compose in the parent directories `searxng/` and (root-level) respectively. Ensure Docker Desktop is running before using `budget` or `full` mode.

### Gap detection (`gap_detector.py`)

Standalone module with zero Rich/Click dependencies — usable by future REST API / MCP Server. Analyzes `AggregatedOutput` for:

| Check | What it detects | Severity triggers |
|-------|----------------|-------------------|
| Language balance | zh/en ratio skew | >80% single language → MEDIUM, 100% → HIGH |
| Authority skew | Tier distribution of domains | >60% UNKNOWN → HIGH, >50% TIER_4 → MEDIUM |
| Source type absence | Missing content types vs query keywords | Academic/news/tutorial queries missing corresponding type → MEDIUM |
| Freshness | Publication date coverage | >60% no dates → INFO, all >1yr old → MEDIUM |
| Source coverage | Results-per-source distribution | 1 source with results out of many → HIGH, per-source zero results → MEDIUM |
| Cross-source diffs | Same URL from 2+ sources | Side-by-side snippet comparison rendered in CLI |

The `GapAnalysis` result is attached to `AggregatedOutput.gap_analysis`. The `gaps: list[str]` field on AggregatedOutput is populated from finding titles for backward compatibility.

Gap warnings and cross-source diffs are rendered automatically in CLI output (no extra flag needed).

### Debug output (`debug_output.py`)

When `--debug` / `-d` is passed, raw per-source search results are saved as JSON files under the directory configured in `config.yaml` → `dev.raw_output_dir` (default: `./debug-output`). The structure is:

```
debug-output/
  YYYY-MM-DD/
    HHMMSS-<query-slug>/
      _summary.json          # query metadata, timing, per-source counts
      searxng.json           # raw StandardResult list per source
      tavily.json
      exa.json
      ...
```

Each `.json` file contains the full serialized `StandardResult` objects including the `raw` field (original API response). This is useful for comparing how different sources represent the same information.

### REST API server (`server.py`)

FastAPI application on port 8000 by default. Endpoints:

```
GET  /api/v1/health    — all source health checks
GET  /api/v1/sources   — list configured sources with status
GET  /api/v1/stats     — budget/usage statistics
POST /api/v1/search    — run multi-source search (body: {query, mode, max_results, sources})
GET  /docs             — interactive Swagger UI
```

Pydantic models for request/response are defined in `server.py`. The server reuses `pipeline.search_all()` — all gap detection and dedup logic applies. API keys are injected from `config.yaml` at request time (same pattern as pipeline).

### MCP server (`mcp_server.py`)

Exposes 4 tools to Claude Desktop or any MCP client via stdio transport:

| Tool | Description |
|------|-------------|
| `search` | Multi-source search with gap analysis |
| `list_sources` | List configured sources and status |
| `get_stats` | Budget/usage stats |
| `health` | Per-source health check |

Claude Desktop config (`claude_desktop_config.json`):
```json
{"mcpServers": {"info-aggregator": {"command": "python", "args": ["-m", "info_aggregator.mcp_server"]}}}
```

### Interaction layer summary

```
CLI          ← cc-search "query" --mode full
REST API     ← http://localhost:8000/api/v1/search  + /docs (Swagger)
MCP Server   ← stdio transport → Claude Desktop
```
