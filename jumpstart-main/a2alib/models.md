# A2A Models

**Module:** `a2a_lib.models`

All A2A models are Pydantic v2 models. Use them to type your service handlers, validate incoming JSON, and serialise outbound responses.

## `A2ATokenClaims`

The JWT payload used to authenticate agent-to-agent calls.

| Field | Description |
|-------|-------------|
| `agent_id` | The calling agent's id. |
| `customer_id` | The customer/tenant context. |
| `permissions` | Granted permissions for this call. |

## `A2ARequest`

An invocation request from one agent to another.

| Field | Description |
|-------|-------------|
| `correlation_id` | Trace id that flows end-to-end across the call. |
| `idempotency_key` | Caller-supplied key used to de-duplicate retries. |
| `agent_id` | The target agent. |
| `skill` | The skill (capability) to invoke on the agent. |
| `input_data` | The input payload, shape defined by the skill. |
| `callback_url` | Optional URL to POST async results to. |
| `ttl_seconds` | How long the request is valid. |
| `metadata` | Free-form metadata dict. |

## `A2AResponse`

The response returned when invoking an agent.

| Field | Description |
|-------|-------------|
| `correlation_id` | Mirrors the request `correlation_id`. |
| `status` | One of `accepted`, `processing`, `completed`, `failed`. |
| `workflow_id` | The agent run's workflow id. |
| `sse_endpoint` | Optional SSE stream URL for live updates. |
| `status_endpoint` | URL to poll for status. |
| `result` | Result payload when `status == "completed"`. |
| `message` | Human-readable message. |

## `AgentCard`

Public metadata advertised by an agent at `/.well-known/agent.json`. Other agents read this to discover capabilities and skills.

| Field | Description |
|-------|-------------|
| `agent_name` | Display name. |
| `version` | Version string. |
| `description` | One-line summary. |
| `capabilities` | List of capabilities the agent supports. |
| `skills` | List of callable skills. |
| `triggers` | Available triggers (e.g., events the agent responds to). |
| `metadata` | Free-form metadata. |

## `A2AError`

Returned when an A2A call fails.

| Field | Description |
|-------|-------------|
| `error_code` | Machine-readable error code. |
| `error_message` | Human-readable message. |
| `correlation_id` | Trace id from the failing request. |
| `retryable` | Whether the caller should retry. |
| `timestamp` | When the error occurred. |

## `A2AUploadRequest`

Body for file-upload endpoints. Carries the file inline as base64.

| Field | Description |
|-------|-------------|
| `filename` | Original file name. |
| `content` | Base64-encoded file bytes. Supports `data:<mime>;base64,...` data URIs. |
| `content_type` | MIME type (optional; inferred for data URIs). |
