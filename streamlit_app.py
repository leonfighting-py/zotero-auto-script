from __future__ import annotations

import copy
from datetime import datetime
import hmac
import os
from pathlib import Path
import re
import time

import streamlit as st
from dotenv import load_dotenv

from citation_pipeline.common.config import VerificationConfig
from citation_pipeline.doi_lookup import (
    build_crossref_search_slots,
    lookup_doi,
    parse_citation_text,
)
from citation_pipeline.crossref_query_pipeline import (
    build_retrieval_plans,
    execute_retrieval_plan,
    finalize_ranked_hits,
)
from citation_pipeline.exporters.zotero_importer import ZoteroImportError, ZoteroImporter

QUERY_HITS_PREVIEW = 10


def _init_env() -> None:
    # 允许从项目根目录或上层目录加载 .env
    load_dotenv()
    load_dotenv(dotenv_path=".env", override=True)


def _require_access_password() -> None:
    """
    Optional lightweight access control for temporary public deployment.
    If APP_ACCESS_PASSWORD is set, visitors must enter password first.
    """
    expected = os.getenv("APP_ACCESS_PASSWORD", "").strip()
    if not expected:
        return

    if st.session_state.get("access_granted", False):
        return

    st.title("文献检索与 Zotero 工具")
    st.warning("当前页面已开启访问密码，请先输入密码。")
    with st.form("access_login_form", clear_on_submit=False):
        provided = st.text_input("访问密码", type="password")
        submitted = st.form_submit_button("进入系统")

    if submitted:
        if hmac.compare_digest(provided, expected):
            st.session_state["access_granted"] = True
            st.rerun()
        st.error("密码错误，请重试。")

    st.stop()


def _split_citations(raw_text: str) -> list[str]:
    text = (raw_text or "").strip()
    if not text:
        return []
    if re.search(r"(?m)^\s*@\w+\s*\{", text):
        return _split_bib_entries(text)
    # 优先按 [数字] 这种参考文献序号切分，兼容多行粘贴
    if re.search(r"\[\s*\d+\s*\]", text):
        chunks = re.split(r"(?=\[\s*\d+\s*\])", text)
        return [re.sub(r"\s+", " ", item).strip() for item in chunks if item.strip()]
    # 无编号时按换行切分
    return [item.strip() for item in text.splitlines() if item.strip()]


def _split_bib_entries(text: str) -> list[str]:
    entries: list[str] = []
    length = len(text)
    i = 0
    while i < length:
        if text[i] != "@":
            i += 1
            continue
        start = i
        open_brace_idx = text.find("{", i)
        if open_brace_idx == -1:
            break
        depth = 0
        j = open_brace_idx
        while j < length:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    entry = text[start : j + 1].strip()
                    if entry:
                        entries.append(entry)
                    i = j + 1
                    break
            j += 1
        else:
            break
    if entries:
        return entries
    return [item.strip() for item in text.splitlines() if item.strip()]


def _reset_pipeline_state() -> None:
    st.session_state["pipeline_citations"] = []
    st.session_state["pipeline_rows"] = []
    st.session_state["pipeline_summary"] = {
        "total": 0,
        "doi_found": 0,
        "doi_not_found": 0,
        "imported": 0,
        "skipped_duplicate": 0,
        "failed": 0,
    }
    st.session_state["pipeline_step1_running"] = False
    st.session_state["pipeline_step1_index"] = 0
    st.session_state["pipeline_step1_done"] = False
    st.session_state["pipeline_step2_running"] = False
    st.session_state["pipeline_step2_index"] = 0
    st.session_state["pipeline_step2_targets"] = []
    st.session_state["pipeline_step2_done"] = False
    st.session_state["pipeline_enable_import"] = False
    st.session_state["pipeline_skip_existing"] = True
    st.session_state["pipeline_collection_prefix"] = "Paper_Refs"
    st.session_state["pipeline_collection_name"] = ""
    st.session_state["pipeline_collection_key"] = ""
    st.session_state["pipeline_status"] = ""
    st.session_state["pipeline_cancelled"] = False


