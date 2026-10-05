"""Agent — coding_agent.

Thin Aetherion binding: takes an uploaded TDD PDF and turns it into real
Pull Requests. No pipeline logic lives here — per the SDK's "no direct I/O
in the agent" rule, the tool does all the actual PDF/LLM/git/GitHub work;
this just orchestrates the one call via `toolExecutor.execute` and shapes
the result for the platform UI.

The one piece of logic here is the error boundary. A workflow that raises
shows up in the console as a failed run with *no output to summarise*, which
says nothing about what broke. Catching it, logging the traceback worker-side
and returning a structured error means the failure is readable in the run
itself. Nothing is swallowed: the full traceback still reaches the worker log.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aetherion_sdk import agent, toolExecutor

logger = logging.getLogger(__name__)


def _workflow_id() -> str | None:
    """Best-effort workflow id for logging; ``None`` outside a workflow."""
    try:
        from temporalio import workflow

        return workflow.info().workflow_id
    except Exception:
        return None


def _parse_base_branch_overrides(raw: Any) -> dict[str, str] | None:
    """The trigger form field arrives as a plain string (there's no native
    "object" trigger type) — accepts either an already-parsed dict (a
    direct CLI/API invocation) or the JSON text the platform UI's text
    field actually sends. Malformed input is ignored rather than failing
    the whole run over an optional field."""
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


@agent(name="coding-agent")
async def coding_agent(payload: dict[str, Any]) -> dict[str, Any]:
    workflow_id = _workflow_id()
    team_id = payload.get("team_id")
    # "uploaded_files" is the platform's own key for a `file`-type trigger's
    # value — it IS the file_key (== file_name) in storage, not file content.
    # The platform delivers it as a list of keys (one per uploaded file), even
    # for a single file; the tool takes exactly one TDD PDF as a plain string.
    uploaded = payload.get("uploaded_files")
    file_key = uploaded[0] if isinstance(uploaded, list) and uploaded else uploaded
    base_branch_by_service = _parse_base_branch_overrides(payload.get("base_branch_by_service"))

    try:
        result = await toolExecutor.execute(
            "run_coding_agent_from_pdf", team_id, file_key, base_branch_by_service
        )

        structured_results = [
            {
                "title": f"PR: {pr['service']}",
                "link": f"[{pr['pr_url']}]({pr['pr_url']})",
                "label": f"Open PR for {pr['service']}",
                "extension": "url",
            }
            for pr in result["pull_requests"]
        ]

        if structured_results:
            # Only the PR links — test results, skipped changes and the TDD
            # title stay in the tool's own activity output, not the run result.
            return {"status": "success", "results": structured_results}

        # No PR at all: say why, so an empty result isn't a silent mystery.
        reasons = (
            "; ".join(f"{s['service']}: {s['reason']}" for s in result["skipped"])
            or "no changes to apply"
        )
        return {
            "status": "no_pull_requests",
            "message": f"No Pull Requests were created — {reasons}"[:500],
            "results": [],
        }
    except Exception as error:  # noqa: BLE001 - surface the failure, never hide it
        logger.error("coding_agent run failed: %s", error, exc_info=True)
        return {
            "status": "error",
            "error": type(error).__name__,
            "message": (
                f"The run failed before it could produce any Pull Requests: "
                f"{type(error).__name__}: {error}"
            )[:500],
            "file_key": file_key,
            "details": {"workflow_id": workflow_id},
        }
