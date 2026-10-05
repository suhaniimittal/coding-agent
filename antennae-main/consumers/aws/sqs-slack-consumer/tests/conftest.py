"""
Shared test configuration for sqs-slack-consumer tests.
"""

import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

# Add sqs-slack-consumer root to path
_slack_root = Path(__file__).resolve().parent.parent
_ursa_src = _slack_root.parent.parent.parent.parent / "ursa" / "src"

if str(_slack_root) not in sys.path:
    sys.path.insert(0, str(_slack_root))
if _ursa_src.exists() and str(_ursa_src) not in sys.path:
    sys.path.insert(0, str(_ursa_src))

# Set env required at import time
if "ENCRYPTION_KEY" not in os.environ:
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
if "TENANT_ID" not in os.environ:
    os.environ["TENANT_ID"] = "test-tenant"


@pytest.fixture
def mock_logger():
    """Mock logger for testing."""
    return Mock()


@pytest.fixture
def env_vars():
    """Sample environment variables."""
    return {
        "ENVIRONMENT": "test",
        "TENANT_ID": "test-tenant",
        "AWS_ACCESS_KEY": "test-access-key",
        "AWS_SECRET_KEY": "test-secret-key",
        "AWS_REGION": "us-east-1",
        "SQS_QUEUE_NAME_PATTERN": "slack-events-{environment}-{tenant_id}",
    }


@pytest.fixture(autouse=True)
def mock_environment(env_vars, monkeypatch):
    """Automatically mock environment variables for all tests."""
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)
