from __future__ import annotations

import requests

from citation_pipeline.common.config import RetrievalConfig
from citation_pipeline.common.models import CandidatePaper
from citation_pipeline.common.utils import clean_text


class SemanticScholarError(Exception):
    pass


class SemanticScholarClient:
    def __init__(self, config: RetrievalConfig):
        self.config = config

    def search(self, query: str) -> list[CandidatePaper]:
        params = {
            "query": query,
            "limit": self.config.semantic_scholar_top_k,
            "fields": "title,abstract,year,url,authors,citationCount,externalIds,venue",
        }
        headers = {}
        if self.config.semantic_scholar_api_key:
            headers["x-api-key"] = self.config.semantic_scholar_api_key

        try:
            response = requests.get(
                f"{self.config.semantic_scholar_base_url}/paper/search",
                params=params,
                headers=headers,
                timeout=self.config.semantic_scholar_timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SemanticScholarError(f"Semantic Scholar 检索失败：{exc}") from exc

        payload = response.json()
        data = payload.get("data") or []
        candidates: list[CandidatePaper] = []
        for item in data:
            candidates.append(
                CandidatePaper(
                    source="semantic_scholar",
                    title=clean_text(item.get("title", "")),
                    authors=[clean_text(author.get("name", "")) for author in (item.get("authors") or []) if clean_text(author.get("name", ""))],
                    year=str(item.get("year", "") or ""),
                    abstract=clean_text(item.get("abstract", "")),
                    venue=clean_text(item.get("venue", "")),
                    doi=clean_text((item.get("externalIds") or {}).get("DOI", "")),
                    url=clean_text(item.get("url", "")),
                    citation_count=item.get("citationCount"),
                    retrieval_score=None,
                    evidence_snippet=clean_text(item.get("abstract", ""))[:280],
                    external_ids={k: str(v) for k, v in (item.get("externalIds") or {}).items() if v},
                    raw_payload=item,
                )
            )
        return candidates
