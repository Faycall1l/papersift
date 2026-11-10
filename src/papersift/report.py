"""PRISMA 2020 flow numbers, as text and as a diagram."""
from __future__ import annotations

import textwrap
from collections import Counter
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from .store import (ABSTRACT, CONFLICT, CONSENSUS, EXCLUDE, FULLTEXT, INCLUDE, PENDING,
                    UNAVAILABLE, Store, is_ai)

NO_REASON = "No reason recorded"


@dataclass
class Flow:
    identified: dict[str, int] = field(default_factory=dict)
    duplicates: int = 0
    unique: int = 0
    excluded_abstract: int = 0
    open_abstract: int = 0
    sought: int = 0
    not_retrieved: int = 0
    assessed: int = 0
    excluded_fulltext: dict[str, int] = field(default_factory=dict)
    open_fulltext: int = 0
    included: int = 0
    ai_only: dict[str, int] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not (self.open_abstract or self.open_fulltext)


def flow(store: Store) -> Flow:
    everything = store.records(include_duplicates=True)
    f = Flow()
    f.identified = dict(Counter(r.source for r in everything).most_common())
    f.duplicates = sum(1 for r in everything if r.duplicate_of)
    f.unique = len(everything) - f.duplicates

    abstract = Counter(store.outcomes(ABSTRACT).values())
    f.excluded_abstract = abstract[EXCLUDE]
    f.open_abstract = abstract[PENDING] + abstract[CONFLICT]
    f.sought = abstract[INCLUDE]

    outcomes = store.outcomes(FULLTEXT)
    decisions = store.decisions(FULLTEXT)
    fulltext = Counter(outcomes.values())
    f.not_retrieved = fulltext[UNAVAILABLE]
    f.open_fulltext = fulltext[PENDING] + fulltext[CONFLICT]
    f.included = fulltext[INCLUDE]
    f.assessed = fulltext[INCLUDE] + fulltext[EXCLUDE]
    reasons = Counter()
    for record_id, outcome in outcomes.items():
        if outcome == EXCLUDE:
            votes = decisions[record_id]
            final = next((d for d in votes if d.reviewer == CONSENSUS), votes[-1])
            reasons[final.reason or NO_REASON] += 1
    f.excluded_fulltext = dict(reasons.most_common())

    for stage in (ABSTRACT, FULLTEXT):
        settled = store.outcomes(stage)
        f.ai_only[stage] = sum(
            1 for record_id, votes in store.decisions(stage).items()
            if settled.get(record_id) in (INCLUDE, EXCLUDE) and all(is_ai(d.reviewer) for d in votes)
        )
    return f


def as_text(f: Flow, title: str = "") -> str:
    lines = [f"# PRISMA flow{': ' + title if title else ''}", ""]
    if not f.complete:
        lines += ["Screening is not finished; these numbers are provisional.", ""]
    lines += ["## Identification", ""]
    lines += [f"- Records from {source}: {n}" for source, n in f.identified.items()]
    lines += [f"- Records identified in total: {sum(f.identified.values())}",
              f"- Duplicates removed: {f.duplicates}", ""]
    lines += ["## Screening", "",
              f"- Records screened on title and abstract: {f.unique - f.open_abstract}",
              f"- Records excluded: {f.excluded_abstract}"]
    if f.open_abstract:
        lines.append(f"- Records still to screen or resolve: {f.open_abstract}")
    lines += [f"- Reports sought for retrieval: {f.sought}",
              f"- Reports not retrieved: {f.not_retrieved}",
              f"- Reports assessed for eligibility: {f.assessed}",
              f"- Reports excluded: {sum(f.excluded_fulltext.values())}"]
    lines += [f"  - {reason}: {n}" for reason, n in f.excluded_fulltext.items()]
    if f.open_fulltext:
        lines.append(f"- Reports still to assess or resolve: {f.open_fulltext}")
    lines += ["", "## Included", "", f"- Studies included in the review: {f.included}", ""]
    if any(f.ai_only.values()):
        lines += ["## Use of automation", "",
                  f"- Title and abstract decisions made by AI with no human reviewer: {f.ai_only[ABSTRACT]}",
                  f"- Full-text decisions made by AI with no human reviewer: {f.ai_only[FULLTEXT]}", ""]
    return "\n".join(lines)


BOX_W, GAP, PAD, LINE = 300, 60, 12, 17


def _box(x: int, y: int, lines: list[str]) -> tuple[str, int]:
    wrapped = [part for line in lines for part in (textwrap.wrap(line, 44) or [""])]
    height = PAD * 2 + LINE * len(wrapped) - 4
    text = "".join(
        f'<text x="{x + PAD}" y="{y + PAD + 11 + i * LINE}">{escape(line)}</text>'
        for i, line in enumerate(wrapped)
    )
    return f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{height}"/>{text}', height


def as_svg(f: Flow) -> str:
    """The PRISMA 2020 flow diagram for database searches."""
    sources = [f"{source} (n = {n})" for source, n in f.identified.items()]
    reasons = [f"{reason} (n = {n})" for reason, n in f.excluded_fulltext.items()]
    rows = [
        (["Records identified from:", *sources],
         ["Records removed before screening:", f"Duplicates (n = {f.duplicates})"]),
        ([f"Records screened (n = {f.unique - f.open_abstract})"],
         [f"Records excluded (n = {f.excluded_abstract})"]),
        ([f"Reports sought for retrieval (n = {f.sought})"],
         [f"Reports not retrieved (n = {f.not_retrieved})"]),
        ([f"Reports assessed for eligibility (n = {f.assessed})"],
         [f"Reports excluded (n = {sum(f.excluded_fulltext.values())})" + (":" if reasons else ""),
          *reasons]),
        ([f"Studies included in review (n = {f.included})"], None),
    ]
    left, right = 20, 20 + BOX_W + GAP
    parts, y, previous_bottom = [], 20, None
    for main, side in rows:
        main_svg, main_h = _box(left, y, main)
        parts.append(main_svg)
        height = main_h
        middle = left + BOX_W // 2
        if previous_bottom is not None:
            parts.append(f'<path d="M{middle} {previous_bottom}V{y}" marker-end="url(#arrow)"/>')
        if side:
            side_svg, side_h = _box(right, y, side)
            parts.append(side_svg)
            arrow_y = y + min(main_h, side_h) // 2
            parts.append(f'<path d="M{left + BOX_W} {arrow_y}H{right}" marker-end="url(#arrow)"/>')
            height = max(main_h, side_h)
        previous_bottom = y + main_h
        y += height + 36
    width, total = right + BOX_W + 20, y - 16
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total}" width="{width}" '
        f'height="{total}" font-family="Helvetica, Arial, sans-serif" font-size="13">'
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="#222" stroke="none"/></marker></defs>'
        f'<rect width="{width}" height="{total}" fill="#fff"/>'
        '<g fill="#fff" stroke="#222" stroke-width="1.2">'
        + "".join(parts).replace("<text", '<text fill="#111" stroke="none"')
        + "</g></svg>"
    )
