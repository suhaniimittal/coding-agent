"""
Unit tests for generic-webhook webhook_handler.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add webhook handler dir to path
_generic_dir = Path(__file__).resolve().parent.parent
if str(_generic_dir) not in sys.path:
    sys.path.insert(0, str(_generic_dir))

from webhook_handler import lambda_handler, process_webhook_event


class TestLambdaHandler:
    """Tests for lambda_handler."""

    def test_lambda_handler_success(self):
        event = {
            "httpMethod": "POST",
            "path": "/webhook",
            "headers": {"X-Tenant-ID": "tenant-123", "Content-Type": "application/json"},
            "queryStringParameters": {"foo": "bar"},
            "body": json.dumps({"key": "value"}),
        }
        with patch.dict("os.environ", {"ENVIRONMENT": "test"}):
            response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["message"] == "Webhook received successfully"
        assert body["tenant_id"] == "tenant-123"
        assert body["method"] == "POST"
        assert body["path"] == "/webhook"
        assert body["received_data"] == {"key": "value"}
        assert body["environment"] == "test"
        assert "timestamp" in body
        assert "Access-Control-Allow-Origin" in response["headers"]

    def test_lambda_handler_tenant_id_case_insensitive(self):
        event = {
            "httpMethod": "POST",
            "path": "/",
            "headers": {"x-tenant-id": "lower-tenant"},
            "queryStringParameters": None,
            "body": "{}",
        }
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["tenant_id"] == "lower-tenant"

    def test_lambda_handler_empty_body(self):
        event = {
            "httpMethod": "GET",
            "path": "/",
            "headers": {},
            "queryStringParameters": {},
            "body": "",
        }
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["received_data"] == {}

    def test_lambda_handler_invalid_json_body(self):
        event = {
            "httpMethod": "POST",
            "path": "/",
            "headers": {},
            "queryStringParameters": {},
            "body": "not valid json {",
        }
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["received_data"] == {}

    def test_lambda_handler_exception_returns_500(self):
        event = {
            "httpMethod": "POST",
            "path": "/",
            "headers": {},
            "queryStringParameters": None,
            "body": "{}",
        }
        with patch("webhook_handler.logger") as mock_logger:
            mock_logger.info.side_effect = RuntimeError("fail")
            response = lambda_handler(event, None)
        assert response["statusCode"] == 500
        body = json.loads(response["body"])
        assert "error" in body
        assert "message" in body


class TestProcessWebhookEvent:
    """Tests for process_webhook_event."""

    def test_process_webhook_event_logs_and_returns_none(self):
        with patch("webhook_handler.logger") as mock_logger:
            result = process_webhook_event("test_event", {"data": 1})
        assert result is None
        mock_logger.info.assert_called_once()
        assert "test_event" in str(mock_logger.info.call_args)
