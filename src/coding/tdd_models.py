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

from pydantic import BaseModel, Field, field_validator


class FileChange(BaseModel):
    file_path: str | None = None
    function_or_symbol: str
    current_behavior: str | None = None
    change_description: str
    implementation_notes: str
    pseudocode_sketch: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    reasoning: str
    change_type: Literal["create", "modify", "delete", "reference"] = Field(
        default="modify",
        description=(
            "What happens to the FILE: create = file does not exist yet; modify = "
            "existing file (including new code added to it); delete = file removed; "
            "reference = file is only read by the new code and is NOT edited."
        ),
    )
    code_check: Literal["VERIFIED", "CORRECTED", "NEW", "UNVERIFIED"] | None = Field(
        default=None,
        description=(
            "First word of the change's 'Code check:' line (VERIFIED, CORRECTED, NEW "
            "or UNVERIFIED); null when the document has no such line."
        ),
    )
    code_check_note: str | None = Field(
        default=None,
        description="Any text after the first word of the 'Code check:' line; null if none.",
    )
    has_real_source: bool = False

    @field_validator("change_type", mode="before")
    @classmethod
    def _first_word_of_change_type(cls, value):
        """'reference (read, not changed)' or 'Modify' still map to the enum —
        one oddly written entry must not fail the whole document."""
        if isinstance(value, str) and value.strip():
            return value.strip().split()[0].strip("()").lower()
        return value

    @field_validator("code_check", mode="before")
    @classmethod
    def _first_word_of_code_check(cls, value):
        """'VERIFIED — Existing test file ...' keeps only 'VERIFIED'; an
        unrecognised value becomes null (= no stamp) instead of an error."""
        if not isinstance(value, str) or not value.strip():
            return None
        word = value.strip().split()[0].strip("—-:.,").upper()
        return word if word in {"VERIFIED", "CORRECTED", "NEW", "UNVERIFIED"} else None


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
