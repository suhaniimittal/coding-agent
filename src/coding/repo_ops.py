"""Turns a set of FilePatch results into a real branch + commit on GitHub,
ready to open as a PR. Files are written into the downloaded working tree
(so the repo's own tests can run against them), then the same changes are
committed to a NEW branch through the GitHub API — no `git` binary needed.
Never touches the repo's default branch.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.ingestion import github_api

from .models import FilePatch


class RepoOpsError(Exception):
    """Raised when the branch/commit can't be created — caller must not
    treat the branch as committed when this is raised."""


def branch_name(issue_summary: str, suffix: str) -> str:
    """`fix/<slug>-<suffix>` — `suffix` (e.g. a short run id) keeps repeated
    runs against the same issue from colliding on an existing branch name."""
    slug = re.sub(r"[^a-z0-9]+", "-", issue_summary.lower()).strip("-")[:40] or "change"
    return f"fix/{slug}-{suffix}"


def changed_patches(repo_root: Path, patches: list[FilePatch]) -> list[FilePatch]:
    """The patches that would actually change something — call BEFORE
    apply_patches. A modify whose content already matches, or a delete of a
    file that isn't there, is dropped, so an all-no-op run opens no PR."""
    changed = []
    for patch in patches:
        target = repo_root / patch.file_path
        if patch.change_type == "delete":
            if target.exists():
                changed.append(patch)
            continue
        try:
            unchanged = target.exists() and target.read_text() == (patch.new_content or "")
        except UnicodeDecodeError:
            unchanged = False
        if not unchanged:
            changed.append(patch)
    return changed


def apply_patches(repo_root: Path, patches: list[FilePatch]) -> None:
    """Writes every patch's content to disk (create/modify) or removes the
    file (delete). Parent directories are created as needed for a brand-new
    file's path."""
    for patch in patches:
        target = repo_root / patch.file_path
        if patch.change_type == "delete":
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(patch.new_content or "")


def commit_patches(
    repo: str,
    repo_root: Path,
    base_sha: str,
    branch: str,
    message: str,
    patches: list[FilePatch],
) -> str:
    """Creates `branch` (parent `base_sha`) with one commit applying
    `patches` — which must already be applied in `repo_root`, so each
    file's executable bit is carried over. Returns the commit sha."""
    files = []
    for patch in patches:
        if patch.change_type == "delete":
            files.append({"path": patch.file_path, "content": None, "mode": "100644"})
            continue
        target = repo_root / patch.file_path
        executable = target.exists() and target.stat().st_mode & 0o111
        files.append(
            {
                "path": patch.file_path,
                "content": patch.new_content or "",
                "mode": "100755" if executable else "100644",
            }
        )
    try:
        return github_api.create_branch_commit(repo, base_sha, branch, message, files)
    except github_api.GitHubApiError as e:
        raise RepoOpsError(str(e)) from e
