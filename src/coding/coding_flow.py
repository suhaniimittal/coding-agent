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

import uuid
from pathlib import Path

from src.ingestion import github_api
from src.ingestion.repo_fetcher import RepoFetchError, cloned_repo

from . import patcher, repo_ops, test_runner
from .models import FilePatch, SkippedChange, VerificationResult, VerifiedChange
from .tdd_models import TechnicalDesignDoc
from .validation import resolve_services, verify_changes_for_service


def _read_current_content(repo_root: Path, file_path: str) -> str | None:
    target = repo_root / file_path
    return target.read_text() if target.exists() else None


async def _build_patches(
    repo_root: Path, service: str, service_changes: list[VerifiedChange]
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
            llm_patches, missing = await patcher.generate_service_patch(
                service, llm_changes, file_contents
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
) -> str:
    lines = [f"## {tdd.title}", "", tdd.issue_summary, "", "### Acceptance criteria"]
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


async def run_coding_agent(
    tdd: TechnicalDesignDoc, base_branch_by_service: dict[str, str] | None = None
) -> dict:
    """Runs the full pipeline for every service the TDD touches. Returns
    {"pull_requests": [...], "skipped": [...]} — one PR entry per service
    that had at least one committable change, and every SkippedChange across
    the whole TDD (graph-resolution failures, structural-check failures,
    unreadable files, LLM failures) surfaced for a human, never silently
    dropped."""
    spec_by_service, skipped = resolve_services(tdd)
    base_branch_by_service = base_branch_by_service or {}

    changes_by_service: dict[str, list] = {}
    for service_plan in tdd.services:
        if service_plan.service in spec_by_service:
            changes_by_service.setdefault(service_plan.service, []).extend(service_plan.changes)

    pull_requests = []
    for service, changes in changes_by_service.items():
        spec = spec_by_service[service]
        run_id = uuid.uuid4().hex[:8]

        try:
            with cloned_repo(spec.repo, branch=spec.branch) as checkout:
                repo_root = checkout.root

                service_verified, service_skipped = verify_changes_for_service(
                    service, spec, repo_root, changes
                )
                skipped.extend(service_skipped)
                if not service_verified:
                    continue

                patches, patch_skipped = await _build_patches(repo_root, service, service_verified)
                skipped.extend(patch_skipped)
                if not patches:
                    continue

                to_commit = repo_ops.changed_patches(repo_root, patches)
                repo_ops.apply_patches(repo_root, patches)
                if not to_commit:
                    continue

                verification = test_runner.run_tests(repo_root)

                # The repo's REAL default branch (main, master, ...), never
                # a hardcoded guess, unless a base was asked for explicitly.
                base = base_branch_by_service.get(service) or spec.branch or checkout.default_branch
                if base != checkout.default_branch:
                    # An explicitly requested base that doesn't exist yet
                    # (e.g. "develop") is created from the checked-out commit,
                    # before the fix branch, so a failure leaves nothing behind.
                    github_api.ensure_branch(spec.repo, base, checkout.head_sha)

                new_branch = repo_ops.branch_name(tdd.issue_summary, run_id)
                repo_ops.commit_patches(
                    spec.repo,
                    repo_root,
                    checkout.head_sha,
                    new_branch,
                    f"fix: {tdd.title}",
                    to_commit,
                )
                pr_url = github_api.create_pull_request(
                    spec.repo,
                    head=new_branch,
                    base=base,
                    title=f"fix: {tdd.title}",
                    body=_pr_body(
                        tdd,
                        service_verified,
                        to_commit,
                        [s for s in skipped if s.service == service],
                        verification,
                    ),
                )
                pull_requests.append(
                    {"service": service, "pr_url": pr_url, "verification": verification}
                )
        except (RepoFetchError, repo_ops.RepoOpsError, github_api.GitHubApiError) as e:
            # One service failing must not lose the PRs already opened for
            # the others — report it as skipped, with the real reason.
            skipped.extend(SkippedChange(service, c, f"{type(e).__name__}: {e}") for c in changes)

    return {"pull_requests": pull_requests, "skipped": skipped}
