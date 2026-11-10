"""Move records between a review and a Zotero library.

Needs ZOTERO_API_KEY (zotero.org/settings/keys, with write access). Uses your
personal library unless a group id is given.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .project import ProjectError
from .sources.http import get
from .store import (ABSTRACT, EXCLUDE, FULLTEXT, INCLUDE, MAYBE, UNAVAILABLE, Decision, Record,
                    Store)

API = "https://api.zotero.org"
SYSTEM = "zotero"
PAGE = 100
WRITE_BATCH = 50
TAGS = {"include": INCLUDE, "exclude": EXCLUDE, "maybe": MAYBE, "no full text": UNAVAILABLE}


class Library:
    def __init__(self, group: str | None = None):
        key = os.environ.get("ZOTERO_API_KEY")
        if not key:
            raise ProjectError("Set ZOTERO_API_KEY. Create a key with write access at "
                               "zotero.org/settings/keys.")
        self.headers = {"Zotero-API-Key": key, "Zotero-API-Version": "3"}
        group = group or os.environ.get("ZOTERO_GROUP")
        if group:
            self.prefix = f"{API}/groups/{group}"
        else:
            user = json.loads(get(f"{API}/keys/current", headers=self.headers))["userID"]
            self.prefix = f"{API}/users/{user}"

    def read(self, path: str) -> list[dict]:
        items, start = [], 0
        while True:
            page = json.loads(get(self.prefix + path, {"limit": PAGE, "start": start},
                                  headers=self.headers))
            items += page
            if len(page) < PAGE:
                return items
            start += PAGE

    def write(self, path: str, objects: list[dict]) -> list[str | None]:
        """Create objects and return their new keys, None where Zotero refused one."""
        keys: list[str | None] = []
        for start in range(0, len(objects), WRITE_BATCH):
            batch = objects[start:start + WRITE_BATCH]
            reply = json.loads(get(
                self.prefix + path, data=json.dumps(batch).encode(), method="POST",
                headers={**self.headers, "Content-Type": "application/json"}))
            keys += [reply.get("success", {}).get(str(i)) for i in range(len(batch))]
        return keys

    def collection(self, name: str, create: bool = False) -> str:
        for collection in self.read("/collections"):
            if collection["data"]["name"] == name:
                return collection["key"]
        if not create:
            raise ProjectError(f"No Zotero collection named '{name}'.")
        key = self.write("/collections", [{"name": name}])[0]
        if not key:
            raise ProjectError(f"Zotero would not create the collection '{name}'.")
        return key

    def items(self, collection: str | None) -> list[dict]:
        path = f"/collections/{collection}/items/top" if collection else "/items/top"
        return [i for i in self.read(path) if i["data"].get("itemType") not in ("attachment", "note")]

    def add_to_collection(self, item_key: str, collection: str) -> None:
        item = json.loads(get(f"{self.prefix}/items/{item_key}", headers=self.headers))
        current = item["data"].get("collections", [])
        if collection in current:
            return
        get(f"{self.prefix}/items/{item_key}", method="PATCH",
            data=json.dumps({"collections": current + [collection]}).encode(),
            headers={**self.headers, "Content-Type": "application/json",
                     "If-Unmodified-Since-Version": str(item["version"])})

    def pdf(self, item_key: str) -> bytes | None:
        for child in self.read(f"/items/{item_key}/children"):
            data = child["data"]
            if data.get("contentType") == "application/pdf" and data.get("linkMode", "").startswith("imported"):
                content = get(f"{self.prefix}/items/{child['key']}/file", headers=self.headers)
                if content.startswith(b"%PDF"):
                    return content
        return None


def to_item(record: Record, collection: str) -> dict:
    creators = []
    for name in record.authors:
        if "," in name:
            last, first = (part.strip() for part in name.split(",", 1))
            creators.append({"creatorType": "author", "lastName": last, "firstName": first})
        else:
            creators.append({"creatorType": "author", "name": name})
    return {
        "itemType": "journalArticle",
        "title": record.title,
        "creators": creators,
        "abstractNote": record.abstract or "",
        "date": str(record.year or ""),
        "publicationTitle": record.venue or "",
        "DOI": record.doi or "",
        "url": record.url or "",
        "collections": [collection],
    }


def from_item(item: dict) -> Record:
    data = item["data"]
    authors = [c.get("name") or f"{c.get('lastName', '')}, {c.get('firstName', '')}".strip(", ")
               for c in data.get("creators", []) if c.get("creatorType") == "author"]
    year = re.search(r"(19|20)\d\d", data.get("date") or "")
    return Record(
        source=SYSTEM,
        source_id=item["key"],
        title=data.get("title") or "",
        abstract=data.get("abstractNote") or None,
        authors=[a for a in authors if a],
        year=int(year.group()) if year else None,
        venue=data.get("publicationTitle") or data.get("proceedingsTitle") or None,
        doi=data.get("DOI") or None,
        url=data.get("url") or None,
    )


def decision_from_tags(tags: list[str], stage: str) -> tuple[str, str | None] | None:
    """Read a decision from tags such as "include" or "exclude: wrong design"."""
    allowed = {ABSTRACT: (INCLUDE, EXCLUDE, MAYBE), FULLTEXT: (INCLUDE, EXCLUDE, UNAVAILABLE)}[stage]
    found = []
    for tag in tags:
        word, _, reason = tag.partition(":")
        decision = TAGS.get(word.strip().lower())
        if decision in allowed:
            found.append((decision, reason.strip() or None))
    return found[0] if len(found) == 1 else None


def push(store: Store, library: Library, records: list[Record], collection_name: str) -> dict:
    collection = library.collection(collection_name, create=True)
    linked = store.links(SYSTEM)
    new = [r for r in records if r.id not in linked]
    keys = library.write("/items", [to_item(r, collection) for r in new])
    for record, key in zip(new, keys):
        if key:
            store.link(record.id, SYSTEM, key)
    existing = [r for r in records if r.id in linked]
    for record in existing:
        library.add_to_collection(linked[record.id], collection)
    return {"created": sum(1 for k in keys if k), "refused": sum(1 for k in keys if not k),
            "already": len(existing)}


def pull(store: Store, library: Library, collection_name: str | None) -> dict:
    collection = library.collection(collection_name) if collection_name else None
    known = set(store.links(SYSTEM).values())
    records = [from_item(i) for i in library.items(collection) if i["key"] not in known]
    added = store.add_records(records)
    rows = store.db.execute("SELECT id, source_id FROM records WHERE source = ?", (SYSTEM,))
    for row in rows.fetchall():
        store.link(row["id"], SYSTEM, row["source_id"])
    store.log_search(SYSTEM, collection_name or "whole library", len(records), added)
    return {"found": len(records), "added": added}


def read_decisions(store: Store, library: Library, collection_name: str, stage: str,
                   reviewer: str) -> dict:
    by_key = {key: record_id for record_id, key in store.links(SYSTEM).items()}
    eligible = {r.id for r in store.eligible(stage)}
    recorded = unclear = 0
    for item in library.items(library.collection(collection_name)):
        record_id = by_key.get(item["key"])
        tags = [t["tag"] for t in item["data"].get("tags", [])]
        if record_id not in eligible or not tags:
            continue
        decision = decision_from_tags(tags, stage)
        if decision is None:
            unclear += any(t.partition(":")[0].strip().lower() in TAGS for t in tags)
            continue
        if stage == FULLTEXT and decision[0] == EXCLUDE and not decision[1]:
            unclear += 1
            continue
        store.decide(Decision(record_id, stage, reviewer, *decision))
        recorded += 1
    return {"recorded": recorded, "unclear": unclear}


def fetch_pdfs(store: Store, library: Library, root: Path) -> int:
    folder = root / "fulltext"
    folder.mkdir(exist_ok=True)
    linked, got = store.links(SYSTEM), 0
    for record in store.eligible(FULLTEXT):
        path = folder / f"{record.id}.pdf"
        if path.exists() or record.id not in linked:
            continue
        content = library.pdf(linked[record.id])
        if content:
            path.write_bytes(content)
            store.update_record(record.id, pdf_path=str(path.relative_to(root)))
            got += 1
    return got
