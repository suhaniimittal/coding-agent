"""Unit tests for providers/slack.py."""

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest

_unified_dir = Path(__file__).resolve().parent.parent
if str(_unified_dir) not in sys.path:
    sys.path.insert(0, str(_unified_dir))

from providers.base import TenantResolutionError
from providers.slack import SlackPlugin


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    return "v0=" + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_valid_signature_passes(self):
        plugin = SlackPlugin()
        secret = "s3cr3t"
        body = b'{"type":"event_callback"}'
        ts = str(int(time.time()))
        sig = _sign(secret, ts, body)
        headers = {"x-slack-signature": sig, "x-slack-request-timestamp": ts}
        assert plugin.verify_signature(headers, body, {"signing_secret": secret}) is True

    def test_wrong_secret_fails(self):
        plugin = SlackPlugin()
        body = b"{}"
        ts = str(int(time.time()))
        sig = _sign("actual-secret", ts, body)
        headers = {"x-slack-signature": sig, "x-slack-request-timestamp": ts}
        assert plugin.verify_signature(headers, body, {"signing_secret": "wrong-secret"}) is False

    def test_missing_secret_fails(self):
        plugin = SlackPlugin()
        assert plugin.verify_signature({}, b"{}", {}) is False

    def test_missing_headers_fails(self):
        plugin = SlackPlugin()
        assert plugin.verify_signature({}, b"{}", {"signing_secret": "s"}) is False

    def test_expired_timestamp_fails(self):
        plugin = SlackPlugin()
        secret = "s3cr3t"
        body = b"{}"
        old_ts = str(int(time.time()) - 400)  # >5 min old
        sig = _sign(secret, old_ts, body)
        headers = {"x-slack-signature": sig, "x-slack-request-timestamp": old_ts}
        assert plugin.verify_signature(headers, body, {"signing_secret": secret}) is False

    def test_non_numeric_timestamp_fails(self):
        plugin = SlackPlugin()
        headers = {"x-slack-signature": "v0=abc", "x-slack-request-timestamp": "not-a-number"}
        assert plugin.verify_signature(headers, b"{}", {"signing_secret": "s"}) is False


class TestParseBody:
    def test_plain_json_events_payload_unaffected(self):
        plugin = SlackPlugin()
        body = b'{"type": "event_callback", "team_id": "T1"}'
        assert plugin.parse_body({}, body) == {"type": "event_callback", "team_id": "T1"}

    def test_form_encoded_interactivity_payload_unpacks_payload_field(self):
        plugin = SlackPlugin()
        inner = {"type": "block_actions", "team": {"id": "T1"}, "trigger_id": "trg1"}
        body = urlencode({"payload": json.dumps(inner)}).encode()
        headers = {"content-type": "application/x-www-form-urlencoded"}
        assert plugin.parse_body(headers, body) == inner

    def test_form_encoded_missing_payload_field_returns_empty_dict(self):
        plugin = SlackPlugin()
        body = urlencode({"something_else": "x"}).encode()
        headers = {"content-type": "application/x-www-form-urlencoded"}
        assert plugin.parse_body(headers, body) == {}

    def test_form_encoded_invalid_json_in_payload_returns_empty_dict(self):
        plugin = SlackPlugin()
        body = urlencode({"payload": "not-json"}).encode()
        headers = {"content-type": "application/x-www-form-urlencoded"}
        assert plugin.parse_body(headers, body) == {}


