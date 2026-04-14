from __future__ import annotations

import os, re, sys, time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import bibtexparser
from bibtexparser.bparser import BibTexParser
from dotenv import load_dotenv
from habanero import Crossref
from pyzotero import zotero

REVIEW_PREFIX = "⚠️ [需手动检查] "
REVIEW_NOTE = "原始信息来自 AI；Crossref 未能可靠校验，请手动核查标题、作者、期刊、年份与 DOI。"
ANSI = {"green": "\033[92m", "yellow": "\033[93m", "red": "\033[91m", "blue": "\033[94m", "reset": "\033[0m"}


@dataclass
class Config:
    zotero_library_id: str
    zotero_library_type: str
    zotero_api_key: str
    crossref_mailto: str
    crossref_score_threshold: float
    input_bib: Path
    collection_prefix: str
    request_delay_seconds: float


class FatalError(Exception):
    pass


def c(msg: str, color: str) -> str:
    return msg if not sys.stdout.isatty() else f"{ANSI[color]}{msg}{ANSI['reset']}"


def info(msg: str) -> None: print(c(f"[信息] {msg}", "blue"))
def ok(msg: str) -> None: print(c(f"[成功] {msg}", "green"))
def warn(msg: str) -> None: print(c(f"[警告] {msg}", "yellow"))
def err(msg: str) -> None: print(c(f"[错误] {msg}", "red"))


def load_config() -> Config:
    load_dotenv()
    library_id = os.getenv("ZOTERO_LIBRARY_ID", "").strip()
    library_type = os.getenv("ZOTERO_LIBRARY_TYPE", "user").strip() or "user"
    api_key = os.getenv("ZOTERO_API_KEY", "").strip()
    crossref_mailto = os.getenv("CROSSREF_MAILTO", "").strip()
    threshold_raw = os.getenv("CROSSREF_SCORE_THRESHOLD", "30").strip() or "30"
    input_bib = os.getenv("INPUT_BIB", "reference.bib").strip() or "reference.bib"
    prefix = os.getenv("COLLECTION_PREFIX", "Paper_Refs").strip() or "Paper_Refs"
    delay_raw = os.getenv("REQUEST_DELAY_SECONDS", "0.3").strip() or "0.3"
    missing = []
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
    return Config(library_id, library_type, api_key, crossref_mailto, threshold, Path(input_bib), prefix, delay)


def clean(v: str) -> str:
    return re.sub(r"\s+", " ", (v or "").replace("{", "").replace("}", "")).strip()


def first_author(author_field: str) -> str:
    s = clean(author_field)
    if not s:
        return ""
    first = s.split(" and ")[0].strip()
    return first.split(",", 1)[0].strip() if "," in first else (first.split()[-1] if first.split() else "")


def parse_authors(author_field: str) -> list[dict[str, str]]:
    s = clean(author_field)
    if not s:
        return []
    out = []
    for raw in s.split(" and "):
        raw = raw.strip()
        if not raw:
            continue
        if "," in raw:
            last, first = [x.strip() for x in raw.split(",", 1)]
        else:
            parts = raw.split()
            first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", raw)
        out.append({"creatorType": "author", "firstName": first, "lastName": last})
    return out


def parse_refs(bib_path: Path) -> list[dict[str, Any]]:
    if not bib_path.exists():
        raise FatalError(f"输入文件不存在：{bib_path}")
    if bib_path.stat().st_size == 0:
        raise FatalError(f"输入文件为空：{bib_path}")
    try:
        with bib_path.open("r", encoding="utf-8") as f:
            parser = BibTexParser(common_strings=True)
            parser.ignore_nonstandard_types = False
            db = bibtexparser.load(f, parser=parser)
    except Exception as exc:
        raise FatalError(f"BibTeX 解析失败：{exc}") from exc
    if not db.entries:
        raise FatalError(".bib 文件中未解析到任何条目。")
    refs = []
    for i, e in enumerate(db.entries, start=1):
        author_raw = clean(e.get("author", ""))
        refs.append({
            "order": i,
            "entry_key": clean(e.get("ID", "")),
            "title": clean(e.get("title", "")),
            "author": first_author(author_raw),
            "author_raw": author_raw,
            "year": clean(e.get("year", "")),
            "journal": clean(e.get("journal", "") or e.get("journaltitle", "")),
            "doi": clean(e.get("doi", "")),
            "corrected_doi": "",
            "needs_review": False,
            "match_score": None,
            "crossref_title": "",
            "crossref_authors": [],
            "crossref_journal": "",
            "crossref_year": "",
        })
    return refs


