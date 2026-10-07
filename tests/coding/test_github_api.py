import httpx
import pytest

from src.ingestion import github_api


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.text = data, status, str(data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=httpx.Request("POST", "http://x"), response=self
            )

    def json(self):
        return self._data


def test_create_branch_commit_runs_blob_tree_commit_ref_in_order(monkeypatch):
    posts = []

    def fake_get(url, **kw):
        assert url.endswith("/git/commits/base1")
        return _Resp({"tree": {"sha": "basetree"}})

    def fake_post(url, headers=None, json=None, timeout=None):
        posts.append((url.replace("https://api.github.com", ""), json))
        n = len(posts)
        return _Resp({"sha": f"sha{n}"})

    monkeypatch.setattr(github_api.httpx, "get", fake_get)
    monkeypatch.setattr(github_api.httpx, "post", fake_post)

    files = [
        {"path": "A.java", "content": "a", "mode": "100644"},
        {"path": "old.java", "content": None, "mode": "100644"},
    ]
    sha = github_api.create_branch_commit("o/r", "base1", "fix/x", "fix: m", files, token="t")

    assert [u for u, _ in posts] == [
        "/repos/o/r/git/blobs",
        "/repos/o/r/git/trees",
        "/repos/o/r/git/commits",
        "/repos/o/r/git/refs",
    ]
    tree_payload = posts[1][1]
    assert tree_payload["base_tree"] == "basetree"
    assert tree_payload["tree"] == [
        {"path": "A.java", "mode": "100644", "type": "blob", "sha": "sha1"},
        {"path": "old.java", "mode": "100644", "type": "blob", "sha": None},
    ]
    assert posts[2][1] == {"message": "fix: m", "tree": "sha2", "parents": ["base1"]}
    assert posts[3][1] == {"ref": "refs/heads/fix/x", "sha": "sha3"}
    assert sha == "sha3"


def test_create_branch_commit_error_includes_github_message(monkeypatch):
    monkeypatch.setattr(
        github_api.httpx, "get", lambda url, **kw: _Resp({"tree": {"sha": "t"}})
    )
    monkeypatch.setattr(
        github_api.httpx,
        "post",
        lambda *a, **kw: _Resp({"message": "Resource not accessible by personal access token"}, 403),
    )

    with pytest.raises(github_api.GitHubApiError, match="not accessible"):
        github_api.create_branch_commit(
            "o/r", "b", "fix/x", "m", [{"path": "A", "content": "a", "mode": "100644"}]
        )


def test_download_zipball_returns_bytes_and_follows_redirects(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen.update(url=url, follow=kw.get("follow_redirects"))
        r = _Resp({})
        r.content = b"zipbytes"
        return r

    monkeypatch.setattr(github_api.httpx, "get", fake_get)

    assert github_api.download_zipball("o/r", "sha1", token="t") == b"zipbytes"
    assert seen["url"].endswith("/repos/o/r/zipball/sha1") and seen["follow"] is True


def test_get_default_branch(monkeypatch):
    monkeypatch.setattr(
        github_api.httpx, "get", lambda url, **kw: _Resp({"default_branch": "master"})
    )
    assert github_api.get_default_branch("o/r", token="t") == "master"


def test_branch_exists_true_and_false(monkeypatch):
    monkeypatch.setattr(github_api.httpx, "get", lambda url, **kw: _Resp({}, 200))
    assert github_api.branch_exists("o/r", "develop", token="t") is True
    monkeypatch.setattr(github_api.httpx, "get", lambda url, **kw: _Resp({}, 404))
    assert github_api.branch_exists("o/r", "develop", token="t") is False


def test_branch_exists_raises_on_other_errors(monkeypatch):
    monkeypatch.setattr(github_api.httpx, "get", lambda url, **kw: _Resp({}, 500))
    with pytest.raises(github_api.GitHubApiError):
        github_api.branch_exists("o/r", "develop", token="t")


def test_ensure_branch_creates_missing_branch_at_given_sha(monkeypatch):
    monkeypatch.setattr(github_api, "branch_exists", lambda *a, **kw: False)
    posts = []
    monkeypatch.setattr(
        github_api.httpx,
        "post",
        lambda url, headers=None, json=None, timeout=None: posts.append((url, json))
        or _Resp({"sha": "x"}),
    )

    assert github_api.ensure_branch("o/r", "develop", "sha1", token="t") is True
    assert posts[0][0].endswith("/repos/o/r/git/refs")
    assert posts[0][1] == {"ref": "refs/heads/develop", "sha": "sha1"}


def test_ensure_branch_leaves_existing_branch_alone(monkeypatch):
    monkeypatch.setattr(github_api, "branch_exists", lambda *a, **kw: True)
    monkeypatch.setattr(
        github_api.httpx, "post", lambda *a, **kw: pytest.fail("must not create an existing branch")
    )
    assert github_api.ensure_branch("o/r", "develop", "sha1", token="t") is False


def test_create_pull_request_error_includes_github_message(monkeypatch):
    monkeypatch.setattr(
        github_api.httpx,
        "post",
        lambda *a, **kw: _Resp({"message": "Validation Failed", "errors": ["base invalid"]}, 422),
    )
    with pytest.raises(github_api.GitHubApiError, match="base invalid"):
        github_api.create_pull_request("o/r", "fix/x", "develop", "t", "b", token="t")


def test_plain_slug_uses_github_com_by_default(monkeypatch):
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    assert github_api._target("o/r") == ("https://api.github.com", "o/r")


def test_plain_slug_uses_github_api_url_when_set(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://github.asurint.com/api/v3/")
    assert github_api._target("keystone/ui") == ("https://github.asurint.com/api/v3", "keystone/ui")


def test_full_enterprise_url_derives_its_own_api_base(monkeypatch):
    monkeypatch.setenv("GITHUB_API_URL", "https://ignored.example/api/v3")
    base, slug = github_api._target("https://github.asurint.com/keystone/reports-api.git")
    assert (base, slug) == ("https://github.asurint.com/api/v3", "keystone/reports-api")


def test_full_github_com_url_uses_public_api():
    assert github_api._target("https://github.com/o/r/") == ("https://api.github.com", "o/r")


def test_requests_go_to_the_enterprise_host(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return _Resp({"default_branch": "develop"})

    monkeypatch.setattr(github_api.httpx, "get", fake_get)
    monkeypatch.setenv("GITHUB_API_URL", "https://github.asurint.com/api/v3")

    assert github_api.get_default_branch("keystone/reports-api", token="t") == "develop"
    assert seen["url"] == "https://github.asurint.com/api/v3/repos/keystone/reports-api"
