"""Unit tests for webhook_handler.py — the unified Lambda entrypoint."""

import base64
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlencode

_unified_dir = Path(__file__).resolve().parent.parent
if str(_unified_dir) not in sys.path:
    sys.path.insert(0, str(_unified_dir))

import webhook_handler


def _sign_slack(secret: str, timestamp: str, body: str) -> str:
    basestring = f"v0:{timestamp}:{body}"
    return "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()


def _slack_event(body_obj: dict, secret: str = "slack-secret") -> dict:
    body = json.dumps(body_obj)
    ts = str(int(time.time()))
    sig = _sign_slack(secret, ts, body)
    return {
        "pathParameters": {"provider_slug": "slack"},
        "headers": {"X-Slack-Signature": sig, "X-Slack-Request-Timestamp": ts},
        "body": body,
        "isBase64Encoded": False,
    }


def _slack_interaction_event(inner_payload: dict, secret: str = "slack-secret") -> dict:
    """Slack Interactivity payloads: form-encoded, real JSON in a "payload" field,
    signed the same HMAC-over-raw-body way as Events."""
    body = urlencode({"payload": json.dumps(inner_payload)})
    ts = str(int(time.time()))
    sig = _sign_slack(secret, ts, body)
    return {
        "pathParameters": {"provider_slug": "slack"},
        "headers": {
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        "body": body,
        "isBase64Encoded": False,
    }


def _github_event(body_obj: dict, secret: str = "gh-secret", tenant_id: str = "tenant-1") -> dict:
    body = json.dumps(body_obj)
    sig = "sha256=" + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return {
        "pathParameters": {"provider_slug": "github"},
        "headers": {
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "pull_request",
            "X-Tenant-ID": tenant_id,
        },
        "body": body,
        "isBase64Encoded": False,
    }


class TestUnknownProvider:
    def test_returns_404(self):
        response = webhook_handler.lambda_handler(
            {"pathParameters": {"provider_slug": "bogus"}, "headers": {}, "body": "{}"}, None
        )
        assert response["statusCode"] == 404

    def test_missing_path_parameters_returns_404(self):
        response = webhook_handler.lambda_handler({"headers": {}, "body": "{}"}, None)
        assert response["statusCode"] == 404


class TestSlackPath:
    def test_valid_event_publishes_and_returns_200(self):
        captured = []
        event = _slack_event(
            {
                "type": "event_callback",
                "team_id": "T1",
                "event_id": "Ev1",
                "event": {"type": "app_mention", "channel": "C1", "user": "U1", "text": "hi"},
            }
        )
        with (
            patch.dict(
                "os.environ",
                {
                    "SLACK_SIGNING_SECRET": "slack-secret",
                    "ROUTING_DICT": json.dumps({"T1": {"tenant_id": "tenant-1"}}),
                },
            ),
            patch.object(
                webhook_handler,
                "publish_canonical_event",
                side_effect=lambda e, **kw: captured.append(e),
            ),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert len(captured) == 1
        assert captured[0]["event_type"] == "app_mention.received"
        assert captured[0]["tenant_id"] == "tenant-1"

    def test_invalid_signature_returns_401(self):
        event = _slack_event({"type": "event_callback"}, secret="right-secret")
        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": "wrong-secret"}):
            response = webhook_handler.lambda_handler(event, None)
        assert response["statusCode"] == 401

    def test_url_verification_challenge_short_circuits(self):
        body_obj = {"type": "url_verification", "challenge": "abc123"}
        event = _slack_event(body_obj)
        with (
            patch.dict("os.environ", {"SLACK_SIGNING_SECRET": "slack-secret"}),
            patch.object(webhook_handler, "publish_canonical_event") as mock_publish,
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["challenge"] == "abc123"
        mock_publish.assert_not_called()

    def test_unregistered_workspace_returns_200_not_400(self):
        """Slack retry-bombs on non-2xx for unregistered workspaces — must stay 200."""
        event = _slack_event({"type": "event_callback", "team_id": "T-unknown", "event": {}})
        with patch.dict(
            "os.environ", {"SLACK_SIGNING_SECRET": "slack-secret", "ROUTING_DICT": "{}"}
        ):
            response = webhook_handler.lambda_handler(event, None)
        assert response["statusCode"] == 200

    def test_base64_encoded_body_is_decoded(self):
        body_obj = {"type": "url_verification", "challenge": "xyz"}
        body = json.dumps(body_obj)
        ts = str(int(time.time()))
        sig = _sign_slack("slack-secret", ts, body)
        event = {
            "pathParameters": {"provider_slug": "slack"},
            "headers": {"X-Slack-Signature": sig, "X-Slack-Request-Timestamp": ts},
            "body": base64.b64encode(body.encode()).decode(),
            "isBase64Encoded": True,
        }
        with patch.dict("os.environ", {"SLACK_SIGNING_SECRET": "slack-secret"}):
            response = webhook_handler.lambda_handler(event, None)
        assert json.loads(response["body"])["challenge"] == "xyz"


class TestSlackInteractivity:
    """block_actions / view_submission must be forwarded synchronously to the
    resolved tenant's Pulsar pod, not published to SQS — see
    ProviderPlugin.direct_forward_target's docstring for why (trigger_id)."""

    def _env(self, **extra):
        return {
            "SLACK_SIGNING_SECRET": "slack-secret",
            "ROUTING_DICT": json.dumps({"T1": {"tenant_id": "tenant-1", "tenant_name": "acme"}}),
            "INTERNAL_API_KEY": "internal-key",
            "ENVIRONMENT": "dev",
            **extra,
        }

    def test_block_actions_forwards_synchronously_instead_of_publishing(self):
        event = _slack_interaction_event(
            {"type": "block_actions", "team": {"id": "T1"}, "trigger_id": "trg1"}
        )
        mock_resp = Mock(status_code=200, text="", headers={})
        with (
            patch.dict("os.environ", self._env(), clear=True),
            patch.object(webhook_handler, "publish_canonical_event") as mock_publish,
            patch.object(webhook_handler.requests, "post", return_value=mock_resp) as mock_post,
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        mock_publish.assert_not_called()
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://acme.dev.aetherion.io/api/v1/pulsar/api/slack/interactions"
        assert kwargs["data"] == event["body"].encode()
        assert kwargs["headers"]["X-Internal-Api-Key"] == "internal-key"
        assert kwargs["headers"]["x-slack-signature"] == event["headers"]["X-Slack-Signature"]
        assert kwargs["headers"]["content-type"] == "application/x-www-form-urlencoded"

    def test_view_submission_also_forwards(self):
        event = _slack_interaction_event({"type": "view_submission", "team": {"id": "T1"}})
        mock_resp = Mock(status_code=200, text="", headers={})
        with (
            patch.dict("os.environ", self._env(), clear=True),
            patch.object(webhook_handler, "publish_canonical_event") as mock_publish,
            patch.object(webhook_handler.requests, "post", return_value=mock_resp) as mock_post,
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        mock_publish.assert_not_called()
        mock_post.assert_called_once()

    def test_empty_downstream_body_is_relayed_not_replaced_with_ok_true(self):
        """Slack closes a modal only on an empty body; an {"ok": true} of our own
        makes it report "We had some trouble connecting" and keep the modal open."""
        event = _slack_interaction_event({"type": "view_submission", "team": {"id": "T1"}})
        mock_resp = Mock(status_code=200, text="", headers={})
        with (
            patch.dict("os.environ", self._env(), clear=True),
            patch.object(webhook_handler, "publish_canonical_event"),
            patch.object(webhook_handler.requests, "post", return_value=mock_resp),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["body"] == ""
        assert "ok" not in response["body"]

    def test_response_action_body_is_relayed_verbatim(self):
        """A view_submission validation response must reach Slack untouched, or
        field errors never render on the modal."""
        event = _slack_interaction_event({"type": "view_submission", "team": {"id": "T1"}})
        body = '{"response_action": "errors", "errors": {"final_feedback": "Required."}}'
        mock_resp = Mock(status_code=200, text=body, headers={"Content-Type": "application/json"})
        with (
            patch.dict("os.environ", self._env(), clear=True),
            patch.object(webhook_handler, "publish_canonical_event"),
            patch.object(webhook_handler.requests, "post", return_value=mock_resp),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["response_action"] == "errors"
        assert response["headers"]["Content-Type"] == "application/json"

    def test_downstream_non_200_status_is_relayed(self):
        event = _slack_interaction_event({"type": "view_submission", "team": {"id": "T1"}})
        mock_resp = Mock(status_code=401, text="", headers={})
        with (
            patch.dict("os.environ", self._env(), clear=True),
            patch.object(webhook_handler, "publish_canonical_event"),
            patch.object(webhook_handler.requests, "post", return_value=mock_resp),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 401

    def test_unregistered_workspace_returns_200_without_forwarding_or_publishing(self):
        event = _slack_interaction_event({"type": "block_actions", "team": {"id": "T-unknown"}})
        with (
            patch.dict("os.environ", self._env(ROUTING_DICT="{}"), clear=True),
            patch.object(webhook_handler, "publish_canonical_event") as mock_publish,
            patch.object(webhook_handler.requests, "post") as mock_post,
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        mock_publish.assert_not_called()
        mock_post.assert_not_called()

    def test_forward_failure_is_swallowed_and_still_returns_200(self):
        """A downed tenant pod must not turn into a Slack-visible error/retry storm."""
        event = _slack_interaction_event({"type": "block_actions", "team": {"id": "T1"}})
        with (
            patch.dict("os.environ", self._env(), clear=True),
            patch.object(webhook_handler, "publish_canonical_event") as mock_publish,
            patch.object(
                webhook_handler.requests, "post", side_effect=RuntimeError("connection refused")
            ),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert response["body"] == ""
        mock_publish.assert_not_called()

    def test_events_api_path_still_publishes_unaffected(self):
        """Sanity check: adding the Interactivity branch must not change the
        existing Events API (event_callback) path at all."""
        captured = []
        event = _slack_event(
            {
                "type": "event_callback",
                "team_id": "T1",
                "event": {"type": "message", "channel": "C1", "user": "U1"},
            }
        )
        with (
            patch.dict("os.environ", self._env(), clear=True),
            patch.object(
                webhook_handler,
                "publish_canonical_event",
                side_effect=lambda e, **kw: captured.append(e),
            ),
            patch.object(webhook_handler.requests, "post") as mock_post,
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert len(captured) == 1
        mock_post.assert_not_called()


class TestGitHubPath:
    def test_valid_event_publishes_and_returns_200(self):
        captured = []
        event = _github_event(
            {
                "action": "opened",
                "pull_request": {"number": 1, "merged": False},
                "repository": {"full_name": "acme/repo"},
                "sender": {"login": "octocat"},
            }
        )
        with (
            patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "gh-secret"}),
            patch.object(
                webhook_handler,
                "publish_canonical_event",
                side_effect=lambda e, **kw: captured.append(e),
            ),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert captured[0]["event_type"] == "pull_request.opened"
        assert captured[0]["tenant_id"] == "tenant-1"

    def test_invalid_signature_returns_401(self):
        event = _github_event({"action": "opened"}, secret="right")
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "wrong"}):
            response = webhook_handler.lambda_handler(event, None)
        assert response["statusCode"] == 401

    def test_missing_tenant_header_returns_400_not_200(self):
        """Unlike Slack, GitHub tenant-resolution failures should 400, not silently 200."""
        body = json.dumps({"action": "opened"})
        sig = "sha256=" + hmac.new(b"gh-secret", body.encode(), hashlib.sha256).hexdigest()
        event = {
            "pathParameters": {"provider_slug": "github"},
            "headers": {"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request"},
            "body": body,
            "isBase64Encoded": False,
        }
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "gh-secret"}):
            response = webhook_handler.lambda_handler(event, None)
        assert response["statusCode"] == 400


class TestJiraPath:
    def test_valid_event_publishes_and_returns_200(self):
        captured = []
        body = json.dumps(
            {"webhookEvent": "jira:issue_created", "issue": {"key": "PLAT-1", "fields": {}}}
        )
        event = {
            "pathParameters": {"provider_slug": "jira"},
            "headers": {"X-Jira-Webhook-Secret": "jira-secret"},
            "queryStringParameters": {"tenant_id": "tenant-1"},
            "body": body,
            "isBase64Encoded": False,
        }
        with (
            patch.dict("os.environ", {"JIRA_WEBHOOK_SECRET": "jira-secret"}),
            patch.object(
                webhook_handler,
                "publish_canonical_event",
                side_effect=lambda e, **kw: captured.append(e),
            ),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert captured[0]["event_type"] == "issue.created"

    def test_missing_tenant_query_param_returns_400(self):
        event = {
            "pathParameters": {"provider_slug": "jira"},
            "headers": {"X-Jira-Webhook-Secret": "jira-secret"},
            "queryStringParameters": {},
            "body": "{}",
            "isBase64Encoded": False,
        }
        with patch.dict("os.environ", {"JIRA_WEBHOOK_SECRET": "jira-secret"}):
            response = webhook_handler.lambda_handler(event, None)
        assert response["statusCode"] == 400


class TestSecretsConfig:
    """Config sourced from LAMBDA_WEBHOOK_SIGNING_SECRET_ARN + LAMBDA_WEBHOOK_<TENANT_NAME>_ARN
    secrets, with tenant values overriding common ones."""

    def test_common_secret_supplies_slack_config(self):
        """No SLACK_SIGNING_SECRET/ROUTING_DICT env vars — everything comes from
        the common secret."""
        captured = []
        event = _slack_event(
            {
                "type": "event_callback",
                "team_id": "T1",
                "event_id": "Ev1",
                "event": {"type": "app_mention", "channel": "C1", "user": "U1"},
            }
        )

        def fake_get_secret(secret_identifier, key=None, ttl=None, default=None):
            assert secret_identifier == "arn:common"
            return {
                "SLACK_SIGNING_SECRET": "slack-secret",
                "ROUTING_DICT": {"T1": {"tenant_id": "tenant-1"}},
            }

        with (
            patch.dict("os.environ", {"LAMBDA_WEBHOOK_SIGNING_SECRET_ARN": "arn:common"}),
            patch.object(webhook_handler, "get_secret", side_effect=fake_get_secret),
            patch.object(
                webhook_handler,
                "publish_canonical_event",
                side_effect=lambda e, **kw: captured.append(e),
            ),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert captured[0]["tenant_id"] == "tenant-1"

    def test_routing_dict_as_json_string_in_secret(self):
        """ROUTING_DICT stored as a JSON string (not a nested object) still parses."""
        captured = []
        event = _slack_event(
            {"type": "event_callback", "team_id": "T1", "event": {"type": "message"}}
        )

        def fake_get_secret(secret_identifier, key=None, ttl=None, default=None):
            return {
                "SLACK_SIGNING_SECRET": "slack-secret",
                "ROUTING_DICT": json.dumps({"T1": {"tenant_id": "tenant-1"}}),
            }

        with (
            patch.dict("os.environ", {"LAMBDA_WEBHOOK_SIGNING_SECRET_ARN": "arn:common"}),
            patch.object(webhook_handler, "get_secret", side_effect=fake_get_secret),
            patch.object(
                webhook_handler,
                "publish_canonical_event",
                side_effect=lambda e, **kw: captured.append(e),
            ),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert captured[0]["tenant_id"] == "tenant-1"

    def test_tenant_secret_overrides_common(self):
        """After tenant resolution, LAMBDA_WEBHOOK_<TENANT_NAME>_SECRETS_ARN values
        win on key clashes — here the tenant supplies its own
        UNIFIED_SQS_QUEUE_NAME_PATTERN. Tenant name comes from the tenant's
        ROUTING_DICT entry."""
        published = []
        event = _slack_event(
            {"type": "event_callback", "team_id": "T1", "event": {"type": "message"}}
        )

        secrets_by_arn = {
            "arn:common": {
                "SLACK_SIGNING_SECRET": "slack-secret",
                "ROUTING_DICT": {"T1": {"tenant_id": "tenant-1", "tenant_name": "acme"}},
                "UNIFIED_SQS_QUEUE_NAME_PATTERN": "unified-events-{environment}-{tenant_id}",
            },
            "arn:acme": {
                "UNIFIED_SQS_QUEUE_NAME_PATTERN": "tenant-custom-{environment}-{tenant_id}",
            },
        }

        def fake_get_secret(secret_identifier, key=None, ttl=None, default=None):
            return secrets_by_arn.get(secret_identifier, default)

        with (
            patch.dict(
                "os.environ",
                {
                    "LAMBDA_WEBHOOK_SIGNING_SECRET_ARN": "arn:common",
                    "LAMBDA_WEBHOOK_ACME_SECRETS_ARN": "arn:acme",
                },
            ),
            patch.object(webhook_handler, "get_secret", side_effect=fake_get_secret),
            patch.object(
                webhook_handler,
                "publish_canonical_event",
                side_effect=lambda e, **kw: published.append((e, kw)),
            ),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        canonical, kwargs = published[0]
        assert canonical["tenant_id"] == "tenant-1"
        assert kwargs["queue_name_pattern"] == "tenant-custom-{environment}-{tenant_id}"

    def test_tenant_name_maps_to_env_var(self):
        """Tenant names resolve to LAMBDA_WEBHOOK_<TENANT_NAME>_SECRETS_ARN (the
        shared per-tenant convention), uppercased with non-alphanumerics replaced
        by underscores."""
        assert (
            webhook_handler._tenant_secret_env_var("calfus") == "LAMBDA_WEBHOOK_CALFUS_SECRETS_ARN"
        )
        assert (
            webhook_handler._tenant_secret_env_var("acme-corp")
            == "LAMBDA_WEBHOOK_ACME_CORP_SECRETS_ARN"
        )

    def test_query_param_tenant_name_for_non_slack_providers(self):
        """GitHub/Jira have no ROUTING_DICT entry — tenant name comes from the
        ?tenant= query param baked into the webhook URL (gmail-webhook convention)."""
        published = []
        body = json.dumps({"webhookEvent": "jira:issue_created", "issue": {"key": "PLAT-1"}})
        event = {
            "pathParameters": {"provider_slug": "jira"},
            "headers": {"X-Jira-Webhook-Secret": "jira-secret"},
            "queryStringParameters": {"tenant_id": "tenant-1", "tenant": "acme"},
            "body": body,
            "isBase64Encoded": False,
        }

        secrets_by_arn = {
            "arn:common": {"JIRA_WEBHOOK_SECRET": "jira-secret"},
            "arn:acme": {"UNIFIED_SQS_QUEUE_NAME_PATTERN": "acme-{environment}-{tenant_id}"},
        }

        def fake_get_secret(secret_identifier, key=None, ttl=None, default=None):
            return secrets_by_arn.get(secret_identifier, default)

        with (
            patch.dict(
                "os.environ",
                {
                    "LAMBDA_WEBHOOK_SIGNING_SECRET_ARN": "arn:common",
                    "LAMBDA_WEBHOOK_ACME_SECRETS_ARN": "arn:acme",
                },
            ),
            patch.object(webhook_handler, "get_secret", side_effect=fake_get_secret),
            patch.object(
                webhook_handler,
                "publish_canonical_event",
                side_effect=lambda e, **kw: published.append((e, kw)),
            ),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        canonical, kwargs = published[0]
        assert canonical["tenant_id"] == "tenant-1"
        assert kwargs["queue_name_pattern"] == "acme-{environment}-{tenant_id}"

    def test_secret_fetch_failure_falls_back_to_env(self):
        """A Secrets Manager outage must not take down ingestion — env fallback holds."""
        captured = []
        event = _slack_event(
            {"type": "event_callback", "team_id": "T1", "event": {"type": "message"}}
        )

        with (
            patch.dict(
                "os.environ",
                {
                    "LAMBDA_WEBHOOK_SIGNING_SECRET_ARN": "arn:common",
                    "SLACK_SIGNING_SECRET": "slack-secret",
                    "ROUTING_DICT": json.dumps({"T1": {"tenant_id": "tenant-1"}}),
                },
            ),
            patch.object(
                webhook_handler, "get_secret", side_effect=RuntimeError("secretsmanager down")
            ),
            patch.object(
                webhook_handler,
                "publish_canonical_event",
                side_effect=lambda e, **kw: captured.append(e),
            ),
        ):
            response = webhook_handler.lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert captured[0]["tenant_id"] == "tenant-1"


class TestErrorHandling:
    def test_invalid_json_body_returns_400(self):
        event = _github_event({"action": "opened"})
        event["body"] = "not valid json {"
        # Re-sign against the new (invalid-JSON) body so it passes signature verification
        # and reaches the JSON-parsing step.
        sig = "sha256=" + hmac.new(b"gh-secret", event["body"].encode(), hashlib.sha256).hexdigest()
        event["headers"]["X-Hub-Signature-256"] = sig
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": "gh-secret"}):
            response = webhook_handler.lambda_handler(event, None)
        assert response["statusCode"] == 400

    def test_unhandled_exception_returns_200(self):
        """Providers that retry-bomb on non-2xx must still get 200 even on our own bugs."""
        event = _slack_event({"type": "event_callback", "team_id": "T1", "event": {}})
        with (
            patch.dict(
                "os.environ",
                {
                    "SLACK_SIGNING_SECRET": "slack-secret",
                    "ROUTING_DICT": json.dumps({"T1": {"tenant_id": "t1"}}),
                },
            ),
            patch.object(
                webhook_handler, "publish_canonical_event", side_effect=RuntimeError("boom")
            ),
        ):
            response = webhook_handler.lambda_handler(event, None)
        assert response["statusCode"] == 200


def _twilio_event(token: str = "tw-token", url: str = "https://api.example.com/webhooks/v2/twilio"):
    from urllib.parse import parse_qsl, urlencode

    from twilio.request_validator import RequestValidator

    body = urlencode(
        {
            "From": "whatsapp:+15551234567",
            "To": "whatsapp:+14155238886",
            "Body": "approve",
            "MessageSid": "SM123",
            "NumMedia": "0",
        }
    )
    params = dict(parse_qsl(body, keep_blank_values=True))
    return {
        "pathParameters": {"provider_slug": "twilio"},
        "headers": {
            "X-Twilio-Signature": RequestValidator(token).compute_signature(url, params),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        "body": body,
        "isBase64Encoded": False,
    }


class TestTwilioPath:
    _ENV = {
        "TWILIO_AUTH_TOKEN": "tw-token",
        "TWILIO_WEBHOOK_URL": "https://api.example.com/webhooks/v2/twilio",
        "TWILIO_NUMBER_TENANT_MAP": json.dumps(
            {"+14155238886": {"tenant_id": "tenant-1", "tenant_name": "acme"}}
        ),
    }

    def test_success_returns_204_with_empty_body(self):
        """Twilio parses the response as TwiML — a JSON body logs error 12300 on
        every single inbound message and drowns out real webhook failures."""
        captured = []
        with (
            patch.dict("os.environ", self._ENV),
            patch.object(
                webhook_handler,
                "publish_canonical_event",
                side_effect=lambda e, **kw: captured.append(e),
            ),
        ):
            response = webhook_handler.lambda_handler(_twilio_event(), None)

        assert response["statusCode"] == 204
        assert response["body"] == ""
        assert "Content-Type" not in response.get("headers", {})
        assert captured, "event should still publish to SQS"
