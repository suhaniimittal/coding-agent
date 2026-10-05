"""Turns verified TDD changes into actual file content — one LLM call per
service, covering every modify/create file in that service's changeset at
once. A "delete" needs no LLM call at all — there's nothing to write, so
callers apply it directly without going through this module.

The call is asked for structured JSON (one entry per file), not free-form
text with multiple files concatenated — with no repair loop to catch a bad
parse, the response has to be trustworthy on the first try.

Same provider-fallback split as summarizer.py/embedder.py: AI Gateway if
AGENTS_GATEWAY_KEY + AI_GATEWAY_URL are set, else OpenAI directly.
"""

from __future__ import annotations

import json
import os

from .models import FilePatch, VerifiedChange

_GATEWAY_PROVIDER, _GATEWAY_MODEL = "anthropic", "claude-sonnet-4-6"
_OPENAI_MODEL = "gpt-4o"


class PatcherError(Exception):
    """Raised when the LLM call for a service fails outright, or its
    response can't be parsed as the expected JSON shape at all — caller
    (coding_flow.py) must skip every change in this service's call rather
    than apply anything half-formed. A single file missing from an
    otherwise-valid response is NOT this error — see generate_service_patch."""


def group_verified_changes(changes: list[VerifiedChange]) -> dict[str, list[VerifiedChange]]:
    """Groups verified changes by file_path so a file touched by more than
    one FileChange entry is still described once in the prompt, with all of
    its requested changes listed together."""
    groups: dict[str, list[VerifiedChange]] = {}
    for v in changes:
        groups.setdefault(v.change.file_path, []).append(v)
    return groups


def effective_change_type(group: list[VerifiedChange]) -> str:
    """A single file's change_type, resolved from every FileChange entry
    that targets it — never just the first entry's, since the TDD can
    describe one file with a mix of change_types (e.g. a "modify" entry for
    the surrounding file plus a "create" entry for a new class added inside
    it, when there's no dedicated "new symbol in an existing file"
    change_type). "modify" wins whenever present: the file demonstrably
    already exists, so treating it as brand-new ("create") would tell the
    LLM to invent content from scratch and silently discard everything
    already there."""
    types = {v.change.change_type for v in group}
    if "modify" in types:
        return "modify"
    if "delete" in types:
        return "delete"
    return "create"


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
            max_tokens=16000,
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
        max_tokens=16000,
    )
    return resp.choices[0].message.content


def _strip_fences(text: str) -> str:
    """The model is told not to use fences, but strip them defensively —
    same discipline as summarizer.py's _parse_response, since a stray
    ```json fence would otherwise break JSON parsing below."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _build_prompt(service: str, file_groups: dict[str, list[VerifiedChange]], file_contents: dict[str, str | None]) -> tuple[str, str]:
    system_prompt = (
        "You are making precise, minimal code changes to a set of files in "
        "one real codebase's service, based on a technical design document. "
        "Respond with ONLY a JSON array, no markdown fences, no commentary "
        "before or after it. Each element must be an object with exactly "
        'these keys: "file_path" (string, must exactly match one of the '
        '"file_path" values listed below), "change_type" ("modify" or '
        '"create", copied from the input), and "content" (string — the '
        "COMPLETE content of the file, not a diff or excerpt, ready to "
        "write directly to disk as-is). Return exactly one array entry per "
        "file listed below — never omit a listed file, never add one that "
        "wasn't listed."
    )

    files_section = []
    for file_path, group in file_groups.items():
        change_type = effective_change_type(group)
        requested_changes = [
            {
                "function_or_symbol": v.change.function_or_symbol,
                "current_behavior": v.change.current_behavior,
                "change_description": v.change.change_description,
                "implementation_notes": v.change.implementation_notes,
                "pseudocode_sketch": v.change.pseudocode_sketch,
                "acceptance_criteria": v.change.acceptance_criteria,
                "reasoning": v.change.reasoning,
            }
            for v in group
        ]
        files_section.append(
            {
                "file_path": file_path,
                "change_type": change_type,
                "current_content": file_contents.get(file_path) if change_type == "modify" else None,
                "requested_changes": requested_changes,
            }
        )

    user_prompt = f"Service: {service}\n\nFiles to change:\n" + json.dumps(files_section, indent=2)
    return system_prompt, user_prompt


def _parse_response(raw: str) -> dict[str, dict]:
    """Parses the LLM's JSON array into {file_path: entry}. Raises
    PatcherError only when the response isn't usable AT ALL (not valid
    JSON, not a list, or an element missing its required keys) — a valid
    list simply missing one of the requested files is not an error here,
    that's handled per-file by the caller."""
    text = _strip_fences(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise PatcherError(f"LLM response was not valid JSON: {e}") from e
    if not isinstance(parsed, list):
        raise PatcherError("LLM response JSON must be a list")

    by_path: dict[str, dict] = {}
    for entry in parsed:
        if not isinstance(entry, dict) or "file_path" not in entry or "content" not in entry:
            raise PatcherError(f"malformed entry in LLM response: {entry!r}")
        by_path[entry["file_path"]] = entry
    return by_path


async def generate_service_patch(
    service: str,
    changes: list[VerifiedChange],
    file_contents: dict[str, str | None],
) -> tuple[list[FilePatch], list[str]]:
    """One LLM call producing the full corrected content for every
    modify/create file in this service's changeset at once. `changes` must
    only contain modify/create entries — a caller applies "delete" directly,
    no LLM call needed for it.

    Returns (patches, missing_file_paths): `missing_file_paths` lists any
    requested file whose entry was absent or empty in an otherwise-valid
    response — the plan's "structured, not free-form" contract treats a
    missing file as failed/skipped for that file, not silently dropped and
    not grounds to fail the whole service.

    Raises PatcherError only when the call itself fails or the response
    can't be parsed as the expected JSON shape at all.
    """
    if not changes:
        return [], []

    file_groups = group_verified_changes(changes)
    system_prompt, user_prompt = _build_prompt(service, file_groups, file_contents)

    try:
        if _use_gateway():
            raw = await _call_via_gateway(system_prompt, user_prompt)
        else:
            raw = await _call_via_openai(system_prompt, user_prompt)
    except Exception as e:  # noqa: BLE001 - a provider error must surface as a clear per-service failure
        raise PatcherError(f"{service}: LLM call failed: {e}") from e

    by_path = _parse_response(raw)

    patches: list[FilePatch] = []
    missing: list[str] = []
    for file_path, group in file_groups.items():
        entry = by_path.get(file_path)
        content = entry.get("content") if entry else None
        if not isinstance(content, str) or not content.strip():
            missing.append(file_path)
            continue
        change_type = effective_change_type(group)
        changes_applied = [v.change.change_description for v in group]
        patches.append(FilePatch(service, file_path, change_type, content, changes_applied))

    return patches, missing
