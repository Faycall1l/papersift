"""OpenAlex: all disciplines, about 250 million works.

Queries match title and abstract. Use AND, OR, NOT (capitals), "quoted phrases"
and parentheses. Works without a key at a low shared daily limit; set
OPENALEX_API_KEY (free at openalex.org/settings/api) for more.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator

from ..store import Record
from .http import get

URL = "https://api.openalex.org/works"
FIELDS = "id,doi,title,publication_year,authorships,primary_location,open_access,abstract_inverted_index"


def _params(query: str, year_from: int | None, year_to: int | None) -> dict:
    filters = [f"title_and_abstract.search:{query}"]
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    return {"filter": ",".join(filters), "api_key": os.environ.get("OPENALEX_API_KEY")}


def count(query: str, year_from: int | None = None, year_to: int | None = None) -> int:
    params = {**_params(query, year_from, year_to), "per-page": 1, "select": "id"}
    return json.loads(get(URL, params))["meta"]["count"]


def with_title(query: str, title: str) -> str:
    return f'({query}) AND "{title}"'


def works(filters: str, limit: int, select: str = FIELDS) -> Iterator[dict]:
    """Every work matching an OpenAlex filter string, up to a limit."""
    params = {"filter": filters, "per-page": 200, "select": select, "cursor": "*",
              "api_key": os.environ.get("OPENALEX_API_KEY")}
    fetched = 0
    while params["cursor"] and fetched < limit:
        page = json.loads(get(URL, params))
        for work in page["results"]:
            if fetched >= limit:
                return
            fetched += 1
            yield work
        params["cursor"] = page["meta"].get("next_cursor") if page["results"] else None


def search(query: str, year_from: int | None = None, year_to: int | None = None,
           limit: int = 2000) -> Iterator[Record]:
    filters = _params(query, year_from, year_to)["filter"]
    return (to_record(work) for work in works(filters, limit))


def to_record(work: dict, source: str = "openalex") -> Record:
    location = work.get("primary_location") or {}
    return Record(
        source=source,
        source_id=work["id"].rsplit("/", 1)[-1],
        doi=work.get("doi"),
        title=work.get("title") or "",
        abstract=_abstract(work.get("abstract_inverted_index")),
        authors=[a["author"]["display_name"] for a in work.get("authorships") or [] if a.get("author")],
        year=work.get("publication_year"),
        venue=(location.get("source") or {}).get("display_name"),
        url=location.get("landing_page_url") or work["id"],
        pdf_url=location.get("pdf_url") or (work.get("open_access") or {}).get("oa_url"),
    )


def _abstract(index: dict | None) -> str | None:
    if not index:
        return None
    words = sorted((pos, word) for word, positions in index.items() for pos in positions)
    return " ".join(word for _, word in words)