def with_retry(func, config: Config, desc: str) -> Any:
    last = None
    for attempt in range(1, 4):
        try:
            result = func()
            time.sleep(config.request_delay_seconds)
            return result
        except Exception as exc:
            last = exc
            status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 429 and attempt < 3:
                wait = attempt * 2
                warn(f"{desc} 被限流，第 {attempt} 次重试前等待 {wait} 秒。")
                time.sleep(wait)
                continue
            if attempt < 3:
                warn(f"{desc} 失败，第 {attempt} 次重试中：{exc}")
                time.sleep(1.5 * attempt)
                continue
            raise
    raise last or RuntimeError("未知请求错误")


def xref_meta(item: dict[str, Any]) -> dict[str, Any]:
    published = item.get("published-print") or item.get("published-online") or item.get("issued") or {}
    date_parts = published.get("date-parts") or []
    year = str(date_parts[0][0]) if date_parts and date_parts[0] else ""
    authors = [
        {
            "creatorType": "author",
            "firstName": a.get("given", "") or "",
            "lastName": a.get("family", "") or a.get("name", "") or "",
        }
        for a in (item.get("author") or [])
    ]
    return {
        "title": clean((item.get("title") or [""])[0]),
        "authors": authors,
        "journal": clean((item.get("container-title") or [""])[0]),
        "year": year,
        "doi": clean(item.get("DOI", "")),
        "score": item.get("score"),
    }


def validate_doi(ref: dict[str, Any], cr: Crossref, config: Config):
    if not ref["doi"]:
        return None
    try:
        resp = with_retry(lambda: cr.works(ids=ref["doi"]), config, f"DOI 校验 {ref['doi']}")
    except Exception as exc:
        warn(f"第 {ref['order']} 篇 DOI 校验失败：{exc}")
        return None
    msg = resp.get("message") if isinstance(resp, dict) else None
    return xref_meta(msg) if msg else None


def search_crossref(ref: dict[str, Any], cr: Crossref, config: Config):
    query = " ".join(x for x in [ref["title"], ref["author"], ref["year"]] if x)
    try:
        resp = with_retry(lambda: cr.works(query_bibliographic=query, limit=1), config, f"Crossref 模糊搜索：{ref['title']}")
    except Exception as exc:
        warn(f"第 {ref['order']} 篇模糊搜索失败：{exc}")
        return None
    items = ((resp or {}).get("message") or {}).get("items") or []
    return xref_meta(items[0]) if items else None


def validate_and_correct(refs: list[dict[str, Any]], config: Config) -> list[dict[str, Any]]:
    cr = Crossref(mailto=config.crossref_mailto)
    total = len(refs)
    for ref in refs:
        info(f"校验进度 {ref['order']}/{total}：{ref['title'] or '[无标题]'}")
        meta = validate_doi(ref, cr, config)
        if meta:
            ref.update({
                "corrected_doi": meta["doi"] or ref["doi"],
                "crossref_title": meta["title"],
                "crossref_authors": meta["authors"],
                "crossref_journal": meta["journal"],
                "crossref_year": meta["year"],
                "match_score": meta["score"],
            })
            ok(f"第 {ref['order']} 篇 DOI 校验成功：{ref['corrected_doi']}")
            continue
        meta = search_crossref(ref, cr, config)
        if meta and meta["doi"] and (meta["score"] or 0) >= config.crossref_score_threshold:
            ref.update({
                "corrected_doi": meta["doi"],
                "crossref_title": meta["title"],
                "crossref_authors": meta["authors"],
                "crossref_journal": meta["journal"],
                "crossref_year": meta["year"],
                "match_score": meta["score"],
            })
            ok(f"第 {ref['order']} 篇模糊匹配成功：score={meta['score']}, DOI={meta['doi']}")
            continue
        ref["needs_review"] = True
        ref["match_score"] = meta["score"] if meta else None
        warn(f"第 {ref['order']} 篇需人工检查：{'最佳分数 ' + str(meta['score']) + ' 低于阈值' if meta and meta['score'] is not None else '未找到可靠 Crossref 结果'}。")
    return refs


