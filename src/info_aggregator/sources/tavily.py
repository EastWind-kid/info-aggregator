"""Tavily cloud API adapter.

Tavily is an AI-optimized search API designed for LLM agents.
Docs: https://docs.tavily.com
"""

from __future__ import annotations

import os

import httpx

from ..models import AuthorityTier, ContentType, SearchQuery, StandardResult
from . import SearchSource, SourceInfo, SourceRegistry

TAVILY_API_URL = "https://api.tavily.com/search"


class TavilySearcher(SearchSource):
    """Adapter for Tavily cloud search API."""

    info = SourceInfo(
        name="tavily",
        type="cloud",
        cost_per_call=1,  # Basic search = 1 credit
    )

    def __init__(
        self,
        api_key: str | None = None,
        timeout: int = 15,
    ):
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def search(self, query: SearchQuery) -> list[StandardResult]:
        """Search Tavily and return standardized results."""
        if not self.api_key:
            return self._error_result(
                "TAVILY_API_KEY not set. Set env var or configure in code."
            )

        client = await self._get_client()

        try:
            response = await client.post(
                TAVILY_API_URL,
                json={
                    "api_key": self.api_key,
                    "query": query.original,
                    "search_depth": "basic",
                    "include_answer": True,
                    "include_raw_content": False,
                    "max_results": min(query.max_results, 20),
                },
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            return self._error_result(f"HTTP error: {e}")
        except Exception as e:
            return self._error_result(f"Unexpected error: {e}")

        results: list[StandardResult] = []

        # Tavily's synthesized answer
        answer = data.get("answer", "")

        for item in data.get("results", []):
            url = item.get("url", "")
            result = StandardResult(
                url=url,
                title=item.get("title", ""),
                snippet=item.get("content", ""),
                source_name="tavily",
                sources={"tavily"},
                language=self._detect_language(item.get("title", "")),
                content_type=self._classify_content(item),
                relevance_scores={"tavily": item.get("score", 0.5)},
                raw=item,
            )

            # Attach Tavily's synthesized answer to the top result
            if answer and len(results) == 0:
                result.ai_summary = answer

            result.authority_tier = self._classify_authority(url)
            results.append(result)

        return results

    async def health(self) -> dict:
        """Check Tavily API health."""
        if not self.api_key:
            return {"status": "not_configured", "error": "API key not set"}

        try:
            client = await self._get_client()
            response = await client.post(
                TAVILY_API_URL,
                json={
                    "api_key": self.api_key,
                    "query": "health check",
                    "max_results": 1,
                },
            )
            if response.status_code == 200:
                return {"status": "ok", "remaining_quota": None}
            return {"status": "degraded", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "down", "error": str(e)}

    def _detect_language(self, text: str) -> str:
        for ch in text:
            if "一" <= ch <= "鿿":
                return "zh"
        return "en"

    def _classify_content(self, item: dict) -> ContentType:
        """Use Tavily's content categorization if available."""
        return ContentType.UNKNOWN

    def _classify_authority(self, url: str) -> AuthorityTier:
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
        return [
            StandardResult(
                url="",
                title=f"[Tavily] {message}",
                source_name="tavily",
                sources={"tavily"},
            )
        ]

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# Auto-register
SourceRegistry.register(TavilySearcher())
