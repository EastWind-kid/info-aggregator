"""Exa cloud API adapter.

Exa is a neural/semantic search engine for AI agents.
Docs: https://docs.exa.ai
"""

from __future__ import annotations

import os

import httpx

from ..models import AuthorityTier, ContentType, SearchQuery, StandardResult
from . import SearchSource, SourceInfo, SourceRegistry

EXA_API_URL = "https://api.exa.ai/search"


class ExaSearcher(SearchSource):
    """Adapter for Exa cloud search API."""

    info = SourceInfo(
        name="exa",
        type="cloud",
        cost_per_call=1,  # Basic search = 1 credit
    )

    def __init__(
        self,
        api_key: str | None = None,
        timeout: int = 15,
    ):
        self.api_key = api_key or os.environ.get("EXA_API_KEY", "")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                },
            )
        return self._client

    async def search(self, query: SearchQuery) -> list[StandardResult]:
        """Search Exa and return standardized results."""
        if not self.api_key:
            return self._error_result(
                "EXA_API_KEY not set. Set env var or configure in code."
            )

        client = await self._get_client()

        try:
            response = await client.post(
                EXA_API_URL,
                json={
                    "query": query.original,
                    "type": "auto",
                    "numResults": min(query.max_results, 20),
                    "useAutoprompt": True,
                    "contents": {
                        "highlights": {
                            "numSentences": 3,
                            "highlightsPerUrl": 3,
                        }
                    },
                },
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            return self._error_result(f"HTTP error: {e}")
        except Exception as e:
            return self._error_result(f"Unexpected error: {e}")

        results: list[StandardResult] = []

        for item in data.get("results", []):
            url = item.get("url", "")
            highlights = item.get("highlights", [])

            # Use highlights as snippet if available (Exa's killer feature)
            snippet = ""
            if highlights:
                snippet = " | ".join(highlights)
            if not snippet:
                snippet = item.get("text", "")[:500]

            result = StandardResult(
                url=url,
                title=item.get("title", ""),
                snippet=snippet,
                source_name="exa",
                sources={"exa"},
                language=self._detect_language(item.get("title", "")),
                content_type=self._classify_content(item),
                relevance_scores={"exa": item.get("score", 0.5)},
                highlights=highlights,
                published_date=self._parse_date(item.get("publishedDate")),
                raw=item,
            )

            result.authority_tier = self._classify_authority(url)
            results.append(result)

        return results

    async def health(self) -> dict:
        """Check Exa API health."""
        if not self.api_key:
            return {"status": "not_configured", "error": "API key not set"}

        try:
            client = await self._get_client()
            response = await client.post(
                EXA_API_URL,
                json={"query": "test", "numResults": 1},
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

    def _parse_date(self, date_str: str | None) -> None:
        """Parse date string (future: return datetime)."""
        return None

    def _classify_content(self, item: dict) -> ContentType:
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
                title=f"[Exa] {message}",
                source_name="exa",
                sources={"exa"},
            )
        ]

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# Auto-register
SourceRegistry.register(ExaSearcher())
