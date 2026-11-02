"""Tool entrypoint for coding_agent's whole flow: a TDD PDF in, Pull Request
URLs out. No shared code/database with the planning project — the PDF is
the only handoff.
"""

from __future__ import annotations

import logging
import os

from aetherion_sdk import tool
from common_lib.storage.storage_client import RetrievalMode, storage

from src.coding.coding_flow import run_coding_agent
from src.coding.pdf_extract import extract_text
from src.coding.tdd_extraction import extract_tdd

logger = logging.getLogger(__name__)


def _log_tdd(tdd) -> None:
    """What the LLM pulled out of the PDF, per service — so a service or file
    that is missing from the run can be traced to the extraction step."""
    logger.info("coding_agent: TDD %r has %d service(s)", tdd.title, len(tdd.services))
    for plan in tdd.services:
        paths = ", ".join(c.file_path or "<no path>" for c in plan.changes) or "none"
        logger.info(
            "coding_agent:   %s: %d change(s) -> %s", plan.service, len(plan.changes), paths
        )


def _log_outcome(result: dict) -> None:
    """One line per pushed branch and one WARNING per skipped change, with
    the reason. File paths and reasons only — never file contents."""
    skipped = result["skipped"]
    logger.info(
        "coding_agent: %d branch(es) pushed, %d change(s) skipped",
        len(result["branches"]),
        len(skipped),
    )
    for b in result["branches"]:
        logger.info(
            "coding_agent:   Branch pushed for %s — %s of %s change(s) applied: %s%s",
            b["service"],
            b.get("changes_applied", "?"),
            b.get("changes_total", "?"),
            b["branch_url"],
            f" (PR: {b['pr_url']})" if b.get("pr_url") else "",
        )
    for item in skipped:
        logger.warning(
            "coding_agent:   SKIPPED %s %s — %s", item.service, item.change.file_path, item.reason
        )
    for w in result.get("warnings", []):
        logger.warning("coding_agent:   WARNING %s: %s", w["service"], w["message"])
    for note in result.get("service_notes", []):
        logger.warning(
            "coding_agent:   NOTHING PUSHED for %s — %s", note["service"], note["reason"]
        )


@tool()
async def run_coding_agent_from_pdf(
    team_id: str,
    file_key: str,
    branch_name: str | None = None,
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
    _log_tdd(tdd)
    result = await run_coding_agent(tdd, branch_name=branch_name, doc_text=pdf_text)
    _log_outcome(result)

    return {
        "tdd_title": tdd.title,
        "branches": [
            {
                "service": b["service"],
                "repo": b["repo"],
                "branch": b["branch"],
                "branch_url": b["branch_url"],
                "pr_url": b["pr_url"],
                "tests_ran": b["verification"].ran,
                "tests_passed": b["verification"].passed,
                "verification_detail": b["verification"].detail,
            }
            for b in result["branches"]
        ],
        "skipped": [
            {"service": s.service, "file_path": s.change.file_path, "reason": s.reason}
            for s in result["skipped"]
        ],
        "service_notes": result.get("service_notes", []),
        "warnings": result.get("warnings", []),
    }
