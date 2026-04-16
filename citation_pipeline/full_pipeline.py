from __future__ import annotations

from dataclasses import dataclass

from citation_pipeline.common.models import CandidatePaper, RetrievalQuery, RetrievalResult, ReviewLogRecord, VerifiedReference
from citation_pipeline.ranking import CandidateRanker
from citation_pipeline.retrieval.pipeline import RetrievalPipeline
from citation_pipeline.retrieval.query_builder import QueryBuilder
from citation_pipeline.review_logger import ReviewLogger
from citation_pipeline.verification.crossref_verifier import CrossrefVerifier


@dataclass
class FullPipelineResult:
    query: RetrievalQuery
    retrieval: RetrievalResult
    verified_candidates: list[VerifiedReference]
    mode: str = "shadow"


class FullCitationPipeline:
    def __init__(
        self,
        query_builder: QueryBuilder,
        retrieval_pipeline: RetrievalPipeline,
        verifier: CrossrefVerifier,
        review_logger: ReviewLogger | None = None,
        ranker: CandidateRanker | None = None,
    ):
        self.query_builder = query_builder
        self.retrieval_pipeline = retrieval_pipeline
        self.verifier = verifier
        self.review_logger = review_logger
        self.ranker = ranker or CandidateRanker()

    def run_claim(self, segment_id: str, text: str) -> FullPipelineResult:
        query = self.query_builder.build(segment_id=segment_id, text=text)
        retrieval = self.retrieval_pipeline.run(query)
        verified_candidates = self.verify_candidates(retrieval.candidates)
        ranked_candidates = self.ranker.rank(query, verified_candidates)
        result = FullPipelineResult(query=query, retrieval=retrieval, verified_candidates=ranked_candidates)
        if self.review_logger is not None:
            self.review_logger.append(
                ReviewLogRecord(
                    segment_id=segment_id,
                    claim_text=text,
                    query_bundle=query.all_queries,
                    candidate_count=len(retrieval.candidates),
                    verified_candidates=[candidate.to_dict() for candidate in ranked_candidates],
                    metadata={"mode": result.mode},
                )
            )
        return result

    def verify_candidates(self, candidates: list[CandidatePaper]) -> list[VerifiedReference]:
        normalized = [self.normalize_candidate(candidate, order=index) for index, candidate in enumerate(candidates, start=1)]
        if not normalized:
            return []
        return self.verifier.verify_all(normalized)

    def normalize_candidate(self, candidate: CandidatePaper, order: int = 1) -> VerifiedReference:
        author_raw = " and ".join(candidate.authors)
        return VerifiedReference(
            order=order,
            entry_key=f"candidate_{order}",
            title=candidate.title,
            author=candidate.authors[0] if candidate.authors else "",
            author_raw=author_raw,
            year=candidate.year,
            journal=candidate.venue,
            doi=candidate.doi,
            source=candidate.source,
            url=candidate.url,
            citation_count=candidate.citation_count,
            retrieval_score=candidate.retrieval_score,
            evidence_snippet=candidate.evidence_snippet,
        )
