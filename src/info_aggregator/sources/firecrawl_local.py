"""Firecrawl local adapter — self-hosted Docker deployment.

Firecrawl is a content extraction engine, not a search engine.
Its role in the pipeline:
1. Takes search result URLs from other sources
2. Fetches full markdown content
3. Provides full-text for authority/quality scoring
"""

from __future__ import annotations

import httpx

from ..models import AuthorityTier, ContentType, SearchQuery, StandardResult
from . import SearchSource, SourceInfo, SourceRegistry


class FirecrawlLocalScraper(SearchSource):
    """Adapter for locally deployed Firecrawl (Docker)."""

    info = SourceInfo(
        name="firecrawl_local",
        type="local",
        cost_per_call=None,  # Free — self-hosted
    )

    def __init__(self, base_url: str = "http://localhost:3002", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def search(self, query: SearchQuery) -> list[StandardResult]:
        """Scrape the given URL(s) and return extracted content.

        Tries the original query as a URL. Also accepts scrape_urls
        passed via the rewritten dict as a list of URLs to scrape.
        """
        urls_to_scrape: list[str] = []

        # If query looks like a URL, scrape it
        if query.original.startswith(("http://", "https://")):
            urls_to_scrape.append(query.original)

        # Support batch scraping via rewritten dict
        scrape_list = query.rewritten.get("scrape_urls", "")
        if scrape_list:
            if isinstance(scrape_list, str):
                urls_to_scrape.extend(
                    u.strip() for u in scrape_list.split(",") if u.strip()
                )
            elif isinstance(scrape_list, list):
                urls_to_scrape.extend(scrape_list)

        if not urls_to_scrape:
            return []

        client = await self._get_client()
        results: list[StandardResult] = []

        for url in urls_to_scrape[: query.max_results]:
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
        """Scrape a single URL and return a StandardResult."""
        try:
            response = await client.post(
                f"{self.base_url}/v1/scrape",
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
        title = metadata.get("title") or metadata.get("og:title") or url
        snippet = markdown[:500] if markdown else ""

        result = StandardResult(
            url=source_url,
            title=title,
            snippet=snippet,
            full_content=markdown if len(markdown) > 500 else None,
            source_name="firecrawl_local",
            sources={"firecrawl_local"},
            language=metadata.get("language", ""),
            content_type=ContentType.UNKNOWN,
            word_count=len(markdown.split()) if markdown else 0,
            relevance_scores={"firecrawl_local": 1.0},
            raw=data,
        )

        result.authority_tier = self._classify_authority(source_url)
        return result

    async def health(self) -> dict:
        """Check Firecrawl health."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/")
            if response.status_code == 200:
                return {"status": "ok", "remaining_quota": None}
            return {"status": "degraded", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "down", "error": str(e)}

    def _classify_authority(self, url: str) -> AuthorityTier:
        """Classify URL into authority tiers."""
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

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# Auto-register
SourceRegistry.register(FirecrawlLocalScraper())
