"""CLI entry point for Info Aggregator."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# Force UTF-8 on Windows to avoid GBK encode errors with emoji
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

console = Console(force_terminal=True, legacy_windows=False)


def _render_results(output) -> None:
    """Render aggregated output using Rich."""
    results = output.all_results

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    console.print()
    console.print(f"[bold]Results: {len(results)}[/bold] unique items "
                  f"from [green]{', '.join(output.sources_used)}[/green] "
                  f"in [dim]{output.total_time_ms:.0f}ms[/dim]")

    # Credits summary
    if output.credits_spent:
        credit_str = " ".join(
            f"[cyan]{name}[/cyan]:{count}" for name, count in output.credits_spent.items()
        )
        console.print(f"[dim]Credits spent: {credit_str}[/dim]")

    # Gap warnings (before individual results)
    if output.gap_analysis:
        _render_gap_warnings(output.gap_analysis)

    console.print()

    for i, r in enumerate(results[:30], 1):
        # Authority badge
        tier_style = {
            "TIER_1": "A1",
            "TIER_4": "A4",
        }.get(r.authority_tier.name, "??")

        # Source count badge
        src_badge = f"{r.source_count}s"
        lang = r.language if r.language else "?"

        # Build result line with Rich Text for safe rendering
        from rich.text import Text

        line = Text()
        line.append(f"{i:2}. ", style="bold")
        line.append(f"{tier_style} ", style={
            "A1": "bold green", "A4": "dim red",
        }.get(tier_style, "dim"))
        line.append(f"[{src_badge}] ", style="cyan" if r.source_count >= 2 else "")
        line.append(r.title, style="bold")
        console.print(line)

        if r.snippet:
            console.print(Text(f"     {r.snippet[:200]}", style="dim"))

        # Source detail
        source_labels = []
        for s in sorted(r.sources):
            score = r.relevance_scores.get(s, 0)
            source_labels.append(f"{s}:{score:.1f}" if score > 0 else s)

        console.print(Text(f"     {r.url}", style="dim underline"))
        console.print(Text(f"     {' | '.join(source_labels)} | {lang}", style="dim italic"))

        console.print()

    if len(results) > 30:
        console.print(f"[dim]... and {len(results) - 30} more results[/dim]")

    # Cross-source comparison (after individual results)
    if output.gap_analysis:
        _render_cross_source_diffs(output.gap_analysis)

    # Summary statistics
    console.print()
    stats = Table(title="Coverage Summary")
    stats.add_column("Source", style="cyan")
    stats.add_column("Results", justify="right")
    for src_name, src_results in output.results_by_source.items():
        stats.add_row(src_name, str(len(src_results)))
    console.print(stats)


# ── Gap warning rendering ────────────────────────────────────

_SEVERITY_ICONS = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🔵",
    "info": "ℹ️ ",
}

_SEVERITY_STYLE = {
    "high": "bold red",
    "medium": "yellow",
    "low": "blue",
    "info": "dim",
}


def _render_gap_warnings(gap_analysis) -> None:
    """Render gap detection findings as a Rich Panel."""
    from .gap_detector import GapAnalysis

    assert isinstance(gap_analysis, GapAnalysis)

    findings = gap_analysis.findings
    if not findings:
        return

    # Build content lines
    lines: list[str] = []
    for f in findings[:8]:  # cap to avoid overwhelming output
        icon = _SEVERITY_ICONS.get(f.severity.value, "  ")
        style = _SEVERITY_STYLE.get(f.severity.value, "dim")
        lines.append(f"[{style}]{icon} {f.severity.value.upper():6s}[/{style}] {f.title}")

        if f.suggested_queries:
            for sq in f.suggested_queries[:2]:
                lines.append(f"          [dim italic]→ {sq}[/dim italic]")

    if len(findings) > 8:
        lines.append(f"[dim]... and {len(findings) - 8} more findings[/dim]")

    content = "\n".join(lines)

    # Summary header
    if gap_analysis.summary:
        content = f"[bold]{gap_analysis.summary}[/bold]\n\n{content}"

    console.print(Panel(
        content,
        title="Gap Warnings",
        border_style="yellow",
        padding=(0, 1),
    ))


def _render_cross_source_diffs(gap_analysis) -> None:
    """Render cross-source snippet comparisons for shared URLs."""
    from .gap_detector import GapAnalysis

    assert isinstance(gap_analysis, GapAnalysis)

    diffs = gap_analysis.cross_source_diffs
    if not diffs:
        return

    console.print()
    console.print(
        Panel.fit(
            f"[dim]{len(diffs)} URL(s) covered by multiple sources — "
            f"comparing snippets[/dim]",
            title="Cross-Source Comparison",
            border_style="cyan",
            padding=(0, 1),
        )
    )

    shown = 0
    for d in diffs[:5]:
        shown += 1
        console.print()

        # Title & URL header
        from rich.text import Text
        header = Text()
        header.append(f"  {shown}. ", style="bold")
        header.append(d.title, style="bold")
        console.print(header)
        console.print(Text(f"     {d.url}", style="dim underline"))

        # Per-source snippets in a table
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column("source", style="cyan", width=14)
        tbl.add_column("snippet", style="dim")

        for entry in d.entries:
            snip = entry.snippet[:200] if entry.snippet else "(no snippet)"
            score = f" [{entry.relevance_score:.1f}]" if entry.relevance_score else ""
            tbl.add_row(f"  {entry.source_name}{score}", snip)

        console.print(tbl)

    if len(diffs) > 5:
        console.print(f"\n[dim]... and {len(diffs) - 5} more cross-source comparisons[/dim]")


def _show_stats(config: dict) -> None:
    """Display usage statistics."""
    from .budget import get_tracker

    tracker = get_tracker()
    s = tracker.stats()
    sources = config.get("sources", {})

    console.print()
    console.print(
        Panel.fit("Usage Statistics (this month)", title="Stats", border_style="green")
    )

    # Summary
    console.print(f"[bold]Total calls:[/bold] {s['monthly_calls']}")
    console.print(f"[bold]Total cloud credits:[/bold] {s['monthly_credits']}")
    console.print()

    # Per-source breakdown
    tbl = Table(title="Per-Source Usage")
    tbl.add_column("Source", style="cyan")
    tbl.add_column("Type")
    tbl.add_column("Credits Used", justify="right")
    tbl.add_column("Monthly Limit", justify="right")
    tbl.add_column("Remaining", justify="right")
    tbl.add_column("Status")

    for src_name, src_cfg in sources.items():
        used = s["by_source"].get(src_name, 0)
        limit = src_cfg.get("monthly_limit", 0)
        src_type = src_cfg.get("type", "?")

        if src_type == "cloud" and limit > 0:
            remaining = max(0, limit - used)
            pct = (used / limit * 100) if limit > 0 else 0
            if pct > 90:
                status = "[red]critical[/red]"
            elif pct > 50:
                status = "[yellow]half[/yellow]"
            else:
                status = "[green]ok[/green]"
            limit_str = str(limit)
            remaining_str = str(remaining)
        else:
            status = "[dim]n/a[/dim]"
            limit_str = "∞"
            remaining_str = "∞"

        tbl.add_row(
            src_name,
            src_type,
            str(used),
            limit_str,
            remaining_str,
            status,
        )

    console.print(tbl)


class _SearchGroup(click.Group):
    """Custom group: routes unknown first args to 'search' subcommand."""

    def resolve_command(self, ctx, args):
        if args and args[0] not in self.commands:
            cmd = self.commands["search"]
            return cmd.name, cmd, args
        return super().resolve_command(ctx, args)


@click.group(cls=_SearchGroup, invoke_without_command=False)
@click.version_option(version="0.2.0", prog_name="cc-search")
@click.pass_context
def main(ctx) -> None:
    """Multi-source information aggregation — break out of your filter bubble.

    \b
    Examples:
      cc-search "Rust async vs Go goroutines" --mode full
      cc-search "大模型安全对齐" --mode budget
      cc-search --stats
      cc-search serve --port 8000
    """
    if ctx.invoked_subcommand is None:
        # When --version is the only flag, Click handles it via version_option
        pass


@main.command("search", hidden=True)
@click.argument("query", required=False)
@click.option(
    "--mode",
    "-m",
    type=click.Choice(["budget", "full", "manual"], case_sensitive=False),
    default="budget",
    help="Search mode (default: budget)",
)
@click.option(
    "--sources",
    "-s",
    default="",
    help="Comma-separated source names (for manual mode)",
)
@click.option(
    "--config",
    "-c",
    default=None,
    type=click.Path(exists=True),
    help="Path to config file",
)
@click.option("--max-results", "-n", default=10, help="Max results per source")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose output")
@click.option("--debug", "-d", is_flag=True, help="Save raw results to debug output dir")
@click.option("--stats", is_flag=True, help="Show usage statistics and exit")
def _search_cmd(
    query: str | None,
    mode: str,
    sources: str,
    config: str | None,
    max_results: int,
    verbose: bool,
    debug: bool,
    stats: bool,
) -> None:
    """Multi-source information aggregation — break out of your filter bubble.

    QUERY: Your search topic or question.

    \b
    Examples:
      cc-search "Rust async vs Go goroutines" --mode full
      cc-search "大模型安全对齐" --mode budget
      cc-search "NixOS flakes" --mode manual --sources searxng,exa
      cc-search --stats
    """
    start_time = time.monotonic()

    # --- Config loading ---
    console.print("[dim]Loading configuration...[/dim]")
    from .config import load_config

    try:
        cfg = load_config(Path(config) if config else None)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    # --- Stats mode ---
    if stats:
        _show_stats(cfg)
        return

    if not query:
        console.print("[red]Error:[/red] QUERY is required for search mode.")
        console.print("Use --stats to see usage statistics, or provide a query.")
        sys.exit(1)

    # --- Header ---
    console.print()
    console.print(
        Panel.fit(
            f"[bold]{query}[/bold]",
            title="Query",
            border_style="blue",
        )
    )

    # --- Mode info ---
    specified = [s.strip() for s in sources.split(",") if s.strip()]
    console.print(
        f"Mode: [cyan]{mode}[/cyan]  |  "
        f"Specified sources: [green]{specified if specified else 'from config'}[/green]"
    )

    if verbose:
        from .config import get_enabled_sources
        active = get_enabled_sources(mode, cfg)
        tbl = Table("Source", "Type", "Status", title="Source Details")
        for src_name, src_cfg in cfg.get("sources", {}).items():
            enabled = "on" if src_cfg.get("enabled") else "off"
            is_active = ">" if src_name in active else "-"
            style = "green" if src_cfg.get("enabled") else "dim"
            tbl.add_row(
                f"[{style}]{is_active} {src_name}[/{style}]",
                f"[{style}]{src_cfg.get('type', '?')}[/{style}]",
                f"[{style}]{enabled}[/{style}]",
            )
        console.print(tbl)

    # --- Run search ---
    console.print("[dim]Searching...[/dim]")

    from .models import SearchQuery
    from .pipeline import search_all

    sq = SearchQuery(
        original=query,
        mode=mode,
        specified_sources=specified,
        max_results=max_results,
    )

    try:
        output = asyncio.run(search_all(sq, cfg))
    except Exception as e:
        console.print(f"[red]Search error:[/red] {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    # --- Debug output ---
    if debug:
        from .debug_output import save_debug_output
        out_dir = save_debug_output(output, cfg)
        if out_dir:
            console.print(f"[dim]Debug output saved to: {out_dir}[/dim]")
        else:
            console.print(
                "[yellow]Debug mode on but dev.raw_output_dir is not set in config.yaml[/yellow]"
            )

    # --- Render ---
    _render_results(output)

    # --- Footer ---
    elapsed = (time.monotonic() - start_time) * 1000
    console.print(f"[dim]Wall-clock: {elapsed:.0f}ms[/dim]")


# ── MCP command ──────────────────────────────────────────────


@main.command("mcp")
def mcp() -> None:
    """Start MCP server (stdio transport for Claude Desktop)."""
    from .mcp_server import run
    run()


# ── Serve command ────────────────────────────────────────────


@main.command("serve")
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", default=8000, help="Bind port")
@click.option("--reload", is_flag=True, help="Auto-reload on code changes")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the REST API server."""
    import uvicorn

    console.print(f"[bold]Starting Info Aggregator API on http://{host}:{port}[/bold]")
    console.print(f"[dim]Docs: http://{host}:{port}/docs[/dim]")
    uvicorn.run(
        "info_aggregator.server:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
