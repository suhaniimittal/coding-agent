"""
Tests for config.py configuration management.
"""

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from config import (
    AWSSecretsManagerProvider,
    CachedSecret,
    ConfigurationManager,
    LocalEnvironmentProvider,
    get_config_manager,
    get_secret,
)


class FakeClientError(Exception):
    """Substitute for botocore.exceptions.ClientError."""

    def __init__(self, error_code="AccessDenied"):
        self.response = {"Error": {"Code": error_code}}
        super().__init__(error_code)


@pytest.fixture
def mock_botocore():
    """Patch botocore so ClientError imports inside get() resolve to FakeClientError."""
    mock_exc = MagicMock()
    mock_exc.ClientError = FakeClientError
    mock_mod = MagicMock()
    mock_mod.exceptions = mock_exc
    with patch.dict(sys.modules, {"botocore": mock_mod, "botocore.exceptions": mock_exc}):
        yield


@pytest.fixture
def aws_provider(mock_botocore):
    """Fresh AWSSecretsManagerProvider backed by a mock boto3 client for each test."""
    AWSSecretsManagerProvider.reset_instance()
    mock_client = MagicMock()
    provider = AWSSecretsManagerProvider(client=mock_client)
    yield provider
    AWSSecretsManagerProvider.reset_instance()


# ---------------------------------------------------------------------------
# CachedSecret
# ---------------------------------------------------------------------------


class TestCachedSecret:
    def test_cached_secret_dataclass(self):
        """Test CachedSecret dataclass."""
        secret = CachedSecret(value="test-value", expires_at=1234567890.0)
        assert secret.value == "test-value"
        assert secret.expires_at == 1234567890.0


# ---------------------------------------------------------------------------
# LocalEnvironmentProvider
# ---------------------------------------------------------------------------


class TestLocalEnvironmentProvider:
    def test_init_logs_message(self):
        """Test that LocalEnvironmentProvider logs on init."""
        provider = LocalEnvironmentProvider()
        assert provider is not None

    def test_get_simple_env_var(self):
        """Test getting a simple environment variable."""
        provider = LocalEnvironmentProvider()
        with patch.dict(os.environ, {"MY_SECRET": "secret-value"}):
            result = provider.get("MY_SECRET")
            assert result == "secret-value"

    def test_get_missing_env_var_returns_default(self):
        """Test missing env var returns default."""
        provider = LocalEnvironmentProvider()
        result = provider.get("NONEXISTENT_VAR", default="fallback")
        assert result == "fallback"

    def test_get_json_env_var(self):
        """Test parsing JSON from environment variable."""
        provider = LocalEnvironmentProvider()
        json_value = json.dumps({"key1": "value1", "key2": "value2"})
        with patch.dict(os.environ, {"JSON_SECRET": json_value}):
            result = provider.get("JSON_SECRET", key="key1")
            assert result == "value1"

    def test_get_json_env_var_full_dict(self):
        """Test getting full JSON dict from environment variable."""
        provider = LocalEnvironmentProvider()
        json_value = json.dumps({"key1": "value1"})
        with patch.dict(os.environ, {"JSON_SECRET": json_value}):
            result = provider.get("JSON_SECRET")
            assert result == {"key1": "value1"}

    def test_get_with_arn_extracts_env_var_name(self):
        """Test that ARN is converted to env var name."""
        provider = LocalEnvironmentProvider()
        arn = "arn:aws:secretsmanager:us-east-1:123456:secret:my-secret-name-abc123"
        with patch.dict(os.environ, {"MY_SECRET_NAME": "secret-value"}):
            result = provider.get(arn)
            assert result == "secret-value"

    def test_get_with_direct_json_string(self):
        """Test passing JSON string directly as secret_id."""
        provider = LocalEnvironmentProvider()
        json_str = json.dumps({"token": "abc123"})
        result = provider.get(json_str, key="token")
        assert result == "abc123"

    def test_get_with_invalid_json_direct_returns_default(self):
        """Test invalid JSON direct string returns default."""
        provider = LocalEnvironmentProvider()
        result = provider.get("{invalid json", default="fallback")
        assert result == "fallback"

    def test_get_key_from_non_json_returns_default(self):
        """Test extracting key from non-JSON value returns default."""
        provider = LocalEnvironmentProvider()
        with patch.dict(os.environ, {"PLAIN_VAR": "plain-string"}):
            result = provider.get("PLAIN_VAR", key="some_key", default="fallback")
            assert result == "fallback"

    def test_extract_value_from_non_dict(self):
        """Test _extract_value with non-dict value and key."""
        provider = LocalEnvironmentProvider()
        result = provider._extract_value("string-value", key="some_key", default="fallback")
        assert result == "fallback"

    def test_refresh_calls_get(self):
        """Test refresh delegates to get."""
        provider = LocalEnvironmentProvider()
        with patch.dict(os.environ, {"TEST_VAR": "value"}):
            result = provider.refresh("TEST_VAR")
            assert result == "value"

    def test_invalidate_no_op(self):
        """Test invalidate is a no-op for local provider."""
        provider = LocalEnvironmentProvider()
        provider.invalidate("some_id")
        provider.invalidate()

    def test_extract_env_var_name_with_short_arn_returns_arn(self):
        """ARN with fewer than 7 colon-separated parts returns the raw string."""
        provider = LocalEnvironmentProvider()
        short_arn = "arn:aws:secretsmanager:us-east-1:123456"
        result = provider._extract_env_var_name(short_arn)
        assert result == short_arn

    def test_extract_env_var_name_non_arn_passthrough(self):
        """Non-ARN secret_id is returned as-is."""
        provider = LocalEnvironmentProvider()
        assert provider._extract_env_var_name("PLAIN_VAR") == "PLAIN_VAR"

    def test_get_direct_json_without_key_returns_dict(self):
        """Direct JSON string without a key returns parsed dict."""
        provider = LocalEnvironmentProvider()
        json_str = json.dumps({"a": 1})
        result = provider.get(json_str)
        assert result == {"a": 1}


