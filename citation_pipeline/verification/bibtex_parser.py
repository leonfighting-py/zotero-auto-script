from __future__ import annotations

from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser

from citation_pipeline.common.models import VerifiedReference
from citation_pipeline.common.utils import clean_text, first_author


class VerificationError(Exception):
    pass


class BibtexReferenceParser:
    def parse(self, bib_path: Path) -> list[VerifiedReference]:
        if not bib_path.exists():
            raise VerificationError(f"输入文件不存在：{bib_path}")
        if bib_path.stat().st_size == 0:
            raise VerificationError(f"输入文件为空：{bib_path}")

        try:
            with bib_path.open("r", encoding="utf-8") as handle:
                parser = BibTexParser(common_strings=True)
                parser.ignore_nonstandard_types = False
                db = bibtexparser.load(handle, parser=parser)
        except Exception as exc:
            raise VerificationError(f"BibTeX 解析失败：{exc}") from exc

        if not db.entries:
            raise VerificationError(".bib 文件中未解析到任何条目。")

        references: list[VerifiedReference] = []
        for index, entry in enumerate(db.entries, start=1):
            author_raw = clean_text(entry.get("author", ""))
            references.append(
                VerifiedReference(
                    order=index,
                    entry_key=clean_text(entry.get("ID", "")),
                    title=clean_text(entry.get("title", "")),
                    author=first_author(author_raw),
                    author_raw=author_raw,
                    year=clean_text(entry.get("year", "")),
                    journal=clean_text(entry.get("journal", "") or entry.get("journaltitle", "")),
                    doi=clean_text(entry.get("doi", "")),
                )
            )
        return references
