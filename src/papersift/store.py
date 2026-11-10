from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ABSTRACT = "abstract"
FULLTEXT = "fulltext"
STAGES = (ABSTRACT, FULLTEXT)

INCLUDE = "include"
EXCLUDE = "exclude"
MAYBE = "maybe"
UNAVAILABLE = "unavailable"  # full text could not be obtained
PENDING = "pending"
CONFLICT = "conflict"

CONSENSUS = "consensus"  # reviewer name used when a disagreement is settled

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT,
    doi TEXT,
    title TEXT NOT NULL,
    abstract TEXT,
    authors TEXT,
    year INTEGER,
    venue TEXT,
    url TEXT,
    pdf_url TEXT,
    pdf_path TEXT,
    duplicate_of INTEGER REFERENCES records(id),
    UNIQUE (source, source_id)
);
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    query TEXT NOT NULL,
    run_at TEXT NOT NULL,
    found INTEGER NOT NULL,
    added INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    record_id INTEGER NOT NULL REFERENCES records(id),
    stage TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    note TEXT,
    decided_at TEXT NOT NULL,
    PRIMARY KEY (record_id, stage, reviewer)
);
"""


@dataclass
class Record:
    source: str
    title: str
    source_id: str | None = None
    doi: str | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    url: str | None = None
    pdf_url: str | None = None
    pdf_path: str | None = None
    id: int = 0
    duplicate_of: int | None = None


@dataclass
class Decision:
    record_id: int
    stage: str
    reviewer: str
    decision: str
    reason: str | None = None
    note: str | None = None


def clean_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.I)
    return doi.lower() or None


def is_ai(reviewer: str) -> bool:
    return reviewer == "ai" or reviewer.startswith("ai:")


def settle(decisions: list[Decision]) -> str:
    """The outcome of one record at one stage, given every reviewer's decision."""
    for d in decisions:
        if d.reviewer == CONSENSUS:
            return d.decision
    votes = {d.decision for d in decisions}
    if not votes:
        return PENDING
    if len(votes) == 1 and MAYBE not in votes:
        return votes.pop()
    return CONFLICT


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def add_records(self, records: list[Record]) -> int:
        added = 0
        for r in records:
            title = " ".join((r.title or "").split())
            if not title:
                continue
            cur = self.db.execute(
                "INSERT OR IGNORE INTO records "
                "(source, source_id, doi, title, abstract, authors, year, venue, url, pdf_url) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    r.source,
                    r.source_id,
                    clean_doi(r.doi),
                    title,
                    " ".join(r.abstract.split()) if r.abstract else None,
                    "; ".join(r.authors),
                    r.year,
                    r.venue,
                    r.url,
                    r.pdf_url,
                ),
            )
            added += cur.rowcount
        self.db.commit()
        return added

    def log_search(self, source: str, query: str, found: int, added: int) -> None:
        self.db.execute(
            "INSERT INTO searches (source, query, run_at, found, added) VALUES (?,?,?,?,?)",
            (source, query, _now(), found, added),
        )
        self.db.commit()

    def searches(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM searches ORDER BY id").fetchall()

    def records(self, include_duplicates: bool = False) -> list[Record]:
        where = "" if include_duplicates else "WHERE duplicate_of IS NULL"
        rows = self.db.execute(f"SELECT * FROM records {where} ORDER BY id").fetchall()
        return [_record(row) for row in rows]

    def record(self, record_id: int) -> Record | None:
        row = self.db.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        return _record(row) if row else None

    def update_record(self, record_id: int, **fields) -> None:
        sets = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE records SET {sets} WHERE id = ?", (*fields.values(), record_id))
        self.db.commit()

    def decide(self, d: Decision) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?)",
            (d.record_id, d.stage, d.reviewer, d.decision, d.reason, d.note, _now()),
        )
        self.db.commit()

    def undo(self, record_id: int, stage: str, reviewer: str) -> None:
        self.db.execute(
            "DELETE FROM decisions WHERE record_id = ? AND stage = ? AND reviewer = ?",
            (record_id, stage, reviewer),
        )
        self.db.commit()

    def decisions(self, stage: str) -> dict[int, list[Decision]]:
        rows = self.db.execute(
            "SELECT record_id, stage, reviewer, decision, reason, note "
            "FROM decisions WHERE stage = ? ORDER BY decided_at",
            (stage,),
        ).fetchall()
        by_record: dict[int, list[Decision]] = {}
        for row in rows:
            by_record.setdefault(row["record_id"], []).append(Decision(*row))
        return by_record

    def eligible(self, stage: str) -> list[Record]:
        """Records that reach a stage: all unique records, then abstract includes."""
        records = self.records()
        if stage == ABSTRACT:
            return records
        passed = self.outcomes(ABSTRACT)
        return [r for r in records if passed.get(r.id) == INCLUDE]

    def outcomes(self, stage: str) -> dict[int, str]:
        decisions = self.decisions(stage)
        return {r.id: settle(decisions.get(r.id, [])) for r in self.eligible(stage)}

    def todo(self, stage: str, reviewer: str) -> list[Record]:
        """Records this reviewer has not decided yet."""
        decisions = self.decisions(stage)
        done = {
            rid for rid, ds in decisions.items()
            if any(d.reviewer in (reviewer, CONSENSUS) for d in ds)
        }
        return [r for r in self.eligible(stage) if r.id not in done]


def _record(row: sqlite3.Row) -> Record:
    data = dict(row)
    data["authors"] = [a for a in (data["authors"] or "").split("; ") if a]
    return Record(**data)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
