"""Learn from screening decisions which records are likely to be relevant.

The model is naive Bayes over the words and word pairs of each title and
abstract. It is rebuilt from the decisions made so far, so the records still
to screen can be shown most-likely-relevant first.
"""
from __future__ import annotations

import math
import random
import re
from collections import Counter

from .store import Record

SMOOTHING = 0.5


def features(record: Record) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]{2,}", f"{record.title} {record.abstract or ''}".lower())
    return frozenset(words) | frozenset(f"{a} {b}" for a, b in zip(words, words[1:]))


class Ranker:
    def __init__(self):
        self.seen = {True: Counter(), False: Counter()}
        self.total = {True: 0, False: 0}

    def learn(self, terms: frozenset[str], relevant: bool) -> None:
        self.seen[relevant].update(terms)
        self.total[relevant] += 1

    @property
    def ready(self) -> bool:
        return self.total[True] > 0 and self.total[False] > 0

    def score(self, terms: frozenset[str]) -> float:
        """Higher means more like the included records than the excluded ones."""
        yes, no = self.seen[True], self.seen[False]
        yes_n = self.total[True] + 2 * SMOOTHING
        no_n = self.total[False] + 2 * SMOOTHING
        return sum(
            math.log((yes[t] + SMOOTHING) / yes_n) - math.log((no[t] + SMOOTHING) / no_n)
            for t in terms if t in yes or t in no
        )


def by_relevance(records: list[Record], labels: dict[int, bool], cache: dict | None = None) -> list[Record]:
    """Records sorted most likely relevant first, given labelled record ids.

    Returns the records unchanged until there is at least one include and one exclude.
    """
    cache = {} if cache is None else cache
    ranker = Ranker()
    for record_id, relevant in labels.items():
        if record_id in cache:
            ranker.learn(cache[record_id], relevant)
    if not ranker.ready:
        return records
    for record in records:
        if record.id not in cache:
            cache[record.id] = features(record)
    return sorted(records, key=lambda r: -ranker.score(cache[r.id]))


def simulate(records: list[Record], truth: dict[int, bool], runs: int = 5, batch: int = 5) -> dict:
    """Replay screening in relevance order against known answers.

    Each run starts from one random include and one random exclude, then always
    screens the highest-ranked records next, re-ranking every `batch` records.
    """
    records = [r for r in records if r.id in truth]
    cache = {r.id: features(r) for r in records}
    relevant = [r for r in records if truth[r.id]]
    irrelevant = [r for r in records if not truth[r.id]]
    if not relevant or not irrelevant:
        raise ValueError("The reference reviewer needs at least one include and one exclude.")
    target = math.ceil(0.95 * len(relevant))
    marks = (0.1, 0.25, 0.5)
    needed, recall_at = [], {m: [] for m in marks}
    for run in range(runs):
        rng = random.Random(run)
        labels = {rng.choice(relevant).id: True, rng.choice(irrelevant).id: False}
        found, screened, reached = 1, 2, None
        found_after = {}
        remaining = [r for r in records if r.id not in labels]
        while remaining:
            remaining = by_relevance(remaining, labels, cache)
            for record in remaining[:batch]:
                labels[record.id] = truth[record.id]
                found += truth[record.id]
                screened += 1
                found_after[screened] = found
                if reached is None and found >= target:
                    reached = screened
            remaining = remaining[batch:]
        needed.append(reached or len(records))
        for m in marks:
            cut = max(2, round(m * len(records)))
            recall_at[m].append(found_after.get(cut, 1) / len(relevant))
    mean_needed = sum(needed) / runs
    return {
        "records": len(records), "relevant": len(relevant), "runs": runs,
        "needed_for_95": mean_needed,
        "wss95": (len(records) - mean_needed) / len(records) - 0.05,
        "recall_at": {m: sum(v) / runs for m, v in recall_at.items()},
    }
