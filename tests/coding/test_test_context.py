import json

import pytest

from src.coding import coding_flow, patcher
from src.coding.models import FilePatch, VerifiedChange
from src.coding.tdd_models import FileChange
from src.coding.test_context import build_test_context

SECTION = "src/common/accounts/client/components/SettingsForm/sections/AtsIntegrationSection"
EXAMPLE = "src/apps/client/modules/Settings/pages/ApplicationSettings/components/AtsIntegrationSettings/view.test.tsx"


def _v(path, change_type="modify", notes="n", symbol="handleCcEmailAddressesBlur"):
    return VerifiedChange(
        "ui",
        "keystone/ui",
        None,
        FileChange(
            file_path=path,
            function_or_symbol=symbol,
            change_type=change_type,
            change_description="d",
            implementation_notes=notes,
            pseudocode_sketch="p",
            reasoning="r",
        ),
    )


def _repo(tmp_path, with_example=True, with_claude=True):
    (tmp_path / SECTION).mkdir(parents=True)
    (tmp_path / SECTION / "view.tsx").write_text(
        "export const AtsIntegrationSectionView = () => null;"
    )
    if with_example:
        (tmp_path / EXAMPLE).parent.mkdir(parents=True)
        (tmp_path / EXAMPLE).write_text("new SetupComponentBuilder().build();")
    if with_claude:
        (tmp_path / "CLAUDE.md").write_text("Use SetupComponentBuilder in tests.")
    return tmp_path


def test_new_test_gets_production_file_named_example_and_claude_md(tmp_path):
    repo = _repo(tmp_path)
    code = _v(f"{SECTION}/view.tsx")
    test = _v(
        f"{SECTION}/view.test.tsx",
        "create",
        notes=f"Reuse the setup from `ui/{EXAMPLE}`, then blur.",
    )
    groups = {code.change.file_path: [code], test.change.file_path: [test]}

    ctx = build_test_context(repo, groups, [code, test])

    refs = ctx[f"{SECTION}/view.test.tsx"]
    assert [r["path"] for r in refs] == [f"{SECTION}/view.tsx", EXAMPLE, "CLAUDE.md"]
    assert "ALSO being changed" in refs[0]["role"]  # the prod file is in this same call
    assert "SetupComponentBuilder" in refs[1]["content"]
    assert code.change.file_path not in ctx  # production files get no references


def test_falls_back_to_the_nearest_existing_test(tmp_path):
    repo = _repo(tmp_path, with_claude=False)
    (tmp_path / "src/common/accounts/client/components/SettingsForm/other.test.tsx").write_text("x")
    test = _v(f"{SECTION}/view.test.tsx", "create")

    ctx = build_test_context(repo, {test.change.file_path: [test]}, [test])

    paths = [r["path"] for r in ctx[f"{SECTION}/view.test.tsx"]]
    assert "src/common/accounts/client/components/SettingsForm/other.test.tsx" in paths


def test_long_reference_files_are_truncated(tmp_path):
    repo = _repo(tmp_path, with_example=False, with_claude=False)
    (tmp_path / SECTION / "view.tsx").write_text("x" * 50_000)
    code = _v(f"{SECTION}/view.tsx")
    test = _v(f"{SECTION}/view.test.tsx", "create")

    ctx = build_test_context(repo, {test.change.file_path: [test]}, [code, test])

    assert len(ctx[f"{SECTION}/view.test.tsx"][0]["content"]) < 13_000


def test_no_test_files_means_no_context(tmp_path):
    repo = _repo(tmp_path)
    code = _v(f"{SECTION}/view.tsx")
    assert build_test_context(repo, {code.change.file_path: [code]}, [code]) == {}


def test_prompt_carries_references_and_test_rules():
    test = _v(f"{SECTION}/view.test.tsx", "create")
    refs = {
        test.change.file_path: [
            {"path": EXAMPLE, "role": "example", "content": "SetupComponentBuilder"}
        ]
    }

    system, user = patcher._build_prompt("ui", {test.change.file_path: [test]}, {}, refs)

    entry = json.loads(user.split("Files to change:\n", 1)[1])[0]
    assert entry["reference_files"][0]["path"] == EXAMPLE
    assert "READ-ONLY" in system and "never invent" in system
    assert "validateAt" in system and "jest.fn()" in system


@pytest.mark.asyncio
async def test_coding_flow_passes_context_for_test_files(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    code = _v(f"{SECTION}/view.tsx")
    test = _v(f"{SECTION}/view.test.tsx", "create")
    seen = {}

    async def fake(service, changes, file_contents, context_files=None):
        seen["ctx"] = context_files
        return [FilePatch(service, code.change.file_path, "modify", "changed", [])], []

    monkeypatch.setattr(coding_flow.patcher, "generate_service_patch", fake)

    await coding_flow._build_patches(repo, "ui", [code, test])

    assert list(seen["ctx"]) == [f"{SECTION}/view.test.tsx"]


def test_path_wrapped_across_lines_in_the_pdf_is_still_found(tmp_path):
    repo = _repo(tmp_path, with_claude=False)
    head, tail = EXAMPLE.rsplit("/", 1)
    notes = f"Reuse the setup patterns from `{head}/\n{tail}` and blur."
    test = _v(f"{SECTION}/view.test.tsx", "create", notes=notes)

    ctx = build_test_context(repo, {test.change.file_path: [test]}, [test])

    assert EXAMPLE in [r["path"] for r in ctx[f"{SECTION}/view.test.tsx"]]


def test_example_tests_named_only_in_the_pdf_text_are_found(tmp_path):
    repo = _repo(tmp_path, with_claude=False)
    head, tail = EXAMPLE.rsplit("/", 1)
    pdf_text = f"How: Reuse the setup patterns from\n `{head}/\n   {tail}` and blur.\nFile: src/main/Other.kt"
    test = _v(f"{SECTION}/view.test.tsx", "create", notes="Reuse the existing setup patterns.")

    ctx = build_test_context(repo, {test.change.file_path: [test]}, [test], doc_text=pdf_text)

    refs = ctx[f"{SECTION}/view.test.tsx"]
    assert [r["path"] for r in refs] == [EXAMPLE]
    assert "named in the TDD" in refs[0]["role"]
