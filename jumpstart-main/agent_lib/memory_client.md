# Memory Client

**Module:** `agent_lib.memory.mem0_client`

The `AetherionMemory` class provides Mem0 memory client integration.

**`AetherionMemory(timeout=30)`** — Initializes Mem0 memory client.

| Environment Variable | Description |
|---------------------|-------------|
| `VECTOR_STORE` | Vector store provider |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` | LLM configuration |
| `EMBEDDER`, `EMBEDDING_MODEL` | Embedder configuration |
| `GRAPH_STORE`, `GRAPH_STORE_URI`, `GRAPH_STORE_USER`, `GRAPH_STORE_PASSWORD` | Graph store configuration |

**Example:**

```python
from agent_lib.memory.mem0_client import memory

# Synchronous
memory.client.add(
    user_id="user1",
    messages=[{"role": "user", "content": "I like hiking"}],
    agent_id="agent1",
    run_id="run1"
)

# Async
async_client = await memory.init_async_client()
```
