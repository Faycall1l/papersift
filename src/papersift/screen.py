"""Screening in the terminal, one record at a time."""
from __future__ import annotations

import shutil
import textwrap
import webbrowser

from .project import Protocol
from .store import (ABSTRACT, CONFLICT, CONSENSUS, EXCLUDE, FULLTEXT, INCLUDE, MAYBE,
                    UNAVAILABLE, Decision, Record, Store, is_ai)

KEYS = {
    ABSTRACT: "[i]nclude  [e]xclude  [m]aybe  [o]pen in browser  [s]kip  [b]ack  [q]uit",
    FULLTEXT: "[i]nclude  [e]xclude  [n]o full text  [o]pen in browser  [s]kip  [b]ack  [q]uit",
}


def show(record: Record, position: str = "") -> None:
    width = min(shutil.get_terminal_size().columns, 100)
    print("\n" + "-" * width)
    details = " · ".join(str(x) for x in (position, record.year, record.venue) if x)
    print(details)
    print(textwrap.fill(record.title, width))
    if record.authors:
        names = ", ".join(record.authors[:6]) + (" et al." if len(record.authors) > 6 else "")
        print(textwrap.fill(names, width))
    print()
    print(textwrap.fill(record.abstract or "(no abstract)", width))
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
        show_ai: bool = False) -> None:
    """Ask the reviewer about each record they have not decided at this stage."""
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

    position = 0
    while position < len(queue):
        record = queue[position]
        show(record, f"{position + 1} of {len(queue)}")
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
    print("\nDone. Run `papersift status` to see where the review stands.")
