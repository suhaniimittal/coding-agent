"""
Tests for webhooks/aws/gmail-webhook/webhook_handler.
Load module with PyJWKClient and boto3 mocked to avoid import-time errors.
"""

import base64
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

_gmail_webhook_dir = Path(__file__).resolve().parent.parent
_handler_path = _gmail_webhook_dir / "webhook_handler.py"


def _load_gmail_webhook_handler():
    """Load gmail webhook_handler with PyJWKClient and boto3 mocked."""
    spec = importlib.util.spec_from_file_location("gmail_webhook_handler", _handler_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {_handler_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    # Avoid PyJWKClient() raising at import; avoid boto3 needing AWS
    with patch("jwt.PyJWKClient", Mock(return_value=Mock())):
        with patch("boto3.client", MagicMock()):
            # Load gmail webhook's config too (same dir)
            old_path = sys.path.copy()
            try:
                sys.path.insert(0, str(_gmail_webhook_dir))
                spec.loader.exec_module(mod)
            finally:
                sys.path[:] = old_path
    return mod


@pytest.fixture(scope="module")
def gmail_wh():
    """Load gmail webhook handler once per test module."""
    return _load_gmail_webhook_handler()


class TestSanitizeEmailForQueue:
    def test_sanitize_email(self, gmail_wh):
        assert gmail_wh._sanitize_email_for_queue("User@Example.COM") == "user-example-com"
        assert gmail_wh._sanitize_email_for_queue("a+b@c.d") == "a-b-c-d"
        assert len(gmail_wh._sanitize_email_for_queue("a" * 100)) == 64


class TestGetSqsQueueUrlByName:
    def test_success(self, gmail_wh):
        with patch.object(gmail_wh, "sqs_client") as m:
            m.get_queue_url.return_value = {"QueueUrl": "https://sqs.us-east-1.amazonaws.com/123/q"}
            assert gmail_wh.get_sqs_queue_url_by_name("my-queue") == "https://sqs.us-east-1.amazonaws.com/123/q"

    def test_non_existent_returns_none(self, gmail_wh):
        from botocore.exceptions import ClientError
        with patch.object(gmail_wh, "sqs_client") as m:
            m.get_queue_url.side_effect = ClientError(
                {"Error": {"Code": "AWS.SimpleQueueService.NonExistentQueue"}}, "GetQueueUrl"
            )
            assert gmail_wh.get_sqs_queue_url_by_name("missing") is None

    def test_other_error_returns_none(self, gmail_wh):
        with patch.object(gmail_wh, "sqs_client") as m:
            m.get_queue_url.side_effect = Exception("Network error")
            assert gmail_wh.get_sqs_queue_url_by_name("q") is None


class TestSendToSqs:
    def test_success(self, gmail_wh):
        with patch.object(gmail_wh, "sqs_client") as m:
            m.send_message.return_value = {"MessageId": "msg-1"}
            assert gmail_wh.send_to_sqs("https://sqs.example.com/q", {"k": "v"}, "a@b.com", "tenant") is True

    def test_client_error_returns_false(self, gmail_wh):
        from botocore.exceptions import ClientError
        with patch.object(gmail_wh, "sqs_client") as m:
            m.send_message.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "SendMessage")
            assert gmail_wh.send_to_sqs("https://sqs.example.com/q", {}, "a@b.com", "t") is False


class TestDecodeBase64Json:
    def test_valid_json(self, gmail_wh):
        data = {"emailAddress": "a@b.com", "historyId": "123"}
        b64 = base64.b64encode(json.dumps(data).encode()).decode()
        assert gmail_wh._decode_base64_json(b64) == data

    def test_invalid_json_returns_raw(self, gmail_wh):
        b64 = base64.b64encode(b"not json").decode()
        result = gmail_wh._decode_base64_json(b64)
        assert "raw" in result


class TestParsePubsubBody:
    def test_valid_message(self, gmail_wh):
        payload = {"emailAddress": "a@b.com", "historyId": "1"}
        b64 = base64.b64encode(json.dumps(payload).encode()).decode()
        event = {"message": {"data": b64}}
        assert gmail_wh.parse_pubsub_body(event) == payload

    def test_no_message_returns_none(self, gmail_wh):
        assert gmail_wh.parse_pubsub_body({}) is None
        assert gmail_wh.parse_pubsub_body({"message": {}}) is None

    def test_missing_data_returns_none(self, gmail_wh):
        assert gmail_wh.parse_pubsub_body({"message": {}}) is None


class TestBuildProcessedEvent:
    def test_basic(self, gmail_wh):
        raw = {"message": {}}
        parsed = {"emailAddress": "a@b.com", "historyId": "42"}
        out = gmail_wh.build_processed_event("a@b.com", raw, parsed)
        assert out["source"] == "gmail"
        assert out["emailAddress"] == "a@b.com"
        assert out["payload"] == parsed
        assert out["historyId"] == "42"


class TestBuildQueueName:
    def test_format(self, gmail_wh):
        name = gmail_wh.build_queue_name("dev", "gmail-{environment}-{email}", "User@Example.com")
        assert "dev" in name
        assert "user-example-com" in name
        assert len(name) <= 80


class TestGetGoogleJwtAudienceIssuer:
    def test_default_tenant(self, gmail_wh):
        with patch.object(gmail_wh, "get_secret", side_effect=lambda x, key=None, default=None: default):
            aud, iss = gmail_wh.get_google_jwt_audience_issuer("tid", None, "dev")
            assert "tid" in aud
            assert "dev" in iss

    def test_with_tenant(self, gmail_wh):
        with patch.object(gmail_wh, "get_secret", side_effect=lambda x, key=None, default=None: default):
            aud, iss = gmail_wh.get_google_jwt_audience_issuer("tid", "mytenant", "prod")
            assert "mytenant" in str(iss).lower() or "mytenant" in str(aud).lower()


class TestVerifyGoogleJwt:
    def test_missing_or_malformed_auth_returns_none(self, gmail_wh):
        assert gmail_wh.verify_google_jwt("", "t", "tenant", "dev") is None
        assert gmail_wh.verify_google_jwt("Basic x", "t", "tenant", "dev") is None

    def test_jwks_none_raises(self, gmail_wh):
        with patch.object(gmail_wh, "jwks_client", None):
            with pytest.raises(RuntimeError, match="JWKS client"):
                gmail_wh.verify_google_jwt("Bearer token", "t", "t", "dev")


class TestGmailLambdaHandler:
    def test_missing_tenant_id_returns_400(self, gmail_wh):
        event = {"queryStringParameters": {}, "headers": {}, "body": ""}
        res = gmail_wh.lambda_handler(event, None)
        assert res["statusCode"] == 400
        assert "tenant" in res["body"].lower()

    def test_missing_tenant_returns_400(self, gmail_wh):
        event = {"queryStringParameters": {"tenant_id": "t1"}, "headers": {}, "body": ""}
        res = gmail_wh.lambda_handler(event, None)
        assert res["statusCode"] == 400

    def test_unauthorized_when_jwt_fails(self, gmail_wh):
        event = {
            "queryStringParameters": {"tenant_id": "t1", "tenant": "t"},
            "headers": {"Authorization": "Bearer bad"},
            "body": "",
        }
        with patch.object(gmail_wh, "verify_google_jwt", return_value=None):
            res = gmail_wh.lambda_handler(event, None)
        assert res["statusCode"] == 401

    def test_invalid_pubsub_returns_400(self, gmail_wh):
        body = json.dumps({"message": {}})  # no data
        event = {
            "queryStringParameters": {"tenant_id": "t1", "tenant": "t"},
            "headers": {"Authorization": "Bearer x"},
            "body": base64.b64encode(body.encode()).decode(),
        }
        with patch.object(gmail_wh, "verify_google_jwt", return_value="issuer@example.com"):
            with patch.object(gmail_wh, "parse_pubsub_body", return_value=None):
                res = gmail_wh.lambda_handler(event, None)
        assert res["statusCode"] == 400

    def test_missing_email_address_returns_400(self, gmail_wh):
        raw = {"message": {"data": base64.b64encode(json.dumps({}).encode()).decode()}}
        event = {
            "queryStringParameters": {"tenant_id": "t1", "tenant": "t"},
            "headers": {"Authorization": "Bearer x"},
            "body": base64.b64encode(json.dumps(raw).encode()).decode(),
        }
        with patch.object(gmail_wh, "verify_google_jwt", return_value="issuer@example.com"):
            with patch.object(gmail_wh, "parse_pubsub_body", return_value={}):
                res = gmail_wh.lambda_handler(event, None)
        assert res["statusCode"] == 400

    def test_queue_not_found_returns_500(self, gmail_wh):
        raw = {"message": {"data": base64.b64encode(json.dumps({"emailAddress": "a@b.com"}).encode()).decode()}}
        event = {
            "queryStringParameters": {"tenant_id": "t1", "tenant": "t"},
            "headers": {"Authorization": "Bearer x"},
            "body": base64.b64encode(json.dumps(raw).encode()).decode(),
        }
        with patch.object(gmail_wh, "verify_google_jwt", return_value="issuer@example.com"):
            with patch.object(gmail_wh, "parse_pubsub_body", return_value={"emailAddress": "a@b.com"}):
                with patch.object(gmail_wh, "get_secret", return_value="gmail-events-{environment}-{tenant_id}"):
                    with patch.object(gmail_wh, "get_sqs_queue_url_by_name", return_value=None):
                        res = gmail_wh.lambda_handler(event, None)
        assert res["statusCode"] == 500
        assert "Queue" in res["body"] or "queue" in res["body"]

    def test_success_returns_200(self, gmail_wh):
        payload = {"emailAddress": "a@b.com", "historyId": "1"}
        raw = {"message": {"data": base64.b64encode(json.dumps(payload).encode()).decode()}}
        event = {
            "queryStringParameters": {"tenant_id": "t1", "tenant": "t"},
            "headers": {"Authorization": "Bearer x"},
            "body": base64.b64encode(json.dumps(raw).encode()).decode(),
        }
        with patch.object(gmail_wh, "verify_google_jwt", return_value="issuer@example.com"):
            with patch.object(gmail_wh, "parse_pubsub_body", return_value=payload):
                with patch.object(gmail_wh, "get_secret", return_value="gmail-events-{environment}-{tenant_id}"):
                    with patch.object(gmail_wh, "get_sqs_queue_url_by_name", return_value="https://sqs.example.com/q"):
                        with patch.object(gmail_wh, "send_to_sqs", return_value=True):
                            res = gmail_wh.lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "a@b.com" in res["body"]

    def test_send_failure_returns_500(self, gmail_wh):
        payload = {"emailAddress": "a@b.com"}
        raw = {"message": {"data": base64.b64encode(json.dumps(payload).encode()).decode()}}
        event = {
            "queryStringParameters": {"tenant_id": "t1", "tenant": "t"},
            "headers": {"Authorization": "Bearer x"},
            "body": base64.b64encode(json.dumps(raw).encode()).decode(),
        }
        with patch.object(gmail_wh, "verify_google_jwt", return_value="issuer@example.com"):
            with patch.object(gmail_wh, "parse_pubsub_body", return_value=payload):
                with patch.object(gmail_wh, "get_secret", return_value="gmail-events-{environment}-{tenant_id}"):
                    with patch.object(gmail_wh, "get_sqs_queue_url_by_name", return_value="https://sqs.example.com/q"):
                        with patch.object(gmail_wh, "send_to_sqs", return_value=False):
                            res = gmail_wh.lambda_handler(event, None)
        assert res["statusCode"] == 500
