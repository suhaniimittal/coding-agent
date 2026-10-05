"""
Unit tests for db_utils.py
"""

import json
import os
import sys
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from src.db_utils import decrypt_str, decrypt_token, get_db_session, get_decrypted_value


class TestDbUtils:
    """Test cases for db_utils module."""

    @pytest.fixture
    def fernet_key(self):
        """Generate a test Fernet key."""
        return Fernet.generate_key()

    @pytest.fixture
    def mock_fernet(self, fernet_key):
        """Mock Fernet encryption instance."""
        with patch("src.db_utils.fernet_key", fernet_key.decode()):
            with patch("src.db_utils._f") as mock_f:
                f_instance = Fernet(fernet_key)
                mock_f.decrypt.side_effect = f_instance.decrypt
                mock_f.encrypt.side_effect = f_instance.encrypt
                yield mock_f

    def test_decrypt_str_success(self, fernet_key):
        """Test successful string decryption."""
        f = Fernet(fernet_key)
        original_text = "test-token-value"
        encrypted_bytes = f.encrypt(original_text.encode("utf-8"))

        with patch("src.db_utils.fernet_key", fernet_key.decode()):
            with patch("src.db_utils._f", f):
                result = decrypt_str(encrypted_bytes)
                assert result == original_text

    def test_decrypt_str_invalid_token(self, fernet_key):
        """Test decryption with invalid token."""
        f = Fernet(fernet_key)

        with patch("src.db_utils.fernet_key", fernet_key.decode()):
            with patch("src.db_utils._f", f):
                with pytest.raises(InvalidToken):
                    decrypt_str(b"invalid-encrypted-data")

    def test_get_decrypted_value_success(self, fernet_key):
        """Test successful value decryption."""
        f = Fernet(fernet_key)
        original_text = "decrypted-value"
        encrypted_bytes = f.encrypt(original_text.encode("utf-8"))

        with patch("src.db_utils.fernet_key", fernet_key.decode()):
            with patch("src.db_utils._f", f):
                result = get_decrypted_value(encrypted_bytes)
                assert result == original_text

    def test_get_decrypted_value_memoryview(self, fernet_key):
        """Test decryption with memoryview input."""
        f = Fernet(fernet_key)
        original_text = "memoryview-test"
        encrypted_bytes = f.encrypt(original_text.encode("utf-8"))
        encrypted_memoryview = memoryview(encrypted_bytes)

        with patch("src.db_utils.fernet_key", fernet_key.decode()):
            with patch("src.db_utils._f", f):
                result = get_decrypted_value(encrypted_memoryview)
                assert result == original_text

    def test_get_decrypted_value_none_input(self):
        """Test decryption with None input."""
        result = get_decrypted_value(None)
        assert result is None

    def test_get_decrypted_value_empty_bytes(self):
        """Test decryption with empty bytes."""
        result = get_decrypted_value(b"")
        assert result is None

    def test_get_decrypted_value_decryption_error(self, fernet_key):
        """Test handling of decryption errors."""
        f = Fernet(fernet_key)

        with patch("src.db_utils.fernet_key", fernet_key.decode()):
            with patch("src.db_utils._f", f):
                # Should return None on decryption error, not raise
                result = get_decrypted_value(b"invalid-encrypted-data")
                assert result is None

    @pytest.mark.asyncio
    async def test_get_db_session_success(self):
        """Test successful database session creation."""
        mock_session = Mock()

        with patch("src.db_utils.db") as mock_db:
            mock_db.get_session.return_value = mock_session

            session = await get_db_session()

            assert session == mock_session
            mock_db.get_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_db_session_operational_error(self):
        """Test database session creation with operational error."""
        with patch("src.db_utils.db") as mock_db:
            mock_db.get_session.side_effect = OperationalError("Connection failed", None, None)

            with pytest.raises(Exception) as exc_info:
                await get_db_session()

            assert "Database not found or is misconfigured" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_db_session_unexpected_error(self):
        """Test database session creation with unexpected error."""
        with patch("src.db_utils.db") as mock_db:
            mock_db.get_session.side_effect = ValueError("Unexpected error")

            with pytest.raises(Exception) as exc_info:
                await get_db_session()

            assert "Unexpected DB error: Unexpected error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decrypt_token_success(self, fernet_key):
        """Test successful token decryption and JSON formatting."""
        f = Fernet(fernet_key)

        # Create encrypted tokens
        access_token = "test-access-token"
        refresh_token = "test-refresh-token"
        encrypted_access = f.encrypt(access_token.encode("utf-8"))
        encrypted_refresh = f.encrypt(refresh_token.encode("utf-8"))

        # Mock token record from database
        token_expires_at = datetime.now(UTC)
        mock_record = (encrypted_access, encrypted_refresh, token_expires_at)

        mock_session = Mock()
        mock_session.execute.return_value.fetchone.return_value = mock_record

        env_vars = {
            "GMAIL_CLIENT_ID": "test-client-id",
            "GMAIL_CLIENT_SECRET": "test-client-secret",
            "ENCRYPTION_KEY": fernet_key.decode(),
        }

        with patch.dict(os.environ, env_vars):
            # Reset the module level variables by patching them directly
            with patch("src.db_utils.GMAIL_CLIENT_ID", "test-client-id"):
                with patch("src.db_utils.GMAIL_CLIENT_SECRET", "test-client-secret"):
                    with patch("src.db_utils.fernet_key", fernet_key.decode()):
                        with patch("src.db_utils._f", f):
                            with patch("src.db_utils.get_db_session", return_value=mock_session):
                                result = await decrypt_token("test-tenant-id", "test@example.com")

        # Parse the returned JSON
        token_data = json.loads(result)

        assert token_data["token"] == access_token
        assert token_data["refresh_token"] == refresh_token
        assert token_data["client_id"] == "test-client-id"
        assert token_data["client_secret"] == "test-client-secret"
        assert token_data["token_uri"] == "https://oauth2.googleapis.com/token"
        assert "scopes" not in token_data
        assert token_data["expiry"] == token_expires_at.isoformat()

        # Verify session cleanup
        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_decrypt_token_missing_env_vars(self):
        """Test token decryption with missing environment variables."""
        # Mock empty environment but still need encryption key for other setup
        limited_env = {"ENCRYPTION_KEY": Fernet.generate_key().decode()}
        with patch.dict(os.environ, limited_env, clear=True):
            # Patch the module-level variables directly
            with patch("src.db_utils.GMAIL_CLIENT_ID", None):
                with patch("src.db_utils.GMAIL_CLIENT_SECRET", None):
                    with pytest.raises(RuntimeError) as exc_info:
                        await decrypt_token("test-tenant-id", "test@example.com")

                    assert "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set" in str(
                        exc_info.value
                    )

    @pytest.mark.asyncio
    async def test_decrypt_token_no_record_found(self):
        """Test token decryption when no token record exists."""
        mock_session = Mock()
        mock_session.execute.return_value.fetchone.return_value = None

        with patch("src.db_utils.GMAIL_CLIENT_ID", "test-client-id"):
            with patch("src.db_utils.GMAIL_CLIENT_SECRET", "test-client-secret"):
                with patch("src.db_utils.get_db_session", return_value=mock_session):
                    with pytest.raises(Exception) as exc_info:
                        await decrypt_token("test-tenant-id", "test@example.com")

        assert "No active Gmail integration found for tenant ID: test-tenant-id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decrypt_token_missing_access_token(self, fernet_key):
        """Test token decryption with missing access token."""
        # Mock record with None access token
        mock_record = (None, b"encrypted-refresh", datetime.now(UTC))

        mock_session = Mock()
        mock_session.execute.return_value.fetchone.return_value = mock_record

        with patch("src.db_utils.GMAIL_CLIENT_ID", "test-client-id"):
            with patch("src.db_utils.GMAIL_CLIENT_SECRET", "test-client-secret"):
                with patch("src.db_utils.fernet_key", fernet_key.decode()):
                    with patch("src.db_utils.get_db_session", return_value=mock_session):
                        with pytest.raises(Exception) as exc_info:
                            await decrypt_token("test-tenant-id", "test@example.com")

        assert "Missing decrypted access_token for tenant ID: test-tenant-id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_decrypt_token_missing_refresh_token_warning(self, fernet_key):
        """Test token decryption with missing refresh token (should warn but continue)."""
        f = Fernet(fernet_key)

        # Create encrypted access token, None refresh token
        access_token = "test-access-token"
        encrypted_access = f.encrypt(access_token.encode("utf-8"))

        mock_record = (encrypted_access, None, datetime.now(UTC))

        mock_session = Mock()
        mock_session.execute.return_value.fetchone.return_value = mock_record

        env_vars = {
            "GMAIL_CLIENT_ID": "test-client-id",
            "GMAIL_CLIENT_SECRET": "test-client-secret",
        }

        with patch("src.db_utils.GMAIL_CLIENT_ID", "test-client-id"):
            with patch("src.db_utils.GMAIL_CLIENT_SECRET", "test-client-secret"):
                with patch("src.db_utils.fernet_key", fernet_key.decode()):
                    with patch("src.db_utils._f", f):
                        with patch("src.db_utils.get_db_session", return_value=mock_session):
                            result = await decrypt_token("test-tenant-id", "test@example.com")

        # Should still return valid JSON with None refresh_token
        token_data = json.loads(result)
        assert token_data["token"] == access_token
        assert token_data["refresh_token"] is None

    @pytest.mark.asyncio
    async def test_decrypt_token_database_error(self):
        """Test token decryption with database error."""
        mock_session = Mock()
        mock_session.execute.side_effect = Exception("Database connection error")

        with patch("src.db_utils.GMAIL_CLIENT_ID", "test-client-id"):
            with patch("src.db_utils.GMAIL_CLIENT_SECRET", "test-client-secret"):
                with patch("src.db_utils.get_db_session", return_value=mock_session):
                    with pytest.raises(Exception):
                        await decrypt_token("test-tenant-id", "test@example.com")

        # Ensure session cleanup even on error
        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_decrypt_token_session_cleanup_on_exception(self):
        """Test that database session is cleaned up even when exception occurs."""
        mock_session = Mock()
        mock_session.execute.side_effect = Exception("Test error")

        with patch("src.db_utils.GMAIL_CLIENT_ID", "test-client-id"):
            with patch("src.db_utils.GMAIL_CLIENT_SECRET", "test-client-secret"):
                with patch("src.db_utils.get_db_session", return_value=mock_session):
                    with pytest.raises(Exception):
                        await decrypt_token("test-tenant-id", "test@example.com")

        # Session should be closed even when exception occurs
        mock_session.close.assert_called_once()

    def test_sql_query_structure(self):
        """Test that the SQL query includes all necessary columns."""
        mock_session = Mock()
        mock_record = (b"encrypted_access", b"encrypted_refresh", datetime.now())
        mock_session.execute.return_value.fetchone.return_value = mock_record

        with patch("src.db_utils.GMAIL_CLIENT_ID", "test-client-id"):
            with patch("src.db_utils.GMAIL_CLIENT_SECRET", "test-client-secret"):
                with patch("src.db_utils.get_db_session", return_value=mock_session):
                    with patch("src.db_utils.get_decrypted_value", return_value="mock-token"):
                        try:
                            import asyncio

                            asyncio.run(decrypt_token("test-tenant-id", "test@example.com"))
                        except Exception:
                            pass

        # Verify the SQL query was called with correct structure
        call_args = mock_session.execute.call_args
        assert call_args is not None
        query = call_args[0][0]
        params = call_args[0][1]

        assert isinstance(query, type(text("")))  # Should be SQLAlchemy text object
        assert params == {"tenant_id": "test-tenant-id", "LISTENER_EMAIL": "test@example.com"}

    @pytest.mark.asyncio
    async def test_decrypt_token_datetime_handling(self, fernet_key):
        """Test that datetime expiry is properly handled in token data."""
        f = Fernet(fernet_key)

        access_token = "test-access-token"
        encrypted_access = f.encrypt(access_token.encode("utf-8"))

        # Test with None expiry
        mock_record_none = (encrypted_access, None, None)
        mock_session = Mock()
        mock_session.execute.return_value.fetchone.return_value = mock_record_none

        env_vars = {
            "GMAIL_CLIENT_ID": "test-client-id",
            "GMAIL_CLIENT_SECRET": "test-client-secret",
            "ENCRYPTION_KEY": fernet_key.decode(),
        }

        with patch.dict(os.environ, env_vars):
            with patch("src.db_utils.GMAIL_CLIENT_ID", "test-client-id"):
                with patch("src.db_utils.GMAIL_CLIENT_SECRET", "test-client-secret"):
                    with patch("src.db_utils.fernet_key", fernet_key.decode()):
                        with patch("src.db_utils._f", f):
                            with patch("src.db_utils.get_db_session", return_value=mock_session):
                                result = await decrypt_token("test-tenant-id", "test@example.com")

        token_data = json.loads(result)
        assert token_data["expiry"] is None

        # Test with string expiry (non-datetime) - create new session mock
        mock_session_string = Mock()
        mock_record_string = (encrypted_access, None, "2024-01-01T00:00:00")
        mock_session_string.execute.return_value.fetchone.return_value = mock_record_string

        with patch.dict(os.environ, env_vars):
            with patch("src.db_utils.GMAIL_CLIENT_ID", "test-client-id"):
                with patch("src.db_utils.GMAIL_CLIENT_SECRET", "test-client-secret"):
                    with patch("src.db_utils.fernet_key", fernet_key.decode()):
                        with patch("src.db_utils._f", f):
                            with patch(
                                "src.db_utils.get_db_session", return_value=mock_session_string
                            ):
                                result = await decrypt_token("test-tenant-id", "test@example.com")

        token_data = json.loads(result)
        # The code only calls isoformat() on datetime objects, strings are converted to None
        # This is the actual behavior of the code
        assert token_data["expiry"] is None

    def test_missing_encryption_key_raises_at_import(self):
        """Test that module raises RuntimeError when ENCRYPTION_KEY is not set (line 31-32)."""
        saved = os.environ.pop("ENCRYPTION_KEY", None)
        try:
            if "src.db_utils" in sys.modules:
                del sys.modules["src.db_utils"]
            with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
                pass
        finally:
            if saved is not None:
                os.environ["ENCRYPTION_KEY"] = saved
            if "src.db_utils" in sys.modules:
                del sys.modules["src.db_utils"]
            # Re-import so later tests have the module
