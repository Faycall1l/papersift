from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import __version__, agreement, export, extract, ops, report, screen
from .dedupe import mark_duplicates
from .importers import read_file
from .project import DB_FILE, PROTOCOL_FILE, TEMPLATE, ProjectError, find_root, load_protocol
from .sources import SourceError
from .sources.http import get as http_get
from .store import (ABSTRACT, CONSENSUS, EXCLUDE, FULLTEXT, INCLUDE, MAYBE, UNAVAILABLE,
                    Store)


def open_project():
    root = find_root()
    return root, load_protocol(root), Store(root / DB_FILE)


def stage_of(args) -> str:
    return FULLTEXT if args.fulltext else ABSTRACT


def reviewer_of(args) -> str:
    return args.reviewer or os.environ.get("PAPERSIFT_REVIEWER") or getpass.getuser()


def cmd_init(args) -> None:
    folder = Path(args.folder)
    target = folder / PROTOCOL_FILE
    if target.exists():
        raise ProjectError(f"{target} already exists.")
    title = args.title or input("Short title for the review > ").strip()
    question = args.question or input("The question it answers > ").strip()
    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE.format(
        title=json.dumps(title), question=json.dumps(question), year=date.today().year))
    Store(folder / DB_FILE).close()
    print(f"Created {target}")
    print("Next: write your criteria and searches in that file, then run `papersift search`.")
    print("With an Anthropic API key, `papersift plan` can draft the searches for you.")


def cmd_search(args) -> None:
    _, protocol, store = open_project()
    ops.run_searches(store, protocol, args.again)
    print("Next: `papersift screen`")


def cmd_add(args) -> None:
    _, _, store = open_project()
    for name in args.files:
        records = read_file(Path(name))
        added = store.add_records(records)
        store.log_search(f"file:{Path(name).name}", "", len(records), added)
        print(f"{name}: {len(records)} records, {added} new")
    print(f"Duplicates marked: {mark_duplicates(store)}")


def cmd_chase(args) -> None:
    from .chase import chase
    _, _, store = open_project()
    seeds = export.included(store)
    if args.from_abstract:
        seeds = store.eligible(FULLTEXT)
    if not seeds:
        raise ProjectError("No included studies to start from. Screen first, "
                           "or use --from-abstract to start from the abstract-stage includes.")
    both = not (args.backward or args.forward)
    result = chase(store, seeds, backward=both or args.backward, forward=both or args.forward)
    print(f"Started from {result['seeds']} studies ({result['unresolved']} not found in OpenAlex)")
    print(f"Found {result['found']} citing or cited records, {result['added']} new")
    print(f"Duplicates marked: {mark_duplicates(store)}")
    print("New records are waiting in `papersift screen`.")


def cmd_duplicates(args) -> None:
    _, _, store = open_project()
    if args.keep:
        store.update_record(args.keep, duplicate_of=None)
        print(f"Record {args.keep} is no longer treated as a duplicate.")
        return
    for r in store.records(include_duplicates=True):
        if r.duplicate_of:
            original = store.record(r.duplicate_of)
            print(f"{r.id:>6}  {r.title[:90]}\n  same as {original.id}: {original.title[:80]}")


def cmd_status(args) -> None:
    _, protocol, store = open_project()
    print(report.as_text(report.flow(store), protocol.title))


def cmd_screen(args) -> None:
    _, protocol, store = open_project()
    if args.ai:
        from .ai import screen_all
        screen_all(store, protocol, stage_of(args), model=args.model, limit=args.limit)
        return
    screen.run(store, protocol, stage_of(args), reviewer_of(args), show_ai=args.show_ai)


def cmd_extract(args) -> None:
    _, protocol, store = open_project()
    if args.ai:
        from .ai import extract_all
        extract_all(store, protocol, model=args.model, limit=args.limit)
    elif args.id:
        if not (args.field and args.value):
            raise ProjectError("Give --field and --value with --id.")
        if args.field not in {f["name"] for f in protocol.extraction}:
            raise ProjectError(f"No extraction field named '{args.field}' in {PROTOCOL_FILE}.")
        store.set_value(args.id, args.field, reviewer_of(args), args.value)
    else:
        extract.run(store, protocol, reviewer_of(args))


