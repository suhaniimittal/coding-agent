"""Tool entrypoint for coding_agent's whole flow: a TDD PDF in, Pull Request
URLs out. No shared code/database with the planning project — the PDF is
the only handoff.
"""

from __future__ import annotations

import os

from aetherion_sdk import tool
from common_lib.storage.storage_client import RetrievalMode, storage

from src.coding.coding_flow import run_coding_agent
from src.coding.pdf_extract import extract_text
from src.coding.tdd_extraction import extract_tdd


@tool()
async def run_coding_agent_from_pdf(
    team_id: str, file_key: str, base_branch_by_service: dict | None = None
) -> dict:
    """Retrieves the uploaded TDD PDF from storage, extracts its text,
    recovers the structured TechnicalDesignDoc from that text (one LLM
    call), then runs the coding pipeline: validates each proposed change
    against a real clone (free, no LLM call), patches/commits/pushes a
    branch per affected service (one LLM call per service), verifies with
    the repo's own real test command (no repair/retry LLM call — a failure
    is reported honestly, not auto-fixed), and opens a PR per service —
    never auto-merged. Returns pull request URLs plus every change that
    couldn't be safely auto-applied, with why."""
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

    pdf_text = extract_text(pdf_bytes)
    tdd = await extract_tdd(pdf_text)
    result = await run_coding_agent(tdd, base_branch_by_service=base_branch_by_service)

    return {
        "tdd_title": tdd.title,
        "pull_requests": [
            {
                "service": pr["service"],
                "pr_url": pr["pr_url"],
                "tests_ran": pr["verification"].ran,
                "tests_passed": pr["verification"].passed,
                "verification_detail": pr["verification"].detail,
            }
            for pr in result["pull_requests"]
        ],
        "skipped": [
            {"service": s.service, "file_path": s.change.file_path, "reason": s.reason}
            for s in result["skipped"]
        ],
    }