# ---------------------------------------------------------------------------
# AWSSecretsManagerProvider — singleton
# ---------------------------------------------------------------------------


class TestAWSSecretsManagerProviderSingleton:
    def test_singleton_returns_same_instance(self):
        AWSSecretsManagerProvider.reset_instance()
        mock_client = MagicMock()
        p1 = AWSSecretsManagerProvider(client=mock_client)
        p2 = AWSSecretsManagerProvider(client=mock_client)
        assert p1 is p2
        AWSSecretsManagerProvider.reset_instance()

    def test_init_only_runs_once(self):
        """Second __init__ call is ignored; first TTL wins."""
        AWSSecretsManagerProvider.reset_instance()
        mock_client = MagicMock()
        p = AWSSecretsManagerProvider(client=mock_client, default_ttl=100)
        AWSSecretsManagerProvider(client=mock_client, default_ttl=999)
        assert p._default_ttl == 100
        AWSSecretsManagerProvider.reset_instance()

    def test_reset_instance_allows_new_creation(self):
        AWSSecretsManagerProvider.reset_instance()
        p1 = AWSSecretsManagerProvider(client=MagicMock())
        AWSSecretsManagerProvider.reset_instance()
        p2 = AWSSecretsManagerProvider(client=MagicMock())
        assert p1 is not p2
        AWSSecretsManagerProvider.reset_instance()


# ---------------------------------------------------------------------------
# AWSSecretsManagerProvider — _fetch_secret
# ---------------------------------------------------------------------------


