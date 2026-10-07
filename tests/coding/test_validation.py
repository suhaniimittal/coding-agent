from __future__ import annotations

from src.coding.graph_lookup import ServiceSpec
from src.coding.tdd_models import FileChange, ServicePlan, TechnicalDesignDoc
from src.coding.validation import resolve_services, verify_changes_for_service


def _change(**overrides) -> FileChange:
    defaults = dict(
        file_path="src/App.java",
        function_or_symbol="handler",
        change_description="fix it",
        implementation_notes="notes",
        pseudocode_sketch="pseudo",
        reasoning="because",
        change_type="modify",
    )
    defaults.update(overrides)
    return FileChange(**defaults)


# --- resolve_services --------------------------------------------------


def test_resolve_services_finds_known_service(monkeypatch):
    monkeypatch.setattr(
        "src.coding.validation.resolve_service",
        lambda name: ServiceSpec(name="orders", repo="org/orders") if name == "orders" else None,
    )
    tdd = TechnicalDesignDoc(
        title="t", issue_summary="s", services=[ServicePlan(service="orders", changes=[_change()])]
    )
    resolved, skipped = resolve_services(tdd)
    assert list(resolved.keys()) == ["orders"]
    assert skipped == []


def test_resolve_services_skips_service_with_no_matching_graph_node(monkeypatch):
    monkeypatch.setattr("src.coding.validation.resolve_service", lambda name: None)
    tdd = TechnicalDesignDoc(
        title="t",
        issue_summary="s",
        services=[ServicePlan(service="ghost", changes=[_change(), _change(file_path="b.java")])],
    )
    resolved, skipped = resolve_services(tdd)
    assert resolved == {}
    assert len(skipped) == 2
    assert all("service not found in the graph" in s.reason for s in skipped)


def test_unresolved_service_reason_lists_known_services(monkeypatch):
    monkeypatch.setattr("src.coding.validation.resolve_service", lambda name: None)
    monkeypatch.setattr("src.coding.validation.list_service_names", lambda: ["a", "b"])
    tdd = TechnicalDesignDoc(
        title="t", issue_summary="s", services=[ServicePlan(service="ghost", changes=[_change()])]
    )
    _, skipped = resolve_services(tdd)
    assert "Known services: a, b" in skipped[0].reason


def test_ambiguous_service_is_skipped_not_guessed(monkeypatch):
    from src.coding.graph_lookup import AmbiguousServiceError

    def _raise(name):
        raise AmbiguousServiceError(name, ["user-service", "UserService"])

    monkeypatch.setattr("src.coding.validation.resolve_service", _raise)
    tdd = TechnicalDesignDoc(
        title="t", issue_summary="s", services=[ServicePlan(service="user", changes=[_change()])]
    )
    resolved, skipped = resolve_services(tdd)
    assert resolved == {}
    assert "ambiguous" in skipped[0].reason and "user-service" in skipped[0].reason


# --- verify_changes_for_service -----------------------------------------


def test_create_change_verified_without_touching_disk(tmp_path):
    spec = ServiceSpec(name="orders", repo="org/orders")
    change = _change(change_type="create", file_path="new/File.java")
    verified, skipped = verify_changes_for_service("orders", spec, tmp_path, [change])
    assert len(verified) == 1
    assert skipped == []


def test_create_change_without_file_path_is_skipped(tmp_path):
    spec = ServiceSpec(name="orders", repo="org/orders")
    change = _change(change_type="create", file_path=None)
    verified, skipped = verify_changes_for_service("orders", spec, tmp_path, [change])
    assert verified == []
    assert "no file_path" in skipped[0].reason


def test_modify_change_verified_when_file_really_exists_in_clone(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.java").write_text("class App {}")
    spec = ServiceSpec(name="orders", repo="org/orders")
    change = _change(change_type="modify", file_path="src/App.java")
    verified, skipped = verify_changes_for_service("orders", spec, tmp_path, [change])
    assert len(verified) == 1
    assert skipped == []


def test_modify_change_skipped_when_file_missing_from_clone(tmp_path):
    """The real design point of this rewrite: no Neo4j graph is consulted —
    only whatever is actually present in the clone right now."""
    spec = ServiceSpec(name="orders", repo="org/orders")
    change = _change(change_type="modify", file_path="src/Gone.java")
    verified, skipped = verify_changes_for_service("orders", spec, tmp_path, [change])
    assert verified == []
    assert "not found in the current clone" in skipped[0].reason


def test_modify_change_never_requires_the_symbol_itself_to_preexist(tmp_path):
    """A brand-new method added to an existing file must still verify —
    only the FILE's existence is checked, never the symbol's."""
    (tmp_path / "App.java").write_text("class App {}")
    spec = ServiceSpec(name="orders", repo="org/orders")
    change = _change(
        change_type="modify", file_path="App.java", function_or_symbol="brandNewMethod"
    )
    verified, skipped = verify_changes_for_service("orders", spec, tmp_path, [change])
    assert len(verified) == 1
    assert skipped == []


def test_missing_function_or_symbol_is_skipped(tmp_path):
    (tmp_path / "App.java").write_text("class App {}")
    spec = ServiceSpec(name="orders", repo="org/orders")
    change = _change(change_type="modify", file_path="App.java", function_or_symbol="")
    verified, skipped = verify_changes_for_service("orders", spec, tmp_path, [change])
    assert verified == []
    assert "missing file_path or function_or_symbol" in skipped[0].reason


def test_path_escaping_the_repo_is_skipped_for_every_change_type(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "secret.txt").write_text("outside")
    spec = ServiceSpec(name="orders", repo="org/orders")
    bad_paths = ["../secret.txt", "../../etc/passwd", str(tmp_path / "secret.txt")]
    changes = [
        _change(file_path=p, change_type=t)
        for p in bad_paths
        for t in ("modify", "create", "delete")
    ]

    verified, skipped = verify_changes_for_service("orders", spec, repo, changes)

    assert verified == []
    assert len(skipped) == len(changes)
    assert all("outside the repository" in s.reason for s in skipped)


def test_nested_path_inside_the_repo_is_still_verified(tmp_path):
    (tmp_path / "src" / "main").mkdir(parents=True)
    (tmp_path / "src" / "main" / "A.java").write_text("class A {}")
    spec = ServiceSpec(name="orders", repo="org/orders")

    verified, skipped = verify_changes_for_service(
        "orders", spec, tmp_path, [_change(file_path="src/main/A.java")]
    )

    assert len(verified) == 1 and skipped == []
