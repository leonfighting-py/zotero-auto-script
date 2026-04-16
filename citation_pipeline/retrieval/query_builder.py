from __future__ import annotations

import re

from citation_pipeline.common.models import RetrievalQuery


class QueryBuilder:
    """Rule-based first version for claim normalization and lightweight rewrites."""

    STOPWORDS = {
        "the", "a", "an", "and", "or", "but", "that", "this", "these", "those",
        "is", "are", "was", "were", "be", "been", "being", "of", "to", "for", "in",
        "on", "with", "by", "under", "from", "as", "at", "into", "their", "our",
        "recent", "recently", "methods", "method", "study", "studies", "approach", "approaches",
    }

    def build(self, segment_id: str, text: str) -> RetrievalQuery:
        normalized = " ".join(text.split())
        tokens = re.findall(r"[A-Za-z][A-Za-z\-]+", normalized.lower())
        keywords = [token for token in tokens if token not in self.STOPWORDS]

        rewrites: list[str] = []
        if keywords:
            rewrites.append(" ".join(keywords[:8]))
        if len(keywords) >= 4:
            rewrites.append(" ".join(keywords[:4]))

        field_hints = []
        keyword_set = set(keywords)
        if {"learning", "neural", "model", "prediction", "gnn"} & keyword_set:
            field_hints.append("Computer Science")
        if {"polymer", "copolymer", "materials", "material", "property"} & keyword_set:
            field_hints.append("Materials Science")

        return RetrievalQuery(segment_id=segment_id, text=normalized, rewrites=rewrites, field_hints=field_hints)
