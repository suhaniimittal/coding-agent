# Decorators & Executors Reference

This reference covers the core decorators, executors, and runtime helpers used to build Aetherion agents and tools.

## Table of Contents
- [Decorators](#decorators)
  - [@agent](#agent)
  - [@tool](#tool)
  - [@signal](#signal)
  - [@query](#query)
  - [@register](#register)
- [Executors](#executors)
  - [toolExecutor](#toolexecutor)
  - [agentExecutor](#agentexecutor)
  - [agent_handle (Workflow Context)](#agent_handle-workflow-context)
- [WorkflowExecution Handle](#workflowexecution-handle)
- [Tool Execution Options](#tool-execution-options)

## Decorators

Use these decorators to register your Python functions as Aetherion components.

### `@agent`

Registers a function as an Agent (a long-running workflow).

**Import:**
```python
from aetherion_sdk import agent
```

**Signature:**
```python
def agent(
    *,
    name: Optional[str] = None,
    task_queue: Optional[str] = None,
    run_timeout: Optional[timedelta] = None,
)
```

**Parameters:**
- `name` (str, optional): Custom name for the agent. Defaults to the function name.
- `task_queue` (str, optional): Task queue to listen on. Defaults to `AETHERION_AGENT_TASK_QUEUE`.
- `run_timeout` (timedelta, optional): Maximum execution time for the agent.

**Example:**
```python
from datetime import timedelta
from aetherion_sdk import agent

@agent(name="PaymentAgent", run_timeout=timedelta(hours=1))
async def payment_flow(amount: float):
    ...
```

---

### `@tool`

Registers a function as a Tool (an activity that performs work).

**Import:**
```python
from aetherion_sdk import tool
```

**Signature:**
```python
def tool(
    *,
    name: Optional[str] = None,
    start_to_close_timeout: Optional[timedelta] = timedelta(seconds=30),
    schedule_to_close_timeout: Optional[timedelta] = None,
    heartbeat_timeout: Optional[timedelta] = None,
    retry_policy: Optional[RetryPolicy] = None,
    task_queue: Optional[str] = None,
)
```

**Parameters:**
- `name` (str, optional): Custom name. Defaults to the function name.
- `start_to_close_timeout` (timedelta): Max time for a single execution attempt. Default 30s.
- `schedule_to_close_timeout` (timedelta, optional): Max total time from scheduling to completion.
- `heartbeat_timeout` (timedelta, optional): Max interval between heartbeats for long-running tools.
- `retry_policy` (RetryPolicy, optional): Custom retry logic (max attempts, backoff).
- `task_queue` (str, optional): Task queue override. Defaults to `AETHERION_TOOL_TASK_QUEUE`.

**Example:**
```python
from datetime import timedelta
from aetherion_sdk import tool

@tool(start_to_close_timeout=timedelta(minutes=5))
def long_running_tool(data: str):
    ...
```

---

### `@signal`

Marks a function as a signal handler inside an agent. Signals are asynchronous notifications sent to a running agent.

**Import:**
```python
from aetherion_sdk import signal
```

**Signature:**
```python
def signal(name: Optional[str] = None)
```

**Example:**
```python
from aetherion_sdk import agent, signal

@agent()
async def OrderAgent(order_id: str):
    state = {"cancelled": False}

    @signal()
    async def cancel():
        state["cancelled"] = True

    # workflow body uses `state["cancelled"]`
```

---

### `@query`

Marks a function as a query handler inside an agent. Queries are synchronous, read-only views into an agent's state.

**Import:**
```python
from aetherion_sdk import query
```

**Signature:**
```python
def query(name: Optional[str] = None)
```

**Example:**
```python
from aetherion_sdk import agent, query

@agent()
async def OrderAgent(order_id: str):
    state = {"status": "pending"}

    @query()
    def status() -> str:
        return state["status"]
```

---

### `@register`

Registers a tool with explicit metadata for runtime discovery.

**Import:**
```python
from aetherion_sdk import register
```

**Signature:**
```python
def register(
    *,
    name: Optional[str] = None,
    description: str,
    input_schema: List[Dict[str, Any]],
    output_schema: List[Dict[str, Any]],
    type: Optional[str] = None,
    start_to_close_timeout: Optional[int] = None,
    schedule_to_close_timeout: Optional[timedelta] = None,
    heartbeat_timeout: Optional[timedelta] = None,
)
```

**Parameters:**
- `description` (str, required): Human-readable description of the tool.
- `input_schema` / `output_schema` (List[Dict]): JSON-serialisable schemas.
- Other parameters mirror `@tool`.

---

## Executors

Executors let you invoke agents and tools dynamically by name.

### `toolExecutor`

Used inside an agent to call a tool.

**Import:**
```python
from aetherion_sdk import toolExecutor
```

**Usage:**
```python
result = await toolExecutor.execute(
    "tool_name",     # Name registered via @tool
    *args,           # Positional arguments for the tool
    **options,       # Override timeouts, retry, task_queue, etc.
)
```

**Allowed options:**
- `start_to_close_timeout`
- `schedule_to_close_timeout`
- `heartbeat_timeout`
- `retry_policy`
- `task_queue`

**Example:**
```python
from datetime import timedelta
from aetherion_sdk import toolExecutor

result = await toolExecutor.execute(
    "send_email",
    "user@example.com",
    "Hello!",
    start_to_close_timeout=timedelta(seconds=10),
)
```

---

### `agentExecutor`

Used to call another agent as a child workflow.

**Import:**
```python
from aetherion_sdk import agentExecutor
```

**Usage:**
```python
result = await agentExecutor.execute(
    "AgentName",       # Name registered via @agent
    *args,
    **options,
)
```

**Allowed options:**
- `execution_timeout`
- `run_timeout`
- `task_timeout`
- `retry_policy`
- `task_queue`
- `workflow_id`

**Parallel execution:**
```python
results = await agentExecutor.run_parallel({
    "task1": ("AgentA", arg1),
    "task2": ("AgentB", arg2),
})
# results["task1"] -> result from AgentA
# results["task2"] -> result from AgentB
```

**Awaiting multiple in-flight calls:**
```python
results = await agentExecutor.gather(
    agentExecutor.execute("AgentA", arg1),
    agentExecutor.execute("AgentB", arg2),
)
```

---

### `agent_handle` (Workflow Context)

Used inside an agent to interact with the workflow runtime without importing low-level workflow primitives.

**Import:**
```python
from aetherion_sdk import agent_handle
```

**Key methods:**
- `await agent_handle.wait_for(seconds)` — sleep for the given duration. Accepts `int`, `float`, or `timedelta`.
- `await agent_handle.wait_for(condition_fn)` — pause until `condition_fn()` returns `True`.
- `agent_handle.now()` — current workflow time (deterministic).
- `agent_handle.get_child(child_id)` — return a handle to a child workflow by ID.

**Example:**
```python
from datetime import timedelta
from aetherion_sdk import agent_handle

await agent_handle.wait_for(timedelta(seconds=60))
```

---

## WorkflowExecution Handle

`AetherionRuntime.start_workflow()` and `start_agent_by_name()` return a `WorkflowExecution` handle.

| Member | Description |
|--------|-------------|
| `.id` | The workflow ID for this run. |
| `.agent` | The `AgentDefinition` for the running agent. |
| `await .result()` | Wait for and return the agent's final result. |
| `await .signal(name, *args, **kwargs)` | Send a signal to the running agent. |
| `await .query(name, *args, **kwargs)` | Run a query against the agent's state. |
| `await .cancel()` | Cancel the running agent. |

**Example:**
```python
from aetherion_sdk import AetherionRuntime

runtime = AetherionRuntime()
exec = await runtime.start_agent_by_name("OrderAgent", "order-42")

# Push a signal while it's running
await exec.signal("cancel")

# Read state mid-flight
status = await exec.query("status")

# Wait for it to finish
result = await exec.result()
await runtime.close()
```

---

## Tool Execution Options

Each tool has a default `start_to_close_timeout` of 30 seconds. Override per call by passing options to `toolExecutor.execute(...)`.

> ⚠️ **Important:** If a tool will take longer than the default 30 seconds, set `start_to_close_timeout` explicitly.

```python
from datetime import timedelta
from aetherion_sdk import toolExecutor

result = await toolExecutor.execute(
    "analyze_text",
    "Some long text here",
    start_to_close_timeout=timedelta(seconds=120),
)
```

**Inside an agent:**

```python
from datetime import timedelta
from typing import Dict, Any
from aetherion_sdk import agent, toolExecutor

@agent()
async def HelloAgent(payload: Dict[str, Any]):
    user_input = payload.get("input")

    analysis = await toolExecutor.execute(
        "analyze_text",
        user_input,
        start_to_close_timeout=timedelta(seconds=5),
    )
    return analysis
```

**Supported options for tools:**

| Option | Description |
|--------|-------------|
| `start_to_close_timeout` | Max allowed execution duration once the tool starts. |
| `schedule_to_close_timeout` | Max total time from scheduling to completion (queue + run). |
| `heartbeat_timeout` | Max interval between heartbeats for a running tool. |
| `retry_policy` | Rules for automatic retries (attempts, intervals, backoff). |
| `task_queue` | Name of the queue the tool is dispatched to. |

---

[Next Step: Publish](publish.md)
