from papersift import agreement, export, extract, report
from papersift.cli import main
from papersift.dedupe import mark_duplicates, title_key
from papersift.importers import read_bibtex, read_csv, read_ris
from papersift.project import load_protocol, save_searches
from papersift.store import (ABSTRACT, CONFLICT, CONSENSUS, EXCLUDE, FULLTEXT, INCLUDE, MAYBE,
                             PENDING, UNAVAILABLE, Decision, Record, Store, clean_doi, settle)


def make_store(tmp_path, n=6):
    store = Store(tmp_path / "t.db")
    store.add_records([
        Record(source="a", source_id=str(i), title=f"A sufficiently long distinct title number {i}")
        for i in range(n)
    ])
    return store


def vote(store, record_id, reviewer, decision, stage=ABSTRACT, reason=None):
    store.decide(Decision(record_id, stage, reviewer, decision, reason))


def test_clean_doi():
    assert clean_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert clean_doi("doi:10.1/x") == "10.1/x"
    assert clean_doi("") is None


def test_settle():
    d = lambda who, what: Decision(1, ABSTRACT, who, what)
    assert settle([]) == PENDING
    assert settle([d("a", INCLUDE), d("b", INCLUDE)]) == INCLUDE
    assert settle([d("a", INCLUDE), d("b", EXCLUDE)]) == CONFLICT
    assert settle([d("a", MAYBE)]) == CONFLICT
    assert settle([d("a", INCLUDE), d("b", EXCLUDE), d(CONSENSUS, EXCLUDE)]) == EXCLUDE


def test_dedupe_by_doi_and_title(tmp_path):
    store = Store(tmp_path / "t.db")
    store.add_records([
        Record(source="a", source_id="1", title="Effects of Sleep on Memory: A Trial", doi="10.1/X"),
        Record(source="b", source_id="1", title="Something else entirely, but same DOI",
               doi="https://doi.org/10.1/x", abstract="An abstract."),
        Record(source="c", source_id="1", title="Effects of sleep on memory - a trial."),
        Record(source="c", source_id="2", title="Editorial"),
        Record(source="d", source_id="2", title="Editorial"),
    ])
    assert mark_duplicates(store) == 2
    kept = store.records()
    assert [r.source for r in kept] == ["a", "c", "d"]
    assert kept[0].abstract == "An abstract."
    assert title_key("Café: Über-Résumé") == "cafeuberresume"


def test_same_source_record_is_not_added_twice(tmp_path):
    store = make_store(tmp_path, 2)
    assert store.add_records([Record(source="a", source_id="0", title="again")]) == 0


def test_ris():
    text = "TY  - JOUR\nTI  - A title\nAU  - Doe, J\nAU  - Roe, R\nPY  - 2021/03\nAB  - Line one\n  continues\nDO  - 10.1/a\nER  - \n\nTY  - JOUR\nT1  - Second\nER  - \n"
    first, second = read_ris(text)
    assert (first.title, first.authors, first.year, first.doi) == ("A title", ["Doe, J", "Roe, R"], 2021, "10.1/a")
    assert first.abstract == "Line one continues"
    assert second.title == "Second"


def test_bibtex():
    text = '@article{k1,\n title = {A {Nested} title, with comma},\n author = "Doe, J and Roe, R",\n year = 2020,\n doi = {10.1/b}\n}\n@comment{x}\n@inproceedings{k2, title={Other}, booktitle={Conf}}'
    first, second = read_bibtex(text)
    assert first.title == "A Nested title, with comma"
    assert first.authors == ["Doe, J", "Roe, R"]
    assert (first.year, first.doi) == (2020, "10.1/b")
    assert (second.title, second.venue) == ("Other", "Conf")


def test_csv():
    text = 'Title,Authors,Year,DOI,Source title\n"A, title",Doe J; Roe R,2019,10.1/c,J Things\n'
    (record,) = read_csv(text)
    assert (record.title, record.authors, record.year, record.venue) == ("A, title", ["Doe J", "Roe R"], 2019, "J Things")


def test_flow_and_export(tmp_path):
    store = make_store(tmp_path, 7)
    ids = [r.id for r in store.records()]
    store.update_record(ids[6], duplicate_of=ids[0])
    for i in ids[:4]:
        vote(store, i, "ana", INCLUDE)
    vote(store, ids[4], "ana", EXCLUDE)
    vote(store, ids[0], "ai", INCLUDE)
    vote(store, ids[3], "ai", EXCLUDE)
    assert [r.id for r in store.eligible(FULLTEXT)] == ids[:3]
    assert [r.id for r in store.todo(ABSTRACT, "ana")] == [ids[5]]
    vote(store, ids[0], "ana", INCLUDE, FULLTEXT)
    vote(store, ids[1], "ana", EXCLUDE, FULLTEXT, "Wrong design")
    vote(store, ids[2], "ana", UNAVAILABLE, FULLTEXT)

    f = report.flow(store)
    assert (f.duplicates, f.unique, f.excluded_abstract, f.open_abstract) == (1, 6, 1, 2)
    assert (f.sought, f.not_retrieved, f.assessed, f.included) == (3, 1, 2, 1)
    assert f.excluded_fulltext == {"Wrong design": 1}
    assert not f.complete
    assert "Wrong design: 1" in report.as_text(f)
    assert report.as_svg(f).startswith("<svg")

    assert [r.id for r in export.included(store)] == [ids[0]]
    assert len(export.to_csv(store, only_included=True).splitlines()) == 2
    assert "TI  - A sufficiently" in export.to_ris(export.included(store))
    assert export.to_bibtex(export.included(store)).startswith("@article{anon")


