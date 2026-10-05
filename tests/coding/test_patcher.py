from __future__ import annotations

import json

import pytest

from src.coding import patcher
from src.coding.models import VerifiedChange
from src.coding.tdd_models import FileChange


def _verified(**overrides) -> VerifiedChange:
    change_defaults = dict(
        file_path="src/orders/OrderService.java",
        function_or_symbol="createOrder",
        change_description="propagate payment failure",
        implementation_notes="n",
        pseudocode_sketch="p",
        reasoning="r",
        change_type=overrides.pop("change_type", "modify"),
    )
    change_defaults.update({k: v for k, v in overrides.items() if k in change_defaults})
    change = FileChange(**change_defaults)
    return VerifiedChange(
        service=overrides.get("service", "orders"),
        repo=overrides.get("repo", "org/orders"),
        branch=overrides.get("branch"),
        change=change,
    )


def test_group_verified_changes_by_file():
    a = _verified(file_path="a.java")
    b = _verified(file_path="a.java", function_or_symbol="other")
    c = _verified(file_path="b.java")

    groups = patcher.group_verified_changes([a, b, c])

    assert groups["a.java"] == [a, b]


@pytest.mark.parametrize(
    "types,expected",
    [
        (["modify"], "modify"),
        (["create"], "create"),
        (["delete"], "delete"),
        (["create", "modify"], "modify"),  # a real TDD shape: "add a new class" (create) + "modify" the same existing file
        (["modify", "create"], "modify"),  # order-independent — not just group[0]
        (["delete", "modify"], "modify"),
    ],
)
def test_effective_change_type_resolves_mixed_groups(types, expected):
    group = [_verified(change_type=t, file_path="a.java") for t in types]
    assert patcher.effective_change_type(group) == expected


@pytest.mark.asyncio
async def test_no_changes_returns_empty_without_llm_call(monkeypatch):
    called = False

    async def fake_call(*a, **kw):
        nonlocal called
        called = True
        return "[]"

    monkeypatch.setattr(patcher, "_call_via_openai", fake_call)

    patches, missing = await patcher.generate_service_patch("orders", [], {})

    assert not called
    assert patches == []
    assert missing == []


@pytest.mark.asyncio
async def test_single_llm_call_covers_all_files(monkeypatch):
    call_count = {"n": 0}

    async def fake_call(system_prompt, user_prompt):
        call_count["n"] += 1
        assert "a.java" in user_prompt
        assert "b.java" in user_prompt
        return json.dumps(
            [
                {"file_path": "a.java", "change_type": "modify", "content": "public class A {}"},
                {"file_path": "b.java", "change_type": "create", "content": "public class B {}"},
            ]
        )

    monkeypatch.setattr(patcher, "_use_gateway", lambda: False)
    monkeypatch.setattr(patcher, "_call_via_openai", fake_call)

    v_modify = _verified(change_type="modify", file_path="a.java")
    v_create = _verified(change_type="create", file_path="b.java")

    patches, missing = await patcher.generate_service_patch(
        "orders", [v_modify, v_create], {"a.java": "old content"}
    )

    assert call_count["n"] == 1
    assert missing == []
    by_path = {p.file_path: p for p in patches}
    assert by_path["a.java"].new_content == "public class A {}"
    assert by_path["a.java"].change_type == "modify"
    assert by_path["b.java"].new_content == "public class B {}"
    assert by_path["b.java"].change_type == "create"


@pytest.mark.asyncio
async def test_missing_file_in_response_is_reported_not_raised(monkeypatch):
    async def fake_call(system_prompt, user_prompt):
        return json.dumps([{"file_path": "a.java", "change_type": "modify", "content": "public class A {}"}])

    monkeypatch.setattr(patcher, "_use_gateway", lambda: False)
    monkeypatch.setattr(patcher, "_call_via_openai", fake_call)

    v_a = _verified(change_type="modify", file_path="a.java")
    v_b = _verified(change_type="create", file_path="b.java")

    patches, missing = await patcher.generate_service_patch("orders", [v_a, v_b], {"a.java": "old"})

    assert [p.file_path for p in patches] == ["a.java"]
    assert missing == ["b.java"]


@pytest.mark.asyncio
async def test_strips_markdown_fences(monkeypatch):
    async def fake_call(system_prompt, user_prompt):
        return "```json\n" + json.dumps([{"file_path": "a.java", "change_type": "modify", "content": "public class A {}"}]) + "\n```"

    monkeypatch.setattr(patcher, "_use_gateway", lambda: False)
    monkeypatch.setattr(patcher, "_call_via_openai", fake_call)
    v = _verified(change_type="modify", file_path="a.java")

    patches, missing = await patcher.generate_service_patch("orders", [v], {"a.java": "old"})

    assert patches[0].new_content == "public class A {}"
    assert missing == []


@pytest.mark.asyncio
async def test_invalid_json_raises_patcher_error(monkeypatch):
    async def fake_call(system_prompt, user_prompt):
        return "not json at all"

    monkeypatch.setattr(patcher, "_use_gateway", lambda: False)
    monkeypatch.setattr(patcher, "_call_via_openai", fake_call)
    v = _verified(change_type="modify", file_path="a.java")

    with pytest.raises(patcher.PatcherError, match="not valid JSON"):
        await patcher.generate_service_patch("orders", [v], {"a.java": "old"})


@pytest.mark.asyncio
async def test_non_list_json_raises_patcher_error(monkeypatch):
    async def fake_call(system_prompt, user_prompt):
        return json.dumps({"file_path": "a.java", "content": "x"})

    monkeypatch.setattr(patcher, "_use_gateway", lambda: False)
    monkeypatch.setattr(patcher, "_call_via_openai", fake_call)
    v = _verified(change_type="modify", file_path="a.java")

    with pytest.raises(patcher.PatcherError, match="must be a list"):
        await patcher.generate_service_patch("orders", [v], {"a.java": "old"})


@pytest.mark.asyncio
async def test_llm_failure_raises_patcher_error(monkeypatch):
    async def fake_call(system_prompt, user_prompt):
        raise RuntimeError("provider down")

    monkeypatch.setattr(patcher, "_use_gateway", lambda: False)
    monkeypatch.setattr(patcher, "_call_via_openai", fake_call)
    v = _verified(change_type="modify", file_path="a.java")

    with pytest.raises(patcher.PatcherError, match="LLM call failed"):
        await patcher.generate_service_patch("orders", [v], {"a.java": "old"})
