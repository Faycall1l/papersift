"""Screening in the terminal, one record at a time."""
from __future__ import annotations

import re
import shutil
import sys
import textwrap
import webbrowser

from .project import Protocol
from .rank import by_relevance, features
from .store import (ABSTRACT, CONFLICT, CONSENSUS, EXCLUDE, FULLTEXT, INCLUDE, MAYBE,
                    UNAVAILABLE, Decision, Record, Store, is_ai)

KEYS = {
    ABSTRACT: "[i]nclude  [e]xclude  [m]aybe  [o]pen in browser  [s]kip  [b]ack  [q]uit",
    FULLTEXT: "[i]nclude  [e]xclude  [n]o full text  [o]pen in browser  [s]kip  [b]ack  [q]uit",
}


GREEN, RED, RESET = "\033[1;32m", "\033[1;31m", "\033[0m"


def colour(text: str, protocol: Protocol | None) -> str:
    """Mark the protocol's highlight words, when writing to a terminal."""
    if not protocol or not sys.stdout.isatty():
        return text
    for words, code in ((protocol.highlight_include, GREEN), (protocol.highlight_exclude, RED)):
        if words:
            pattern = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
            text = re.sub(rf"\b({pattern})\w*", lambda m: f"{code}{m.group()}{RESET}", text, flags=re.I)
    return text


def show(record: Record, position: str = "", protocol: Protocol | None = None) -> None:
    width = min(shutil.get_terminal_size().columns, 100)
    print("\n" + "-" * width)
    details = " · ".join(str(x) for x in (position, record.year, record.venue) if x)
    print(details)
    print(colour(textwrap.fill(record.title, width), protocol))
    if record.authors:
        names = ", ".join(record.authors[:6]) + (" et al." if len(record.authors) > 6 else "")
        print(textwrap.fill(names, width))
    print()
    print(colour(textwrap.fill(record.abstract or "(no abstract)", width), protocol))
    if record.pdf_path:
        print(f"\nFull text: {record.pdf_path}")
    print()


def show_votes(decisions: list[Decision], blind: bool = False) -> None:
    for d in decisions:
        if blind and not is_ai(d.reviewer):
            continue
        reason = f" ({d.reason})" if d.reason else ""
        note = f": {d.note}" if d.note else ""
        print(f"  {d.reviewer}: {d.decision}{reason}{note}")
    print()


def ask_reason(protocol: Protocol) -> str | None:
    """Pick an exclusion reason. Returns None if the reviewer backs out."""
    for i, reason in enumerate(protocol.exclude, 1):
        print(f"  {i}. {reason}")
    while True:
        answer = input("Reason (number, or type your own, blank to cancel) > ").strip()
        if not answer:
            return None
        if answer.isdigit():
            if 1 <= int(answer) <= len(protocol.exclude):
                return protocol.exclude[int(answer) - 1]
            print("No reason with that number.")
            continue
        return answer


def run(store: Store, protocol: Protocol, stage: str, reviewer: str,
        show_ai: bool = False, ranked: bool = True) -> None:
    """Ask the reviewer about each record they have not decided at this stage.

    Once the reviewer has included and excluded at least one record, the rest are
    shown most likely relevant first, re-ranked after every decision.
    """
    resolving = reviewer == CONSENSUS
    if resolving:
        outcomes = store.outcomes(stage)
        queue = [r for r in store.eligible(stage) if outcomes[r.id] == CONFLICT]
    else:
        queue = store.todo(stage, reviewer)
    if not queue:
        print("Nothing to resolve." if resolving else "Nothing left to screen at this stage.")
        return

    if protocol.include or protocol.exclude:
        print("Include if:", *[f"  + {c}" for c in protocol.include], sep="\n")
        print("Exclude if:", *[f"  - {c}" for c in protocol.exclude], sep="\n")

    ranked = ranked and not resolving
    cache = {r.id: features(r) for r in store.eligible(stage)} if ranked else {}
    position = 0

    def rerank() -> bool:
        """Sort the records not yet shown by relevance. False until there is enough to learn from."""
        labels = {
            record_id: d.decision == INCLUDE
            for record_id, votes in store.decisions(stage).items()
            for d in votes if d.reviewer == reviewer and d.decision in (INCLUDE, EXCLUDE)
        }
        queue[position:] = by_relevance(queue[position:], labels, cache)
        return len(set(labels.values())) == 2

    learning = ranked and rerank()
    if learning:
        print("Showing the records most likely to be relevant first.")
    while position < len(queue):
        record = queue[position]
        streak = f"{store.since_last_include(stage, reviewer)} since last include" if learning else ""
        show(record, " · ".join(x for x in (f"{position + 1} of {len(queue)}", streak) if x), protocol)
        votes = store.decisions(stage).get(record.id, [])
        if resolving:
            show_votes(votes)
        elif show_ai:
            show_votes(votes, blind=True)

        try:
            key = input(KEYS[stage] + " > ").strip().lower()[:1]
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if key == "q":
            return
        if key == "s":
            position += 1
        elif key == "b":
            if position:
                position -= 1
                store.undo(queue[position].id, stage, reviewer)
        elif key == "o":
            target = record.url or (f"https://doi.org/{record.doi}" if record.doi else None)
            if target:
                webbrowser.open(target)
            else:
                print("This record has no link.")
        elif key == "i":
            store.decide(Decision(record.id, stage, reviewer, INCLUDE))
            position += 1
            learning = ranked and rerank()
        elif key == "m" and stage == ABSTRACT and not resolving:
            store.decide(Decision(record.id, stage, reviewer, MAYBE))
            position += 1
        elif key == "n" and stage == FULLTEXT:
            store.decide(Decision(record.id, stage, reviewer, UNAVAILABLE))
            position += 1
        elif key == "e":
            reason = ask_reason(protocol) if stage == FULLTEXT else None
            if stage == FULLTEXT and reason is None:
                continue
            store.decide(Decision(record.id, stage, reviewer, EXCLUDE, reason))
            position += 1
            learning = ranked and rerank()
    print("\nDone. Run `papersift status` to see where the review stands.")
