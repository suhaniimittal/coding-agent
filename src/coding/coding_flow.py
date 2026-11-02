"""Orchestrates the whole coding pipeline: TDD -> verified changes -> one
real patch call per service -> a real branch -> real test verification -> a
real Pull Request. Never touches the default branch, never auto-merges,
never claims a fix works without having actually tried it, and never
retries an LLM call to "repair" a test failure — a failure is reported
honestly in the PR instead.

Considered and rejected: a second "critic" LLM pass reviewing each proposed
change before applying it. Rejected because it doesn't catch the failure
mode that matters most — a proposal that *sounds* correct from reading the
code but is wrong about the real world. A second LLM reading the same code
has the identical blind spot and would approve the same broken plan. That
class of error can only be caught by actually trying it (a real test run),
not by asking for another opinion. So validation here is either free (a
real filesystem check against the clone) or real (actual test execution) —
never a second guess.

Download-before-validate ordering (different from a Neo4j-backed structural
check): this project has zero shared infrastructure with the planning
project, so "does this file exist" is checked against a real clone made
right here, which means the clone has to happen before validation can run
for that service, not after.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from src.ingestion import github_api
from src.ingestion.repo_fetcher import RepoFetchError, cloned_repo

from . import patcher, repo_ops, test_runner
from .models import FilePatch, SkippedChange, VerificationResult, VerifiedChange
from .tdd_models import TechnicalDesignDoc
from .test_context import build_test_context
from .test_pairing import enforce_test_pairs
from .validation import resolve_services, triage_changes, verify_changes_for_service


def _read_current_content(repo_root: Path, file_path: str) -> str | None:
    target = repo_root / file_path
    return target.read_text() if target.exists() else None


async def _build_patches(
    repo_root: Path,
    service: str,
    service_changes: list[VerifiedChange],
    doc_text: str | None = None,
) -> tuple[list[FilePatch], list[SkippedChange]]:
    """One LLM call for this whole service (patcher.py's contract), plus
    direct (no-LLM) handling of any "delete" entries. A "modify" naming a
    path that doesn't actually exist in the clone is skipped before the
    call — there's nothing to send as its current content."""
    patches: list[FilePatch] = []
    skipped: list[SkippedChange] = []

    file_groups = patcher.group_verified_changes(service_changes)
    llm_groups: dict[str, list[VerifiedChange]] = {}
    file_contents: dict[str, str | None] = {}

    for file_path, group in file_groups.items():
        change_type = patcher.effective_change_type(group)
        if change_type == "delete":
            changes_applied = [v.change.change_description for v in group]
            patches.append(FilePatch(service, file_path, "delete", None, changes_applied))
            continue

        if change_type == "modify":
            current_content = _read_current_content(repo_root, file_path)
            if current_content is None:
                for v in group:
                    skipped.append(
                        SkippedChange(
                            service, v.change, f"{file_path!r} not found in the cloned repo"
                        )
                    )
                continue
            file_contents[file_path] = current_content

        llm_groups[file_path] = group

    llm_changes = [v for group in llm_groups.values() for v in group]
    if llm_changes:
        try:
            # Fix A: test files get the production file they cover, an example
            # test and CLAUDE.md as read-only context (only passed when there
            # is a test file to write).
            context = build_test_context(repo_root, llm_groups, service_changes, doc_text)
            extra = {"context_files": context} if context else {}
            llm_patches, missing = await patcher.generate_service_patch(
                service, llm_changes, file_contents, **extra
            )
        except patcher.PatcherError as e:
            for v in llm_changes:
                skipped.append(SkippedChange(service, v.change, str(e)))
        else:
            patches.extend(llm_patches)
            for file_path in missing:
                for v in llm_groups[file_path]:
                    skipped.append(
                        SkippedChange(
                            service, v.change, "LLM response missing content for this file"
                        )
                    )

    return patches, skipped


def _pr_body(
    tdd: TechnicalDesignDoc,
    service_changes: list[VerifiedChange],
    applied: list[FilePatch],
    skipped: list[SkippedChange],
    verification: VerificationResult,
    warnings: list[str] | None = None,
) -> str:
    lines = [f"## {tdd.title}", "", tdd.issue_summary]
    if warnings:
        lines += ["", "### ⚠️ Test changes NOT applied — this PR will likely fail CI"]
        lines += [f"- {w}" for w in warnings]
    lines += ["", "### Acceptance criteria"]
    criteria = [c for v in service_changes for c in v.change.acceptance_criteria]
    lines += [f"- [ ] {c}" for c in criteria] or ["- (none specified)"]

    lines += ["", "### Files changed"]
    lines += [
        f"- `{p.file_path}` ({p.change_type}): {', '.join(p.changes_applied)}" for p in applied
    ] or ["- (none)"]

    if skipped:
        lines += ["", "### Skipped changes (not auto-applied — needs a human)"]
        lines += [
            f"- `{s.change.file_path}` / {s.change.function_or_symbol}: {s.reason}" for s in skipped
        ]

    lines += ["", "### Verification"]
    if not verification.ran:
        lines.append(f"Not run: {verification.detail}")
    elif verification.passed:
        lines.append("Tests ran and passed.")
    else:
        lines.append(
            f"Tests ran and FAILED — opening this PR for human review, not claiming it's fixed:\n\n```\n{verification.detail[-2000:]}\n```"
        )

    lines += [
        "",
        "🤖 Generated by coding_agent",
    ]
    return "\n".join(lines)


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _open_pull_request_enabled() -> bool:
    """PRs are OFF by default: the agent pushes a branch and stops, so the
    team watches Jenkins on that branch and opens the PR themselves.
    OPEN_PULL_REQUEST=true brings automatic PRs back."""
    return _flag("OPEN_PULL_REQUEST")


async def run_coding_agent(
    tdd: TechnicalDesignDoc,
    branch_name: str | None = None,
    doc_text: str | None = None,
    open_pull_request: bool | None = None,
) -> dict:
    """Runs the full pipeline for every service the TDD touches and PUSHES one
    new branch per service (`branch_name`, the same name in every repo, else a
    generated fix/... name) with one commit. No PR is opened unless
    OPEN_PULL_REQUEST is enabled. Returns {"branches": [...], "skipped": [...],
    "service_notes": [...], "warnings": [...]} — one branches entry per
    service that got a pushed branch, every SkippedChange surfaced for a
    human, and one service_notes entry {"service", "reason"} for every
    resolved service that ended with nothing pushed, so a service never
    disappears from the output without a reason."""
    spec_by_service, skipped = resolve_services(tdd)
    head = (branch_name or "").strip()
    if open_pull_request is None:
        open_pull_request = _open_pull_request_enabled()

    changes_by_service: dict[str, list] = {}
    for service_plan in tdd.services:
        if service_plan.service in spec_by_service:
            changes_by_service.setdefault(service_plan.service, []).extend(service_plan.changes)

    branches = []
    service_notes: list[dict] = []
    warnings: list[dict] = []

    def no_pr(service: str, reason: str) -> None:
        service_notes.append({"service": service, "reason": reason})

    for service, changes in changes_by_service.items():
        spec = spec_by_service[service]
        run_id = uuid.uuid4().hex[:8]

        if not changes:
            no_pr(service, "the TDD lists no file changes for this service")
            continue
        actionable, triage_skipped, references = triage_changes(service, changes)
        skipped.extend(triage_skipped)
        if not actionable:
            parts = []
            if references:
                parts.append(f"{references} reference-only (read, not changed)")
            if triage_skipped:
                parts.append(f"{len(triage_skipped)} unverified by the planner")
            no_pr(service, "no actionable changes: " + ", ".join(parts))
            continue

        try:
            with cloned_repo(spec.repo, branch=spec.branch) as checkout:
                repo_root = checkout.root
                # The repo's REAL default branch (main, master, ...), never a
                # hardcoded guess: the new branch starts here and a PR, when
                # enabled, targets it.
                base = spec.branch or checkout.default_branch

                # The branch name the user chose (same in every repo) is used
                # INSTEAD of a generated fix/... name. Checked before any LLM
                # call: it must not be the base/default branch, and it must not
                # exist yet — the agent only ever creates branches.
                if head:
                    if head in (base, checkout.default_branch):
                        reason = f"branch name {head!r} is this repo's base/default branch"
                    elif github_api.branch_exists(spec.repo, head):
                        reason = (
                            f"branch {head!r} already exists in {spec.repo} — delete it or "
                            "choose a new name; the agent never writes to an existing branch"
                        )
                    else:
                        reason = None
                    if reason:
                        skipped.extend(SkippedChange(service, c, reason) for c in actionable)
                        no_pr(service, reason)
                        continue

                service_verified, service_skipped = verify_changes_for_service(
                    service, spec, repo_root, actionable
                )
                skipped.extend(service_skipped)
                if not service_verified:
                    no_pr(service, "no change passed the checks against the repo — see skipped")
                    continue

                patches, patch_skipped = await _build_patches(
                    repo_root, service, service_verified, doc_text
                )
                skipped.extend(patch_skipped)
                if not patches:
                    no_pr(service, "no code could be produced — see skipped")
                    continue

                to_commit = repo_ops.changed_patches(repo_root, patches)
                patches, to_commit, pair_skipped, pair_warnings = enforce_test_pairs(
                    service, changes, patches, to_commit
                )
                skipped.extend(pair_skipped)
                warnings.extend({"service": service, "message": w} for w in pair_warnings)
                repo_ops.apply_patches(repo_root, patches)
                if not to_commit:
                    no_pr(
                        service, "the generated code was identical to the repo — nothing to commit"
                    )
                    continue

                # Testing is left to CI: Jenkins builds and tests the pushed
                # branch. A local run on a fresh download (no dependencies
                # installed) cannot give a real result, so it is off unless
                # RUN_LOCAL_TESTS is enabled.
                if _flag("RUN_LOCAL_TESTS"):
                    verification = test_runner.run_tests(repo_root)
                else:
                    verification = VerificationResult(
                        ran=False,
                        passed=False,
                        detail="not run by the agent — Jenkins runs the tests on the pushed branch",
                    )

                new_branch = head or repo_ops.branch_name(tdd.issue_summary, run_id)
                # With no PR, the commit message carries what the PR body used
                # to: acceptance criteria, files, skipped changes, warnings and
                # the test result — visible on GitHub next to the code.
                summary = _pr_body(
                    tdd,
                    service_verified,
                    to_commit,
                    [s for s in skipped if s.service == service],
                    verification,
                    pair_warnings,
                )
                repo_ops.commit_patches(
                    spec.repo,
                    repo_root,
                    checkout.head_sha,
                    new_branch,
                    f"fix: {tdd.title}\n\n{summary}",
                    to_commit,
                )
                pr_url = None
                if open_pull_request:
                    pr_url = github_api.create_pull_request(
                        spec.repo,
                        head=new_branch,
                        base=base,
                        title=f"fix: {tdd.title}",
                        body=summary,
                    )
                branches.append(
                    {
                        "service": service,
                        "repo": spec.repo,
                        "branch": new_branch,
                        "branch_url": github_api.branch_web_url(spec.repo, new_branch),
                        "pr_url": pr_url,
                        "verification": verification,
                        "changes_applied": len(to_commit),
                        "changes_total": len(
                            {c.file_path for c in changes if c.change_type != "reference"}
                        ),
                    }
                )
        except (RepoFetchError, repo_ops.RepoOpsError, github_api.GitHubApiError) as e:
            # One service failing must not lose the branches already pushed
            # for the others — report it as skipped, with the real reason.
            skipped.extend(
                SkippedChange(service, c, f"{type(e).__name__}: {e}") for c in actionable
            )
            no_pr(service, f"{type(e).__name__} — see skipped")

    return {
        "branches": branches,
        "skipped": skipped,
        "service_notes": service_notes,
        "warnings": warnings,
    }
