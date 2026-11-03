"""planning_agent's embedded data now carries the ticket itself ("issue") and
each open question's source: "card" (a person decides) or "planner" (the
code answers it). The prompt puts the ticket first and the TDD second; only
planner questions and risks must be answered with evidence."""

from __future__ import annotations

from src.coding import prompts
from src.coding.coder import _unanswered
from src.coding.tdd_reader import Task

SERVICE = {"service": "ui", "repo": "keystone/ui", "branch": "develop", "commit": "0905c4e"}


def _task(**extra) -> Task:
    return Task.model_validate(
        {"title": "Blank band beside the title", "services": [SERVICE]} | extra
    )


ISSUE = {
    "key": "KS-17531",
    "type": "Bug",
    "title": "Client UI - White space added and controls shift when report selected",
    "description": "Selecting a report shifts every control down.",
    "attachments": [
        {
            "name": "shift.png",
            "description": "The image shows: a blank band beside the title; the search row drops.",
            "text": "",
            "cut": False,
        }
    ],
    "answers": [{"question": "Which browsers?", "answers": ["Chrome 130", "Edge latest"]}],
}
DETAIL = [
    {
        "question": "Should archived reports show by default?",
        "why_it_matters": "",
        "source": "card",
    },
    {"question": "Does the grid keep the button width?", "why_it_matters": "", "source": "planner"},
]


def test_the_embedded_issue_and_question_sources_are_read():
    task = _task(issue=ISSUE, open_questions_detail=DETAIL)
    assert task.issue.key == "KS-17531"
    assert task.issue.attachments[0].description.startswith("The image shows")
    assert [q.question for q in task.card_questions()] == [DETAIL[0]["question"]]
    assert [q.question for q in task.code_questions()] == [DETAIL[1]["question"]]


def test_an_older_pdf_without_the_new_keys_still_reads():
    task = _task(open_questions=["Does the grid keep the button width?"])
    assert task.issue is None and task.card_questions() == []
    assert [q.question for q in task.code_questions()] == ["Does the grid keep the button width?"]


def test_the_prompt_puts_the_ticket_first_and_splits_the_questions():
    text = prompts.task_text(_task(issue=ISSUE, open_questions_detail=DETAIL), "ui")
    assert text.index("## The ticket (what must be true)") < text.index("## The TDD (a guide")
    assert "a blank band beside the title" in text
    assert "Which browsers? Chrome 130 / Edge latest" in text
    to_code = text.index("Open questions to answer from the code")
    for_person = text.index("Open questions for a person")
    assert text.index("Does the grid keep the button width?") > to_code
    assert text.index("Should archived reports show by default?") > for_person


def test_a_high_level_tdd_labels_its_guesses():
    text = prompts.task_text(_task(high_level=True, cause="c", fix="f"), "ui")
    assert "Likely cause (TDD): c" in text and "Suggested approach (TDD): f" in text
    assert "Cause (TDD)" not in text


def test_the_ticket_is_capped():
    big = ISSUE | {"description": "x" * 40_000}
    assert len(prompts.ticket_text(_task(issue=big))) <= prompts.ISSUE_CHARS + 20


def test_only_planner_questions_and_risks_need_an_answer():
    task = _task(open_questions_detail=DETAIL, risks=["Labels of changing width move the fields"])
    missing = _unanswered(task, {"answers": []})
    assert len(missing) == 2
    assert not any("archived reports show by default" in m for m in missing)
    answered = {
        "answers": [
            {
                "item": "Does the grid keep the button width?",
                "answer": "no",
                "evidence": "InfiniteScrollGrid/view.tsx:119",
            },
            {
                "item": "Labels of changing width move the fields",
                "answer": "yes",
                "evidence": "InfiniteScrollGrid/styles.ts:23",
            },
        ]
    }
    assert _unanswered(task, answered) == []
