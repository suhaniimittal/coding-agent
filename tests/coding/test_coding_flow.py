from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.coding import coding_flow
from src.coding.graph_lookup import ServiceSpec
from src.coding.models import FilePatch, SkippedChange, VerificationResult
from src.coding.tdd_models import FileChange, ServicePlan, TechnicalDesignDoc
from src.ingestion.github_api import GitHubApiError
from src.ingestion.repo_fetcher import RepoCheckout, RepoFetchError


def _init_repo(tmp_path):
    (tmp_path / "OrderService.java").write_text("class OrderService {}\n")


def _change(**overrides) -> FileChange:
    defaults = dict(
        file_path="OrderService.java",
        function_or_symbol="createOrder",
        change_description="propagate payment failure",
        implementation_notes="n",
        pseudocode_sketch="p",
        reasoning="r",
        change_type="modify",
        acceptance_criteria=["failure propagates to checkout"],
    )
    defaults.update(overrides)
    return FileChange(**defaults)


def _tdd(change: FileChange) -> TechnicalDesignDoc:
    return TechnicalDesignDoc(
        title="Fix payment failure propagation",
        issue_summary="payment failures not propagated",
        services=[ServicePlan(service="orders", changes=[change])],
    )


def _fake_cloned_repo(tmp_path, default_branch="main"):
    @contextmanager
    def _fake(repo, branch=None, **kw):
        yield RepoCheckout(tmp_path, default_branch, "basesha123")

    return _fake


def _stub_commit(monkeypatch):
    """Replaces the GitHub commit call; returns the dict recording its args."""
    committed = {}

    def fake_commit_patches(repo, repo_root, base_sha, branch, message, patches):
        committed.update(
            repo=repo, base_sha=base_sha, branch=branch, message=message, patches=patches
        )
        return "newcommitsha"

    monkeypatch.setattr(coding_flow.repo_ops, "commit_patches", fake_commit_patches)
    return committed


_SPEC = ServiceSpec(name="orders", repo="org/orders")


def _resolve_orders(monkeypatch):
    monkeypatch.setattr(coding_flow, "resolve_services", lambda tdd: ({"orders": _SPEC}, []))


