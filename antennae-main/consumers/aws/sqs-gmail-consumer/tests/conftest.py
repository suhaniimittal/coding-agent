"""
Shared test configuration for sqs-gmail-consumer tests.
- Sets up path to allow "from src.xxx" imports.
- Gmail-specific fixtures for testing gmail consumer modules.
"""

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from cryptography.fernet import Fernet

# Add sqs-gmail-consumer root so "from src.xxx" works
_gmail_root = Path(__file__).resolve().parent.parent
_ursa_src = _gmail_root.parent.parent.parent.parent / "ursa" / "src"

if str(_gmail_root) not in sys.path:
    sys.path.insert(0, str(_gmail_root))

# Set env required at import time by gmail consumer (before any test or consumer code loads)
if "ENCRYPTION_KEY" not in os.environ:
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
if "TENANT_ID" not in os.environ:
    os.environ["TENANT_ID"] = "test-tenant"

# Mock common_lib modules BEFORE any test modules try to import from src.*
# This is necessary because common_lib uses Python 3.10+ syntax (match statement)
_mock_storage = MagicMock()
_mock_storage.init_client = MagicMock()
_mock_storage.store_object = MagicMock()

_mock_db = MagicMock()
_mock_db.get_session = MagicMock()

_mock_agent_manager = MagicMock()
_mock_executor_client = MagicMock()
_mock_setup_logger = MagicMock(return_value=MagicMock())

_mock_temporal = MagicMock()
_mock_temporal.ExecutionMode = MagicMock()
_mock_temporal.TaskPayload = MagicMock()
_mock_temporal.WorkflowPayload = MagicMock()

_mock_storage_service_class = MagicMock()

# Pre-populate sys.modules with mocked common_lib modules
sys.modules["common_lib"] = MagicMock()
sys.modules["common_lib.storage"] = MagicMock()
sys.modules["common_lib.storage.storage_client"] = MagicMock()
sys.modules["common_lib.storage.storage_client"].storage = _mock_storage
sys.modules["common_lib.storage.storage_client"].StorageService = _mock_storage_service_class
sys.modules["common_lib.utils"] = MagicMock()
sys.modules["common_lib.utils.logger"] = MagicMock()
sys.modules["common_lib.utils.logger"].setup_logger = _mock_setup_logger
sys.modules["common_lib.database"] = MagicMock()
sys.modules["common_lib.database.connection"] = MagicMock()
sys.modules["common_lib.database.connection"].db = _mock_db
sys.modules["common_lib.deployment"] = MagicMock()
sys.modules["common_lib.deployment.agent_manager"] = MagicMock()
sys.modules["common_lib.deployment.agent_manager"].AgentManager = _mock_agent_manager
sys.modules["common_lib.execution"] = MagicMock()
sys.modules["common_lib.execution.executor_client"] = MagicMock()
sys.modules["common_lib.execution.executor_client"].ExecutorClient = _mock_executor_client
sys.modules["common_lib.models"] = MagicMock()
sys.modules["common_lib.models.temporal"] = _mock_temporal


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    return Mock()


@pytest.fixture
def sample_email_config():
    """Sample email filter configuration."""
    return {
        "whitelist_mode": True,
        "allowed_senders": ["test@example.com", "admin@company.com"],
        "blocked_senders": ["spam@badactor.com"],
        "allow_domains": ["trusted-domain.com"],
        "block_domains": ["spam-domain.com"],
    }


@pytest.fixture
def mock_sqs_message():
    """Mock SQS message structure."""
    import json

    return {
        "Messages": [
            {
                "MessageId": "test-message-id-123",
                "ReceiptHandle": "test-receipt-handle",
                "Body": json.dumps({"historyId": "12345678"}),
            }
        ]
    }


@pytest.fixture
def mock_gmail_message():
    """Mock Gmail API message response."""
    return {
        "id": "test-gmail-msg-id",
        "payload": {
            "headers": [
                {"name": "From", "value": "Test Sender <test@example.com>"},
                {"name": "Subject", "value": "Test Subject"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {
                        "data": "VGVzdCBtZXNzYWdlIGJvZHk="  # base64 encoded "Test message body"
                    },
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "test-attachment.pdf",
                    "body": {"attachmentId": "test-attachment-id"},
                },
            ],
        },
    }


