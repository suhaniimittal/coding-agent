# LLM Factory

**Module:** `agent_lib.llm.llm_factory`

`LlmFactory` picks the right provider for a given `model_id` and returns a ready-to-use LLM client.

## Quick Example

```python
from agent_lib.llm.llm_factory import LlmFactory
from agent_lib.llm.models import OpenAIConfig

config = OpenAIConfig(
    model_id="gpt-4",
    api_key="sk-...",
    embedding_model="text-embedding-ada-002",
)
llm = LlmFactory.get_llm_connection(config)
```

## Public API

| Method | Description |
|--------|-------------|
| `LlmFactory.detect_provider(model_id)` | Return the provider name (`"openai"`, `"anthropic"`, `"gemini"`, `"bedrock"`) for the given model id. |
| `LlmFactory.build_config(model_id, config_values)` | Build the right `LlmConfig` subclass for `model_id` from a lookup of values. |
| `LlmFactory.get_llm_connection(config, agent_name=None, guardrails_config=None)` | Return an `LlmCalls` client for the given config. Raises `ValueError` for unknown config types. |

## Model Id → Provider Mapping

| Prefix | Provider |
|--------|----------|
| `gpt-`, `o1-`, `o3-`, `o4-`, `chatgpt-` | OpenAI |
| `claude-` | Anthropic |
| `gemini-` | Gemini |
| `amazon.`, `anthropic.`, `meta.`, `us.`, `eu.`, `ap.` | Bedrock |