class TestAWSSecretsManagerProviderFetchSecret:
    ARN = "arn:aws:secretsmanager:us-east-1:123456789:secret:my-secret-abc123"

    def test_fetches_json_secret_string(self, aws_provider):
        aws_provider._client.get_secret_value.return_value = {"SecretString": '{"key": "value"}'}
        result = aws_provider._fetch_secret(self.ARN)
        assert result == {"key": "value"}

    def test_fetches_plain_string_secret(self, aws_provider):
        aws_provider._client.get_secret_value.return_value = {"SecretString": "plain-secret"}
        result = aws_provider._fetch_secret(self.ARN)
        assert result == "plain-secret"

    def test_fetches_binary_secret(self, aws_provider):
        aws_provider._client.get_secret_value.return_value = {"SecretBinary": b"binary-data"}
        result = aws_provider._fetch_secret(self.ARN)
        assert result == b"binary-data"

    def test_raises_when_no_secret_data(self, aws_provider):
        aws_provider._client.get_secret_value.return_value = {}
        with pytest.raises(ValueError, match="has no SecretString or SecretBinary"):
            aws_provider._fetch_secret(self.ARN)


# ---------------------------------------------------------------------------
# AWSSecretsManagerProvider — _is_expired
# ---------------------------------------------------------------------------


class TestAWSSecretsManagerProviderIsExpired:
    def test_expired_returns_true(self, aws_provider):
        cached = CachedSecret(value="v", expires_at=time.time() - 1)
        assert aws_provider._is_expired(cached) is True

    def test_not_expired_returns_false(self, aws_provider):
        cached = CachedSecret(value="v", expires_at=time.time() + 100)
        assert aws_provider._is_expired(cached) is False


# ---------------------------------------------------------------------------
# AWSSecretsManagerProvider — get
# ---------------------------------------------------------------------------


class TestAWSSecretsManagerProviderGet:
    ARN = "arn:aws:secretsmanager:us-east-1:123456789:secret:my-secret-abc123"

    def test_non_arn_returns_value_as_is(self, aws_provider):
        result = aws_provider.get("plain-text-value")
        assert result == "plain-text-value"

    def test_cache_hit_skips_fetch(self, aws_provider):
        aws_provider._cache[self.ARN] = CachedSecret(
            value={"key": "cached"}, expires_at=time.time() + 100
        )
        result = aws_provider.get(self.ARN, key="key")
        assert result == "cached"
        aws_provider._client.get_secret_value.assert_not_called()

    def test_cache_miss_fetches_and_caches(self, aws_provider):
        aws_provider._cache.clear()
        aws_provider._client.get_secret_value.return_value = {"SecretString": '{"token": "abc"}'}
        result = aws_provider.get(self.ARN, key="token")
        assert result == "abc"
        assert self.ARN in aws_provider._cache

    def test_custom_ttl_is_used(self, aws_provider):
        aws_provider._cache.clear()
        aws_provider._client.get_secret_value.return_value = {"SecretString": '"secret"'}
        aws_provider.get(self.ARN, ttl=600)
        assert aws_provider._cache[self.ARN].expires_at > time.time() + 590

    def test_extract_key_from_fetched_secret(self, aws_provider):
        aws_provider._cache.clear()
        aws_provider._client.get_secret_value.return_value = {
            "SecretString": '{"user": "admin", "pass": "secret"}'
        }
        result = aws_provider.get(self.ARN, key="pass")
        assert result == "secret"

    def test_missing_key_returns_default(self, aws_provider):
        aws_provider._cache.clear()
        aws_provider._client.get_secret_value.return_value = {"SecretString": '{"other": "val"}'}
        result = aws_provider.get(self.ARN, key="missing", default="fallback")
        assert result == "fallback"

    def test_key_from_non_dict_secret_returns_default(self, aws_provider):
        aws_provider._cache.clear()
        aws_provider._client.get_secret_value.return_value = {"SecretString": "plain-string"}
        result = aws_provider.get(self.ARN, key="some_key", default="fallback")
        assert result == "fallback"

    def test_client_error_no_stale_reraises(self, aws_provider):
        aws_provider._cache.clear()
        aws_provider._client.get_secret_value.side_effect = FakeClientError("AccessDenied")
        with pytest.raises(FakeClientError):
            aws_provider.get(self.ARN)

    def test_client_error_stale_refresh_on_error_false_returns_stale(self, aws_provider):
        aws_provider._refresh_on_error = False
        aws_provider._cache[self.ARN] = CachedSecret(
            value={"token": "stale"}, expires_at=time.time() - 1
        )
        aws_provider._client.get_secret_value.side_effect = FakeClientError("Throttling")
        result = aws_provider.get(self.ARN, key="token")
        assert result == "stale"
        aws_provider._refresh_on_error = True

    def test_client_error_stale_refresh_on_error_true_reraises(self, aws_provider):
        aws_provider._refresh_on_error = True
        aws_provider._cache[self.ARN] = CachedSecret(
            value={"token": "stale"}, expires_at=time.time() - 1
        )
        aws_provider._client.get_secret_value.side_effect = FakeClientError("AccessDenied")
        with pytest.raises(FakeClientError):
            aws_provider.get(self.ARN)

    def test_generic_exception_no_stale_reraises(self, aws_provider):
        aws_provider._cache.clear()
        aws_provider._client.get_secret_value.side_effect = RuntimeError("network error")
        with pytest.raises(RuntimeError):
            aws_provider.get(self.ARN)

    def test_generic_exception_stale_refresh_on_error_false_returns_stale(self, aws_provider):
        aws_provider._refresh_on_error = False
        aws_provider._cache[self.ARN] = CachedSecret(
            value="stale-value", expires_at=time.time() - 1
        )
        aws_provider._client.get_secret_value.side_effect = RuntimeError("err")
        result = aws_provider.get(self.ARN)
        assert result == "stale-value"
        aws_provider._refresh_on_error = True

    def test_generic_exception_stale_refresh_on_error_true_reraises(self, aws_provider):
        aws_provider._refresh_on_error = True
        aws_provider._cache[self.ARN] = CachedSecret(
            value="stale-value", expires_at=time.time() - 1
        )
        aws_provider._client.get_secret_value.side_effect = RuntimeError("err")
        with pytest.raises(RuntimeError):
            aws_provider.get(self.ARN)


