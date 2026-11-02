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
    # One branch name for every repo the TDD changes (e.g. "sm/KS-17392-2");
    # empty means the agent generates fix/<issue>-<id>.
    branch_name = str(payload.get("branch_name") or "").strip() or None

    try:
        result = await toolExecutor.execute(
            "run_coding_agent_from_pdf", team_id, file_key, branch_name
        )

        structured_results = [
            (
                {
                    "title": f"PR: {b['service']}",
                    "link": f"[Open PR for {b['service']}]({b['pr_url']})",
                    "label": f"Open PR for {b['service']}",
                    "extension": "url",
                }
                if b.get("pr_url")
                else {
                    "title": f"Branch: {b['service']}",
                    "link": f"[{b['branch']} in {b['repo']}]({b['branch_url']})",
                    "label": f"Open branch for {b['service']}",
                    "extension": "url",
                }
            )
            for b in result["branches"]
        ]

        if structured_results:
            # Only the branch (or PR) links — test results, skipped changes and
            # the TDD title stay in the tool's own activity output.
            return {"status": "success", "results": structured_results}

        # Nothing pushed at all: say why, so an empty result isn't a mystery.
        reasons = (
            "; ".join(
                [f"{n['service']}: {n['reason']}" for n in result.get("service_notes", [])]
                + [f"{s['service']}: {s['reason']}" for s in result["skipped"]]
            )
            or "no changes to apply"
        )
        return {
            "status": "nothing_pushed",
            "message": f"No branch was pushed — {reasons}"[:500],
            "results": [],
        }
    except Exception as error:  # noqa: BLE001 - surface the failure, never hide it
        logger.error("coding_agent run failed: %s", error, exc_info=True)
        return {
            "status": "error",
            "error": type(error).__name__,
            "message": (
                f"The run failed before it could push any branch: {type(error).__name__}: {error}"
            )[:500],
            "file_key": file_key,
            "details": {"workflow_id": workflow_id},
        }
