"""Trimmed TechnicalDesignDoc schema — only the fields the coding pipeline
itself actually reads (validation.py, patcher.py, coding_flow.py). The
planning-side TDD has many more fields (architecture diagrams, sequence
steps, testing plan, risks, ...) that only matter for a human reading the
document, never for turning it into code changes — deliberately left out
here rather than copied wholesale, since this project has zero dependency
on the planning project and shouldn't carry schema it never uses.

This is the schema `tdd_extraction.py`'s LLM call is asked to fill in from
the PDF's extracted text, and the shape `coding_flow.py` operates on.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FileChange(BaseModel):
    file_path: str | None = None
    function_or_symbol: str
    current_behavior: str | None = None
    change_description: str
    implementation_notes: str
    pseudocode_sketch: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    reasoning: str
    change_type: Literal["create", "modify", "delete"] = "modify"
    has_real_source: bool = False


class ServicePlan(BaseModel):
    service: str
    changes: list[FileChange] = Field(default_factory=list)


class TechnicalDesignDoc(BaseModel):
    title: str = Field(
        description=(
            "A short, specific title for this issue/fix — never a generic or "
            "schema-name placeholder."
        )
    )
    issue_summary: str
    services: list[ServicePlan]
