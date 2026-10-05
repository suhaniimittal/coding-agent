# OpenAI LLM

**Module:** `agent_lib.llm.openai`

**`OpenAI(config)`** — Initializes OpenAI LLM wrapper.

| Parameter | Type | Description |
|-----------|------|-------------|
| `config` | OpenAIConfig | Configuration object |
| → `model_id` | str | Model name (e.g., "gpt-4") |
| → `api_key` | str | OpenAI API key |
| → `embedding_model` | str, optional | Embedding model name |

**`OpenAI.call_llm(references, prompt)`** — Makes an async streaming LLM call with reference data.

> **Note:** Automatically fetches reference data and appends to prompt

**`OpenAI.call_llm_sync(reference, prompt)`** — Makes a synchronous LLM call.

**Example:**

```python
from agent_lib.llm.openai import OpenAI
from agent_lib.llm.models import OpenAIConfig
from common_lib.models.embeddings import Reference

config = OpenAIConfig(model_id="gpt-4", api_key="sk-...")
llm = OpenAI(config)

# Streaming
async for chunk in llm.call_llm(
    [Reference(agent="test", filename="doc.pdf")],
    "What is in this document?"
):
    print(chunk.content, end="")

# Synchronous
response = llm.call_llm_sync(
    Reference(agent="test", filename="doc.pdf"),
    "Summarize this document"
)
print(response.content)
```
