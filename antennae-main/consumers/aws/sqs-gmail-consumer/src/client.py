import json

from common_lib.database.connection import db  # Assuming db.get_session exists
from common_lib.deployment.agent_manager import AgentManager
from common_lib.execution.executor_client import ExecutorClient
from common_lib.models.temporal import ExecutionMode, TaskPayload, WorkflowPayload
from common_lib.storage.storage_client import storage
from common_lib.utils.logger import setup_logger
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

logger = setup_logger(__name__)


async def get_db_session() -> Session:
    """Creates a tenant-specific DB session manually."""
    try:
        session = db.get_session()
        return session
    except OperationalError:
        raise Exception("Database not found or is misconfigured.")
    except Exception as e:
        raise Exception(f"Unexpected DB error: {e}")


async def _run_agent_generator(
    agent_name: str,
    agent_params: str,
    run_in_sync: bool,
    db_session: Session,
):
    """Internal generator for running agent/workflow. Yields updates as they come."""
    if not agent_name:
        raise Exception("Agent name is required")

    logger.info(f"Running agent params: {agent_params}")
    parsed_params = json.loads(agent_params) if agent_params else {}
    activity_task_queues = {}

    try:
        task_payload = parsed_params.get("task_payload", {})
        workflow_payload = parsed_params.get("workflow_payload", {})

        logger.info(f"Task payload: {task_payload}")
        logger.info(f"Workflow payload: {workflow_payload}")

        request_type = "chat" if task_payload and workflow_payload else "run"

        agent = AgentManager.get_agent_details(agent_name, db_session=db_session)
        if not agent:
            raise Exception(f"Agent {agent_name} not found")

        activity_task_queues = agent.get("activity_task_queues", {})
        logger.info(f"Activity task queues: {activity_task_queues}")

    except json.JSONDecodeError:
        raise Exception("Invalid JSON in agent_params")

    try:
        if request_type == "chat":
            logger.info("Request type is chat — preparing workflow and task payloads")

            workflow_metadata = workflow_payload.get("workflow_metadata") or {}
            workflow_payload.setdefault("workflow_args", {})
            workflow_payload["workflow_args"]["activity_task_queues"] = activity_task_queues

            workflow_payload["workflow_args"].setdefault(
                "llm",
                {
                    "provider": "openai",
                    "model": "gpt-4o",
                    "max_tokens": 4096,
                    "temperature": 0.5,
                },
            )
            workflow_payload["workflow_args"].setdefault(
                "prompts",
                {
                    "system": "You are an AI orchestrator.",
                    "user": "",
                },
            )

            workflow_payload.setdefault(
                "workflow_metadata",
                {
                    "signal_name": "new_task",
                    "query_name": "get_task_result",
                    "supports_signals": True,
                    "is_long_running": True,
                    "default_timeout": 360,
                },
            )

            logger.info(f"Workflow args: {workflow_payload.get('workflow_args')}")

            task_payload = TaskPayload(**task_payload)
            workflow_payload = WorkflowPayload(**workflow_payload)
            exec_client = ExecutorClient()

            async for update in exec_client.submit_task(
                workflow_payload=workflow_payload,
                task_payload=task_payload,
                execution_mode=ExecutionMode.START_OR_SIGNAL,
                wait_for_result=run_in_sync,
                timeout_seconds=workflow_metadata.get("default_timeout")
                or workflow_payload.workflow_metadata.get("default_timeout"),
            ):
                logger.info(f"Yielding update: {update}")
                yield update

        else:
            logger.info("Request type is run — starting workflow directly")

            client = ExecutorClient()
            parsed_params["is_workflow_only"] = True
            parsed_params["activity_task_queues"] = activity_task_queues
            logger.info(f"agent details : {agent}")
            logger.info(f"workflow_id : {agent.get('id')}")
            workflow_id = str(agent.get("id"))
            task_queue = f"{workflow_id}-task-queue"

            workflow_result = await client.start_workflow(
                agent_name,
                parsed_params,
                id=workflow_id,
                task_queue=task_queue,
                run_in_sync=run_in_sync,
                agent_id=agent.get("id"),
            )

            yield {
                "status": workflow_result.get("status", "unknown"),
                "result": workflow_result.get("result"),
                "agent_name": agent_name,
                "workflow_id": workflow_result.get("workflow_id", workflow_id),
                "run_id": workflow_result.get("run_id"),
            }

    except Exception as e:
        logger.error(f"Failed to run agent: {str(e)}", exc_info=True)
        raise


async def run_agent(
    agent_name: str,
    agent_params: str = "{}",
    run_in_sync: bool = False,
):
    """Simplified version of the FastAPI route that can be called directly."""
    try:
        logger.info("Initializing Ursa storage client...")
        storage.init_client()
        logger.info("Ursa storage client initialized.")
    except Exception as e:
        logger.critical(f"CRITICAL: Failed to initialize Ursa storage client: {e}", exc_info=True)
        raise RuntimeError("Ursa storage client initialization failed") from e
    db_session = await get_db_session()
    final_result = None

    async for result in _run_agent_generator(
        agent_name=agent_name,
        agent_params=agent_params,
        run_in_sync=run_in_sync,
        db_session=db_session,
    ):
        final_result = result

    db_session.close()
    return final_result