class TestDirectForwardTarget:
    def test_non_interaction_event_returns_none(self):
        plugin = SlackPlugin()
        body = {"type": "event_callback"}
        assert plugin.direct_forward_target({}, body, "tenant-1", "acme") is None

    def test_missing_tenant_name_returns_none_when_deployed(self):
        plugin = SlackPlugin()
        body = {"type": "block_actions"}
        with patch.dict("os.environ", {"ENVIRONMENT": "dev"}, clear=True):
            assert plugin.direct_forward_target({}, body, "tenant-1", None) is None

    def test_explicit_url_override_wins(self):
        plugin = SlackPlugin()
        body = {"type": "block_actions"}
        with patch.dict(
            "os.environ",
            {"ENVIRONMENT": "dev", "PULSAR_INTERACTIONS_URL": "https://ngrok.test/hook"},
            clear=True,
        ):
            target = plugin.direct_forward_target({}, body, "tenant-1", "acme")
        assert target == "https://ngrok.test/hook"

    def test_local_does_not_require_tenant_name(self):
        """Locally there's one Pulsar, so a ROUTING_DICT entry with no
        tenant_name must still forward rather than silently falling through."""
        plugin = SlackPlugin()
        body = {"type": "block_actions"}
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}, clear=True):
            target = plugin.direct_forward_target({}, body, "tenant-1", None)
        assert target == "http://localhost:8090/api/v1/pulsar/api/slack/interactions"

    def test_local_honors_pulsar_host_and_port(self):
        plugin = SlackPlugin()
        body = {"type": "block_actions"}
        with patch.dict(
            "os.environ",
            {"ENVIRONMENT": "local", "PULSAR_HOST": "127.0.0.1", "PULSAR_PORT": "8090"},
            clear=True,
        ):
            target = plugin.direct_forward_target({}, body, "tenant-1", None)
        assert target == "http://127.0.0.1:8090/api/v1/pulsar/api/slack/interactions"

    def test_block_actions_builds_deployed_tenant_url(self):
        plugin = SlackPlugin()
        body = {"type": "block_actions"}
        with patch.dict("os.environ", {"ENVIRONMENT": "dev"}, clear=True):
            target = plugin.direct_forward_target({}, body, "tenant-1", "acme")
        assert target == "https://acme.dev.aetherion.io/api/v1/pulsar/api/slack/interactions"

    def test_view_submission_builds_deployed_tenant_url(self):
        plugin = SlackPlugin()
        body = {"type": "view_submission"}
        with patch.dict("os.environ", {"ENVIRONMENT": "prod"}, clear=True):
            target = plugin.direct_forward_target({}, body, "tenant-1", "acme")
        assert target == "https://acme.prod.aetherion.io/api/v1/pulsar/api/slack/interactions"

    def test_local_environment_targets_localhost(self):
        plugin = SlackPlugin()
        body = {"type": "block_actions"}
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}, clear=True):
            target = plugin.direct_forward_target({}, body, "tenant-1", "acme")
        assert target == "http://localhost:8090/api/v1/pulsar/api/slack/interactions"


class TestHandleChallenge:
    def test_url_verification_returns_challenge(self):
        plugin = SlackPlugin()
        result = plugin.handle_challenge({}, {"type": "url_verification", "challenge": "abc123"})
        assert result == {"challenge": "abc123"}

    def test_other_event_types_return_none(self):
        plugin = SlackPlugin()
        assert plugin.handle_challenge({}, {"type": "event_callback"}) is None


class TestIdentifyTenant:
    def test_resolves_via_routing_dict(self):
        plugin = SlackPlugin()
        config = {"routing_dict": {"T123": {"tenant_id": "tenant-abc"}}}
        tenant_id = plugin.identify_tenant({}, {"team_id": "T123"}, {}, config)
        assert tenant_id == "tenant-abc"

    def test_resolves_via_nested_team_object(self):
        plugin = SlackPlugin()
        config = {"routing_dict": {"T123": {"tenant_id": "tenant-abc"}}}
        tenant_id = plugin.identify_tenant({}, {"team": {"id": "T123"}}, {}, config)
        assert tenant_id == "tenant-abc"

    def test_missing_team_id_raises(self):
        plugin = SlackPlugin()
        with pytest.raises(TenantResolutionError, match="Missing team_id"):
            plugin.identify_tenant({}, {}, {}, {"routing_dict": {}})

    def test_unregistered_workspace_raises(self):
        plugin = SlackPlugin()
        with pytest.raises(TenantResolutionError, match="Unknown Slack workspace"):
            plugin.identify_tenant({}, {"team_id": "T999"}, {}, {"routing_dict": {}})