def cmd_resolve(args) -> None:
    _, protocol, store = open_project()
    screen.run(store, protocol, stage_of(args), CONSENSUS)


def cmd_plan(args) -> None:
    root, protocol, _ = open_project()
    from .planner import plan
    plan(root, protocol, model=args.model, known=args.known or [])


def cmd_fetch(args) -> None:
    root, _, store = open_project()
    folder = root / "fulltext"
    folder.mkdir(exist_ok=True)
    got = missing = 0
    for record in store.eligible(FULLTEXT):
        path = folder / f"{record.id}.pdf"
        if not path.exists():
            try:
                data = http_get(record.pdf_url, tries=2) if record.pdf_url else b""
            except SourceError:
                data = b""
            if not data.startswith(b"%PDF"):
                missing += 1
                continue
            path.write_bytes(data)
            got += 1
        relative = str(path.relative_to(root))
        if record.pdf_path != relative:
            store.update_record(record.id, pdf_path=relative)
    print(f"Downloaded {got} open-access PDFs into {folder}")
    if missing:
        print(f"{missing} could not be downloaded. Save them yourself as fulltext/<id>.pdf "
              "(`papersift list --fulltext` shows the ids), then run `papersift fetch` again.")


def cmd_zotero(args) -> None:
    from . import zotero
    root, protocol, store = open_project()
    library = zotero.Library(args.group)
    stage = stage_of(args)
    if args.action == "push":
        outcomes = store.outcomes(ABSTRACT)
        records = {
            "included": lambda: export.included(store),
            "fulltext": lambda: store.eligible(FULLTEXT),
            "to-screen": lambda: [r for r in store.records() if outcomes[r.id] in ("pending", "conflict")],
        }[args.what]()
        name = args.collection or f"{protocol.title or 'papersift'}: {args.what}"
        result = zotero.push(store, library, records, name)
        print(f"Collection '{name}': {result['created']} items created, "
              f"{result['already']} already in Zotero")
        if result["refused"]:
            print(f"Zotero refused {result['refused']} items.")
    elif args.action == "pull":
        result = zotero.pull(store, library, args.collection)
        print(f"{result['found']} new items in Zotero, {result['added']} records added")
        print(f"Duplicates marked: {mark_duplicates(store)}")
    elif args.action == "decisions":
        if not args.collection:
            raise ProjectError("Name the collection you tagged with --collection.")
        result = zotero.read_decisions(store, library, args.collection, stage, reviewer_of(args))
        print(f"Recorded {result['recorded']} {stage} decisions from tags")
        if result["unclear"]:
            print(f"{result['unclear']} items have conflicting tags, or an exclude tag with no "
                  "reason at full text (use `exclude: reason`), and were skipped.")
    elif args.action == "pdfs":
        print(f"Copied {zotero.fetch_pdfs(store, library, root)} PDFs from Zotero into fulltext/")


def cmd_report(args) -> None:
    root, protocol, store = open_project()
    f = report.flow(store)
    (root / "prisma.md").write_text(report.as_text(f, protocol.title))
    (root / "prisma.svg").write_text(report.as_svg(f))
    print(report.as_text(f, protocol.title))
    print(f"Wrote {root / 'prisma.md'} and {root / 'prisma.svg'}")


def cmd_export(args) -> None:
    root, _, store = open_project()
    if args.extraction:
        protocol = load_protocol(root)
        target = Path(args.output) if args.output else root / "extraction.csv"
        target.write_text(extract.table(store, protocol))
        print(f"Wrote {target}")
        return
    if args.format == "csv":
        text = export.to_csv(store, only_included=not args.all)
    else:
        records = store.records() if args.all else export.included(store)
        text = export.to_ris(records) if args.format == "ris" else export.to_bibtex(records)
    name = ("records" if args.all else "included") + "." + args.format
    target = Path(args.output) if args.output else root / name
    target.write_text(text)
    print(f"Wrote {target}")


