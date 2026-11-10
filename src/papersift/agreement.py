from __future__ import annotations

from collections import Counter

from .store import CONSENSUS, Store


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
