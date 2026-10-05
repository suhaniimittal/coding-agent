"""
Configuration management with Strategy pattern for local and AWS environments.

This module provides a flexible configuration system that:
- Uses environment variables when ENVIRONMENT="local"
- Uses AWS Secrets Manager for other environments
- Implements the Strategy pattern for easy testing and extension
"""

import json
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Constants
AWS_SERVICE_SECRETS_MANAGER = "secretsmanager"
SECRETS_MANAGER_ARN_PREFIX = "arn:aws:secretsmanager:"
RESPONSE_KEY_SECRET_STRING = "SecretString"
RESPONSE_KEY_SECRET_BINARY = "SecretBinary"
ERROR_KEY = "Error"
ERROR_CODE_KEY = "Code"
DEFAULT_ERROR_CODE = "Unknown"
DEFAULT_TTL_SECONDS = 300  # 5 minutes
LOG_SECRET_ID_MAX_LENGTH = 50


@dataclass
class CachedSecret:
    """Represents a cached secret with its value and expiration time."""

    value: Any
    expires_at: float


# ============================================================================
# Strategy Pattern: Configuration Providers
# ============================================================================


class ConfigurationProvider(ABC):
    """
    Abstract base class for configuration providers.

    This defines the interface that all configuration providers must implement,
    allowing the system to switch between different configuration sources.
    """

    @abstractmethod
    def get(
        self,
        secret_id: str,
        key: str | None = None,
        ttl: int | None = None,
        default: Any = None,
    ) -> Any:
        """
        Get a configuration value.

        Args:
            secret_id: Identifier for the configuration (ARN, env var name, etc.)
            key: Optional key to extract from a JSON configuration
            ttl: Optional TTL for caching
            default: Default value if not found

        Returns:
            The configuration value
        """
        pass

    @abstractmethod
    def refresh(self, secret_id: str, ttl: int | None = None) -> Any:
        """Force refresh a configuration value."""
        pass

    @abstractmethod
    def invalidate(self, secret_id: str | None = None) -> None:
        """Invalidate cached configuration values."""
        pass


class LocalEnvironmentProvider(ConfigurationProvider):
    """
    Configuration provider that reads from environment variables.

    This provider is used when ENVIRONMENT="local" for local development.
    It supports both simple string values and JSON-encoded environment variables.
    """

    def __init__(self):
        """Initialize the local environment provider."""
        logger.info("Using LocalEnvironmentProvider for configuration")

    def get(
        self,
        secret_id: str,
        key: str | None = None,
        ttl: int | None = None,
        default: Any = None,
    ) -> Any:
        """
        Get configuration from environment variables.

        For ARN-formatted secret_ids, extracts the secret name and looks for
        a corresponding environment variable. Supports JSON parsing.

        Args:
            secret_id: Environment variable name, ARN, or JSON string
            key: Optional key to extract from JSON value
            ttl: Ignored for environment variables
            default: Default value if not found

        Returns:
            The configuration value from environment
        """
        # Check if secret_id is already a JSON string (not an ARN or env var name)
        if secret_id and secret_id.strip().startswith("{"):
            # It's a JSON string directly - parse it
            try:
                parsed_value = json.loads(secret_id)
                logger.debug("Parsed secret_id as direct JSON string")
                return self._extract_value(parsed_value, key, default)
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Failed to parse secret_id as JSON: {secret_id[:50]}...")
                return default

        # Extract env var name from ARN if needed
        env_var_name = self._extract_env_var_name(secret_id)

        # Get from environment
        value = os.environ.get(env_var_name)

        if value is None:
            logger.debug(f"Environment variable '{env_var_name}' not found, using default")
            return default

        # Try to parse as JSON
        try:
            parsed_value = json.loads(value)
            logger.debug(f"Parsed '{env_var_name}' as JSON")
            return self._extract_value(parsed_value, key, default)
        except (json.JSONDecodeError, TypeError):
            # Not JSON, return as string
            if key is not None:
                logger.warning(f"Cannot extract key '{key}' from non-JSON env var '{env_var_name}'")
                return default
            return value

    def _extract_env_var_name(self, secret_id: str) -> str:
        """
        Extract environment variable name from secret_id.

        If secret_id is an ARN, extracts the secret name.
        Otherwise, returns the secret_id as-is.

        Args:
            secret_id: Secret identifier (ARN or env var name)

        Returns:
            Environment variable name
        """
        if secret_id.startswith(SECRETS_MANAGER_ARN_PREFIX):
            # Extract secret name from ARN
            # Format: arn:aws:secretsmanager:region:account:secret:name-XXXXXX
            parts = secret_id.split(":")
            if len(parts) >= 7:
                # Get the secret name and remove the random suffix
                secret_name = parts[6].rsplit("-", 1)[0]
                # Convert to uppercase env var format
                return secret_name.upper().replace("-", "_")

        return secret_id

    def _extract_value(self, value: Any, key: str | None, default: Any) -> Any:
        """Extract a specific key from a value if requested."""
        if key is None:
            return value

        if isinstance(value, dict):
            return value.get(key, default)

        logger.warning(f"Cannot extract key '{key}' from non-dict value")
        return default

    def refresh(self, secret_id: str, ttl: int | None = None) -> Any:
        """
        Refresh configuration (no-op for environment variables).

        Environment variables don't need refreshing, so this just calls get().
        """
        return self.get(secret_id, ttl=ttl)

    def invalidate(self, secret_id: str | None = None) -> None:
        """
        Invalidate cache (no-op for environment variables).

        Environment variables don't have a cache to invalidate.
        """
        logger.debug("Invalidate called on LocalEnvironmentProvider (no-op)")


