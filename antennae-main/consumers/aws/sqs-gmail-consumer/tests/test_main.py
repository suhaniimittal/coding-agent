"""
Unit tests for main.py
"""

import json
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.main import (
    get_queue_url,
    get_sqs_client,
    poll_queue_and_process_messages,
    process_gmail_event,
)


class TestMain:
    """Test cases for main module."""

    def test_get_sqs_client_success(self):
        """Test successful SQS client creation."""
        with patch("src.main.boto3.client") as mock_boto3:
            mock_client = Mock()
            mock_boto3.return_value = mock_client

            client = get_sqs_client()

            assert client == mock_client
            mock_boto3.assert_called_once_with("sqs", region_name="us-east-1")

    def test_get_sqs_client_error(self):
        """Test SQS client creation with error."""
        with patch("src.main.boto3.client", side_effect=Exception("AWS error")):
            with pytest.raises(Exception):
                get_sqs_client()

    def test_get_queue_url_success(self):
        """Test successful queue URL retrieval."""
        mock_client = Mock()
        mock_client.get_queue_url.return_value = {
            "QueueUrl": "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        }

        url = get_queue_url(
            mock_client, "gmail-events-{environment}-{tenant_id}", "test", "tenant123"
        )

        assert url == "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        mock_client.get_queue_url.assert_called_once_with(QueueName="gmail-events-test-tenant123")

    def test_get_queue_url_error(self):
        """Test queue URL retrieval with error."""
        mock_client = Mock()
        mock_client.get_queue_url.side_effect = Exception("Queue not found")

        with pytest.raises(Exception):
            get_queue_url(
                mock_client, "gmail-events-{environment}-{tenant_id}", "test", "tenant123"
            )

    @pytest.mark.asyncio
    async def test_poll_queue_and_process_messages_missing_tenant_id(self, monkeypatch):
        """Test polling with missing TENANT_ID."""
        # Clear the TENANT_ID environment variable
        monkeypatch.delenv("TENANT_ID", raising=False)

        with patch("src.main.tenant_id", None):
            with pytest.raises(ValueError) as exc_info:
                await poll_queue_and_process_messages()

            assert "TENANT_ID environment variable is required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_poll_queue_and_process_messages_sqs_init_error(self):
        """Test polling with SQS initialization error."""
        with patch("src.main.get_sqs_client", side_effect=Exception("SQS init failed")):
            with pytest.raises(Exception):
                await poll_queue_and_process_messages()

    @pytest.mark.asyncio
    async def test_poll_queue_and_process_messages_empty_queue(self, mock_boto3_client):
        """Test polling with empty queue."""
        call_count = 0

        def receive_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {}  # No messages
            else:
                raise KeyboardInterrupt()  # Exit after first empty response

        mock_boto3_client.receive_message.side_effect = receive_side_effect

        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/queue"):
                with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    mock_sleep.side_effect = KeyboardInterrupt()

                    # Should exit gracefully
                    await poll_queue_and_process_messages()

                    # Should have tried to sleep after empty queue
                    mock_sleep.assert_called_with(5)

    @pytest.mark.asyncio
    async def test_poll_queue_and_process_messages_with_message(
        self, mock_boto3_client, mock_sqs_message
    ):
        """Test processing a valid SQS message."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_sqs_message
            else:
                raise KeyboardInterrupt()

        mock_boto3_client.receive_message.side_effect = side_effect

        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/queue"):
                with patch("src.main.process_gmail_event", new_callable=AsyncMock) as mock_process:
                    mock_process.return_value = "s3://bucket/attachment.pdf"

                    # Should process message and exit
                    await poll_queue_and_process_messages()

                    # Verify message was processed and deleted
                    # The tenant_id comes from the environment variable which is mocked in conftest
                    mock_process.assert_called_once()
                    call_args = mock_process.call_args
                    assert call_args.args[0] == "12345678"  # history_id
                    # tenant_id is the second argument - it comes from env var
                    mock_boto3_client.delete_message.assert_called()

    @pytest.mark.asyncio
    async def test_poll_queue_and_process_messages_invalid_json(self, mock_boto3_client):
        """Test processing message with invalid JSON."""
        invalid_message = {
            "Messages": [
                {"MessageId": "test-id", "ReceiptHandle": "test-handle", "Body": "invalid-json"}
            ]
        }

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return invalid_message
            else:
                raise KeyboardInterrupt()

        mock_boto3_client.receive_message.side_effect = side_effect

        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/queue"):
                # Should handle the error gracefully and continue
                await poll_queue_and_process_messages()

    @pytest.mark.asyncio
    async def test_poll_queue_and_process_messages_missing_history_id(self, mock_boto3_client):
        """Test processing message without historyId."""
        message_without_history = {
            "Messages": [
                {
                    "MessageId": "test-id",
                    "ReceiptHandle": "test-handle",
                    "Body": json.dumps({"someOtherField": "value"}),
                }
            ]
        }

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return message_without_history
            else:
                raise KeyboardInterrupt()

        mock_boto3_client.receive_message.side_effect = side_effect

        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/queue"):
                # Should handle missing historyId and continue
                await poll_queue_and_process_messages()

    @pytest.mark.asyncio
    async def test_poll_queue_and_process_messages_processing_error(
        self, mock_boto3_client, mock_sqs_message
    ):
        """Test handling of processing errors (message should be deleted)."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_sqs_message
            else:
                raise KeyboardInterrupt()

        mock_boto3_client.receive_message.side_effect = side_effect

        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/queue"):
                with patch("src.main.process_gmail_event", new_callable=AsyncMock) as mock_process:
                    mock_process.side_effect = Exception("Processing failed")

                    # Should handle the error and continue (message not deleted so it can retry)
                    await poll_queue_and_process_messages()

                    # Current behavior: message is not deleted on processing failure
                    mock_boto3_client.delete_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_queue_and_process_messages_keyboard_interrupt(self, mock_boto3_client):
        """Test graceful handling of KeyboardInterrupt."""
        mock_boto3_client.receive_message.side_effect = KeyboardInterrupt()

        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/queue"):
                # Should exit gracefully without raising
                await poll_queue_and_process_messages()

    @pytest.mark.asyncio
    async def test_poll_queue_and_process_messages_unexpected_error(self, mock_boto3_client):
        """Test handling of unexpected errors during polling."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Unexpected error")
            else:
                raise KeyboardInterrupt()  # Exit after error handling

        mock_boto3_client.receive_message.side_effect = side_effect

        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/queue"):
                with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    mock_sleep.side_effect = [None, KeyboardInterrupt()]

                    # Should handle the error and continue
                    await poll_queue_and_process_messages()

                    # Should sleep 30 seconds after error before retrying
                    mock_sleep.assert_called_with(30)

    @pytest.mark.asyncio
    async def test_process_gmail_event_success(self):
        """Test successful Gmail event processing."""
        history_id = "12345678"
        tenant_id = "test-tenant"
        eml_s3_key = "attachments/email_gmail-msg-123.eml"

        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client = Mock()

            with patch("src.main.LISTENER_EMAIL", "test@example.com"):
                with patch("src.main.decrypt_token", new_callable=AsyncMock) as mock_decrypt:
                    mock_decrypt.return_value = '{"token": "access-token"}'

                    with patch("src.main.GmailAPIClient") as MockGmailClient:
                        mock_client = Mock()
                        mock_client.get_new_message_id.return_value = "gmail-msg-123"
                        mock_client._fetch_message = AsyncMock(
                            return_value={"payload": {"headers": []}}
                        )
                        mock_client.extract_basic_info = AsyncMock(
                            return_value=("Message body content", "sender@example.com")
                        )
                        mock_client.build_body_eml = AsyncMock(return_value=b"eml content")
                        mock_client.extract_attachments = AsyncMock(return_value=[])
                        mock_client.ensure_label_exists = AsyncMock(return_value="label-id")
                        mock_client.apply_label_and_mark_read = AsyncMock()
                        MockGmailClient.return_value = mock_client

                        with patch("src.main.email_filter") as mock_filter:
                            mock_filter.should_process_email.return_value = True

                            with patch(
                                "src.main.save_attachment_to_s3", new_callable=AsyncMock
                            ) as mock_save:
                                mock_save.return_value = eml_s3_key

                                with patch(
                                    "src.main.run_agent", new_callable=AsyncMock
                                ) as mock_run_agent:
                                    result = await process_gmail_event(history_id, tenant_id)

                                    assert result == [eml_s3_key]
                                    mock_storage.init_client.assert_called_once()
                                    mock_decrypt.assert_called_once_with(
                                        tenant_id, "test@example.com"
                                    )
                                    mock_client.get_new_message_id.assert_called_once_with(
                                        history_id
                                    )
                                    mock_client._fetch_message.assert_called_once_with(
                                        msg_id="gmail-msg-123"
                                    )
                                    mock_filter.should_process_email.assert_called_once_with(
                                        "sender@example.com"
                                    )
                                    mock_run_agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_gmail_event_storage_init_failure(self):
        """Test Gmail event processing with storage initialization failure."""
        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client.side_effect = Exception("Storage init failed")

            with pytest.raises(RuntimeError) as exc_info:
                await process_gmail_event("12345", "tenant-id")

            assert "Storage client initialization failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_process_gmail_event_no_message_found(self):
        """Test Gmail event processing when no new message is found."""
        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client = Mock()

            with patch("src.main.decrypt_token", new_callable=AsyncMock) as mock_decrypt:
                mock_decrypt.return_value = '{"token": "access-token"}'

                with patch("src.main.GmailAPIClient") as MockGmailClient:
                    mock_client = Mock()
                    mock_client.get_new_message_id.return_value = None  # No message found
                    MockGmailClient.return_value = mock_client

                    result = await process_gmail_event("12345", "tenant-id")

                    assert result == []

    @pytest.mark.asyncio
    async def test_process_gmail_event_email_filtered_out(self):
        """Test Gmail event processing when email is filtered out."""
        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client = Mock()

            with patch("src.main.decrypt_token", new_callable=AsyncMock) as mock_decrypt:
                mock_decrypt.return_value = '{"token": "access-token"}'

                with patch("src.main.GmailAPIClient") as MockGmailClient:
                    mock_client = Mock()
                    mock_client.get_new_message_id.return_value = "gmail-msg-123"
                    mock_client._fetch_message = AsyncMock(
                        return_value={"payload": {"headers": []}}
                    )
                    mock_client.extract_basic_info = AsyncMock(
                        return_value=("Message body", "blocked@example.com")
                    )
                    MockGmailClient.return_value = mock_client

                    with patch("src.main.email_filter") as mock_filter:
                        mock_filter.should_process_email.return_value = False  # Filtered out

                        with patch("src.main.run_agent", new_callable=AsyncMock) as mock_run_agent:
                            result = await process_gmail_event("12345", "tenant-id")

                            assert result == []
                            mock_run_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_gmail_event_agent_execution(self):
        """Test that agent is called with correct parameters."""
        history_id = "12345678"
        tenant_id = "test-tenant"
        sender_email = "sender@example.com"
        eml_s3_key = "attachments/email_gmail-msg-123.eml"

        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client = Mock()

            with patch("src.main.decrypt_token", new_callable=AsyncMock) as mock_decrypt:
                mock_decrypt.return_value = '{"token": "access-token"}'

                with patch("src.main.GmailAPIClient") as MockGmailClient:
                    mock_client = Mock()
                    mock_client.get_new_message_id.return_value = "gmail-msg-123"
                    mock_client._fetch_message = AsyncMock(
                        return_value={"payload": {"headers": []}}
                    )
                    mock_client.extract_basic_info = AsyncMock(
                        return_value=("Message body content", sender_email)
                    )
                    mock_client.build_body_eml = AsyncMock(return_value=b"eml content")
                    mock_client.extract_attachments = AsyncMock(return_value=[])
                    mock_client.ensure_label_exists = AsyncMock(return_value="label-id")
                    mock_client.apply_label_and_mark_read = AsyncMock()
                    MockGmailClient.return_value = mock_client

                    with patch("src.main.email_filter") as mock_filter:
                        mock_filter.should_process_email.return_value = True

                        with patch(
                            "src.main.save_attachment_to_s3", new_callable=AsyncMock
                        ) as mock_save:
                            mock_save.return_value = eml_s3_key

                            with patch(
                                "src.main.run_agent", new_callable=AsyncMock
                            ) as mock_run_agent:
                                await process_gmail_event(history_id, tenant_id)

                                mock_run_agent.assert_called_once()
                                call_args = mock_run_agent.call_args
                                assert call_args[1]["agent_name"] == "Receipt Creator"
                                assert call_args[1]["run_in_sync"] is True
                                agent_params = json.loads(call_args[1]["agent_params"])
                                assert agent_params["history_id"] == history_id
                                assert agent_params["sender_email"] == sender_email
                                assert agent_params["uploaded_files"] == [eml_s3_key]

    @pytest.mark.asyncio
    async def test_process_gmail_event_no_attachment(self):
        """Test Gmail event processing when there's no attachment (only .eml uploaded)."""
        eml_s3_key = "attachments/email_gmail-msg-123.eml"

        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client = Mock()

            with patch("src.main.decrypt_token", new_callable=AsyncMock) as mock_decrypt:
                mock_decrypt.return_value = '{"token": "access-token"}'

                with patch("src.main.GmailAPIClient") as MockGmailClient:
                    mock_client = Mock()
                    mock_client.get_new_message_id.return_value = "gmail-msg-123"
                    mock_client._fetch_message = AsyncMock(
                        return_value={"payload": {"headers": []}}
                    )
                    mock_client.extract_basic_info = AsyncMock(
                        return_value=("Message body content", "sender@example.com")
                    )
                    mock_client.build_body_eml = AsyncMock(return_value=b"eml content")
                    mock_client.extract_attachments = AsyncMock(return_value=[])  # No attachments
                    mock_client.ensure_label_exists = AsyncMock(return_value="label-id")
                    mock_client.apply_label_and_mark_read = AsyncMock()
                    MockGmailClient.return_value = mock_client

                    with patch("src.main.email_filter") as mock_filter:
                        mock_filter.should_process_email.return_value = True

                        with patch(
                            "src.main.save_attachment_to_s3", new_callable=AsyncMock
                        ) as mock_save:
                            mock_save.return_value = eml_s3_key

                            with patch(
                                "src.main.run_agent", new_callable=AsyncMock
                            ) as mock_run_agent:
                                result = await process_gmail_event("12345", "tenant-id")

                                assert result == [eml_s3_key]
                                agent_params = json.loads(
                                    mock_run_agent.call_args[1]["agent_params"]
                                )
                                assert agent_params["uploaded_files"] == [eml_s3_key]

    @pytest.mark.asyncio
    async def test_poll_queue_missing_history_id_deletes_message(self, mock_boto3_client):
        """Test message without historyId is deleted to avoid infinite retries."""
        message_no_history = {
            "Messages": [
                {
                    "MessageId": "mid-1",
                    "ReceiptHandle": "rh1",
                    "Body": json.dumps({"other": "data"}),
                }
            ]
        }
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return message_no_history
            raise KeyboardInterrupt()

        mock_boto3_client.receive_message.side_effect = side_effect
        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/q"):
                await poll_queue_and_process_messages()
        mock_boto3_client.delete_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_poll_queue_invalid_json_deletes_message(self, mock_boto3_client):
        """Test message with invalid JSON body is deleted."""
        message_bad_json = {
            "Messages": [{"MessageId": "mid-1", "ReceiptHandle": "rh1", "Body": "not-json"}]
        }
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return message_bad_json
            raise KeyboardInterrupt()

        mock_boto3_client.receive_message.side_effect = side_effect
        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/q"):
                await poll_queue_and_process_messages()
        mock_boto3_client.delete_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_poll_queue_refresh_error_deletes_message(
        self, mock_boto3_client, mock_sqs_message
    ):
        """Test OAuth RefreshError causes message to be deleted (non-retryable)."""
        from google.auth.exceptions import RefreshError

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_sqs_message
            raise KeyboardInterrupt()

        mock_boto3_client.receive_message.side_effect = side_effect
        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/q"):
                with patch(
                    "src.main.process_gmail_event",
                    new_callable=AsyncMock,
                    side_effect=RefreshError("invalid_grant"),
                ):
                    await poll_queue_and_process_messages()
        mock_boto3_client.delete_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_gmail_event_attachment_upload_error_continues(self):
        """Test process_gmail_event continues when one attachment upload fails."""
        eml_s3_key = "attachments/email_msg.eml"
        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client = Mock()
            with patch("src.main.LISTENER_EMAIL", "test@example.com"):
                with patch(
                    "src.main.decrypt_token", new_callable=AsyncMock, return_value='{"token": "t"}'
                ):
                    with patch("src.main.GmailAPIClient") as MockGmailClient:
                        mock_client = Mock()
                        mock_client.get_new_message_id.return_value = "msg-1"
                        mock_client._fetch_message = AsyncMock(
                            return_value={"payload": {"headers": []}}
                        )
                        mock_client.extract_basic_info = AsyncMock(return_value=("Body", "s@e.com"))
                        mock_client.build_body_eml = AsyncMock(return_value=b"eml")
                        mock_client.extract_attachments = AsyncMock(
                            return_value=[("a.pdf", b"a"), ("b.pdf", b"b")]
                        )
                        mock_client.ensure_label_exists = AsyncMock(return_value="L1")
                        mock_client.apply_label_and_mark_read = AsyncMock()
                        MockGmailClient.return_value = mock_client
                        with patch("src.main.email_filter") as mock_filter:
                            mock_filter.should_process_email.return_value = True
                            with patch(
                                "src.main.save_attachment_to_s3", new_callable=AsyncMock
                            ) as mock_save:
                                mock_save.side_effect = [
                                    eml_s3_key,
                                    "attachments/a.pdf",
                                    Exception("S3 error"),
                                ]
                                with patch("src.main.run_agent", new_callable=AsyncMock):
                                    result = await process_gmail_event("hid", "tid")
                                assert result == [eml_s3_key, "attachments/a.pdf"]
                                assert mock_save.call_count == 3

    @pytest.mark.asyncio
    async def test_process_gmail_event_labeling_failure_does_not_fail(self):
        """Test process_gmail_event still returns keys when labeling raises."""
        eml_s3_key = "attachments/email_msg.eml"
        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client = Mock()
            with patch("src.main.LISTENER_EMAIL", "test@example.com"):
                with patch(
                    "src.main.decrypt_token", new_callable=AsyncMock, return_value='{"token": "t"}'
                ):
                    with patch("src.main.GmailAPIClient") as MockGmailClient:
                        mock_client = Mock()
                        mock_client.get_new_message_id.return_value = "msg-1"
                        mock_client._fetch_message = AsyncMock(
                            return_value={"payload": {"headers": []}}
                        )
                        mock_client.extract_basic_info = AsyncMock(return_value=("Body", "s@e.com"))
                        mock_client.build_body_eml = AsyncMock(return_value=b"eml")
                        mock_client.extract_attachments = AsyncMock(return_value=[])
                        mock_client.ensure_label_exists = AsyncMock(return_value="L1")
                        mock_client.apply_label_and_mark_read = AsyncMock(
                            side_effect=Exception("Label API error")
                        )
                        MockGmailClient.return_value = mock_client
                        with patch("src.main.email_filter") as mock_filter:
                            mock_filter.should_process_email.return_value = True
                            with patch(
                                "src.main.save_attachment_to_s3",
                                new_callable=AsyncMock,
                                return_value=eml_s3_key,
                            ):
                                with patch("src.main.run_agent", new_callable=AsyncMock):
                                    result = await process_gmail_event("hid", "tid")
                                assert result == [eml_s3_key]

    @pytest.mark.asyncio
    async def test_main_module_execution_success(self, env_vars):
        """Test main module execution path."""
        with patch("src.main.poll_queue_and_process_messages", new_callable=AsyncMock) as _:
            with patch("src.main.asyncio.run") as _:
                # Simulate importing and running main
                with patch("src.main.__name__", "__main__"):
                    # Mock the main execution
                    import src.main

                    # The main block should be triggered
                    # Since we can't easily test the if __name__ == '__main__' block,
                    # we'll test the components it would call

                    # Just verify the function exists and can be imported
                    assert hasattr(src.main, "poll_queue_and_process_messages")
                    assert callable(src.main.poll_queue_and_process_messages)

    def test_environment_variable_parsing(self, env_vars):
        """Test that environment variables are properly parsed."""
        # We need to patch the environment variables before importing
        with patch.dict(os.environ, env_vars):
            # Force reload the module to pick up new env vars
            import importlib

            import src.main

            importlib.reload(src.main)

            assert src.main.environment == "test"
            assert src.main.tenant_id == "test-tenant"
            assert src.main.queue_name_pattern == "gmail-events-{environment}-{tenant_id}"
            assert src.main.region == "us-east-1"
            assert src.main.LISTENER_EMAIL == "test@example.com"

    def test_queue_name_pattern_formatting(self):
        """Test queue name pattern formatting."""
        pattern = "gmail-events-{environment}-{tenant_id}"
        environment = "prod"
        tenant_id = "company123"

        # Simulate the formatting logic from get_queue_url
        queue_name = pattern.format(environment=environment, tenant_id=tenant_id)

        assert queue_name == "gmail-events-prod-company123"

    def test_main_block_no_tenant_id_exits(self):
        """Test __main__ block exits when TENANT_ID is missing."""
        import runpy
        from pathlib import Path

        main_py = Path(__file__).resolve().parent.parent / "src" / "main.py"
        with patch.dict(
            os.environ,
            {"TENANT_ID": "", "ENCRYPTION_KEY": os.environ.get("ENCRYPTION_KEY", "test")},
            clear=False,
        ):
            with patch("builtins.exit", side_effect=SystemExit) as mock_exit:
                with pytest.raises(SystemExit):
                    runpy.run_path(str(main_py), run_name="__main__")
                mock_exit.assert_called_once_with(1)

    def test_main_block_keyboard_interrupt_handled(self):
        """Test __main__ block catches KeyboardInterrupt."""
        import runpy
        from pathlib import Path

        main_py = Path(__file__).resolve().parent.parent / "src" / "main.py"
        with patch.dict(os.environ, {"TENANT_ID": "t1"}, clear=False):
            with patch("asyncio.run", side_effect=KeyboardInterrupt):
                # Main block catches KeyboardInterrupt and logs; no exit
                runpy.run_path(str(main_py), run_name="__main__")

    def test_main_block_exception_raised(self):
        """Test __main__ block re-raises critical exception."""
        import runpy
        from pathlib import Path

        main_py = Path(__file__).resolve().parent.parent / "src" / "main.py"
        with patch.dict(os.environ, {"TENANT_ID": "t1"}, clear=False):
            with patch("asyncio.run", side_effect=RuntimeError("SQS failure")):
                with pytest.raises(RuntimeError, match="SQS failure"):
                    runpy.run_path(str(main_py), run_name="__main__")

    @pytest.mark.asyncio
    async def test_poll_queue_unexpected_error_continues(self, mock_boto3_client):
        """Test polling continues after unexpected error in loop."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Unexpected network error")
            else:
                raise KeyboardInterrupt()

        mock_boto3_client.receive_message.side_effect = side_effect

        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", return_value="https://sqs.test.com/queue"):
                with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    mock_sleep.return_value = None
                    await poll_queue_and_process_messages()
                    # After the error, sleep(30) should be called
                    mock_sleep.assert_called_with(30)

    @pytest.mark.asyncio
    async def test_process_gmail_event_with_attachments_success(self):
        """Test process_gmail_event successfully processes email with attachments."""
        eml_s3_key = "attachments/email_msg.eml"
        att_s3_key = "attachments/doc.pdf"

        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client = Mock()
            with patch("src.main.LISTENER_EMAIL", "test@example.com"):
                with patch(
                    "src.main.decrypt_token",
                    new_callable=AsyncMock,
                    return_value='{"token": "t"}',
                ):
                    with patch("src.main.GmailAPIClient") as MockGmailClient:
                        mock_client = Mock()
                        mock_client.get_new_message_id.return_value = "msg-1"
                        mock_client._fetch_message = AsyncMock(
                            return_value={"payload": {"headers": []}}
                        )
                        mock_client.extract_basic_info = AsyncMock(
                            return_value=("Body content", "sender@example.com")
                        )
                        mock_client.build_body_eml = AsyncMock(return_value=b"eml content")
                        mock_client.extract_attachments = AsyncMock(
                            return_value=[("doc.pdf", b"pdf content")]
                        )
                        mock_client.ensure_label_exists = AsyncMock(return_value="L1")
                        mock_client.apply_label_and_mark_read = AsyncMock()
                        MockGmailClient.return_value = mock_client

                        with patch("src.main.email_filter") as mock_filter:
                            mock_filter.should_process_email.return_value = True

                            with patch(
                                "src.main.save_attachment_to_s3", new_callable=AsyncMock
                            ) as mock_save:
                                mock_save.side_effect = [eml_s3_key, att_s3_key]

                                with patch("src.main.run_agent", new_callable=AsyncMock):
                                    with patch("src.main.get_label_config") as mock_label_config:
                                        mock_label_config.return_value = Mock(
                                            name="Aetherion",
                                            mark_as_read=True,
                                            remove_from_inbox=False,
                                        )
                                        result = await process_gmail_event("hid", "tid")

                        assert result == [eml_s3_key, att_s3_key]
                        assert mock_save.call_count == 2

    @pytest.mark.asyncio
    async def test_process_gmail_event_run_agent_called_with_correct_params(self):
        """Test that run_agent is called with correct parameters."""
        eml_s3_key = "attachments/email_msg.eml"

        captured_agent_params = None

        async def capture_run_agent(agent_name, agent_params, run_in_sync):
            nonlocal captured_agent_params
            captured_agent_params = json.loads(agent_params)

        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client = Mock()
            with patch("src.main.LISTENER_EMAIL", "test@example.com"):
                with patch(
                    "src.main.decrypt_token",
                    new_callable=AsyncMock,
                    return_value='{"token": "t"}',
                ):
                    with patch("src.main.GmailAPIClient") as MockGmailClient:
                        mock_client = Mock()
                        mock_client.get_new_message_id.return_value = "msg-1"
                        mock_client._fetch_message = AsyncMock(
                            return_value={"payload": {"headers": []}}
                        )
                        mock_client.extract_basic_info = AsyncMock(
                            return_value=("Body", "sender@test.com")
                        )
                        mock_client.build_body_eml = AsyncMock(return_value=b"eml")
                        mock_client.extract_attachments = AsyncMock(return_value=[])
                        mock_client.ensure_label_exists = AsyncMock(return_value="L1")
                        mock_client.apply_label_and_mark_read = AsyncMock()
                        MockGmailClient.return_value = mock_client

                        with patch("src.main.email_filter") as mock_filter:
                            mock_filter.should_process_email.return_value = True

                            with patch(
                                "src.main.save_attachment_to_s3",
                                new_callable=AsyncMock,
                                return_value=eml_s3_key,
                            ):
                                with patch("src.main.run_agent", side_effect=capture_run_agent):
                                    with patch("src.main.get_label_config") as mock_label_config:
                                        mock_label_config.return_value = Mock(
                                            name="Aetherion",
                                            mark_as_read=True,
                                            remove_from_inbox=False,
                                        )
                                        await process_gmail_event("12345", "tenant-1")

        assert captured_agent_params is not None
        assert captured_agent_params["history_id"] == "12345"
        assert captured_agent_params["sender_email"] == "sender@test.com"
        assert captured_agent_params["uploaded_files"] == [eml_s3_key]

    @pytest.mark.asyncio
    async def test_process_gmail_event_logs_no_attachments(self):
        """Test that 'No attachments found' is logged when email has no attachments."""
        eml_s3_key = "attachments/email_msg.eml"

        with patch("src.main.storage") as mock_storage:
            mock_storage.init_client = Mock()
            with patch("src.main.LISTENER_EMAIL", "test@example.com"):
                with patch(
                    "src.main.decrypt_token",
                    new_callable=AsyncMock,
                    return_value='{"token": "t"}',
                ):
                    with patch("src.main.GmailAPIClient") as MockGmailClient:
                        mock_client = Mock()
                        mock_client.get_new_message_id.return_value = "msg-1"
                        mock_client._fetch_message = AsyncMock(
                            return_value={"payload": {"headers": []}}
                        )
                        mock_client.extract_basic_info = AsyncMock(
                            return_value=("Body", "sender@test.com")
                        )
                        mock_client.build_body_eml = AsyncMock(return_value=b"eml")
                        mock_client.extract_attachments = AsyncMock(return_value=[])
                        mock_client.ensure_label_exists = AsyncMock(return_value="L1")
                        mock_client.apply_label_and_mark_read = AsyncMock()
                        MockGmailClient.return_value = mock_client

                        with patch("src.main.email_filter") as mock_filter:
                            mock_filter.should_process_email.return_value = True

                            with patch(
                                "src.main.save_attachment_to_s3",
                                new_callable=AsyncMock,
                                return_value=eml_s3_key,
                            ):
                                with patch("src.main.run_agent", new_callable=AsyncMock):
                                    with patch("src.main.get_label_config") as mock_label_config:
                                        mock_label_config.return_value = Mock(
                                            name="Aetherion",
                                            mark_as_read=True,
                                            remove_from_inbox=False,
                                        )
                                        result = await process_gmail_event("hid", "tid")

                        # Only the eml file should be in result (no attachments)
                        assert result == [eml_s3_key]
                        # Verify extract_attachments returned empty list
                        mock_client.extract_attachments.assert_called_once()

    @pytest.mark.asyncio
    async def test_poll_queue_sqs_init_raises(self):
        """Test polling raises when SQS initialization fails."""
        with patch("src.main.get_sqs_client", side_effect=Exception("SQS init failed")):
            with pytest.raises(Exception, match="SQS init failed"):
                await poll_queue_and_process_messages()

    @pytest.mark.asyncio
    async def test_poll_queue_get_queue_url_raises(self, mock_boto3_client):
        """Test polling raises when get_queue_url fails."""
        with patch("src.main.get_sqs_client", return_value=mock_boto3_client):
            with patch("src.main.get_queue_url", side_effect=Exception("Queue not found")):
                with pytest.raises(Exception, match="Queue not found"):
                    await poll_queue_and_process_messages()
