from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from citation_pipeline.claims_loader import load_claims_from_file
from citation_pipeline.common.config import RetrievalConfig, VerificationConfig
from citation_pipeline.common.models import ReviewFeedbackRecord
from citation_pipeline.exporters.zotero_importer import ZoteroImporter, ZoteroImportError
from citation_pipeline.full_pipeline import FullCitationPipeline
from citation_pipeline.retrieval import QueryBuilder, RetrievalPipeline, SemanticScholarClient
from citation_pipeline.review_logger import ReviewFeedbackLogger, ReviewLogger
from citation_pipeline.verification.bibtex_parser import VerificationError
from citation_pipeline.verification.crossref_verifier import CrossrefVerifier
from citation_pipeline.verification.pipeline import VerificationPipeline


class FatalError(Exception):
    pass


def load_verification_config() -> VerificationConfig:
    load_dotenv()
    library_id = os.getenv("ZOTERO_LIBRARY_ID", "").strip()
    library_type = os.getenv("ZOTERO_LIBRARY_TYPE", "user").strip() or "user"
    api_key = os.getenv("ZOTERO_API_KEY", "").strip()
    crossref_mailto = os.getenv("CROSSREF_MAILTO", "").strip()
    threshold_raw = os.getenv("CROSSREF_SCORE_THRESHOLD", "30").strip() or "30"
    input_bib = os.getenv("INPUT_BIB", "reference.bib").strip() or "reference.bib"
    prefix = os.getenv("COLLECTION_PREFIX", "Paper_Refs").strip() or "Paper_Refs"
    delay_raw = os.getenv("REQUEST_DELAY_SECONDS", "0.3").strip() or "0.3"

    missing: list[str] = []
    if not library_id:
        missing.append("ZOTERO_LIBRARY_ID")
    if not api_key:
        missing.append("ZOTERO_API_KEY")
    if not crossref_mailto:
        missing.append("CROSSREF_MAILTO")
    if missing:
        raise FatalError(f"缺少必要配置：{', '.join(missing)}。请先填写 .env 文件。")
    if library_type not in {"user", "group"}:
        raise FatalError("ZOTERO_LIBRARY_TYPE 只能是 user 或 group。")

    try:
        threshold = float(threshold_raw)
        delay = float(delay_raw)
    except ValueError as exc:
        raise FatalError("CROSSREF_SCORE_THRESHOLD / REQUEST_DELAY_SECONDS 必须是数字。") from exc

    return VerificationConfig(
        zotero_library_id=library_id,
        zotero_library_type=library_type,
        zotero_api_key=api_key,
        crossref_mailto=crossref_mailto,
        crossref_score_threshold=threshold,
        input_bib=Path(input_bib),
        collection_prefix=prefix,
        request_delay_seconds=delay,
    )


def load_retrieval_config() -> RetrievalConfig:
    load_dotenv()
    timeout_raw = os.getenv("SEMANTIC_SCHOLAR_TIMEOUT_SECONDS", "20").strip() or "20"
    top_k_raw = os.getenv("SEMANTIC_SCHOLAR_TOP_K", "5").strip() or "5"
    log_path = os.getenv("REVIEW_LOG_PATH", "review_logs/full_pipeline_reviews.jsonl").strip() or "review_logs/full_pipeline_reviews.jsonl"
    feedback_path = os.getenv("REVIEW_FEEDBACK_PATH", "review_logs/full_pipeline_feedback.jsonl").strip() or "review_logs/full_pipeline_feedback.jsonl"
    claims_input_path = os.getenv("CLAIMS_INPUT_PATH", "claims.txt").strip() or "claims.txt"

    try:
        timeout = float(timeout_raw)
        top_k = int(top_k_raw)
    except ValueError as exc:
        raise FatalError("SEMANTIC_SCHOLAR_TIMEOUT_SECONDS / SEMANTIC_SCHOLAR_TOP_K 格式错误。") from exc

    return RetrievalConfig(
        semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip(),
        semantic_scholar_base_url=os.getenv("SEMANTIC_SCHOLAR_BASE_URL", "https://api.semanticscholar.org/graph/v1").strip() or "https://api.semanticscholar.org/graph/v1",
        semantic_scholar_timeout_seconds=timeout,
        semantic_scholar_top_k=top_k,
        review_log_path=Path(log_path),
        review_feedback_path=Path(feedback_path),
        claims_input_path=Path(claims_input_path),
    )


def print_summary(collection_name: str, stats: dict[str, int], failures: list[str]) -> None:
    print()
    print("[信息] 导入汇总")
    print(f"- Collection：{collection_name}")
    print(f"- 总文献数：{stats['total']}")
    print(f"- 正常导入数：{stats['normal']}")
    print(f"- 降级导入数：{stats['review']}")
    print(f"- 完全失败数：{stats['failed']}")
    if failures:
        print("- 失败明细：")
        for item in failures:
            print(f"  - {item}")
    print()
    print("请在 Zotero 中打开本次新建的 Collection，并搜索 `⚠️` 快速定位需手动检查的条目。")


