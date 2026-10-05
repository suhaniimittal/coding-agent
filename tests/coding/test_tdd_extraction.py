from __future__ import annotations

import json

import pytest

from src.coding import tdd_extraction
from src.coding.tdd_extraction import TddExtractionError, extract_tdd

_VALID_TDD_JSON = json.dumps(
    {
        "title": "Fix total calculation",
        "issue_summary": "Orders show the wrong total price",
        "services": [
            {
                "service": "orders",
                "changes": [
                    {
                        "file_path": "src/OrdersController.java",
                        "function_or_symbol": "calculateTotal",
                        "change_description": "Fix summation of item prices",
                        "implementation_notes": "Use a running total",
                        "pseudocode_sketch": "total = sum(prices)",
                        "acceptance_criteria": ["Total matches sum of item prices"],
                        "reasoning": "Off-by-one in the loop",
                        "change_type": "modify",
                    }
                ],
            }
        ],
    }
)


@pytest.mark.asyncio
async def test_extract_tdd_parses_a_valid_response(monkeypatch):
    monkeypatch.setattr(tdd_extraction, "_use_gateway", lambda: False)

    async def fake_call_via_openai(system_prompt, user_prompt):
        return _VALID_TDD_JSON

    monkeypatch.setattr(tdd_extraction, "_call_via_openai", fake_call_via_openai)

    tdd = await extract_tdd("Some extracted PDF text")

    assert tdd.title == "Fix total calculation"
    assert tdd.services[0].service == "orders"
    assert tdd.services[0].changes[0].file_path == "src/OrdersController.java"


@pytest.mark.asyncio
async def test_extract_tdd_strips_markdown_fences(monkeypatch):
    monkeypatch.setattr(tdd_extraction, "_use_gateway", lambda: False)

    async def fake_call_via_openai(system_prompt, user_prompt):
        return f"```json\n{_VALID_TDD_JSON}\n```"

    monkeypatch.setattr(tdd_extraction, "_call_via_openai", fake_call_via_openai)

    tdd = await extract_tdd("text")
    assert tdd.title == "Fix total calculation"


@pytest.mark.asyncio
async def test_extract_tdd_raises_on_invalid_json(monkeypatch):
    monkeypatch.setattr(tdd_extraction, "_use_gateway", lambda: False)

    async def fake_call_via_openai(system_prompt, user_prompt):
        return "not json at all"

    monkeypatch.setattr(tdd_extraction, "_call_via_openai", fake_call_via_openai)

    with pytest.raises(TddExtractionError, match="not valid JSON"):
        await extract_tdd("text")


@pytest.mark.asyncio
async def test_extract_tdd_raises_on_schema_mismatch(monkeypatch):
    monkeypatch.setattr(tdd_extraction, "_use_gateway", lambda: False)

    async def fake_call_via_openai(system_prompt, user_prompt):
        return json.dumps({"title": "Missing required fields"})

    monkeypatch.setattr(tdd_extraction, "_call_via_openai", fake_call_via_openai)

    with pytest.raises(TddExtractionError, match="didn't match schema"):
        await extract_tdd("text")


@pytest.mark.asyncio
async def test_extract_tdd_raises_when_the_llm_call_itself_fails(monkeypatch):
    monkeypatch.setattr(tdd_extraction, "_use_gateway", lambda: False)

    async def failing_call(system_prompt, user_prompt):
        raise RuntimeError("provider is down")

    monkeypatch.setattr(tdd_extraction, "_call_via_openai", failing_call)

    with pytest.raises(TddExtractionError, match="LLM call failed"):
        await extract_tdd("text")


@pytest.mark.asyncio
async def test_extract_tdd_uses_gateway_when_configured(monkeypatch):
    monkeypatch.setattr(tdd_extraction, "_use_gateway", lambda: True)
    calls = []

    async def fake_call_via_gateway(system_prompt, user_prompt):
        calls.append((system_prompt, user_prompt))
        return _VALID_TDD_JSON

    async def fail_openai(system_prompt, user_prompt):
        raise AssertionError("must not call OpenAI when the gateway is configured")

    monkeypatch.setattr(tdd_extraction, "_call_via_gateway", fake_call_via_gateway)
    monkeypatch.setattr(tdd_extraction, "_call_via_openai", fail_openai)

    tdd = await extract_tdd("extracted text")
    assert tdd.title == "Fix total calculation"
    assert len(calls) == 1
    assert "extracted text" in calls[0][1]
