# AWS Bedrock LLM

**Module:** `agent_lib.llm.bedrock`

**`Bedrock(config)`** — Initializes AWS Bedrock LLM wrapper.

| Parameter | Type | Description |
|-----------|------|-------------|
| `config` | BedrockConfig | Configuration object |
| → `model_id` | str | Bedrock model ID |
| → `region_name` | str | AWS region |
| → `aws_access_key_id` | str | AWS access key |
| → `aws_secret_access_key` | str | AWS secret key |

**Example:**

```python
from agent_lib.llm.bedrock import Bedrock
from agent_lib.llm.models import BedrockConfig
from common_lib.models.embeddings import Reference

config = BedrockConfig(
    model_id="anthropic.claude-v2",
    region_name="us-east-1",
    aws_access_key_id="AKIA...",
    aws_secret_access_key="..."
)
llm = Bedrock(config)

for chunk in llm.call_llm(
    Reference(agent="test", businesskey="doc1"),
    "Process this data"
):
    print(chunk.content, end="")
```
