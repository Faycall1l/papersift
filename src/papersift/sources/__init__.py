from . import arxiv, europepmc, openalex, pubmed, semanticscholar
from .http import SourceError

SOURCES = {
    "openalex": openalex,
    "pubmed": pubmed,
    "europepmc": europepmc,
    "arxiv": arxiv,
    "semanticscholar": semanticscholar,
}


def get_source(name: str):
    try:
        return SOURCES[name.lower()]
    except KeyError:
        raise SourceError(f"Unknown source '{name}'. Choose from: {', '.join(SOURCES)}") from None


__all__ = ["SOURCES", "SourceError", "get_source"]
