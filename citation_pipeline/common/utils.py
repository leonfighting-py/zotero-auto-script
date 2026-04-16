from __future__ import annotations

import re


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("{", "").replace("}", "")).strip()


def first_author(author_field: str) -> str:
    normalized = clean_text(author_field)
    if not normalized:
        return ""
    first = normalized.split(" and ")[0].strip()
    return first.split(",", 1)[0].strip() if "," in first else (first.split()[-1] if first.split() else "")


def parse_authors(author_field: str) -> list[dict[str, str]]:
    normalized = clean_text(author_field)
    if not normalized:
        return []
    authors: list[dict[str, str]] = []
    for raw in normalized.split(" and "):
        raw = raw.strip()
        if not raw:
            continue
        if "," in raw:
            last, first = [item.strip() for item in raw.split(",", 1)]
        else:
            parts = raw.split()
            first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", raw)
        authors.append({"creatorType": "author", "firstName": first, "lastName": last})
    return authors
