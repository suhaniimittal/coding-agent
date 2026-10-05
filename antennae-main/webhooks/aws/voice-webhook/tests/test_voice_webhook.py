"""
Tests for webhooks/aws/voice-webhook/webhook_handler.
Load module with voice-webhook dir on path so config resolves.
"""

import base64
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

_voice_webhook_dir = Path(__file__).resolve().parent.parent
_handler_path = _voice_webhook_dir / "webhook_handler.py"


def _load_voice_webhook_handler():
    """Load voice webhook_handler with twilio/requests mocked and voice-webhook on path."""
    # Mock twilio so import succeeds when twilio is not installed
    twilio_validator = MagicMock()
    twilio_request_validator = MagicMock()
    twilio_request_validator.RequestValidator = MagicMock()
    twilio_mod = MagicMock()
    twilio_mod.request_validator = twilio_request_validator
    with patch.dict(sys.modules, {"twilio": twilio_mod, "twilio.request_validator": twilio_request_validator}):
        spec = importlib.util.spec_from_file_location("voice_webhook_handler", _handler_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {_handler_path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        old_path = sys.path.copy()
        try:
            sys.path.insert(0, str(_voice_webhook_dir))
            spec.loader.exec_module(mod)
        finally:
            sys.path[:] = old_path
    return mod


@pytest.fixture(scope="module")
def voice_wh():
    return _load_voice_webhook_handler()


class TestParseFormData:
    def test_empty_returns_empty(self, voice_wh):
        assert voice_wh.parse_form_data("") == {}

    def test_single_param(self, voice_wh):
        assert voice_wh.parse_form_data("CallSid=CA123&From=%2B15551234567") == {
            "CallSid": "CA123",
            "From": "+15551234567",
        }

    def test_blank_values(self, voice_wh):
        out = voice_wh.parse_form_data("a=1&b=&c=3")
        assert out.get("b") == ""


class TestGenerateUrlVariants:
    def test_no_query_returns_single_url(self, voice_wh):
        event = {"path": "/voice", "headers": {"Host": "api.example.com"}, "requestContext": {"domainName": "api.example.com"}}
        urls = voice_wh.generate_url_variants(event)
        assert len(urls) == 1
        assert "https://api.example.com/voice" in urls[0]

    def test_raw_query_string_returns_single_url(self, voice_wh):
        event = {
            "path": "/voice",
            "rawQueryString": "tenant_id=t1&agent_id=a1",
            "headers": {},
            "requestContext": {"domainName": "api.example.com"},
        }
        urls = voice_wh.generate_url_variants(event)
        assert len(urls) == 1
        assert "tenant_id=t1&agent_id=a1" in urls[0]

    def test_query_params_generates_permutations(self, voice_wh):
        event = {
            "path": "/voice",
            "queryStringParameters": {"tenant_id": "t1", "agent_id": "a1"},
            "headers": {},
            "requestContext": {"domainName": "api.example.com"},
        }
        urls = voice_wh.generate_url_variants(event)
        assert len(urls) == 2  # 2! permutations
        assert all("tenant_id=" in u and "agent_id=" in u for u in urls)


class TestGenerateTwimlResponse:
    def test_contains_stream_url_and_token(self, voice_wh):
        twiml = voice_wh.generate_twiml_response("wss://stream.example.com/ws", "secret-token")
        assert "wss://stream.example.com/ws" in twiml
        assert "secret-token" in twiml
        assert "<Stream" in twiml
        assert "auth_token" in twiml


class TestBuildFullUrl:
    def test_local_uses_env(self, voice_wh):
        with patch.dict("os.environ", {"ENVIRONMENT": "local", "VOICE_WEBHOOK_URL": "https://local.example.com/voice"}):
            event = {}
            assert voice_wh.build_full_url(event) == "https://local.example.com/voice"

    def test_lambda_builds_from_event(self, voice_wh):
        with patch.dict("os.environ", {"ENVIRONMENT": "prod"}, clear=False):
            event = {
                "path": "/voice",
                "rawQueryString": "tenant_id=t1&agent_id=a1",
                "headers": {"Host": "api.example.com"},
                "requestContext": {"domainName": "api.example.com"},
            }
            url = voice_wh.build_full_url(event)
            assert "https://api.example.com/voice" in url
            assert "tenant_id=t1" in url

    def test_build_full_url_from_query_string_params(self, voice_wh):
        """Test build_full_url when rawQueryString is missing uses queryStringParameters."""
        with patch.dict("os.environ", {"ENVIRONMENT": "prod"}, clear=False):
            event = {
                "path": "/voice",
                "queryStringParameters": {"tenant_id": "t1", "agent_id": "a1"},
                "headers": {"Host": "api.example.com"},
                "requestContext": {"domainName": "api.example.com"},
            }
            url = voice_wh.build_full_url(event)
            assert "https://api.example.com/voice" in url
            assert "tenant_id=t1" in url
            assert "agent_id=a1" in url

    def test_build_full_url_without_query_string(self, voice_wh):
        """Test build_full_url when no query parameters are present."""
        with patch.dict("os.environ", {"ENVIRONMENT": "prod"}, clear=False):
            event = {
                "path": "/voice",
                "queryStringParameters": None,
                "headers": {"Host": "api.example.com"},
                "requestContext": {"domainName": "api.example.com"},
            }
            url = voice_wh.build_full_url(event)
            assert url == "https://api.example.com/voice"
            assert "?" not in url


class TestGetTwilioAuthToken:
    def test_missing_env_raises(self, voice_wh):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(Exception, match="LAMBDA_WEBHOOK_SIGNING_SECRET_ARN"):
                voice_wh.get_twilio_auth_token()

    def test_returns_secret(self, voice_wh):
        with patch.dict("os.environ", {"LAMBDA_WEBHOOK_SIGNING_SECRET_ARN": "arn:aws:secrets:test"}):
            with patch.object(voice_wh, "get_secret", return_value="twilio-secret"):
                assert voice_wh.get_twilio_auth_token() == "twilio-secret"


class TestGetMilkywayAuthToken:
    def test_missing_vars_returns_none(self, voice_wh):
        with patch.dict("os.environ", {"LAMBDA_WEBHOOK_T1_SECRETS_ARN": "arn:test"}):
            with patch.object(voice_wh, "get_secret", side_effect=lambda arn, key: None):
                assert voice_wh.get_milkyway_auth_token("t1") is None

    def test_cached_token_returned(self, voice_wh):
        voice_wh._token_cache["t1"] = {"token": "cached", "expiry": 1e12}
        assert voice_wh.get_milkyway_auth_token("t1") == "cached"
        voice_wh._token_cache.pop("t1", None)

    def test_fetches_and_caches(self, voice_wh):
        voice_wh._token_cache.pop("t1", None)
        with patch.dict("os.environ", {"LAMBDA_WEBHOOK_T1_SECRETS_ARN": "arn:test"}):
            with patch.object(
                voice_wh,
                "get_secret",
                side_effect=lambda arn, key: {"AUTH_TOKEN_URL": "https://auth/", "AUTH_CLIENT_ID": "id", "AUTH_CLIENT_SECRET": "sec"}.get(key, "https://auth/"),
            ):
                with patch.object(voice_wh, "requests") as req:
                    resp = Mock()
                    resp.status_code = 200
                    resp.json.return_value = {"access_token": "new-token", "expires_in": 3600}
                    req.post.return_value = resp
                    assert voice_wh.get_milkyway_auth_token("t1") == "new-token"
        voice_wh._token_cache.pop("t1", None)

    def test_auth_request_non_200_returns_none(self, voice_wh):
        voice_wh._token_cache.pop("t1", None)
        with patch.dict("os.environ", {"LAMBDA_WEBHOOK_T1_SECRETS_ARN": "arn:test"}):
            with patch.object(voice_wh, "get_secret", side_effect=lambda arn, key: "https://auth/" if key == "AUTH_TOKEN_URL" else ("id" if key == "AUTH_CLIENT_ID" else "sec")):
                with patch.object(voice_wh, "requests") as req:
                    resp = Mock()
                    resp.status_code = 401
                    resp.text = "Unauthorized"
                    req.post.return_value = resp
                    assert voice_wh.get_milkyway_auth_token("t1") is None
        voice_wh._token_cache.pop("t1", None)

    def test_auth_response_missing_access_token_returns_none(self, voice_wh):
        voice_wh._token_cache.pop("t1", None)
        with patch.dict("os.environ", {"LAMBDA_WEBHOOK_T1_SECRETS_ARN": "arn:test"}):
            with patch.object(voice_wh, "get_secret", side_effect=lambda arn, key: "https://auth/" if key == "AUTH_TOKEN_URL" else ("id" if key == "AUTH_CLIENT_ID" else "sec")):
                with patch.object(voice_wh, "requests") as req:
                    resp = Mock()
                    resp.status_code = 200
                    resp.json.return_value = {"expires_in": 3600}
                    req.post.return_value = resp
                    assert voice_wh.get_milkyway_auth_token("t1") is None
        voice_wh._token_cache.pop("t1", None)

    def test_auth_request_exception_returns_none(self, voice_wh):
        voice_wh._token_cache.pop("t1", None)
        with patch.dict("os.environ", {"LAMBDA_WEBHOOK_T1_SECRETS_ARN": "arn:test"}):
            with patch.object(
                voice_wh,
                "get_secret",
                side_effect=lambda arn, key: (
                    "https://auth/"
                    if key == "AUTH_TOKEN_URL"
                    else ("id" if key == "AUTH_CLIENT_ID" else "sec")
                ),
            ):
                # Use the same RequestException class from the module's requests import
                with patch.object(
                    voice_wh.requests,
                    "post",
                    side_effect=voice_wh.requests.RequestException("Network error"),
                ):
                    assert voice_wh.get_milkyway_auth_token("t1") is None
        voice_wh._token_cache.pop("t1", None)


class TestVerifyTwilioSignature:
    def test_missing_signature_returns_false(self, voice_wh):
        event = {"headers": {}}
        result = voice_wh.verify_twilio_signature(
            event, "token", "https://example.com/voice", {}
        )
        assert result is False

    def test_valid_signature_returns_true(self, voice_wh):
        event = {"headers": {"X-Twilio-Signature": "valid-sig"}}
        with patch.object(voice_wh, "RequestValidator") as V:
            V.return_value.validate.return_value = True
            result = voice_wh.verify_twilio_signature(
                event, "auth", "https://example.com/voice", {"CallSid": "CA123"}
            )
            assert result is True

    def test_alternative_url_validation_success(self, voice_wh):
        """Test fallback to alternative URL variants when rawQueryString is missing."""
        event = {
            "headers": {"X-Twilio-Signature": "valid-sig"},
            # No rawQueryString - triggers alternative validation
        }
        with patch.object(voice_wh, "RequestValidator") as V:
            # First validation fails, second (with variant URL) succeeds
            V.return_value.validate.side_effect = [False, True]
            with patch.object(
                voice_wh,
                "generate_url_variants",
                return_value=["https://alt1.com/voice", "https://alt2.com/voice"],
            ):
                result = voice_wh.verify_twilio_signature(
                    event, "auth", "https://example.com/voice", {"CallSid": "CA123"}
                )
                assert result is True

    def test_alternative_url_validation_all_fail(self, voice_wh):
        """Test when all URL variants fail validation."""
        event = {
            "headers": {"X-Twilio-Signature": "valid-sig"},
        }
        with patch.object(voice_wh, "RequestValidator") as V:
            V.return_value.validate.return_value = False
            with patch.object(
                voice_wh,
                "generate_url_variants",
                return_value=["https://alt1.com/voice"],
            ):
                result = voice_wh.verify_twilio_signature(
                    event, "auth", "https://example.com/voice", {"CallSid": "CA123"}
                )
                assert result is False

    def test_skips_duplicate_url_variant(self, voice_wh):
        """Test that duplicate URL in variants is skipped."""
        event = {
            "headers": {"X-Twilio-Signature": "valid-sig"},
        }
        with patch.object(voice_wh, "RequestValidator") as V:
            V.return_value.validate.return_value = False
            # Return the same URL as the original - should be skipped
            with patch.object(
                voice_wh,
                "generate_url_variants",
                return_value=["https://example.com/voice"],
            ):
                result = voice_wh.verify_twilio_signature(
                    event, "auth", "https://example.com/voice", {"CallSid": "CA123"}
                )
                assert result is False
                # validate should only be called once (original URL)
                assert V.return_value.validate.call_count == 1

    def test_exception_returns_false(self, voice_wh):
        """Test that exceptions in signature validation return False."""
        event = {"headers": {"X-Twilio-Signature": "valid-sig"}}
        with patch.object(voice_wh, "RequestValidator") as V:
            V.return_value.validate.side_effect = Exception("Validation error")
            result = voice_wh.verify_twilio_signature(
                event, "auth", "https://example.com/voice", {"CallSid": "CA123"}
            )
            assert result is False


class TestVoiceLambdaHandler:
    @pytest.fixture
    def base_event(self):
        return {
            "body": "CallSid=CA123&From=%2B15551234567&To=%2B15559876543",
            "path": "/voice",
            "queryStringParameters": {"tenant_id": "t1", "agent_id": "a1"},
            "headers": {"Host": "api.example.com", "X-Twilio-Signature": "sig"},
            "requestContext": {"domainName": "api.example.com", "requestId": "req-1"},
        }

    @pytest.fixture
    def mock_context(self):
        ctx = Mock()
        ctx.aws_request_id = "req-1"
        return ctx

    def test_403_invalid_signature(self, voice_wh, base_event, mock_context):
        with patch.object(voice_wh, "get_twilio_auth_token", return_value="twilio-token"):
            with patch.object(voice_wh, "build_full_url", return_value="https://api.example.com/voice"):
                with patch.object(voice_wh, "verify_twilio_signature", return_value=False):
                    res = voice_wh.lambda_handler(base_event, mock_context)
        assert res["statusCode"] == 403
        assert "Invalid signature" in res["body"]

    def test_400_missing_params(self, voice_wh, mock_context):
        event = {
            "body": "CallSid=CA123",
            "path": "/voice",
            "queryStringParameters": {},  # missing tenant_id, agent_id
            "headers": {"Host": "api.example.com", "X-Twilio-Signature": "sig"},
            "requestContext": {"domainName": "api.example.com"},
        }
        with patch.object(voice_wh, "get_twilio_auth_token", return_value="twilio-token"):
            with patch.object(voice_wh, "build_full_url", return_value="https://api.example.com/voice"):
                with patch.object(voice_wh, "verify_twilio_signature", return_value=True):
                    res = voice_wh.lambda_handler(event, mock_context)
        assert res["statusCode"] == 400
        assert "Missing" in res["body"]

    def test_200_success_with_twiml(self, voice_wh, base_event, mock_context):
        with patch.object(voice_wh, "get_twilio_auth_token", return_value="twilio-token"):
            with patch.object(voice_wh, "build_full_url", return_value="https://api.example.com/voice"):
                with patch.object(voice_wh, "verify_twilio_signature", return_value=True):
                    with patch.object(voice_wh, "get_milkyway_auth_token", return_value="stream-token"):
                        res = voice_wh.lambda_handler(base_event, mock_context)
        assert res["statusCode"] == 200
        assert "application/xml" in res["headers"]["Content-Type"]
        assert "<Stream" in res["body"]
        assert "stream-token" in res["body"]

    def test_200_fallback_when_token_missing(self, voice_wh, base_event, mock_context):
        with patch.object(voice_wh, "get_twilio_auth_token", return_value="twilio-token"):
            with patch.object(voice_wh, "build_full_url", return_value="https://api.example.com/voice"):
                with patch.object(voice_wh, "verify_twilio_signature", return_value=True):
                    with patch.object(voice_wh, "get_milkyway_auth_token", return_value=None):
                        res = voice_wh.lambda_handler(base_event, mock_context)
        assert res["statusCode"] == 200
        assert "application/xml" in res["headers"]["Content-Type"]
        assert "Thank you for calling" in res["body"]

    def test_200_fallback_when_twiml_block_raises(self, voice_wh, base_event, mock_context):
        """Exception inside TwiML generation (e.g. get_milkyway succeeds but later code raises) returns fallback."""
        with patch.object(voice_wh, "get_twilio_auth_token", return_value="twilio-token"):
            with patch.object(voice_wh, "build_full_url", return_value="https://api.example.com/voice"):
                with patch.object(voice_wh, "verify_twilio_signature", return_value=True):
                    with patch.object(voice_wh, "get_milkyway_auth_token", return_value="stream-token"):
                        with patch.object(voice_wh, "generate_twiml_response", side_effect=RuntimeError("TwiML error")):
                            res = voice_wh.lambda_handler(base_event, mock_context)
        assert res["statusCode"] == 200
        assert "Thank you for calling" in res["body"]

    def test_200_fallback_when_outer_exception(self, voice_wh, base_event, mock_context):
        """Outer exception (e.g. get_twilio_auth_token raises) returns fallback TwiML."""
        with patch.object(
            voice_wh, "get_twilio_auth_token", side_effect=Exception("Secrets error")
        ):
            res = voice_wh.lambda_handler(base_event, mock_context)
        assert res["statusCode"] == 200
        assert "Thank you for calling" in res["body"]

    def test_200_fallback_when_twilio_token_none(self, voice_wh, base_event, mock_context):
        """When get_twilio_auth_token returns None, should raise and return fallback."""
        with patch.object(voice_wh, "get_twilio_auth_token", return_value=None):
            res = voice_wh.lambda_handler(base_event, mock_context)
        assert res["statusCode"] == 200
        assert "Thank you for calling" in res["body"]

    def test_base64_encoded_body(self, voice_wh, mock_context):
        """Test handling of base64-encoded body."""

        body_content = "CallSid=CA123&From=%2B15551234567&To=%2B15559876543"
        encoded_body = base64.b64encode(body_content.encode()).decode()
        event = {
            "body": encoded_body,
            "isBase64Encoded": True,
            "path": "/voice",
            "queryStringParameters": {"tenant_id": "t1", "agent_id": "a1"},
            "headers": {"Host": "api.example.com", "X-Twilio-Signature": "sig"},
            "requestContext": {"domainName": "api.example.com", "requestId": "req-1"},
        }
        with patch.object(voice_wh, "get_twilio_auth_token", return_value="twilio-token"):
            with patch.object(
                voice_wh, "build_full_url", return_value="https://api.example.com/voice"
            ):
                with patch.object(voice_wh, "verify_twilio_signature", return_value=True):
                    with patch.object(
                        voice_wh, "get_milkyway_auth_token", return_value="stream-token"
                    ):
                        res = voice_wh.lambda_handler(event, mock_context)
        assert res["statusCode"] == 200
        assert "<Stream" in res["body"]

    def test_lambda_handler_with_none_context(self, voice_wh, base_event):
        """Test lambda_handler when context is None."""
        with patch.object(voice_wh, "get_twilio_auth_token", return_value="twilio-token"):
            with patch.object(
                voice_wh, "build_full_url", return_value="https://api.example.com/voice"
            ):
                with patch.object(voice_wh, "verify_twilio_signature", return_value=True):
                    with patch.object(
                        voice_wh, "get_milkyway_auth_token", return_value="stream-token"
                    ):
                        res = voice_wh.lambda_handler(base_event, None)
        assert res["statusCode"] == 200
