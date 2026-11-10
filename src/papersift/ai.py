"""Screening by a language model, recorded as one more reviewer."""
from __future__ import annotations

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .project import Protocol, ProjectError, find_root
from .store import ABSTRACT, EXCLUDE, FULLTEXT, INCLUDE, MAYBE, Decision, Record, Store

DEFAULT_MODEL = "claude-opus-5-5"
MAX_PDF_BYTES = 30 * 1024 * 1024
WORKERS = 4

INSTRUCTIONS = {
    ABSTRACT: (
        "You are screening titles and abstracts for a systematic review. "
        "Decide from the title and abstract alone.\n"
        "- include: the record plausibly meets every inclusion criterion and no exclusion criterion.\n"
        "- exclude: the record clearly fails an inclusion criterion or clearly meets an exclusion criterion.\n"
        "- maybe: the abstract is missing or does not say enough to tell.\n"
        "Missing a relevant study is worse than passing an irrelevant one to full-text "
        "review, so exclude only when the abstract leaves no real doubt."
    ),
    FULLTEXT: (
        "You are assessing the full text of a report for eligibility in a systematic review. "
        "Read the whole document. Include it only if it meets every inclusion criterion "
        "and no exclusion criterion."
    ),
}


def model_name(model: str | None = None) -> str:
    return model or os.environ.get("PAPERSIFT_MODEL") or DEFAULT_MODEL


def client(model: str):
    """An API client, after checking that the credentials and the model work."""
    try:
        import anthropic
    except ImportError:
        raise ProjectError('The AI features need an extra install: pip install "papersift[ai]"') from None
    try:
        api = anthropic.Anthropic()
        api.models.retrieve(model)
    except anthropic.NotFoundError:
        raise ProjectError(f"The Anthropic API has no model named '{model}'.") from None
    except anthropic.APIConnectionError as err:
        raise ProjectError(f"Could not reach the Anthropic API: {err}") from None
    except (anthropic.AnthropicError, TypeError) as err:
        raise ProjectError(
            f"The AI features need an Anthropic API key in ANTHROPIC_API_KEY. ({err})") from None
    return api


def protocol_text(protocol: Protocol) -> str:
    lines = [f"Review: {protocol.title}", f"Question: {protocol.question}", "", "Inclusion criteria:"]
    lines += [f"- {c}" for c in protocol.include]
    lines += ["", "Exclusion criteria:"]
    lines += [f"- {c}" for c in protocol.exclude]
    return "\n".join(lines)


def _schema(stage: str) -> dict:
    choices = [INCLUDE, EXCLUDE, MAYBE] if stage == ABSTRACT else [INCLUDE, EXCLUDE]
    return {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": choices},
            "reason": {
                "type": "string",
                "description": "When excluding: the one criterion that decides it, copied "
                               "word for word from the protocol. Otherwise empty.",
            },
            "note": {"type": "string", "description": "One sentence explaining the decision."},
        },
        "required": ["decision", "reason", "note"],
        "additionalProperties": False,
    }


def _content(record: Record, stage: str, root: Path) -> list[dict] | None:
    if stage == ABSTRACT:
        text = f"Title: {record.title}\n\nAbstract: {record.abstract or '(none available)'}"
        return [{"type": "text", "text": text}]
    if not record.pdf_path:
        return None
    data = (root / record.pdf_path).read_bytes()
    if len(data) > MAX_PDF_BYTES:
        return None
    return [
        {"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf",
            "data": base64.standard_b64encode(data).decode("ascii"),
        }},
        {"type": "text", "text": f"Title: {record.title}\n\nAssess this report."},
    ]


def judge(api, model: str, system: str, stage: str, content: list[dict]) -> dict | None:
    """One decision from the model, or None if it gave no usable answer."""
    response = api.messages.create(
        model=model,
        max_tokens=4000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={
            "effort": "low" if stage == ABSTRACT else "medium",
            "format": {"type": "json_schema", "schema": _schema(stage)},
        },
    )
    if response.stop_reason != "end_turn":
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def screen_all(store: Store, protocol: Protocol, stage: str,
               model: str | None = None, limit: int | None = None) -> None:
    import anthropic

    if not (protocol.include or protocol.exclude):
        raise ProjectError("The protocol has no criteria for the AI to apply.")
    model, root = model_name(model), find_root()
    api = client(model)
    reviewer = f"ai:{model}"
    system = INSTRUCTIONS[stage] + "\n\n" + protocol_text(protocol)
    queue = store.todo(stage, reviewer)[:limit]
    if not queue:
        print("Nothing left for the AI to screen at this stage.")
        return

    jobs, no_text = [], 0
    for record in queue:
        content = _content(record, stage, root)
        if content is None:
            no_text += 1
        else:
            jobs.append((record, content))
    print(f"Screening {len(jobs)} records as reviewer '{reviewer}'")

    def work(job):
        record, content = job
        try:
            return record, judge(api, model, system, stage, content), None
        except anthropic.APIError as err:
            return record, None, err

    counts = {INCLUDE: 0, EXCLUDE: 0, MAYBE: 0}
    failed = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for done, (record, answer, error) in enumerate(pool.map(work, jobs), 1):
            if answer is None:
                failed += 1
            else:
                store.decide(Decision(record.id, stage, reviewer, answer["decision"],
                                      answer["reason"] or None, answer["note"] or None))
                counts[answer["decision"]] += 1
            if done % 25 == 0 or done == len(jobs):
                print(f"  {done} of {len(jobs)}")

    print(f"Include {counts[INCLUDE]}, exclude {counts[EXCLUDE]}, maybe {counts[MAYBE]}")
    if failed:
        print(f"{failed} got no answer and are left for a person. Run again to retry.")
    if no_text:
        print(f"{no_text} have no PDF under 30 MB. Run `papersift fetch` first, "
              "or screen them yourself.")
    print("The AI counts as one reviewer. Screen the same records yourself, or check a "
          "sample, with `papersift screen --show-ai`, then `papersift agreement`.")
