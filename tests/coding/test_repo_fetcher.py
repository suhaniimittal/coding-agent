import io
import zipfile

import pytest

from src.ingestion import repo_fetcher
from src.ingestion.github_api import GitHubApiError


def _zip(entries: dict[str, str], executable: set[str] = frozenset()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100755 if name in executable else 0o100644) << 16
            zf.writestr(info, content)
    return buf.getvalue()


def test_extract_zip_strips_top_level_folder_and_keeps_exec_bit(tmp_path):
    data = _zip(
        {"org-repo-abc/src/A.java": "class A {}", "org-repo-abc/run.sh": "#!/bin/sh"},
        executable={"org-repo-abc/run.sh"},
    )

    repo_fetcher._extract_zip(data, tmp_path)

    assert (tmp_path / "src" / "A.java").read_text() == "class A {}"
    assert (tmp_path / "run.sh").stat().st_mode & 0o111
    assert not (tmp_path / "src" / "A.java").stat().st_mode & 0o111


def test_extract_zip_rejects_path_escaping_destination(tmp_path):
    data = _zip({"org-repo-abc/../../evil.txt": "x"})

    with pytest.raises(repo_fetcher.RepoFetchError, match="unsafe"):
        repo_fetcher._extract_zip(data, tmp_path / "dest")


def test_cloned_repo_yields_checkout_and_cleans_up(monkeypatch):
    calls = {}
    monkeypatch.setattr(repo_fetcher.github_api, "get_default_branch", lambda slug, token: "master")

    def fake_head(slug, ref, token):
        calls["ref"] = ref
        return "sha9"

    monkeypatch.setattr(repo_fetcher.github_api, "get_remote_head_sha", fake_head)
    monkeypatch.setattr(
        repo_fetcher.github_api,
        "download_zipball",
        lambda slug, ref, token, timeout: _zip({"top/F.txt": "hi"}),
    )

    with repo_fetcher.cloned_repo("org/r", github_token="t") as checkout:
        root = checkout.root
        assert (root / "F.txt").read_text() == "hi"
        assert (checkout.default_branch, checkout.head_sha) == ("master", "sha9")
    assert calls["ref"] == "master"  # no branch given -> the default branch
    assert not root.exists()


def test_cloned_repo_wraps_api_errors(monkeypatch):
    def boom(slug, token):
        raise GitHubApiError("404")

    monkeypatch.setattr(repo_fetcher.github_api, "get_default_branch", boom)

    with pytest.raises(repo_fetcher.RepoFetchError, match="404"):
        with repo_fetcher.cloned_repo("org/r", github_token="t"):
            pass
