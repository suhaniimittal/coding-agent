# Data Models

**Module:** `common_lib.data_models.models`

SQLAlchemy ORM models for agent and activity records. You generally interact with these indirectly through the [execution helpers](execution.md); read them directly when you need ad-hoc queries.

## `Agent`

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key. |
| `name` | str | Registered agent name. |
| `task_queues` | JSON | Queues this agent listens on. |
| `config` | JSON | Static configuration. |
| `runtime_config` | JSON | Runtime configuration overrides. |
| `is_active` | bool | Whether the agent is currently enabled. |
| `enabled` | bool | Feature-flag column for soft enable/disable. |
| `status` | str | Lifecycle status (e.g., `ACTIVE`, `INACTIVE`). |

## `AgentConfig`

Key/value store for agent configuration, with built-in encryption support for secret values.

## `Activity`

Definition records for activities (tools). Linked to one or more agents.

## `AgentRun`

A single execution of an agent.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key. |
| `workflow_id` | str | Engine-side workflow ID. |
| `agent_id` | UUID | FK → `Agent.id`. |
| `schedule_id` | UUID, nullable | FK → schedule row when the run was triggered by a recurring schedule. |
| `status` | str | `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`. |
| `status_details` | JSON | Free-form status payload. |
| `input_payload` | JSON | The input the agent was invoked with. |
| `result` | JSON | Final result. |
| `total_items` / `success_items` / `failed_items` | int | Optional batch counters. |
| `created_by`, `updated_by` | str | Audit fields. |

## `ActivityRun`

A single activity (tool) execution inside an `AgentRun`.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key. |
| `agent_run_id` | UUID | FK → `AgentRun.id`. |
| `activity_name` | str | Registered tool name. |
| `input_parameters` | JSON | Inputs passed to the activity. |
| `output_results` | JSON | Outputs returned. |
| `attempt` | int | Retry attempt number. |
| `error_message` | str, nullable | Set when the activity fails. |
| `status` | str | Activity status. |

## `WorkItem`

Generic per-item tracker for batch agents. Each row is linked to an `agent_run_id` and captures a single unit of work.
