import io
import json
import urllib.error

import pytest

from papersift.sources import SourceError, arxiv, europepmc, get_source, http, openalex, pubmed, semanticscholar


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)


def fake_get(payload):
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return lambda *args, **kwargs: data


def test_get_source():
    assert get_source("PubMed") is pubmed
    with pytest.raises(SourceError):
        get_source("nope")


def test_openalex(monkeypatch):
    work = {
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x", "title": "T",
        "publication_year": 2020, "abstract_inverted_index": {"world": [1], "hello": [0]},
        "authorships": [{"author": {"display_name": "A B"}}],
        "primary_location": {"source": {"display_name": "J"}, "landing_page_url": "http://l"},
    }
    page = {"results": [work], "meta": {"count": 1, "next_cursor": None}}
    monkeypatch.setattr(openalex, "get", fake_get(page))
    assert openalex.count("q", 2019, 2021) == 1
    (record,) = list(openalex.search("q", 2019, 2021))
    assert (record.source_id, record.abstract, record.authors, record.venue) == ("W1", "hello world", ["A B"], "J")
    assert openalex.with_title("q", "T") == '(q) AND "T"'


def test_openalex_limit(monkeypatch):
    works = [{"id": f"https://openalex.org/W{i}", "title": "t"} for i in range(5)]
    monkeypatch.setattr(openalex, "get", fake_get({"results": works, "meta": {"next_cursor": "c"}}))
    assert len(list(openalex.search("q", limit=3))) == 3


def test_pubmed(monkeypatch):
    xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>42</PMID><Article>
    <Journal><Title>J</Title><JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue></Journal>
    <ArticleTitle>A <i>title</i></ArticleTitle>
    <Abstract><AbstractText Label="AIM">Do.</AbstractText><AbstractText>Done.</AbstractText></Abstract>
    <AuthorList><Author><ForeName>Ann</ForeName><LastName>Lee</LastName></Author></AuthorList>
    </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/y</ArticleId>
    </ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""

    def get(url, params=None, **kwargs):
        if "esearch" in url:
            return json.dumps({"esearchresult": {"count": "1", "idlist": ["42"]}}).encode()
        return xml
    monkeypatch.setattr(pubmed, "get", get)
    assert pubmed.count("q") == 1
    (record,) = list(pubmed.search("q", 2020, 2022))
    assert (record.source_id, record.title, record.year, record.doi) == ("42", "A title", 2021, "10.1/y")
    assert record.abstract == "AIM: Do. Done."
    assert record.authors == ["Ann Lee"]


def test_europepmc(monkeypatch):
    item = {"source": "MED", "id": "7", "doi": "10.1/z", "title": "T", "pubYear": "2019",
            "abstractText": "<b>Hi</b> there", "authorList": {"author": [{"fullName": "X Y"}]},
            "journalInfo": {"journal": {"title": "J"}}}
    page = {"hitCount": 1, "resultList": {"result": [item]}, "nextCursorMark": "*"}
    monkeypatch.setattr(europepmc, "get", fake_get(page))
    assert europepmc.count("q") == 1
    (record,) = list(europepmc.search("q", 2018))
    assert (record.source_id, record.year, record.venue) == ("MED:7", 2019, "J")
    assert "<" not in record.abstract


def test_arxiv(monkeypatch):
    xml = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/">
    <o:totalResults>1</o:totalResults><entry><id>http://arxiv.org/abs/2101.00001v2</id>
    <published>2021-01-01T00:00:00Z</published><title>T</title><summary>S</summary>
    <author><name>Q R</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/2101.00001v2"/></entry></feed>"""
    empty = b'<feed xmlns="http://www.w3.org/2005/Atom"/>'
    replies, queries = iter([xml, empty]), []

    def get(url, params=None, **kwargs):
        queries.append(params["search_query"])
        return next(replies)
    monkeypatch.setattr(arxiv, "get", get)
    (record,) = list(arxiv.search("q", 2020, 2022))
    assert record.source_id == "2101.00001"
    assert record.doi == "10.48550/arXiv.2101.00001"
    assert record.pdf_url.endswith("v2")
    assert "submittedDate:[202001010000 TO 202212312359]" in queries[0]


def test_semanticscholar(monkeypatch):
    paper = {"paperId": "abc", "title": "T", "year": 2022, "externalIds": {"ArXiv": "2201.1"},
             "authors": [{"name": "Z"}], "openAccessPdf": {"url": "http://p"}}
    monkeypatch.setattr(semanticscholar, "get", fake_get({"total": 1, "data": [paper]}))
    assert semanticscholar.count("q") == 1
    (record,) = list(semanticscholar.search("q"))
    assert record.doi == "10.48550/arXiv.2201.1" and record.pdf_url == "http://p"
    assert semanticscholar.paper_id(record) == "abc"


def test_http_retries_then_raises(monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError("http://x", 503, "busy", {}, io.BytesIO(b"busy"))
    monkeypatch.setattr(http.urllib.request, "urlopen", urlopen)
    with pytest.raises(SourceError):
        http.get("http://x", tries=3)
    assert len(calls) == 3
