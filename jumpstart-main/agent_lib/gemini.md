# Google Gemini LLM

**Module:** `agent_lib.llm.gemini`

**`Gemini(config)`** — Initializes Google Gemini LLM wrapper.

| Parameter | Type | Description |
|-----------|------|-------------|
| `config` | GeminiConfig | Configuration object |
| → `model_id` | str | Gemini model ID (e.g., "gemini-pro") |
| → `google_api_key` | str | Google API key |

**`Gemini.generate_content(prompt)`** — Exposes the raw GenerativeModel.generate_content method.

**Example:**

```python
from agent_lib.llm.gemini import Gemini
from agent_lib.llm.models import GeminiConfig
from common_lib.models.embeddings import Reference

config = GeminiConfig(
    model_id="gemini-pro",
    google_api_key="AIza..."
)
llm = Gemini(config)

for chunk in llm.call_llm(
    Reference(agent="test", businesskey="doc1"),
    "Generate a summary"
):
    print(chunk.content, end="")
```
