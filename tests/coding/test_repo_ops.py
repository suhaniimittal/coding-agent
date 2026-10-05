from __future__ import annotations

import pytest

from src.coding import repo_ops
from src.coding.models import FilePatch
from src.ingestion.github_api import GitHubApiError


def test_branch_name_slugifies_issue_summary():
    name = repo_ops.branch_name("Payment failures not propagated!", "abc123")
    assert name == "fix/payment-failures-not-propagated-abc123"


def test_branch_name_falls_back_when_summary_has_no_alnum():
    assert repo_ops.branch_name("!!!", "abc123") == "fix/change-abc123"


def test_apply_patches_writes_modify_and_create(tmp_path):
    (tmp_path / "a.java").write_text("class A {}\n")
    patches = [
        FilePatch("orders", "a.java", "modify", "class A { /* fixed */ }", ["fix a"]),
        FilePatch("orders", "new/B.java", "create", "class B {}", ["add b"]),
    ]

    repo_ops.apply_patches(tmp_path, patches)

    assert (tmp_path / "a.java").read_text() == "class A { /* fixed */ }"
    assert (tmp_path / "new" / "B.java").read_text() == "class B {}"


def test_apply_patches_delete_removes_file(tmp_path):
    (tmp_path / "a.java").write_text("class A {}\n")

    repo_ops.apply_patches(tmp_path, [FilePatch("orders", "a.java", "delete", None, ["rm"])])

    assert not (tmp_path / "a.java").exists()


def test_apply_patches_delete_missing_file_does_not_raise(tmp_path):
    repo_ops.apply_patches(tmp_path, [FilePatch("orders", "nope.java", "delete", None, [])])


def test_changed_patches_drops_noops_and_missing_deletes(tmp_path):
    (tmp_path / "same.java").write_text("same")
    (tmp_path / "diff.java").write_text("old")
    (tmp_path / "gone.java").write_text("x")
    patches = [
        FilePatch("s", "same.java", "modify", "same", []),
        FilePatch("s", "diff.java", "modify", "new", []),
        FilePatch("s", "fresh.java", "create", "new file", []),
        FilePatch("s", "gone.java", "delete", None, []),
        FilePatch("s", "never-existed.java", "delete", None, []),
    ]

    changed = repo_ops.changed_patches(tmp_path, patches)

    assert [p.file_path for p in changed] == ["diff.java", "fresh.java", "gone.java"]


def test_commit_patches_sends_files_with_modes_to_github(tmp_path, monkeypatch):
    (tmp_path / "run.sh").write_text("#!/bin/sh\n")
    (tmp_path / "run.sh").chmod(0o755)
    (tmp_path / "a.java").write_text("new a")
    patches = [
        FilePatch("s", "run.sh", "modify", "#!/bin/sh\necho hi\n", []),
        FilePatch("s", "a.java", "modify", "new a", []),
        FilePatch("s", "old.java", "delete", None, []),
    ]
    sent = {}

    def fake_create_branch_commit(repo, base_sha, branch, message, files, token=None):
        sent.update(repo=repo, base_sha=base_sha, branch=branch, message=message, files=files)
        return "sha1"

    monkeypatch.setattr(repo_ops.github_api, "create_branch_commit", fake_create_branch_commit)

    sha = repo_ops.commit_patches("org/r", tmp_path, "base1", "fix/x", "fix: msg", patches)

    assert sha == "sha1"
    assert (sent["repo"], sent["base_sha"], sent["branch"]) == ("org/r", "base1", "fix/x")
    assert sent["files"] == [
        {"path": "run.sh", "content": "#!/bin/sh\necho hi\n", "mode": "100755"},
        {"path": "a.java", "content": "new a", "mode": "100644"},
        {"path": "old.java", "content": None, "mode": "100644"},
    ]


def test_commit_patches_wraps_github_errors(tmp_path, monkeypatch):
    def failing(*a, **kw):
        raise GitHubApiError("could not create branch: 403")

    monkeypatch.setattr(repo_ops.github_api, "create_branch_commit", failing)

    with pytest.raises(repo_ops.RepoOpsError, match="403"):
        repo_ops.commit_patches("o/r", tmp_path, "b", "fix/x", "m", [])
