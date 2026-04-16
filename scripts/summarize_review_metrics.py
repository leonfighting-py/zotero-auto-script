from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

REVIEW_LOG = Path("review_logs/full_pipeline_reviews.jsonl")
FEEDBACK_LOG = Path("review_logs/full_pipeline_feedback.jsonl")
ACCEPT_ACTIONS = {"accepted", "recorded", "selected"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def build_review_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in records:
        segment_id = str(record.get("segment_id") or "")
        if segment_id:
            index[segment_id] = record
    return index


def summarize(review_records: list[dict[str, Any]], feedback_records: list[dict[str, Any]]) -> dict[str, Any]:
    review_index = build_review_index(review_records)
    recommendation_counter: Counter[str] = Counter()
    total_candidates = 0
    total_verified = 0
    empty_results = 0
    top1_recommended = 0
    top1_verified = 0

    for record in review_records:
        candidates = record.get("verified_candidates") or []
        if not candidates:
            empty_results += 1
        total_candidates += len(candidates)
        if candidates:
            first_candidate = candidates[0]
            if str(first_candidate.get("recommendation_label") or "") == "recommended":
                top1_recommended += 1
            if not first_candidate.get("needs_review", True):
                top1_verified += 1
        for candidate in candidates:
            if not candidate.get("needs_review", True):
                total_verified += 1
            recommendation_counter[str(candidate.get("recommendation_label") or "unknown")] += 1

    accepted = 0
    top1_accepted = 0
    manual_override = 0
    rank_counter: Counter[str] = Counter()
    action_counter: Counter[str] = Counter()
    accepted_label_counter: Counter[str] = Counter()

    for feedback in feedback_records:
        action = str(feedback.get("action") or "unknown")
        action_counter[action] += 1
        segment_id = str(feedback.get("segment_id") or "")
        selected_rank = feedback.get("selected_rank")
        review_record = review_index.get(segment_id)
        candidates = (review_record or {}).get("verified_candidates") or []

        if action in ACCEPT_ACTIONS:
            accepted += 1
            if selected_rank == 1:
                top1_accepted += 1
            if selected_rank is not None and selected_rank > 1:
                manual_override += 1
            if isinstance(selected_rank, int) and 1 <= selected_rank <= len(candidates):
                selected_candidate = candidates[selected_rank - 1]
                accepted_label_counter[str(selected_candidate.get("recommendation_label") or "unknown")] += 1
                if selected_rank > 1:
                    top_candidate = candidates[0] if candidates else None
                    if top_candidate and top_candidate.get("recommendation_label") == "recommended":
                        manual_override += 0
        if selected_rank is not None:
            rank_counter[str(selected_rank)] += 1

    reviewed_segments = len(review_records)
    feedback_segments = len(feedback_records)

    recommended_total = recommendation_counter.get("recommended", 0)
    consider_total = recommendation_counter.get("consider", 0)
    recommended_accepted = accepted_label_counter.get("recommended", 0)
    consider_accepted = accepted_label_counter.get("consider", 0)

    return {
        "reviewed_segments": reviewed_segments,
        "feedback_segments": feedback_segments,
        "empty_result_rate": round(empty_results / reviewed_segments, 4) if reviewed_segments else 0.0,
        "avg_candidates_per_segment": round(total_candidates / reviewed_segments, 4) if reviewed_segments else 0.0,
        "verification_pass_rate": round(total_verified / total_candidates, 4) if total_candidates else 0.0,
        "top1_verified_rate": round(top1_verified / reviewed_segments, 4) if reviewed_segments else 0.0,
        "top1_recommended_rate": round(top1_recommended / reviewed_segments, 4) if reviewed_segments else 0.0,
        "recommendation_distribution": dict(recommendation_counter),
        "feedback_action_distribution": dict(action_counter),
        "candidate_acceptance_rate": round(accepted / feedback_segments, 4) if feedback_segments else 0.0,
        "top1_acceptance_rate": round(top1_accepted / feedback_segments, 4) if feedback_segments else 0.0,
        "manual_override_rate": round(manual_override / feedback_segments, 4) if feedback_segments else 0.0,
        "recommended_acceptance_rate": round(recommended_accepted / recommended_total, 4) if recommended_total else 0.0,
        "consider_acceptance_rate": round(consider_accepted / consider_total, 4) if consider_total else 0.0,
        "accepted_label_distribution": dict(accepted_label_counter),
        "selected_rank_distribution": dict(rank_counter),
    }


def print_summary(summary: dict[str, Any]) -> None:
    print("Online review metrics")
    print(f"- reviewed_segments: {summary['reviewed_segments']}")
    print(f"- feedback_segments: {summary['feedback_segments']}")
    print(f"- empty_result_rate: {summary['empty_result_rate']}")
    print(f"- avg_candidates_per_segment: {summary['avg_candidates_per_segment']}")
    print(f"- verification_pass_rate: {summary['verification_pass_rate']}")
    print(f"- top1_verified_rate: {summary['top1_verified_rate']}")
    print(f"- top1_recommended_rate: {summary['top1_recommended_rate']}")
    print(f"- candidate_acceptance_rate: {summary['candidate_acceptance_rate']}")
    print(f"- top1_acceptance_rate: {summary['top1_acceptance_rate']}")
    print(f"- manual_override_rate: {summary['manual_override_rate']}")
    print(f"- recommended_acceptance_rate: {summary['recommended_acceptance_rate']}")
    print(f"- consider_acceptance_rate: {summary['consider_acceptance_rate']}")
    print("- recommendation_distribution:")
    for key, value in summary["recommendation_distribution"].items():
        print(f"  - {key}: {value}")
    print("- accepted_label_distribution:")
    for key, value in summary["accepted_label_distribution"].items():
        print(f"  - {key}: {value}")
    print("- feedback_action_distribution:")
    for key, value in summary["feedback_action_distribution"].items():
        print(f"  - {key}: {value}")
    print("- selected_rank_distribution:")
    for key, value in summary["selected_rank_distribution"].items():
        print(f"  - rank {key}: {value}")


if __name__ == "__main__":
    reviews = load_jsonl(REVIEW_LOG)
    feedback = load_jsonl(FEEDBACK_LOG)
    print_summary(summarize(reviews, feedback))
