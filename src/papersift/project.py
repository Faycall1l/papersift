from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROTOCOL_FILE = "protocol.yaml"
DB_FILE = "papersift.db"

TEMPLATE = """\
# The review protocol. Edit this file, then run `papersift search`.

title: {title}

# The question the review answers.
question: {question}

# A record is included only if it meets every include criterion
# and no exclude criterion. Exclude criteria are also the reasons
# offered when a paper is excluded at full text.
criteria:
  include:
    - Reports original empirical results
  exclude:
    - Not about the review topic
    - Not a research paper (editorial, tutorial, abstract only)
    - Not in English

# How many reviewers must decide each record before its outcome counts.
# An AI reviewer counts as one.
reviewers: 1

# Words to colour while screening: green for include, red for exclude.
highlight:
  include: []
  exclude: []

# Publication years to search. Remove a line to leave that side open.
years:
  from: 2015
  to: {year}

# What to record from each included study. Used by `papersift extract`.
extraction:
  - name: design
    question: What was the study design?
  - name: sample_size
    question: How many participants were analysed?

# One entry per database query.
# Sources: openalex, pubmed, europepmc, arxiv, semanticscholar.
# Each source has its own query syntax; see the README.
searches:
  - source: openalex
    query: '"your topic" AND (term OR "other term")'
    max_results: 2000
"""


class ProjectError(Exception):
    pass


@dataclass
class Protocol:
    title: str = ""
    question: str = ""
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    searches: list[dict] = field(default_factory=list)
    extraction: list[dict] = field(default_factory=list)
    reviewers: int = 1
    highlight_include: list[str] = field(default_factory=list)
    highlight_exclude: list[str] = field(default_factory=list)


def find_root(start: Path | None = None) -> Path:
    here = Path(start or os.environ.get("PAPERSIFT_PROJECT") or Path.cwd()).expanduser().resolve()
    for folder in [here, *here.parents]:
        if (folder / PROTOCOL_FILE).exists():
            return folder
    raise ProjectError(
        f"No {PROTOCOL_FILE} found here or in a parent folder. "
        "Run `papersift init` to start a review."
    )


def load_protocol(root: Path) -> Protocol:
    try:
        data = yaml.safe_load((root / PROTOCOL_FILE).read_text()) or {}
    except yaml.YAMLError as err:
        raise ProjectError(f"{PROTOCOL_FILE} is not valid YAML: {err}") from err
    criteria = data.get("criteria") or {}
    years = data.get("years") or {}
    highlight = data.get("highlight") or {}
    return Protocol(
        title=str(data.get("title") or ""),
        question=str(data.get("question") or ""),
        include=[str(c) for c in criteria.get("include") or []],
        exclude=[str(c) for c in criteria.get("exclude") or []],
        year_from=years.get("from"),
        year_to=years.get("to"),
        searches=list(data.get("searches") or []),
        reviewers=max(1, int(data.get("reviewers") or 1)),
        highlight_include=[str(w) for w in highlight.get("include") or []],
        highlight_exclude=[str(w) for w in highlight.get("exclude") or []],
        extraction=[
            {"name": str(f["name"]), "question": str(f.get("question") or f["name"]),
             "choices": [str(c) for c in f.get("choices") or []]}
            for f in data.get("extraction") or []
        ],
    )


def save_searches(root: Path, searches: list[dict]) -> None:
    """Replace the searches block, leaving the rest of the file as written."""
    path = root / PROTOCOL_FILE
    lines = path.read_text().splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("searches:")), None)
    if start is None:
        kept = lines + [""]
    else:
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " -#"):
            end += 1
        kept = lines[:start] + lines[end:]
    block = yaml.safe_dump({"searches": searches}, sort_keys=False, allow_unicode=True, width=1000)
    path.write_text("\n".join(kept).rstrip() + "\n\n" + block)
