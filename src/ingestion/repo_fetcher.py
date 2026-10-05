"""Downloads a service's source repo as a snapshot, for the coding pipeline.

No `git` binary is used (the platform container has none): the repo is
fetched through the GitHub API as a zip and extracted into a temp dir.
Callers get a RepoCheckout via the `cloned_repo` context manager and
everything under its root is deleted on exit.
"""

from __future__ import annotations

import io
import os
import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import github_api


class RepoFetchError(Exception):
    """Raised when a repo can't be downloaded — the caller reports this
    service as skipped; it must not abort the run for other services."""


@dataclass(frozen=True)
class RepoCheckout:
    """An extracted snapshot: `root` is the working tree, `default_branch`
    the repo's real default branch (the PR's base unless overridden) and
    `head_sha` the exact commit the snapshot was taken from (the new
    branch's parent)."""

    root: Path
    default_branch: str
    head_sha: str


def _extract_zip(data: bytes, dest: Path) -> None:
    """Extracts GitHub's zipball into `dest`, dropping its single top-level
    `owner-repo-sha/` folder. Keeps the executable bit; skips symlinks (a
    zip stores them as small text files, which would corrupt them if
    written as regular files) and refuses any path escaping `dest`."""
    root = dest.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            relative = Path(*Path(info.filename).parts[1:])
            if not relative.parts:
                continue
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                continue
            target = (root / relative).resolve()
            if root not in target.parents:
                raise RepoFetchError(f"unsafe path in repo archive: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            if mode & 0o111:
                target.chmod(target.stat().st_mode | 0o111)


@contextmanager
def cloned_repo(
    slug: str, github_token: str | None = None, branch: str | None = None, timeout: int = 120
) -> Iterator[RepoCheckout]:
    """Downloads `slug` ("org/repo") at `branch` (None = the repo's default
    branch) into a temp dir and yields a RepoCheckout. Only pass `branch`
    when a service explicitly names one."""
    token = github_token if github_token is not None else os.environ.get("GITHUB_TOKEN")
    try:
        default_branch = github_api.get_default_branch(slug, token)
        head_sha = github_api.get_remote_head_sha(slug, branch or default_branch, token)
        data = github_api.download_zipball(slug, head_sha, token, timeout=timeout)
    except github_api.GitHubApiError as e:
        raise RepoFetchError(f"could not download {slug}: {e}") from e

    with tempfile.TemporaryDirectory(prefix="repo_fetcher_") as tmpdir:
        try:
            _extract_zip(data, Path(tmpdir))
        except zipfile.BadZipFile as e:
            raise RepoFetchError(f"downloaded archive for {slug} is not a valid zip") from e
        yield RepoCheckout(Path(tmpdir), default_branch, head_sha)
