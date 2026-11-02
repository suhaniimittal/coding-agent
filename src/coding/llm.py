"""The coding agent's model calls (2026-11-02): one prompt in, one answer out.

The coder (coder.py) keeps its own few rounds by resending the growing
prompt, so the same plain call works on both paths:

- AI Gateway when a gateway key is set (always, on the platform). The key
  may be under AGENTS_GATEWAY_KEY, BIFROST_VIRTUAL_KEY or AI_GATEWAY_KEY; the
  address is AI_GATEWAY_URL (or BIFROST_URL), else the SDK's default. The
  model is CODING_MODEL, else the strongest Claude the gateway offers: a
  model it rejects is skipped for the next.
- OpenAI directly otherwise (a laptop), with OPENAI_API_KEY, thinking on.

Secrets come from the environment only, and nothing here logs a prompt.
"""

from __future__ import annotations

import json
import os
import re

_GATEWAY_PROVIDER = "anthropic"
_GATEWAY_MODELS = ("claude-opus-5-5", "claude-opus-4-8", "claude-sonnet-4-6")
_KEY_NAMES = ("AGENTS_GATEWAY_KEY", "BIFROST_VIRTUAL_KEY", "AI_GATEWAY_KEY")
# Words in a gateway error that mean "this model is not available here".
_MODEL_REJECTED = re.compile(
    r"model|not found|404|does not exist|unknown|invalid|unsupported|no such", re.IGNORECASE
)
_OPENAI_MODEL = "gpt-5.4"
_OPENAI_REASONING_EFFORT = "high"

_working_model: str | None = None


class LlmError(Exception):
    """The model could not be reached or gave no answer."""


def _gateway_key() -> str:
    return next((v for v in (os.environ.get(n, "").strip() for n in _KEY_NAMES) if v), "")


def _gateway_url() -> str:
    url = os.environ.get("AI_GATEWAY_URL", "").strip() or os.environ.get("BIFROST_URL", "").strip()
    if url:
        return url
    from agent_lib.gateway.config import gateway_url

    return gateway_url()


def use_gateway() -> bool:
    return bool(_gateway_key())


def _models() -> list[str]:
    chosen = [_working_model, os.environ.get("CODING_MODEL", "").strip()]
    return list(dict.fromkeys(m for m in [*chosen, *_GATEWAY_MODELS] if m))


async def complete(system_prompt: str, user_prompt: str, max_tokens: int = 16000) -> str:
    """The model's answer as text. The prompts ask for one JSON object; on
    OpenAI that is also enforced by the response format."""
    try:
        if use_gateway():
            return await _via_gateway(system_prompt, user_prompt, max_tokens)
        return await _via_openai(system_prompt, user_prompt, max_tokens)
    except LlmError:
        raise
    except Exception as e:  # noqa: BLE001 - any provider error is one clear failure
        raise LlmError(f"model call failed: {type(e).__name__}: {e}") from e


async def _via_gateway(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    global _working_model
    from agent_lib.gateway.ai import AiGatewayClient

    models = _models()
    async with AiGatewayClient(gateway_key=_gateway_key(), gateway_url=_gateway_url()) as client:
        for model in models:
            try:
                reply = await client.chat(
                    provider=_GATEWAY_PROVIDER,
                    model_name=model,
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                )
            except Exception as error:
                if model == models[-1] or not _MODEL_REJECTED.search(str(error)):
                    raise
                print(f"  [CODER] {model} not available ({error}); trying the next model")
                continue
            if model != _working_model:
                print(f"  [CODER] answered by {model}")
            _working_model = model
            return reply["content"]
    raise LlmError("no model answered")  # unreachable: the last model raises


async def _via_openai(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI()  # reads OPENAI_API_KEY from the environment
    resp = await client.chat.completions.create(
        model=_OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        # Room for the thinking on top of the answer.
        max_completion_tokens=max_tokens + 24000,
        reasoning_effort=_OPENAI_REASONING_EFFORT,
    )
    return resp.choices[0].message.content or ""


def strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def parse_json(text: str) -> dict:
    """The one JSON object in a reply: as it is, without code fences, or the
    part from the first "{" to the last "}" when words surround it."""
    clean = strip_fences(text)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        if start < 0 or end <= start:
            raise LlmError("the answer holds no JSON object") from None
        try:
            value = json.loads(clean[start : end + 1])
        except json.JSONDecodeError as e:
            raise LlmError(f"the answer is not valid JSON: {e}") from e
    if not isinstance(value, dict):
        raise LlmError("the answer is JSON but not an object")
    return value
