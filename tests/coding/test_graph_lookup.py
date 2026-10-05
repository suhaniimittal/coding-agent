import pytest

from src.coding import graph_lookup


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def run(self, query, **params):
        return iter(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDriver:
    def __init__(self, rows):
        self._session = FakeSession(rows)

    def session(self):
        return self._session


def _patch_driver(monkeypatch, rows):
    monkeypatch.setattr(graph_lookup, "get_driver", lambda: FakeDriver(rows))


ROWS = [
    {"name": "orders", "repo": "org/orders"},
    {"name": "bank-transfer-service", "repo": "org/bank-transfer-service"},
]


def test_exact_name_resolves(monkeypatch):
    _patch_driver(monkeypatch, ROWS)
    assert graph_lookup.resolve_service("orders") == graph_lookup.ServiceSpec(
        name="orders", repo="org/orders", branch=None
    )


def test_prose_style_name_resolves_to_graph_name(monkeypatch):
    _patch_driver(monkeypatch, ROWS)
    spec = graph_lookup.resolve_service("BankTransferService")
    assert spec == graph_lookup.ServiceSpec(
        name="bank-transfer-service", repo="org/bank-transfer-service", branch=None
    )


def test_underscore_and_space_variants_resolve(monkeypatch):
    _patch_driver(monkeypatch, ROWS)
    assert graph_lookup.resolve_service("bank_transfer_service").name == "bank-transfer-service"
    assert graph_lookup.resolve_service("Bank Transfer Service").name == "bank-transfer-service"


def test_unknown_service_returns_none(monkeypatch):
    _patch_driver(monkeypatch, ROWS)
    assert graph_lookup.resolve_service("ghost") is None


def test_empty_name_returns_none(monkeypatch):
    _patch_driver(monkeypatch, ROWS)
    assert graph_lookup.resolve_service("---") is None


def test_node_without_repo_is_not_resolvable(monkeypatch):
    _patch_driver(monkeypatch, [{"name": "orders", "repo": None}])
    assert graph_lookup.resolve_service("orders") is None


def test_ambiguous_name_raises_with_candidates(monkeypatch):
    _patch_driver(
        monkeypatch,
        [{"name": "user-service", "repo": "o/a"}, {"name": "UserService", "repo": "o/b"}],
    )
    with pytest.raises(graph_lookup.AmbiguousServiceError) as exc:
        graph_lookup.resolve_service("userservice")
    assert exc.value.candidates == ["UserService", "user-service"]


def test_list_service_names_sorted(monkeypatch):
    _patch_driver(monkeypatch, ROWS)
    assert graph_lookup.list_service_names() == ["bank-transfer-service", "orders"]