class AWSSecretsManagerProvider(ConfigurationProvider):
    """
    Configuration provider that reads from AWS Secrets Manager.

    This provider is used for non-local environments and includes:
    - TTL-based caching to minimize API calls
    - Thread-safe operations
    - Automatic JSON parsing
    - Stale value fallback on errors
    """

    _instance: Optional["AWSSecretsManagerProvider"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs) -> "AWSSecretsManagerProvider":
        """Singleton pattern to ensure one cache across the Lambda container."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        default_ttl: int = DEFAULT_TTL_SECONDS,
        client: Any | None = None,
        refresh_on_error: bool = True,
    ):
        """
        Initialize the AWS Secrets Manager provider.

        Args:
            default_ttl: Default time-to-live in seconds for cached secrets
            client: Optional boto3 secretsmanager client (useful for testing)
            refresh_on_error: If True, attempt to refresh on error; if False, return cached value
        """
        if self._initialized:
            return

        import boto3

        self._cache: dict[str, CachedSecret] = {}
        self._cache_lock = threading.RLock()
        self._default_ttl = default_ttl
        self._client = client or boto3.client(AWS_SERVICE_SECRETS_MANAGER)
        self._refresh_on_error = refresh_on_error
        self._initialized = True

        logger.info(f"Using AWSSecretsManagerProvider with TTL={default_ttl}s")

    def _fetch_secret(self, secret_id: str) -> Any:
        """
        Fetch a secret from AWS Secrets Manager.

        Args:
            secret_id: The ARN or name of the secret

        Returns:
            The secret value (parsed JSON if applicable, otherwise raw string)

        Raises:
            ClientError: If the secret cannot be fetched
        """
        logger.info(
            f"Fetching secret from Secrets Manager: {secret_id[:LOG_SECRET_ID_MAX_LENGTH]}..."
        )

        response = self._client.get_secret_value(SecretId=secret_id)

        if RESPONSE_KEY_SECRET_STRING in response:
            secret_value = response[RESPONSE_KEY_SECRET_STRING]
            # Try to parse as JSON
            try:
                return json.loads(secret_value)
            except json.JSONDecodeError:
                return secret_value
        elif RESPONSE_KEY_SECRET_BINARY in response:
            return response[RESPONSE_KEY_SECRET_BINARY]
        else:
            raise ValueError(
                f"Secret {secret_id} has no {RESPONSE_KEY_SECRET_STRING} or "
                f"{RESPONSE_KEY_SECRET_BINARY}"
            )

    def _is_expired(self, cached: CachedSecret) -> bool:
        """Check if a cached secret has expired."""
        return time.time() >= cached.expires_at

    def get(
        self,
        secret_id: str,
        key: str | None = None,
        ttl: int | None = None,
        default: Any = None,
    ) -> Any:
        """
        Get a secret from cache or fetch from Secrets Manager if not cached or expired.

        Args:
            secret_id: The ARN or name of the secret in Secrets Manager
            key: Optional key to extract from a JSON secret
            ttl: Optional TTL override for this specific secret
            default: Default value to return if secret/key not found

        Returns:
            The secret value, or default if not found
        """
        # Handle non-ARN values (plain text secrets for backward compatibility)
        if not secret_id.startswith(SECRETS_MANAGER_ARN_PREFIX):
            logger.debug("Treating as plain text secret (not an ARN)")
            return secret_id

        cache_ttl = ttl if ttl is not None else self._default_ttl

        with self._cache_lock:
            cached = self._cache.get(secret_id)

            # Return cached value if valid and not expired
            if cached is not None and not self._is_expired(cached):
                logger.debug(f"Cache hit for secret: {secret_id[:LOG_SECRET_ID_MAX_LENGTH]}...")
                return self._extract_value(cached.value, key, default)

            # Need to fetch/refresh the secret
            try:
                from botocore.exceptions import ClientError

                secret_value = self._fetch_secret(secret_id)
                self._cache[secret_id] = CachedSecret(
                    value=secret_value,
                    expires_at=time.time() + cache_ttl,
                )
                logger.info(
                    f"Cached secret with TTL={cache_ttl}s: {secret_id[:LOG_SECRET_ID_MAX_LENGTH]}."
                )
                return self._extract_value(secret_value, key, default)

            except ClientError as e:
                error_code = e.response.get(ERROR_KEY, {}).get(ERROR_CODE_KEY, DEFAULT_ERROR_CODE)
                logger.error(f"Error fetching secret {secret_id}: {error_code}")

                # If we have a cached value and refresh_on_error is False, return stale value
                if cached is not None and not self._refresh_on_error:
                    logger.warning(
                        f"Returning stale cached value for: {secret_id[:LOG_SECRET_ID_MAX_LENGTH]}."
                    )
                    return self._extract_value(cached.value, key, default)

                raise

            except Exception as e:
                logger.error(f"Unexpected error fetching secret: {e}", exc_info=True)

                if cached is not None and not self._refresh_on_error:
                    logger.warning(
                        f"Returning stale cached value for: {secret_id[:LOG_SECRET_ID_MAX_LENGTH]}."
                    )
                    return self._extract_value(cached.value, key, default)

                raise

    def _extract_value(self, secret_value: Any, key: str | None, default: Any) -> Any:
        """Extract a specific key from a secret value if requested."""
        if key is None:
            return secret_value

        if isinstance(secret_value, dict):
            return secret_value.get(key, default)

        logger.warning(f"Cannot extract key '{key}' from non-dict secret")
        return default

    def refresh(self, secret_id: str, ttl: int | None = None) -> Any:
        """
        Force refresh a secret from Secrets Manager.

        Args:
            secret_id: The ARN or name of the secret
            ttl: Optional TTL override

        Returns:
            The refreshed secret value
        """
        cache_ttl = ttl if ttl is not None else self._default_ttl

        with self._cache_lock:
            # Remove from cache to force refresh
            self._cache.pop(secret_id, None)

        logger.info(f"Forcing refresh for secret: {secret_id[:LOG_SECRET_ID_MAX_LENGTH]}...")
        return self.get(secret_id, ttl=cache_ttl)

    def invalidate(self, secret_id: str | None = None) -> None:
        """
        Invalidate cached secrets.

        Args:
            secret_id: Optional specific secret to invalidate. If None, invalidates all.
        """
        with self._cache_lock:
            if secret_id:
                self._cache.pop(secret_id, None)
                logger.info(f"Invalidated cache for: {secret_id[:LOG_SECRET_ID_MAX_LENGTH]}...")
            else:
                self._cache.clear()
                logger.info("Invalidated all cached secrets")

    def get_cache_info(self) -> dict[str, Any]:
        """
        Get information about the current cache state.

        Returns:
            dict with cache statistics
        """
        with self._cache_lock:
            now = time.time()
            return {
                "total_cached": len(self._cache),
                "default_ttl": self._default_ttl,
                "secrets": {
                    secret_id[:LOG_SECRET_ID_MAX_LENGTH]: {
                        "expires_in": max(0, int(cached.expires_at - now)),
                        "is_expired": self._is_expired(cached),
                    }
                    for secret_id, cached in self._cache.items()
                },
            }

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance. Useful for testing."""
        with cls._lock:
            cls._instance = None


