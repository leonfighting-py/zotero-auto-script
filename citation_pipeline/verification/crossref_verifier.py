from __future__ import annotations

import time
from typing import Any

from habanero import Crossref

from citation_pipeline.common.config import VerificationConfig
from citation_pipeline.common.models import VerifiedReference
from citation_pipeline.common.utils import clean_text


class CrossrefVerifier:
    def __init__(self, config: VerificationConfig):
        self.config = config
        self.client = Crossref(mailto=config.crossref_mailto)

    def verify_all(self, references: list[VerifiedReference]) -> list[VerifiedReference]:
        total = len(references)
        for reference in references:
            print(f"[信息] 校验进度 {reference.order}/{total}：{reference.title or '[无标题]'}")
            meta = self._validate_doi(reference)
            if meta:
                self._apply_meta(reference, meta, fallback_doi=reference.doi)
                print(f"[成功] 第 {reference.order} 篇 DOI 校验成功：{reference.corrected_doi}")
                continue

            meta = self._search_crossref(reference)
            if meta and meta["doi"] and (meta["score"] or 0) >= self.config.crossref_score_threshold:
                self._apply_meta(reference, meta, fallback_doi=reference.doi)
                print(f"[成功] 第 {reference.order} 篇模糊匹配成功：score={meta['score']}, DOI={meta['doi']}")
                continue

            reference.needs_review = True
            reference.match_score = meta["score"] if meta else None
            reason = (
                f"最佳分数 {meta['score']} 低于阈值"
                if meta and meta["score"] is not None
                else "未找到可靠 Crossref 结果"
            )
            print(f"[警告] 第 {reference.order} 篇需人工检查：{reason}。")
        return references

    def _apply_meta(self, reference: VerifiedReference, meta: dict[str, Any], fallback_doi: str) -> None:
        reference.corrected_doi = meta["doi"] or fallback_doi
        reference.crossref_title = meta["title"]
        reference.crossref_authors = meta["authors"]
        reference.crossref_journal = meta["journal"]
        reference.crossref_year = meta["year"]
        reference.match_score = meta["score"]
        reference.needs_review = False

    def _validate_doi(self, reference: VerifiedReference) -> dict[str, Any] | None:
        if not reference.doi:
            return None
        try:
            response = self._with_retry(
                lambda: self.client.works(ids=reference.doi),
                f"DOI 校验 {reference.doi}",
            )
        except Exception as exc:
            print(f"[警告] 第 {reference.order} 篇 DOI 校验失败：{exc}")
            return None
        message = response.get("message") if isinstance(response, dict) else None
        return self._xref_meta(message) if message else None

    def _search_crossref(self, reference: VerifiedReference) -> dict[str, Any] | None:
        query = " ".join(part for part in [reference.title, reference.author, reference.year] if part)
        try:
            response = self._with_retry(
                lambda: self.client.works(query_bibliographic=query, limit=1),
                f"Crossref 模糊搜索：{reference.title}",
            )
        except Exception as exc:
            print(f"[警告] 第 {reference.order} 篇模糊搜索失败：{exc}")
            return None
        items = ((response or {}).get("message") or {}).get("items") or []
        return self._xref_meta(items[0]) if items else None

    def _with_retry(self, func, description: str) -> Any:
        last_error = None
        for attempt in range(1, 4):
            try:
                result = func()
                time.sleep(self.config.request_delay_seconds)
                return result
            except Exception as exc:
                last_error = exc
                status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
                if status_code == 429 and attempt < 3:
                    wait_seconds = attempt * 2
                    print(f"[警告] {description} 被限流，第 {attempt} 次重试前等待 {wait_seconds} 秒。")
                    time.sleep(wait_seconds)
                    continue
                if attempt < 3:
                    print(f"[警告] {description} 失败，第 {attempt} 次重试中：{exc}")
                    time.sleep(1.5 * attempt)
                    continue
                raise
        raise last_error or RuntimeError("未知请求错误")

    def _xref_meta(self, item: dict[str, Any]) -> dict[str, Any]:
        published = item.get("published-print") or item.get("published-online") or item.get("issued") or {}
        date_parts = published.get("date-parts") or []
        year = str(date_parts[0][0]) if date_parts and date_parts[0] else ""
        authors = [
            {
                "creatorType": "author",
                "firstName": author.get("given", "") or "",
                "lastName": author.get("family", "") or author.get("name", "") or "",
            }
            for author in (item.get("author") or [])
        ]
        return {
            "title": clean_text((item.get("title") or [""])[0]),
            "authors": authors,
            "journal": clean_text((item.get("container-title") or [""])[0]),
            "year": year,
            "doi": clean_text(item.get("DOI", "")),
            "score": item.get("score"),
        }
