"""PubMed: biomedicine and life sciences.

Queries use PubMed syntax, exactly as on pubmed.ncbi.nlm.nih.gov, e.g.
("heart failure"[tiab] OR "Heart Failure"[Mesh]) AND telemonitoring[tiab].
NCBI_API_KEY is optional and raises the rate limit.
"""
from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator

from ..store import Record
from .http import get

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
BATCH = 200
MAX_IDS = 9999  # esearch does not return more ids than this per query


def _esearch(query: str, year_from: int | None, year_to: int | None, retmax: int) -> dict:
    params = {
        "db": "pubmed", "term": query, "retmode": "json", "retmax": retmax,
        "api_key": os.environ.get("NCBI_API_KEY"),
    }
    if year_from or year_to:
        params.update(datetype="pdat", mindate=year_from or 1800, maxdate=year_to or 3000)
    return json.loads(get(BASE + "esearch.fcgi", params))["esearchresult"]


def count(query: str, year_from: int | None = None, year_to: int | None = None) -> int:
    return int(_esearch(query, year_from, year_to, 0)["count"])


def search(query: str, year_from: int | None = None, year_to: int | None = None,
           limit: int = 2000) -> Iterator[Record]:
    ids = _esearch(query, year_from, year_to, min(limit, MAX_IDS))["idlist"]
    pause = 0.15 if os.environ.get("NCBI_API_KEY") else 0.4
    for start in range(0, len(ids), BATCH):
        xml = get(BASE + "efetch.fcgi", {
            "db": "pubmed", "id": ",".join(ids[start:start + BATCH]), "retmode": "xml",
            "api_key": os.environ.get("NCBI_API_KEY"),
        })
        for article in ET.fromstring(xml).iter("PubmedArticle"):
            yield _record(article)
        time.sleep(pause)


def _text(node) -> str:
    return "".join(node.itertext()).strip() if node is not None else ""


def _record(article) -> Record:
    pmid = _text(article.find(".//PMID"))
    parts = []
    for section in article.iter("AbstractText"):
        label = section.get("Label")
        parts.append(f"{label}: {_text(section)}" if label else _text(section))
    authors = []
    for author in article.iter("Author"):
        name = " ".join(filter(None, [_text(author.find("ForeName")), _text(author.find("LastName"))]))
        authors.append(name or _text(author.find("CollectiveName")))
    doi = next((_text(i) for i in article.iter("ArticleId") if i.get("IdType") == "doi"), None)
    year = _text(article.find(".//JournalIssue/PubDate/Year")) or _text(article.find(".//PubDate/MedlineDate"))[:4]
    return Record(
        source="pubmed",
        source_id=pmid,
        doi=doi,
        title=_text(article.find(".//ArticleTitle")),
        abstract=" ".join(parts) or None,
        authors=[a for a in authors if a],
        year=int(year) if year.isdigit() else None,
        venue=_text(article.find(".//Journal/Title")) or None,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    )
