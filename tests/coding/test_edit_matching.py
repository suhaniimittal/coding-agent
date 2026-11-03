"""BTS-40: both edits to view.tsx were refused ("old_text was not found") and
a pull request was opened holding only the new test. These cover the four
fixes: the file a step changes is shown whole, a copied line number on a
blank line is still stripped, spaces at line ends don't block a match (and a
miss shows the closest real lines), and a failed edit opens no PR."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.coding import coder, coding_flow, context_pack, prompts, workspace
from src.coding.coder import _unnumber
from src.coding.repo_tools import RepoTools, ToolError
from src.coding.tdd_reader import FileHint, ServiceTarget, Step, Task

VIEW = "src/screens/view.tsx"


def _tools(tmp_path: Path, text: str, rel: str = VIEW) -> RepoTools:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return RepoTools(tmp_path)


def _task(where: str = VIEW) -> Task:
    return Task(
        title="Keep the archive action mounted",
        services=[ServiceTarget(service="ui", repo="o/ui", branch="develop", commit="0905c4e")],
        steps=[Step(service="ui", where=where, what="always pass actionButton")],
    )


# --- 1. the file a step changes is shown whole ---------------------------------------------


def _long_file() -> str:
    lines = [f"const filler{n} = {n};  // padding to make the file long" for n in range(1500)]
    lines[1203] = "  const updateArchiveButton = useCallback((api) => {"
    return "\n".join(lines) + "\n"


def test_a_where_file_over_the_normal_limit_is_shown_whole(tmp_path):
    tools = _tools(tmp_path, _long_file())
    pack = context_pack.build(tools, context_pack.Index(tools), _task(), "ui")
    assert len(_long_file()) > context_pack.FILE_CHARS
    assert "long: parts shown" not in pack.text
    assert " 1204  " in pack.text and " 1500  " in pack.text
    assert "the TDD says to change it" in pack.text


def test_a_file_only_listed_to_look_at_keeps_the_normal_limit(tmp_path):
    tools = _tools(tmp_path, _long_file())
    task = _task(where="")
    task.files_to_look_at = [FileHint(path=VIEW, why="context")]
    pack = context_pack.build(tools, context_pack.Index(tools), task, "ui")
    assert "long: parts shown" in pack.text


# --- 2. line numbers copied into old_text --------------------------------------------------


def test_unnumber_strips_a_blank_line_that_lost_its_two_spaces():
    copied = " 1206    const a = 1;\n 1207\n 1208    const b = 2;"
    assert _unnumber(copied) == "  const a = 1;\n\n  const b = 2;"


def test_unnumber_leaves_real_code_alone():
    code = "  const a = 1;\n\n  return a;"
    assert _unnumber(code) == code


# --- 3. matching ---------------------------------------------------------------------------


def test_spaces_at_line_ends_do_not_block_a_single_match(tmp_path):
    tools = _tools(tmp_path, "a();\n  if (x) {\n    y();\n  }\nz();\n")
    tools.replace_in_file(VIEW, "  if (x) {   \n    y();\n  }", "  if (x) {\n    w();\n  }")
    assert (tmp_path / VIEW).read_text() == "a();\n  if (x) {\n    w();\n  }\nz();\n"


def test_a_miss_shows_the_closest_real_lines(tmp_path):
    tools = _tools(tmp_path, "a();\n  const allArchived = rows.every((r) => r.isArchived);\nz();\n")
    with pytest.raises(ToolError) as e:
        tools.replace_in_file(VIEW, "const allArchived = rows.every((r) => r.archived);", "x")
    assert "closest text in the file is near line 2" in str(e.value)
    assert "    2    const allArchived = rows.every((r) => r.isArchived);" in str(e.value)
    assert (tmp_path / VIEW).read_text().startswith("a();")  # nothing written


def test_wrong_indentation_is_still_refused(tmp_path):
    tools = _tools(tmp_path, "  if (x) {\n    y();\n  }\n")
    with pytest.raises(ToolError):
        tools.replace_in_file(VIEW, "if (x) {\n  y();\n}", "z")


# --- 4. a failed edit opens no pull request ------------------------------------------------


def test_a_failed_edit_opens_no_pull_request(tmp_path, monkeypatch):
    root = tmp_path / "ui"
    (root / "src").mkdir(parents=True)
    (root / "src" / "view.test.tsx").write_text("test\n")
    checkout = workspace.Checkout("run-1-ab", "ui", "o/ui", "develop", "0905c4e", root)
    monkeypatch.setattr(coding_flow.github_api, "branch_exists", lambda *a, **k: False)
    monkeypatch.setattr(coding_flow.workspace, "download", lambda *a, **k: checkout)

    async def fake_run(*a, **k):
        return coder.Outcome(
            "changed",
            {"plan": [], "edits": []},
            problems=[f"edit 1 ({VIEW}): old_text was not found in {VIEW}; copy...\n  12  code"],
        )

    monkeypatch.setattr(coding_flow.coder, "run", fake_run)
    pushed = []
    monkeypatch.setattr(coding_flow.github_api, "create_branch_commit", lambda *a: pushed.append(a))

    result = asyncio.run(
        coding_flow._one_service(_task(), _task().services[0], "run-1-ab", "sm/KS-1")
    )
    assert result["status"] == "failed"
    assert "no pull request was opened" in result["reason"]
    assert "12  code" not in result["reason"]  # no code in the reason
    assert pushed == []


# --- 5. edits by line number: the model no longer re-types the old code ------------------

ARCHIVE = """\
const a = 1;

  const updateArchiveButton = useCallback((api) => {
    const rows = api.getSelectedRows();
    const allArchived = rows.every((r) => r.isArchived);
  }, []);

            actionButton={
              selectedReports.length > 0
                ? { id: "archive" }
                : null
            }
