"""Debug output — save raw search results for inspection."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import AggregatedOutput


def _slugify(text: str, max_len: int = 40) -> str:
    """Convert query text to a safe filename slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug[:max_len]


def _make_serializable(obj):
    """Recursively convert objects to JSON-serializable types."""
    if hasattr(obj, "__dataclass_fields__"):
        result = {}
        for f in obj.__dataclass_fields__:
            value = getattr(obj, f)
            result[f] = _make_serializable(value)
        return result
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if hasattr(obj, "value"):  # Enum
        return obj.value
    return obj


def save_debug_output(
    output: AggregatedOutput,
    config: dict,
) -> Path | None:
    """Save raw search results to the configured debug directory.

    Returns the output directory path, or None if not configured.
    """
    dev_cfg = config.get("dev", {})
    raw_dir = dev_cfg.get("raw_output_dir", "")

    if not raw_dir:
        return None

    base = Path(raw_dir).resolve()

    # Build timestamped subdirectory
    now = datetime.now(timezone.utc)
    date_dir = now.strftime("%Y-%m-%d")
    ts = now.strftime("%H%M%S")
    slug = _slugify(output.query.original)
    out_dir = base / date_dir / f"{ts}-{slug}"

    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Per-source raw results
    for src_name, results in output.results_by_source.items():
        serialized = _make_serializable(results)
        filepath = out_dir / f"{src_name}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(serialized, f, ensure_ascii=False, indent=2)

    # 2. Summary metadata
    summary = {
        "query": output.query.original,
        "mode": output.query.mode,
        "specified_sources": output.query.specified_sources,
        "sources_used": output.sources_used,
        "total_unique_results": len(output.all_results),
        "credits_spent": output.credits_spent,
        "total_time_ms": output.total_time_ms,
        "per_source_counts": {
            name: len(res) for name, res in output.results_by_source.items()
        },
        "generated_at": now.isoformat(),
    }

    with open(out_dir / "_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return out_dir
