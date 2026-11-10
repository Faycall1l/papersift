from __future__ import annotations

import csv
import io
import re

from .store import ABSTRACT, CONSENSUS, FULLTEXT, INCLUDE, Record, Store

FORMATS = ("csv", "ris", "bib")


def included(store: Store) -> list[Record]:
    outcomes = store.outcomes(FULLTEXT)
    return [r for r in store.eligible(FULLTEXT) if outcomes[r.id] == INCLUDE]


def to_csv(store: Store, only_included: bool = False) -> str:
    """One row per record with the outcome of each stage."""
    abstract, fulltext = store.outcomes(ABSTRACT), store.outcomes(FULLTEXT)
    reasons = {}
    for record_id, votes in store.decisions(FULLTEXT).items():
        final = next((d for d in votes if d.reviewer == CONSENSUS), votes[-1])
        reasons[record_id] = final.reason or ""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "title", "authors", "year", "venue", "doi", "url", "source",
                     "duplicate_of", "abstract_stage", "fulltext_stage", "fulltext_reason", "abstract"])
    for r in store.records(include_duplicates=not only_included):
        if only_included and fulltext.get(r.id) != INCLUDE:
            continue
        writer.writerow([r.id, r.title, "; ".join(r.authors), r.year or "", r.venue or "",
                         r.doi or "", r.url or "", r.source, r.duplicate_of or "",
                         abstract.get(r.id, ""), fulltext.get(r.id, ""),
                         reasons.get(r.id, ""), r.abstract or ""])
    return out.getvalue()


def to_ris(records: list[Record]) -> str:
    lines = []
    for r in records:
        lines.append("TY  - JOUR")
        lines.append(f"TI  - {r.title}")
        lines += [f"AU  - {a}" for a in r.authors]
        for tag, value in (("PY", r.year), ("JO", r.venue), ("DO", r.doi), ("UR", r.url), ("AB", r.abstract)):
            if value:
                lines.append(f"{tag}  - {value}")
        lines += ["ER  - ", ""]
    return "\n".join(lines)


def to_bibtex(records: list[Record]) -> str:
    entries, used = [], set()
    for r in records:
        surname = re.sub(r"[^A-Za-z]", "", r.authors[0].split(",")[0].split()[-1]) if r.authors else "anon"
        word = next((w for w in re.findall(r"[A-Za-z]+", r.title) if len(w) > 3), "paper")
        key = base = f"{surname}{r.year or ''}{word}".lower()
        n = 1
        while key in used:
            n += 1
            key = f"{base}{n}"
        used.add(key)
        fields = {
            "title": r.title, "author": " and ".join(r.authors), "year": r.year,
            "journal": r.venue, "doi": r.doi, "url": r.url,
        }
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields.items() if value)
        entries.append(f"@article{{{key},\n{body}\n}}")
    return "\n\n".join(entries) + "\n"
