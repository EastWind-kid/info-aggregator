"""Firecrawl cloud API adapter.

Firecrawl Cloud is a hosted web scraping service.
Docs: https://docs.firecrawl.dev
"""

from __future__ import annotations

import os

import httpx

from ..models import AuthorityTier, ContentType, SearchQuery, StandardResult
from . import SearchSource, SourceInfo, SourceRegistry

FIRECRAWL_CLOUD_URL = "https://api.firecrawl.dev"


class FirecrawlCloudScraper(SearchSource):
    """Adapter for Firecrawl cloud API."""

    info = SourceInfo(
        name="firecrawl_cloud",
        type="cloud",
        cost_per_call=1,
    )

    def __init__(
        self,
        api_key: str | None = None,
        timeout: int = 30,
    ):
        self.api_key = api_key or os.environ.get("FIRECRAWL_API_KEY", "")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
        return self._client

    async def search(self, query: SearchQuery) -> list[StandardResult]:
        """Scrape URLs — query.original must be a URL, or use scrape_urls."""
        if not self.api_key:
            return self._error_result(
                "FIRECRAWL_API_KEY not set. Set env var or configure in code."
            )

        urls: list[str] = []
        if query.original.startswith(("http://", "https://")):
            urls.append(query.original)

        scrape_list = query.rewritten.get("scrape_urls", "")
        if scrape_list:
            if isinstance(scrape_list, str):
                urls.extend(u.strip() for u in scrape_list.split(",") if u.strip())
            elif isinstance(scrape_list, list):
                urls.extend(scrape_list)

        if not urls:
            return []

        client = await self._get_client()
        results: list[StandardResult] = []

        for url in urls[: query.max_results]:
            try:
                r = await self._scrape_one(client, url)
                if r:
                    results.append(r)
            except Exception:
                continue

        return results

    async def _scrape_one(
        self, client: httpx.AsyncClient, url: str
    ) -> StandardResult | None:
        try:
            response = await client.post(
                f"{FIRECRAWL_CLOUD_URL}/v1/scrape",
                json={"url": url, "formats": ["markdown"]},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError:
            return None

        if not data.get("success"):
            return None

        content = data.get("data", {})
        markdown = content.get("markdown", "")
        metadata = content.get("metadata", {})

        source_url = metadata.get("sourceURL") or metadata.get("url") or url
        title = metadata.get("title") or source_url

        result = StandardResult(
            url=source_url,
            title=title,
            snippet=markdown[:500] if markdown else "",
            full_content=markdown if len(markdown) > 500 else None,
            source_name="firecrawl_cloud",
            sources={"firecrawl_cloud"},
            language=metadata.get("language", ""),
            word_count=len(markdown.split()) if markdown else 0,
            relevance_scores={"firecrawl_cloud": 1.0},
            raw=data,
        )
        result.authority_tier = self._classify_authority(source_url)
        return result

    async def health(self) -> dict:
        if not self.api_key:
            return {"status": "not_configured", "error": "API key not set"}
        return {"status": "ok", "remaining_quota": None}

    def _classify_authority(self, url: str) -> AuthorityTier:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        for p in [
            "arxiv.org", "doi.org", "ieee.org", ".gov", ".edu",
            ".gov.cn", ".edu.cn",
        ]:
            if p in domain:
                return AuthorityTier.TIER_1
        for p in ["twitter.com", "x.com", "reddit.com", "zhihu.com", "weibo.com"]:
            if p in domain:
                return AuthorityTier.TIER_4
        return AuthorityTier.UNKNOWN

    def _error_result(self, msg: str) -> list[StandardResult]:
        return [
            StandardResult(
                url="",
                title=f"[Firecrawl Cloud] {msg}",
                source_name="firecrawl_cloud",
                sources={"firecrawl_cloud"},
            )
        ]

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


SourceRegistry.register(FirecrawlCloudScraper())