def _build_verification_config(collection_prefix: str) -> VerificationConfig:
    library_id = os.getenv("ZOTERO_LIBRARY_ID", "").strip()
    library_type = os.getenv("ZOTERO_LIBRARY_TYPE", "user").strip() or "user"
    api_key = os.getenv("ZOTERO_API_KEY", "").strip()
    crossref_mailto = os.getenv("CROSSREF_MAILTO", "").strip()
    threshold_raw = os.getenv("CROSSREF_SCORE_THRESHOLD", "30").strip() or "30"
    delay_raw = os.getenv("REQUEST_DELAY_SECONDS", "0.3").strip() or "0.3"

    missing = []
    if not library_id:
        missing.append("ZOTERO_LIBRARY_ID")
    if not api_key:
        missing.append("ZOTERO_API_KEY")
    if missing:
        raise ValueError(f"自动导入 Zotero 缺少配置：{', '.join(missing)}")
    if library_type not in {"user", "group"}:
        raise ValueError("ZOTERO_LIBRARY_TYPE 必须为 user 或 group。")
    if not crossref_mailto:
        raise ValueError("缺少 CROSSREF_MAILTO，请先在 .env 中配置邮箱。")

    return VerificationConfig(
        zotero_library_id=library_id,
        zotero_library_type=library_type,
        zotero_api_key=api_key,
        crossref_mailto=crossref_mailto,
        crossref_score_threshold=float(threshold_raw),
        input_bib=Path("reference.bib"),
        collection_prefix=collection_prefix,
        request_delay_seconds=float(delay_raw),
    )


