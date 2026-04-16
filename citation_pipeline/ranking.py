from __future__ import annotations

import re
from datetime import datetime

from citation_pipeline.common.models import RetrievalQuery, VerifiedReference


class CandidateRanker:
    def rank(self, query: RetrievalQuery, candidates: list[VerifiedReference]) -> list[VerifiedReference]:
        current_year = datetime.now().year
        query_terms = self._query_terms(query)

        for candidate in candidates:
            title = (candidate.crossref_title or candidate.title).lower()
            matched_terms = sorted(term for term in query_terms if term in title)
            candidate.matched_terms = matched_terms

            title_match = min(len(matched_terms) / max(len(query_terms), 1), 1.0)
            verification_score = 1.0 if not candidate.needs_review else 0.25
            crossref_score = min((candidate.match_score or 0.0) / 100.0, 1.0)
            citation_prior = min((candidate.citation_count or 0) / 500.0, 1.0)
            recency = self._recency_score(candidate.crossref_year or candidate.year, current_year)

            final_score = (
                0.35 * verification_score
                + 0.25 * crossref_score
                + 0.20 * title_match
                + 0.10 * citation_prior
                + 0.10 * recency
            )
            candidate.ranking_score = round(final_score, 4)
            candidate.recommendation_label = self._label_for(candidate)

        return sorted(candidates, key=lambda item: item.ranking_score, reverse=True)

    def _query_terms(self, query: RetrievalQuery) -> set[str]:
        terms = set()
        for value in query.all_queries:
            for token in re.findall(r"[a-zA-Z][a-zA-Z\-]+", value.lower()):
                if len(token) >= 4:
                    terms.add(token)
        return terms

    def _recency_score(self, year_value: str, current_year: int) -> float:
        try:
            year = int(year_value)
        except (TypeError, ValueError):
            return 0.0
        age = max(current_year - year, 0)
        if age <= 2:
            return 1.0
        if age <= 5:
            return 0.8
        if age <= 10:
            return 0.5
        return 0.2

    def _label_for(self, candidate: VerifiedReference) -> str:
        if not candidate.needs_review and candidate.ranking_score >= 0.75:
            return "recommended"
        if candidate.ranking_score >= 0.5:
            return "consider"
        return "needs_review"
