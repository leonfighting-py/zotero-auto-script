from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from citation_pipeline.common.models import ReviewFeedbackRecord, ReviewLogRecord


class ReviewLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: ReviewLogRecord) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "segment_id": record.segment_id,
            "claim_text": record.claim_text,
            "query_bundle": record.query_bundle,
            "candidate_count": record.candidate_count,
            "verified_candidates": record.verified_candidates,
            "metadata": record.metadata,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class ReviewFeedbackLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: ReviewFeedbackRecord) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "segment_id": record.segment_id,
            "claim_text": record.claim_text,
            "selected_rank": record.selected_rank,
            "selected_doi": record.selected_doi,
            "selected_title": record.selected_title,
            "action": record.action,
            "notes": record.notes,
            "metadata": record.metadata,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
