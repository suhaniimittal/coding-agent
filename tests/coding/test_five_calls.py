"""The five calls (2026-11-03): understand -> write -> verify -> fix -> verify.
Our code between them: the checklist covers every requirement and test-plan
item, every item is met by an edit that changed a file, a "pass" must quote
a line that is in the code, a uniform indentation shift is removed, and the
budget of five calls (a resend included) is never passed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.coding import coder, coding_flow, context_pack, prompts, workspace
from src.coding.repo_tools import RepoTools
from src.coding.tdd_reader import ServiceTarget, Task

VIEW = "src/screens/view.tsx"
OLD = 'const archiveTitle = "Archive";'
NEW = 'const archiveTitle = "Archive/Unarchive";'
TEXT = f"{OLD}\nexport const pageSize = 25;\n"


def _task(**extra) -> Task:
    return Task(
        title="Archive button label",
        services=[ServiceTarget(service="ui", repo="o/ui", branch="develop", commit="0905c4e")],
        requirements=["No report selected: the button reads Archive/Unarchive"],
        **extra,
    )


def _tools(tmp_path: Path) -> RepoTools:
    target = tmp_path / VIEW
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEXT, encoding="utf-8")
    return RepoTools(tmp_path)


def _understood(**extra) -> str:
    return json.dumps(
        {
            "checklist": [
                {
                    "check": "No report selected: the button reads Archive/Unarchive",
                    "covers": ["R1"],
                    "kind": "change",
                    "where": f"{VIEW}:1",
                }
            ],
            "cause": "the title is set once",
            "locations": [{"path": VIEW, "what": "line 1", "why": "the label"}],
            "answers": [],
            "keep": [VIEW],
        }
        | extra
    )


def _write(new: str, was: str = OLD, met_by=None) -> str:
    edit = {
        "path": VIEW,
        "start_line": 1,
        "end_line": 1,
        "first_line": was,
        "last_line": was,
        "new": new,
    }
    return json.dumps(
        {
            "plan": [{"path": VIEW, "change": "the label", "why": "R1"}],
            "edits": [edit],
            "met_by": [{"item": "C1", "edits": [1]}] if met_by is None else met_by,
            "answers": [],
            "pr_title": "Archive label",
            "summary": "s",
        }
    )


def _verdict(verdict: str = "pass", quote: str = NEW, findings=()) -> str:
    item = {"id": "C1", "verdict": verdict, "path": VIEW, "line": 1, "quote": quote, "why": "w"}
    return json.dumps({"items": [item], "findings": list(findings)})


def _fake_model(monkeypatch, replies: list[str]):
    seen, sent = [], []

    async def complete(system, user, max_tokens=16000, effort=None):
        if system == prompts.UNDERSTAND:
            seen.append("understand")
        elif system == prompts.VERIFY:
            seen.append("verify")
        else:
            seen.append("write")
        sent.append(user)
        return replies.pop(0)

    monkeypatch.setattr(coder.llm, "complete", complete)
    return seen, sent


def _run(tmp_path: Path, task: Task | None = None, pack_text: str = "STARTER PACK"):
    tools = _tools(tmp_path)
    pack = context_pack.Pack(text=pack_text)
    return asyncio.run(coder.run(task or _task(), "ui", tools, context_pack.Index(tools), pack))


# --- the calls -------------------------------------------------------------------------------


def test_a_change_that_passes_its_check_takes_three_calls(tmp_path, monkeypatch):
    seen, sent = _fake_model(monkeypatch, [_understood(), _write(NEW), _verdict()])
    out = _run(tmp_path)
    assert seen == ["understand", "write", "verify"]
    assert out.status == "changed" and out.calls == 3 and out.problems == [] and out.verified
    assert out.checklist[0]["verdict"] == "pass" and out.checklist[0]["at"] == f"{VIEW}:1"
    assert "STARTER PACK" in sent[0]  # call 1: the whole starter pack
    assert "STARTER PACK" not in sent[1] and "## The files you kept" in sent[1]
    assert "- C1 [change covers R1]" in sent[1]  # the checklist goes to the change
    assert "BEFORE:" in sent[2] and "AFTER:" in sent[2]  # the check sees both
    assert "Archive label" not in sent[2]  # ...and not the author's account


def test_a_failed_item_is_fixed_and_the_whole_checklist_checked_again(tmp_path, monkeypatch):
    fixed = 'const archiveTitle = selected ? "Archive" : "Archive/Unarchive";'
    seen, sent = _fake_model(
        monkeypatch,
        [
            _understood(),
            _write(NEW),
            _verdict("fail"),
            _write(fixed, was=NEW),
            _verdict(quote=fixed),
        ],
    )
    out = _run(tmp_path)
    assert seen == ["understand", "write", "verify", "write", "verify"]
    assert out.calls == 5 and out.problems == [] and out.verified
    assert out.review and out.review[0].startswith("C1 not met")
    assert "C1 not met" in sent[3]  # the fix is told what failed
    assert (tmp_path / VIEW).read_text().startswith(fixed)


def test_a_pass_that_quotes_no_real_line_counts_as_a_fail(tmp_path, monkeypatch):
    seen, _ = _fake_model(
        monkeypatch,
        [
            _understood(),
            _write(NEW),
            _verdict(quote="const somethingElse = notInTheFile();"),
            _write(NEW + " // x", was=NEW),
            _verdict(quote=NEW),
        ],
    )
    out = _run(tmp_path)
    assert seen == ["understand", "write", "verify", "write", "verify"]
    assert "quotes no line that is in the code" in out.review[0]


def test_a_finding_goes_to_the_fix(tmp_path, monkeypatch):
    finding = {"path": VIEW, "line": 1, "problem": "label is wrong", "fix": "use the constant"}
    finding_without_a_fix = {"path": VIEW, "line": 1, "problem": "vague"}
    seen, _ = _fake_model(
        monkeypatch,
        [
            _understood(),
            _write(NEW),
            _verdict(findings=[finding, finding_without_a_fix]),
            _write(NEW + " // fixed", was=NEW),
            _verdict(),
        ],
    )
    out = _run(tmp_path)
    assert len(seen) == 5
    assert out.review == [f"{VIEW}:1: label is wrong Fix: use the constant"]  # vague one dropped


def test_an_item_with_no_edit_behind_it_goes_to_the_fix(tmp_path, monkeypatch):
    seen, sent = _fake_model(
        monkeypatch,
        [
            _understood(),
            _write(NEW, met_by=[]),
            _verdict(),
            _write(NEW + " // C1", was=NEW),
            _verdict(),
        ],
    )
    out = _run(tmp_path)
    assert len(seen) == 5 and out.problems == []
    assert "C1 has no edit in met_by" in sent[3]


def test_the_budget_of_five_calls_is_never_passed(tmp_path, monkeypatch):
    seen, _ = _fake_model(
        monkeypatch,
        ["not json", _understood(), _write(NEW), _verdict("fail"), _write(NEW + " // x", was=NEW)],
    )
    out = _run(tmp_path)
    assert out.calls == 5 and len(seen) == 5  # the resend took the second check's place
    assert out.status == "changed" and not out.verified
    reasons = coding_flow._draft_reasons(_task(), out)
    assert "the final change was not checked against the checklist" in reasons


def test_giving_up_in_call_1_stops_at_once(tmp_path, monkeypatch):
    seen, _ = _fake_model(monkeypatch, [json.dumps({"give_up": "needs a product decision"})])
    out = _run(tmp_path)
    assert seen == ["understand"] and out.status == "gave_up"
    assert (tmp_path / VIEW).read_text() == TEXT


def test_giving_up_the_fix_keeps_the_change_as_a_draft(tmp_path, monkeypatch):
    _fake_model(
        monkeypatch,
        [
            _understood(),
            _write(NEW),
            _verdict("fail"),
            json.dumps({"give_up": "the ticket and the test plan disagree"}),
        ],
    )
    out = _run(tmp_path)
    assert out.status == "changed" and out.problems[0].startswith("C1 not met")
    assert any("the fix was given up" in n for n in out.notes)
    assert coding_flow._draft_reasons(_task(), out)


def test_what_call_1_asked_for_is_shown_to_the_change(tmp_path, monkeypatch):
    need = {"need": {"search": [{"text": "pageSize"}]}}
    _, sent = _fake_model(monkeypatch, [_understood(**need), _write(NEW), _verdict()])
    _run(tmp_path)
    assert "## What you asked to see" in sent[1] and "export const pageSize" in sent[1]


def test_answers_from_call_1_count(tmp_path, monkeypatch):
    question = "Does the grid keep the button width?"
    answer = {"item": question, "answer": "no", "evidence": "InfiniteScrollGrid/view.tsx:119"}
    _fake_model(monkeypatch, [_understood(answers=[answer]), _write(NEW), _verdict()])
    out = _run(tmp_path, _task(open_questions=[question]))
    assert out.problems == [] and out.change["answers"] == [answer]


# --- the checklist ---------------------------------------------------------------------------


def test_requirements_and_test_plan_items_no_item_covers_are_added(tmp_path):
    tools = _tools(tmp_path)
    s = coder._Session("ui", tools, context_pack.Index(tools))
    task = _task(tests=["Select one archived report: the button reads Unarchive"])
    task.requirements.append("The controls next to the button do not move")
    found = json.loads(_understood())
    found["checklist"].append({"check": "", "covers": ["R2"]})  # empty: dropped
    items = coder._checklist(task, found, s)
    assert [i["id"] for i in items] == ["C1", "C2", "C3"]
    assert items[1]["covers"] == ["R2"] and items[1]["check"].startswith("The controls")
    assert items[2]["covers"] == ["T1"]
    assert "R2, T1" in s.notes[0]


def test_task_text_numbers_requirements_and_the_test_plan():
    text = prompts.task_text(_task(tests=["Select a report: the label changes"]), "ui")
    assert "R1. No report selected" in text
    assert "### Test plan (what a tester will check)\nT1. Select a report" in text
    assert prompts.item_ids(_task(tests=["t"])) == {
        "R1": "No report selected: the button reads Archive/Unarchive",
        "T1": "t",
    }


def test_a_quote_must_be_in_the_code_and_long_enough(tmp_path):
    tools = _tools(tmp_path)
    s = coder._Session("ui", tools, context_pack.Index(tools))
    assert coder._quoted(s, VIEW, OLD)
    assert coder._quoted(s, VIEW, f"    1  {OLD}")  # a copied line number is ignored
    assert coder._quoted(s, VIEW, '"Archive";')  # part of a line
    assert not coder._quoted(s, VIEW, "}")
    assert not coder._quoted(s, VIEW, "const other = 1; // not here")


# --- indentation -----------------------------------------------------------------------------

STATE = """\
export const Screen = () => {
  const [state, setState] = useState(
    {
      title: "Archive/Unarchive",
    },
  );
  return null;
};
"""


def test_a_block_shifted_two_spaces_right_is_put_back(tmp_path):
    # KS-17531: the model took the two spaces after a shown line number for indentation.
    target = tmp_path / VIEW
    target.parent.mkdir(parents=True)
    target.write_text(STATE)
    tools = RepoTools(tmp_path)
    s = coder._Session("ui", tools, context_pack.Index(tools))
    shifted = (
        "    const [state, setState] = useState(\n"
        "      NEUTRAL_STATE,\n"
        "    );"
    )
    edit = {
        "path": VIEW,
        "start_line": 2,
        "end_line": 6,
        "first_line": "const [state, setState] = useState(",
        "last_line": ");",
        "new": shifted,
    }
    problems, _ = coder._apply_and_check(s, {"plan": [{"path": VIEW}], "edits": [edit]})
    assert [p for p in problems if p.startswith("edit")] == []
    text = target.read_text()
    assert "\n  const [state, setState] = useState(\n    NEUTRAL_STATE,\n  );\n" in text
    assert any("2 space(s) deeper" in n for n in s.notes)


def test_a_change_that_wraps_code_keeps_its_indentation():
    old = ["    <Button>", "      Archive", "    </Button>"]
    new = "    <Slot>\n      <Button>\n        Archive\n      </Button>\n    </Slot>"
    assert coder._reindent(new, old) == (new, 0)


# --- the pull request ------------------------------------------------------------------------


def test_the_pull_request_shows_the_checklist_and_what_to_check_by_hand(tmp_path):
    root = tmp_path / "ui"
    root.mkdir()
    outcome = coder.Outcome(
        "changed",
        {"plan": [], "edits": [], "summary": "s"},
        checklist=[
            {"id": "C1", "check": "label", "covers": ["R1"], "kind": "change", "where": "",
             "verdict": "pass", "at": f"{VIEW}:1"},
            {"id": "C2", "check": "no shift", "covers": ["R2"], "kind": "change", "where": "",
             "verdict": "unsure", "why": "look at the header in a browser"},
        ],
        verified=True,
    )
    target = _task().services[0]
    body = coding_flow._pr_body(
        _task(), target, outcome, [workspace.Change(VIEW, "modified")], context_pack.Pack(""), []
    )
    assert f"✅ C1 (R1): label — `{VIEW}:1`" in body
    assert "❔ C2 (R2): no shift" in body
    assert "- C2: look at the header in a browser" in body
    assert coding_flow._draft_reasons(_task(), outcome) == []