def _print_accuracy(r: dict, reviewer: str, reference: str, stage: str) -> None:
    def pct(value):
        return "n/a" if value is None else f"{value:.1%}"

    print(f"{reviewer} scored against {reference}, {stage} stage, {r['both']} records")
    print(f"  {reference} included {r['kept'] + r['missed']}: "
          f"{reviewer} passed {r['kept']}, excluded {r['missed']}")
    print(f"  {reference} excluded {r['dropped'] + r['extra']}: "
          f"{reviewer} excluded {r['dropped']}, passed {r['extra']}")
    print(f"Recall: {pct(r['recall'])}")
    print(f"Specificity: {pct(r['specificity'])}")
    print(f"Precision: {pct(r['precision'])}")
    print(f"Records {reviewer} would remove from the pile: {pct(r['work_saved'])}")
    if r["final_includes"]:
        lost = len(r["final_missed_ids"])
        print(f"Studies {reference} included at full text: {r['final_includes']}, "
              f"of which {reviewer} excluded {lost}")
    if r["missed_ids"]:
        print("Missed record ids: " + ", ".join(str(i) for i in r["missed_ids"]))


def cmd_agreement(args) -> None:
    _, _, store = open_project()
    stage = stage_of(args)
    if args.reference:
        if len(args.reviewers) != 1:
            raise ProjectError("Name the one reviewer to score against the reference.")
        _print_accuracy(agreement.against(store, stage, args.reviewers[0], args.reference),
                        args.reviewers[0], args.reference, stage)
        return
    names = args.reviewers or agreement.reviewers(store, stage)
    if len(names) != 2:
        raise ProjectError(f"Name two reviewers to compare. Reviewers at this stage: "
                           f"{', '.join(agreement.reviewers(store, stage)) or 'none'}")
    result = agreement.compare(store, stage, *names)
    print(f"{names[0]} and {names[1]}, {stage} stage")
    print(f"Records both decided: {result['both']}")
    if result["both"]:
        print(f"Agreed on: {result['agreed']} ({result['agreed'] / result['both']:.0%})")
        kappa = result["kappa"]
        print("Cohen's kappa: " + ("undefined (no variation)" if kappa is None else f"{kappa:.2f}"))
        for (a, b), n in sorted(result["table"].items()):
            print(f"  {names[0]} {a} / {names[1]} {b}: {n}")


def cmd_list(args) -> None:
    _, _, store = open_project()
    stage = stage_of(args)
    outcomes = store.outcomes(stage)
    if args.todo:
        records = store.todo(stage, reviewer_of(args))
    else:
        records = [r for r in store.eligible(stage) if args.outcome in (None, outcomes[r.id])]
    records = records[:args.limit] if args.limit else records
    if args.json:
        print(json.dumps([
            {"id": r.id, "title": r.title, "abstract": r.abstract, "year": r.year,
             "venue": r.venue, "doi": r.doi, "pdf_path": r.pdf_path, "outcome": outcomes[r.id]}
            for r in records
        ], indent=1))
        return
    for r in records:
        print(f"{r.id:>6}  {outcomes[r.id]:<11} {r.year or '':<5} {r.title[:90]}")


def cmd_show(args) -> None:
    _, _, store = open_project()
    record = store.record(args.id)
    if not record:
        raise ProjectError(f"No record with id {args.id}.")
    screen.show(record, f"id {record.id}")
    for stage in (ABSTRACT, FULLTEXT):
        votes = store.decisions(stage).get(record.id)
        if votes:
            print(f"{stage}:")
            screen.show_votes(votes)


def cmd_decide(args) -> None:
    _, _, store = open_project()
    ops.decide(store, args.id, stage_of(args), reviewer_of(args), args.decision, args.reason, args.note)


