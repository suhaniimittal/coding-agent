# Agent-to-Agent Library

The `a2a_lib` package implements the Agent-to-Agent (A2A) communication protocol used by Aetherion services. It provides typed request/response models, JWT token claims, agent metadata (`AgentCard`), and helpers for base64-encoded file uploads — everything you need to call one agent from another over HTTP without inventing payload shapes.

## Install & Import

The package ships with the Aetherion SDK install. Import models and helpers directly:

```python
from a2a_lib.models import (
    A2ATokenClaims,
    A2ARequest,
    A2AResponse,
    AgentCard,
    A2AError,
    A2AUploadRequest,
)
from a2a_lib.agent_card import build_agent_card_from_list_response
from a2a_lib.upload_helpers import (
    decode_base64_to_upload_file,
    parse_base64_upload_request,
)
```

## Documentation Contents

- **[Models](models.md)** — Pydantic request/response/error/agent-card models.
- **[Helpers](helpers.md)** — Build agent cards from list responses; decode base64 file uploads.
