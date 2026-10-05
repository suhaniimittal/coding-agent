from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from src.coding.pdf_extract import PdfExtractError, extract_text


def _blank_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_garbage_bytes_raises_pdf_extract_error():
    with pytest.raises(PdfExtractError):
        extract_text(b"this is not a pdf")


def test_blank_page_with_no_text_raises_pdf_extract_error():
    with pytest.raises(PdfExtractError, match="no extractable text"):
        extract_text(_blank_pdf_bytes())
