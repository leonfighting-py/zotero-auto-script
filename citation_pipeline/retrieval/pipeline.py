from __future__ import annotations

from citation_pipeline.common.models import RetrievalQuery, RetrievalResult
from citation_pipeline.retrieval.semantic_scholar import SemanticScholarClient


class RetrievalPipeline:
    def __init__(self, semantic_scholar: SemanticScholarClient):
        self.semantic_scholar = semantic_scholar

    def run(self, query: RetrievalQuery) -> RetrievalResult:
        merged_candidates = []
        seen_keys: set[tuple[str, str]] = set()
        for query_text in query.all_queries:
            for candidate in self.semantic_scholar.search(query_text):
                dedup_key = (candidate.doi.lower(), candidate.title.lower()) if candidate.doi else ("", candidate.title.lower())
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
                merged_candidates.append(candidate)
        return RetrievalResult(segment_id=query.segment_id, query_text=query.text, candidates=merged_candidates)