def test_ai_only_count(tmp_path):
    store = make_store(tmp_path, 2)
    a, b = [r.id for r in store.records()]
    vote(store, a, "ai", EXCLUDE)
    vote(store, b, "ai", EXCLUDE)
    vote(store, b, "ana", EXCLUDE)
    assert report.flow(store).ai_only[ABSTRACT] == 1


def test_kappa(tmp_path):
    assert agreement.cohen_kappa([("include", "include"), ("exclude", "exclude")]) == 1
    assert agreement.cohen_kappa([("include", "exclude"), ("exclude", "include")]) == -1
    assert agreement.cohen_kappa([("include", "include")]) is None
    store = make_store(tmp_path, 4)
    for r, (x, y) in zip(store.records(), [(INCLUDE, INCLUDE), (EXCLUDE, EXCLUDE), (INCLUDE, EXCLUDE), (EXCLUDE, EXCLUDE)]):
        vote(store, r.id, "ana", x)
        vote(store, r.id, "ben", y)
    result = agreement.compare(store, ABSTRACT, "ana", "ben")
    assert (result["both"], result["agreed"]) == (4, 3)
    assert round(result["kappa"], 2) == 0.5


def test_cli_walkthrough(tmp_path, monkeypatch, capsys):
    assert main(["init", str(tmp_path), "--title", "Sleep: a review", "--question", "Does it help?"]) == 0
    monkeypatch.chdir(tmp_path)
    protocol = load_protocol(tmp_path)
    assert protocol.title == "Sleep: a review" and protocol.exclude
    (tmp_path / "x.ris").write_text("TY  - JOUR\nTI  - The first long enough title here\nER  - \nTY  - JOUR\nTI  - The second long enough title here\nER  - \n")
    assert main(["add", "x.ris"]) == 0
    assert main(["decide", "1", "include", "--reviewer", "ana"]) == 0
    assert main(["decide", "2", "exclude", "--reviewer", "ana"]) == 0
    assert main(["decide", "1", "exclude", "--fulltext", "--reviewer", "ana"]) == 1
    assert main(["decide", "2", "include", "--fulltext", "--reviewer", "ana"]) == 1
    assert main(["decide", "1", "include", "--fulltext", "--reviewer", "ana"]) == 0
    assert main(["report"]) == 0
    assert "Studies included in the review: 1" in (tmp_path / "prisma.md").read_text()
    assert main(["export", "--format", "bib"]) == 0
    assert (tmp_path / "included.bib").exists()
    assert main(["list", "--json"]) == 0


def test_save_searches_keeps_rest_of_protocol(tmp_path):
    main(["init", str(tmp_path), "--title", "T", "--question", "Q"])
    save_searches(tmp_path, [{"source": "pubmed", "query": 'a[tiab] AND "b c"[tiab]'}])
    text = (tmp_path / "protocol.yaml").read_text()
    protocol = load_protocol(tmp_path)
    assert protocol.searches == [{"source": "pubmed", "query": 'a[tiab] AND "b c"[tiab]'}]
    assert "# The review protocol" in text and protocol.year_from == 2015


def test_near_duplicates(tmp_path):
    store = Store(tmp_path / "t.db")
    abstract = "We study the effect of sleep on memory consolidation in adults. " * 8
    store.add_records([
        Record(source="a", source_id="1", title="Sleep and memory consolidation in older adults: a randomised trial",
               authors=["Jane Doe", "R Roe"], year=2021, abstract=abstract),
        Record(source="b", source_id="1", title="Sleep and memory consolidation in older adults: a randomized trial",
               authors=["Doe, Jane"], year=2022),
        Record(source="c", source_id="1", title="Does sleeping well help you remember? A trial",
               authors=["Doe J"], year=2022, abstract=abstract),
        Record(source="d", source_id="1", title="Sleep and memory consolidation in older adults: a randomised trial, part 2",
               authors=["Jane Doe"], year=2021),
        Record(source="e", source_id="1", title="Sleep and memory consolidation in older adults: a randomized trials",
               authors=["Someone Else"], year=2021),
    ])
    assert mark_duplicates(store) == 2
    assert [r.source for r in store.records()] == ["a", "d", "e"]


def test_extraction_table(tmp_path):
    main(["init", str(tmp_path), "--title", "T", "--question", "Q"])
    protocol = load_protocol(tmp_path)
    assert [f["name"] for f in protocol.extraction] == ["design", "sample_size"]
    store = make_store(tmp_path, 2)
    a, b = [r.id for r in store.records()]
    for i in (a, b):
        vote(store, i, "ana", INCLUDE)
        vote(store, i, "ana", INCLUDE, FULLTEXT)
    store.set_value(a, "design", "ai:m", "cohort", "We followed a cohort.")
    store.set_value(a, "sample_size", "ai:m", "120")
    store.set_value(a, "design", "ana", "RCT")
    rows = extract.table(store, protocol).splitlines()
    assert rows[0].endswith("design,sample_size,unchecked_ai_fields")
    assert rows[1].endswith("RCT,120,sample_size")
    assert rows[2].endswith(",,")
