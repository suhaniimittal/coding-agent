"""
Unit tests for gmail_client.py
"""

import base64
import json
from unittest.mock import Mock, patch

import pytest
from googleapiclient.errors import HttpError

from src.gmail_client import GmailAPIClient


class TestGmailAPIClient:
    """Test cases for GmailAPIClient class."""

    @pytest.fixture
    def mock_token_data(self):
        """Mock token data for Gmail API."""
        return {
            "token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "mock-client-id",
            "client_secret": "mock-client-secret",
        }

    @pytest.fixture
    def gmail_client(self, mock_token_data):
        """Create a GmailAPIClient instance for testing."""
        token_json = json.dumps(mock_token_data)

        with patch("src.gmail_client.build") as mock_build:
            mock_service = Mock()
            mock_build.return_value = mock_service

            client = GmailAPIClient(token=token_json, email_address="test@example.com")
            client.service = mock_service
            return client

    def test_init_success(self, mock_token_data):
        """Test successful initialization of GmailAPIClient."""
        token_json = json.dumps(mock_token_data)

        with patch("src.gmail_client.build") as mock_build:
            mock_service = Mock()
            mock_build.return_value = mock_service

            client = GmailAPIClient(token=token_json, email_address="test@example.com")

            assert client.email_address == "test@example.com"
            assert client.service == mock_service
            mock_build.assert_called_once_with("gmail", "v1", credentials=client.creds)

    def test_init_invalid_token_json(self):
        """Test initialization with invalid JSON token."""
        invalid_token = "invalid-json"

        with pytest.raises(json.JSONDecodeError):
            GmailAPIClient(token=invalid_token, email_address="test@example.com")

    def test_init_missing_token_fields(self):
        """Test initialization with incomplete token data."""
        incomplete_token = json.dumps({"token": "access-token"})  # Missing other fields

        with patch("src.gmail_client.build") as mock_build:
            mock_service = Mock()
            mock_build.return_value = mock_service

            # Should still work but with None values for missing fields
            client = GmailAPIClient(token=incomplete_token, email_address="test@example.com")
            assert client.creds.token == "access-token"
            assert client.creds.refresh_token is None

    def test_init_proactive_refresh_when_expired(self, mock_token_data):
        """Test proactive token refresh when creds are expired but refresh_token is present."""
        token_json = json.dumps(mock_token_data)
        with patch("src.gmail_client.Credentials") as MockCreds:
            with patch("src.gmail_client.Request"):
                with patch("src.gmail_client.build", return_value=Mock()):
                    inst = MockCreds.return_value
                    inst.expired = True
                    inst.valid = False
                    inst.refresh_token = "mock-refresh-token"
                    inst.refresh = Mock()
                    GmailAPIClient(token=token_json, email_address="test@example.com")
                    inst.refresh.assert_called_once()

    def test_init_expired_no_refresh_token_warning(self, mock_token_data):
        """Test warning path when creds expired and no refresh_token."""
        token_no_refresh = {k: v for k, v in mock_token_data.items()}
        token_no_refresh["refresh_token"] = None
        token_json = json.dumps(token_no_refresh)
        with patch("src.gmail_client.Credentials") as MockCreds:
            with patch("src.gmail_client.build", return_value=Mock()):
                inst = MockCreds.return_value
                inst.expired = True
                inst.valid = False
                inst.refresh_token = None
                inst.refresh = Mock()
                GmailAPIClient(token=token_json, email_address="test@example.com")
                inst.refresh.assert_not_called()

    def test_get_new_message_id_found(self, gmail_client):
        """Test finding new message ID from history."""
        history_response = {
            "history": [
                {
                    "messagesAdded": [
                        {"message": {"id": "message-123"}},
                        {"message": {"id": "message-456"}},  # Should return the last one
                    ]
                }
            ]
        }

        gmail_client.service.users.return_value.history.return_value.list.return_value.execute.\
            return_value = history_response

        message_id = gmail_client.get_new_message_id("12345")

        assert message_id == "message-456"
        gmail_client.service.users.return_value.history.return_value.list.assert_called_once_with(
            userId="me", startHistoryId="12345"
        )

    def test_get_new_message_id_no_history_fallback(self, gmail_client):
        """Test fallback to latest message when no history found."""
        # No history found
        gmail_client.service.users.return_value.history.return_value.list.return_value.execute.\
            return_value = {
            "history": []
        }

        # Mock fallback to latest message
        messages_response = {"messages": [{"id": "latest-message-123"}]}
        gmail_client.service.users.return_value.messages.return_value.list.return_value.execute.\
            return_value = messages_response

        message_id = gmail_client.get_new_message_id("12345")

        assert message_id == "latest-message-123"

        # Verify fallback call
        gmail_client.service.users.return_value.messages.return_value.list.assert_called_once_with(
            userId="me", maxResults=1, q="in:inbox"
        )

    def test_get_new_message_id_no_messages_found(self, gmail_client):
        """Test when no messages are found at all."""
        # No history
        gmail_client.service.users.return_value.history.return_value.list.return_value.execute.\
            return_value = {
            "history": []
        }

        # No messages in fallback
        gmail_client.service.users.return_value.messages.return_value.list.return_value.execute.\
            return_value = {
            "messages": []
        }

        message_id = gmail_client.get_new_message_id("12345")

        assert message_id is None

    def test_get_new_message_id_api_error(self, gmail_client):
        """Test handling of API errors during fallback."""
        # No history
        gmail_client.service.users.return_value.history.return_value.list.return_value.execute.\
            return_value = {
            "history": []
        }

        # API error in fallback
        gmail_client.service.users.return_value.messages.return_value.list.return_value.execute.\
            side_effect = Exception(
            "API Error"
        )

        message_id = gmail_client.get_new_message_id("12345")

        assert message_id is None

    @pytest.mark.asyncio
    async def test_extract_basic_info_simple_message(self, gmail_client):
        """Test extracting basic info from simple text message."""
        message_response = {
            "payload": {
                "headers": [
                    {"name": "From", "value": "Test User <test@example.com>"},
                    {"name": "Subject", "value": "Test Subject"},
                ],
                "mimeType": "text/plain",
                "body": {
                    "data": base64.urlsafe_b64encode(b"Simple message body").decode().rstrip("=")
                },
            }
        }

        body, sender_email = await gmail_client.extract_basic_info(message=message_response)

        assert body == "Simple message body"
        assert sender_email == "test@example.com"

    @pytest.mark.asyncio
    async def test_extract_basic_info_multipart_message(self, gmail_client):
        """Test extracting basic info from multipart message."""
        message_response = {
            "payload": {
                "headers": [{"name": "From", "value": "Test User <test@example.com>"}],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {
                            "data": base64.urlsafe_b64encode(b"Message with attachment")
                            .decode()
                            .rstrip("=")
                        },
                    },
                ],
            }
        }

        body, sender_email = await gmail_client.extract_basic_info(message=message_response)

        assert body == "Message with attachment"
        assert sender_email == "test@example.com"

    @pytest.mark.asyncio
    async def test_extract_basic_info_sender_email_formats(self, gmail_client):
        """Test extraction of sender email from various formats."""
        test_cases = [
            ("Simple <simple@example.com>", "simple@example.com"),
            ("simple@example.com", "simple@example.com"),
            (
                "No Brackets simple@example.com",
                "No Brackets simple@example.com",
            ),
            (
                "Complex Name <complex.email+tag@subdomain.example.com>",
                "complex.email+tag@subdomain.example.com",
            ),
        ]

        for from_header, expected_email in test_cases:
            message_response = {
                "payload": {
                    "headers": [{"name": "From", "value": from_header}],
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Test").decode().rstrip("=")},
                }
            }

            _, sender_email = await gmail_client.extract_basic_info(message=message_response)

            assert sender_email == expected_email

    @pytest.mark.asyncio
    async def test_extract_basic_info_missing_from_header(self, gmail_client):
        """Test extract_basic_info when From header is missing (returns empty sender)."""
        message_response = {
            "payload": {
                "headers": [{"name": "Subject", "value": "No From"}],
                "mimeType": "text/plain",
                "body": {"data": base64.urlsafe_b64encode(b"Body").decode().rstrip("=")},
            }
        }
        body, sender_email = await gmail_client.extract_basic_info(message=message_response)
        assert body == "Body"
        assert sender_email == ""

    @pytest.mark.asyncio
    async def test_extract_basic_info_multipart_plain_part(self, gmail_client):
        """Test extraction from first-level multipart with text/plain part."""
        message_response = {
            "payload": {
                "headers": [{"name": "From", "value": "test@example.com"}],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {
                            "data": base64.urlsafe_b64encode(b"Nested message")
                            .decode()
                            .rstrip("=")
                        },
                    }
                ],
            }
        }

        body, sender_email = await gmail_client.extract_basic_info(message=message_response)

        assert body == "Nested message"
        assert sender_email == "test@example.com"

    @pytest.mark.asyncio
    async def test_reply_to_email(self, gmail_client):
        """Test sending a reply email (placeholder implementation)."""
        gmail_client.service.users.return_value.messages.return_value.send.return_value.execute.\
            return_value = {
            "id": "sent-message-123"
        }

        # Should not raise exception
        await gmail_client.reply_to_email(
            original_msg_id="original-123",
            sender_email="sender@example.com",
            response_s3_key="s3://bucket/response.pdf",
        )

        # Verify send was called
        gmail_client.service.users.return_value.messages.return_value.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_reply_to_email_api_error(self, gmail_client):
        """Test handling of API errors when sending reply."""
        gmail_client.service.users.return_value.messages.return_value.send.return_value.execute.\
            side_effect = HttpError(
            Mock(status=500), b"Server error"
        )

        with pytest.raises(HttpError):
            await gmail_client.reply_to_email(
                original_msg_id="original-123",
                sender_email="sender@example.com",
                response_s3_key="s3://bucket/response.pdf",
            )

    @pytest.mark.asyncio
    async def test_safe_base64_decode_padding(self, gmail_client):
        """Test base64 decoding handles proper padding via extract_basic_info."""
        message_response = {
            "payload": {
                "headers": [{"name": "From", "value": "test@example.com"}],
                "mimeType": "text/plain",
                "body": {
                    "data": "SGVsbG8="  # "Hello" with proper padding
                },
            }
        }
        body, _ = await gmail_client.extract_basic_info(message=message_response)
        assert body == "Hello"

    @pytest.mark.asyncio
    async def test_fetch_message_success(self, gmail_client):
        """Test _fetch_message returns message dict."""
        msg = {"id": "msg-1", "payload": {"headers": []}}
        gmail_client.service.users.return_value.messages.return_value.get.return_value.execute.return_value = msg
        result = await gmail_client._fetch_message("msg-1")
        assert result == msg
        gmail_client.service.users.return_value.messages.return_value.get.assert_called_once_with(
            userId="test@example.com", id="msg-1", format="full"
        )

    @pytest.mark.asyncio
    async def test_fetch_message_http_error(self, gmail_client):
        """Test _fetch_message raises HttpError on API error."""
        gmail_client.service.users.return_value.messages.return_value.get.return_value.execute.side_effect = HttpError(
            Mock(status=404), b"Not found"
        )
        with pytest.raises(HttpError):
            await gmail_client._fetch_message("msg-1")

    @pytest.mark.asyncio
    async def test_build_body_eml_text_only(self, gmail_client):
        """Test build_body_eml with text/plain only."""
        message = {
            "payload": {
                "headers": [
                    {"name": "From", "value": "a@b.com"},
                    {"name": "To", "value": "c@d.com"},
                    {"name": "Subject", "value": "Sub"},
                ],
                "mimeType": "text/plain",
                "body": {"data": base64.urlsafe_b64encode(b"Hello world").decode().rstrip("=")},
            }
        }
        eml_bytes = await gmail_client.build_body_eml(message=message)
        assert b"Hello world" in eml_bytes
        assert b"From: a@b.com" in eml_bytes or b"From:" in eml_bytes

    @pytest.mark.asyncio
    async def test_build_body_eml_with_html_part(self, gmail_client):
        """Test build_body_eml with nested text/plain and text/html."""
        message = {
            "payload": {
                "headers": [{"name": "From", "value": "a@b.com"}],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"Plain").decode().rstrip("=")}},
                    {"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(b"<p>HTML</p>").decode().rstrip("=")}},
                ],
            }
        }
        eml_bytes = await gmail_client.build_body_eml(message=message)
        assert b"Plain" in eml_bytes
        assert b"<p>HTML</p>" in eml_bytes

    @pytest.mark.asyncio
    async def test_extract_attachments_empty(self, gmail_client):
        """Test extract_attachments with no parts."""
        message = {"payload": {}}
        result = await gmail_client.extract_attachments(message=message, msg_id="m1")
        assert result == []

    @pytest.mark.asyncio
    async def test_extract_attachments_one_file(self, gmail_client):
        """Test extract_attachments fetches one attachment."""
        message = {
            "payload": {
                "parts": [
                    {
                        "filename": "doc.pdf",
                        "body": {"attachmentId": "att-1"},
                    }
                ]
            }
        }
        gmail_client.service.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
            "data": base64.urlsafe_b64encode(b"pdf content").decode().rstrip("=")
        }
        result = await gmail_client.extract_attachments(message=message, msg_id="msg-1")
        assert len(result) == 1
        assert result[0][0] == "doc.pdf"
        assert result[0][1] == b"pdf content"

    @pytest.mark.asyncio
    async def test_extract_attachments_fetch_error_continues(self, gmail_client):
        """Test extract_attachments continues when one attachment fails."""
        message = {
            "payload": {
                "parts": [
                    {"filename": "a.pdf", "body": {"attachmentId": "att-1"}},
                    {"filename": "b.pdf", "body": {"attachmentId": "att-2"}},
                ]
            }
        }
        call_count = [0]
        def execute_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("API error")
            return {"data": base64.urlsafe_b64encode(b"b content").decode().rstrip("=")}
        get_mock = gmail_client.service.users.return_value.messages.return_value.attachments.return_value.get
        get_mock.return_value.execute.side_effect = execute_side_effect
        result = await gmail_client.extract_attachments(message=message, msg_id="msg-1")
        assert len(result) == 1
        assert result[0][0] == "b.pdf"

    @pytest.mark.asyncio
    async def test_extract_attachments_nested_parts(self, gmail_client):
        """Test extract_attachments recurses into nested parts (line 259-260)."""
        message = {
            "payload": {
                "parts": [
                    {
                        "mimeType": "application/pdf",
                        "filename": "outer.pdf",
                        "body": {"attachmentId": "att-outer"},
                    },
                    {
                        "mimeType": "multipart/mixed",
                        "parts": [
                            {
                                "mimeType": "application/pdf",
                                "filename": "nested.pdf",
                                "body": {"attachmentId": "att-nested"},
                            }
                        ],
                    },
                ]
            }
        }
        get_mock = gmail_client.service.users.return_value.messages.return_value.attachments.return_value.get
        get_mock.return_value.execute.return_value = {
            "data": base64.urlsafe_b64encode(b"content").decode().rstrip("=")
        }
        result = await gmail_client.extract_attachments(message=message, msg_id="m1")
        assert len(result) == 2
        names = [r[0] for r in result]
        assert "outer.pdf" in names
        assert "nested.pdf" in names

    @pytest.mark.asyncio
    async def test_ensure_label_exists_found(self, gmail_client):
        """Test ensure_label_exists returns id when label exists."""
        with patch("src.gmail_client.get_label_config") as mock_config:
            cfg = Mock()
            cfg.name = "Aetherion"
            cfg.color_bg = "#000"
            cfg.color_text = "#fff"
            mock_config.return_value = cfg
            gmail_client.service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
                "labels": [{"id": "Label_1", "name": "Aetherion"}]
            }
            result = await gmail_client.ensure_label_exists()
            assert result == "Label_1"

    @pytest.mark.asyncio
    async def test_ensure_label_exists_creates_new(self, gmail_client):
        """Test ensure_label_exists creates label when not found."""
        with patch("src.gmail_client.get_label_config") as mock_config:
            cfg = Mock()
            cfg.name = "NewLabel"
            cfg.color_bg = "#aaa"
            cfg.color_text = "#bbb"
            mock_config.return_value = cfg
            gmail_client.service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
                "labels": []
            }
            gmail_client.service.users.return_value.labels.return_value.create.return_value.execute.return_value = {
                "id": "Label_2"
            }
            result = await gmail_client.ensure_label_exists()
            assert result == "Label_2"
            gmail_client.service.users.return_value.labels.return_value.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_label_exists_http_error(self, gmail_client):
        """Test ensure_label_exists raises HttpError on API failure."""
        with patch("src.gmail_client.get_label_config") as mock_config:
            cfg = Mock()
            cfg.name = "X"
            cfg.color_bg = "#000"
            cfg.color_text = "#fff"
            mock_config.return_value = cfg
            gmail_client.service.users.return_value.labels.return_value.list.return_value.execute.side_effect = HttpError(
                Mock(status=500), b"Error"
            )
            with pytest.raises(HttpError):
                await gmail_client.ensure_label_exists()

    @pytest.mark.asyncio
    async def test_apply_label_and_mark_read_success(self, gmail_client):
        """Test apply_label_and_mark_read calls modify."""
        gmail_client.service.users.return_value.messages.return_value.modify.return_value.execute.return_value = {}
        await gmail_client.apply_label_and_mark_read(message_id="m1", label_id="L1", mark_as_read=True, remove_from_inbox=False)
        gmail_client.service.users.return_value.messages.return_value.modify.assert_called_once()
        call_body = gmail_client.service.users.return_value.messages.return_value.modify.call_args[1]["body"]
        assert "L1" in call_body["addLabelIds"]
        assert "UNREAD" in call_body["removeLabelIds"]

    @pytest.mark.asyncio
    async def test_apply_label_and_mark_read_remove_from_inbox(self, gmail_client):
        """Test apply_label_and_mark_read with remove_from_inbox."""
        gmail_client.service.users.return_value.messages.return_value.modify.return_value.execute.return_value = {}
        await gmail_client.apply_label_and_mark_read(message_id="m1", label_id="L1", mark_as_read=True, remove_from_inbox=True)
        call_body = gmail_client.service.users.return_value.messages.return_value.modify.call_args[1]["body"]
        assert "INBOX" in call_body["removeLabelIds"]

    @pytest.mark.asyncio
    async def test_apply_label_and_mark_read_http_error(self, gmail_client):
        """Test apply_label_and_mark_read raises HttpError."""
        gmail_client.service.users.return_value.messages.return_value.modify.return_value.execute.side_effect = HttpError(
            Mock(status=403), b"Forbidden"
        )
        with pytest.raises(HttpError):
            await gmail_client.apply_label_and_mark_read(message_id="m1", label_id="L1")

    @pytest.mark.asyncio
    async def test_get_label_id_found(self, gmail_client):
        """Test get_label_id returns id when found."""
        gmail_client.service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
            "labels": [{"id": "L1", "name": "Inbox"}, {"id": "L2", "name": "MyLabel"}]
        }
        result = await gmail_client.get_label_id("MyLabel")
        assert result == "L2"

    @pytest.mark.asyncio
    async def test_get_label_id_not_found(self, gmail_client):
        """Test get_label_id returns None when not found."""
        gmail_client.service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
            "labels": [{"id": "L1", "name": "Inbox"}]
        }
        result = await gmail_client.get_label_id("NonExistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_label_id_http_error_returns_none(self, gmail_client):
        """Test get_label_id returns None on HttpError."""
        gmail_client.service.users.return_value.labels.return_value.list.return_value.execute.side_effect = HttpError(
            Mock(status=500), b"Error"
        )
        result = await gmail_client.get_label_id("X")
        assert result is None