def run_verification_mode() -> int:
    config = load_verification_config()
    pipeline = VerificationPipeline(config)
    importer = ZoteroImporter(config)

    print("[信息] 阶段 1/3：读取输入文件")
    print("[信息] 阶段 2/3：校验与纠偏")
    references = pipeline.run()
    print("[信息] 阶段 3/3：导入 Zotero")
    collection_name, stats, failures = importer.import_references(references)
    print_summary(collection_name, stats, failures)
    return 0


def build_full_pipeline(verification_config: VerificationConfig, retrieval_config: RetrievalConfig) -> FullCitationPipeline:
    query_builder = QueryBuilder()
    retrieval_pipeline = RetrievalPipeline(SemanticScholarClient(retrieval_config))
    verifier = CrossrefVerifier(verification_config)
    review_logger = ReviewLogger(retrieval_config.review_log_path)
    return FullCitationPipeline(query_builder, retrieval_pipeline, verifier, review_logger)


def print_candidate_block(segment_id: str, claim_text: str, candidates) -> None:
    print()
    print(f"[信息] Segment: {segment_id}")
    print(f"[信息] Claim: {claim_text}")
    for index, candidate in enumerate(candidates[:5], start=1):
        title = candidate.crossref_title or candidate.title
        doi = candidate.corrected_doi or candidate.doi or "[no doi]"
        status = candidate.recommendation_label
        score = f"{candidate.ranking_score:.3f}"
        matched = ", ".join(candidate.matched_terms[:5]) or "-"
        print(f"{index}. [{status}] score={score} | {title} | {candidate.crossref_year or candidate.year} | {doi} | matched: {matched}")


def append_feedback_if_present(feedback_logger: ReviewFeedbackLogger, segment_id: str, claim_text: str) -> None:
    selected_rank_raw = os.getenv("SELECTED_RANK", "").strip()
    selected_doi = os.getenv("SELECTED_DOI", "").strip()
    selected_title = os.getenv("SELECTED_TITLE", "").strip()
    action = os.getenv("REVIEW_ACTION", "").strip()
    notes = os.getenv("REVIEW_NOTES", "").strip()

    if not any([selected_rank_raw, selected_doi, selected_title, action, notes]):
        return

    selected_rank = int(selected_rank_raw) if selected_rank_raw else None
    feedback_logger.append(
        ReviewFeedbackRecord(
            segment_id=segment_id,
            claim_text=claim_text,
            selected_rank=selected_rank,
            selected_doi=selected_doi,
            selected_title=selected_title,
            action=action or "recorded",
            notes=notes,
        )
    )


def run_full_mode() -> int:
    verification_config = load_verification_config()
    retrieval_config = load_retrieval_config()
    claim_text = os.getenv("CLAIM_TEXT", "").strip()
    segment_id = os.getenv("SEGMENT_ID", "segment_001").strip() or "segment_001"
    claims_file = os.getenv("CLAIMS_INPUT_PATH", "").strip()

    pipeline = build_full_pipeline(verification_config, retrieval_config)
    feedback_logger = ReviewFeedbackLogger(retrieval_config.review_feedback_path)

    claims: list[tuple[str, str]] = []
    if claims_file:
        claims = load_claims_from_file(Path(claims_file))
    elif claim_text:
        claims = [(segment_id, claim_text)]
    else:
        raise FatalError("full 模式下必须提供 CLAIM_TEXT 或 CLAIMS_INPUT_PATH。")

    print("[信息] 运行 full citation pipeline（批量版）")
    for current_segment_id, current_claim_text in claims:
        result = pipeline.run_claim(segment_id=current_segment_id, text=current_claim_text)
        print(f"[信息] Query bundle: {result.query.all_queries}")
        print(f"[信息] 检索候选数：{len(result.retrieval.candidates)}")
        print(f"[信息] 已校验候选数：{len(result.verified_candidates)}")
        print_candidate_block(current_segment_id, current_claim_text, result.verified_candidates)
        append_feedback_if_present(feedback_logger, current_segment_id, current_claim_text)

    print(f"\n[信息] review log 已写入：{retrieval_config.review_log_path}")
    print(f"[信息] feedback log 已写入：{retrieval_config.review_feedback_path}")
    return 0


def main() -> int:
    try:
        mode = os.getenv("PIPELINE_MODE", "verification").strip().lower() or "verification"
        if mode == "verification":
            return run_verification_mode()
        if mode == "full":
            return run_full_mode()
        raise FatalError("PIPELINE_MODE 只能是 verification 或 full。")
    except (FatalError, VerificationError, ZoteroImportError, FileNotFoundError, ValueError) as exc:
        print(f"[错误] {exc}")
        return 1
    except KeyboardInterrupt:
        print("[错误] 用户中断执行。")
        return 130
    except Exception as exc:
        print(f"[错误] 未预期异常：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
