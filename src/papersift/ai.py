"""Screening by a language model, recorded as one more reviewer."""
from __future__ import annotations

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .project import Protocol, ProjectError, find_root
from .sources.http import SourceError, get
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


class Compatible:
    """A model behind any server that speaks the OpenAI chat-completions protocol.

    Covers hosted open models and local servers such as Ollama. These read text
    only, so they screen abstracts but not PDFs.
    """
    reads_pdf = False

    def __init__(self, base: str, key: str | None):
        self.url = base.rstrip("/") + "/chat/completions"
        self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if key:
            self.headers["Authorization"] = f"Bearer {key}"

    def complete(self, model: str, system: str, text: str) -> str:
        body = {"model": model, "temperature": 0, "max_tokens": 1024,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]}
        reply = json.loads(get(self.url, data=json.dumps(body).encode(), method="POST",
                               headers=self.headers, tries=6))
        return reply["choices"][0]["message"]["content"] or ""

    def ask(self, model: str, system: str, content: list[dict], schema: dict) -> dict | None:
        system += ("\n\nReply with one JSON object and nothing else. It must match this "
                   "JSON schema:\n" + json.dumps(schema))
        text = "\n\n".join(block["text"] for block in content if block["type"] == "text")
        for _ in range(2):
            reply = self.complete(model, system, text)
            try:
                answer = json.loads(reply[reply.index("{"):reply.rindex("}") + 1])
            except ValueError:
                continue
            if _matches(answer, schema):
                return answer
        return None


def _matches(answer, schema: dict) -> bool:
    """Check an answer against the small subset of JSON schema used here."""
    if schema["type"] == "string":
        return isinstance(answer, str) and answer in schema.get("enum", [answer])
    return (isinstance(answer, dict) and set(answer) == set(schema["required"])
            and all(_matches(answer[name], part) for name, part in schema["properties"].items()))


def client(model: str):
    """An API client, after checking that the credentials and the model work."""
    base = os.environ.get("PAPERSIFT_API_BASE")
    if base:
        api = Compatible(base, os.environ.get("PAPERSIFT_API_KEY"))
        try:
            api.complete(model, "Reply with the word ok.", "ok?")
        except (SourceError, KeyError, ValueError) as err:
            raise ProjectError(f"The model server at {base} did not answer for '{model}': {err}") from None
        return api
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
            "The AI features need an Anthropic API key in ANTHROPIC_API_KEY, or another "
            f"model server in PAPERSIFT_API_BASE. ({err})") from None
    return api


def reads_pdf(api) -> bool:
    return getattr(api, "reads_pdf", True)


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


def ask(api, model: str, system: str, content: list[dict], schema: dict, effort: str) -> dict | None:
    """One structured answer from the model, or None if it gave no usable answer."""
    if isinstance(api, Compatible):
        return api.ask(model, system, content, schema)
    response = api.messages.create(
        model=model,
        max_tokens=8000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason != "end_turn":
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _each(jobs: list, work):
    """Run work(job) over a few threads, yielding (job, answer) with None on API errors."""
    errors: tuple = (SourceError, KeyError)
    try:
        import anthropic
        errors += (anthropic.APIError,)
    except ImportError:
        pass

    def safe(job):
        try:
            return job, work(job)
        except errors:
            return job, None

    with ThreadPoolExecutor(WORKERS) as pool:
        for done, result in enumerate(pool.map(safe, jobs), 1):
            yield result
            if done % 25 == 0 or done == len(jobs):
                print(f"  {done} of {len(jobs)}", flush=True)


def screen_all(store: Store, protocol: Protocol, stage: str,
               model: str | None = None, limit: int | None = None) -> None:
    if not (protocol.include or protocol.exclude):
        raise ProjectError("The protocol has no criteria for the AI to apply.")
    model, root = model_name(model), find_root()
    api = client(model)
    if stage == FULLTEXT and not reads_pdf(api):
        raise ProjectError("This model server reads text only. Full-text AI screening "
                           "needs the Anthropic API.")
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

    schema, effort = _schema(stage), "low" if stage == ABSTRACT else "medium"
    counts = {INCLUDE: 0, EXCLUDE: 0, MAYBE: 0}
    failed = 0
    for (record, _), answer in _each(jobs, lambda job: ask(api, model, system, job[1], schema, effort)):
        if answer is None:
            failed += 1
            continue
        store.decide(Decision(record.id, stage, reviewer, answer["decision"],
                              answer["reason"] or None, answer["note"] or None))
        counts[answer["decision"]] += 1

    print(f"Include {counts[INCLUDE]}, exclude {counts[EXCLUDE]}, maybe {counts[MAYBE]}")
    if failed:
        print(f"{failed} got no answer and are left for a person. Run again to retry.")
    if no_text:
        print(f"{no_text} have no PDF under 30 MB. Run `papersift fetch` first, "
              "or screen them yourself.")
    print("The AI counts as one reviewer. Screen the same records yourself, or check a "
          "sample, with `papersift screen --show-ai`, then `papersift agreement`.")


EXTRACTION = (
    "You are extracting data from a study included in a systematic review. Answer each "
    "question from the document only. For each answer give the value, as short as it can "
    "be while staying exact, and a quote: one sentence copied word for word from the "
    "document that supports it. If the document does not report something, the value is "
    '"not reported" and the quote is empty. Where a question lists choices, the value '
    "must be one of them."
)


def extract_all(store: Store, protocol: Protocol, model: str | None = None,
                limit: int | None = None) -> None:
    from .export import included
    from .extract import require_fields

    require_fields(protocol)
    model, root = model_name(model), find_root()
    api = client(model)
    reviewer = f"ai:{model}"
    fields = protocol.extraction
    values = store.values()
    todo = [r for r in included(store)
            if not all(any(row["reviewer"] == reviewer for row in values.get(r.id, {}).get(f["name"], []))
                       for f in fields)][:limit]
    if not todo:
        print("Nothing left for the AI to extract.")
        return

    answer = {"type": "object", "properties": {"value": {"type": "string"}, "quote": {"type": "string"}},
              "required": ["value", "quote"], "additionalProperties": False}
    schema = {"type": "object", "properties": {f["name"]: answer for f in fields},
              "required": [f["name"] for f in fields], "additionalProperties": False}
    questions = "\n".join(
        f"- {f['name']}: {f['question']}" + (f" Choices: {', '.join(f['choices'])}." if f["choices"] else "")
        for f in fields)
    system = f"{EXTRACTION}\n\nReview: {protocol.title}\n\nQuestions:\n{questions}"

    jobs, abstract_only = [], 0
    for record in todo:
        content = _content(record, FULLTEXT, root) if reads_pdf(api) else None
        if content is None:
            abstract_only += 1
            content = _content(record, ABSTRACT, root)
        jobs.append((record, content))
    print(f"Extracting from {len(jobs)} studies as '{reviewer}'")

    failed = 0
    for (record, _), result in _each(jobs, lambda job: ask(api, model, system, job[1], schema, "medium")):
        if result is None:
            failed += 1
            continue
        for name, item in result.items():
            store.set_value(record.id, name, reviewer, item["value"], item["quote"] or None)
    if abstract_only:
        print(f"{abstract_only} had no PDF and were read from the abstract only.")
    if failed:
        print(f"{failed} got no answer. Run again to retry.")
    print("Check the values with `papersift extract`; unchecked ones are flagged in the export.")
