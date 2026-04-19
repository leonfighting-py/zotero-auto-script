from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Iterator


WorkflowEvent = dict[str, Any]


def _build_initial_row(idx: int, parsed: Any, slots: Any) -> dict[str, str]:
    return {
        "idx": str(idx),
        "stage": "lookup",
        "status": "processing",
        "parsed_author": getattr(parsed, "author", ""),
        "parsed_title": getattr(parsed, "title", ""),
        "parsed_journal": getattr(parsed, "journal", ""),
        "parsed_year": getattr(parsed, "year", ""),
        "parsed_volume": getattr(parsed, "volume", ""),
        "search_query": getattr(slots, "query", ""),
        "doi": "",
        "score": "",
        "zotero_item_key": "",
        "error": "",
    }


def _enrich_slots_with_crossref(row: dict[str, str], result: Any) -> None:
    # 若用户只输入了标题等局部信息，用 Crossref 命中结果回填缺失槽位。
    if not row["parsed_author"]:
        row["parsed_author"] = getattr(result, "authors", "") or row["parsed_author"]
    if not row["parsed_title"]:
        row["parsed_title"] = getattr(result, "title", "") or row["parsed_title"]
    if not row["parsed_journal"]:
        row["parsed_journal"] = getattr(result, "journal", "") or row["parsed_journal"]
    if not row["parsed_year"]:
        row["parsed_year"] = getattr(result, "year", "") or row["parsed_year"]
    if not row["parsed_volume"]:
        row["parsed_volume"] = getattr(result, "volume", "") or row["parsed_volume"]


def _make_parsed_from_row(row: dict[str, str]) -> Any:
    return type(
        "ParsedObj",
        (),
        {
            "author": row["parsed_author"],
            "title": row["parsed_title"],
            "journal": row["parsed_journal"],
            "year": row["parsed_year"],
            "volume": row["parsed_volume"],
        },
    )()


def _make_lookup_from_row(row: dict[str, str]) -> Any:
    return type(
        "LookupObj",
        (),
        {
            "doi": row["doi"],
            "score": float(row["score"]) if row["score"] else None,
            "title": row["parsed_title"],
            "authors": row["parsed_author"],
            "journal": row["parsed_journal"],
            "year": row["parsed_year"],
            "volume": row["parsed_volume"],
        },
    )()


def import_rows_with_events(
    rows: list[dict[str, str]],
    importer: Any = None,
    collection_key: str = "",
    skip_existing_doi: bool = True,
) -> Iterator[WorkflowEvent]:
    targets = [row for row in rows if row.get("doi")]
    summary_delta = {"imported": 0, "skipped_duplicate": 0, "failed": 0}
    total = len(targets)
    yield {"event": "step_started", "step": "zotero", "current": 0, "total": total}
    if importer is None:
        for row in targets:
            row["stage"] = "zotero_done"
            row["status"] = "zotero_failed"
            row["error"] = "未初始化 Zotero 导入器"
            summary_delta["failed"] += 1
        yield {"event": "step_completed", "step": "zotero", "current": 0, "total": total}
        yield {"event": "step_final", "step": "zotero", "rows": rows, "summary_delta": summary_delta}
        return

    for order, row in enumerate(targets, start=1):
        try:
            import_status, item_key = importer.import_doi_entry(
                order=int(row["idx"]),
                parsed=_make_parsed_from_row(row),
                doi_result=_make_lookup_from_row(row),
                collection_key=collection_key,
                skip_existing=skip_existing_doi,
            )
            row["stage"] = "zotero_done"
            row["zotero_item_key"] = item_key
            if import_status == "imported":
                row["status"] = "zotero_imported"
                summary_delta["imported"] += 1
            elif import_status == "skipped_duplicate":
                row["status"] = "zotero_skipped_duplicate"
                summary_delta["skipped_duplicate"] += 1
            else:
                row["status"] = "zotero_failed"
                row["error"] = f"未知导入状态: {import_status}"
                summary_delta["failed"] += 1
        except Exception as exc:  # noqa: BLE001
            row["stage"] = "zotero_done"
            row["status"] = "zotero_failed"
            row["error"] = str(exc)
            summary_delta["failed"] += 1
        yield {"event": "progress", "step": "zotero", "current": order, "total": total, "row": deepcopy(row)}

    yield {"event": "step_completed", "step": "zotero", "current": total, "total": total}
    yield {"event": "step_final", "step": "zotero", "rows": rows, "summary_delta": summary_delta}


