"""Citation chasing: the references of the included studies and the papers citing them."""
from __future__ import annotations

from collections import Counter

from .sources import openalex
from .store import Record, Store

SOURCE = "citations"
BATCH = 40


def _batches(items: list[str]):
    for start in range(0, len(items), BATCH):
        yield "|".join(items[start:start + BATCH])


def _short(openalex_id: str) -> str:
    return openalex_id.rsplit("/", 1)[-1]


def resolve(seeds: list[Record]) -> tuple[dict[str, list[str]], int]:
    """OpenAlex id and reference list for each seed, and how many could not be found."""
    by_id = [r.source_id for r in seeds if r.source in ("openalex", SOURCE) and r.source_id]
    by_doi = [r.doi for r in seeds if r.source not in ("openalex", SOURCE)
              and r.doi and not set(r.doi) & set(",|")]
    found: dict[str, list[str]] = {}
    for name, values in (("openalex", by_id), ("doi", by_doi)):
        for batch in _batches(values):
            for work in openalex.works(f"{name}:{batch}", len(values), "id,referenced_works"):
                found[_short(work["id"])] = [_short(ref) for ref in work.get("referenced_works") or []]
    return found, len(seeds) - len(found)


def chase(store: Store, seeds: list[Record], backward: bool = True, forward: bool = True,
          limit: int = 5000, min_links: int = 1) -> dict:
    """Add the works the seeds cite and the works citing them.

    min_links keeps only works connected to at least that many seeds.
    """
    found, unresolved = resolve(seeds)
    fields = openalex.FIELDS + ",referenced_works"
    works = {}
    if backward:
        references = sorted({ref for refs in found.values() for ref in refs} - set(found))
        for batch in _batches(references[:limit]):
            for work in openalex.works(f"openalex:{batch}", BATCH, fields):
                works[work["id"]] = work
    if forward:
        for batch in _batches(sorted(found)):
            for work in openalex.works(f"cites:{batch}", limit - len(works), fields):
                works[work["id"]] = work
    links = Counter(ref for refs in found.values() for ref in set(refs))
    for work in works.values():
        links[_short(work["id"])] += len(set(found) & {_short(r) for r in work.get("referenced_works") or []})
    records = [openalex.to_record(work, SOURCE) for work in works.values()
               if _short(work["id"]) not in found and links[_short(work["id"])] >= min_links]
    added = store.add_records(records)
    directions = " and ".join(d for d, on in (("backward", backward), ("forward", forward)) if on)
    query = f"{directions} from {len(found)} studies"
    if min_links > 1:
        query += f", linked to at least {min_links} of them"
    store.log_search(SOURCE, query, len(records), added)
    return {"seeds": len(found), "unresolved": unresolved, "found": len(records), "added": added}
