"""Semantic Scholar: all fields, strong in computer science.

Queries match title and abstract. Use + for AND, | for OR, - for NOT,
"quoted phrases" and parentheses, e.g. ("language model" | LLM) + planning.
Works without a key but is often rate limited; set S2_API_KEY for reliable use.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator

from ..store import Record
from .http import get

URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
FIELDS = "title,abstract,year,venue,authors,externalIds,openAccessPdf,url"


def _page(query: str, year_from: int | None, year_to: int | None, fields: str,
          token: str | None = None) -> dict:
    key = os.environ.get("S2_API_KEY")
    years = f"{year_from or ''}-{year_to or ''}" if year_from or year_to else None
    return json.loads(get(
        URL, {"query": query, "fields": fields, "year": years, "token": token},
        headers={"x-api-key": key} if key else None,
    ))


def count(query: str, year_from: int | None = None, year_to: int | None = None) -> int:
    return int(_page(query, year_from, year_to, "title")["total"])


def with_title(query: str, title: str) -> str:
    return f'({query}) + "{title}"'


def search(query: str, year_from: int | None = None, year_to: int | None = None,
           limit: int = 2000) -> Iterator[Record]:
    token, fetched = None, 0
    while fetched < limit:
        page = _page(query, year_from, year_to, FIELDS, token)
        for paper in page.get("data") or []:
            if fetched >= limit:
                return
            fetched += 1
            yield _record(paper)
        token = page.get("token")
        if not token:
            return


def _record(paper: dict) -> Record:
    ids = paper.get("externalIds") or {}
    doi = ids.get("DOI") or (f"10.48550/arXiv.{ids['ArXiv']}" if ids.get("ArXiv") else None)
    return Record(
        source="semanticscholar",
        source_id=paper["paperId"],
        doi=doi,
        title=paper.get("title") or "",
        abstract=paper.get("abstract"),
        authors=[a.get("name", "") for a in paper.get("authors") or []],
        year=paper.get("year"),
        venue=paper.get("venue") or None,
        url=paper.get("url"),
        pdf_url=(paper.get("openAccessPdf") or {}).get("url") or None,
    )
