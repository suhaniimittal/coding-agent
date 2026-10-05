"""Two independent checks on a TDD's proposed changes, neither one an LLM
call:

- resolve_services(): which repo does each service name actually mean?
  Answered by querying Neo4j (graph_lookup.py) — the same graph
  planning_agent writes to. A service planning_agent has never ingested
  has no Service node, so it's correctly treated as unresolvable.
- verify_changes_for_service(): does a proposed file_path really exist?
  Answered by checking the real, freshly-made git clone — never the
  graph, since the graph can be arbitrarily older than right now and this
  check specifically exists to catch drift since the TDD was generated.
"""

from __future__ import annotations

from pathlib import Path

from .graph_lookup import AmbiguousServiceError, ServiceSpec, list_service_names, resolve_service
from .models import SkippedChange, VerifiedChange
from .tdd_models import TechnicalDesignDoc


def verify_changes_for_service(
    service_plan_service: str, spec: ServiceSpec, repo_root: Path, changes
) -> tuple[list[VerifiedChange], list[SkippedChange]]:
    """Splits one service's changes into `verified` (safe to auto-patch) and
    `skipped` (flagged for a human, never applied), checked against
    `repo_root` — an already-cloned working tree for this exact service.

    A "create" change has nothing to look up yet (the file doesn't exist),
    so it's verified as long as it names a file_path. A "modify"/"delete"
    change must resolve to a real file ON DISK in the clone, not just the
    PDF's own claim — this catches a file_path that's genuinely stale
    (moved, renamed, deleted since the TDD was generated).

    Deliberately NOT required: that `function_or_symbol` itself already
    exists in that file. A real, common "modify" is adding a brand-new
    method/field to an existing file — the symbol being named doesn't exist
    yet BY DESIGN, that's the whole point of the change, so requiring it to
    pre-exist would wrongly reject exactly the changes this pipeline most
    needs to apply. The patcher step is always given the file's real
    current content anyway, so it sees the true state regardless of what
    this check confirms.
    """
    verified: list[VerifiedChange] = []
    skipped: list[SkippedChange] = []

    for change in changes:
        if change.change_type == "create":
            if not change.file_path:
                skipped.append(
                    SkippedChange(
                        service_plan_service,
                        change,
                        "change_type is 'create' but no file_path given",
                    )
                )
                continue
            verified.append(VerifiedChange(service_plan_service, spec.repo, spec.branch, change))
            continue

        if not change.file_path or not change.function_or_symbol:
            skipped.append(
                SkippedChange(
                    service_plan_service, change, "missing file_path or function_or_symbol"
                )
            )
            continue

        if (repo_root / change.file_path).exists():
            verified.append(VerifiedChange(service_plan_service, spec.repo, spec.branch, change))
        else:
            skipped.append(
                SkippedChange(
                    service_plan_service,
                    change,
                    f"{change.file_path!r} not found in the current clone of {spec.repo!r} — "
                    "may have moved, been renamed, or been deleted since the TDD was generated",
                )
            )

    return verified, skipped


def _known_services() -> str:
    try:
        return ", ".join(list_service_names()) or "none"
    except Exception:  # noqa: BLE001 - a diagnostic aid must never mask the real skip
        return "unavailable"


def resolve_services(tdd: TechnicalDesignDoc) -> tuple[dict[str, ServiceSpec], list[SkippedChange]]:
    """Resolves every service the TDD names against the real Neo4j graph.
    Returns (spec_by_service_name, skipped) — a service with no matching
    Service node (never ingested by planning_agent) has every one of its
    changes skipped up front, before any clone is attempted for it."""
    resolved: dict[str, ServiceSpec] = {}
    skipped: list[SkippedChange] = []

    for service_plan in tdd.services:
        reason = None
        try:
            spec = resolve_service(service_plan.service)
        except AmbiguousServiceError as error:
            spec, reason = (
                None,
                (
                    f"service name is ambiguous in the graph ({', '.join(error.candidates)}) "
                    "— not guessing which repo to push to"
                ),
            )
        if spec is None:
            reason = reason or (
                "service not found in the graph — planning_agent has never ingested it, "
                f"so there's no known repo to push to. Known services: {_known_services()}"
            )
            for change in service_plan.changes:
                skipped.append(SkippedChange(service_plan.service, change, reason))
            continue
        resolved[service_plan.service] = spec

    return resolved, skipped
