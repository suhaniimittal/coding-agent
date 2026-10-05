"""Resolves a service name to its real GitHub repo by querying the same
Neo4j graph planning_agent writes to — the only way coding_agent knows
which repo a TDD's service name actually means, since it carries no
service-to-repo config file of its own.

This is a deliberate, real dependency on shared infrastructure with the
planning project (unlike the rest of this project, which has none) —
accepted because a service must have gone through planning_agent's own
ingestion at least once for coding_agent to safely know where to push
code. A name with no matching Service node is correctly treated as
unresolvable, never guessed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from neo4j import GraphDatabase

_driver = None


def get_driver():
    """Lazy singleton, same pattern as the planning project's own
    graph_writer.get_driver() — one driver per process, reused across
    calls rather than reconnecting per query."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        )
    return _driver


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    repo: str
    branch: str | None = None


class AmbiguousServiceError(Exception):
    """Two or more graph Service nodes match the same name once case and
    separators are ignored — never guess which repo to push to."""

    def __init__(self, name: str, candidates: list[str]):
        super().__init__(f"{name!r} matches several services: {', '.join(candidates)}")
        self.candidates = candidates


def _normalise(name: str) -> str:
    """Lowercases and drops everything but letters and digits, so
    'BankTransferService', 'bank_transfer_service' and 'bank-transfer-service'
    all compare equal — a TDD names services in prose style, the graph in
    whatever form was ingested."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def list_service_names() -> list[str]:
    """Every Service name in the graph, sorted — used to tell the user what
    a name could have meant when it resolves to nothing."""
    with get_driver().session() as session:
        result = session.run("MATCH (s:Service) RETURN s.name AS name")
        return sorted(r["name"] for r in result if r["name"])


def resolve_service(name: str) -> ServiceSpec | None:
    """Looks up `name`'s real repo in Neo4j, ignoring case and separators
    ('BankTransferService' finds 'bank-transfer-service'). Returns the
    spec under the graph's own service name, or None when nothing matches —
    meaning planning_agent has never ingested it, so there is nothing safe
    to clone. Raises AmbiguousServiceError if more than one node matches.
    `branch` is always None (use the repo's real default branch) — the
    graph only ever stores a service's name and repo, never a pinned
    branch."""
    wanted = _normalise(name)
    if not wanted:
        return None
    with get_driver().session() as session:
        result = session.run("MATCH (s:Service) RETURN s.name AS name, s.repo AS repo")
        matches = [
            (r["name"], r["repo"])
            for r in result
            if r["name"] and r["repo"] and _normalise(r["name"]) == wanted
        ]
    if not matches:
        return None
    if len(matches) > 1:
        raise AmbiguousServiceError(name, sorted(n for n, _ in matches))
    graph_name, repo = matches[0]
    return ServiceSpec(name=graph_name, repo=repo, branch=None)