"""


def _session(tmp_path: Path, text: str = ARCHIVE):
    tools = _tools(tmp_path, text)
    return coder._Session("ui", tools, context_pack.Index(tools))


def _line_edit(start, end, first, last, new):
    return {
        "path": VIEW,
        "start_line": start,
        "end_line": end,
        "first_line": first,
        "last_line": last,
        "new": new,
    }


def test_several_line_edits_to_one_file_all_apply(tmp_path):
    s = _session(tmp_path)
    change = {
        "plan": [{"path": VIEW}],
        "edits": [
            _line_edit(
                4,
                5,
                "const rows = api.getSelectedRows();",
                "const allArchived = rows.every((r) => r.isArchived);",
                "    const rows = api.getSelectedRows();\n"
                "    const allArchived = rows.length > 0 && rows.every((r) => r.isArchived);",
            ),
            # numbered on the file as shown, although edit 1 came first
            _line_edit(
                8, 12, "actionButton={", "}", '            actionButton={{ id: "archive" }}'
            ),
        ],
    }
    problems, _ = coder._apply_and_check(s, change)
    assert [p for p in problems if p.startswith("edit")] == []
    text = (tmp_path / VIEW).read_text()
    assert "rows.length > 0 && rows.every" in text
    assert 'actionButton={{ id: "archive" }}' in text and "selectedReports.length" not in text


def test_a_miscounted_line_number_is_found_by_its_lines(tmp_path):
    s = _session(tmp_path)
    edit = _line_edit(
        6,
        6,
        "const allArchived = rows.every((r) => r.isArchived);",
        "const allArchived = rows.every((r) => r.isArchived);",
        "    const allArchived = false;",
    )
    problems, _ = coder._apply_and_check(s, {"plan": [{"path": VIEW}], "edits": [edit]})
    assert [p for p in problems if p.startswith("edit")] == []
    assert "const allArchived = false;" in (tmp_path / VIEW).read_text()


def test_wrong_lines_are_refused_with_what_is_there_and_nothing_written(tmp_path):
    s = _session(tmp_path)
    good = _line_edit(1, 1, "const a = 1;", "const a = 1;", "const a = 2;")
    bad = _line_edit(4, 4, "something not in the file", "something not in the file", "x")
    problems, _ = coder._apply_and_check(s, {"plan": [{"path": VIEW}], "edits": [good, bad]})
    assert any(p.startswith("edit 2") and "the file there reads" in p for p in problems)
    assert any(p.startswith("edit 1") and "not applied" in p for p in problems)
    assert (tmp_path / VIEW).read_text() == ARCHIVE


def test_overlapping_line_edits_are_refused(tmp_path):
    tools = _tools(tmp_path, ARCHIVE)
    with pytest.raises(ToolError, match="overlap"):
        tools.replace_lines(VIEW, [(3, 5, "x"), (5, 6, "y")])


# --- 6. code only: test files are left to CI ---------------------------------------------

TEST_FILE = "src/screens/archiveAction.test.tsx"


def test_test_edits_are_dropped_by_default_and_the_code_edit_applies(tmp_path, monkeypatch):
    monkeypatch.delenv("CODING_WRITE_TESTS", raising=False)
    s = _session(tmp_path)
    change = {
        "plan": [{"path": VIEW}, {"path": TEST_FILE}],
        "edits": [
            _line_edit(1, 1, "const a = 1;", "const a = 1;", "const a = 2;"),
            {"path": TEST_FILE, "create": "it('works', () => {});\n"},
        ],
    }
    problems, result = coder._apply_and_check(s, change)
    assert problems == []
    assert not (tmp_path / TEST_FILE).exists()
    assert (tmp_path / VIEW).read_text().startswith("const a = 2;")
    assert any(TEST_FILE in n for n in result.notes)


def test_test_edits_apply_when_switched_on(tmp_path, monkeypatch):
    monkeypatch.setenv("CODING_WRITE_TESTS", "true")
    s = _session(tmp_path)
    change = {"plan": [{"path": TEST_FILE}], "edits": [{"path": TEST_FILE, "create": "x\n"}]}
    coder._apply_and_check(s, change)
    assert (tmp_path / TEST_FILE).exists()


def test_the_prompt_says_which_test_rule_applies():
    assert "Do not create, change or delete any test file" in prompts.system(False)
    assert "add the matching test" in prompts.system(True)
    assert "{tests_rule}" not in prompts.system(False)
