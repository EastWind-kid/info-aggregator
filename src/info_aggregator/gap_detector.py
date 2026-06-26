"""Gap detection — identify information blind spots in search results.

Zero Rich/Click dependencies. Importable by REST API / MCP Server directly.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .models import AggregatedOutput, StandardResult


# ── Data models ──────────────────────────────────────────────


class GapType(Enum):
    LANGUAGE_IMBALANCE = "language_imbalance"
    AUTHORITY_SKEW = "authority_skew"
    SOURCE_TYPE_ABSENCE = "source_type_absence"
    FRESHNESS_ISSUE = "freshness_issue"
    SOURCE_COVERAGE_GAP = "source_coverage_gap"


class Severity(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class GapFinding:
    """A single identifiable blind spot or quality concern."""
    gap_type: GapType
    severity: Severity
    title: str
    description: str
    details: dict[str, Any] = field(default_factory=dict)
    suggested_queries: list[str] = field(default_factory=list)


@dataclass
class SourceSnippet:
    """One source's view of a shared URL."""
    source_name: str
    snippet: str
    snippet_length: int = 0
    relevance_score: float = 0.0

    def __post_init__(self) -> None:
        if not self.snippet_length:
            self.snippet_length = len(self.snippet)


@dataclass
class CrossSourceDiff:
    """Same URL, different source perspectives."""
    url: str
    title: str
    entries: list[SourceSnippet] = field(default_factory=list)
    snippet_agreement: float = 0.0


@dataclass
class GapAnalysis:
    """Complete gap-detection output."""
    findings: list[GapFinding] = field(default_factory=list)
    cross_source_diffs: list[CrossSourceDiff] = field(default_factory=list)
    summary: str = ""


# ── GapDetector ───────────────────────────────────────────────


