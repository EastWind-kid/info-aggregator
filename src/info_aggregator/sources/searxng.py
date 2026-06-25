"""SearXNG search source — local Docker deployment."""

from __future__ import annotations

import httpx

from ..models import AuthorityTier, ContentType, SearchQuery, StandardResult
from . import SearchSource, SourceInfo, SourceRegistry


class SearXNGSearcher(SearchSource):
    """Adapter for locally deployed SearXNG (Docker)."""

    info = SourceInfo(
        name="searxng",
        type="local",
        cost_per_call=None,  # Free — self-hosted
    )

    def __init__(self, base_url: str = "http://localhost:8080", timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def search(self, query: SearchQuery) -> list[StandardResult]:
        """Search SearXNG and return standardized results."""
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.base_url}/search",
                params={
                    "q": query.original,
                    "format": "json",
                    "language": "zh-CN",
                },
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            return self._error_result(f"HTTP error: {e}")
        except Exception as e:
            return self._error_result(f"Unexpected error: {e}")

        results: list[StandardResult] = []

        for item in data.get("results", [])[:query.max_results]:
            url = item.get("url", "")
            engine = item.get("engine", "")

            result = StandardResult(
                url=url,
                title=item.get("title", ""),
                snippet=item.get("content", ""),
                source_name=f"searxng/{engine}",
                sources={"searxng"},
                language=self._detect_language(item.get("title", "")),
                content_type=ContentType.UNKNOWN,
                relevance_scores={"searxng": 1.0},
                raw=item,
            )

            # Classify URL authority
            result.authority_tier = self._classify_authority(url)

            results.append(result)

        # Log unresponsive engines
        if data.get("unresponsive_engines"):
            engines = [e[0] for e in data["unresponsive_engines"]]
            # Store as first result's raw metadata
            if results:
                results[0].raw["searxng_unresponsive"] = engines

        return results

    async def health(self) -> dict:
        """Check SearXNG health."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/search?q=health&format=json")
            if response.status_code == 200:
                return {"status": "ok", "remaining_quota": None}
            return {"status": "degraded", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "down", "error": str(e)}

    def _detect_language(self, text: str) -> str:
        """Simple CJK detection."""
        for ch in text:
            if "一" <= ch <= "鿿":
                return "zh"
        return "en"

    def _classify_authority(self, url: str) -> AuthorityTier:
        """Classify URL into authority tiers based on domain patterns.

        TODO: make this config-driven, loaded from config.yaml.
        """
        from urllib.parse import urlparse

        domain = urlparse(url).netloc.lower()

        tier1 = [
            "arxiv.org", "doi.org", "ieee.org", "acm.org",
            "nature.com", "science.org", ".gov", ".edu",
            ".gov.cn", ".edu.cn",
        ]
        tier4 = [
            "twitter.com", "x.com", "reddit.com",
            "zhihu.com", "weibo.com", "tieba.baidu.com",
        ]

        for pattern in tier1:
            if pattern in domain:
                return AuthorityTier.TIER_1

        for pattern in tier4:
            if pattern in domain:
                return AuthorityTier.TIER_4

        return AuthorityTier.UNKNOWN

    def _error_result(self, message: str) -> list[StandardResult]:
        """Return an error result."""
        return [
            StandardResult(
                url="",
                title=f"[SearXNG Error] {message}",
                source_name="searxng",
                sources={"searxng"},
            )
        ]

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# Auto-register
SourceRegistry.register(SearXNGSearcher())