def collection_name(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d')}"


def collection_key(resp: Any) -> str:
    for value in ((resp or {}).get("successful") or {}).values():
        data = value.get("data") or {}
        if data.get("key"):
            return data["key"]
    raise FatalError("Collection 创建成功，但未解析到 collection key。")


def build_item(ref: dict[str, Any], zot: zotero.Zotero, coll_key: str) -> dict[str, Any]:
    item = zot.item_template("journalArticle")
    title = ref["crossref_title"] or ref["title"]
    item["title"] = f"{REVIEW_PREFIX}{title}" if ref["needs_review"] else title
    item["creators"] = ref["crossref_authors"] or parse_authors(ref["author_raw"])
    item["publicationTitle"] = ref["crossref_journal"] or ref["journal"]
    item["date"] = ref["crossref_year"] or ref["year"]
    item["DOI"] = ref["corrected_doi"] or ref["doi"]
    item["collections"] = [coll_key]
    item["extra"] = f"Import Order: {ref['order']}\nOriginal Entry Key: {ref['entry_key']}\nOriginal DOI: {ref['doi'] or '[empty]'}"
    if ref["needs_review"]:
        item["abstractNote"] = REVIEW_NOTE
        item["extra"] += (
            f"\nCrossref Status: needs manual review"
            f"\nAI Title: {ref['title'] or '[empty]'}"
            f"\nAI Author: {ref['author_raw'] or '[empty]'}"
            f"\nAI Journal: {ref['journal'] or '[empty]'}"
            f"\nAI Year: {ref['year'] or '[empty]'}"
        )
    return item


def import_to_zotero(refs: list[dict[str, Any]], config: Config):
    try:
        zot = zotero.Zotero(config.zotero_library_id, config.zotero_library_type, config.zotero_api_key)
    except Exception as exc:
        raise FatalError(f"初始化 Zotero 客户端失败：{exc}") from exc
    coll_name = collection_name(config.collection_prefix)
    info(f"准备创建 Zotero Collection：{coll_name}")
    try:
        coll_resp = zot.create_collections([{"name": coll_name}])
        coll_key = collection_key(coll_resp)
    except Exception as exc:
        raise FatalError(f"Collection 创建失败：{exc}") from exc
    ok(f"Collection 创建成功：{coll_name} ({coll_key})")
    stats = {"total": len(refs), "normal": 0, "review": 0, "failed": 0}
    failed: list[str] = []
    for ref in refs:
        try:
            resp = zot.create_items([build_item(ref, zot, coll_key)])
            if not ((resp or {}).get("successful") or {}):
                raise RuntimeError(f"返回结果中无 successful：{resp}")
            stats["review" if ref["needs_review"] else "normal"] += 1
            ok(f"第 {ref['order']} 篇已导入 Zotero。")
        except Exception as exc:
            stats["failed"] += 1
            failed.append(f"#{ref['order']} {ref['title'] or '[无标题]'} -> {exc}")
            warn(f"第 {ref['order']} 篇导入失败：{exc}")
    return coll_name, stats, failed


def print_summary(coll_name: str, stats: dict[str, int], failed: list[str]) -> None:
    print()
    info("导入汇总")
    print(f"- Collection：{coll_name}")
    print(f"- 总文献数：{stats['total']}")
    print(f"- 正常导入数：{stats['normal']}")
    print(f"- 降级导入数：{stats['review']}")
    print(f"- 完全失败数：{stats['failed']}")
    if failed:
        print("- 失败明细：")
        for item in failed:
            print(f"  - {item}")
    print()
    print("请在 Zotero 中打开本次新建的 Collection，并搜索 `⚠️` 快速定位需手动检查的条目。")


def main() -> int:
    try:
        config = load_config()
        info("阶段 1/3：读取输入文件")
        refs = parse_refs(config.input_bib)
        ok(f"成功读取 {len(refs)} 篇参考文献。")
        info("阶段 2/3：校验与纠偏")
        refs = validate_and_correct(refs, config)
        info("阶段 3/3：导入 Zotero")
        coll_name, stats, failed = import_to_zotero(refs, config)
        print_summary(coll_name, stats, failed)
        return 0
    except FatalError as exc:
        err(str(exc))
        return 1
    except KeyboardInterrupt:
        err("用户中断执行。")
        return 130
    except Exception as exc:
        err(f"未预期异常：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
