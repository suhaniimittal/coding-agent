"""
Unit tests for slack-webhook webhook_handler.
Load the slack webhook_handler module from
file so it does not conflict with other webhook_handler in sys.modules.
"""

import base64
import hashlib
import hmac
import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

_slack_webhook_dir = Path(__file__).resolve().parent.parent
_slack_handler_path = _slack_webhook_dir / "webhook_handler.py"
_spec = importlib.util.spec_from_file_location("slack_webhook_handler", _slack_handler_path)
wh = importlib.util.module_from_spec(_spec)
sys.modules["slack_webhook_handler"] = wh
_spec.loader.exec_module(wh)


def _make_slack_signature(body: str, signing_secret: str, timestamp: str | None = None) -> str:
    ts = timestamp or str(int(time.time()))
    sig_basestring = f"v0:{ts}:{body}"
    computed = "v0=" + hmac.new(
        signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    return computed


class TestVerifySlackSignature:
    """Tests for verify_slack_signature."""

    def test_valid_signature(self):
        body = '{"type": "event_callback"}'
        secret = "my-signing-secret"
        ts = str(int(time.time()))
        sig = _make_slack_signature(body, secret, ts)
        event = {
            "headers": {"x-slack-signature": sig, "x-slack-request-timestamp": ts},
            "body": body,
            "isBase64Encoded": False,
        }
        assert wh.verify_slack_signature(event, secret) is True

    def test_invalid_signature(self):
        event = {
            "headers": {
                "x-slack-signature": "v0=wrong",
                "x-slack-request-timestamp": str(int(time.time())),
            },
            "body": "{}",
            "isBase64Encoded": False,
        }
        assert wh.verify_slack_signature(event, "secret") is False

    def test_missing_signature_returns_false(self):
        event = {
            "headers": {"x-slack-request-timestamp": str(int(time.time()))},
            "body": "{}",
            "isBase64Encoded": False,
        }
        assert wh.verify_slack_signature(event, "secret") is False

    def test_missing_timestamp_returns_false(self):
        event = {
            "headers": {"x-slack-signature": "v0=abc"},
            "body": "{}",
            "isBase64Encoded": False,
        }
        assert wh.verify_slack_signature(event, "secret") is False

    def test_old_timestamp_returns_false(self):
        body = "{}"
        secret = "s"
        ts = str(int(time.time()) - 400)  # > 5 min ago
        sig = _make_slack_signature(body, secret, ts)
        event = {
            "headers": {"x-slack-signature": sig, "x-slack-request-timestamp": ts},
            "body": body,
            "isBase64Encoded": False,
        }
        assert wh.verify_slack_signature(event, secret) is False

    def test_base64_encoded_body(self):
        body = '{"type": "event"}'
        secret = "s"
        ts = str(int(time.time()))
        sig = _make_slack_signature(body, secret, ts)
        event = {
            "headers": {"X-Slack-Signature": sig, "X-Slack-Request-Timestamp": ts},
            "body": base64.b64encode(body.encode()).decode(),
            "isBase64Encoded": True,
        }
        assert wh.verify_slack_signature(event, secret) is True


class TestLoadConfig:
    """_load_config returns the whole common bundle; the handler extracts what it needs."""

    ARN = "arn:aws:secretsmanager:us-east-1:1:secret:x"

    def test_no_arn_returns_empty_bundle(self):
        """No ARN configured → empty dict (caller falls back to env vars)."""
        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("LAMBDA_WEBHOOK_SIGNING_SECRET_ARN", None)
            assert wh._load_config() == {}

    def test_arn_returns_whole_bundle(self):
        """ARN set → the ENTIRE bundle is fetched in one call (no per-key key= arg)."""
        bundle = {
            "SLACK_SIGNING_SECRET": "from-sm",
            "ROUTING_DICT": {"T9": {"tenant_id": "t9", "tenant_name": "n9"}},
            "AUTH_CLIENT_ID": "extra-var",
        }

        def fake_get_secret(secret_id, key=None, default=None):
            assert secret_id == self.ARN  # read from LAMBDA_WEBHOOK_SIGNING_SECRET_ARN
            assert key is None             # whole bundle fetched, not key-by-key
            return bundle

        with patch.dict(
            "os.environ", {"LAMBDA_WEBHOOK_SIGNING_SECRET_ARN": self.ARN}, clear=False
        ), patch.object(wh, "get_secret", side_effect=fake_get_secret):
            assert wh._load_config() == bundle  # full bundle returned, not shaped

    def test_non_dict_bundle_returns_empty(self):
        """A non-dict secret value degrades to an empty bundle."""
        with patch.dict(
            "os.environ", {"LAMBDA_WEBHOOK_SIGNING_SECRET_ARN": self.ARN}, clear=False
        ), patch.object(wh, "get_secret", side_effect=lambda *a, **k: "not-a-dict"):
            assert wh._load_config() == {}

    def test_coerce_routing_variants(self):
        assert wh._coerce_routing({"T": {"tenant_id": "x"}}) == {"T": {"tenant_id": "x"}}
        assert wh._coerce_routing('{"T": {"tenant_id": "x"}}') == {"T": {"tenant_id": "x"}}
        assert wh._coerce_routing("bad json") == {}
        assert wh._coerce_routing("") == {}
        assert wh._coerce_routing(None) == {}


class TestForwardToPulsar:
    """Tests for forward_to_pulsar."""

    def test_success_returns_true(self):
        """200 response from Pulsar returns True."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(wh.requests, "post", return_value=mock_resp) as mock_post:
            result = wh.forward_to_pulsar(
                "https://acme.dev.aetherion.io", '{"type": "event_callback"}', {}, "tenant-1"
            )
        assert result is True
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "https://acme.dev.aetherion.io/api/v1/pulsar/slack/events"

    def test_trailing_slash_stripped_from_pulsar_url(self):
        """Trailing slash in pulsar_url is stripped before appending the path."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(wh.requests, "post", return_value=mock_resp) as mock_post:
            wh.forward_to_pulsar("https://acme.dev.aetherion.io/", "{}", {}, "t1")
        assert mock_post.call_args[0][0] == "https://acme.dev.aetherion.io/api/v1/pulsar/slack/events"

    def test_non_200_returns_false(self):
        """Non-200 response from Pulsar returns False."""
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch.object(wh.requests, "post", return_value=mock_resp):
            result = wh.forward_to_pulsar("https://acme.dev.aetherion.io", "{}", {}, "t1")
        assert result is False

    def test_timeout_returns_false(self):
        """Timeout exception returns False."""
        import requests as req_lib
        with patch.object(wh.requests, "post", side_effect=req_lib.exceptions.Timeout):
            result = wh.forward_to_pulsar("https://acme.dev.aetherion.io", "{}", {}, "t1")
        assert result is False

    def test_generic_exception_returns_false(self):
        """Generic exception returns False."""
        with patch.object(wh.requests, "post", side_effect=RuntimeError("Network error")):
            result = wh.forward_to_pulsar("https://acme.dev.aetherion.io", "{}", {}, "t1")
        assert result is False

    def test_forwards_slack_signature_headers(self):
        """Original Slack signature headers are included in the forwarded request."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        original_headers = {
            "x-slack-signature": "v0=abc123",
            "x-slack-request-timestamp": "1234567890",
        }
        with patch.object(wh.requests, "post", return_value=mock_resp) as mock_post:
            wh.forward_to_pulsar("https://acme.dev.aetherion.io", "{}", original_headers, "t1")
        forwarded = mock_post.call_args.kwargs["headers"]
        assert forwarded.get("x-slack-signature") == "v0=abc123"
        assert forwarded.get("x-slack-request-timestamp") == "1234567890"

    def test_uses_provided_internal_api_key(self):
        """The injected internal_api_key is sent as the X-Internal-Api-Key header."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch.object(wh.requests, "post", return_value=mock_resp) as mock_post:
            wh.forward_to_pulsar(
                "https://acme.dev.aetherion.io", "{}", {}, "t1", internal_api_key="ikey-123"
            )
        assert mock_post.call_args.kwargs["headers"]["X-Internal-Api-Key"] == "ikey-123"


class TestBuildPulsarUrl:
    """Tests for _build_pulsar_url (local vs deployed)."""

    def test_local_uses_localhost(self):
        with patch.object(wh, "_IS_LOCAL", True), patch.dict("os.environ", {}, clear=False):
            # default local target is the docker host port for pulsar
            assert wh._build_pulsar_url("acme").startswith("http://localhost")

    def test_deployed_builds_tenant_subdomain(self):
        with patch.object(wh, "_IS_LOCAL", False), patch.dict("os.environ", {"ENVIRONMENT": "dev"}):
            assert wh._build_pulsar_url("acme") == "https://acme.dev.aetherion.io"


def _event(team_id="T123", **body_extra):
    body = {"type": "event_callback"}
    if team_id is not None:
        body["team_id"] = team_id
    body.update(body_extra)
    return {"body": json.dumps(body), "headers": {}, "isBase64Encoded": False}


def _handle_deployed(event, bundle, verify="false", forward_return=True, forward_side_effect=None):
    """Drive lambda_handler in deployed (non-local) mode: signing secret, routing, and
    internal api key all come from `bundle` (the common ARN); env fallback is disabled.
    INTERNAL_API_KEY is defaulted into the bundle unless a test supplies its own.
    Returns (response, fwd_mock)."""
    bundle = {"INTERNAL_API_KEY": "ikey", **bundle}
    fwd = {"side_effect": forward_side_effect} if forward_side_effect else {"return_value": forward_return}
    with patch.dict("os.environ", {
        "ENVIRONMENT": "dev", "VERIFY_SLACK_SIGNATURE": verify,
        "LAMBDA_WEBHOOK_SIGNING_SECRET_ARN": TestLoadConfig.ARN,
    }, clear=False), patch.object(wh, "_IS_LOCAL", False), \
            patch.object(wh, "get_secret", side_effect=lambda *a, **k: bundle), \
            patch.object(wh, "forward_to_pulsar", **fwd) as mock_fwd:
        resp = wh.lambda_handler(event, None)
    return resp, mock_fwd


class TestRouting:
    """team_id → tenant resolution. Deployed: routing comes from the ARN bundle.
    Local (ENVIRONMENT=local): from env vars, with an unknown-team → demo fallback."""

    def test_bundle_routing_forwards(self):
        """Deployed: routing_dict from the bundle resolves the tenant and forwards."""
        bundle = {"SLACK_SIGNING_SECRET": "s",
                  "ROUTING_DICT": {"TX": {"tenant_id": "tX", "tenant_name": "nx"}}}
        resp, fwd = _handle_deployed(_event("TX"), bundle)
        assert resp["statusCode"] == 200
        assert fwd.call_args.kwargs["tenant_id"] == "tX"

    def test_bundle_bad_routing_json_unknown_team_200(self):
        """Deployed: malformed ROUTING_DICT in the bundle → empty routing → unknown → 200."""
        bundle = {"SLACK_SIGNING_SECRET": "s", "ROUTING_DICT": "NOT_JSON{{{"}
        resp, fwd = _handle_deployed(_event("TX"), bundle)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"]) == {"ok": True}
        fwd.assert_not_called()

    def test_bundle_unknown_team_200(self):
        """Deployed: team_id not in the bundle routing → 200, no forward."""
        bundle = {"SLACK_SIGNING_SECRET": "s", "ROUTING_DICT": {}}
        resp, fwd = _handle_deployed(_event("TUNKNOWN"), bundle)
        assert resp["statusCode"] == 200
        fwd.assert_not_called()

    def test_deployed_without_bundle_raises_but_returns_200(self):
        """Deployed with no ARN → required signing secret/routing missing → raises; the
        handler's top-level guard logs it and still returns 200 (no Slack retry storm)."""
        with patch.dict("os.environ", {"ENVIRONMENT": "dev", "VERIFY_SLACK_SIGNATURE": "false"},
                        clear=False):
            import os as _os
            _os.environ.pop("LAMBDA_WEBHOOK_SIGNING_SECRET_ARN", None)
            _os.environ.pop("SLACK_SIGNING_SECRET", None)
            _os.environ.pop("ROUTING_DICT", None)
            with patch.object(wh, "_IS_LOCAL", False), \
                    patch.object(wh, "forward_to_pulsar") as fwd:
                resp = wh.lambda_handler(_event("TX"), None)
        assert resp["statusCode"] == 200
        fwd.assert_not_called()

    def test_bundle_provides_signing_and_routing(self):
        """Deployed: signing secret + routing both from the bundle; verifies the HMAC
        with the bundle secret and routes via the bundle's routing_dict."""
        body = json.dumps({"type": "event_callback", "team_id": "TB"})
        secret = "bundle-secret"
        ts = str(int(time.time()))
        sig = _make_slack_signature(body, secret, ts)
        bundle = {"SLACK_SIGNING_SECRET": secret,
                  "ROUTING_DICT": {"TB": {"tenant_id": "tb", "tenant_name": "nb"}}}
        event = {"body": body,
                 "headers": {"x-slack-signature": sig, "x-slack-request-timestamp": ts},
                 "isBase64Encoded": False}
        resp, fwd = _handle_deployed(event, bundle, verify="true")
        assert resp["statusCode"] == 200
        assert fwd.call_args.kwargs["tenant_id"] == "tb"

    def test_local_env_routing_forwards(self):
        """Local: routing from the ROUTING_DICT env var; a known team forwards."""
        routing = {"TX": {"tenant_id": "tX", "tenant_name": "nx"}}
        with patch.dict("os.environ", {
            "ENVIRONMENT": "local", "VERIFY_SLACK_SIGNATURE": "false",
            "ROUTING_DICT": json.dumps(routing),
        }, clear=False), patch.object(wh, "_IS_LOCAL", True), \
                patch.object(wh, "forward_to_pulsar", return_value=True) as fwd:
            resp = wh.lambda_handler(_event("TX"), None)
        assert resp["statusCode"] == 200
        assert fwd.call_args.kwargs["tenant_id"] == "tX"

    def test_local_mock_defaults_unknown_team_to_demo(self):
        """Local: an unregistered team_id routes to the demo tenant (mock default)."""
        with patch.dict("os.environ", {
            "ENVIRONMENT": "local", "VERIFY_SLACK_SIGNATURE": "false",
            "ROUTING_DICT": "{}", "TENANT_ID": "demo-uuid",
        }, clear=False), patch.object(wh, "_IS_LOCAL", True), \
                patch.object(wh, "forward_to_pulsar", return_value=True) as fwd:
            resp = wh.lambda_handler(_event("TUNKNOWN"), None)
        assert resp["statusCode"] == 200
        assert fwd.call_args.kwargs["tenant_id"] == "demo-uuid"


class TestSlackLambdaHandler:
    """lambda_handler non-routing behaviour (deployed/bundle mode unless noted)."""

    def test_url_verification_challenge(self):
        """Challenge is answered before config load — no bundle/ARN needed."""
        event = {
            "body": json.dumps({"type": "url_verification", "challenge": "challenge-123"}),
            "headers": {},
            "isBase64Encoded": False,
        }
        with patch.dict("os.environ", {"ENVIRONMENT": "dev", "VERIFY_SLACK_SIGNATURE": "false"}):
            response = wh.lambda_handler(event, None)
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["challenge"] == "challenge-123"

    def test_invalid_signature_returns_401(self):
        body = json.dumps({"type": "event_callback", "team_id": "TB"})
        secret = "my-signing-secret"
        ts = str(int(time.time()))
        sig = _make_slack_signature(body, secret, ts)
        bad_sig = sig[:-4] + "XXXX"
        event = {
            "body": body,
            "headers": {"x-slack-signature": bad_sig, "x-slack-request-timestamp": ts},
            "isBase64Encoded": False,
        }
        bundle = {"SLACK_SIGNING_SECRET": secret, "ROUTING_DICT": {}}
        resp, _ = _handle_deployed(event, bundle, verify="true")
        assert resp["statusCode"] == 401
        assert "Invalid signature" in resp["body"]

    def test_invalid_json_returns_400(self):
        """Bad JSON is rejected before config load — no bundle/ARN needed."""
        event = {"body": "not json", "headers": {}, "isBase64Encoded": False}
        with patch.dict("os.environ", {"VERIFY_SLACK_SIGNATURE": "false"}):
            response = wh.lambda_handler(event, None)
        assert response["statusCode"] == 400
        assert "Invalid JSON" in response["body"]

    def test_missing_team_id_returns_400(self):
        """Event body with no team_id or team.id returns 400."""
        bundle = {"SLACK_SIGNING_SECRET": "s", "ROUTING_DICT": {}}
        resp, _ = _handle_deployed(_event(team_id=None), bundle)
        assert resp["statusCode"] == 400
        assert "Missing team_id" in resp["body"]

    def test_unknown_team_id_returns_200(self):
        """Unregistered team_id returns 200 to prevent Slack retry storms."""
        bundle = {"SLACK_SIGNING_SECRET": "s", "ROUTING_DICT": {}}
        resp, _ = _handle_deployed(_event("TUNKNOWN"), bundle)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"]) == {"ok": True}

    def test_success_forwards_to_pulsar(self):
        """Valid event with a known team is forwarded to Pulsar and returns 200."""
        bundle = {"SLACK_SIGNING_SECRET": "s",
                  "ROUTING_DICT": {"T123": {"tenant_id": "tenant-1", "tenant_name": "acme"}}}
        resp, fwd = _handle_deployed(_event("T123"), bundle)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"]) == {"ok": True}
        fwd.assert_called_once()

    def test_internal_api_key_from_bundle_is_forwarded(self):
        """The bundle's INTERNAL_API_KEY is resolved and passed to forward_to_pulsar."""
        bundle = {"SLACK_SIGNING_SECRET": "s", "INTERNAL_API_KEY": "top-secret",
                  "ROUTING_DICT": {"T123": {"tenant_id": "tenant-1", "tenant_name": "acme"}}}
        resp, fwd = _handle_deployed(_event("T123"), bundle)
        assert resp["statusCode"] == 200
        assert fwd.call_args.kwargs["internal_api_key"] == "top-secret"

    def test_forward_failure_still_returns_200(self):
        """Pulsar forward failure still returns 200 — failures are handled internally."""
        bundle = {"SLACK_SIGNING_SECRET": "s",
                  "ROUTING_DICT": {"T123": {"tenant_id": "tenant-1", "tenant_name": "acme"}}}
        resp, _ = _handle_deployed(_event("T123"), bundle, forward_return=False)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"]) == {"ok": True}

    def test_team_id_from_nested_team_object(self):
        """team_id is resolved from team.id when the top-level team_id key is absent."""
        bundle = {"SLACK_SIGNING_SECRET": "s",
                  "ROUTING_DICT": {"T_NESTED": {"tenant_id": "tenant-2", "tenant_name": "beta"}}}
        event = {"body": json.dumps({"type": "event_callback", "team": {"id": "T_NESTED"}}),
                 "headers": {}, "isBase64Encoded": False}
        resp, fwd = _handle_deployed(event, bundle)
        assert resp["statusCode"] == 200
        assert fwd.call_args.kwargs["tenant_id"] == "tenant-2"

    def test_unhandled_exception_returns_200(self):
        """Unhandled exception returns 200 to avoid Slack retry storms."""
        bundle = {"SLACK_SIGNING_SECRET": "s",
                  "ROUTING_DICT": {"T123": {"tenant_id": "tenant-1", "tenant_name": "acme"}}}
        resp, _ = _handle_deployed(_event("T123"), bundle,
                                   forward_side_effect=RuntimeError("Unexpected"))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"]) == {"ok": True}
