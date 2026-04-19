from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

from habanero import Crossref

@dataclass
class DoiLookupResult:
    query: str
    doi: str
    score: float | None
    title: str
    authors: str
    journal: str
    year: str
    volume: str
    raw: dict[str, Any]
    warning: str | None = None


class DoiLookupError(Exception):
    pass


@dataclass
class ParsedCitation:
    author: str = ""
    title: str = ""
    journal: str = ""
    year: str = ""
    volume: str = ""
    raw_text: str = ""


@dataclass
class CrossrefSearchSlots:
    author: str = ""
    title: str = ""
    journal: str = ""
    year: str = ""
    volume: str = ""
    query: str = ""


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("{", "").replace("}", "")).strip()


def _extract_bib_field(bib_text: str, field_name: str) -> str:
    pattern = rf"(?is)\b{re.escape(field_name)}\s*=\s*(\{{(?:[^{{}}]|\{{[^{{}}]*\}})*\}}|\"[^\"]*\"|[^,\n]+)"
    match = re.search(pattern, bib_text)
    if not match:
        return ""
    value = clean_text(match.group(1).strip().rstrip(","))
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1].strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1].strip()
    return clean_text(value)


def _parse_bibtex_entry(text: str) -> ParsedCitation:
    parsed = ParsedCitation(raw_text=clean_text(text))
    parsed.title = _extract_bib_field(text, "title")
    parsed.author = clean_text(_extract_bib_field(text, "author").replace(" and ", ", "))
    parsed.journal = _extract_bib_field(text, "journal") or _extract_bib_field(text, "booktitle")
    parsed.year = _extract_bib_field(text, "year")
    parsed.volume = _extract_bib_field(text, "volume")
    return parsed


def _format_crossref_authors(authors_raw: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for author in authors_raw or []:
        family = clean_text(str(author.get("family", "")))
        given = clean_text(str(author.get("given", "")))
        full_name = clean_text(f"{given} {family}")
        if full_name:
            parts.append(full_name)
    return ", ".join(parts)


def _build_query(author: str, title: str, journal: str, year: str, volume: str) -> str:
    parts: list[str] = []
    for value in (author, title, journal, year, volume):
        value = (value or "").strip()
        if value:
            parts.append(value)
    return " ".join(parts)


def _with_retry(func, description: str, delay_seconds: float) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            result = func()
            time.sleep(delay_seconds)
            return result
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            status_code = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None),
                "status_code",
                None,
            )
            if status_code == 429 and attempt < 3:
                wait_seconds = attempt * 2
                print(
                    f"[警告] {description} 被限流，第 {attempt} 次重试前等待 {wait_seconds} 秒。",
                )
                time.sleep(wait_seconds)
                continue
            if attempt < 3:
                print(f"[警告] {description} 失败，第 {attempt} 次重试中：{exc}")
                time.sleep(1.5 * attempt)
                continue
            raise
    raise last_error or RuntimeError("未知请求错误")


def parse_citation_text(citation_text: str) -> ParsedCitation:
    """
    把整条参考文献信息尽量拆成 author/title/journal/year/volume。
    适配示例：
    [66] Xu M., ... and Lu X. Highperformance ... bonds[J]. ACS Applied Polymer Materials 2020, 2, 2228
    """
    text = clean_text(citation_text)
    if text.lstrip().startswith("@"):
        return _parse_bibtex_entry(citation_text)
    text = re.sub(r"^\[\s*\d+\s*\]\s*", "", text)
    parsed = ParsedCitation(raw_text=text)
    if not text:
        return parsed

    # 先按 [J] 标记分割（中英文文献常见格式）
    marker = re.search(r"\[\s*[Jj]\s*\]\.?\s*", text)
    if marker:
        left = text[: marker.start()].strip()
        right = text[marker.end() :].strip()
    else:
        # 若无 [J]，尽量按第一个年份切分：左侧作者+标题，右侧期刊+卷期
        year_match = re.search(r"(19|20)\d{2}", text)
        if year_match:
            left = text[: year_match.start()].strip()
            right = text[year_match.start() :].strip()
        else:
            left, right = text, ""

    # 解析作者 + 标题：优先匹配“作者串. 标题”
    author_block = (
        r"[A-Z][A-Za-z'`\-]+(?:\s+[A-Z](?:\.[A-Z])?\.?)"
        r"(?:\s*,\s*[A-Z][A-Za-z'`\-]+(?:\s+[A-Z](?:\.[A-Z])?\.?))*"
        r"(?:\s+and\s+[A-Z][A-Za-z'`\-]+(?:\s+[A-Z](?:\.[A-Z])?\.?))?"
    )
    left_match = re.match(rf"^(?P<authors>{author_block})\s*\.\s*(?P<title>.+)$", left)
    if left_match:
        parsed.author = clean_text(left_match.group("authors"))
        parsed.title = clean_text(left_match.group("title"))
    else:
        # 兜底：把左侧整体当标题
        parsed.title = clean_text(left)

    # 解析右侧期刊、年份、卷号
    right_match = re.search(
        r"^(?P<journal>.+?)\s+(?P<year>(19|20)\d{2})(?:\s*[,，]\s*(?P<volume>[\w\-]+))?",
        right,
    )
    if right_match:
        parsed.journal = clean_text(right_match.group("journal"))
        parsed.year = clean_text(right_match.group("year"))
        parsed.volume = clean_text(right_match.group("volume") or "")
    else:
        # 再兜底一次，只取年份
        year_fallback = re.search(r"(19|20)\d{2}", right)
        if year_fallback:
            parsed.year = year_fallback.group(0)
            parsed.journal = clean_text(right[: year_fallback.start()])

    return parsed


