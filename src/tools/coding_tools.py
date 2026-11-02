"""Tool entrypoint for coding_agent's whole flow: a TDD PDF in, pull request
links out (2026-11-02). No shared code or database with the planning
project: the PDF is the only handoff, its task read from the data
planning_agent keeps inside it (tdd_reader.py).

The whole run is this one activity, so the code it downloads and the model's
prompts never pass through Temporal's history: only the links, statuses and
short reasons below come out.
"""

from __future__ import annotations

import logging
import os

from aetherion_sdk import tool
from common_lib.storage.storage_client import RetrievalMode, storage

from src.coding import coding_flow, tdd_reader

logger = logging.getLogger(__name__)


@tool()
async def run_coding_agent_from_pdf(
    team_id: str,
    file_key: str,
    branch_name: str | None = None,
) -> dict:
    """Reads the uploaded TDD PDF from storage, then, per service it names:
    downloads the code at the TDD's commit, lets the coding model make the
    change (a few calls), checks it, commits it to a new branch and opens a
    pull request (a draft unless everything passed). Returns one result per
    service: its pull request, or why there is none."""
    storage.init_client()
    # Per the SDK's Storage Client docs the bucket is the TENANT_ID env var;
    # the payload's team_id is not reliably populated, so it's only a fallback.
    bucket_name = os.getenv("TENANT_ID") or team_id
    if not bucket_name:
        raise RuntimeError(
            "No storage bucket: TENANT_ID is not set in the environment and no team_id "
            "was passed, so the uploaded PDF can't be retrieved."
        )
    pdf_bytes = storage.retrieve(bucket_name, file_key, RetrievalMode.FULL_OBJECT)

    task, how = await tdd_reader.read_task(pdf_bytes)
    logger.info(
        "coding_agent: TDD %r (%s, %s) read from its %s; services: %s",
        task.title,
        task.ticket or "no ticket",
        task.status,
        how,
        ", ".join(f"{s.service}={s.repo}@{s.commit[:12]}" for s in task.services),
    )
    result = await coding_flow.run(task, branch_name=branch_name)
    for r in result["services"]:
        logger.info(
            "coding_agent:   %s: %s%s%s",
            r["service"],
            r["status"],
            f" {r['pr_url']}" if r.get("pr_url") else "",
            f" — {r['reason']}" if r.get("reason") else "",
        )
    return {
        "tdd_title": task.title,
        "ticket": task.ticket,
        "read_from": how,
        "run_id": result["run_id"],
        "services": result["services"],
    }
