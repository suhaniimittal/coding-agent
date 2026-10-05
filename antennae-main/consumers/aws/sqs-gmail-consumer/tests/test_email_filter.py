"""
Unit tests for email_filter.py
"""

import json
import os
from unittest.mock import Mock, mock_open, patch

from src.email_filter import EmailFilter


class TestEmailFilter:
    """Test cases for EmailFilter class."""

    def test_init_loads_config_from_file(self, sample_email_config):
        """Test that EmailFilter correctly loads configuration from file."""
        config_json = json.dumps(sample_email_config)

        with patch("builtins.open", mock_open(read_data=config_json)):
            with patch("os.path.exists", return_value=True):
                filter_obj = EmailFilter()

                assert filter_obj.whitelist_mode is True
                assert "test@example.com" in filter_obj.allowed_senders
                assert "admin@company.com" in filter_obj.allowed_senders
                assert "spam@badactor.com" in filter_obj.blocked_senders
                assert "trusted-domain.com" in filter_obj.allow_domains
                assert "spam-domain.com" in filter_obj.block_domains

    def test_init_loads_config_from_env_variable(self, sample_email_config):
        """Test that EmailFilter loads configuration from environment variable."""
        config_json = json.dumps(sample_email_config)

        with patch.dict(os.environ, {"EMAIL_FILTER_CONFIG": config_json}):
            with patch("os.path.exists", return_value=False):
                filter_obj = EmailFilter()

                assert filter_obj.whitelist_mode is True
                assert "test@example.com" in filter_obj.allowed_senders

    def test_init_falls_back_to_env_vars(self):
        """Test that EmailFilter falls back to individual environment variables."""
        env_vars = {
            "EMAIL_FILTER_WHITELIST_MODE": "true",
            "EMAIL_FILTER_ALLOWED_SENDERS": "user1@example.com,user2@example.com",
            "EMAIL_FILTER_BLOCKED_SENDERS": "spam@example.com",
            "EMAIL_FILTER_ALLOW_DOMAINS": "trusted.com,verified.com",
            "EMAIL_FILTER_BLOCK_DOMAINS": "spam.com",
        }

        # Clear any existing EMAIL_FILTER_CONFIG first
        with patch.dict(os.environ, {"EMAIL_FILTER_CONFIG": ""}, clear=False):
            with patch.dict(os.environ, env_vars, clear=False):
                with patch("os.path.exists", return_value=False):
                    filter_obj = EmailFilter()

                    assert filter_obj.whitelist_mode is True
                    assert "user1@example.com" in filter_obj.allowed_senders
                    assert "user2@example.com" in filter_obj.allowed_senders
                    assert "spam@example.com" in filter_obj.blocked_senders
                    assert "trusted.com" in filter_obj.allow_domains
                    assert "spam.com" in filter_obj.block_domains

    def test_should_process_email_whitelist_mode_allowed_sender(self):
        """Test email processing in whitelist mode with allowed sender."""
        filter_obj = EmailFilter()
        filter_obj.whitelist_mode = True
        filter_obj.allowed_senders = {"test@example.com"}
        filter_obj.blocked_senders = set()
        filter_obj.allow_domains = set()
        filter_obj.block_domains = set()

        result = filter_obj.should_process_email("test@example.com")
        assert result is True

    def test_should_process_email_whitelist_mode_allowed_domain(self):
        """Test email processing in whitelist mode with allowed domain."""
        filter_obj = EmailFilter()
        filter_obj.whitelist_mode = True
        filter_obj.allowed_senders = set()
        filter_obj.blocked_senders = set()
        filter_obj.allow_domains = {"example.com"}
        filter_obj.block_domains = set()

        result = filter_obj.should_process_email("user@example.com")
        assert result is True

    def test_should_process_email_whitelist_mode_not_allowed(self):
        """Test email processing in whitelist mode with non-allowed sender."""
        filter_obj = EmailFilter()
        filter_obj.whitelist_mode = True
        filter_obj.allowed_senders = {"allowed@example.com"}
        filter_obj.blocked_senders = set()
        filter_obj.allow_domains = set()
        filter_obj.block_domains = set()

        result = filter_obj.should_process_email("notallowed@example.com")
        assert result is False

    def test_should_process_email_blocked_sender_takes_precedence(self):
        """Test that blocked senders take precedence over allowed senders."""
        filter_obj = EmailFilter()
        filter_obj.whitelist_mode = True
        filter_obj.allowed_senders = {"test@example.com"}
        filter_obj.blocked_senders = {"test@example.com"}
        filter_obj.allow_domains = set()
        filter_obj.block_domains = set()

        result = filter_obj.should_process_email("test@example.com")
        assert result is False

    def test_should_process_email_blocked_domain_takes_precedence(self):
        """Test that blocked domains take precedence over allowed domains."""
        filter_obj = EmailFilter()
        filter_obj.whitelist_mode = True
        filter_obj.allowed_senders = set()
        filter_obj.blocked_senders = set()
        filter_obj.allow_domains = {"example.com"}
        filter_obj.block_domains = {"example.com"}

        result = filter_obj.should_process_email("user@example.com")
        assert result is False

    def test_should_process_email_blacklist_mode_allowed_by_default(self):
        """Test email processing in blacklist mode - allowed by default."""
        filter_obj = EmailFilter()
        filter_obj.whitelist_mode = False
        filter_obj.allowed_senders = set()
        filter_obj.blocked_senders = set()
        filter_obj.allow_domains = set()
        filter_obj.block_domains = set()

        result = filter_obj.should_process_email("anyone@example.com")
        assert result is True

    def test_should_process_email_blacklist_mode_blocked_sender(self):
        """Test email processing in blacklist mode with blocked sender."""
        filter_obj = EmailFilter()
        filter_obj.whitelist_mode = False
        filter_obj.allowed_senders = set()
        filter_obj.blocked_senders = {"spam@example.com"}
        filter_obj.allow_domains = set()
        filter_obj.block_domains = set()

        result = filter_obj.should_process_email("spam@example.com")
        assert result is False

    def test_should_process_email_empty_sender(self):
        """Test handling of empty sender email."""
        filter_obj = EmailFilter()

        result = filter_obj.should_process_email("")
        assert result is False

        result = filter_obj.should_process_email(None)
        assert result is False

    def test_should_process_email_case_insensitive(self):
        """Test that email filtering is case-insensitive."""
        filter_obj = EmailFilter()
        filter_obj.whitelist_mode = True
        filter_obj.allowed_senders = {"test@example.com"}
        filter_obj.blocked_senders = set()
        filter_obj.allow_domains = set()
        filter_obj.block_domains = set()

        result = filter_obj.should_process_email("TEST@EXAMPLE.COM")
        assert result is True

    def test_extract_domain(self):
        """Test domain extraction from email addresses."""
        filter_obj = EmailFilter()

        assert filter_obj._extract_domain("user@example.com") == "example.com"
        assert filter_obj._extract_domain("test@SUB.DOMAIN.COM") == "sub.domain.com"
        assert filter_obj._extract_domain("invalid-email") is None
        assert filter_obj._extract_domain("") is None

    def test_add_allowed_sender(self):
        """Test dynamically adding allowed senders."""
        filter_obj = EmailFilter()
        filter_obj.allowed_senders = set()

        filter_obj.add_allowed_sender("NEW@EXAMPLE.COM")
        assert "new@example.com" in filter_obj.allowed_senders

    def test_add_blocked_sender(self):
        """Test dynamically adding blocked senders."""
        filter_obj = EmailFilter()
        filter_obj.blocked_senders = set()

        filter_obj.add_blocked_sender("SPAM@EXAMPLE.COM")
        assert "spam@example.com" in filter_obj.blocked_senders

    def test_remove_allowed_sender(self):
        """Test removing allowed senders."""
        filter_obj = EmailFilter()
        filter_obj.allowed_senders = {"test@example.com"}

        filter_obj.remove_allowed_sender("test@example.com")
        assert "test@example.com" not in filter_obj.allowed_senders

        # Should not raise error for non-existent sender
        filter_obj.remove_allowed_sender("nonexistent@example.com")

    def test_remove_blocked_sender(self):
        """Test removing blocked senders."""
        filter_obj = EmailFilter()
        filter_obj.blocked_senders = {"spam@example.com"}

        filter_obj.remove_blocked_sender("spam@example.com")
        assert "spam@example.com" not in filter_obj.blocked_senders

    def test_get_filter_status(self):
        """Test getting current filter configuration status."""
        filter_obj = EmailFilter()
        filter_obj.whitelist_mode = True
        filter_obj.allowed_senders = {"user@example.com"}
        filter_obj.blocked_senders = {"spam@example.com"}
        filter_obj.allow_domains = {"trusted.com"}
        filter_obj.block_domains = {"spam.com"}

        status = filter_obj.get_filter_status()

        assert status["whitelist_mode"] is True
        assert status["allowed_senders_count"] == 1
        assert status["blocked_senders_count"] == 1
        assert status["allow_domains_count"] == 1
        assert status["block_domains_count"] == 1
        assert "user@example.com" in status["allowed_senders"]
        assert "spam@example.com" in status["blocked_senders"]
        assert "trusted.com" in status["allow_domains"]
        assert "spam.com" in status["block_domains"]

    def test_config_file_error_fallback(self):
        """Test fallback to environment variables when config file has errors."""
        with patch("builtins.open", side_effect=json.JSONDecodeError("Invalid JSON", "doc", 0)):
            with patch("os.path.exists", return_value=True):
                with patch.dict(
                    os.environ, {"EMAIL_FILTER_ALLOWED_SENDERS": "fallback@example.com"}
                ):
                    filter_obj = EmailFilter()

                    # Should fall back to env vars
                    assert "fallback@example.com" in filter_obj.allowed_senders

    def test_whitespace_handling(self):
        """Test that whitespace in configuration is properly handled."""
        config = {
            "whitelist_mode": True,
            "allowed_senders": ["  user1@example.com  ", "\tuser2@example.com\n"],
            "allow_domains": ["  domain.com  "],
        }

        filter_obj = EmailFilter()
        filter_obj._parse_config(config)

        assert "user1@example.com" in filter_obj.allowed_senders
        assert "user2@example.com" in filter_obj.allowed_senders
        assert "domain.com" in filter_obj.allow_domains
        # Ensure no whitespace remains
        assert "  user1@example.com  " not in filter_obj.allowed_senders

    def test_init_env_load_exception_uses_defaults(self):
        """Test EmailFilter uses safe defaults when env loading raises."""
        with patch("src.email_filter.EmailFilter._load_from_env_vars", side_effect=RuntimeError("Env error")):
            filter_obj = EmailFilter()
            assert filter_obj.whitelist_mode is True
            assert filter_obj.allowed_senders == set()
            assert filter_obj.blocked_senders == set()
            assert filter_obj.allow_domains == set()
            assert filter_obj.block_domains == set()

    def test_extract_domain_none_returns_none(self):
        """Test _extract_domain with None returns None."""
        filter_obj = EmailFilter()
        assert filter_obj._extract_domain(None) is None

    def test_extract_domain_empty_string_returns_none(self):
        """Test _extract_domain with empty string returns None."""
        filter_obj = EmailFilter()
        assert filter_obj._extract_domain("") is None
        assert filter_obj._extract_domain("   ") is None

    def test_extract_domain_no_at_returns_none(self):
        """Test _extract_domain with no @ returns None."""
        filter_obj = EmailFilter()
        assert filter_obj._extract_domain("invalid-email") is None

    def test_extract_domain_index_error_returns_none(self):
        """Test _extract_domain when split yields no second part (IndexError) returns None."""
        filter_obj = EmailFilter()
        # Object that has '@' in it but split('@') returns single-element list -> [1] raises IndexError
        bad_email = Mock()
        bad_email.__contains__ = lambda self, x: True
        bad_email.split = lambda x: ["only_one"]
        assert filter_obj._extract_domain(bad_email) is None
