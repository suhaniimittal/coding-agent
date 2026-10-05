"""Talks to the GitHub REST API — the only way this project touches GitHub,
since the platform container has no `git` binary. Covers: a repo's default
branch and branch HEAD, downloading a snapshot as a zip, writing a new
branch + commit through the Git Data API, and opening a Pull Request.

Auth: GITHUB_TOKEN (needs Contents + Pull requests read/write on the repo,
or the classic `repo` scope), optional only for read-only public access.
"""

from __future__ import annotations

import base64
import os

import httpx

_API_BASE = "https://api.github.com"
_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 120


class GitHubApiError(Exception):
    """Raised on any GitHub API failure — callers must catch this and skip
    this service's sync for the run, never let it abort the whole run."""


def _headers(token: str | None) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = token if token is not None else os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def get_remote_head_sha(repo: str, branch: str | None = None, token: str | None = None) -> str:
    """The current HEAD commit sha of `repo`'s `branch`, or its default
    branch when `branch` is None — a single lightweight API call, no clone."""
    ref = branch or "HEAD"
    url = f"{_API_BASE}/repos/{repo}/commits/{ref}"
    try:
        resp = httpx.get(url, headers=_headers(token), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()["sha"]
    except httpx.HTTPError as e:
        raise GitHubApiError(f"could not resolve HEAD for {repo}@{ref}: {e}") from e


def get_changed_files(repo: str, base: str, head: str, token: str | None = None) -> list[dict]:
    """GitHub's Compare API: exactly which files differ between `base` and
    `head` — the "ask GitHub directly which files changed" step, no local
    diff/clone needed.

    Returns [{"path", "status", "sha", "previous_path"}, ...]. `status` is
    normalized to one of "added"/"removed"/"renamed"/"modified" (GitHub's
    own "copied"/"changed"/"unchanged" statuses collapse to "modified" here
    — this pipeline only needs to decide "reparse" vs "delete" vs "just
    relabel"). `sha` is the file's blob sha AT `head` (None for a removed
    file). `previous_path` is set only for a rename.

    Known limitation: GitHub's compare API paginates at 300 changed files
    per response; a diff larger than that returns only the first page here.
    """
    url = f"{_API_BASE}/repos/{repo}/compare/{base}...{head}"
    try:
        resp = httpx.get(url, headers=_headers(token), timeout=_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise GitHubApiError(f"could not compare {base}...{head} for {repo}: {e}") from e

    data = resp.json()
    files = []
    for f in data.get("files", []):
        status = f["status"]
        if status not in ("added", "removed", "renamed"):
            status = "modified"
        files.append(
            {
                "path": f["filename"],
                "status": status,
                "sha": f.get("sha"),
                "previous_path": f.get("previous_filename"),
            }
        )
    return files


def fetch_file_content(repo: str, path: str, ref: str, token: str | None = None) -> str:
    """Raw text content of one file at `ref`, via GitHub's Contents API —
    fetches exactly this one file, never the whole repo."""
    url = f"{_API_BASE}/repos/{repo}/contents/{path}"
    try:
        resp = httpx.get(url, headers=_headers(token), params={"ref": ref}, timeout=_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise GitHubApiError(f"could not fetch {repo}/{path}@{ref}: {e}") from e
    content_b64 = resp.json()["content"]
    return base64.b64decode(content_b64).decode("utf-8")


def create_pull_request(
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    token: str | None = None,
) -> str:
    """Opens a PR from `head` (the branch the coding agent just pushed) into
    `base` (the service's default/manifest branch) — never a merge, always a
    PR for human review. Returns the PR's html_url."""
    url = f"{_API_BASE}/repos/{repo}/pulls"
    payload = {"title": title, "body": body, "head": head, "base": base}
    try:
        resp = httpx.post(url, headers=_headers(token), json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise GitHubApiError(f"could not open PR for {repo} {head}->{base}: {e}") from e
    return resp.json()["html_url"]


def get_default_branch(repo: str, token: str | None = None) -> str:
    """The repo's real default branch (main/master/...), never a guess."""
    url = f"{_API_BASE}/repos/{repo}"
    try:
        resp = httpx.get(url, headers=_headers(token), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()["default_branch"]
    except httpx.HTTPError as e:
        raise GitHubApiError(f"could not read repo info for {repo}: {_describe(e)}") from e


def download_zipball(
    repo: str, ref: str, token: str | None = None, timeout: int = _DOWNLOAD_TIMEOUT
) -> bytes:
    """The repo's files at `ref` (a commit sha or branch) as zip bytes —
    replaces `git clone`. GitHub answers with a redirect to the archive."""
    url = f"{_API_BASE}/repos/{repo}/zipball/{ref}"
    try:
        resp = httpx.get(url, headers=_headers(token), timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise GitHubApiError(f"could not download {repo}@{ref}: {_describe(e)}") from e
    return resp.content


def _describe(error: httpx.HTTPError) -> str:
    """The error text plus GitHub's own message body when there is one —
    a bare '403 Forbidden' doesn't say whether the token lacks write access."""
    response = getattr(error, "response", None)
    if response is not None:
        return f"{error} — {response.text[:300]}"
    return str(error)


def _post(path: str, payload: dict, token: str | None, what: str) -> dict:
    try:
        resp = httpx.post(
            f"{_API_BASE}{path}", headers=_headers(token), json=payload, timeout=_TIMEOUT
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise GitHubApiError(f"could not {what}: {_describe(e)}") from e
    return resp.json()


def create_branch_commit(
    repo: str,
    base_sha: str,
    branch: str,
    message: str,
    files: list[dict],
    token: str | None = None,
) -> str:
    """Creates `branch` pointing at ONE new commit (parent `base_sha`) that
    applies `files` — [{"path", "content" (None = delete), "mode"}] — via the
    Git Data API: blobs -> tree -> commit -> ref. No clone or push needed,
    and it can never touch an existing branch (creating a ref that already
    exists fails with 422). Returns the new commit's sha."""
    try:
        resp = httpx.get(
            f"{_API_BASE}/repos/{repo}/git/commits/{base_sha}",
            headers=_headers(token),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        base_tree = resp.json()["tree"]["sha"]
    except httpx.HTTPError as e:
        raise GitHubApiError(f"could not read base commit {base_sha}: {_describe(e)}") from e

    entries = []
    for f in files:
        if f["content"] is None:
            sha = None
        else:
            blob = _post(
                f"/repos/{repo}/git/blobs",
                {"content": f["content"], "encoding": "utf-8"},
                token,
                f"create blob for {f['path']}",
            )
            sha = blob["sha"]
        entries.append({"path": f["path"], "mode": f["mode"], "type": "blob", "sha": sha})

    tree = _post(
        f"/repos/{repo}/git/trees", {"base_tree": base_tree, "tree": entries}, token, "create tree"
    )
    commit = _post(
        f"/repos/{repo}/git/commits",
        {"message": message, "tree": tree["sha"], "parents": [base_sha]},
        token,
        "create commit",
    )
    _post(
        f"/repos/{repo}/git/refs",
        {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        token,
        f"create branch {branch}",
    )
    return commit["sha"]
