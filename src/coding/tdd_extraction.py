"""Turns a TDD PDF's extracted (unstructured) text back into the structured
TechnicalDesignDoc schema the coding pipeline needs — one LLM call, schema-
validated afterward, never trusted blind. Same "never guess, validate the
parse" pattern the planning project's planner.py uses for its own
generation call — this isn't validating a PLAN's correctness (that's
explicitly rejected elsewhere in this pipeline, see coding_flow.py's
module docstring), it's recovering structure from a document, which is a
fundamentally different, unavoidable task.

Same provider-fallback split as patcher.py: AI Gateway if
AGENTS_GATEWAY_KEY + AI_GATEWAY_URL are set, else OpenAI directly.
"""

from __future__ import annotations

import json
import os

from pydantic import ValidationError

from .tdd_models import TechnicalDesignDoc

_GATEWAY_PROVIDER, _GATEWAY_MODEL = "anthropic", "claude-sonnet-4-6"
_OPENAI_MODEL = "gpt-4o"


class TddExtractionError(Exception):
    """Raised when the extracted PDF text can't be turned into a valid
    TechnicalDesignDoc at all — caller must not proceed to the coding
    pipeline with nothing usable."""


def _build_prompt(pdf_text: str) -> tuple[str, str]:
    schema = json.dumps(TechnicalDesignDoc.model_json_schema(), indent=2)
    system_prompt = (
        "You are extracting structured data from a Technical Design Document "
        "that was just converted from PDF to plain text. The document is "
        "already real and complete — you are not writing or judging it, only "
        "reading it faithfully and returning its content as JSON matching "
        "this exact schema:\n\n"
        f"{schema}\n\n"
        "Rules: use ONLY information actually present in the document text — "
        "never invent a service, file, or change that isn't there. If the "
        "document doesn't clearly separate changes by file, use your best "
        "reading of its 'Files to Create/Modify/Delete' or per-service change "
        "sections to reconstruct the per-file breakdown. Respond with ONLY a "
        "JSON object, no markdown fences, no commentary before or after it."
    )
    user_prompt = f"Document text:\n\n{pdf_text}"
    return system_prompt, user_prompt


def _use_gateway() -> bool:
    return bool(os.environ.get("AGENTS_GATEWAY_KEY") and os.environ.get("AI_GATEWAY_URL"))


async def _call_via_gateway(system_prompt: str, user_prompt: str) -> str:
    from agent_lib.gateway.ai import AiGatewayClient

    async with AiGatewayClient(
        gateway_key=os.environ["AGENTS_GATEWAY_KEY"],
        gateway_url=os.environ["AI_GATEWAY_URL"],
    ) as client:
        reply = await client.chat(
            provider=_GATEWAY_PROVIDER,
            model_name=_GATEWAY_MODEL,
            prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=12000,
        )
        return reply["content"]


async def _call_via_openai(system_prompt: str, user_prompt: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI()  # reads OPENAI_API_KEY from the environment
    resp = await client.chat.completions.create(
        model=_OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=12000,
    )
    return resp.choices[0].message.content


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


async def extract_tdd(pdf_text: str) -> TechnicalDesignDoc:
    """The one LLM call in this project that isn't validating a plan's
    correctness — it's recovering structure from a document. Raises
    TddExtractionError on any failure (call failure, invalid JSON, or a
    schema mismatch) — no repair/placeholder logic like the planning
    side's parse_tdd() has, since this schema is small enough that a
    genuine mismatch here means something is actually wrong with the
    input PDF, not worth quietly papering over."""
    system_prompt, user_prompt = _build_prompt(pdf_text)

    try:
        if _use_gateway():
            raw = await _call_via_gateway(system_prompt, user_prompt)
        else:
            raw = await _call_via_openai(system_prompt, user_prompt)
    except Exception as e:  # noqa: BLE001 - a provider error must surface as a clear extraction failure
        raise TddExtractionError(f"LLM call failed: {e}") from e

    text = _strip_fences(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise TddExtractionError(f"not valid JSON: {e}\nRaw: {raw[:500]}") from e

    try:
        return TechnicalDesignDoc.model_validate(data)
    except ValidationError as e:
        raise TddExtractionError(f"JSON didn't match schema: {e}") from e