def _first_author_surname(author_text: str) -> str:
    value = clean_text(author_text)
    if not value:
        return ""
    first_segment = re.split(r"\band\b", value, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    first_author = first_segment.split(",")[0].strip()
    tokens = re.findall(r"[A-Za-z][A-Za-z'`\-]*", first_author)
    return tokens[0] if tokens else first_author


def build_crossref_search_slots(parsed: ParsedCitation) -> CrossrefSearchSlots:
    """
    一次解析后生成更适合 Crossref 的检索槽位：
    - author: 取第一作者姓氏（通常比整串作者更稳）
    - title/journal/year/volume: 清洗空白后原样使用
    """
    title = clean_text(parsed.title)
    journal = clean_text(parsed.journal)
    year = clean_text(parsed.year)
    volume = clean_text(parsed.volume)
    author = _first_author_surname(parsed.author) or clean_text(parsed.author)

    query = _build_query(author=author, title=title, journal=journal, year=year, volume=volume)
    return CrossrefSearchSlots(
        author=author,
        title=title,
        journal=journal,
        year=year,
        volume=volume,
        query=query,
    )


def lookup_doi(
    author: str = "",
    title: str = "",
    journal: str = "",
    year: str = "",
    volume: str = "",
) -> DoiLookupResult:
    """
    基于 Crossref 的简单 DOI 查询：
    - 将非空的 author/title/journal/year/volume 拼成 query_bibliographic
    - 取 Crossref 返回的 Top-1
    - 使用 CROSSREF_SCORE_THRESHOLD 作为“高置信”阈值，但即便低于阈值也会返回 Top-1，方便你在 UI 中人工判断。
    """
    mailto = os.getenv("CROSSREF_MAILTO", "").strip()
    if not mailto:
        raise DoiLookupError("缺少 CROSSREF_MAILTO，请在 .env 中配置你的邮箱。")

    threshold_raw = os.getenv("CROSSREF_SCORE_THRESHOLD", "30").strip() or "30"
    delay_raw = os.getenv("REQUEST_DELAY_SECONDS", "0.3").strip() or "0.3"

    try:
        threshold = float(threshold_raw)
        delay_seconds = float(delay_raw)
    except ValueError as exc:  # noqa: B904
        raise DoiLookupError("CROSSREF_SCORE_THRESHOLD / REQUEST_DELAY_SECONDS 必须是数字。") from exc

    query = _build_query(author=author, title=title, journal=journal, year=year, volume=volume)
    if not query:
        raise DoiLookupError("至少需要填写标题或作者中的一项。")

    client = Crossref(mailto=mailto)
    description = f"Crossref 查询：{query[:80]}"

    response = _with_retry(
        lambda: client.works(query_bibliographic=query, limit=1),
        description=description,
        delay_seconds=delay_seconds,
    )
    items = ((response or {}).get("message") or {}).get("items") or []
    if not items:
        return DoiLookupResult(
            query=query,
            doi="",
            score=None,
            title="",
            authors="",
            journal="",
            year="",
            volume="",
            raw=response or {},
            warning="未从 Crossref 找到任何候选结果。",
        )

    item = items[0]
    published = (
        item.get("published-print")
        or item.get("published-online")
        or item.get("issued")
        or {}
    )
    date_parts = published.get("date-parts") or []
    year_value = str(date_parts[0][0]) if date_parts and date_parts[0] else ""

    title_value = clean_text((item.get("title") or [""])[0])
    authors_value = _format_crossref_authors(item.get("author") or [])
    journal_value = clean_text((item.get("container-title") or [""])[0])
    volume_value = clean_text(str(item.get("volume", "")))
    doi_value = clean_text(item.get("DOI", ""))
    score_value = item.get("score")

    warning: str | None = None
    if not doi_value:
        warning = "Top-1 结果缺少 DOI。"
    elif score_value is None or score_value < threshold:
        warning = (
            f"Top-1 结果分数 {score_value} 低于阈值 {threshold}，建议人工再次核对。"
        )

    return DoiLookupResult(
        query=query,
        doi=doi_value,
        score=score_value,
        title=title_value,
        authors=authors_value,
        journal=journal_value,
        year=year_value,
        volume=volume_value,
        raw=item,
        warning=warning,
    )