class GapDetector:
    """Analyzes AggregatedOutput for information gaps and cross-source differences."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def analyze(self, output: AggregatedOutput) -> GapAnalysis:
        """Run all gap checks and return structured findings."""
        findings: list[GapFinding] = []

        findings.extend(self._check_language_balance(output))
        findings.extend(self._check_authority_skew(output))
        findings.extend(self._check_source_type_absence(output))
        findings.extend(self._check_freshness(output))
        findings.extend(self._check_source_coverage(output))

        cross_source_diffs = self._build_cross_source_diffs(output)
        summary = self._compose_summary(findings)

        return GapAnalysis(
            findings=findings,
            cross_source_diffs=cross_source_diffs,
            summary=summary,
        )

    # ── 1. Language balance ───────────────────────────────

    def _check_language_balance(
        self, output: AggregatedOutput
    ) -> list[GapFinding]:
        valid = [r for r in output.all_results if r.url]
        if not valid:
            return []

        lang_counts: Counter[str] = Counter()
        for r in valid:
            lang = r.language if r.language else "unknown"
            lang_counts[lang] += 1

        total = sum(lang_counts.values())
        if total <= 3:
            return []

        findings: list[GapFinding] = []

        zh_ratio = lang_counts.get("zh", 0) / total
        en_ratio = lang_counts.get("en", 0) / total
        unknown_ratio = lang_counts.get("unknown", 0) / total

        if zh_ratio >= 1.0:
            findings.append(GapFinding(
                gap_type=GapType.LANGUAGE_IMBALANCE,
                severity=Severity.HIGH,
                title="All results are Chinese-language",
                description=f"{total} results, all in Chinese. No English or international perspectives.",
                details={
                    "zh_count": lang_counts["zh"],
                    "en_count": 0,
                    "total": total,
                    "zh_ratio": round(zh_ratio, 2),
                },
                suggested_queries=[
                    f"{output.query.original} English version",
                    f"{output.query.original} site:en.wikipedia.org",
                ],
            ))
        elif zh_ratio > 0.8 and total > 5:
            findings.append(GapFinding(
                gap_type=GapType.LANGUAGE_IMBALANCE,
                severity=Severity.MEDIUM,
                title=f"Results are {zh_ratio:.0%} Chinese — limited English coverage",
                description=f"Only {lang_counts.get('en', 0)} English results out of {total}. Consider searching in English for broader perspectives.",
                details={
                    "zh_count": lang_counts["zh"], "en_count": lang_counts.get("en", 0),
                    "total": total, "zh_ratio": round(zh_ratio, 2),
                },
                suggested_queries=[f"{output.query.original} English"],
            ))

        if en_ratio >= 1.0 and "zh" in lang_counts and lang_counts["zh"] == 0:
            findings.append(GapFinding(
                gap_type=GapType.LANGUAGE_IMBALANCE,
                severity=Severity.MEDIUM,
                title="No Chinese-language results found",
                description=f"All {total} results are in English. Chinese sources may provide different perspectives.",
                details={"en_count": lang_counts["en"], "zh_count": 0, "total": total},
                suggested_queries=[f"{output.query.original} 中文"],
            ))

        if unknown_ratio > 0.5 and total > 5:
            findings.append(GapFinding(
                gap_type=GapType.LANGUAGE_IMBALANCE,
                severity=Severity.LOW,
                title="Language detection incomplete for >50% of results",
                description="Improve language metadata extraction in source adapters.",
                details={"unknown_ratio": round(unknown_ratio, 2), "total": total},
                suggested_queries=[],
            ))

        return findings

    # ── 2. Authority skew ─────────────────────────────────

    def _check_authority_skew(
        self, output: AggregatedOutput
    ) -> list[GapFinding]:
        valid = [r for r in output.all_results if r.url]
        if not valid:
            return []

        tier_counts: Counter[str] = Counter()
        for r in valid:
            tier_counts[r.authority_tier.name] += 1

        total = sum(tier_counts.values())
        findings: list[GapFinding] = []

        unknown_pct = tier_counts.get("UNKNOWN", 0) / total
        tier4_pct = tier_counts.get("TIER_4", 0) / total
        tier1_cnt = tier_counts.get("TIER_1", 0)

        if unknown_pct > 0.6:
            findings.append(GapFinding(
                gap_type=GapType.AUTHORITY_SKEW,
                severity=Severity.HIGH,
                title=f"{unknown_pct:.0%} of results have unknown authority — cannot assess credibility",
                description="Most domains are not classified. Expand the authority tier domain lists in config.yaml.",
                details={
                    "tier_counts": dict(tier_counts),
                    "unknown_ratio": round(unknown_pct, 2),
                },
                suggested_queries=[
                    f"{output.query.original} site:.edu",
                    f"{output.query.original} site:.gov",
                ],
            ))

        if tier4_pct > 0.5:
            findings.append(GapFinding(
                gap_type=GapType.AUTHORITY_SKEW,
                severity=Severity.MEDIUM,
                title=f"{tier4_pct:.0%} of results are from social media (Tier 4)",
                description="Results are dominated by social/personal content. Authoritative sources may be missing.",
                details={"tier4_ratio": round(tier4_pct, 2), "tier4_count": tier_counts["TIER_4"]},
                suggested_queries=[
                    f"{output.query.original} research paper",
                    f"{output.query.original} official documentation",
                ],
            ))

        if tier1_cnt == 0 and total > 8:
            findings.append(GapFinding(
                gap_type=GapType.AUTHORITY_SKEW,
                severity=Severity.LOW,
                title="No Tier-1 (academic/official) sources found",
                description="Consider adding academic or government perspectives.",
                details={"total": total, "tier1_count": 0},
                suggested_queries=[
                    f"{output.query.original} site:.edu",
                    f"{output.query.original} arxiv",
                ],
            ))

        return findings

    # ── 3. Source type absence ────────────────────────────

    def _check_source_type_absence(
        self, output: AggregatedOutput
    ) -> list[GapFinding]:
        valid = [r for r in output.all_results if r.url]
        if not valid:
            return []

        type_counts: Counter[str] = Counter()
        for r in valid:
            type_counts[r.content_type.name] += 1

        total = sum(type_counts.values())
        findings: list[GapFinding] = []
        query_lower = output.query.original.lower()

        # Heuristic: detect what the user might be looking for
        academic_kw = [
            "paper", "research", "study", "论文", "研究", "arxiv",
            "survey", "综述", "doi", "thesis", "学术",
        ]
        news_kw = [
            "news", "latest", "recent", "新闻", "最新", "today", "昨天",
            "breaking", "update", "刚刚",
        ]
        tutorial_kw = [
            "tutorial", "guide", "howto", "how to", "教程", "指南",
            "入门", "example", "quickstart",
        ]

        if any(kw in query_lower for kw in academic_kw):
            if type_counts.get("ACADEMIC", 0) == 0:
                findings.append(GapFinding(
                    gap_type=GapType.SOURCE_TYPE_ABSENCE,
                    severity=Severity.MEDIUM,
                    title="Query looks academic but no academic-type results found",
                    description="Try academic-focused sources (Exa with academic filter) or add 'arxiv' to the query.",
                    details={"type_counts": dict(type_counts), "query_hint": "academic"},
                    suggested_queries=[
                        f"{output.query.original} arxiv",
                        f"{output.query.original} site:scholar.google.com",
                    ],
                ))

        if any(kw in query_lower for kw in news_kw):
            if type_counts.get("NEWS", 0) == 0:
                findings.append(GapFinding(
                    gap_type=GapType.SOURCE_TYPE_ABSENCE,
                    severity=Severity.MEDIUM,
                    title="Query looks news-oriented but no news-type results found",
                    description="News results may require different sources or a time-filtered query.",
                    details={"type_counts": dict(type_counts), "query_hint": "news"},
                    suggested_queries=[
                        f"{output.query.original} after:2026-01-01",
                    ],
                ))

        if any(kw in query_lower for kw in tutorial_kw):
            if type_counts.get("DOCUMENTATION", 0) == 0:
                findings.append(GapFinding(
                    gap_type=GapType.SOURCE_TYPE_ABSENCE,
                    severity=Severity.LOW,
                    title="Tutorial query but no documentation-type results found",
                    description="Try official documentation sources.",
                    details={"type_counts": dict(type_counts), "query_hint": "tutorial"},
                    suggested_queries=[
                        f"{output.query.original} official docs",
                    ],
                ))

        # General: flag if only one content type dominates
        if len(type_counts) == 1 and total > 5:
            only_type = list(type_counts.keys())[0]
            if only_type == "UNKNOWN":
                findings.append(GapFinding(
                    gap_type=GapType.SOURCE_TYPE_ABSENCE,
                    severity=Severity.INFO,
                    title="All results have UNKNOWN content type — classification needs improvement",
                    description="Source adapters should implement better content type classification.",
                    details={"type_counts": dict(type_counts)},
                    suggested_queries=[],
                ))

        return findings

    # ── 4. Freshness ──────────────────────────────────────

    def _check_freshness(
        self, output: AggregatedOutput
    ) -> list[GapFinding]:
        valid = [r for r in output.all_results if r.url]
        if not valid:
            return []

        no_date = sum(1 for r in valid if r.published_date is None)
        total = len(valid)
        no_date_ratio = no_date / total

        findings: list[GapFinding] = []

        if no_date_ratio > 0.6 and total > 5:
            findings.append(GapFinding(
                gap_type=GapType.FRESHNESS_ISSUE,
                severity=Severity.INFO,
                title=f"{no_date_ratio:.0%} of results have no publication date",
                description="Source adapters are not returning publication dates. Metadata extraction needs improvement.",
                details={"no_date_count": no_date, "total": total, "no_date_ratio": round(no_date_ratio, 2)},
                suggested_queries=[],
            ))

        # Check if all dated results are >1 year old
        dated = [r for r in valid if r.published_date is not None]
        if dated:
            now = datetime.now(timezone.utc)
            old = 0
            for r in dated:
                try:
                    pd = r.published_date
                    if isinstance(pd, str):
                        pd = datetime.fromisoformat(pd.replace("Z", "+00:00"))
                    age_days = (now - pd).days
                    if age_days > 365:
                        old += 1
                except (ValueError, TypeError):
                    pass

            if old > 0 and old == len(dated):
                findings.append(GapFinding(
                    gap_type=GapType.FRESHNESS_ISSUE,
                    severity=Severity.MEDIUM,
                    title="All dated results are over 1 year old",
                    description="Consider adding a time filter or searching with '2025' or '2026'.",
                    details={"old_count": old, "dated_total": len(dated)},
                    suggested_queries=[
                        f"{output.query.original} 2026",
                        f"{output.query.original} latest",
                    ],
                ))

        return findings

    # ── 5. Source coverage ────────────────────────────────

    def _check_source_coverage(
        self, output: AggregatedOutput
    ) -> list[GapFinding]:
        sources_with_results = [
            s for s, results in output.results_by_source.items()
            if any(r.url for r in results)
        ]

        total_active = len(output.sources_used)
        with_results = len(sources_with_results)

        findings: list[GapFinding] = []

        if with_results == 1 and total_active > 2:
            findings.append(GapFinding(
                gap_type=GapType.SOURCE_COVERAGE_GAP,
                severity=Severity.HIGH,
                title=f"Only 1 source ({sources_with_results[0]}) returned results out of {total_active}",
                description="Multiple sources are enabled but only one produced results. Other sources may be down, over-budget, or misconfigured.",
                details={
                    "sources_with_results": sources_with_results,
                    "total_active": total_active,
                    "active_sources": output.sources_used,
                },
                suggested_queries=[
                    f"cc-search --stats",
                    f"Check API keys and Docker services",
                ],
            ))

        # Per-source zero-result check
        for src_name, results in output.results_by_source.items():
            if not any(r.url for r in results) and src_name in output.sources_used:
                findings.append(GapFinding(
                    gap_type=GapType.SOURCE_COVERAGE_GAP,
                    severity=Severity.MEDIUM,
                    title=f"Source '{src_name}' returned 0 valid results",
                    description=f"The source was queried but produced no results with URLs. source may need different input.",
                    details={"source": src_name, "raw_error_count": sum(1 for r in results if not r.url)},
                    suggested_queries=[],
                ))

        # Budget skip info
        budget_skipped = set(output.sources_used) - set(output.results_by_source.keys())
        if budget_skipped:
            findings.append(GapFinding(
                gap_type=GapType.SOURCE_COVERAGE_GAP,
                severity=Severity.INFO,
                title=f"{len(budget_skipped)} source(s) skipped due to budget limits",
                description=f"Skipped: {', '.join(sorted(budget_skipped))}. Use --mode full to override or wait for monthly reset.",
                details={"skipped_sources": sorted(budget_skipped)},
                suggested_queries=[],
            ))

        return findings

    # ── 6. Cross-source diffs ─────────────────────────────

    def _build_cross_source_diffs(
        self, output: AggregatedOutput
    ) -> list[CrossSourceDiff]:
        """Group results by URL, build diffs for those with 2+ sources."""
        # Group by URL
        url_map: dict[str, list[str]] = {}
        for r in output.all_results:
            if r.url and len(r.sources) >= 2:
                url_map[r.url] = sorted(r.sources)

        diffs: list[CrossSourceDiff] = []

        # Re-collect by URL with actual snippets
        seen: set[str] = set()
        for r in output.all_results:
            if r.url not in url_map or r.url in seen:
                continue
            if len(r.sources) < 2:
                continue
            seen.add(r.url)

            entries: list[SourceSnippet] = []
            for src_name in sorted(r.sources):
                # Find this source's version of the result
                src_results = output.results_by_source.get(src_name, [])
                snippet = ""
                score = 0.0
                for sr in src_results:
                    if sr.url == r.url:
                        snippet = sr.snippet
                        score = sr.relevance_scores.get(src_name, 0.0)
                        break
                entries.append(SourceSnippet(
                    source_name=src_name,
                    snippet=snippet,
                    relevance_score=score,
                ))

            if len(entries) >= 2:
                # Simple text-similarity metric: character overlap ratio
                if len(entries) == 2:
                    s1 = entries[0].snippet[:200]
                    s2 = entries[1].snippet[:200]
                    if s1 and s2:
                        shared = sum(1 for c in s1 if c in s2)
                        entries[0].snippet_agreement = shared / max(len(s1), 1)
                        # We'll compute agreement for the whole CrossSourceDiff later
                        agreement = 0.0
                    else:
                        agreement = 0.0
                else:
                    agreement = 0.0

                diffs.append(CrossSourceDiff(
                    url=r.url,
                    title=r.title,
                    entries=entries,
                    snippet_agreement=round(agreement, 2),
                ))

        # Sort: most sources first (most interesting)
        diffs.sort(key=lambda d: len(d.entries), reverse=True)
        return diffs

    # ── 7. Summary ────────────────────────────────────────

    def _compose_summary(self, findings: list[GapFinding]) -> str:
        if not findings:
            return "No gaps detected."

        high = sum(1 for f in findings if f.severity == Severity.HIGH)
        medium = sum(1 for f in findings if f.severity == Severity.MEDIUM)
        low = sum(1 for f in findings if f.severity == Severity.LOW)

        parts: list[str] = []
        if high:
            parts.append(f"{high} high-severity")
        if medium:
            parts.append(f"{medium} medium-severity")
        if low:
            parts.append(f"{low} low-severity")
        info = sum(1 for f in findings if f.severity == Severity.INFO)
        if info:
            parts.append(f"{info} informational")

        base = f"{len(findings)} gaps: {', '.join(parts)}."

        # Mention the top gap type
        if high:
            top = next(f for f in findings if f.severity == Severity.HIGH)
            return f"{base} Most critical: {top.title}."

        return base