@pytest.mark.asyncio
async def test_happy_path_opens_pr(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    change = _change()
    tdd = _tdd(change)

    _resolve_orders(monkeypatch)
    monkeypatch.setattr(coding_flow, "cloned_repo", _fake_cloned_repo(tmp_path))

    async def fake_generate_service_patch(service, changes, file_contents):
        assert file_contents == {"OrderService.java": "class OrderService {}\n"}
        patch = FilePatch(service, "OrderService.java", "modify", "class OrderService { /* fixed */ }", ["propagate payment failure"])
        return [patch], []

    monkeypatch.setattr(coding_flow.patcher, "generate_service_patch", fake_generate_service_patch)

    committed = _stub_commit(monkeypatch)

    created_pr = {}

    def fake_create_pull_request(repo, head, base, title, body, token=None):
        created_pr.update(repo=repo, head=head, base=base, title=title, body=body)
        return "https://github.com/org/orders/pull/1"

    monkeypatch.setattr(coding_flow.github_api, "create_pull_request", fake_create_pull_request)

    result = await coding_flow.run_coding_agent(tdd)

    assert result["skipped"] == []
    assert len(result["pull_requests"]) == 1
    pr = result["pull_requests"][0]
    assert pr["service"] == "orders"
    assert pr["pr_url"] == "https://github.com/org/orders/pull/1"
    assert pr["verification"].ran is False  # no test command marker present in tmp_path
    assert committed["branch"].startswith("fix/")
    assert committed["base_sha"] == "basesha123"
    assert [p.file_path for p in committed["patches"]] == ["OrderService.java"]
    assert created_pr["head"] == committed["branch"]
    assert created_pr["repo"] == "org/orders"
    assert created_pr["base"] == "main"
    assert "propagate payment failure" in created_pr["body"]
    assert "failure propagates to checkout" in created_pr["body"]


@pytest.mark.asyncio
async def test_change_rejected_by_real_clone_check_is_skipped_not_pr_opened(tmp_path, monkeypatch):
    """The point of the whole rewrite: a stale file_path is caught against
    the REAL clone, with no Neo4j graph involved at all."""
    _init_repo(tmp_path)
    change = _change(file_path="DoesNotExist.java")
    tdd = _tdd(change)

    _resolve_orders(monkeypatch)
    monkeypatch.setattr(coding_flow, "cloned_repo", _fake_cloned_repo(tmp_path))
    monkeypatch.setattr(coding_flow.github_api, "create_pull_request", lambda *a, **kw: pytest.fail("should not open a PR"))

    result = await coding_flow.run_coding_agent(tdd)

    assert result["pull_requests"] == []
    assert len(result["skipped"]) == 1
    assert "not found in the current clone" in result["skipped"][0].reason


@pytest.mark.asyncio
async def test_no_pr_when_patch_content_unchanged(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    change = _change()
    tdd = _tdd(change)

    _resolve_orders(monkeypatch)
    monkeypatch.setattr(coding_flow, "cloned_repo", _fake_cloned_repo(tmp_path))

    async def fake_generate_service_patch(service, changes, file_contents):
        patch = FilePatch(service, "OrderService.java", "modify", file_contents["OrderService.java"], ["no-op"])
        return [patch], []

    monkeypatch.setattr(coding_flow.patcher, "generate_service_patch", fake_generate_service_patch)

    called = {"commit": False, "pr": False}
    monkeypatch.setattr(coding_flow.repo_ops, "commit_patches", lambda *a, **kw: called.__setitem__("commit", True))
    monkeypatch.setattr(coding_flow.github_api, "create_pull_request", lambda *a, **kw: called.__setitem__("pr", True))

    result = await coding_flow.run_coding_agent(tdd)

    assert result["pull_requests"] == []
    assert called == {"commit": False, "pr": False}


@pytest.mark.asyncio
async def test_patcher_error_is_skipped_not_raised(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    change = _change()
    tdd = _tdd(change)

    _resolve_orders(monkeypatch)
    monkeypatch.setattr(coding_flow, "cloned_repo", _fake_cloned_repo(tmp_path))

    async def failing_generate_service_patch(*a, **kw):
        raise coding_flow.patcher.PatcherError("LLM call failed: provider down")

    monkeypatch.setattr(coding_flow.patcher, "generate_service_patch", failing_generate_service_patch)
    monkeypatch.setattr(coding_flow.github_api, "create_pull_request", lambda *a, **kw: pytest.fail("should not open a PR"))

    result = await coding_flow.run_coding_agent(tdd)

    assert result["pull_requests"] == []
    assert len(result["skipped"]) == 1
    assert "provider down" in result["skipped"][0].reason


@pytest.mark.asyncio
async def test_missing_file_in_llm_response_is_skipped(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    change = _change()
    tdd = _tdd(change)

    _resolve_orders(monkeypatch)
    monkeypatch.setattr(coding_flow, "cloned_repo", _fake_cloned_repo(tmp_path))

    async def fake_generate_service_patch(service, changes, file_contents):
        return [], ["OrderService.java"]

    monkeypatch.setattr(coding_flow.patcher, "generate_service_patch", fake_generate_service_patch)
    monkeypatch.setattr(coding_flow.github_api, "create_pull_request", lambda *a, **kw: pytest.fail("should not open a PR"))

    result = await coding_flow.run_coding_agent(tdd)

    assert result["pull_requests"] == []
    assert len(result["skipped"]) == 1
    assert "missing content" in result["skipped"][0].reason


@pytest.mark.asyncio
async def test_unresolved_service_never_gets_cloned(tmp_path, monkeypatch):
    change = _change()
    tdd = _tdd(change)
    pre_skipped = [SkippedChange("orders", change, "service not found in the graph")]

    monkeypatch.setattr(coding_flow, "resolve_services", lambda t: ({}, pre_skipped))

    def fail_clone(*a, **kw):
        raise AssertionError("must not clone a service that failed graph resolution")

    monkeypatch.setattr(coding_flow, "cloned_repo", fail_clone)

    result = await coding_flow.run_coding_agent(tdd)

    assert result["pull_requests"] == []
    assert result["skipped"] == pre_skipped


@pytest.mark.asyncio
async def test_single_llm_call_for_multiple_files_in_one_service(tmp_path, monkeypatch):
    """The whole point of the per-service call: 2 files in the TDD -> 1 LLM
    call, not 2 — and a real test failure is reported honestly, never
    retried with another LLM call."""
    _init_repo(tmp_path)
    (tmp_path / "Other.java").write_text("class Other {}\n")

    change_a = _change(file_path="OrderService.java")
    change_b = _change(file_path="Other.java", function_or_symbol="other", change_type="modify")
    tdd = TechnicalDesignDoc(
        title="Fix payment failure propagation",
        issue_summary="payment failures not propagated",
        services=[ServicePlan(service="orders", changes=[change_a, change_b])],
    )

    _resolve_orders(monkeypatch)
    monkeypatch.setattr(coding_flow, "cloned_repo", _fake_cloned_repo(tmp_path))

    call_count = {"n": 0}

    async def fake_generate_service_patch(service, changes, file_contents):
        call_count["n"] += 1
        assert len(changes) == 2
        return [
            FilePatch(service, "OrderService.java", "modify", "class OrderService { /* fixed */ }", ["propagate payment failure"]),
            FilePatch(service, "Other.java", "modify", "class Other { /* fixed */ }", ["propagate payment failure"]),
        ], []

    monkeypatch.setattr(coding_flow.patcher, "generate_service_patch", fake_generate_service_patch)
    _stub_commit(monkeypatch)
    monkeypatch.setattr(
        coding_flow.test_runner,
        "run_tests",
        lambda repo_root, timeout=300: VerificationResult(ran=True, passed=False, detail="AssertionError: boom"),
    )

    created_pr = {}

    def fake_create_pull_request(repo, head, base, title, body, token=None):
        created_pr["body"] = body
        return "https://github.com/org/orders/pull/2"

    monkeypatch.setattr(coding_flow.github_api, "create_pull_request", fake_create_pull_request)

    result = await coding_flow.run_coding_agent(tdd)

    assert call_count["n"] == 1  # one call for the whole service, not per file
    assert len(result["pull_requests"]) == 1
    assert result["pull_requests"][0]["verification"].passed is False
    assert "FAILED" in created_pr["body"]


@pytest.mark.asyncio
async def test_pr_base_uses_repos_real_default_branch_not_hardcoded_main(tmp_path, monkeypatch):
    """Regression test: a real run against a repo whose default branch is
    "master" (not "main") got a 422 from GitHub because the base branch was
    hardcoded — must use whatever branch was actually checked out."""
    _init_repo(tmp_path)
    change = _change()
    tdd = _tdd(change)

    _resolve_orders(monkeypatch)
    monkeypatch.setattr(coding_flow, "cloned_repo", _fake_cloned_repo(tmp_path, "master"))

    async def fake_generate_service_patch(service, changes, file_contents):
        patch = FilePatch(service, "OrderService.java", "modify", "class OrderService { /* fixed */ }", ["propagate payment failure"])
        return [patch], []

    monkeypatch.setattr(coding_flow.patcher, "generate_service_patch", fake_generate_service_patch)
    _stub_commit(monkeypatch)

    created_pr = {}

    def fake_create_pull_request(repo, head, base, title, body, token=None):
        created_pr["base"] = base
        return "https://github.com/org/orders/pull/3"

    monkeypatch.setattr(coding_flow.github_api, "create_pull_request", fake_create_pull_request)

    await coding_flow.run_coding_agent(tdd)

    assert created_pr["base"] == "master"


def _one_file_patch_setup(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _resolve_orders(monkeypatch)
    monkeypatch.setattr(coding_flow, "cloned_repo", _fake_cloned_repo(tmp_path))

    async def fake_generate_service_patch(service, changes, file_contents):
        patch = FilePatch(service, "OrderService.java", "modify", "class OrderService { /* fixed */ }", ["x"])
        return [patch], []

    monkeypatch.setattr(coding_flow.patcher, "generate_service_patch", fake_generate_service_patch)


@pytest.mark.asyncio
async def test_download_failure_is_skipped_not_raised(tmp_path, monkeypatch):
    change = _change()
    _resolve_orders(monkeypatch)

    def failing_clone(*a, **kw):
        raise RepoFetchError("could not download org/orders: 404")

    monkeypatch.setattr(coding_flow, "cloned_repo", failing_clone)

    result = await coding_flow.run_coding_agent(_tdd(change))

    assert result["pull_requests"] == []
    assert len(result["skipped"]) == 1
    assert "RepoFetchError" in result["skipped"][0].reason


@pytest.mark.asyncio
async def test_commit_failure_is_skipped_and_no_pr_opened(tmp_path, monkeypatch):
    _one_file_patch_setup(tmp_path, monkeypatch)

    def failing_commit(*a, **kw):
        raise coding_flow.repo_ops.RepoOpsError("could not create branch: 403 Forbidden")

    monkeypatch.setattr(coding_flow.repo_ops, "commit_patches", failing_commit)
    monkeypatch.setattr(coding_flow.github_api, "create_pull_request", lambda *a, **kw: pytest.fail("should not open a PR"))

    result = await coding_flow.run_coding_agent(_tdd(_change()))

    assert result["pull_requests"] == []
    assert "403 Forbidden" in result["skipped"][0].reason


@pytest.mark.asyncio
async def test_pr_failure_is_skipped_not_raised(tmp_path, monkeypatch):
    _one_file_patch_setup(tmp_path, monkeypatch)
    _stub_commit(monkeypatch)

    def failing_pr(*a, **kw):
        raise GitHubApiError("could not open PR: 422")

    monkeypatch.setattr(coding_flow.github_api, "create_pull_request", failing_pr)

    result = await coding_flow.run_coding_agent(_tdd(_change()))

    assert result["pull_requests"] == []
    assert "GitHubApiError" in result["skipped"][0].reason


@pytest.mark.asyncio
async def test_missing_requested_base_branch_is_created_before_the_fix_branch(tmp_path, monkeypatch):
    _one_file_patch_setup(tmp_path, monkeypatch)
    order = []
    monkeypatch.setattr(
        coding_flow.github_api,
        "ensure_branch",
        lambda repo, branch, sha, token=None: order.append(("ensure", repo, branch, sha)),
    )
    monkeypatch.setattr(
        coding_flow.repo_ops,
        "commit_patches",
        lambda *a, **kw: order.append(("commit",)) or "sha",
    )
    prs = {}

    def fake_pr(repo, head, base, title, body, token=None):
        prs["base"] = base
        return "https://github.com/org/orders/pull/9"

    monkeypatch.setattr(coding_flow.github_api, "create_pull_request", fake_pr)

    result = await coding_flow.run_coding_agent(
        _tdd(_change()), base_branch_by_service={"orders": "develop"}
    )

    assert order == [("ensure", "org/orders", "develop", "basesha123"), ("commit",)]
    assert prs["base"] == "develop"
    assert len(result["pull_requests"]) == 1


@pytest.mark.asyncio
async def test_default_base_branch_is_never_created(tmp_path, monkeypatch):
    _one_file_patch_setup(tmp_path, monkeypatch)
    _stub_commit(monkeypatch)
    monkeypatch.setattr(
        coding_flow.github_api,
        "ensure_branch",
        lambda *a, **kw: pytest.fail("default branch needs no creation"),
    )
    monkeypatch.setattr(
        coding_flow.github_api, "create_pull_request", lambda *a, **kw: "https://x/pull/1"
    )

    await coding_flow.run_coding_agent(_tdd(_change()))
