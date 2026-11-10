"""Steps shared by the command line and the MCP server."""
from __future__ import annotations

from .dedupe import mark_duplicates
from .project import PROTOCOL_FILE, Protocol, ProjectError
from .sources import get_source
from .store import ABSTRACT, EXCLUDE, FULLTEXT, MAYBE, UNAVAILABLE, Decision, Store


def run_searches(store: Store, protocol: Protocol, again: bool = False, log=print) -> None:
    if not protocol.searches:
        raise ProjectError(f"No searches in {PROTOCOL_FILE}. Add one, or run `papersift plan`.")
    already = {(s["source"], s["query"]) for s in store.searches()}
    for entry in protocol.searches:
        name, query = str(entry.get("source", "")), str(entry.get("query", ""))
        if (name, query) in already and not again:
            log(f"{name}: already run, skipping (use --again to rerun)")
            continue
        source = get_source(name)
        limit = int(entry.get("max_results") or 2000)
        total = source.count(query, protocol.year_from, protocol.year_to)
        log(f"{name}: {total} records match")
        if total > limit:
            log(f"  only the first {limit} will be fetched; raise max_results or narrow the query")
        records = list(source.search(query, protocol.year_from, protocol.year_to, limit))
        added = store.add_records(records)
        store.log_search(name, query, total, added)
        log(f"  fetched {len(records)}, {added} new")
    log(f"Duplicates marked: {mark_duplicates(store)}")


def decide(store: Store, record_id: int, stage: str, reviewer: str, decision: str,
           reason: str | None = None, note: str | None = None) -> None:
    if record_id not in {r.id for r in store.eligible(stage)}:
        raise ProjectError(f"Record {record_id} is not at the {stage} stage.")
    if stage == FULLTEXT and decision == EXCLUDE and not reason:
        raise ProjectError("Excluding at full text needs a reason.")
    if {MAYBE: FULLTEXT, UNAVAILABLE: ABSTRACT}.get(decision) == stage:
        raise ProjectError(f"'{decision}' is not a {stage} decision.")
    store.decide(Decision(record_id, stage, reviewer, decision, reason, note))
