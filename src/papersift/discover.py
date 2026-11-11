"""Finding papers the boolean searches may have missed."""
from __future__ import annotations

from .dedupe import title_key
from .project import ProjectError
from .sources import semanticscholar
from .store import ABSTRACT, EXCLUDE, INCLUDE, Record, Store, clean_doi

MAX_SEEDS = 100


def in_review(store: Store, found: list[Record]) -> list[bool]:
    """For each found record, whether the review already has it, by DOI or title."""
    dois, titles = set(), set()
    for record in store.records(include_duplicates=True):
        if record.doi:
            dois.add(record.doi)
        titles.add(title_key(record.title))
    return [clean_doi(r.doi) in dois or title_key(r.title) in titles for r in found]


def similar(store: Store, limit: int = 100) -> tuple[list[Record], int, int]:
    """Recommendations from the abstract-stage decisions: the records, and how many
    included and excluded records they were based on."""
    outcomes = store.outcomes(ABSTRACT)
    ids = {INCLUDE: [], EXCLUDE: []}
    for record in store.eligible(ABSTRACT):
        paper = semanticscholar.paper_id(record)
        if paper and outcomes[record.id] in ids:
            ids[outcomes[record.id]].append(paper)
    liked, disliked = ids[INCLUDE][-MAX_SEEDS:], ids[EXCLUDE][-MAX_SEEDS:]
    if not liked:
        raise ProjectError("No included records with a DOI to start from. Screen some first.")
    return semanticscholar.recommend(liked, disliked, limit), len(liked), len(disliked)
