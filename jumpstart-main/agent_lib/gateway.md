# AI Gateway Client

**Module:** `agent_lib.gateway.ai`

Routes all LLM, embedding, image, and OCR requests through the **Aetherion AI Gateway**.

---

## Configuration

Set these environment variables before creating a gateway_client:

| Env var | Description |
|---|---|
| `AI_GATEWAY_URL` | Base URL of the gateway |
| `AGENTS_GATEWAY_KEY` | Bearer token for authentication |

Read them and pass explicitly when creating the gateway_client:

```python
import os
from agent_lib.gateway.ai import AiGatewayClient

gateway_client = AiGatewayClient(
    gateway_key=os.environ["AGENTS_GATEWAY_KEY"],
    gateway_url=os.environ["AI_GATEWAY_URL"],
)
```

If the parameters are omitted, the gateway_client reads `AI_GATEWAY_URL` and `AGENTS_GATEWAY_KEY` from the environment automatically.

---

## Chat completions

### `chat(provider, model_name, prompt, *, system_prompt, max_tokens) → dict`

Single-turn chat. Returns the full message dict `{"role": "assistant", "content": "..."}`.

```python
import os
from agent_lib.gateway.ai import AiGatewayClient

gateway_client = AiGatewayClient(
    gateway_key=os.environ["AGENTS_GATEWAY_KEY"],
    gateway_url=os.environ["AI_GATEWAY_URL"],
)

reply = await gateway_client.chat(
    provider="anthropic",
    model_name="claude-sonnet-4-6",
    prompt="Explain async/await in Python.",
    system_prompt="You are a concise Python tutor.",
)
print(reply["content"])
```

---

### `chat_with_messages(provider, model_name, messages, *, max_tokens) → dict`

Multi-turn chat from a pre-built message list. Returns the reply message dict.

```python
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user",   "content": "What is the capital of France?"},
    {"role": "assistant", "content": "Paris."},
    {"role": "user",   "content": "What is its population?"},
]

reply = await gateway_client.chat_with_messages(
    provider="openai",
    model_name="gpt-4o",
    messages=messages,
)
print(reply["content"])
```

---

## Streaming

### `stream(provider, model_name, prompt, *, system_prompt) → AsyncIterator[str]`

Stream text delta chunks for a single-turn prompt.

```python
async for chunk in gateway_client.stream(
    provider="anthropic",
    model_name="claude-sonnet-4-6",
    prompt="Write a haiku about the ocean.",
):
    print(chunk, end="", flush=True)
```

---

### `stream_with_messages(provider, model_name, messages) → AsyncIterator[str]`

Stream text delta chunks from a pre-built message list.

```python
messages = [
    {"role": "user", "content": "Tell me a short story."},
]

async for chunk in gateway_client.stream_with_messages(
    provider="openai",
    model_name="gpt-4o",
    messages=messages,
):
    print(chunk, end="", flush=True)
```

---

## Vision

### `describe_image(provider, model_name, prompt, *, image_url, image_b64, media_type, system_prompt) → str`

Send a vision request with an image and a text prompt. Returns the reply text. Supply exactly one of `image_url` or `image_b64`.

```python
# From a URL
description = await gateway_client.describe_image(
    provider="openai",
    model_name="gpt-4o",
    prompt="Describe what is in this image.",
    image_url="https://example.com/photo.jpg",
)

# From raw bytes (e.g. a file on disk)
import base64

with open("diagram.png", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

description = await gateway_client.describe_image(
    provider="openai",
    model_name="gpt-4o",
    prompt="List all text visible in this diagram.",
    image_b64=b64,
    media_type="image/png",
)

print(description)
```

---

## Embeddings

### `embed(provider, model_name, text) → list[float]`

Return the embedding vector for a string or the first item in a list.

```python
vector = await gateway_client.embed(
    provider="openai",
    model_name="text-embedding-3-small",
    text="The quick brown fox jumps over the lazy dog.",
)
print(f"Dimensions: {len(vector)}")
```

---

## Image generation

### `generate_image(provider, model_name, prompt, *, size, n) → bytes`

Generate an image and return its raw bytes. Handles both base64 and URL responses transparently.

```python
image_bytes = await gateway_client.generate_image(
    provider="openai",
    model_name="gpt-image-1",
    prompt="A futuristic city skyline at sunset, digital art.",
    size="1024x1024",
)

with open("output.png", "wb") as f:
    f.write(image_bytes)
```

---

## OCR

### `read_image_text(image_url) → str`

Extract text from an image at the given URL via the gateway's OCR endpoint.

```python
text = await gateway_client.read_image_text(
    image_url="https://example.com/invoice.png"
)
print(text)
```

---

## Models & providers

### `get_models(provider) → dict[str, list[str]]`

Return available models keyed by provider name. Pass `provider` to filter to a single one. Returns `{}` on failure.

```python
all_models = await gateway_client.get_models()
# {"openai": ["gpt-4o", ...], "anthropic": ["claude-sonnet-4-6", ...]}

anthropic_models = await gateway_client.get_models(provider="anthropic")
```

### `get_providers() → list[str]`

Return the list of available provider names.

```python
providers = await gateway_client.get_providers()
# ["openai", "anthropic", "gemini", ...]
```

---

## Lifecycle

`AiGatewayClient` owns an `httpx.AsyncClient` and should be closed when your application shuts down. Use the async context manager for scoped usage or call `close()` explicitly.

```python
import os
from agent_lib.gateway.ai import AiGatewayClient

async with AiGatewayClient(
    gateway_key=os.environ["AGENTS_GATEWAY_KEY"],
    gateway_url=os.environ["AI_GATEWAY_URL"],
) as gateway_client:
    reply = await gateway_client.chat("openai", "gpt-4o", "Hello!")
```
