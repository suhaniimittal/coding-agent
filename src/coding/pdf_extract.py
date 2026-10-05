"""Raw text extraction from an uploaded TDD PDF — the first step of the
coding_agent's own flow, before any structure is recovered from it. No LLM
call here, just reading the PDF's own text layer.

pypdf specifically (not PyMuPDF): this project only ever READS a PDF, never
renders one, so the PyMuPDF licensing concern that applies to the planning
side's PDF generation doesn't apply here at all — pypdf is pure Python,
permissively licensed, and text extraction is all this needs.
"""

from __future__ import annotations

import io


class PdfExtractError(Exception):
    """Raised when the PDF can't be read or has no extractable text at all —
    callers must not proceed to the structured-extraction LLM call with
    nothing to give it."""


def extract_text(pdf_bytes: bytes) -> str:
    """Concatenates every page's text, in order. The input PDF is expected
    to be planning_agent's own rendered TDD (a clean, section-numbered plain
    -text document turned into a PDF) — no table/layout reconstruction is
    needed, a straightforward per-page text extraction is enough."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except PdfReadError as e:
        raise PdfExtractError(f"could not read PDF: {e}") from e

    pages_text = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages_text).strip()
    if not text:
        raise PdfExtractError("PDF has no extractable text (scanned image with no text layer?)")
    return text
