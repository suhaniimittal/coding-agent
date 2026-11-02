"""The run's folders on disk (2026-11-02): where a service's code lives while
the coding agent works on it, and nowhere else.

No Neo4j and no `git` binary (the platform container has none): each
service the TDD names is downloaded through the GitHub API as a zip, at the
exact commit the TDD was written against, and unpacked into

    <CODING_WORK_DIR>/run-<unix time>-<id>/<service>/

One folder per run, so two runs never touch each other's files, even on the
same repo. The model's tools (repo_tools.py) see only that folder.

Right after unpacking, every file's fingerprint (sha256) is kept in memory —
never on disk. Comparing them again after the model's edits gives the exact
files added, changed and deleted: what `git diff` would give with a real
clone, and the list of files to commit.

The folder is deleted CODING_KEEP_MINUTES (default 5) after the PR is
opened (`delete_later`), or at once when a run fails (`delete`). A worker
that stops before either leaves the folder behind, so every run starts with
`sweep`, which deletes any run folder older than 30 minutes; the run's start
time is in its folder name, so a leftover is found without any record of it.

Replaces repo_fetcher.py, whose temp folder was deleted the moment its
`with` block ended and had no run id to sweep it by.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from src.ingestion import github_api

logger = logging.getLogger(__name__)

_KEEP_MINUTES = 5
_SWEEP_MINUTES = 30
_RUN_NAME = re.compile(r"^run-(\d+)-[0-9a-f]+$")


class WorkspaceError(Exception):
    """A service's code could not be downloaded or unpacked. The caller
    reports that service as not done; other services in the run go on."""


@dataclass(frozen=True)
class Change:
    path: str  # relative to the service's folder, with "/"
    status: str  # "added" | "modified" | "deleted"


@dataclass
class Checkout:
    """One service's code, unpacked: `root` is its folder, `sha` the full
    commit it was downloaded at (the new branch's parent), `branch` the
    branch the PR goes into."""

    run_id: str
    service: str
    repo: str
    branch: str
    sha: str
    root: Path
    _original: dict[str, str] = field(default_factory=dict, repr=False)

    def changes(self) -> list[Change]:
        """Every file that differs from the download, by fingerprint."""
        now = _fingerprints(self.root)
        out = [
            Change(path, "added" if path not in self._original else "modified")
            for path, digest in now.items()
            if self._original.get(path) != digest
        ]
        out += [Change(path, "deleted") for path in self._original if path not in now]
        return sorted(out, key=lambda c: c.path)

    def was_in_download(self, path: str) -> bool:
        return path in self._original


def work_dir() -> Path:
    return Path(os.environ.get("CODING_WORK_DIR") or Path(tempfile.gettempdir()) / "coding-runs")


def keep_minutes() -> float:
    try:
        return float(os.environ.get("CODING_KEEP_MINUTES") or _KEEP_MINUTES)
    except ValueError:
        return _KEEP_MINUTES


def new_run_id() -> str:
    return f"run-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def run_folder(run_id: str) -> Path:
    if not _RUN_NAME.match(run_id):
        raise ValueError(f"not a run id: {run_id!r}")
    return work_dir() / run_id


def download(
    run_id: str, service: str, repo: str, branch: str, commit: str, token: str | None = None
) -> Checkout:
    """`repo` ("owner/name" or a full URL) at `commit` (full or short sha,
    as the TDD gives it) into this run's folder for `service`."""
    try:
        sha = github_api.get_remote_head_sha(repo, commit, token)
        data = github_api.download_zipball(repo, sha, token)
    except github_api.GitHubApiError as e:
        raise WorkspaceError(f"could not download {repo} at {commit}: {e}") from e

    root = run_folder(run_id) / _folder_name(service)
    if root.exists():
        raise WorkspaceError(f"{service} was already downloaded in {run_id}")
    root.mkdir(parents=True)
    try:
        _extract_zip(data, root)
    except zipfile.BadZipFile as e:
        raise WorkspaceError(f"the download of {repo} at {commit} is not a valid zip") from e
    original = _fingerprints(root)
    logger.info(
        "coding_agent: downloaded %s at %s for %s (%d files)",
        repo,
        sha[:12],
        service,
        len(original),
    )
    return Checkout(run_id, service, repo, branch, sha, root, original)


def delete(run_id: str) -> None:
    folder = run_folder(run_id)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
        logger.info("coding_agent: deleted %s", folder)


def delete_later(run_id: str, minutes: float | None = None) -> None:
    """Deletes the run's folder after `minutes` (default CODING_KEEP_MINUTES),
    on a background timer: the run returns its PR links at once. If the
    worker stops first, `sweep` deletes the folder on a later run."""
    wait = keep_minutes() if minutes is None else minutes
    timer = threading.Timer(wait * 60, delete, args=(run_id,))
    timer.daemon = True
    timer.start()
    logger.info("coding_agent: %s will be deleted in %g minute(s)", run_id, wait)


def sweep(older_than_minutes: float = _SWEEP_MINUTES) -> list[str]:
    """Deletes every run folder started more than `older_than_minutes` ago;
    returns their ids. Only folders named like a run are touched."""
    root = work_dir()
    if not root.is_dir():
        return []
    cutoff = time.time() - older_than_minutes * 60
    removed = []
    for folder in root.iterdir():
        match = _RUN_NAME.match(folder.name)
        if folder.is_dir() and match and int(match.group(1)) < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder.name)
    if removed:
        logger.info("coding_agent: swept %d old run folder(s): %s", len(removed), removed)
    return removed


def _folder_name(service: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", service).strip("-.") or "service"


def _fingerprints(root: Path) -> dict[str, str]:
    out = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


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
                raise WorkspaceError(f"unsafe path in repo archive: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            if mode & 0o111:
                target.chmod(target.stat().st_mode | 0o111)
