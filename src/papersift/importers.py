"""Read records exported from other tools: RIS, BibTeX and CSV."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .store import Record

CSV_COLUMNS = {
    "title": ("title", "article title", "document title", "ti"),
    "abstract": ("abstract", "abstract note", "ab"),
    "authors": ("authors", "author", "author full names", "au"),
    "year": ("year", "publication year", "py", "date"),
    "doi": ("doi", "di"),
    "venue": ("source title", "journal", "publication title", "venue", "so"),
    "url": ("url", "link"),
}


def read_file(path: Path) -> list[Record]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if suffix in (".ris", ".nbib", ".txt"):
        records = read_ris(text)
    elif suffix in (".bib", ".bibtex"):
        records = read_bibtex(text)
    elif suffix in (".csv", ".tsv"):
        records = read_csv(text, "\t" if suffix == ".tsv" else ",")
    else:
        raise ValueError(f"Cannot read {path.name}: expected .ris, .bib, .csv or .tsv")
    for i, record in enumerate(records):
        record.source = f"file:{path.name}"
        record.source_id = record.doi or str(i)
    return records


def read_ris(text: str) -> list[Record]:
    records, fields, last = [], {}, None
    for line in text.splitlines():
        match = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", line)
        if not match:
            if last and line.strip():
                fields[last][-1] += " " + line.strip()
            continue
        tag, value = match.group(1), match.group(2).strip()
        if tag == "ER":
            if fields:
                records.append(_from_ris(fields))
            fields, last = {}, None
            continue
        fields.setdefault(tag, []).append(value)
        last = tag
    if fields:
        records.append(_from_ris(fields))
    return records


def _from_ris(fields: dict) -> Record:
    def first(*tags):
        return next((fields[t][0] for t in tags if fields.get(t)), None)

    return Record(
        source="file",
        title=first("TI", "T1") or "",
        abstract=first("AB", "N2"),
        authors=fields.get("AU") or fields.get("A1") or [],
        year=_year(first("PY", "Y1", "DA")),
        doi=first("DO"),
        venue=first("JO", "JF", "T2", "J2"),
        url=first("UR"),
    )


def read_bibtex(text: str) -> list[Record]:
    records = []
    for match in re.finditer(r"@(\w+)\s*\{", text):
        if match.group(1).lower() in ("comment", "string", "preamble"):
            continue
        body = _balanced(text, match.end() - 1)
        fields = _bibtex_fields(body)
        authors = [a.strip() for a in re.split(r"\s+and\s+", fields.get("author", "")) if a.strip()]
        records.append(Record(
            source="file",
            title=fields.get("title", ""),
            abstract=fields.get("abstract"),
            authors=authors,
            year=_year(fields.get("year")),
            doi=fields.get("doi"),
            venue=fields.get("journal") or fields.get("booktitle"),
            url=fields.get("url"),
        ))
    return records


def _balanced(text: str, open_at: int) -> str:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    return text[open_at + 1:]


def _bibtex_fields(body: str) -> dict:
    fields, i = {}, body.find(",") + 1
    while True:
        match = re.compile(r"\s*([\w-]+)\s*=\s*").match(body, i)
        if not match:
            return fields
        name, i = match.group(1).lower(), match.end()
        if i < len(body) and body[i] == "{":
            value = _balanced(body, i)
            i += len(value) + 2
        elif i < len(body) and body[i] == '"':
            end = body.find('"', i + 1)
            end = len(body) if end == -1 else end
            value, i = body[i + 1:end], end + 1
        else:
            end = body.find(",", i)
            end = len(body) if end == -1 else end
            value, i = body[i:end], end
        fields[name] = " ".join(value.replace("{", "").replace("}", "").split())
        i = body.find(",", i) + 1
        if i == 0:
            return fields


def read_csv(text: str, delimiter: str = ",") -> list[Record]:
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    headers = {(h or "").strip().lower(): h for h in reader.fieldnames or []}
    column = {
        name: next((headers[a] for a in aliases if a in headers), None)
        for name, aliases in CSV_COLUMNS.items()
    }
    if not column["title"]:
        raise ValueError("The CSV file needs a column named 'title'.")
    records = []
    for row in reader:
        def get(name):
            return (row.get(column[name]) or "").strip() if column[name] else ""

        records.append(Record(
            source="file",
            title=get("title"),
            abstract=get("abstract") or None,
            authors=[a.strip() for a in re.split(r";| and ", get("authors")) if a.strip()],
            year=_year(get("year")),
            doi=get("doi") or None,
            venue=get("venue") or None,
            url=get("url") or None,
        ))
    return records


def _year(value: str | None) -> int | None:
    match = re.search(r"(19|20)\d\d", value or "")
    return int(match.group()) if match else None
