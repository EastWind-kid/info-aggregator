"""Configuration loader for Info Aggregator."""

import os
from pathlib import Path
from typing import Any

import yaml


def _find_config() -> Path:
    """Find config file: env var > local > project root."""
    if env_path := os.environ.get("CC_SEARCH_CONFIG"):
        return Path(env_path)

    local = Path("config.yaml")
    if local.exists():
        return local

    # Look relative to package
    pkg = Path(__file__).parent.parent / "config.yaml"
    if pkg.exists():
        return pkg

    raise FileNotFoundError(
        "config.yaml not found. Set CC_SEARCH_CONFIG env var "
        "or place config.yaml in current directory."
    )


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load and return the full configuration dict."""
    path = path or _find_config()
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_source_config(source_name: str, config: dict | None = None) -> dict:
    """Get configuration for a specific source."""
    if config is None:
        config = load_config()
    sources = config.get("sources", {})
    if source_name not in sources:
        raise KeyError(f"Unknown source: {source_name}")
    return sources[source_name]


def get_enabled_sources(mode: str, config: dict | None = None) -> list[str]:
    """Get list of source names enabled for the given mode."""
    if config is None:
        config = load_config()

    mode_config = config.get("modes", {}).get(mode)
    if not mode_config:
        raise ValueError(f"Unknown mode: {mode}. Valid: full, budget, manual")

    sources = config.get("sources", {})
    enabled = []

    for name, cfg in sources.items():
        if not cfg.get("enabled", False):
            continue

        source_type = cfg.get("type", "cloud")
        if mode == "full":
            enabled.append(name)
        elif mode == "budget":
            if not mode_config.get("use_cloud") and source_type == "cloud":
                continue
            enabled.append(name)
        elif mode == "manual":
            # In manual mode, all enabled sources are available but
            # the user specifies which ones to use at query time
            pass

    return enabled
