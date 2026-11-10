from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .store import Record, Store

SIMILAR = 0.9

MIN_TITLE_KEY = 20  # shorter titles ("Editorial", "Introduction") are not matched by title


def title_key(title: str) -> str:
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plain.lower())


def surname(record: Record) -> str:
    """Family name of the first author, from "Doe, Jane", "Jane Doe" or "Doe J"."""
    if not record.authors:
        return ""
    name = record.authors[0]
    if "," in name:
        family = name.split(",")[0]
    else:
        parts = name.split()
        initials = len(parts) > 1 and len(parts[-1]) <= 2 and parts[-1].isupper()
        family = parts[0] if initials else parts[-1] if parts else ""
    return title_key(family)


def same_work(a: Record, b: Record) -> bool:
    """True for records that look like one paper indexed twice, e.g. preprint and journal."""
    if a.year and b.year and abs(a.year - b.year) > 2:
        return False
    title_a, title_b = title_key(a.title), title_key(b.title)
    if re.findall(r"\d+", title_a) != re.findall(r"\d+", title_b):
        return False
    if SequenceMatcher(None, title_a, title_b).ratio() >= SIMILAR:
        return True
    start_a, start_b = title_key(a.abstract or "")[:400], title_key(b.abstract or "")[:400]
    return min(len(start_a), len(start_b)) >= 200 and \
        SequenceMatcher(None, start_a, start_b).ratio() >= SIMILAR


def mark_duplicates(store: Store) -> int:
    """Mark records that repeat an earlier record.

    Two records match if they share a DOI or a title, or if they have the same
    first author and a near-identical title or abstract. The earliest record is
    kept and takes any abstract, DOI or PDF link it lacks from its duplicates.
    """
    by_author: dict[str, list[Record]] = {}
    seen: dict[str, int] = {}
    kept = {}
    marked = 0
    for record in store.records():
        keys = []
        if record.doi:
            keys.append("doi:" + record.doi)
        key = title_key(record.title)
        if len(key) >= MIN_TITLE_KEY:
            keys.append("title:" + key)
        original = next((seen[k] for k in keys if k in seen), None)
        author = surname(record)
        if original is None and author:
            original = next((r.id for r in by_author.get(author, []) if same_work(r, record)), None)
        if original is None:
            kept[record.id] = record
            if author:
                by_author.setdefault(author, []).append(record)
            for k in keys:
                seen[k] = record.id
            continue
        store.update_record(record.id, duplicate_of=original)
        marked += 1
        keeper = kept[original]
        gaps = {
            name: getattr(record, name)
            for name in ("abstract", "doi", "pdf_url", "venue")
            if not getattr(keeper, name) and getattr(record, name)
        }
        if gaps:
            store.update_record(original, **gaps)
            for name, value in gaps.items():
                setattr(keeper, name, value)
        for k in keys:
            seen.setdefault(k, original)
    return marked
