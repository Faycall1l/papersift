from __future__ import annotations

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

# Publication years to search. Remove a line to leave that side open.
years:
  from: 2015
  to: {year}

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


def find_root(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
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
    return Protocol(
        title=str(data.get("title") or ""),
        question=str(data.get("question") or ""),
        include=[str(c) for c in criteria.get("include") or []],
        exclude=[str(c) for c in criteria.get("exclude") or []],
        year_from=years.get("from"),
        year_to=years.get("to"),
        searches=list(data.get("searches") or []),
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