def process_citations_with_events(
    citations: list[str],
    lookup_doi_func: Callable[..., Any],
    parse_citation_func: Callable[[str], Any],
    build_slots_func: Callable[[Any], Any],
    importer: Any = None,
    collection_key: str = "",
    enable_zotero_import: bool = False,
    skip_existing_doi: bool = True,
) -> Iterator[WorkflowEvent]:
    rows: list[dict[str, str]] = []
    summary = {
        "total": len(citations),
        "doi_found": 0,
        "doi_not_found": 0,
        "imported": 0,
        "skipped_duplicate": 0,
        "failed": 0,
    }
    total = len(citations)

    yield {"event": "step_started", "step": "crossref", "current": 0, "total": total}
    for idx, citation in enumerate(citations, start=1):
        parsed = parse_citation_func(citation)
        slots = build_slots_func(parsed)
        row = _build_initial_row(idx=idx, parsed=parsed, slots=slots)
        try:
            result = lookup_doi_func(
                author=getattr(slots, "author", ""),
                title=getattr(slots, "title", ""),
                journal=getattr(slots, "journal", ""),
                year=getattr(slots, "year", ""),
                volume=getattr(slots, "volume", ""),
            )
            doi = (getattr(result, "doi", "") or "").strip()
            row["doi"] = doi
            row["score"] = f"{getattr(result, 'score', '')}" if getattr(result, "score", None) is not None else ""
            _enrich_slots_with_crossref(row, result)
            row["stage"] = "lookup_done"
            if doi:
                row["status"] = "doi_found"
                summary["doi_found"] += 1
            else:
                row["status"] = "doi_not_found"
                row["error"] = getattr(result, "warning", "") or "未找到 DOI"
                summary["doi_not_found"] += 1
        except Exception as exc:  # noqa: BLE001
            row["stage"] = "failed"
            row["status"] = "error"
            row["error"] = str(exc)
            summary["failed"] += 1

        rows.append(row)
        yield {
            "event": "progress",
            "step": "crossref",
            "current": idx,
            "total": total,
            "row": deepcopy(row),
        }

    yield {
        "event": "step_completed",
        "step": "crossref",
        "current": total,
        "total": total,
    }

    if not enable_zotero_import:
        yield {"event": "final", "rows": rows, "summary": summary}
        return

    for event in import_rows_with_events(
        rows=rows,
        importer=importer,
        collection_key=collection_key,
        skip_existing_doi=skip_existing_doi,
    ):
        if event["event"] == "step_final":
            delta = event["summary_delta"]
            summary["imported"] += delta["imported"]
            summary["skipped_duplicate"] += delta["skipped_duplicate"]
            summary["failed"] += delta["failed"]
            continue
        yield event

    yield {"event": "final", "rows": rows, "summary": summary}


def process_citations(
    citations: list[str],
    lookup_doi_func: Callable[..., Any],
    parse_citation_func: Callable[[str], Any],
    build_slots_func: Callable[[Any], Any],
    importer: Any = None,
    collection_key: str = "",
    enable_zotero_import: bool = False,
    skip_existing_doi: bool = True,
    progress_callback: Callable[[int, int, dict[str, str]], None] | None = None,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    rows: list[dict[str, str]] = []
    summary: dict[str, int] = {}
    for event in process_citations_with_events(
        citations=citations,
        lookup_doi_func=lookup_doi_func,
        parse_citation_func=parse_citation_func,
        build_slots_func=build_slots_func,
        importer=importer,
        collection_key=collection_key,
        enable_zotero_import=enable_zotero_import,
        skip_existing_doi=skip_existing_doi,
    ):
        if event["event"] == "progress" and progress_callback:
            progress_callback(event["current"], event["total"], deepcopy(event["row"]))
        if event["event"] == "final":
            rows = event["rows"]
            summary = event["summary"]
    return rows, summary
