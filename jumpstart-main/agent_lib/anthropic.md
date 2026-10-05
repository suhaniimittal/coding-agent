# Anthropic LLM

**Module:** `agent_lib.llm.anthropic`

**`Anthropic(config)`** — Initializes Anthropic LLM wrapper.

| Parameter | Type | Description |
|-----------|------|-------------|
| `config` | AnthropicConfig | Configuration object |
| → `model_id` | str | Model name (e.g., "claude-3-opus-20240229") |
| → `anthropic_api_key` | str | Anthropic API key |

**Example:**

```python
from agent_lib.llm.anthropic import Anthropic
from agent_lib.llm.models import AnthropicConfig
from common_lib.models.embeddings import Reference

config = AnthropicConfig(
    model_id="claude-3-opus-20240229",
    anthropic_api_key="sk-ant-..."
)
llm = Anthropic(config)

for chunk in llm.call_llm(
    Reference(agent="test", businesskey="doc1"),
    "Analyze this document"
):
    print(chunk.content, end="")
```
