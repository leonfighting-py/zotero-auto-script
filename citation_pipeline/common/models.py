from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CandidatePaper:
    source: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    abstract: str = ""
    venue: str = ""
    doi: str = ""
    url: str = ""
    citation_count: int | None = None
    retrieval_score: float | None = None
    evidence_snippet: str = ""
    external_ids: dict[str, str] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalQuery:
    segment_id: str
    text: str
    rewrites: list[str] = field(default_factory=list)
    field_hints: list[str] = field(default_factory=list)

    @property
    def all_queries(self) -> list[str]:
        values = [self.text, *self.rewrites]
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(normalized)
        return deduped


@dataclass
class RetrievalResult:
    segment_id: str
    query_text: str
    candidates: list[CandidatePaper] = field(default_factory=list)


@dataclass
class VerifiedReference:
    order: int
    entry_key: str
    title: str
    author: str
    author_raw: str
    year: str
    journal: str
    doi: str
    corrected_doi: str = ""
    needs_review: bool = False
    match_score: float | None = None
    crossref_title: str = ""
    crossref_authors: list[dict[str, str]] = field(default_factory=list)
    crossref_journal: str = ""
    crossref_year: str = ""
    source: str = ""
    url: str = ""
    citation_count: int | None = None
    retrieval_score: float | None = None
    evidence_snippet: str = ""
    ranking_score: float = 0.0
    recommendation_label: str = "needs_review"
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "entry_key": self.entry_key,
            "title": self.title,
            "author": self.author,
            "author_raw": self.author_raw,
            "year": self.year,
            "journal": self.journal,
            "doi": self.doi,
            "corrected_doi": self.corrected_doi,
            "needs_review": self.needs_review,
            "match_score": self.match_score,
            "crossref_title": self.crossref_title,
            "crossref_authors": self.crossref_authors,
            "crossref_journal": self.crossref_journal,
            "crossref_year": self.crossref_year,
            "source": self.source,
            "url": self.url,
            "citation_count": self.citation_count,
            "retrieval_score": self.retrieval_score,
            "evidence_snippet": self.evidence_snippet,
            "ranking_score": self.ranking_score,
            "recommendation_label": self.recommendation_label,
            "matched_terms": self.matched_terms,
        }


@dataclass
class ReviewLogRecord:
    segment_id: str
    claim_text: str
    query_bundle: list[str]
    candidate_count: int
    verified_candidates: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewFeedbackRecord:
    segment_id: str
    claim_text: str
    selected_rank: int | None = None
    selected_doi: str = ""
    selected_title: str = ""
    action: str = "pending"
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
