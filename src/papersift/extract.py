"""Data extraction: the same questions answered for every included study."""
from __future__ import annotations

import csv
import io

from . import screen
from .export import included
from .project import PROTOCOL_FILE, Protocol, ProjectError
from .store import Store, is_ai

QUIT = ":q"


def require_fields(protocol: Protocol) -> None:
    if not protocol.extraction:
        raise ProjectError(f"Add an `extraction:` list to {PROTOCOL_FILE} first.")


def settled(rows: list) -> tuple[str, bool]:
    """The value to report for one field, and whether only AI supplied it."""
    human = [r for r in rows if not is_ai(r["reviewer"])]
    return (human or rows)[-1]["value"], not human


def run(store: Store, protocol: Protocol, reviewer: str) -> None:
    require_fields(protocol)
    values = store.values()
    for record in included(store):
        mine = {f for f, rows in values.get(record.id, {}).items()
                if any(r["reviewer"] == reviewer for r in rows)}
        fields = [f for f in protocol.extraction if f["name"] not in mine]
        if not fields:
            continue
        screen.show(record, f"id {record.id}")
        print(f"Type an answer. Enter keeps the suggestion or leaves it empty. {QUIT} to stop.\n")
        for field in fields:
            suggestion = next((r for r in values.get(record.id, {}).get(field["name"], [])
                               if is_ai(r["reviewer"])), None)
            print(field["question"])
            if field["choices"]:
                print("  Choices: " + ", ".join(field["choices"]))
            if suggestion:
                print(f"  AI: {suggestion['value']}")
                if suggestion["quote"]:
                    print(f'  "{suggestion["quote"]}"')
            try:
                answer = input(f"{field['name']} > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if answer == QUIT:
                return
            if not answer and suggestion:
                answer = suggestion["value"]
            if answer:
                store.set_value(record.id, field["name"], reviewer, answer)
    print("Nothing left to extract. `papersift export --extraction` writes the table.")


def table(store: Store, protocol: Protocol) -> str:
    """One row per included study, one column per extraction field."""
    require_fields(protocol)
    names = [f["name"] for f in protocol.extraction]
    values = store.values()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "title", "authors", "year", "doi", *names, "unchecked_ai_fields"])
    for record in included(store):
        row, unchecked = [], []
        for name in names:
            rows = values.get(record.id, {}).get(name)
            value, ai_only = settled(rows) if rows else ("", False)
            row.append(value)
            if ai_only:
                unchecked.append(name)
        writer.writerow([record.id, record.title, "; ".join(record.authors), record.year or "",
                         record.doi or "", *row, "; ".join(unchecked)])
    return out.getvalue()
