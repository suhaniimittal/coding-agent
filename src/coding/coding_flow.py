"""The coding run (2026-11-02): a TDD's task -> per service, its code on
disk -> the coding model's change -> one commit on a new branch -> a pull
request -> the folder deleted.

Per service in the TDD's "Services to change" table, one after another:

1. download the repository at the TDD's commit into this run's folder
   (workspace.py; no git, no Neo4j);
2. gather the code around the TDD's files (context_pack.py, no model call);
3. the coding model's five calls (coder.py: understand, write, verify,
   fix what failed, verify again), with our code's checks between them;
4. the changed files (by fingerprint) as ONE commit on a NEW branch, made
   through the GitHub API; an existing branch is never written to;
5. a pull request into the TDD's branch, as a draft unless CODING_DRAFT_PR
   is false and nothing is open. The description says what changed and
   why, how each requirement is met, where the TDD was not followed, the
   checks' result and the TDD's test plan;
6. the folder: deleted CODING_KEEP_MINUTES after the run when any pull
   request was opened, at once otherwise.

Nothing is merged, and the default branch is never touched. A service that
can't be done is reported with its reason; the others go on. Tests are left
to Jenkins, which builds the pushed branch: the agent can't build.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from src.ingestion import github_api

from . import coder, context_pack, workspace
from .repo_tools import RepoTools
from .tdd_reader import ServiceTarget, Task


def _draft_by_default() -> bool:
    return os.environ.get("CODING_DRAFT_PR", "true").strip().lower() not in {"0", "false", "no"}


async def run(task: Task, branch_name: str | None = None) -> dict:
    """{"services": [one result per service], "run_id": ...}. A result has
    "service", "repo", "status" ("pr_opened" | "gave_up" | "no_change" |
    "failed" | "skipped"), "reason", and when a pull request was opened its
    "pr_url", "branch", "branch_url", "draft", "files" and "model_calls"."""
    workspace.sweep()
    run_id = workspace.new_run_id()
    results = []
    try:
        for target in task.services:
            results.append(await _one_service(task, target, run_id, branch_name))
    finally:
        if any(r["status"] == "pr_opened" for r in results):
            workspace.delete_later(run_id)
        else:
            workspace.delete(run_id)
    return {"run_id": run_id, "services": results}


async def _one_service(
    task: Task, target: ServiceTarget, run_id: str, branch_name: str | None
) -> dict:
    out = {"service": target.service, "repo": target.repo, "status": "failed", "reason": ""}
    branch = (branch_name or "").strip() or _branch_name(task, run_id)
    try:
        if branch == target.branch:
            return {**out, "status": "skipped", "reason": f"{branch!r} is the base branch"}
        if github_api.branch_exists(target.repo, branch):
            return {
                **out,
                "status": "skipped",
                "reason": f"branch {branch!r} already exists in {target.repo}; the agent only "
                "creates new branches",
            }
        checkout = workspace.download(
            run_id, target.service, target.repo, target.branch, target.commit
        )
    except (workspace.WorkspaceError, github_api.GitHubApiError) as e:
        return {**out, "reason": str(e)}

    tools = RepoTools(checkout.root)
    index = context_pack.Index(tools, target.folder)
    pack = context_pack.build(tools, index, task, target.service)
    print(
        f"  [CODER] {target.service}: context: {len(pack.shown)} file(s) shown, "
        f"{len(pack.listed)} listed, {len(pack.names)} name(s), "
        f"{len(pack.missing)} TDD path(s) not found"
    )
    outcome = await coder.run(task, target.service, tools, index, pack)
    out["model_calls"] = outcome.calls
    if outcome.status != "changed":
        return {**out, "status": outcome.status, "reason": outcome.reason}
    # An edit that never applied means the change is only part done (BTS-40
    # opened a PR holding just the new test): report it, open nothing.
    failed_edits = [p.split("\n", 1)[0] for p in outcome.problems if p.startswith("edit ")]
    if failed_edits:
        return {
            **out,
            "reason": "the change could not be applied in full, so no pull request was opened: "
            + "; ".join(failed_edits),
        }

    changes = checkout.changes()
    if not changes:
        return {**out, "status": "no_change", "reason": "the change left every file as it was"}
    change = outcome.change
    draft_reasons = _draft_reasons(task, outcome)
    title = _title(task, change)
    body = _pr_body(task, target, outcome, changes, pack, draft_reasons)
    try:
        github_api.create_branch_commit(
            target.repo,
            checkout.sha,
            branch,
            f"{title}\n\n{change.get('summary') or ''}".strip(),
            _commit_files(checkout.root, changes),
        )
        draft = _draft_by_default() or bool(draft_reasons)
        pr_url, draft = _open_pr(target, branch, title, body, draft)
    except github_api.GitHubApiError as e:
        return {**out, "reason": str(e)}
    print(f"  [CODER] {target.service}: pull request opened{' (draft)' if draft else ''}")
    return {
        **out,
        "status": "pr_opened",
        "pr_url": pr_url,
        "branch": branch,
        "branch_url": github_api.branch_web_url(target.repo, branch),
        "draft": draft,
        "files": [f"{c.path} ({c.status})" for c in changes],
        "reason": "; ".join(draft_reasons),
    }


def _open_pr(target: ServiceTarget, branch: str, title: str, body: str, draft: bool):
    """(url, draft). A repository whose plan has no draft pull requests
    refuses one: it is then opened as a normal one, marked in the title."""
    try:
        return github_api.create_pull_request(
            target.repo, branch, target.branch, title, body, draft=draft
        ), draft
    except github_api.GitHubApiError as e:
        if not draft or "draft" not in str(e).lower():
            raise
    url = github_api.create_pull_request(
        target.repo, branch, target.branch, f"[Draft] {title}", body, draft=False
    )
    return url, True


def _draft_reasons(task: Task, outcome: coder.Outcome) -> list[str]:
    """Why the pull request is a draft; none when every check passed, every
    checklist item passed (or can only be judged by a person), and the final
    code is the code the last check judged."""
    reasons = [f"check not passed: {p}" for p in outcome.problems]
    reasons += [f"over the size limit: {p}" for p in outcome.over_limits]
    if not outcome.verified:
        reasons.append("the final change was not checked against the checklist")
    if task.status == "draft":
        reasons.append("the TDD is a draft")
    if task.high_level:
        reasons.append("the TDD was written from file paths only")
    return reasons


def _branch_name(task: Task, run_id: str) -> str:
    base = task.ticket or task.title or "change"
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:40] or "change"
    return f"fix/{slug}-{run_id.rsplit('-', 1)[-1]}"


def _title(task: Task, change: dict) -> str:
    title = str(change.get("pr_title") or task.title or "Change from TDD").strip()
    if task.ticket and task.ticket.lower() not in title.lower():
        title = f"{task.ticket}: {title}"
    return title[:200]


def _commit_files(root: Path, changes: list[workspace.Change]) -> list[dict]:
    files = []
    for c in changes:
        if c.status == "deleted":
            files.append({"path": c.path, "content": None, "mode": "100644"})
            continue
        target = root / c.path
        with open(target, encoding="utf-8", newline="") as f:
            content = f.read()
        executable = target.stat().st_mode & 0o111
        files.append(
            {"path": c.path, "content": content, "mode": "100755" if executable else "100644"}
        )
    return files


def _pr_body(
    task: Task,
    target: ServiceTarget,
    outcome: coder.Outcome,
    changes: list[workspace.Change],
    pack: context_pack.Pack,
    draft_reasons: list[str],
) -> str:
    change = outcome.change
    lines = []
    if draft_reasons:
        lines += ["> **Draft: please review closely.**"]
        lines += [f"> - {r}" for r in draft_reasons]
        lines.append("")
    lines += [str(change.get("summary") or task.fix or "").strip(), ""]
    lines.append(f"Ticket: {task.ticket or '-'} · TDD: {task.title}")
    lines.append(f"Based on `{target.repo}` at `{target.branch}` commit `{target.commit[:12]}`.")

    lines += ["", "### Cause", str(change.get("cause") or "-")]
    if change.get("evidence"):
        lines += ["", str(change["evidence"])]

    lines += ["", "### Changes"]
    status = {c.path: c.status for c in changes}
    for item in change.get("plan") or []:
        if isinstance(item, dict) and item.get("path") in status:
            why = f" Why: {item['why']}" if item.get("why") else ""
            lines.append(
                f"- `{item['path']}` ({status[item['path']]}): {item.get('change') or ''}{why}"
            )
    planned = {i.get("path") for i in change.get("plan") or [] if isinstance(i, dict)}
    lines += [f"- `{c.path}` ({c.status})" for c in changes if c.path not in planned]

    def bullets(title: str, values) -> None:
        values = [str(v) for v in values or [] if v]
        if values:
            lines.extend(["", f"### {title}", *(f"- {v}" for v in values)])

    marks = {"pass": "✅", "fail": "❌", "unsure": "❔"}
    checklist = [
        f"{marks.get(i.get('verdict'), '⏺')} {i['id']}"
        + (f" ({', '.join(i['covers'])})" if i.get("covers") else "")
        + f": {i['check']}"
        + (f" — `{i['at']}`" if i.get("at") else "")
        for i in outcome.checklist
    ]
    bullets("Checklist (✅ passed the agent's check, ❌ failed, ❔ for a person)", checklist)
    by_hand = [
        f"{i['id']}: {i.get('why') or i['check']}"
        for i in outcome.checklist
        if i.get("verdict") == "unsure"
    ]
    bullets("To check by hand", by_hand)
    answers = [
        f"{a.get('item')}: {a.get('answer')} ({a.get('evidence') or 'no evidence given'})"
        for a in change.get("answers") or []
        if isinstance(a, dict)
    ]
    bullets("Open questions and risks: what the code shows", answers)
    bullets("Where this differs from the TDD", change.get("differs_from_tdd"))
    bullets("Found by the agent's first check (sent back for a fix)", outcome.review)
    bullets("Shared code changed", change.get("shared_code"))
    bullets("To check in review", change.get("doubts"))
    if task.open_questions_detail:
        decide = [q.question for q in task.card_questions()]
        bullets("Open questions for a person to decide", decide)
    else:
        bullets("Open questions from the TDD", task.open_questions)
    if pack.missing:
        bullets("TDD paths not found in the repository", pack.missing)

    if task.tests:
        lines += ["", "### Test plan (from the TDD)"]
        lines += [f"{n}. {t}" for n, t in enumerate(task.tests, start=1)]

    lines += ["", "### Checks by the agent"]
    if outcome.problems:
        lines += [f"- Not passed: {p}" for p in outcome.problems]
    else:
        lines.append("- Syntax, imports and scope of the changed files: passed.")
    lines += [f"- {n}" for n in outcome.notes]
    lines.append("- Not built or tested by the agent: Jenkins builds this branch.")
    lines += ["", f"🤖 Generated by coding_agent from the TDD ({outcome.calls} model call(s))."]
    return "\n".join(lines)
