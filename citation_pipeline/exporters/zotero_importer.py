from __future__ import annotations

from datetime import datetime
from typing import Any

from pyzotero import zotero

from citation_pipeline.common.config import VerificationConfig
from citation_pipeline.common.models import VerifiedReference
from citation_pipeline.common.utils import parse_authors

REVIEW_PREFIX = "⚠️ [需手动检查] "
REVIEW_NOTE = "原始信息来自 AI；Crossref 未能可靠校验，请手动核查标题、作者、期刊、年份与 DOI。"


class ZoteroImportError(Exception):
    pass


class ZoteroImporter:
    def __init__(self, config: VerificationConfig):
        self.config = config
        try:
            self.client = zotero.Zotero(config.zotero_library_id, config.zotero_library_type, config.zotero_api_key)
        except Exception as exc:
            raise ZoteroImportError(f"初始化 Zotero 客户端失败：{exc}") from exc

    def import_references(self, references: list[VerifiedReference]) -> tuple[str, dict[str, int], list[str]]:
        collection_name, collection_key = self.create_collection()
        print(f"[成功] Collection 创建成功：{collection_name} ({collection_key})")
        stats = {"total": len(references), "normal": 0, "review": 0, "failed": 0}
        failures: list[str] = []
        for reference in references:
            try:
                response = self.client.create_items([self._build_item(reference, collection_key)])
                if not ((response or {}).get("successful") or {}):
                    raise RuntimeError(f"返回结果中无 successful：{response}")
                stats["review" if reference.needs_review else "normal"] += 1
                print(f"[成功] 第 {reference.order} 篇已导入 Zotero。")
            except Exception as exc:
                stats["failed"] += 1
                failures.append(f"#{reference.order} {reference.title or '[无标题]'} -> {exc}")
                print(f"[警告] 第 {reference.order} 篇导入失败：{exc}")
        return collection_name, stats, failures

    def create_collection(self, collection_name: str = "") -> tuple[str, str]:
        target_name = (collection_name or "").strip() or self._collection_name()
        print(f"[信息] 准备创建 Zotero Collection：{target_name}")
        try:
            response = self.client.create_collections([{"name": target_name}])
            collection_key = self._collection_key(response)
        except Exception as exc:
            raise ZoteroImportError(f"Collection 创建失败：{exc}") from exc
        return target_name, collection_key

    def normalize_doi(self, doi: str) -> str:
        value = (doi or "").strip()
        if value.lower().startswith("https://doi.org/"):
            value = value[16:]
        if value.lower().startswith("http://doi.org/"):
            value = value[15:]
        return value.strip().lower()

    def has_existing_doi(self, doi: str) -> bool:
        normalized = self.normalize_doi(doi)
        if not normalized:
            return False
        try:
            candidates = self.client.items(q=normalized)
        except Exception:
            # 查询失败时不阻断主流程，交给后续导入返回结果处理。
            return False

        for candidate in candidates or []:
            existing = self.normalize_doi((candidate.get("data") or {}).get("DOI", ""))
            if existing == normalized:
                return True
        return False

    def import_doi_entry(
        self,
        order: int,
        parsed,
        doi_result,
        collection_key: str,
        skip_existing: bool = True,
    ) -> tuple[str, str]:
        doi = self.normalize_doi(getattr(doi_result, "doi", ""))
        if not doi:
            return "missing_doi", ""
        if skip_existing and self.has_existing_doi(doi):
            return "skipped_duplicate", ""

        item = self.client.item_template("journalArticle")
        item["title"] = (getattr(doi_result, "title", "") or getattr(parsed, "title", "") or f"Reference {order}").strip()
        item["creators"] = parse_authors(getattr(parsed, "author", ""))
        item["publicationTitle"] = (getattr(doi_result, "journal", "") or getattr(parsed, "journal", "")).strip()
        item["date"] = (getattr(doi_result, "year", "") or getattr(parsed, "year", "")).strip()
        item["DOI"] = doi
        item["url"] = f"https://doi.org/{doi}"
        item["collections"] = [collection_key]
        item["extra"] = (
            "Import Source: streamlit_doi_lookup\n"
            f"Import Order: {order}\n"
            f"Crossref Score: {getattr(doi_result, 'score', '')}"
        )

        try:
            response = self.client.create_items([item])
            for value in ((response or {}).get("successful") or {}).values():
                data = value.get("data") or {}
                return "imported", str(data.get("key", ""))
            raise ZoteroImportError(f"返回结果中无 successful：{response}")
        except Exception as exc:
            raise ZoteroImportError(f"DOI 条目导入失败：{exc}") from exc

    def _collection_name(self) -> str:
        return f"{self.config.collection_prefix}_{datetime.now().strftime('%Y%m%d')}"

    def _collection_key(self, response: Any) -> str:
        for value in ((response or {}).get("successful") or {}).values():
            data = value.get("data") or {}
            if data.get("key"):
                return data["key"]
        raise ZoteroImportError("Collection 创建成功，但未解析到 collection key。")

    def _build_item(self, reference: VerifiedReference, collection_key: str) -> dict[str, Any]:
        item = self.client.item_template("journalArticle")
        title = reference.crossref_title or reference.title
        item["title"] = f"{REVIEW_PREFIX}{title}" if reference.needs_review else title
        item["creators"] = reference.crossref_authors or parse_authors(reference.author_raw)
        item["publicationTitle"] = reference.crossref_journal or reference.journal
        item["date"] = reference.crossref_year or reference.year
        item["DOI"] = reference.corrected_doi or reference.doi
        item["collections"] = [collection_key]
        item["extra"] = (
            f"Import Order: {reference.order}\n"
            f"Original Entry Key: {reference.entry_key}\n"
            f"Original DOI: {reference.doi or '[empty]'}"
        )
        if reference.needs_review:
            item["abstractNote"] = REVIEW_NOTE
            item["extra"] += (
                "\nCrossref Status: needs manual review"
                f"\nAI Title: {reference.title or '[empty]'}"
                f"\nAI Author: {reference.author_raw or '[empty]'}"
                f"\nAI Journal: {reference.journal or '[empty]'}"
                f"\nAI Year: {reference.year or '[empty]'}"
            )
        return item