# ============================================================================
# Configuration Manager (Context in Strategy Pattern)
# ============================================================================


class ConfigurationManager:
    """
    Configuration manager that selects the appropriate provider based on environment.

    This is the Context in the Strategy pattern. It delegates to the appropriate
    ConfigurationProvider based on the ENVIRONMENT variable.
    """

    _instance: Optional["ConfigurationManager"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs) -> "ConfigurationManager":
        """Singleton pattern to ensure consistent configuration across the application."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, provider: ConfigurationProvider | None = None):
        """
        Initialize the configuration manager.

        Args:
            provider: Optional explicit provider (useful for testing)
        """
        if self._initialized:
            return

        environment = os.environ.get("ENVIRONMENT", "").lower()

        if provider:
            # Explicit provider (for testing)
            self._provider = provider
            logger.info(f"Using explicit configuration provider: {type(provider).__name__}")
        elif environment == "local":
            # Local development - use environment variables
            self._provider = LocalEnvironmentProvider()
        else:
            # AWS environment - use Secrets Manager
            self._provider = AWSSecretsManagerProvider()

        self._environment = environment
        self._initialized = True

        logger.info(f"ConfigurationManager initialized for environment: {environment or 'not-set'}")

    def get(
        self,
        secret_id: str,
        key: str | None = None,
        ttl: int | None = None,
        default: Any = None,
    ) -> Any:
        """
        Get a configuration value using the selected provider.

        Args:
            secret_id: Configuration identifier (ARN, env var name, etc.)
            key: Optional key to extract from JSON configuration
            ttl: Optional TTL for caching
            default: Default value if not found

        Returns:
            The configuration value
        """
        return self._provider.get(secret_id, key=key, ttl=ttl, default=default)

    def refresh(self, secret_id: str, ttl: int | None = None) -> Any:
        """Force refresh a configuration value."""
        return self._provider.refresh(secret_id, ttl=ttl)

    def invalidate(self, secret_id: str | None = None) -> None:
        """Invalidate cached configuration values."""
        self._provider.invalidate(secret_id)

    @property
    def provider(self) -> ConfigurationProvider:
        """Get the current configuration provider."""
        return self._provider

    @property
    def environment(self) -> str:
        """Get the current environment."""
        return self._environment

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance. Useful for testing."""
        with cls._lock:
            if cls._instance and hasattr(cls._instance, "_provider"):
                # Reset provider instances too
                if isinstance(cls._instance._provider, AWSSecretsManagerProvider):
                    AWSSecretsManagerProvider.reset_instance()
            cls._instance = None


# ============================================================================
# Convenience Functions (Backward Compatibility)
# ============================================================================


_config_manager: ConfigurationManager | None = None


def get_config_manager(provider: ConfigurationProvider | None = None) -> ConfigurationManager:
    """
    Get or create the global ConfigurationManager instance.

    Args:
        provider: Optional explicit provider (useful for testing)

    Returns:
        ConfigurationManager instance
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigurationManager(provider=provider)
    return _config_manager


def get_secret(
    secret_identifier: str,
    key: str | None = None,
    ttl: int | None = None,
    default: Any = None,
) -> Any:
    """
    Convenience function to get a configuration value using the global ConfigurationManager.

    This function maintains backward compatibility with the old API while using
    the new Strategy pattern underneath.

    Args:
        secret_identifier: Configuration identifier (ARN, env var name, etc.)
        key: Optional key to extract from JSON configuration
        ttl: Optional TTL for caching
        default: Default value if not found

    Returns:
        The configuration value
    """
    return get_config_manager().get(secret_identifier, key=key, ttl=ttl, default=default)


# Alias for backward compatibility
SecretsManager = ConfigurationManager
get_secrets_manager = get_config_manager
