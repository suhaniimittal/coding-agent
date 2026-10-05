# Payload Codec

**Module:** `common_lib.execution.payload_codec`

`RedisCodec` is a payload codec that offloads large workflow payloads to Redis. The workflow engine has a strict per-message size limit (~500KB); the codec writes oversized payloads to Redis and replaces the in-message payload with a small reference.

Use this codec whenever an agent or tool may produce or consume large inputs/outputs (file bytes, big JSON blobs, embeddings batches).

## Quick Example

```python
from aetherion_sdk import AetherionRuntime
from common_lib.execution.payload_codec import RedisCodec

runtime = AetherionRuntime()
client = await runtime.connect()

# Attach the codec for this client/process
client.data_converter = client.data_converter.with_payload_codec(RedisCodec())
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | `localhost` | Redis hostname. |
| `REDIS_PORT` | `6379` | Redis port. |
| `REDIS_DB` | `0` | Redis logical database. |
| `REDIS_PASSWORD` | _none_ | Redis password (optional). |

## When to Use

- Tools that read or return file contents larger than a few hundred KB.
- Agents that pass big intermediate state between tool calls.
- Embedding pipelines that ship vectors and chunks together.

If your payloads are reliably small (< 100KB), you don't need this codec.
