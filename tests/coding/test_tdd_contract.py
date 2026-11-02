"""R2 (reference), R3 (Code check) and R6 (a reason for every service with no PR)."""

import pytest

from src.coding import coding_flow
from src.coding.graph_lookup import ServiceSpec
from src.coding.models import FilePatch
from src.coding.tdd_models import FileChange, ServicePlan, TechnicalDesignDoc
from src.coding.validation import triage_changes


def _change(**kw) -> FileChange:
    base = dict(
        file_path="OrderService.java",
        function_or_symbol="createOrder",
        change_description="d",
        implementation_notes="n",
        pseudocode_sketch="p",
        reasoning="r",
    )
    base.update(kw)
    return FileChange(**base)


def _tdd(*changes) -> TechnicalDesignDoc:
    return TechnicalDesignDoc(
        title="t",
        issue_summary="s",
        services=[ServicePlan(service="orders", changes=list(changes))],
    )


# --- parsing --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [("reference (read, not changed)", "reference"), ("Modify", "modify"), ("(create)", "create")],
)
def test_change_type_takes_the_first_word(raw, expected):
    assert _change(change_type=raw).change_type == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("VERIFIED — Existing test file `x.test.tsx`", "VERIFIED"),
        ("unverified", "UNVERIFIED"),
        ("NEW — New code in the existing file", "NEW"),
        ("something odd", None),
        (None, None),
    ],
)
def test_code_check_takes_the_first_word(raw, expected):
    assert _change(code_check=raw).code_check == expected


def test_older_tdd_without_the_new_fields_still_parses():
    change = FileChange(**{k: v for k, v in _change().model_dump().items() if k != "code_check"})
    assert change.code_check is None


# --- R2 / R3 triage ----------------------------------------------------------


def test_triage_drops_references_and_skips_unverified():
    ref = _change(change_type="reference")
    unverified = _change(code_check="UNVERIFIED", code_check_note="symbol not found")
    ok = _change(code_check="VERIFIED")
    new = _change(code_check="NEW")

    actionable, skipped, references = triage_changes("orders", [ref, unverified, ok, new])

    assert actionable == [ok, new]
    assert references == 1
    assert len(skipped) == 1 and skipped[0].change is unverified
    assert "UNVERIFIED (symbol not found)" in skipped[0].reason


# --- R6 + end to end --------------------------------------------------------------


def _no_download(monkeypatch):
    def fail(*a, **kw):
        raise AssertionError("must not download a repo when nothing is actionable")

    monkeypatch.setattr(coding_flow, "cloned_repo", fail)
    monkeypatch.setattr(
        coding_flow,
        "resolve_services",
        lambda t: ({"orders": ServiceSpec("orders", "org/orders")}, []),
    )


@pytest.mark.asyncio
async def test_only_reference_entries_give_no_pr_no_download_and_a_reason(monkeypatch):
    _no_download(monkeypatch)

    result = await coding_flow.run_coding_agent(_tdd(_change(change_type="reference")))

    assert result["branches"] == [] and result["skipped"] == []
    assert result["service_notes"] == [
        {
            "service": "orders",
            "reason": "no actionable changes: 1 reference-only (read, not changed)",
        }
    ]


@pytest.mark.asyncio
async def test_service_with_no_changes_gets_a_reason(monkeypatch):
    _no_download(monkeypatch)

    result = await coding_flow.run_coding_agent(_tdd())

    assert result["service_notes"][0]["reason"] == "the TDD lists no file changes for this service"


@pytest.mark.asyncio
async def test_reference_file_is_never_sent_to_the_llm(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from src.ingestion.repo_fetcher import RepoCheckout

    (tmp_path / "OrderService.java").write_text("class OrderService {}\n")
    (tmp_path / "Helper.java").write_text("class Helper {}\n")

    @contextmanager
    def fake_clone(repo, branch=None, **kw):
        yield RepoCheckout(tmp_path, "main", "sha")

    monkeypatch.setattr(coding_flow, "cloned_repo", fake_clone)
    monkeypatch.setattr(
        coding_flow,
        "resolve_services",
        lambda t: ({"orders": ServiceSpec("orders", "org/orders")}, []),
    )
    seen = {}

    async def fake_patch(service, changes, file_contents):
        seen["files"] = sorted(file_contents)
        return [
            FilePatch(service, "OrderService.java", "modify", "class OrderService { int x; }", [])
        ], []

    monkeypatch.setattr(coding_flow.patcher, "generate_service_patch", fake_patch)
    monkeypatch.setattr(coding_flow.repo_ops, "commit_patches", lambda *a, **kw: "sha2")
    monkeypatch.setattr(
        coding_flow.github_api, "create_pull_request", lambda *a, **kw: "https://x/pull/1"
    )

    result = await coding_flow.run_coding_agent(
        _tdd(_change(), _change(file_path="Helper.java", change_type="reference"))
    )

    assert seen["files"] == ["OrderService.java"]
    assert len(result["branches"]) == 1 and result["service_notes"] == []
