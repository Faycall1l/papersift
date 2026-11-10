"""arXiv: preprints in physics, mathematics, computer science and related fields.

Queries use arXiv syntax with field prefixes, e.g.
(ti:"language model" OR abs:"language model") AND abs:planning.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator

from ..store import Record
from .http import get

URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
TOTAL = "{http://a9.com/-/spec/opensearch/1.1/}totalResults"
BATCH = 200


def _query(query: str, year_from: int | None, year_to: int | None) -> str:
    if not (year_from or year_to):
        return query
    return (f"({query}) AND submittedDate:"
            f"[{year_from or 1991}01010000 TO {year_to or 2100}12312359]")


def _fetch(query: str, start: int, size: int):
    xml = get(URL, {
        "search_query": query, "start": start, "max_results": size,
        "sortBy": "submittedDate", "sortOrder": "descending",
    })
    return ET.fromstring(xml)


def count(query: str, year_from: int | None = None, year_to: int | None = None) -> int:
    return int(_fetch(_query(query, year_from, year_to), 0, 1).findtext(TOTAL) or 0)


def with_title(query: str, title: str) -> str:
    return f'({query}) AND ti:"{title}"'


def search(query: str, year_from: int | None = None, year_to: int | None = None,
           limit: int = 2000) -> Iterator[Record]:
    full = _query(query, year_from, year_to)
    start = 0
    while start < limit:
        entries = _fetch(full, start, min(BATCH, limit - start)).findall(ATOM + "entry")
        if not entries:
            return
        for entry in entries:
            yield _record(entry)
        start += len(entries)
        time.sleep(3)


def _record(entry) -> Record:
    link = entry.findtext(ATOM + "id") or ""
    arxiv_id = re.sub(r"v\d+$", "", link.rsplit("/abs/", 1)[-1])
    pdf = next((l.get("href") for l in entry.findall(ATOM + "link") if l.get("title") == "pdf"), None)
    published = entry.findtext(ATOM + "published") or ""
    return Record(
        source="arxiv",
        source_id=arxiv_id,
        doi=entry.findtext("{http://arxiv.org/schemas/atom}doi") or f"10.48550/arXiv.{arxiv_id}",
        title=entry.findtext(ATOM + "title") or "",
        abstract=entry.findtext(ATOM + "summary"),
        authors=[a.findtext(ATOM + "name") or "" for a in entry.findall(ATOM + "author")],
        year=int(published[:4]) if published[:4].isdigit() else None,
        venue="arXiv",
        url=link,
        pdf_url=pdf,
    )
