"""An agent that drafts database searches and tests them against the live databases."""
from __future__ import annotations

from pathlib import Path

from .ai import client, model_name, protocol_text
from .project import PROTOCOL_FILE, Protocol, ProjectError, save_searches
from .sources import SOURCES, SourceError, get_source

MAX_TOOL_CALLS = 60

SYSTEM = """\
You are helping a researcher build the database searches for a systematic review.

A good search finds every study that could meet the inclusion criteria (high recall) \
while staying small enough for people to screen. Build each query from the concepts in \
the review question: synonyms and spelling variants joined with OR inside a concept, \
concepts joined with AND. Do not add terms for concepts the criteria do not require.

Work by testing, not guessing: count results, read sample titles to see what the query \
is catching, and adjust. If the researcher has named papers the search must find, check \
that each query finds them, in every database that indexes them, and fix the query when \
it does not. Queries returning more than a few thousand records usually need narrowing; \
queries returning a handful usually need more synonyms.

Choose the databases that fit the field. Each has its own syntax:

{sources}

When you are satisfied, call propose once for each final query, then finish with a short \
plain-language note on what the searches cover and any known gaps.\
"""


def plan(root: Path, protocol: Protocol, model: str | None = None, known: list[str] | None = None) -> None:
    from anthropic import beta_tool

    if not protocol.question:
        raise ProjectError(f"Write the review question in {PROTOCOL_FILE} first.")
    model, known = model_name(model), known or []
    api = client(model)
    years = (protocol.year_from, protocol.year_to)
    proposed: list[dict] = []
    calls = 0

    def guarded(action):
        nonlocal calls
        calls += 1
        if calls > MAX_TOOL_CALLS:
            return "No test calls left. Propose your final queries now."
        try:
            return action()
        except SourceError as err:
            return f"Error: {err}"

    @beta_tool
    def count_results(source: str, query: str) -> str:
        """Count the records a query returns, within the review's year range.

        Args:
            source: Database name.
            query: Query in that database's syntax.
        """
        def action():
            n = get_source(source).count(query, *years)
            print(f"  {source}: {n} for {query}")
            return str(n)
        return guarded(action)

    @beta_tool
    def sample_titles(source: str, query: str) -> str:
        """Return the titles of the first 25 records a query returns.

        Args:
            source: Database name.
            query: Query in that database's syntax.
        """
        def action():
            records = get_source(source).search(query, *years, limit=25)
            return "\n".join(f"- {r.title} ({r.year})" for r in records) or "No results."
        return guarded(action)

    @beta_tool
    def finds_known_papers(source: str, query: str) -> str:
        """Check which of the papers the researcher named are returned by a query.

        Args:
            source: Database name.
            query: Query in that database's syntax.
        """
        def action():
            if not known:
                return "The researcher named no papers."
            module = get_source(source)
            lines = []
            for title in known:
                plain = " ".join("".join(c if c.isalnum() else " " for c in title).split())
                in_database = module.count(module.with_title(plain.split()[0], plain)) > 0
                found = in_database and module.count(module.with_title(query, plain), *years) > 0
                status = "found" if found else "MISSED" if in_database else "not in this database"
                lines.append(f"- {status}: {title}")
            return "\n".join(lines)
        return guarded(action)

    @beta_tool
    def propose(source: str, query: str) -> str:
        """Put a final query forward for the researcher to approve.

        Args:
            source: Database name.
            query: Query in that database's syntax.
        """
        def action():
            total = get_source(source).count(query, *years)
            proposed.append({"source": source, "query": query, "max_results": max(2000, total)})
            return f"Recorded. {total} results."
        return guarded(action)

    syntax = "\n\n".join(f"{name}:\n{module.__doc__.strip()}" for name, module in SOURCES.items())
    request = protocol_text(protocol)
    request += f"\n\nYears: {years[0] or 'any'} to {years[1] or 'any'}"
    if known:
        request += "\n\nPapers the search must find:\n" + "\n".join(f"- {t}" for t in known)

    print("Drafting and testing searches. This takes a few minutes.")
    runner = api.beta.messages.tool_runner(
        model=model,
        max_tokens=16000,
        max_iterations=MAX_TOOL_CALLS,
        system=SYSTEM.format(sources=syntax),
        tools=[count_results, sample_titles, finds_known_papers, propose],
        messages=[{"role": "user", "content": request}],
    )
    final = None
    for final in runner:
        pass
    if final is not None:
        print("\n" + "\n".join(b.text for b in final.content if b.type == "text"))

    if not proposed:
        print("\nNo searches were proposed.")
        return
    print("\nProposed searches:")
    for entry in proposed:
        print(f"  {entry['source']}: {entry['query']}")
    answer = input(f"\nWrite these to {PROTOCOL_FILE}, replacing the current searches? [y/N] > ")
    if answer.strip().lower().startswith("y"):
        save_searches(root, proposed)
        print("Saved. Read them over, then run `papersift search`.")