def _process_single_crossref_row(citation: str, idx: int) -> dict[str, str]:
    parsed = parse_citation_text(citation)
    slots = build_crossref_search_slots(parsed)
    row = {
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
    try:
        result = lookup_doi(
            author=getattr(slots, "author", ""),
            title=getattr(slots, "title", ""),
            journal=getattr(slots, "journal", ""),
            year=getattr(slots, "year", ""),
            volume=getattr(slots, "volume", ""),
        )
        row["doi"] = (getattr(result, "doi", "") or "").strip()
        row["score"] = f"{getattr(result, 'score', '')}" if getattr(result, "score", None) is not None else ""
        if not row["parsed_author"]:
            row["parsed_author"] = getattr(result, "authors", "") or row["parsed_author"]
        if not row["parsed_journal"]:
            row["parsed_journal"] = getattr(result, "journal", "") or row["parsed_journal"]
        if not row["parsed_year"]:
            row["parsed_year"] = getattr(result, "year", "") or row["parsed_year"]
        if not row["parsed_volume"]:
            row["parsed_volume"] = getattr(result, "volume", "") or row["parsed_volume"]
        row["stage"] = "lookup_done"
        if row["doi"]:
            row["status"] = "doi_found"
        else:
            row["status"] = "doi_not_found"
            row["error"] = getattr(result, "warning", "") or "未找到 DOI"
    except Exception as exc:  # noqa: BLE001
        row["stage"] = "failed"
        row["status"] = "error"
        row["error"] = str(exc)
    return row


def _update_crossref_summary(summary: dict[str, int], row: dict[str, str]) -> None:
    if row["status"] == "doi_found":
        summary["doi_found"] += 1
    elif row["status"] == "doi_not_found":
        summary["doi_not_found"] += 1
    elif row["status"] == "error":
        summary["failed"] += 1


def _import_single_row(
    row: dict[str, str],
    importer: ZoteroImporter,
    collection_key: str,
    skip_existing_doi: bool,
) -> dict[str, str]:
    if not row.get("doi"):
        return row
    parsed_obj = type(
        "ParsedObj",
        (),
        {
            "author": row.get("parsed_author", ""),
            "title": row.get("parsed_title", ""),
            "journal": row.get("parsed_journal", ""),
            "year": row.get("parsed_year", ""),
            "volume": row.get("parsed_volume", ""),
        },
    )()
    doi_obj = type(
        "LookupObj",
        (),
        {
            "doi": row.get("doi", ""),
            "score": float(row.get("score")) if row.get("score") else None,
            "title": row.get("parsed_title", ""),
            "authors": row.get("parsed_author", ""),
            "journal": row.get("parsed_journal", ""),
            "year": row.get("parsed_year", ""),
            "volume": row.get("parsed_volume", ""),
        },
    )()
    try:
        import_status, item_key = importer.import_doi_entry(
            order=int(row["idx"]),
            parsed=parsed_obj,
            doi_result=doi_obj,
            collection_key=collection_key,
            skip_existing=skip_existing_doi,
        )
        row["stage"] = "zotero_done"
        row["zotero_item_key"] = item_key
        if import_status == "imported":
            row["status"] = "zotero_imported"
        elif import_status == "skipped_duplicate":
            row["status"] = "zotero_skipped_duplicate"
        else:
            row["status"] = "zotero_failed"
            row["error"] = f"未知导入状态: {import_status}"
    except Exception as exc:  # noqa: BLE001
        row["stage"] = "zotero_done"
        row["status"] = "zotero_failed"
        row["error"] = str(exc)
    return row


def _hits_to_query_rows(hits) -> list[dict[str, str]]:
    rows_out: list[dict[str, str]] = []
    for i, h in enumerate(hits, start=1):
        rows_out.append(
            {
                "rank": str(i),
                "recommendation_label": h.recommendation_label,
                "final_score": f"{h.final_score:.4f}",
                "crossref_score": "" if h.crossref_score is None else f"{h.crossref_score:.2f}",
                "title_overlap": f"{h.title_overlap:.4f}",
                "rule_bonus": f"{getattr(h, 'rule_bonus', 0.0):.4f}",
                "doi": h.doi,
                "title": h.title,
                "authors": h.authors,
                "journal": h.journal,
                "year": h.year,
                "matched_terms": ", ".join(h.matched_terms[:12]),
                "search_subquery": h.search_subquery,
            }
        )
    return rows_out


def _query_row_key(row: dict[str, str]) -> str:
    doi = (row.get("doi") or "").strip().lower()
    if doi:
        return f"doi::{doi}"
    return f"title::{(row.get('title') or '').strip().lower()}::{(row.get('year') or '').strip()}"


def _query_table_row_to_import_row(row: dict[str, str]) -> dict[str, str]:
    """将 Query 结果表行转为与文献 DOI Tab 一致的导入行结构。"""
    return {
        "idx": str(row.get("rank", "0")),
        "parsed_author": row.get("authors", ""),
        "parsed_title": row.get("title", ""),
        "parsed_journal": row.get("journal", ""),
        "parsed_year": row.get("year", ""),
        "parsed_volume": "",
        "doi": (row.get("doi") or "").strip(),
        "score": (row.get("crossref_score") or "").strip(),
        "stage": "",
        "status": "",
        "zotero_item_key": "",
        "error": "",
    }


def _reset_query_pipeline_state() -> None:
    st.session_state["query_pipeline_result"] = {"subqueries": [], "keywords": [], "hits": []}
    st.session_state["query_pipeline_plans"] = []
    st.session_state["query_pipeline_pairs"] = []
    st.session_state["query_pipeline_plan_index"] = 0
    st.session_state["query_pipeline_running"] = False
    st.session_state["query_pipeline_stage"] = "idle"
    st.session_state["query_pipeline_status"] = ""
    st.session_state["query_pipeline_user_query"] = ""
    _clear_query_editor_session_keys()


def _clear_query_editor_session_keys() -> None:
    for k in (
        "query_editor_rows",
        "query_editor_row_keys",
        "query_editor_rows_top",
        "query_editor_row_keys_top",
        "query_editor_rows_more",
        "query_editor_row_keys_more",
        "query_de_cached_nonce",
        "query_de_top_rows",
        "query_de_more_rows",
    ):
        st.session_state.pop(k, None)


def render_query_search_tab() -> None:
    if "query_pipeline_stage" not in st.session_state:
        _reset_query_pipeline_state()

    st.markdown(
        "输入一段**英文检索式**（claim 或关键词句）。系统用规则抽取关键词并构造多条 Crossref 检索串，"
        "召回后按与 query 的词重合、Crossref 分数、年份等规则打分排序。"
        "排序完成后可勾选结果并 **Import** 到 Zotero（新建 Collection，与「文献 DOI 与 Zotero」Tab 相同）。"
        "**暂未接入大模型**；下方说明如何预留语义重排接口。",
    )

    st.markdown(
        """
<style>
button[data-testid="baseButton-secondary"] p {
  color: #d62728 !important;
  font-weight: 700 !important;
}
</style>
""",
        unsafe_allow_html=True,
    )

    with st.form("query_pipeline_form"):
        q_text = st.text_area(
            "Query",
            height=140,
            placeholder=(
                "e.g. Recent machine learning methods improve polymer property prediction "
                "under low-data settings."
            ),
        )
        run = st.form_submit_button("检索并排序")
    if run:
        if not (q_text or "").strip():
            st.warning("请输入 Query。")
        else:
            try:
                _reset_query_pipeline_state()
                subqueries, keywords, plans = build_retrieval_plans((q_text or "").strip())
                st.session_state["query_pipeline_result"] = {
                    "subqueries": subqueries,
                    "keywords": keywords,
                    "hits": [],
                }
                st.session_state["query_pipeline_user_query"] = (q_text or "").strip()
                st.session_state["query_pipeline_plans"] = plans
                st.session_state["query_pipeline_stage"] = "retrieving"
                st.session_state["query_pipeline_status"] = (
                    f"阶段 1/3：关键词提取完成。已生成 {len(subqueries)} 条检索子串。"
                )
                st.session_state["query_selected_keys"] = set()
                st.session_state["query_pipeline_running"] = True
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except Exception as exc:  # noqa: BLE001
                st.error(f"检索失败：{exc}")

    res = st.session_state.get("query_pipeline_result")
    stage = str(st.session_state.get("query_pipeline_stage", "idle"))
    if stage == "idle" or not res:
        return

    st.subheader("检索语句与关键词")
    st.caption("以下子串由 `QueryBuilder` 生成，用于 Crossref `query.bibliographic`。")
    subqueries = res.get("subqueries") or []
    for sq in subqueries[:5]:
        st.code(sq, language=None)
    if len(subqueries) > 5:
        with st.expander(f"查看更多（其余 {len(subqueries) - 5} 条子串）"):
            for sq in subqueries[5:]:
                st.code(sq, language=None)
    kws = res.get("keywords") or []
    st.markdown("**关键词（展示）**： " + (", ".join(kws) if kws else "—"))

    plans = st.session_state.get("query_pipeline_plans", [])
    current = int(st.session_state.get("query_pipeline_plan_index", 0))
    total = len(plans)
    running = bool(st.session_state.get("query_pipeline_running", False))
    stage = str(st.session_state.get("query_pipeline_stage", "idle"))
    st.subheader("检索进度")
    st.progress((current / total) if total else 0.0)
    st.write(st.session_state.get("query_pipeline_status", ""))

    control_col1, control_col2 = st.columns([1, 1])
    if running:
        if control_col1.button("暂停检索", key="query_pause_button"):
            st.session_state["query_pipeline_running"] = False
            st.session_state["query_pipeline_stage"] = "paused"
            st.session_state["query_pipeline_status"] = "Paused. Click Start Again to run from start."
            st.rerun()

    if stage == "paused" and not running:
        if st.button("Start Again", key="query_start_again_button"):
            st.session_state["query_pipeline_pairs"] = []
            st.session_state["query_pipeline_plan_index"] = 0
            st.session_state["query_pipeline_result"]["hits"] = []
            st.session_state["query_selected_keys"] = set()
            _clear_query_editor_session_keys()
            st.session_state["query_pipeline_running"] = True
            st.session_state["query_pipeline_stage"] = "retrieving"
            st.session_state["query_pipeline_status"] = "阶段 2/3：Crossref 召回中（Start Again）。"
            st.rerun()

    if running and current < total:
        try:
            mail = (os.getenv("CROSSREF_MAILTO", "") or "").strip()
            if not mail:
                raise ValueError("缺少 CROSSREF_MAILTO，请在 .env 中配置邮箱。")
            tk = int(os.getenv("CROSSREF_QUERY_TOP_K", "10").strip() or "10")
            delay = float(os.getenv("REQUEST_DELAY_SECONDS", "0.3").strip() or "0.3")
            plan = plans[current]
            items = execute_retrieval_plan(mail, plan, limit=tk, delay_seconds=delay)
            pairs = list(st.session_state.get("query_pipeline_pairs", []))
            for item in items:
                pairs.append((item, plan["display"]))
            st.session_state["query_pipeline_pairs"] = pairs
            st.session_state["query_pipeline_plan_index"] = current + 1
            st.session_state["query_pipeline_status"] = (
                f"阶段 2/3：召回进度 {current + 1}/{total}，当前子串：`{plan['display']}`，新增 {len(items)} 条。"
            )
            if current + 1 >= total:
                query_for_rank = str(st.session_state.get("query_pipeline_user_query", "")).strip()
                hits = finalize_ranked_hits(user_query=query_for_rank, pairs=pairs)
                st.session_state["query_pipeline_result"]["hits"] = hits
                st.session_state["query_pipeline_running"] = False
                st.session_state["query_pipeline_stage"] = "done"
                st.session_state["query_pipeline_status"] = f"阶段 3/3：排序完成，候选 {len(hits)} 条。"
                st.session_state["query_table_nonce"] = int(st.session_state.get("query_table_nonce", 0)) + 1
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.session_state["query_pipeline_running"] = False
            st.session_state["query_pipeline_stage"] = "error"
            st.session_state["query_pipeline_status"] = f"检索失败：{exc}"
            st.rerun()

    if stage != "done":
        return
    hits = res.get("hits") or []
    preview_n = min(QUERY_HITS_PREVIEW, len(hits))
    st.subheader(f"召回与排序结果（共 {len(hits)} 条，默认前 {preview_n} 条）")
    st.caption(
        "若表格下方看不到「跳过已存在 DOI / Collection 前缀 / Import」或勾选仍异常："
        "请在运行 Streamlit 的终端按 **Ctrl+C** 结束进程，然后在 **`zotero-auto-script` 目录**执行 "
        "`streamlit run streamlit_app.py`，再在浏览器 **Ctrl+Shift+R** 强制刷新。"
    )
    if not hits:
        st.info("检索完成，但没有可展示的排序结果。")
        return

    rows_df = _hits_to_query_rows(hits)
    rows_top = rows_df[:QUERY_HITS_PREVIEW]
    rows_more = rows_df[QUERY_HITS_PREVIEW:]

    if "query_zotero_skip_existing" not in st.session_state:
        st.session_state["query_zotero_skip_existing"] = True
    if "query_zotero_collection_prefix" not in st.session_state:
        st.session_state["query_zotero_collection_prefix"] = os.getenv("COLLECTION_PREFIX", "Paper_Refs")

    _hits_column_config = {
        "select": st.column_config.CheckboxColumn("选择", pinned=True),
        "rank": "序",
        "recommendation_label": "标签",
        "final_score": "综合分",
        "crossref_score": "Crossref分",
        "title_overlap": "标题匹配",
        "rule_bonus": "规则加分",
        "doi": "DOI",
        "title": "标题",
        "authors": "作者",
        "journal": "期刊",
        "year": "年份",
        "matched_terms": "命中词",
        "search_subquery": "命中子检索串",
    }
    _hits_disabled = [
        "rank",
        "recommendation_label",
        "final_score",
        "crossref_score",
        "title_overlap",
        "rule_bonus",
        "doi",
        "title",
        "authors",
        "journal",
        "year",
        "matched_terms",
        "search_subquery",
    ]

    table_nonce = int(st.session_state.get("query_table_nonce", 0))
    # 关键：不要每轮用 query_selected_keys 重造整表数据，否则 data_editor 会认为数据全变而重挂载，
    # 勾选状态与点击事件会错乱（需等一轮 rerun 或连点）。仅在 table_nonce 变化时初始化一份缓存，
    # 之后只把编辑器返回值写回 session，由组件自己维护勾选。
    cached_n = st.session_state.get("query_de_cached_nonce")
    if cached_n != table_nonce:
        st.session_state["query_de_top_rows"] = [{**dict(r), "select": False} for r in rows_top]
        st.session_state["query_de_more_rows"] = [{**dict(r), "select": False} for r in rows_more]
        st.session_state["query_de_cached_nonce"] = table_nonce
        st.session_state["query_selected_keys"] = set()

    top_rows = st.session_state["query_de_top_rows"]
    # 传入深拷贝，避免 data_editor 内部原地改列表导致 session 与组件状态不同步
    edited_top = st.data_editor(
        copy.deepcopy(top_rows),
        use_container_width=True,
        hide_index=True,
        key=f"qhit_top_{table_nonce}",
        column_config=_hits_column_config,
        disabled=_hits_disabled,
    )
    st.session_state["query_de_top_rows"] = edited_top

    edited_more: list[dict[str, str | bool]] = []
    if rows_more:
        with st.expander(f"查看更多（其余 {len(rows_more)} 条）", expanded=False):
            more_rows = st.session_state["query_de_more_rows"]
            edited_more = st.data_editor(
                copy.deepcopy(more_rows),
                use_container_width=True,
                hide_index=True,
                key=f"qhit_more_{table_nonce}",
                column_config=_hits_column_config,
                disabled=_hits_disabled,
            )
            st.session_state["query_de_more_rows"] = edited_more

    def _collect_keys_from_edits(*parts: list) -> set[str]:
        keys: set[str] = set()
        for part in parts:
            for row in part:
                raw_row = {k: str(v) for k, v in row.items() if k != "select"}
                if bool(row.get("select")):
                    keys.add(_query_row_key(raw_row))
        return keys

    st.session_state["query_selected_keys"] = _collect_keys_from_edits(edited_top, edited_more)

    selected_rows_for_import: list[dict[str, str]] = []
    for row in list(edited_top) + list(edited_more):
        if not row.get("select"):
            continue
        raw_row = {k: str(v) for k, v in row.items() if k != "select"}
        if (raw_row.get("doi") or "").strip():
            selected_rows_for_import.append(raw_row)
    selected_rows_for_import.sort(key=lambda r: int(r.get("rank") or "0"))

    st.caption(
        "勾选条目后点击 **Import** 将写入 Zotero（每次导入新建一个 Collection，逻辑与「文献 DOI 与 Zotero」Tab 一致）。"
    )
    zc_a, zc_b = st.columns(2)
    with zc_a:
        st.checkbox("跳过已存在 DOI", key="query_zotero_skip_existing")
    with zc_b:
        st.text_input("Collection 前缀", key="query_zotero_collection_prefix")

    # 左：清除；右：Import（仅在有勾选时显示），中间留白避免挤在一起
    act_left, _act_spacer, act_right = st.columns([2, 4, 2])
    with act_left:
        clear_sel = st.button(
            "清除选择",
            key="query_clear_selection_btn",
            use_container_width=True,
        )
    import_clicked = False
    with act_right:
        if selected_rows_for_import:
            import_clicked = st.button(
                "Import",
                type="primary",
                key="query_zotero_import_btn",
                use_container_width=True,
            )

    if clear_sel:
        for row in st.session_state.get("query_de_top_rows") or []:
            row["select"] = False
        for row in st.session_state.get("query_de_more_rows") or []:
            row["select"] = False
        st.session_state["query_selected_keys"] = set()
        st.rerun()

    if import_clicked and selected_rows_for_import:
        try:
            prefix = str(st.session_state.get("query_zotero_collection_prefix", "Paper_Refs")).strip() or "Paper_Refs"
            cfg = _build_verification_config(prefix)
            importer = ZoteroImporter(cfg)
            collection_name, collection_key = importer.create_collection()
            skip_ex = bool(st.session_state.get("query_zotero_skip_existing", True))
            imported_n = skipped_n = failed_n = 0
            err_lines: list[str] = []
            for raw in selected_rows_for_import:
                im_row = _query_table_row_to_import_row(raw)
                updated = _import_single_row(im_row, importer, collection_key, skip_ex)
                stt = updated.get("status", "")
                if stt == "zotero_imported":
                    imported_n += 1
                elif stt == "zotero_skipped_duplicate":
                    skipped_n += 1
                else:
                    failed_n += 1
                    if updated.get("error"):
                        err_lines.append(str(updated["error"]))
            parts_msg = [
                f"Collection：`{collection_name}`",
                f"新建 {imported_n} 条",
                f"跳过重复 {skipped_n} 条",
                f"失败 {failed_n} 条",
            ]
            summary_msg = "；".join(parts_msg) + "。"
            if failed_n and err_lines:
                st.warning(summary_msg + "\n\n" + "\n".join(err_lines[:8]))
            else:
                st.success(summary_msg)
        except ValueError as exc:
            st.error(str(exc))
        except ZoteroImportError as exc:
            st.error(str(exc))

    with st.expander("大模型语义重排（预留接口）"):
        st.markdown(
            "在 `citation_pipeline/crossref_query_pipeline.py` 中，"
            "`run_crossref_query_pipeline(..., llm_reranker=...)` 可在规则排序后插入语义重排。\n\n"
            "- 实现 `LLMReranker` 协议的 `rerank(user_query, hits)`，或传入 "
            "`Callable[[str, list[CrossrefQueryHit]], list[CrossrefQueryHit]]`。\n"
            "- Streamlit 侧后续可增加「启用 LLM 重排」开关并注入具体实现。"
        )


def render_doi_zotero_tab() -> None:
    st.markdown(
        "直接粘贴一条或多条参考文献信息（自动提取作者、标题、期刊、年份、卷号）。"
        "系统先用 Crossref 批量检索 DOI；若勾选自动导入，则继续把结果写入 Zotero 并实时展示全流程状态。",
    )

    if "pipeline_step1_done" not in st.session_state:
        _reset_pipeline_state()

    with st.form("doi_query"):
        citation_text = st.text_area(
            "完整参考文献信息（支持多条粘贴）",
            value="",
            placeholder="例如：[1] Author. Title[J]. Journal 2024, 10, 100-110",
            height=220,
        )
        col_left, col_right = st.columns(2)
        with col_left:
            enable_zotero_import = st.checkbox("自动导入 Zotero", value=True)
            skip_existing_doi = st.checkbox("跳过已存在 DOI", value=True)
        with col_right:
            collection_prefix = st.text_input(
                "Collection 前缀",
                value=os.getenv("COLLECTION_PREFIX", "Paper_Refs"),
            )

        submitted = st.form_submit_button("批量查询 DOI")
    if submitted:
        _reset_pipeline_state()
        citations = _split_citations(citation_text)
        if not citations:
            st.error("没有识别到可处理的文献条目，请粘贴至少一条。")
            return
        st.session_state["pipeline_citations"] = citations
        st.session_state["pipeline_summary"]["total"] = len(citations)
        st.session_state["pipeline_enable_import"] = enable_zotero_import
        st.session_state["pipeline_skip_existing"] = skip_existing_doi
        st.session_state["pipeline_collection_prefix"] = collection_prefix.strip() or "Paper_Refs"
        st.session_state["pipeline_step1_running"] = True
        st.session_state["pipeline_status"] = "Step 1/2 启动：Crossref DOI 查询"
        st.rerun()

    citations = st.session_state.get("pipeline_citations", [])
    rows = st.session_state.get("pipeline_rows", [])
    summary = st.session_state.get("pipeline_summary", {})
    enable_zotero_import = bool(st.session_state.get("pipeline_enable_import", False))
    skip_existing_doi = bool(st.session_state.get("pipeline_skip_existing", True))
    collection_prefix = str(st.session_state.get("pipeline_collection_prefix", "Paper_Refs"))
    step1_running = bool(st.session_state.get("pipeline_step1_running", False))
    step2_running = bool(st.session_state.get("pipeline_step2_running", False))

    if not citations:
        st.caption("在上方粘贴文献并点击「批量查询 DOI」。")
        return

    if citations:
        st.subheader("批量处理进度")
        st.markdown("**Step 1/2：Crossref DOI 查询**")
        step1_total = max(len(citations), 1)
        step1_done = len(rows)
        st.progress(min(step1_done / step1_total, 1.0))
        action_col1, action_col2 = st.columns([1, 1])
        cancel_step1 = action_col1.button(
            "中止 Step 1",
            type="secondary",
            disabled=(not step1_running),
            key="cancel_step1_button",
        )
        if cancel_step1 and step1_running:
            st.session_state["pipeline_step1_running"] = False
            st.session_state["pipeline_cancelled"] = True
            st.session_state["pipeline_status"] = "Step 1 已中止：保留当前 Crossref 结果。"
            st.rerun()
        if step1_running:
            idx0 = int(st.session_state.get("pipeline_step1_index", 0))
            if idx0 < len(citations):
                current_idx = idx0 + 1
                row = _process_single_crossref_row(citations[idx0], current_idx)
                rows.append(row)
                _update_crossref_summary(summary, row)
                st.session_state["pipeline_rows"] = rows
                st.session_state["pipeline_summary"] = summary
                st.session_state["pipeline_step1_index"] = idx0 + 1
                st.session_state["pipeline_status"] = (
                    f"Step 1/2（Crossref）处理中：{current_idx}/{len(citations)}，状态：{row['status']}"
                )
                if idx0 + 1 >= len(citations):
                    st.session_state["pipeline_step1_running"] = False
                    st.session_state["pipeline_step1_done"] = True
                    st.session_state["pipeline_status"] = "Step 1/2 已完成：Crossref DOI 查询"
                time.sleep(0.05)
                st.rerun()

    st.write(st.session_state.get("pipeline_status", ""))

    if (
        enable_zotero_import
        and st.session_state.get("pipeline_step1_done")
        and not st.session_state.get("pipeline_step2_done")
        and not st.session_state.get("pipeline_cancelled")
    ):
        st.markdown("**Step 2/2：Zotero 条目插入**")
        target_rows = [row for row in rows if row.get("doi")]
        step2_total = max(len(target_rows), 1)
        step2_done = int(st.session_state.get("pipeline_step2_index", 0))
        st.progress(min(step2_done / step2_total, 1.0))
        action_col1, action_col2, action_col3 = st.columns([1, 1, 1])
        start_step2 = action_col1.button("开始 Step 2 导入", type="primary", disabled=step2_running)
        cancel_step2 = action_col2.button("中止 Step 2", type="secondary", disabled=(not step2_running))
        cancel_task = action_col3.button("中止任务", type="secondary", disabled=step2_running)
        if cancel_task:
            st.session_state["pipeline_cancelled"] = True
            st.warning("已中止：仅保留 Step 1 的 Crossref 结果。")
        if start_step2 and not step2_running:
            try:
                config = _build_verification_config(collection_prefix=collection_prefix)
                run_collection_name = f"{config.collection_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                importer = ZoteroImporter(config)
                collection_name, collection_key = importer.create_collection(run_collection_name)
                st.session_state["pipeline_collection_name"] = collection_name
                st.session_state["pipeline_collection_key"] = collection_key
                st.session_state["pipeline_step2_targets"] = [i for i, row in enumerate(rows) if row.get("doi")]
                st.session_state["pipeline_step2_running"] = True
                st.session_state["pipeline_status"] = "Step 2/2 启动：Zotero 条目插入"
                st.rerun()
            except (ValueError, ZoteroImportError) as exc:
                st.error(f"Zotero 初始化失败：{exc}")
        if cancel_step2 and step2_running:
            st.session_state["pipeline_step2_running"] = False
            st.session_state["pipeline_cancelled"] = True
            st.session_state["pipeline_status"] = "Step 2 已中止：保留当前入库结果。"
            st.rerun()

        if step2_running:
            try:
                config = _build_verification_config(collection_prefix=collection_prefix)
                importer = ZoteroImporter(config)
                target_indices = st.session_state.get("pipeline_step2_targets", [])
                current_step2 = int(st.session_state.get("pipeline_step2_index", 0))
                if current_step2 < len(target_indices):
                    target_row_index = target_indices[current_step2]
                    updated = _import_single_row(
                        row=rows[target_row_index],
                        importer=importer,
                        collection_key=st.session_state.get("pipeline_collection_key", ""),
                        skip_existing_doi=skip_existing_doi,
                    )
                    rows[target_row_index] = updated
                    if updated["status"] == "zotero_imported":
                        summary["imported"] = summary.get("imported", 0) + 1
                    elif updated["status"] == "zotero_skipped_duplicate":
                        summary["skipped_duplicate"] = summary.get("skipped_duplicate", 0) + 1
                    else:
                        summary["failed"] = summary.get("failed", 0) + 1
                    st.session_state["pipeline_step2_index"] = current_step2 + 1
                    st.session_state["pipeline_status"] = (
                        f"Step 2/2（Zotero）处理中：{current_step2 + 1}/{len(target_indices)}，状态：{updated['status']}"
                    )
                    if current_step2 + 1 >= len(target_indices):
                        st.session_state["pipeline_step2_running"] = False
                        st.session_state["pipeline_step2_done"] = True
                        st.session_state["pipeline_status"] = "Step 2/2 已完成：Zotero 条目插入"
                    st.session_state["pipeline_rows"] = rows
                    st.session_state["pipeline_summary"] = summary
                    time.sleep(0.05)
                    st.rerun()
            except (ValueError, ZoteroImportError) as exc:
                st.error(f"Step 2 导入失败：{exc}")
                st.session_state["pipeline_step2_running"] = False

    found = summary.get("doi_found", 0)
    if enable_zotero_import and st.session_state.get("pipeline_step2_done"):
        st.subheader("批量处理结果（含 Zotero 入库）")
    else:
        st.subheader("批量 DOI 检索结果")
    if enable_zotero_import and st.session_state.get("pipeline_step2_done"):
        st.success(
            f"共处理 {summary.get('total', len(rows))} 条；查到 DOI {summary.get('doi_found', 0)} 条；"
            f"导入成功 {summary.get('imported', 0)} 条；重复跳过 {summary.get('skipped_duplicate', 0)} 条；失败 {summary.get('failed', 0)} 条。",
        )
        if st.session_state.get("pipeline_collection_name"):
            st.caption(f"本次导入目标 Collection：`{st.session_state['pipeline_collection_name']}`")
    else:
        st.success(f"共处理 {len(rows)} 条；查到 DOI {found} 条；未查到 {len(rows) - found} 条。")
    st.dataframe(
        rows,
        use_container_width=True,
        column_config={
            "idx": "序号",
            "parsed_author": "作者",
            "parsed_title": "标题",
            "parsed_journal": "期刊",
            "parsed_year": "年份",
            "parsed_volume": "卷号",
            "search_query": "Crossref检索串",
            "doi": "DOI",
            "score": "Crossref分数",
            "status": "状态",
            "stage": "阶段",
            "zotero_item_key": "Zotero Item Key",
            "error": "错误信息",
        },
    )

    st.markdown("---")
    st.subheader("可复制结果")
    doi_prefixed = [f"DOI: {row['doi']}" for row in rows if row["doi"]]
    if not doi_prefixed:
        doi_prefixed = ["DOI: NOT_FOUND"]
    st.text_area(
        "Zotero 复制区（带 DOI: 前缀）",
        value="\n".join(doi_prefixed),
        height=160,
    )


def main() -> None:
    st.set_page_config(page_title="文献检索与 Zotero 工具", page_icon="📚", layout="centered")
    _init_env()
    _require_access_password()
    st.title("文献检索与 Zotero 工具")
    tab_doi, tab_query = st.tabs(["文献 DOI 与 Zotero", "Query 检索（Crossref）"])
    with tab_doi:
        render_doi_zotero_tab()
    with tab_query:
        render_query_search_tab()


if __name__ == "__main__":
    main()

