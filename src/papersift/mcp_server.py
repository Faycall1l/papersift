"""MCP server: lets an AI assistant work on a review through the same steps as the CLI."""
from __future__ import annotations

import functools

from . import chase as chasing
from . import export, report
from .ops import decide, run_searches
from .project import DB_FILE, ProjectError, find_root, load_protocol, save_searches
from .sources import SOURCES, SourceError, get_source
from .store import ABSTRACT, EXCLUDE, FULLTEXT, INCLUDE, MAYBE, STAGES, UNAVAILABLE, Store

INSTRUCTIONS = """\
papersift runs a PRISMA-style systematic review kept in one project folder.
Usual order: read the protocol, develop searches with count_results and sample_titles,
save them with set_searches, run_searches, then screen with list_records and decide,
first at the abstract stage and then at the fulltext stage, and finish with report.
Decisions you record are stored under an AI reviewer name and reported as such; a person
is expected to check them. At the abstract stage exclude only when the abstract leaves no
doubt, and use maybe when it does not say enough.\
"""


def build():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:
        raise ProjectError('The MCP server needs an extra install: pip install "papersift[mcp]"') from None

    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer("papersift", instructions=INSTRUCTIONS)

    def tool(name: str | None = None):
        """Register a tool whose expected failures reach the assistant as readable errors."""
        def register(function):
            @functools.wraps(function)
            def safe(*args, **kwargs):
                try:
                    return function(*args, **kwargs)
                except (ProjectError, SourceError, ValueError) as err:
                    raise ToolError(str(err)) from None
            return server.tool(name=name)(safe)
        return register

    def project():
        root = find_root()
        p, store = load_protocol(root), Store(root / DB_FILE)
        store.required = p.reviewers
        return root, p, store

    def stage_of(stage: str) -> str:
        if stage not in STAGES:
            raise ValueError(f"stage must be one of: {', '.join(STAGES)}")
        return stage

    @tool()
    def protocol() -> dict:
        """The review question, criteria, year range, searches and extraction fields."""
        _, p, _ = project()
        return {"title": p.title, "question": p.question, "include": p.include,
                "exclude": p.exclude, "years": [p.year_from, p.year_to],
                "searches": p.searches, "extraction": p.extraction}

    @tool()
    def status() -> str:
        """PRISMA flow numbers so far: identified, duplicates, screened, excluded, included."""
        _, p, store = project()
        return report.as_text(report.flow(store), p.title)

    @tool()
    def query_syntax() -> dict:
        """Query syntax of each database that can be searched."""
        return {name: module.__doc__.strip() for name, module in SOURCES.items()}

    @tool()
    def count_results(source: str, query: str) -> int:
        """Count the records a query returns in a database, within the review's years."""
        _, p, _ = project()
        return get_source(source).count(query, p.year_from, p.year_to)

    @tool()
    def sample_titles(source: str, query: str) -> list[str]:
        """Titles of the first 25 records a query returns, to see what it catches."""
        _, p, _ = project()
        records = get_source(source).search(query, p.year_from, p.year_to, limit=25)
        return [f"{r.title} ({r.year})" for r in records]

    @tool()
    def set_searches(searches: list[dict]) -> str:
        """Replace the searches in the protocol. Each entry: {source, query, max_results}."""
        root, _, _ = project()
        for entry in searches:
            get_source(str(entry.get("source", "")))
            if not entry.get("query"):
                raise ValueError("Every search needs a query.")
        save_searches(root, searches)
        return f"Saved {len(searches)} searches."

    @tool(name="run_searches")
    def run_searches_tool(again: bool = False) -> str:
        """Run the protocol's searches, add the records and remove duplicates."""
        _, p, store = project()
        lines: list[str] = []
        run_searches(store, p, again, lines.append)
        return "\n".join(lines)

    @tool()
    def chase_citations(from_abstract_stage: bool = False) -> dict:
        """Add the references of the included studies and the papers that cite them."""
        _, _, store = project()
        seeds = store.eligible(FULLTEXT) if from_abstract_stage else export.included(store)
        return chasing.chase(store, seeds)

    @tool()
    def list_records(stage: str = ABSTRACT, reviewer: str = "", outcome: str = "",
                     limit: int = 20, with_abstracts: bool = True) -> list[dict]:
        """Records at a stage ("abstract" or "fulltext").

        Give reviewer to get only the records that reviewer has not decided yet, or
        outcome (pending, conflict, include, exclude, unavailable) to filter by result.
        """
        _, _, store = project()
        stage = stage_of(stage)
        outcomes = store.outcomes(stage)
        records = store.todo(stage, reviewer) if reviewer else store.eligible(stage)
        records = [r for r in records if not outcome or outcomes[r.id] == outcome][:limit]
        return [{"id": r.id, "title": r.title, "year": r.year, "venue": r.venue,
                 "outcome": outcomes[r.id], "pdf_path": r.pdf_path,
                 **({"abstract": r.abstract} if with_abstracts else {})} for r in records]

    @tool()
    def get_record(record_id: int) -> dict:
        """One record in full, with every reviewer's decisions and any extracted values."""
        root, _, store = project()
        r = store.record(record_id)
        if not r:
            raise ValueError(f"No record with id {record_id}.")
        decisions = {
            stage: [{"reviewer": d.reviewer, "decision": d.decision, "reason": d.reason, "note": d.note}
                    for d in store.decisions(stage).get(r.id, [])]
            for stage in STAGES
        }
        values = {name: [{"reviewer": v["reviewer"], "value": v["value"], "quote": v["quote"]} for v in rows]
                  for name, rows in store.values().get(r.id, {}).items()}
        return {"id": r.id, "title": r.title, "authors": r.authors, "year": r.year, "venue": r.venue,
                "doi": r.doi, "url": r.url, "abstract": r.abstract,
                "pdf_path": str(root / r.pdf_path) if r.pdf_path else None,
                "duplicate_of": r.duplicate_of, "decisions": decisions, "extracted": values}

    @tool(name="decide")
    def decide_tool(record_id: int, decision: str, stage: str = ABSTRACT, reason: str = "",
                    note: str = "", reviewer: str = "ai:mcp") -> str:
        """Record a screening decision.

        decision: include, exclude, maybe (abstract stage only) or unavailable (fulltext
        stage only, when the full text cannot be obtained). At the fulltext stage an
        exclusion needs a reason, copied from the protocol's exclusion criteria.
        reviewer: keep the "ai:" prefix, e.g. "ai:<your model name>".
        """
        if decision not in (INCLUDE, EXCLUDE, MAYBE, UNAVAILABLE):
            raise ValueError("decision must be include, exclude, maybe or unavailable")
        _, _, store = project()
        decide(store, record_id, stage_of(stage), reviewer, decision, reason or None, note or None)
        return "Recorded."

    @tool()
    def set_extracted_value(record_id: int, field: str, value: str, quote: str = "",
                            reviewer: str = "ai:mcp") -> str:
        """Record one extraction field for an included study, with a supporting quote."""
        _, p, store = project()
        if field not in {f["name"] for f in p.extraction}:
            raise ValueError(f"No extraction field named '{field}' in the protocol.")
        store.set_value(record_id, field, reviewer, value, quote or None)
        return "Recorded."

    @tool(name="report")
    def report_tool() -> str:
        """Write prisma.md and prisma.svg in the project folder and return the flow numbers."""
        root, p, store = project()
        flow = report.flow(store)
        text = report.as_text(flow, p.title)
        (root / "prisma.md").write_text(text)
        (root / "prisma.svg").write_text(report.as_svg(flow))
        return text

    return server
