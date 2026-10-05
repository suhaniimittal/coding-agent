"""
Tests for webhook config module (Strategy-based configuration).
Uses gmail-webhook config; same pattern as voice-webhook.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_config_dir = Path(__file__).resolve().parent.parent
_config_path = _config_dir / "config.py"


def _load_config():
    spec = importlib.util.spec_from_file_location("webhook_config", _config_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {_config_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    old = sys.path.copy()
    try:
        sys.path.insert(0, str(_config_dir))
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = old
    return mod


@pytest.fixture(scope="module")
def config():
    return _load_config()


@pytest.fixture(autouse=True)
def reset_singletons(config):
    """Reset config singletons so tests don't leak state."""
    yield
    if hasattr(config.ConfigurationManager, "reset_instance"):
        config.ConfigurationManager.reset_instance()
    if hasattr(config, "AWSSecretsManagerProvider") and hasattr(config.AWSSecretsManagerProvider, "reset_instance"):
        config.AWSSecretsManagerProvider.reset_instance()
    # Clear global _config_manager so next get_config_manager can create fresh
    if hasattr(config, "_config_manager"):
        config._config_manager = None


class TestLocalEnvironmentProvider:
    def test_json_string_secret_id(self, config):
        provider = config.LocalEnvironmentProvider()
        secret_id = json.dumps({"SQS_QUEUE_NAME_PATTERN": "my-queue-{env}"})
        assert provider.get(secret_id, key="SQS_QUEUE_NAME_PATTERN") == "my-queue-{env}"

    def test_env_var_value(self, config):
        provider = config.LocalEnvironmentProvider()
        with patch.dict("os.environ", {"MY_SECRET": "hello"}, clear=False):
            assert provider.get("MY_SECRET") == "hello"

    def test_env_var_json_with_key(self, config):
        provider = config.LocalEnvironmentProvider()
        with patch.dict("os.environ", {"SECRET_ARN": json.dumps({"key1": "val1"})}, clear=False):
            assert provider.get("SECRET_ARN", key="key1") == "val1"

    def test_missing_env_returns_default(self, config):
        provider = config.LocalEnvironmentProvider()
        with patch.dict("os.environ", {}, clear=False):
            assert provider.get("NONEXISTENT", default="default") == "default"

    def test_extract_env_var_name_from_arn(self, config):
        provider = config.LocalEnvironmentProvider()
        arn = "arn:aws:secretsmanager:us-east-1:123:secret:my-secret-name-AbCdEf"
        name = provider._extract_env_var_name(arn)
        assert "MY_SECRET_NAME" in name or "my_secret_name" in name.upper()


class TestGetSecret:
    def test_get_secret_uses_default_with_local_provider(self, config):
        config.ConfigurationManager.reset_instance()
        config._config_manager = None
        with patch.dict("os.environ", {"ENVIRONMENT": "local", "MISSING_VAR": ""}, clear=False):
            config.get_config_manager(provider=config.LocalEnvironmentProvider())
            out = config.get_secret("MISSING_VAR", key="X", default="fallback")
            assert out == "fallback"


class TestAWSSecretsManagerProvider:
    def test_non_arn_returns_secret_id(self, config):
        config.AWSSecretsManagerProvider.reset_instance()
        mock_client = MagicMock()
        provider = config.AWSSecretsManagerProvider.__new__(config.AWSSecretsManagerProvider)
        provider._cache = {}
        provider._cache_lock = __import__("threading").RLock()
        provider._default_ttl = 300
        provider._client = mock_client
        provider._refresh_on_error = True
        provider._initialized = True
        # Not an ARN
        assert provider.get("plain-text-id", default="x") == "plain-text-id"

    def test_get_fetches_and_caches(self, config):
        config.AWSSecretsManagerProvider.reset_instance()
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": json.dumps({"key1": "val1"})}
        provider = config.AWSSecretsManagerProvider(client=mock_client)
        with patch.dict("os.environ", {"ENVIRONMENT": "prod"}, clear=False):
            out = provider.get("arn:aws:secretsmanager:us-east-1:123:secret:name-xxxx", key="key1", default=None)
        assert out == "val1"
        mock_client.get_secret_value.assert_called_once()

    def test_invalidate_clears_cache(self, config):
        config.AWSSecretsManagerProvider.reset_instance()
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {"SecretString": "cached"}
        provider = config.AWSSecretsManagerProvider(client=mock_client)
        provider.get("arn:aws:secretsmanager:us-east-1:123:secret:name-xxxx")
        info = provider.get_cache_info()
        assert info["total_cached"] >= 1
        provider.invalidate("arn:aws:secretsmanager:us-east-1:123:secret:name-xxxx")
        info2 = provider.get_cache_info()
        assert info2["total_cached"] == 0