@pytest.fixture
def mock_gmail_attachment():
    """Mock Gmail attachment response."""
    return {
        "data": "VGVzdCBhdHRhY2htZW50IGRhdGE="  # base64 encoded "Test attachment data"
    }


@pytest.fixture
def mock_db_token_record():
    """Mock database token record."""
    fernet_key = Fernet.generate_key()
    f = Fernet(fernet_key)
    access_token = f.encrypt(b"mock-access-token")
    refresh_token = f.encrypt(b"mock-refresh-token")
    return {
        "access_token_enc": access_token,
        "refresh_token_enc": refresh_token,
        "token_expires_at": datetime.now(UTC),
        "fernet_key": fernet_key.decode(),
    }


@pytest.fixture
def mock_boto3_client():
    """Mock boto3 SQS client."""
    client = Mock()
    client.get_queue_url.return_value = {
        "QueueUrl": "https://sqs.us-east-1.amazonaws.com/123/test-queue"
    }
    client.receive_message.return_value = {}
    client.delete_message.return_value = {}
    return client


@pytest.fixture
def mock_gmail_service():
    """Mock Gmail API service."""
    service = Mock()
    messages_mock = Mock()
    service.users.return_value.messages.return_value = messages_mock
    messages_mock.get.return_value.execute.return_value = {}
    messages_mock.list.return_value.execute.return_value = {"messages": []}
    attachments_mock = Mock()
    messages_mock.attachments.return_value = attachments_mock
    attachments_mock.get.return_value.execute.return_value = {}
    history_mock = Mock()
    service.users.return_value.history.return_value = history_mock
    history_mock.list.return_value.execute.return_value = {"history": []}
    return service


@pytest.fixture
def mock_storage_service():
    """Mock storage service for S3 operations."""
    storage = Mock()
    storage.init_client = Mock()
    storage.store_object = Mock()
    return storage


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    session = Mock()
    session.execute.return_value.fetchone.return_value = None
    session.close = Mock()
    return session


@pytest.fixture
def sample_agent_config():
    """Sample agent configuration."""
    return {
        "id": "test-agent-id",
        "name": "TestWorkflow",
        "activity_task_queues": {"default": "test-queue"},
    }


@pytest.fixture
def mock_executor_client():
    """Mock executor client for agent operations."""
    client = Mock()
    client.start_workflow = AsyncMock(
        return_value={
            "status": "completed",
            "result": "test-result",
            "workflow_id": "test-workflow-id",
            "run_id": "test-run-id",
        }
    )

    async def mock_submit_task(*args, **kwargs):
        yield {"status": "running", "result": None}
        yield {"status": "completed", "result": "test-result"}

    client.submit_task = mock_submit_task
    return client


@pytest.fixture
def env_vars():
    """Sample environment variables (used by gmail consumer tests)."""
    return {
        "ENVIRONMENT": "test",
        "TENANT_ID": "test-tenant",
        "QUEUE_NAME_PATTERN": "gmail-events-{environment}-{tenant_id}",
        "AWS_REGION": "us-east-1",
        "LISTENER_EMAIL": "test@example.com",
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "GMAIL_CLIENT_ID": "test-client-id",
        "GMAIL_CLIENT_SECRET": "test-client-secret",
        "EMAIL_FILTER_WHITELIST_MODE": "true",
        "EMAIL_FILTER_ALLOWED_SENDERS": "test@example.com,admin@company.com",
        "EMAIL_FILTER_BLOCKED_SENDERS": "spam@badactor.com",
        "EMAIL_FILTER_ALLOW_DOMAINS": "trusted-domain.com",
        "EMAIL_FILTER_BLOCK_DOMAINS": "spam-domain.com",
    }


@pytest.fixture(autouse=True)
def mock_environment(env_vars, monkeypatch):
    """Automatically mock environment variables for all tests."""
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_common_lib_modules():
    """Mock all common_lib imports to avoid dependency issues."""
    return {
        "storage": Mock(),
        "logger": Mock(),
        "db": Mock(),
        "agent_manager": Mock(),
        "executor_client": Mock(),
    }
