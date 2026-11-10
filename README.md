<p align="center"><img src="docs/logo.svg" alt="papersift" width="420"></p>

papersift finds and screens papers for a systematic review and keeps the
counts a PRISMA 2020 flow diagram needs. It searches OpenAlex, PubMed and
arXiv, reads exports from other databases, removes duplicates, walks you
through title/abstract and full-text screening, and writes the flow diagram.

Everything works without AI. With an Anthropic API key, an AI can draft and
test your searches and act as an extra reviewer.

## Install

Needs Python 3.9 or newer.

```
pip install git+https://github.com/Faycall1l/papersift
```

For the AI features:

```
pip install "papersift[ai] @ git+https://github.com/Faycall1l/papersift"
```

## A review, start to finish

```
papersift init my-review
cd my-review
```

This creates `protocol.yaml`. Open it in any text editor and write your
question, your inclusion and exclusion criteria, and your searches:

```yaml
title: "Telemonitoring in heart failure"
question: "Does home telemonitoring reduce readmissions in adults with heart failure?"

criteria:
  include:
    - Adults with heart failure
    - Home telemonitoring intervention
    - Reports hospital readmissions
  exclude:
    - Not a controlled study
    - Conference abstract only

years:
  from: 2010
  to: 2025

searches:
  - source: pubmed
    query: '"heart failure"[tiab] AND (telemonitoring[tiab] OR "remote monitoring"[tiab])'
  - source: openalex
    query: '"heart failure" AND (telemonitoring OR "remote monitoring")'
```

Then:

| Step | Command |
| --- | --- |
| Run the searches | `papersift search` |
| Add exports from Scopus, Web of Science, Zotero, etc. | `papersift add export.ris` |
| Screen titles and abstracts | `papersift screen` |
| Download open-access PDFs | `papersift fetch` |
| Screen full texts | `papersift screen --fulltext` |
| See where things stand | `papersift status` |
| Write `prisma.md` and `prisma.svg` | `papersift report` |
| Export the included studies | `papersift export --format csv` (or `ris`, `bib`) |

Duplicates are removed automatically after every search and import, by DOI
and by title. Screening can be stopped and resumed at any time.

## Writing searches

Each database has its own query language.

| Source | Covers | Query syntax |
| --- | --- | --- |
| `openalex` | All fields | `AND`, `OR`, `NOT`, `"exact phrase"`, parentheses. Matches title and abstract. |
| `pubmed` | Biomedicine | The same as on the PubMed website: `term[tiab]`, `"Term"[Mesh]`, etc. |
| `arxiv` | Physics, maths, computer science preprints | Field prefixes: `ti:"..."`, `abs:"..."`, joined with `AND`, `OR`, `ANDNOT`. |

Each search fetches at most 2000 records unless you set `max_results`.

For any other database, export your results as RIS, BibTeX or CSV and use
`papersift add`.

OpenAlex allows a small number of requests per day without a key. For a
large search, get a free key at openalex.org/settings/api and set
`OPENALEX_API_KEY`. `NCBI_API_KEY` is optional and speeds up PubMed.

## More than one reviewer

Each person screens under their own name and does not see the others'
decisions:

```
papersift screen --reviewer ana
papersift screen --reviewer ben
papersift agreement ana ben
papersift resolve
```

`agreement` reports percent agreement and Cohen's kappa. `resolve` shows
each record the reviewers disagree on, with their decisions, and records the
final one. A "maybe" always goes to `resolve`.

The review lives in one file, `papersift.db`, next to `protocol.yaml`. Share
the folder to work together; only one person should screen at a time.

## Using AI

Set `ANTHROPIC_API_KEY`, then:

```
papersift plan --known "Title of a paper the search must find" "Another title"
```

`plan` reads your question and criteria, drafts queries, runs them against
the databases to check result counts, reads sample titles, checks that the
papers you named are found, and shows you the final queries before saving
them to `protocol.yaml`.

```
papersift screen --ai
papersift screen --ai --fulltext
```

The AI screens as a reviewer named `ai:<model>`. Its decisions are stored
with a reason and a one-sentence note. It is told to exclude at the abstract
stage only when the abstract leaves no doubt, and to answer "maybe"
otherwise. Full-text screening reads the PDFs downloaded by `papersift fetch`.

The AI is one reviewer, not the final word. Check its work:

```
papersift screen --reviewer ana --show-ai
papersift agreement ana ai:claude-opus-5-5
```

`papersift report` states how many decisions were made by AI with no human
reviewer, so you can report it.

The default model is `claude-opus-5-5`. Choose another with `--model` or
`PAPERSIFT_MODEL`; a smaller model costs much less on large screens. Titles,
abstracts and PDFs are sent to the Anthropic API.

## Scripting

Every step also works without prompts, for scripts or coding agents:

```
papersift list --todo --json --limit 20
papersift show 42
papersift decide 42 exclude --reviewer ana --note "animal study"
papersift decide 42 exclude --fulltext --reviewer ana --reason "Not a controlled study"
```

## Limits

- Duplicate detection needs matching DOIs or titles. It will miss a preprint
  whose title changed on publication.
- Full texts behind a paywall are not downloaded. Save them yourself as
  `fulltext/<id>.pdf` and run `papersift fetch` again.
- The flow diagram covers database searches. Records from citation chasing
  or other sources can be added with `papersift add` and are counted by file.

## Development

```
pip install -e ".[dev,ai]"
pytest
```

Apache License 2.0.
