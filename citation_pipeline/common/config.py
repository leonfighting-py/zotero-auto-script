from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class VerificationConfig:
    zotero_library_id: str
    zotero_library_type: str
    zotero_api_key: str
    crossref_mailto: str
    crossref_score_threshold: float
    input_bib: Path
    collection_prefix: str
    request_delay_seconds: float


@dataclass
class RetrievalConfig:
    semantic_scholar_api_key: str = ""
    semantic_scholar_base_url: str = "https://api.semanticscholar.org/graph/v1"
    semantic_scholar_timeout_seconds: float = 20.0
    semantic_scholar_top_k: int = 5
    review_log_path: Path = Path("review_logs/full_pipeline_reviews.jsonl")
    review_feedback_path: Path = Path("review_logs/full_pipeline_feedback.jsonl")
    claims_input_path: Path = Path("claims.txt")


@dataclass
class RuntimeConfig:
    verification: VerificationConfig
    retrieval: RetrievalConfig
    enable_retrieval: bool = False
    enable_auto_insert: bool = False
