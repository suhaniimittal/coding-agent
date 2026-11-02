"""Agent — coding_agent.

Thin Aetherion binding: takes an uploaded TDD PDF and turns it into pull
requests. No pipeline logic lives here — per the SDK's "no direct I/O in the
agent" rule, the tool does all the actual PDF/LLM/GitHub work; this just
orchestrates the one call via `toolExecutor.execute` and shapes the result
for the platform UI: one link per pull request, or why there is none.

The one piece of logic here is the error boundary. A workflow that raises
shows up in the console as a failed run with *no output to summarise*, which
says nothing about what broke. Catching it, logging the traceback worker-side
and returning a structured error means the failure is readable in the run
itself. Nothing is swallowed: the full traceback still reaches the worker log.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from aetherion_sdk import agent, toolExecutor
from temporalio.common import RetryPolicy

# A run downloads each service and makes a few long model calls per service;
# the platform's default activity timeout (~30 s) would kill it mid-way.
_RUN_TIMEOUT = timedelta(minutes=15)
# Never re-run on its own: a second run would open a second branch and pull
# request beside the first one's. A failure is reported instead.
_RUN_RETRY = RetryPolicy(maximum_attempts=1)

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
    # empty means the agent generates fix/<ticket>-<id>.
    branch_name = str(payload.get("branch_name") or "").strip() or None

    try:
        result = await toolExecutor.execute(
            "run_coding_agent_from_pdf",
            team_id,
            file_key,
            branch_name,
            start_to_close_timeout=_RUN_TIMEOUT,
            retry_policy=_RUN_RETRY,
        )
    except Exception as error:  # noqa: BLE001 - surface the failure, never hide it
        logger.error("coding_agent run failed: %s", error, exc_info=True)
        root = _root(error)
        return {
            "status": "error",
            "error": type(root).__name__,
            "message": f"The run failed before any pull request was opened: {root}"[:1000],
            "file_key": file_key,
            "details": {"workflow_id": workflow_id},
        }

    services = result["services"]
    links = [
        {
            "title": f"{'Draft PR' if s.get('draft') else 'PR'}: {s['service']}",
            "link": f"[Open the pull request for {s['service']}]({s['pr_url']})",
            "label": f"Open PR for {s['service']}",
            "extension": "url",
        }
        for s in services
        if s.get("pr_url")
    ]
    others = [
        f"{s['service']}: {s['status']} — {s['reason']}" for s in services if not s.get("pr_url")
    ]
    if links:
        out: dict[str, Any] = {"status": "success", "results": links}
        if others:
            out["message"] = "; ".join(others)[:1000]
        return out
    return {
        "status": "nothing_pushed",
        "message": ("No pull request was opened — " + "; ".join(others or ["no services"]))[:1000],
        "results": [],
    }


def _root(error: BaseException) -> BaseException:
    """The error at the bottom of Temporal's wrapping (ActivityError ->
    ApplicationError -> ...), whose message says what actually went wrong."""
    while getattr(error, "cause", None) is not None:
        error = error.cause
    return error
