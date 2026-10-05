# A2A Helpers

Helper functions for working with agent cards and base64-encoded file uploads.

## `build_agent_card_from_list_response`

**Module:** `a2a_lib.agent_card`

Transform a platform "list agents" API response into an `AgentCard` suitable for serving from `/.well-known/agent.json`.

```python
from a2a_lib.agent_card import build_agent_card_from_list_response

agent_card = build_agent_card_from_list_response(
    agent_data=list_response["agents"][0],
    base_url="https://my-agent.example.com",
)
if agent_card is None:
    raise RuntimeError("Agent not found in list response")
```

| Parameter | Description |
|-----------|-------------|
| `agent_data` | Dict pulled from the platform agent-list response. |
| `base_url` | Public base URL the agent is served from. Used to build absolute skill URLs. Defaults to `""`. |

**Returns:** `AgentCard | None` — `None` if the input cannot be transformed.

## `decode_base64_to_upload_file`

**Module:** `a2a_lib.upload_helpers`

Decode a base64-encoded body into a FastAPI `UploadFile`. Supports plain base64 and `data:` URIs (the MIME type from the URI overrides `content_type` if both are provided).

```python
from a2a_lib.upload_helpers import decode_base64_to_upload_file

upload_file = decode_base64_to_upload_file(
    base64_content="iVBORw0KGgoAAAANS...",  # or "data:image/png;base64,iVBORw0..."
    filename="screenshot.png",
    content_type="image/png",
)
```

| Parameter | Description |
|-----------|-------------|
| `base64_content` | Base64 string. May be a `data:<mime>;base64,...` URI. |
| `filename` | Filename to assign to the `UploadFile`. |
| `content_type` | Optional MIME type. Ignored if `base64_content` is a data URI. |

**Raises:** `ValueError` if the base64 cannot be decoded.

## `parse_base64_upload_request`

**Module:** `a2a_lib.upload_helpers`

Parse a FastAPI `Request` body as an `A2AUploadRequest` and return a `UploadFile`. Use this in FastAPI endpoints that accept JSON-wrapped uploads.

```python
from fastapi import APIRouter, Request, UploadFile
from a2a_lib.upload_helpers import parse_base64_upload_request

router = APIRouter()

@router.post("/upload")
async def upload(request: Request) -> dict:
    upload_file: UploadFile = await parse_base64_upload_request(request)
    contents = await upload_file.read()
    return {"filename": upload_file.filename, "size": len(contents)}
```

**Raises:**

- `HTTPException(400)` if the body is not valid JSON.
- `HTTPException(400)` if the body fails `A2AUploadRequest` validation.
- `HTTPException(400)` if base64 decoding fails.
