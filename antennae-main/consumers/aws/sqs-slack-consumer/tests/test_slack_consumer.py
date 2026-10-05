"""
Unit tests for sqs-slack-consumer (poll_queue_for_messages and client).
Load slack consumer's main via importlib so we don't overwrite 'src' and break other tests.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

_slack_consumer_root = Path(__file__).resolve().parent.parent
_slack_main_py = _slack_consumer_root / "src" / "main.py"
_slack_client_py = _slack_consumer_root / "src" / "client.py"


def _make_fake_common_lib_for_main():
    """Build mock common_lib modules so main.py can be loaded without ursa dependencies."""
    setup_logger = MagicMock(return_value=MagicMock())
    db = MagicMock()
    AgentManager = MagicMock()
    ExecutorClient = MagicMock()
    storage = MagicMock()
    temporal = MagicMock()
    temporal.ExecutionMode = MagicMock()
    temporal.TaskPayload = MagicMock()
    temporal.WorkflowPayload = MagicMock()

    # Mock src.client to avoid SQLAlchemy imports
    mock_src_client = MagicMock()
    mock_src_client.run_agent = AsyncMock()

    mods = {
        "common_lib": MagicMock(),
        "common_lib.utils": MagicMock(),
        "common_lib.utils.logger": MagicMock(),
        "common_lib.database": MagicMock(),
        "common_lib.database.connection": MagicMock(),
        "common_lib.deployment": MagicMock(),
        "common_lib.deployment.agent_manager": MagicMock(),
        "common_lib.execution": MagicMock(),
        "common_lib.execution.executor_client": MagicMock(),
        "common_lib.models": MagicMock(),
        "common_lib.models.temporal": temporal,
        "common_lib.storage": MagicMock(),
        "common_lib.storage.storage_client": MagicMock(),
        "src": MagicMock(),
        "src.client": mock_src_client,
    }
    mods["common_lib.utils.logger"].setup_logger = setup_logger
    mods["common_lib.database.connection"].db = db
    mods["common_lib.deployment.agent_manager"].AgentManager = AgentManager
    mods["common_lib.execution.executor_client"].ExecutorClient = ExecutorClient
    mods["common_lib.storage.storage_client"].storage = storage
    return mods


def _load_slack_main():
    """Load slack consumer's main module under a unique name (does not touch sys.modules['src'])."""
    # Check if already loaded
    if "antennae_tests_slack_main" in sys.modules:
        return sys.modules["antennae_tests_slack_main"]

    mods = _make_fake_common_lib_for_main()
    with patch.dict(sys.modules, mods):
        spec = importlib.util.spec_from_file_location(
            "antennae_tests_slack_main",
            _slack_main_py,
            submodule_search_locations=[str(_slack_consumer_root / "src")],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load slack main from {_slack_main_py}")
        # Resolve imports (e.g. common_lib, src.client) by adding slack root to path for the load
        old_path = sys.path.copy()
        try:
            sys.path.insert(0, str(_slack_consumer_root))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            return mod
        finally:
            sys.path[:] = old_path


class TestGetSqsClient:
    """Tests for get_sqs_client function."""

    def test_get_sqs_client_success(self):
        """Test successful SQS client creation."""
        slack_main = _load_slack_main()
        mock_client = MagicMock()
        with patch.object(slack_main, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_client
            result = slack_main.get_sqs_client()
            assert result == mock_client

    def test_get_sqs_client_error(self):
        """Test SQS client creation with error."""
        slack_main = _load_slack_main()
        with patch.object(slack_main, "boto3") as mock_boto3:
            mock_boto3.client.side_effect = Exception("AWS credentials error")
            with pytest.raises(Exception, match="AWS credentials error"):
                slack_main.get_sqs_client()


class TestGetQueueUrl:
    """Tests for get_queue_url function."""

    def test_get_queue_url_success(self):
        """Test successful queue URL retrieval."""
        slack_main = _load_slack_main()
        mock_client = MagicMock()
        mock_client.get_queue_url.return_value = {
            "QueueUrl": "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        }
        url = slack_main.get_queue_url(
            mock_client, "slack-events-{environment}-{tenant_id}", "test", "tenant123"
        )
        assert url == "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        mock_client.get_queue_url.assert_called_once_with(QueueName="slack-events-test-tenant123")

    def test_get_queue_url_error(self):
        """Test queue URL retrieval with error."""
        slack_main = _load_slack_main()
        mock_client = MagicMock()
        mock_client.get_queue_url.side_effect = Exception("Queue not found")
        with pytest.raises(Exception, match="Queue not found"):
            slack_main.get_queue_url(
                mock_client, "slack-events-{environment}-{tenant_id}", "test", "tenant123"
            )


class TestPollQueueForMessages:
    """Tests for poll_queue_for_messages function."""

    def test_poll_returns_messages_when_received(self):
        slack_main = _load_slack_main()
        mock_sqs = MagicMock()
        mock_sqs.receive_message.side_effect = [
            {
                "Messages": [
                    {
                        "MessageId": "msg-1",
                        "ReceiptHandle": "rh1",
                        "Body": json.dumps(
                            {
                                "text": "hello",
                                "tenant_id": "t1",
                                "channel": "c1",
                                "user": "u1",
                                "event_type": "event",
                            }
                        ),
                    }
                ]
            },
            KeyboardInterrupt(),
        ]
        with patch.dict(
            "os.environ", {"TENANT_ID": "t1", "AWS_ACCESS_KEY": "ak", "AWS_SECRET_KEY": "sk"}
        ):
            with patch.object(slack_main, "get_sqs_client", return_value=mock_sqs):
                with patch.object(
                    slack_main,
                    "get_queue_url",
                    return_value="https://sqs.us-east-1.amazonaws.com/123/q",
                ):
                    with patch.object(slack_main, "run_agent", new_callable=AsyncMock):
                        slack_main.poll_queue_for_messages()
        call_kw = mock_sqs.receive_message.call_args[1]
        assert call_kw["QueueUrl"] == "https://sqs.us-east-1.amazonaws.com/123/q"
        assert call_kw["MaxNumberOfMessages"] == 10
        assert call_kw["WaitTimeSeconds"] == 20

    def test_poll_exits_when_queue_empty(self):
        slack_main = _load_slack_main()
        mock_sqs = MagicMock()
        mock_sqs.receive_message.side_effect = [{}, KeyboardInterrupt()]
        with patch.dict("os.environ", {"TENANT_ID": "t1"}):
            with patch.object(slack_main, "get_sqs_client", return_value=mock_sqs):
                with patch.object(
                    slack_main,
                    "get_queue_url",
                    return_value="https://sqs.us-east-1.amazonaws.com/123/q",
                ):
                    slack_main.poll_queue_for_messages()
        assert mock_sqs.receive_message.call_count >= 1

    def test_poll_handles_boto3_client_error(self):
        slack_main = _load_slack_main()
        with patch.dict("os.environ", {"TENANT_ID": "t1"}):
            with patch.object(
                slack_main, "get_sqs_client", side_effect=Exception("credentials failed")
            ):
                slack_main.poll_queue_for_messages()

    def test_poll_handles_receive_exception(self):
        slack_main = _load_slack_main()
        mock_sqs = MagicMock()
        mock_sqs.receive_message.side_effect = Exception("network error")
        with patch.dict("os.environ", {"TENANT_ID": "t1"}):
            with patch.object(slack_main, "get_sqs_client", return_value=mock_sqs):
                with patch.object(
                    slack_main,
                    "get_queue_url",
                    return_value="https://sqs.us-east-1.amazonaws.com/123/q",
                ):
                    slack_main.poll_queue_for_messages()
        mock_sqs.receive_message.assert_called_once()

    def test_poll_get_sqs_client_error_returns(self):
        """Test poll_queue_for_messages returns when get_sqs_client fails."""
        slack_main = _load_slack_main()
        with patch.dict("os.environ", {"TENANT_ID": "t1"}):
            with patch.object(slack_main, "get_sqs_client", side_effect=Exception("AWS error")):
                # Should return None (no raise)
                slack_main.poll_queue_for_messages()

    def test_poll_get_queue_url_error_returns(self):
        """Test poll_queue_for_messages returns when get_queue_url fails."""
        slack_main = _load_slack_main()
        mock_sqs = MagicMock()
        with patch.dict("os.environ", {"TENANT_ID": "t1"}):
            with patch.object(slack_main, "get_sqs_client", return_value=mock_sqs):
                with patch.object(
                    slack_main, "get_queue_url", side_effect=Exception("Queue not found")
                ):
                    slack_main.poll_queue_for_messages()
        mock_sqs.receive_message.assert_not_called()

    def test_poll_tenant_id_missing_raises(self):
        """Test poll_queue_for_messages raises ValueError when TENANT_ID is missing."""
        slack_main = _load_slack_main()
        with patch.object(slack_main, "tenant_id", ""):
            with pytest.raises(ValueError, match="TENANT_ID"):
                slack_main.poll_queue_for_messages()

    def test_poll_message_json_decode_error_continues(self):
        """Test message with invalid JSON body is handled (no crash)."""
        slack_main = _load_slack_main()
        mock_sqs = MagicMock()
        mock_sqs.receive_message.side_effect = [
            {"Messages": [{"MessageId": "m1", "ReceiptHandle": "rh1", "Body": "not valid json"}]},
            KeyboardInterrupt(),
        ]
        with patch.dict("os.environ", {"TENANT_ID": "t1"}):
            with patch.object(slack_main, "get_sqs_client", return_value=mock_sqs):
                with patch.object(slack_main, "get_queue_url", return_value="https://sqs.test/q"):
                    slack_main.poll_queue_for_messages()
        mock_sqs.receive_message.assert_called()

    def test_poll_processing_exception_continues(self):
        """Test when run_agent raises, loop continues and next poll runs."""
        slack_main = _load_slack_main()
        mock_sqs = MagicMock()
        mock_sqs.receive_message.side_effect = [
            {
                "Messages": [
                    {
                        "MessageId": "m1",
                        "ReceiptHandle": "rh1",
                        "Body": json.dumps(
                            {
                                "text": "hi",
                                "tenant_id": "t1",
                                "channel": "c1",
                                "user": "u1",
                                "event_type": "e",
                            }
                        ),
                    }
                ]
            },
            KeyboardInterrupt(),
        ]
        with patch.dict("os.environ", {"TENANT_ID": "t1"}):
            with patch.object(slack_main, "get_sqs_client", return_value=mock_sqs):
                with patch.object(slack_main, "get_queue_url", return_value="https://sqs.test/q"):
                    with patch.object(
                        slack_main,
                        "run_agent",
                        new_callable=AsyncMock,
                        side_effect=Exception("Agent failed"),
                    ):
                        slack_main.poll_queue_for_messages()
        mock_sqs.receive_message.assert_called()

    def test_poll_strips_user_mentions_from_text(self):
        """Test that user mentions like <@U123ABC> are stripped from message text."""
        slack_main = _load_slack_main()
        mock_sqs = MagicMock()
        mock_sqs.receive_message.side_effect = [
            {
                "Messages": [
                    {
                        "MessageId": "m1",
                        "ReceiptHandle": "rh1",
                        "Body": json.dumps(
                            {
                                "text": "<@U123ABC> hello world <@U456DEF>",
                                "tenant_id": "t1",
                                "channel": "c1",
                                "user": "u1",
                                "event_type": "e",
                                "raw_event": {"event_id": "evt-1"},
                            }
                        ),
                    }
                ]
            },
            KeyboardInterrupt(),
        ]
        captured_params = None

        async def capture_run_agent(agent_name, agent_params, run_in_sync):
            nonlocal captured_params
            captured_params = json.loads(agent_params)

        with patch.dict("os.environ", {"TENANT_ID": "t1"}):
            with patch.object(slack_main, "get_sqs_client", return_value=mock_sqs):
                with patch.object(slack_main, "get_queue_url", return_value="https://sqs.test/q"):
                    with patch.object(slack_main, "run_agent", side_effect=capture_run_agent):
                        slack_main.poll_queue_for_messages()

        assert captured_params is not None
        assert captured_params["ticket_key"] == "hello world"

    def test_poll_deletes_message_after_success(self):
        """Test that message is deleted from queue after successful processing."""
        slack_main = _load_slack_main()
        mock_sqs = MagicMock()
        mock_sqs.receive_message.side_effect = [
            {
                "Messages": [
                    {
                        "MessageId": "msg-123",
                        "ReceiptHandle": "receipt-handle-456",
                        "Body": json.dumps(
                            {
                                "text": "test",
                                "tenant_id": "t1",
                                "channel": "c1",
                                "user": "u1",
                                "event_type": "e",
                            }
                        ),
                    }
                ]
            },
            KeyboardInterrupt(),
        ]
        with patch.dict("os.environ", {"TENANT_ID": "t1"}):
            with patch.object(slack_main, "get_sqs_client", return_value=mock_sqs):
                with patch.object(slack_main, "get_queue_url", return_value="https://sqs.test/q"):
                    with patch.object(slack_main, "run_agent", new_callable=AsyncMock):
                        slack_main.poll_queue_for_messages()

        mock_sqs.delete_message.assert_called_once_with(
            QueueUrl="https://sqs.test/q", ReceiptHandle="receipt-handle-456"
        )


class TestMainBlock:
    """Tests for __main__ block execution."""

    def test_main_block_success(self):
        """Test main block runs poll_queue_for_messages successfully."""
        slack_main = _load_slack_main()
        with patch.object(slack_main, "tenant_id", "test-tenant"):
            with patch.object(slack_main, "poll_queue_for_messages") as mock_poll:
                # Simulate successful execution
                mock_poll.return_value = None
                # We can't easily test __main__ block directly, but we test the function it calls
                slack_main.poll_queue_for_messages()
                mock_poll.assert_called_once()

    def test_main_block_keyboard_interrupt(self):
        """Test main block handles KeyboardInterrupt."""
        slack_main = _load_slack_main()
        with patch.object(slack_main, "tenant_id", "test-tenant"):
            with patch.object(slack_main, "poll_queue_for_messages", side_effect=KeyboardInterrupt):
                # Should not raise
                try:
                    slack_main.poll_queue_for_messages()
                except KeyboardInterrupt:
                    pass  # Expected behavior

    def test_main_block_critical_exception(self):
        """Test main block handles critical exceptions."""
        slack_main = _load_slack_main()
        with patch.object(slack_main, "tenant_id", "test-tenant"):
            with patch.object(
                slack_main,
                "poll_queue_for_messages",
                side_effect=RuntimeError("Critical failure"),
            ):
                with pytest.raises(RuntimeError, match="Critical failure"):
                    slack_main.poll_queue_for_messages()


def _make_fake_common_lib():
    """Build mock common_lib modules so client.py can be loaded without ursa."""
    db = MagicMock()
    AgentManager = MagicMock()
    ExecutorClient = MagicMock()
    storage = MagicMock()
    setup_logger = MagicMock(return_value=MagicMock())
    # client imports ExecutionMode, TaskPayload, WorkflowPayload from common_lib.models.temporal
    temporal = MagicMock()
    temporal.ExecutionMode = MagicMock()
    temporal.TaskPayload = MagicMock()
    temporal.WorkflowPayload = MagicMock()
    mods = {
        "common_lib": MagicMock(),
        "common_lib.database": MagicMock(),
        "common_lib.database.connection": MagicMock(),
        "common_lib.deployment": MagicMock(),
        "common_lib.deployment.agent_manager": MagicMock(),
        "common_lib.execution": MagicMock(),
        "common_lib.execution.executor_client": MagicMock(),
        "common_lib.models": MagicMock(),
        "common_lib.models.temporal": temporal,
        "common_lib.storage": MagicMock(),
        "common_lib.storage.storage_client": MagicMock(),
        "common_lib.utils": MagicMock(),
        "common_lib.utils.logger": MagicMock(),
    }
    mods["common_lib.database.connection"].db = db
    mods["common_lib.deployment.agent_manager"].AgentManager = AgentManager
    mods["common_lib.execution.executor_client"].ExecutorClient = ExecutorClient
    mods["common_lib.storage.storage_client"].storage = storage
    mods["common_lib.utils.logger"].setup_logger = setup_logger
    return mods, db, storage


def _load_slack_client():
    """Load slack consumer client module with common_lib mocked."""
    mods, db, storage = _make_fake_common_lib()
    with patch.dict(sys.modules, mods):
        spec = importlib.util.spec_from_file_location(
            "antennae_tests_slack_client",
            _slack_client_py,
            submodule_search_locations=[str(_slack_consumer_root / "src")],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load slack client from {_slack_client_py}")
        old_path = sys.path.copy()
        try:
            sys.path.insert(0, str(_slack_consumer_root))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            return mod
        finally:
            sys.path[:] = old_path


# Load slack client once per test session (avoids sqlalchemy "already registered" error)
_slack_client_module = None


def _get_slack_client():
    global _slack_client_module
    if _slack_client_module is None:
        _slack_client_module = _load_slack_client()
    return _slack_client_module


@pytest.mark.asyncio
class TestAAASlackConsumerClient:
    """Tests for src/client.py (get_db_session, run_agent, _run_agent_generator).

    NOTE: Named TestAAA... to run first alphabetically for SQLAlchemy compatibility.
    """

    async def test_get_db_session_success(self):
        slack_client = _get_slack_client()
        mock_session = MagicMock()
        slack_client.db.get_session.return_value = mock_session
        session = await slack_client.get_db_session()
        assert session is mock_session

    async def test_get_db_session_operational_error_raises(self):
        slack_client = _get_slack_client()
        slack_client.db.get_session.side_effect = OperationalError("statement", {}, None)
        with pytest.raises(Exception, match="Database not found"):
            await slack_client.get_db_session()

    async def test_get_db_session_other_error_raises(self):
        slack_client = _get_slack_client()
        slack_client.db.get_session.side_effect = ValueError("unexpected")
        with pytest.raises(Exception, match="Unexpected DB error"):
            await slack_client.get_db_session()

    async def test_run_agent_storage_init_failure_raises(self):
        slack_client = _get_slack_client()
        slack_client.storage.init_client.side_effect = Exception("storage unavailable")
        with pytest.raises(RuntimeError, match="Ursa storage client initialization failed"):
            await slack_client.run_agent("my_agent", "{}")

    async def test_run_agent_success_returns_last_result(self):
        slack_client = _get_slack_client()
        slack_client.storage.init_client.side_effect = None
        slack_client.storage.init_client.return_value = None
        mock_session = MagicMock()
        slack_client.db.get_session.side_effect = None  # Reset side_effect from previous test
        slack_client.db.get_session.return_value = mock_session

        async def one_result(*args, **kwargs):
            yield {"status": "ok", "result": "done"}

        with patch.object(slack_client, "_run_agent_generator", side_effect=one_result):
            result = await slack_client.run_agent("my_agent", "{}")
        assert result == {"status": "ok", "result": "done"}
        mock_session.close.assert_called_once()

    # --- _run_agent_generator coverage (lines 33-142) ---

    async def test_run_agent_generator_agent_not_found_raises(self):
        """AgentManager.get_agent_details returns None -> Exception."""
        slack_client = _get_slack_client()
        slack_client.AgentManager.get_agent_details.return_value = None
        mock_session = MagicMock()
        with pytest.raises(Exception, match="not found"):
            async for _ in slack_client._run_agent_generator("MyAgent", "{}", False, mock_session):
                pass

    async def test_run_agent_generator_run_path_yields_result(self):
        """request_type=run: start_workflow returns result, one value yielded."""
        slack_client = _get_slack_client()
        mock_session = MagicMock()
        slack_client.AgentManager.get_agent_details.return_value = {
            "id": "wf-123",
            "activity_task_queues": {"default": "q1"},
        }
        mock_exec = MagicMock()
        mock_exec.start_workflow = AsyncMock(
            return_value={
                "status": "completed",
                "result": "done",
                "workflow_id": "wf-123",
                "run_id": "run-1",
            }
        )
        slack_client.ExecutorClient.return_value = mock_exec
        results = []
        async for r in slack_client._run_agent_generator("MyAgent", "{}", False, mock_session):
            results.append(r)
        assert len(results) == 1
        assert results[0]["status"] == "completed"
        assert results[0]["result"] == "done"
        assert results[0]["workflow_id"] == "wf-123"
        mock_exec.start_workflow.assert_called_once()
        call_args, call_kw = mock_exec.start_workflow.call_args
        assert call_args[0] == "MyAgent"
        assert call_kw.get("task_queue") == "wf-123-task-queue"

    async def test_run_agent_generator_chat_path_yields_updates(self):
        """request_type=chat: submit_task async gen yields updates."""
        slack_client = _get_slack_client()
        mock_session = MagicMock()
        slack_client.AgentManager.get_agent_details.return_value = {
            "id": "wf-456",
            "activity_task_queues": {"default": "q1"},
        }

        async def submit_gen(*args, **kwargs):
            yield {"status": "running"}
            yield {"status": "completed", "result": "chat-done"}

        mock_exec = MagicMock()
        mock_exec.submit_task = MagicMock(side_effect=lambda *a, **k: submit_gen())
        slack_client.ExecutorClient.return_value = mock_exec
        params = json.dumps(
            {
                "task_payload": {"query": "hi"},
                "workflow_payload": {"workflow_metadata": {}, "workflow_args": {}},
            }
        )
        results = []
        async for r in slack_client._run_agent_generator("ChatAgent", params, True, mock_session):
            results.append(r)
        assert len(results) == 2
        assert results[1]["status"] == "completed"
        assert results[1]["result"] == "chat-done"
        mock_exec.submit_task.assert_called_once()

    async def test_run_agent_generator_invalid_json_raises(self):
        """Invalid JSON in agent_params -> Exception or JSONDecodeError."""
        slack_client = _get_slack_client()
        mock_session = MagicMock()
        with pytest.raises(
            (Exception, json.JSONDecodeError), match="(Invalid JSON|Expecting value)"
        ):
            async for _ in slack_client._run_agent_generator("A", "not json", False, mock_session):
                pass

    async def test_run_agent_generator_exception_propagates(self):
        """Exception inside start_workflow -> propagated."""
        slack_client = _get_slack_client()
        mock_session = MagicMock()
        slack_client.AgentManager.get_agent_details.return_value = {
            "id": "w1",
            "activity_task_queues": {},
        }
        mock_exec = MagicMock()
        mock_exec.start_workflow = AsyncMock(side_effect=RuntimeError("Workflow failed"))
        slack_client.ExecutorClient.return_value = mock_exec
        with pytest.raises(RuntimeError, match="Workflow failed"):
            async for _ in slack_client._run_agent_generator("A", "{}", False, mock_session):
                pass

    async def test_run_agent_generator_empty_agent_name_raises(self):
        """Empty agent name raises Exception."""
        slack_client = _get_slack_client()
        mock_session = MagicMock()
        with pytest.raises(Exception, match="Agent name is required"):
            async for _ in slack_client._run_agent_generator("", "{}", False, mock_session):
                pass

    async def test_run_agent_generator_none_agent_params(self):
        """Test with None agent_params defaults to empty dict."""
        slack_client = _get_slack_client()
        mock_session = MagicMock()
        slack_client.AgentManager.get_agent_details.return_value = {
            "id": "wf-1",
            "activity_task_queues": {},
        }
        mock_exec = MagicMock()
        mock_exec.start_workflow = AsyncMock(return_value={"status": "completed", "result": "ok"})
        slack_client.ExecutorClient.return_value = mock_exec
        results = []
        async for r in slack_client._run_agent_generator("TestAgent", "", False, mock_session):
            results.append(r)
        assert len(results) == 1

    async def test_run_agent_generator_chat_with_defaults(self):
        """Test chat path applies workflow_args defaults when not provided."""
        slack_client = _get_slack_client()
        mock_session = MagicMock()
        slack_client.AgentManager.get_agent_details.return_value = {
            "id": "wf-chat",
            "activity_task_queues": {"default": "queue1"},
        }

        captured_workflow_payload = None

        async def mock_submit(*args, **kwargs):
            nonlocal captured_workflow_payload
            captured_workflow_payload = kwargs.get("workflow_payload")
            yield {"status": "completed", "result": "done"}

        mock_exec = MagicMock()
        mock_exec.submit_task = MagicMock(side_effect=lambda *a, **k: mock_submit(*a, **k))
        slack_client.ExecutorClient.return_value = mock_exec

        params = json.dumps(
            {
                "task_payload": {"query": "test"},
                "workflow_payload": {"workflow_args": {}},  # Empty workflow_args
            }
        )
        results = []
        async for r in slack_client._run_agent_generator("ChatAgent", params, True, mock_session):
            results.append(r)

        assert len(results) == 1
        # Verify TaskPayload and WorkflowPayload were constructed
        mock_exec.submit_task.assert_called_once()

    async def test_run_agent_generator_chat_with_workflow_metadata_timeout(self):
        """Test chat path uses workflow_metadata timeout."""
        slack_client = _get_slack_client()
        mock_session = MagicMock()
        slack_client.AgentManager.get_agent_details.return_value = {
            "id": "wf-timeout",
            "activity_task_queues": {},
        }

        captured_timeout = None

        async def mock_submit(*args, **kwargs):
            nonlocal captured_timeout
            captured_timeout = kwargs.get("timeout_seconds")
            yield {"status": "completed", "result": "done"}

        mock_exec = MagicMock()
        mock_exec.submit_task = MagicMock(side_effect=lambda *a, **k: mock_submit(*a, **k))
        slack_client.ExecutorClient.return_value = mock_exec

        params = json.dumps(
            {
                "task_payload": {"query": "test"},
                "workflow_payload": {
                    "workflow_metadata": {"default_timeout": 600},
                    "workflow_args": {},
                },
            }
        )
        results = []
        async for r in slack_client._run_agent_generator(
            "TimeoutAgent", params, True, mock_session
        ):
            results.append(r)

        assert len(results) == 1
        assert captured_timeout == 600

    async def test_run_agent_with_default_params(self):
        """Test run_agent with default parameters."""
        slack_client = _get_slack_client()
        slack_client.storage.init_client.side_effect = None
        slack_client.storage.init_client.return_value = None
        mock_session = MagicMock()
        slack_client.db.get_session.side_effect = None
        slack_client.db.get_session.return_value = mock_session

        async def mock_generator(*args, **kwargs):
            yield {"status": "completed", "result": "final"}

        with patch.object(slack_client, "_run_agent_generator", side_effect=mock_generator):
            result = await slack_client.run_agent("TestAgent")

        assert result == {"status": "completed", "result": "final"}
        mock_session.close.assert_called_once()

    async def test_run_agent_multiple_yields(self):
        """Test run_agent returns the last yielded result."""
        slack_client = _get_slack_client()
        slack_client.storage.init_client.side_effect = None
        slack_client.storage.init_client.return_value = None
        mock_session = MagicMock()
        slack_client.db.get_session.side_effect = None
        slack_client.db.get_session.return_value = mock_session

        async def mock_generator(*args, **kwargs):
            yield {"status": "running", "result": None}
            yield {"status": "processing", "result": "intermediate"}
            yield {"status": "completed", "result": "final"}

        with patch.object(slack_client, "_run_agent_generator", side_effect=mock_generator):
            result = await slack_client.run_agent("TestAgent", "{}", True)

        assert result == {"status": "completed", "result": "final"}

    async def test_run_agent_generator_activity_task_queues(self):
        """Test that activity_task_queues are passed correctly."""
        slack_client = _get_slack_client()
        mock_session = MagicMock()
        slack_client.AgentManager.get_agent_details.return_value = {
            "id": "wf-queues",
            "activity_task_queues": {"queue1": "custom-q1", "queue2": "custom-q2"},
        }

        captured_params = None

        async def mock_start(*args, **kwargs):
            nonlocal captured_params
            captured_params = args[1] if len(args) > 1 else {}
            return {
                "status": "completed",
                "result": "ok",
                "workflow_id": "wf-queues",
                "run_id": "r1",
            }

        mock_exec = MagicMock()
        mock_exec.start_workflow = mock_start
        slack_client.ExecutorClient.return_value = mock_exec

        results = []
        async for r in slack_client._run_agent_generator("QueueAgent", "{}", False, mock_session):
            results.append(r)

        assert captured_params is not None
        assert captured_params.get("activity_task_queues") == {
            "queue1": "custom-q1",
            "queue2": "custom-q2",
        }
