# Execution Helpers

**Module:** `common_lib.execution`

Functions and types for tracking the lifecycle of agent runs and activity runs. Use these from inside tools or from supporting services to record `pending → started → completed / failed` transitions in the database.

## Quick Example

```python
from common_lib.execution.agent_execution import (
    create_agent_run,
    mark_agent_run_started,
    mark_agent_run_completed,
)

agent_run_id = create_agent_run(
    agent_name="HelloAgent",
    workflow_id="hello-001",
    input_payload={"input": "world"},
    status="PENDING",
)

mark_agent_run_started(agent_run_id)

# ... do work ...

mark_agent_run_completed(
    agent_run_id,
    status="COMPLETED",
    result={"greeting": "Hello, world!"},
)
```

## `ExecutionMode`

`common_lib.models.temporal.ExecutionMode` describes how a workflow should be dispatched.

| Value | Behaviour |
|-------|-----------|
| `START_AND_WAIT` | Start a workflow and wait for the result. |
| `START_AND_SIGNAL` | Start a workflow and signal it after start. |
| `SIGNAL_EXISTING` | Signal an already-running workflow. |
| `START_OR_SIGNAL` | Start the workflow if not running, otherwise signal the existing one. |

## `ExecutorClient`

`common_lib.execution.executor_client.ExecutorClient` orchestrates workflow execution against the platform's workflow engine. Use it for advanced cases where you need explicit control over execution mode (signals to running agents, idempotent re-entry, etc.).

## Agent Run Lifecycle

| Function | Description |
|----------|-------------|
| `create_agent_run(agent_id=None, workflow_id=None, schedule_id=None, agent_name=None, status="PENDING", status_details=None, input_payload=None, created_by="system", updated_by="system")` | Insert an `AgentRun` row; returns the run id. |
| `mark_agent_run_started(agent_run_id)` | Transition to `RUNNING`. |
| `mark_agent_run_completed(agent_run_id, status, status_details=None, result=None)` | Transition to `COMPLETED` (or terminal status) with optional result. |
| `update_agent_run_status(agent_id, agent_run_id, status, status_details=None)` | Generic status update. |
| `set_agent_run_counters(agent_run_id, total_items, success_items, failed_items)` | Update batch counters on a run. |
| `get_agent_run_status(agent_run_id, db_session)` | Look up a run by id. |
| `get_all_agent_runs(db_session, page_size, page_number, query)` | Paginated list of runs. |
| `lookup_agent_id(name=None, task_queue=None)` | Resolve an agent id from its name or task queue. |

`schedule_id` on `AgentRun` lets you correlate a run with the schedule that triggered it — pass it through `create_agent_run` when invoking an agent on a recurring schedule.

## Activity Run Lifecycle

| Function | Description |
|----------|-------------|
| `create_activity_run(agent_run_id, activity_name, input_parameters, attempt)` | Create an `ActivityRun` linked to an `AgentRun`. |
| `mark_activity_run_completed(activity_run_id, output_results)` | Mark an activity as completed and persist its output. |
| `mark_activity_run_failed(activity_run_id, error_message)` | Mark an activity as failed. |

```python
from common_lib.execution.activity_execution import (
    create_activity_run,
    mark_activity_run_completed,
    mark_activity_run_failed,
)

activity_run_id = create_activity_run(
    agent_run_id=agent_run_id,
    activity_name="send_email",
    input_parameters={"to": "user@example.com"},
    attempt=1,
)

try:
    output = send_email(...)
    mark_activity_run_completed(activity_run_id, output_results=output)
except Exception as e:
    mark_activity_run_failed(activity_run_id, error_message=str(e))
    raise
```
