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
        collection_name = self._collection_name()
        print(f"[信息] 准备创建 Zotero Collection：{collection_name}")
        try:
            response = self.client.create_collections([{"name": collection_name}])
            collection_key = self._collection_key(response)
        except Exception as exc:
            raise ZoteroImportError(f"Collection 创建失败：{exc}") from exc

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
