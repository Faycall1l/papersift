from __future__ import annotations

from collections import Counter

from .store import CONSENSUS, EXCLUDE, FULLTEXT, INCLUDE, Store


def reviewers(store: Store, stage: str) -> list[str]:
    names = {d.reviewer for votes in store.decisions(stage).values() for d in votes}
    return sorted(names - {CONSENSUS})


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Agreement between two reviewers beyond chance. None if undefined."""
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(first[label] * second[label] for label in first) / (n * n)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def compare(store: Store, stage: str, a: str, b: str) -> dict:
    pairs = []
    for votes in store.decisions(stage).values():
        by_reviewer = {d.reviewer: d.decision for d in votes}
        if a in by_reviewer and b in by_reviewer:
            pairs.append((by_reviewer[a], by_reviewer[b]))
    return {
        "both": len(pairs),
        "agreed": sum(x == y for x, y in pairs),
        "kappa": cohen_kappa(pairs),
        "table": Counter(pairs),
    }


def against(store: Store, stage: str, reviewer: str, reference: str) -> dict:
    """Score one reviewer against another taken as correct.

    A "maybe" from the reviewer counts as passing the record on. Recall is the
    share of the reference's includes that the reviewer passed on.
    """
    counts = {"kept": 0, "missed": 0, "extra": 0, "dropped": 0}
    missed = []
    votes_by_record = store.decisions(stage)
    for record_id, votes in votes_by_record.items():
        by_reviewer = {d.reviewer: d.decision for d in votes}
        if reviewer not in by_reviewer or by_reviewer.get(reference) not in (INCLUDE, EXCLUDE):
            continue
        relevant = by_reviewer[reference] == INCLUDE
        passed = by_reviewer[reviewer] != EXCLUDE
        counts["kept" if relevant and passed else "missed" if relevant
               else "extra" if passed else "dropped"] += 1
        if relevant and not passed:
            missed.append(record_id)

    final = {record_id for record_id, votes in store.decisions(FULLTEXT).items()
             if any(d.reviewer == reference and d.decision == INCLUDE for d in votes)}
    judged = {record_id for record_id, votes in votes_by_record.items()
              if any(d.reviewer == reviewer for d in votes)}
    final_missed = sorted(final & set(missed))

    def share(part: int, whole: int) -> float | None:
        return part / whole if whole else None

    total = sum(counts.values())
    return {
        **counts, "both": total, "missed_ids": missed,
        "recall": share(counts["kept"], counts["kept"] + counts["missed"]),
        "specificity": share(counts["dropped"], counts["dropped"] + counts["extra"]),
        "precision": share(counts["kept"], counts["kept"] + counts["extra"]),
        "work_saved": share(counts["dropped"] + counts["missed"], total),
        "final_includes": len(final & judged), "final_missed_ids": final_missed,
    }
