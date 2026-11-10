"""Europe PMC: life sciences, including PubMed, PubMed Central and preprints.

Queries use Europe PMC syntax, as on europepmc.org: AND, OR, NOT, "quoted phrases",
parentheses and field prefixes such as TITLE:"..." and ABSTRACT:"...".
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator

from ..store import Record
from .http import get

URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _query(query: str, year_from: int | None, year_to: int | None) -> str:
    if not (year_from or year_to):
        return query
    return f"({query}) AND PUB_YEAR:[{year_from or 1800} TO {year_to or 3000}]"


def _page(query: str, size: int, cursor: str = "*") -> dict:
    return json.loads(get(URL, {
        "query": query, "format": "json", "resultType": "core",
        "pageSize": size, "cursorMark": cursor,
    }))


def count(query: str, year_from: int | None = None, year_to: int | None = None) -> int:
    return int(_page(_query(query, year_from, year_to), 1)["hitCount"])


def with_title(query: str, title: str) -> str:
    return f'({query}) AND TITLE:"{title}"'


def search(query: str, year_from: int | None = None, year_to: int | None = None,
           limit: int = 2000) -> Iterator[Record]:
    full, cursor, fetched = _query(query, year_from, year_to), "*", 0
    while fetched < limit:
        page = _page(full, min(1000, limit - fetched), cursor)
        results = page.get("resultList", {}).get("result", [])
        for item in results:
            fetched += 1
            yield _record(item)
        next_cursor = page.get("nextCursorMark")
        if not results or not next_cursor or next_cursor == cursor:
            return
        cursor = next_cursor


def _record(item: dict) -> Record:
    links = (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []
    pdf = next((l["url"] for l in links
                if l.get("documentStyle") == "pdf" and l.get("availabilityCode") in ("OA", "F")), None)
    authors = [a.get("fullName", "") for a in (item.get("authorList") or {}).get("author") or []]
    year = str(item.get("pubYear") or "")
    return Record(
        source="europepmc",
        source_id=f"{item.get('source')}:{item.get('id')}",
        doi=item.get("doi"),
        title=item.get("title") or "",
        abstract=re.sub(r"<[^>]+>", " ", item.get("abstractText") or "") or None,
        authors=[a for a in authors if a],
        year=int(year) if year.isdigit() else None,
        venue=((item.get("journalInfo") or {}).get("journal") or {}).get("title"),
        url=f"https://europepmc.org/article/{item.get('source')}/{item.get('id')}",
        pdf_url=pdf,
    )