# ---------------------------------------------------------------------------
# AWSSecretsManagerProvider — refresh
# ---------------------------------------------------------------------------


class TestAWSSecretsManagerProviderRefresh:
    ARN = "arn:aws:secretsmanager:us-east-1:123456789:secret:my-secret-abc123"

    def test_refresh_evicts_cache_and_returns_new_value(self, aws_provider):
        aws_provider._cache[self.ARN] = CachedSecret(value="old", expires_at=time.time() + 100)
        aws_provider._client.get_secret_value.return_value = {"SecretString": '"new-value"'}
        result = aws_provider.refresh(self.ARN)
        assert result == "new-value"
        assert aws_provider._cache[self.ARN].value == "new-value"

    def test_refresh_with_custom_ttl(self, aws_provider):
        aws_provider._cache.pop(self.ARN, None)
        aws_provider._client.get_secret_value.return_value = {"SecretString": '"refreshed"'}
        result = aws_provider.refresh(self.ARN, ttl=120)
        assert result == "refreshed"
        assert aws_provider._cache[self.ARN].expires_at > time.time() + 110


# ---------------------------------------------------------------------------
# AWSSecretsManagerProvider — invalidate
# ---------------------------------------------------------------------------


class TestAWSSecretsManagerProviderInvalidate:
    ARN = "arn:aws:secretsmanager:us-east-1:123456789:secret:my-secret-abc123"

    def test_invalidate_specific_secret(self, aws_provider):
        aws_provider._cache[self.ARN] = CachedSecret(value="v", expires_at=time.time() + 100)
        aws_provider.invalidate(self.ARN)
        assert self.ARN not in aws_provider._cache

    def test_invalidate_all_secrets(self, aws_provider):
        aws_provider._cache["arn:1"] = CachedSecret(value="v1", expires_at=time.time() + 100)
        aws_provider._cache["arn:2"] = CachedSecret(value="v2", expires_at=time.time() + 100)
        aws_provider.invalidate()
        assert len(aws_provider._cache) == 0

    def test_invalidate_nonexistent_does_not_raise(self, aws_provider):
        aws_provider.invalidate("arn:nonexistent")  # must not raise


# ---------------------------------------------------------------------------
# AWSSecretsManagerProvider — get_cache_info
# ---------------------------------------------------------------------------