class TestToCanonical:
    def test_app_mention_maps_correctly(self):
        plugin = SlackPlugin()
        body = {
            "type": "event_callback",
            "team_id": "T1",
            "event_id": "Ev1",
            "event": {
                "type": "app_mention",
                "channel": "C1",
                "user": "U1",
                "text": "hi",
                "ts": "1.1",
            },
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "app_mention.received"
        assert event.event_source == "slack"
        assert event.source_resource_id == "C1"
        assert event.structured_payload["channel_id"] == "C1"
        assert event.structured_payload["text"] == "hi"
        assert event.raw_payload == body

    def test_reaction_added_derives_channel_from_item(self):
        plugin = SlackPlugin()
        body = {
            "type": "event_callback",
            "team_id": "T1",
            "event": {
                "type": "reaction_added",
                "user": "U1",
                "reaction": "thumbsup",
                "item": {"channel": "C1"},
            },
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "reaction.added"
        assert event.structured_payload["channel_id"] == "C1"
        assert event.structured_payload["reaction"] == "thumbsup"

    def test_dict_channel_without_an_id_falls_back_to_channel_id(self):
        """A channel object with no "id" must not resolve to None while Slack sent
        channel_id — the derivation's own comment promises it never does."""
        body = {
            "type": "event_callback",
            "team_id": "T1",
            "event": {"type": "message", "channel": {"name": "general"}, "channel_id": "C1"},
        }
        event = SlackPlugin().to_canonical({}, body, "t1", {})
        assert event.structured_payload["channel_id"] == "C1"

    def test_verification_token_never_reaches_filters_or_agent_params(self):
        """It is persisted per event in event_store and interpolatable into agent params."""
        body = {
            "token": "xoxb-verification-token",
            "type": "event_callback",
            "team_id": "T1",
            "event": {"type": "app_mention", "channel": "C1", "text": "hi"},
        }
        event = SlackPlugin().to_canonical({}, body, "t1", {})
        assert "token" not in event.structured_payload
        assert "token" not in event.raw_payload

    def test_file_shared_keeps_slacks_own_channel_id(self):
        """No "channel" and no "item" — deriving None here used to blank the real id."""
        plugin = SlackPlugin()
        body = {
            "type": "event_callback",
            "team_id": "T1",
            "event": {
                "type": "file_shared",
                "channel_id": "C1",
                "file_id": "F1",
                "user_id": "U1",
            },
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "file.shared"
        assert event.structured_payload["channel_id"] == "C1"
        assert event.source_resource_id == "C1"

    def test_channel_created_normalises_nested_channel_object(self):
        plugin = SlackPlugin()
        body = {
            "type": "event_callback",
            "team_id": "T1",
            "event": {"type": "channel_created", "channel": {"id": "C99", "name": "general"}},
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "channel.created"
        assert event.source_resource_id == "C99"
        assert event.structured_payload["channel_id"] == "C99"
        assert event.structured_payload["channel"] == {"id": "C99", "name": "general"}

    def test_unmapped_event_type_falls_back_gracefully(self):
        plugin = SlackPlugin()
        body = {"type": "event_callback", "team_id": "T1", "event": {"type": "some_future_event"}}
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "some_future_event.received"

    def test_bot_actor_detected(self):
        plugin = SlackPlugin()
        body = {
            "type": "event_callback",
            "team_id": "T1",
            "event": {"type": "message", "bot_id": "B1", "user": "U1"},
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.actor.actor_type == "bot"

    def test_structured_payload_preserves_full_event_and_envelope(self):
        """No data loss: outer envelope + inner event fields must both be reachable."""
        plugin = SlackPlugin()
        body = {
            "type": "event_callback",
            "team_id": "T1",
            "api_app_id": "A123",
            "event": {"type": "message", "text": "hi", "custom_field": "value"},
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.structured_payload["api_app_id"] == "A123"
        assert event.structured_payload["custom_field"] == "value"
