from __future__ import annotations

import re
import unicodedata

from .store import Store

MIN_TITLE_KEY = 20  # shorter titles ("Editorial", "Introduction") are not matched by title


def title_key(title: str) -> str:
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plain.lower())


def mark_duplicates(store: Store) -> int:
    """Mark records that share a DOI or a title with an earlier record.

    The earliest record is kept and takes any abstract, DOI or PDF link
    it lacks from its duplicates.
    """
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
        if original is None:
            kept[record.id] = record
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