class TestAWSSecretsManagerProviderGetCacheInfo:
    ARN = "arn:aws:secretsmanager:us-east-1:123456789:secret:my-secret-abc123"

    def test_empty_cache(self, aws_provider):
        aws_provider._cache.clear()
        info = aws_provider.get_cache_info()
        assert info["total_cached"] == 0
        assert info["default_ttl"] == aws_provider._default_ttl
        assert info["secrets"] == {}

    def test_live_entry(self, aws_provider):
        aws_provider._cache.clear()
        aws_provider._cache[self.ARN] = CachedSecret(value="v", expires_at=time.time() + 100)
        info = aws_provider.get_cache_info()
        assert info["total_cached"] == 1
        key = self.ARN[:50]
        assert key in info["secrets"]
        assert info["secrets"][key]["expires_in"] > 0
        assert info["secrets"][key]["is_expired"] is False

    def test_expired_entry(self, aws_provider):
        aws_provider._cache.clear()
        aws_provider._cache[self.ARN] = CachedSecret(value="v", expires_at=time.time() - 1)
        info = aws_provider.get_cache_info()
        key = self.ARN[:50]
        assert info["secrets"][key]["is_expired"] is True
        assert info["secrets"][key]["expires_in"] == 0


# ---------------------------------------------------------------------------
# ConfigurationManager
# ---------------------------------------------------------------------------


class TestConfigurationManager:
    def test_manager_with_provider(self):
        """Test ConfigurationManager with explicit provider."""
        import config

        config.ConfigurationManager.reset_instance()
        provider = LocalEnvironmentProvider()
        manager = ConfigurationManager(provider)
        assert manager._provider == provider
        config.ConfigurationManager.reset_instance()

    def test_get_delegates_to_provider(self):
        """Test get method delegates to provider."""
        import config

        config.ConfigurationManager.reset_instance()
        provider = LocalEnvironmentProvider()
        manager = ConfigurationManager(provider)
        with patch.dict(os.environ, {"TEST_VAR": "test-value"}):
            result = manager.get("TEST_VAR")
            assert result == "test-value"
        config.ConfigurationManager.reset_instance()

    def test_refresh_delegates_to_provider(self):
        """Test refresh method delegates to provider."""
        import config

        config.ConfigurationManager.reset_instance()
        provider = LocalEnvironmentProvider()
        manager = ConfigurationManager(provider)
        with patch.dict(os.environ, {"TEST_VAR": "refreshed"}):
            result = manager.refresh("TEST_VAR")
            assert result == "refreshed"
        config.ConfigurationManager.reset_instance()

    def test_invalidate_delegates_to_provider(self):
        """Test invalidate method delegates to provider."""
        import config

        config.ConfigurationManager.reset_instance()
        provider = LocalEnvironmentProvider()
        manager = ConfigurationManager(provider)
        manager.invalidate("some_key")
        manager.invalidate()
        config.ConfigurationManager.reset_instance()

    def test_provider_property(self):
        import config

        config.ConfigurationManager.reset_instance()
        provider = LocalEnvironmentProvider()
        manager = ConfigurationManager(provider)
        assert manager.provider is provider
        config.ConfigurationManager.reset_instance()

    def test_environment_property(self):
        import config

        config.ConfigurationManager.reset_instance()
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}):
            manager = ConfigurationManager(provider=LocalEnvironmentProvider())
            assert manager.environment == "staging"
        config.ConfigurationManager.reset_instance()

    def test_reset_instance_allows_reinit(self):
        import config

        config.ConfigurationManager.reset_instance()
        provider = LocalEnvironmentProvider()
        m1 = ConfigurationManager(provider=provider)
        config.ConfigurationManager.reset_instance()
        m2 = ConfigurationManager(provider=provider)
        assert m1 is not m2
        config.ConfigurationManager.reset_instance()

    def test_local_env_creates_local_provider(self):
        import config

        config.ConfigurationManager.reset_instance()
        with patch.dict(os.environ, {"ENVIRONMENT": "local"}):
            manager = ConfigurationManager()
            assert isinstance(manager._provider, LocalEnvironmentProvider)
        config.ConfigurationManager.reset_instance()

    def test_aws_env_creates_aws_provider(self, mock_botocore):
        import config

        config.ConfigurationManager.reset_instance()
        AWSSecretsManagerProvider.reset_instance()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            with patch.dict(os.environ, {"ENVIRONMENT": "prod"}):
                manager = ConfigurationManager()
                assert isinstance(manager._provider, AWSSecretsManagerProvider)
        config.ConfigurationManager.reset_instance()
        AWSSecretsManagerProvider.reset_instance()

    def test_reset_instance_also_resets_aws_provider(self, mock_botocore):
        import config

        config.ConfigurationManager.reset_instance()
        AWSSecretsManagerProvider.reset_instance()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            with patch.dict(os.environ, {"ENVIRONMENT": "prod"}):
                ConfigurationManager()
        config.ConfigurationManager.reset_instance()
        assert AWSSecretsManagerProvider._instance is None


