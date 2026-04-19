"""
Query → 关键词/检索串 → Crossref 召回 → 规则打分排序。
预留 LLM 语义重排接口（默认不调用）。
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator, Protocol, runtime_checkable

from habanero import Crossref

from citation_pipeline.doi_lookup import _format_crossref_authors, _with_retry, clean_text
from citation_pipeline.retrieval.query_builder import QueryBuilder


@dataclass
class CrossrefQueryHit:
    """单条 Crossref 召回结果（已打分）。"""

    doi: str
    title: str
    authors: str
    journal: str
    year: str
    volume: str
    crossref_score: float | None
    search_subquery: str
    matched_terms: list[str] = field(default_factory=list)
    title_overlap: float = 0.0
    meta_overlap: float = 0.0
    recency: float = 0.0
    final_score: float = 0.0
    rule_bonus: float = 0.0
    recommendation_label: str = "needs_review"
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMReranker(Protocol):
    """后续接入大模型时实现此协议，对规则排序后的列表做语义重排。"""

    def rerank(self, user_query: str, hits: list[CrossrefQueryHit]) -> list[CrossrefQueryHit]:
        ...


RerankFn = Callable[[str, list[CrossrefQueryHit]], list[CrossrefQueryHit]]
QueryPipelineEvent = dict[str, Any]
FetchWorksFn = Callable[..., list[dict[str, Any]]]


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", (text or "").lower()))


def _ordered_unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = clean_text(value)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _extract_year_terms(text: str) -> list[str]:
    return _ordered_unique(re.findall(r"\b(?:19|20)\d{2}\b", text))


def _extract_acronyms(text: str) -> list[str]:
    return _ordered_unique(re.findall(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)*\b", text))


def _extract_names(text: str) -> list[str]:
    raw_names = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b", text)
    blacklist = {
        "The Non", "Sorting Genetic", "Genetic Algorithm", "Unlike Traditional",
        "This Characteristic", "Pareto Front", "Multi Objective", "Objective Optimization",
    }
    names: list[str] = []
    for item in raw_names:
        value = clean_text(item)
        if value in blacklist:
            continue
        if len(value.split()) < 2:
            continue
        names.append(value)
    return _ordered_unique(names)


def _extract_phrase_acronym_pairs(text: str) -> list[tuple[str, str]]:
    pairs = re.findall(r"([A-Za-z][A-Za-z\s\-]{8,120})\(\s*([A-Z]{2,}(?:-[A-Z0-9]+)*)\s*\)", text)
    out: list[tuple[str, str]] = []
    for phrase, acronym in pairs:
        clean_phrase = clean_text(phrase)
        if clean_phrase:
            out.append((clean_phrase, acronym))
    return out


def _keyword_tokens(user_text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z\-]+", user_text.lower())
    stop = QueryBuilder.STOPWORDS
    return [t for t in tokens if t not in stop]


def _year_from_work(item: dict[str, Any]) -> str:
    published = (
        item.get("published-print")
        or item.get("published-online")
        or item.get("issued")
        or {}
    )
    parts = published.get("date-parts") or []
    if parts and parts[0]:
        return str(parts[0][0])
    return ""


def _recency_score(year_str: str, current_year: int) -> float:
    try:
        year = int(year_str)
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


def _year_alignment_score(year_str: str, query_years: list[str]) -> float:
    """用户 query 含年份时，用与目标年份的接近度替代“越新越好”。"""
    try:
        y = int(year_str)
    except (TypeError, ValueError):
        return 0.35
    best = 0.0
    for qy in query_years:
        try:
            q = int(qy)
        except (TypeError, ValueError):
            continue
        dist = abs(y - q)
        if dist == 0:
            best = max(best, 1.0)
        elif dist == 1:
            best = max(best, 0.88)
        elif dist <= 3:
            best = max(best, 0.55)
        elif dist <= 6:
            best = max(best, 0.35)
        else:
            best = max(best, 0.12)
    return best


def _df_counts(hits: list[CrossrefQueryHit]) -> dict[str, int]:
    df: dict[str, int] = {}
    for h in hits:
        for t in _token_set(" ".join([h.title, h.authors, h.journal])):
            df[t] = df.get(t, 0) + 1
    return df


def _idf_value(t: str, df_map: dict[str, int], n_docs: int) -> float:
    dfc = df_map.get(t, 0)
    return math.log((n_docs + 1) / (dfc + 1)) + 1.0


def _weighted_query_token_weight(t: str, df_map: dict[str, int], n_docs: int) -> float:
    """对在当前候选集中过于常见的词降权，突出区分性词。"""
    idf = _idf_value(t, df_map, n_docs)
    dfc = df_map.get(t, 0)
    specificity = 1.0 - (dfc / max(n_docs, 1))
    specificity = max(0.0, min(1.0, specificity))
    return idf * (0.32 + 0.68 * specificity)


def _overlap_ratio_weighted(
    q_tokens: set[str],
    doc_tokens: set[str],
    df_map: dict[str, int],
    n_docs: int,
) -> float:
    inter = q_tokens & doc_tokens
    if not inter or not q_tokens:
        return 0.0
    num = sum(_weighted_query_token_weight(t, df_map, n_docs) for t in inter)
    den = sum(_weighted_query_token_weight(t, df_map, n_docs) for t in q_tokens)
    return min(num / max(den, 1e-9), 1.0)


def _acronym_hits_in_title(acronyms: list[str], title_lower: str) -> int:
    n = 0
    for raw in acronyms:
        ac = raw.strip().lower()
        if len(ac) < 2:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(ac)}(?![a-z0-9])", title_lower):
            n += 1
    return n


def _surname_hits_in_authors(surnames: list[str], authors_lower: str) -> int:
    n = 0
    for s in surnames:
        sn = s.strip().lower()
        if len(sn) < 3:
            continue
        if re.search(rf"(?<![a-z]){re.escape(sn)}(?![a-z])", authors_lower):
            n += 1
    return n


def _bigram_hits_in_title(keyword_tokens: list[str], title_lower: str) -> int:
    if len(keyword_tokens) < 2:
        return 0
    n = 0
    for i in range(len(keyword_tokens) - 1):
        a, b = keyword_tokens[i], keyword_tokens[i + 1]
        if len(a) < 2 or len(b) < 2:
            continue
        phrase = f"{a} {b}"
        if phrase in title_lower:
            n += 1
    return min(n, 4)


def _work_to_hit(item: dict[str, Any], subquery: str) -> CrossrefQueryHit:
    title = clean_text((item.get("title") or [""])[0])
    journal = clean_text((item.get("container-title") or [""])[0])
    vol = clean_text(str(item.get("volume", "")))
    return CrossrefQueryHit(
        doi=clean_text(item.get("DOI", "")).lower(),
        title=title,
        authors=_format_crossref_authors(item.get("author") or []),
        journal=journal,
        year=_year_from_work(item),
        volume=vol,
        crossref_score=item.get("score"),
        search_subquery=subquery,
        raw=item,
    )


def _dedupe_by_doi_then_title(items: list[tuple[dict[str, Any], str]]) -> list[tuple[dict[str, Any], str]]:
    seen_doi: set[str] = set()
    seen_title: set[str] = set()
    out: list[tuple[dict[str, Any], str]] = []
    for item, subq in items:
        doi = clean_text(item.get("DOI", "")).lower()
        title = clean_text((item.get("title") or [""])[0]).lower()
        if doi:
            if doi in seen_doi:
                continue
            seen_doi.add(doi)
        else:
            if not title or title in seen_title:
                continue
            seen_title.add(title)
        out.append((item, subq))
    return out


def score_and_rank_hits(
    user_query: str,
    hits: list[CrossrefQueryHit],
    *,
    threshold_recommended: float | None = None,
    threshold_consider: float | None = None,
) -> list[CrossrefQueryHit]:
    """
    规则打分：Crossref 分 + 批量 IDF 加权的标题/全文元数据重合 + 年份（或 query 年份对齐）
    + 作者姓氏 / 缩写 / 短语 bigram / 年份命中 等加分（有上限）。
    """
    current_year = datetime.now().year
    t_rec = threshold_recommended if threshold_recommended is not None else float(
        os.getenv("QUERY_SCORE_RECOMMENDED", "0.72").strip() or "0.72"
    )
    t_con = threshold_consider if threshold_consider is not None else float(
        os.getenv("QUERY_SCORE_CONSIDER", "0.45").strip() or "0.45"
    )

    q_tokens = _token_set(user_query)
    if not q_tokens:
        q_tokens = _token_set(" ".join(h.title for h in hits))

    n_docs = max(len(hits), 1)
    df_map = _df_counts(hits) if hits else {}

    acronyms = _extract_acronyms(user_query)
    names = _extract_names(user_query)
    surnames = [n.split()[-1] for n in names if n.split()]
    query_years = _extract_year_terms(user_query)
    kw_tokens = _keyword_tokens(user_query)

    ranked: list[CrossrefQueryHit] = []
    for h in hits:
        doc_blob = " ".join([h.title, h.authors, h.journal])
        doc_tokens = _token_set(doc_blob)
        title_tokens = _token_set(h.title)
        matched = sorted(q_tokens & doc_tokens)
        h.matched_terms = matched

        title_overlap = _overlap_ratio_weighted(q_tokens, title_tokens, df_map, n_docs)
        meta_overlap = _overlap_ratio_weighted(q_tokens, doc_tokens, df_map, n_docs)
        h.title_overlap = round(title_overlap, 4)
        h.meta_overlap = round(meta_overlap, 4)

        cr = h.crossref_score
        crossref_norm = min((cr or 0.0) / 100.0, 1.0) if cr is not None else 0.0

        if query_years:
            h.recency = round(_year_alignment_score(h.year, query_years), 4)
            time_w = 0.13
        else:
            h.recency = round(_recency_score(h.year, current_year), 4)
            time_w = 0.10

        title_lower = (h.title or "").lower()
        authors_lower = (h.authors or "").lower()

        bonus = 0.0
        if _surname_hits_in_authors(surnames, authors_lower):
            bonus += 0.10
        ah = _acronym_hits_in_title(acronyms, title_lower)
        if ah:
            bonus += min(0.11, 0.055 * ah)
        if query_years and (h.year in query_years):
            bonus += 0.07
        bh = _bigram_hits_in_title(kw_tokens, title_lower)
        if bh:
            bonus += min(0.10, 0.035 * bh)

        h.rule_bonus = round(min(bonus, 0.24), 4)

        # 主体线性分（系数和约 0.80～0.84），再加 capped bonus，避免“泛词重合”压过区分信号
        base = (
            0.19 * crossref_norm
            + 0.34 * title_overlap
            + 0.24 * meta_overlap
            + time_w * h.recency
        )
        final = min(1.0, base + h.rule_bonus)
        h.final_score = round(final, 4)
        if final >= t_rec:
            h.recommendation_label = "recommended"
        elif final >= t_con:
            h.recommendation_label = "consider"
        else:
            h.recommendation_label = "needs_review"
        ranked.append(h)

    ranked.sort(
        key=lambda x: (
            x.final_score,
            x.crossref_score if x.crossref_score is not None else 0.0,
            len(x.matched_terms),
        ),
        reverse=True,
    )
    return ranked


def _fetch_crossref_works(
    mailto: str,
    *,
    query_bibliographic: str | None = None,
    query: str | None = None,
    query_author: str | None = None,
    query_title: str | None = None,
    limit: int,
    delay_seconds: float,
    label: str,
) -> list[dict[str, Any]]:
    client = Crossref(mailto=mailto)
    kwargs: dict[str, Any] = {"limit": limit}
    if query_bibliographic:
        kwargs["query_bibliographic"] = query_bibliographic
    if query:
        kwargs["query"] = query
    if query_author:
        kwargs["query_author"] = query_author
    if query_title:
        kwargs["query_title"] = query_title
    desc = f"Crossref {label}: {clean_text(str(kwargs))[:120]}"
    response = _with_retry(
        lambda: client.works(**kwargs),
        description=desc,
        delay_seconds=delay_seconds,
    )
    return ((response or {}).get("message") or {}).get("items") or []


def build_keyword_queries(user_text: str) -> tuple[list[str], list[str]]:
    """返回 (用于 Crossref 的检索串列表, 展示用关键词列表)。"""
    qb = QueryBuilder()
    rq = qb.build(segment_id="user_query", text=user_text.strip())
    tokens = _keyword_tokens(user_text)
    acronyms = _extract_acronyms(user_text)
    names = _extract_names(user_text)
    years = _extract_year_terms(user_text)
    phrase_pairs = _extract_phrase_acronym_pairs(user_text)

    keywords = _ordered_unique(tokens[:24] + [a.lower() for a in acronyms] + years)

    queries: list[str] = []
    queries.extend(rq.all_queries)
    if tokens:
        queries.append(" ".join(tokens[:12]))
        queries.append(" ".join(tokens[:6]))

    for acronym in acronyms:
        anchor_tokens = [t for t in tokens if len(t) > 4][:5]
        queries.append(" ".join([acronym, *anchor_tokens]).strip())

    for phrase, acronym in phrase_pairs:
        phrase_terms = " ".join(re.findall(r"[A-Za-z][A-Za-z\-]{2,}", phrase.lower())[:6])
        queries.append(f"{acronym} {phrase_terms}".strip())

    for name in names:
        surname = name.split()[-1]
        anchor = (acronyms[0] if acronyms else "") or (" ".join(tokens[:4]) if tokens else "")
        queries.append(f"{name} {anchor}".strip())
        queries.append(f"{surname} {anchor}".strip())

    if years:
        queries.append(f"{' '.join(tokens[:6])} {years[0]}".strip())

    # 避免 Crossref bibliographic 过长导致噪音。
    queries = [clean_text(q)[:180] for q in queries if clean_text(q)]
    queries = _ordered_unique(queries)
    return queries, keywords


def build_retrieval_plans(user_query: str) -> tuple[list[str], list[str], list[dict[str, str]]]:
    """构建检索子串与可执行计划，供前端分步执行与暂停控制。"""
    subqueries, keywords = build_keyword_queries(user_query)
    names = _extract_names(user_query)
    acronyms = _extract_acronyms(user_query)
    token_anchor = " ".join(_keyword_tokens(user_query)[:8])
    title_anchor = clean_text(" ".join(_keyword_tokens(user_query)[:12]))

    plans: list[dict[str, str]] = []
    for sq in subqueries:
        plans.append({"mode": "query_bibliographic", "display": sq})
    for name in names[:3]:
        surname = name.split()[-1]
        title_query = " ".join([*(acronyms[:1]), token_anchor]).strip()[:160]
        if title_query:
            plans.append(
                {
                    "mode": "author_title",
                    "display": f"{surname} + {title_query}",
                    "author": surname,
                    "title": title_query,
                }
            )
    if title_anchor:
        plans.append({"mode": "query", "display": title_anchor})
    return subqueries, keywords, plans


def execute_retrieval_plan(
    mail: str,
    plan: dict[str, str],
    *,
    limit: int,
    delay_seconds: float,
    fetch_works_fn: FetchWorksFn | None = None,
) -> list[dict[str, Any]]:
    """执行单条检索计划。"""
    fetch_fn = fetch_works_fn or _fetch_crossref_works
    mode = plan["mode"]
    if mode == "query_bibliographic":
        return fetch_fn(
            mail,
            query_bibliographic=plan["display"],
            limit=limit,
            delay_seconds=delay_seconds,
            label="query_bibliographic",
        )
    if mode == "author_title":
        return fetch_fn(
            mail,
            query_author=plan.get("author", ""),
            query_title=plan.get("title", ""),
            limit=limit,
            delay_seconds=delay_seconds,
            label="query_author+query_title",
        )
    return fetch_fn(
        mail,
        query=plan["display"],
        limit=limit,
        delay_seconds=delay_seconds,
        label="query",
    )


def finalize_ranked_hits(
    user_query: str,
    pairs: list[tuple[dict[str, Any], str]],
    *,
    llm_reranker: LLMReranker | RerankFn | None = None,
) -> list[CrossrefQueryHit]:
    deduped = _dedupe_by_doi_then_title(pairs)
    hits = [_work_to_hit(item, subq) for item, subq in deduped]
    ranked = score_and_rank_hits(user_query, hits)
    if llm_reranker is not None:
        rerank = getattr(llm_reranker, "rerank", None)
        if callable(rerank):
            ranked = rerank(user_query, ranked)
        elif callable(llm_reranker):
            ranked = llm_reranker(user_query, ranked)
    return ranked


def run_crossref_query_pipeline(
    user_query: str,
    *,
    mailto: str | None = None,
    top_k_per_query: int | None = None,
    delay_seconds: float | None = None,
    llm_reranker: LLMReranker | RerankFn | None = None,
    fetch_works_fn: FetchWorksFn | None = None,
) -> tuple[list[str], list[str], list[CrossrefQueryHit]]:
    """
    完整链路：关键词/多检索串 → Crossref 召回 → 去重 → 规则打分排序 → 可选 LLM 重排。

    llm_reranker 可为实现 LLMReranker 的对象，或 Callable[[str, list[CrossrefQueryHit]], list[CrossrefQueryHit]]。
    未接入大模型时传 None。
    """
    mail = (mailto or os.getenv("CROSSREF_MAILTO", "") or "").strip()
    if not mail:
        raise ValueError("缺少 CROSSREF_MAILTO，请在 .env 中配置邮箱。")

    tk = int(os.getenv("CROSSREF_QUERY_TOP_K", "10").strip() or "10") if top_k_per_query is None else top_k_per_query
    delay = float(os.getenv("REQUEST_DELAY_SECONDS", "0.3").strip() or "0.3") if delay_seconds is None else delay_seconds

    subqueries, keywords = build_keyword_queries(user_query)
    if not subqueries:
        return [], keywords, []

    pairs: list[tuple[dict[str, Any], str]] = []
    for event in run_crossref_query_pipeline_with_events(
        user_query,
        mailto=mail,
        top_k_per_query=tk,
        delay_seconds=delay,
        llm_reranker=llm_reranker,
        fetch_works_fn=fetch_works_fn,
    ):
        if event["event"] == "final":
            return event["subqueries"], event["keywords"], event["hits"]

    return subqueries, keywords, []


def run_crossref_query_pipeline_with_events(
    user_query: str,
    *,
    mailto: str | None = None,
    top_k_per_query: int | None = None,
    delay_seconds: float | None = None,
    llm_reranker: LLMReranker | RerankFn | None = None,
    fetch_works_fn: FetchWorksFn | None = None,
) -> Iterator[QueryPipelineEvent]:
    """事件流接口：关键词阶段 -> 召回进度 -> 排序完成。"""
    mail = (mailto or os.getenv("CROSSREF_MAILTO", "") or "").strip()
    if not mail:
        raise ValueError("缺少 CROSSREF_MAILTO，请在 .env 中配置邮箱。")

    tk = int(os.getenv("CROSSREF_QUERY_TOP_K", "10").strip() or "10") if top_k_per_query is None else top_k_per_query
    delay = float(os.getenv("REQUEST_DELAY_SECONDS", "0.3").strip() or "0.3") if delay_seconds is None else delay_seconds
    fetch_fn = fetch_works_fn or _fetch_crossref_works

    subqueries, keywords, plans = build_retrieval_plans(user_query)
    yield {
        "event": "keywords_ready",
        "subqueries": subqueries,
        "keywords": keywords,
    }
    if not subqueries:
        yield {"event": "final", "subqueries": [], "keywords": keywords, "hits": []}
        return

    yield {"event": "retrieval_started", "total": len(plans)}

    pairs: list[tuple[dict[str, Any], str]] = []
    for idx, plan in enumerate(plans, start=1):
        items = execute_retrieval_plan(
            mail,
            plan,
            limit=tk,
            delay_seconds=delay,
            fetch_works_fn=fetch_fn,
        )
        for it in items:
            pairs.append((it, plan["display"]))
        yield {
            "event": "retrieval_progress",
            "current": idx,
            "total": len(plans),
            "query": plan["display"],
            "fetched": len(items),
            "accumulated": len(pairs),
        }

    ranked = finalize_ranked_hits(user_query, pairs, llm_reranker=llm_reranker)

    yield {"event": "ranking_done", "candidate_count": len(ranked)}
    yield {"event": "final", "subqueries": subqueries, "keywords": keywords, "hits": ranked}