def cmd_mcp(args) -> None:
    from .mcp_server import build
    if args.project:
        os.environ["PAPERSIFT_PROJECT"] = args.project
    find_root()
    build().run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="papersift",
        description="Find and screen papers for a systematic review.",
        epilog="Usual order: init, search, screen, screen --fulltext, report, export.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", metavar="command")

    def command(name, func, help):
        p = sub.add_parser(name, help=help, description=help)
        p.set_defaults(func=func)
        return p

    def staged(p, who=True):
        p.add_argument("--fulltext", action="store_true", help="work on the full-text stage")
        if who:
            p.add_argument("--reviewer", help="your name (default: your login name)")
        return p

    p = command("init", cmd_init, "Start a review in a folder.")
    p.add_argument("folder", nargs="?", default=".")
    p.add_argument("--title")
    p.add_argument("--question")

    p = command("plan", cmd_plan, "Draft database searches with AI and test them.")
    p.add_argument("--known", nargs="*", metavar="TITLE",
                   help="titles of papers the search must find")
    p.add_argument("--model")

    p = command("search", cmd_search, "Run the searches in protocol.yaml.")
    p.add_argument("--again", action="store_true", help="rerun searches already run")

    p = command("add", cmd_add, "Add records from RIS, BibTeX or CSV files.")
    p.add_argument("files", nargs="+")

    p = command("chase", cmd_chase, "Add the references of included studies and the papers citing them.")
    p.add_argument("--backward", action="store_true", help="only their references")
    p.add_argument("--forward", action="store_true", help="only papers citing them")
    p.add_argument("--from-abstract", action="store_true",
                   help="start from records included at the abstract stage")

    p = command("duplicates", cmd_duplicates, "List records removed as duplicates.")
    p.add_argument("--keep", type=int, metavar="ID", help="restore a record wrongly removed")

    command("status", cmd_status, "Show where the review stands.")

    p = staged(command("screen", cmd_screen, "Screen records one at a time."))
    p.add_argument("--ai", action="store_true", help="let AI screen as one more reviewer")
    p.add_argument("--show-ai", action="store_true", help="show the AI's decision while you screen")
    p.add_argument("--limit", type=int, help="with --ai, screen at most this many records")
    p.add_argument("--model")

    p = command("extract", cmd_extract, "Record data from each included study.")
    p.add_argument("--ai", action="store_true", help="let AI fill in the fields from the PDFs")
    p.add_argument("--reviewer", help="your name (default: your login name)")
    p.add_argument("--limit", type=int, help="with --ai, at most this many studies")
    p.add_argument("--model")
    p.add_argument("--id", type=int, help="set one value without prompts")
    p.add_argument("--field")
    p.add_argument("--value")

    staged(command("resolve", cmd_resolve, "Settle records where reviewers disagree."), who=False)
    p = staged(command("zotero", cmd_zotero, "Exchange records, decisions and PDFs with Zotero."))
    p.add_argument("action", choices=["push", "pull", "decisions", "pdfs"])
    p.add_argument("--what", choices=["included", "fulltext", "to-screen"], default="included",
                   help="which records to push (default: included)")
    p.add_argument("--collection", help="Zotero collection name")
    p.add_argument("--group", help="id of a Zotero group library (default: your own library)")

    command("fetch", cmd_fetch, "Download open-access PDFs for the full-text stage.")
    command("report", cmd_report, "Write the PRISMA flow numbers and diagram.")

    p = command("export", cmd_export, "Export included studies.")
    p.add_argument("--format", choices=export.FORMATS, default="csv")
    p.add_argument("--all", action="store_true", help="export every record, not only included")
    p.add_argument("--extraction", action="store_true", help="export the data extraction table")
    p.add_argument("--output")

    p = staged(command("agreement", cmd_agreement, "Compare two reviewers (Cohen's kappa)."), who=False)
    p.add_argument("reviewers", nargs="*")
    p.add_argument("--reference", metavar="NAME",
                   help="score one reviewer against this one: recall, specificity, precision")

    p = command("mcp", cmd_mcp, "Run as an MCP server so an AI assistant can work on the review.")
    p.add_argument("--project", help="review folder (default: the current folder)")

    p = staged(command("list", cmd_list, "List records at a stage."))
    p.add_argument("--outcome", choices=["pending", "conflict", INCLUDE, EXCLUDE, UNAVAILABLE])
    p.add_argument("--todo", action="store_true", help="only records this reviewer has not decided")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")

    p = command("show", cmd_show, "Show one record and its decisions.")
    p.add_argument("id", type=int)

    p = staged(command("decide", cmd_decide, "Record a decision without the interactive screen."))
    p.add_argument("id", type=int)
    p.add_argument("decision", choices=[INCLUDE, EXCLUDE, MAYBE, UNAVAILABLE])
    p.add_argument("--reason")
    p.add_argument("--note")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        args.func(args)
    except (ProjectError, SourceError, ValueError) as err:
        print(f"papersift: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