# ---------------------------------------------------------------------------
# get_config_manager
# ---------------------------------------------------------------------------


class TestGetConfigManager:
    def test_creates_local_provider_for_local_env(self):
        """Test get_config_manager with local environment."""
        with patch.dict(os.environ, {"ENVIRONMENT": "local"}):
            import config

            config._config_manager = None
            config.ConfigurationManager.reset_instance()
            manager = get_config_manager()
            assert isinstance(manager._provider, LocalEnvironmentProvider)
        config.ConfigurationManager.reset_instance()

    def test_reuses_singleton_manager(self):
        """Test get_config_manager returns same instance."""
        with patch.dict(os.environ, {"ENVIRONMENT": "local"}):
            import config

            config._config_manager = None
            config.ConfigurationManager.reset_instance()
            manager1 = get_config_manager()
            manager2 = get_config_manager()
            assert manager1 is manager2
        config.ConfigurationManager.reset_instance()

    def test_uses_explicit_provider(self):
        """Test get_config_manager with explicit provider."""
        import config

        config._config_manager = None
        config.ConfigurationManager.reset_instance()
        provider = LocalEnvironmentProvider()
        manager = get_config_manager(provider)
        assert isinstance(manager._provider, LocalEnvironmentProvider)
        config.ConfigurationManager.reset_instance()


# ---------------------------------------------------------------------------
# get_secret
# ---------------------------------------------------------------------------


class TestGetSecret:
    def test_get_secret_local_environment(self):
        """Test get_secret with local environment."""
        with patch.dict(os.environ, {"ENVIRONMENT": "local", "MY_SECRET": "local-secret"}):
            import config

            config._config_manager = None
            config.ConfigurationManager.reset_instance()
            result = get_secret("MY_SECRET")
            assert result == "local-secret"
        config.ConfigurationManager.reset_instance()

    def test_get_secret_with_key(self):
        """Test get_secret with key extraction."""
        json_value = json.dumps({"api_key": "secret123", "other": "value"})
        with patch.dict(os.environ, {"ENVIRONMENT": "local", "JSON_SECRET": json_value}):
            import config

            config._config_manager = None
            config.ConfigurationManager.reset_instance()
            result = get_secret("JSON_SECRET", key="api_key")
            assert result == "secret123"
        config.ConfigurationManager.reset_instance()

    def test_get_secret_with_ttl_and_default(self):
        """Test get_secret with TTL and default value."""
        with patch.dict(os.environ, {"ENVIRONMENT": "local"}):
            import config

            config._config_manager = None
            config.ConfigurationManager.reset_instance()
            result = get_secret("NONEXISTENT", ttl=60, default="fallback")
            assert result == "fallback"
        config.ConfigurationManager.reset_instance()


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------


class TestAliases:
    def test_secrets_manager_is_configuration_manager(self):
        from config import ConfigurationManager, SecretsManager

        assert SecretsManager is ConfigurationManager

    def test_get_secrets_manager_is_get_config_manager(self):
        from config import get_config_manager, get_secrets_manager

        assert get_secrets_manager is get_config_manager
