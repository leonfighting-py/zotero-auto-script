from __future__ import annotations

import json
from pathlib import Path


def load_claims_from_file(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"claims 输入文件不存在：{path}")
    if path.suffix.lower() == ".jsonl":
        return _load_jsonl_claims(path)
    return _load_text_claims(path)


def _load_text_claims(path: Path) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            claims.append((f"segment_{index:03d}", line))
    return claims


def _load_jsonl_claims(path: Path) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index, raw_line in enumerate(handle, start=1):
            payload = json.loads(raw_line)
            text = str(payload.get("claim_text") or payload.get("text") or "").strip()
            if not text:
                continue
            segment_id = str(payload.get("segment_id") or f"segment_{index:03d}")
            claims.append((segment_id, text))
    return claims
