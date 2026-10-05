# File Utilities

**Module:** `agent_lib.utils.file_utils`

`fetch_reference_data(reference)` fetches a document referenced by a `Reference` object and extracts its text content, automatically choosing the right extractor based on file extension.

## Quick Example

```python
from agent_lib.utils.file_utils import fetch_reference_data
from common_lib.models.embeddings import Reference

# Reference points to a file in storage
reference = Reference(
    agent="test",
    customer="customer1",
    filename="document.pdf",
    content_type="application/pdf",
)
text = fetch_reference_data(reference)

# Or pass bytes directly
reference = Reference(
    agent="test",
    filename="document.pdf",
    file_content=pdf_bytes,
)
text = fetch_reference_data(reference)
```

## Public API

| Function | Description |
|----------|-------------|
| `fetch_reference_data(reference)` | Fetch the referenced document and return its extracted text as a `str`. |

## Supported File Types

| Extension | Extractor |
|-----------|-----------|
| `.pdf` | PyMuPDF |
| `.docx` | python-docx |
| `.csv` | csv module |
| `.xlsx`, `.xls` | openpyxl |
| `.html`, `.htm` | BeautifulSoup |
| `.md` | markdown parser |
