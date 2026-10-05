"""
Slack provider plugin — ported from webhooks/aws/slack-webhook/webhook_handler.py.

Slack is a single, unified app shared across tenants (no per-tenant secret in the
URL) — tenant resolution is body["team_id"] -> config["routing_dict"] lookup,
mirroring the legacy Lambda's ROUTING_DICT env var.
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs

from canonical_event import CanonicalEvent, EventActor
from providers.base import ProviderPlugin, TenantResolutionError

# Slack Interactivity payload types (button clicks, modal submits, shortcuts) —
# these arrive form-encoded, not as raw JSON, and need a synchronous response
# fast enough to open a modal with the request's trigger_id (see
# direct_forward_target below). Never appear in the Events API's "type" field.
_INTERACTION_TYPES = {
    "block_actions",
    "view_submission",
    "view_closed",
    "shortcut",
    "message_action",
}

# Pulsar mounts its FastAPI app under root_path="/api/v1/pulsar" (see pulsar's
# auth/policies.BASE_API_PATH); the interactions route itself is "/api/slack/interactions".
_PULSAR_INTERACTIONS_PATH = "/api/v1/pulsar/api/slack/interactions"

# Slack Events API "type" field -> our canonical event_type. Keep in sync with
# flyway's provider_events seed for slug='slack' (see V119__Agent_event_subscriptions.sql) — a
# provider_event row with no matching entry here just never gets used, and an
# entry here with no provider_events row just never becomes subscribable.
# Anything not listed falls back to "{slack_event_type}.received" so a brand
# new Slack event type still gets a usable (if unpolished) canonical name
# without a code change — only add an explicit mapping when you want a nicer
# name and a catalog row for it.
_SLACK_EVENT_TYPE_MAP = {
    "app_mention": "app_mention.received",
    "message": "message.created",
    "reaction_added": "reaction.added",
    "reaction_removed": "reaction.removed",
    "member_joined_channel": "member_joined_channel.received",
    "member_left_channel": "member_left_channel.received",
    "channel_created": "channel.created",
    "channel_rename": "channel.renamed",
    "channel_archive": "channel.archived",
    "channel_unarchive": "channel.unarchived",
    "file_shared": "file.shared",
    "app_home_opened": "app_home.opened",
    "pin_added": "pin.added",
    "pin_removed": "pin.removed",
    "team_join": "team.joined",
}


class SlackPlugin(ProviderPlugin):
    def verify_signature(self, headers: dict[str, str], body: bytes, config: dict) -> bool:
        signing_secret = config.get("signing_secret")
        if not signing_secret:
            return False

        signature = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")
        timestamp = headers.get("x-slack-request-timestamp") or headers.get(
            "X-Slack-Request-Timestamp"
        )
        if not signature or not timestamp:
            return False

        try:
            if abs(int(time.time()) - int(timestamp)) > 300:
                return False
        except ValueError:
            return False

        sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
        computed = (
            "v0="
            + hmac.new(signing_secret.encode(), sig_basestring.encode(), hashlib.sha256).hexdigest()
        )
        return hmac.compare_digest(computed, signature)

    def parse_body(self, headers: dict[str, str], body: bytes) -> dict:
        content_type = (headers.get("content-type") or headers.get("Content-Type") or "").lower()
        if "application/x-www-form-urlencoded" not in content_type:
            return super().parse_body(headers, body)

        # Interactivity payloads (block_actions, view_submission, shortcuts) are
        # form-encoded with the real JSON packed into one "payload" field — unlike
        # every other Slack payload (Events, url_verification), which is raw JSON.
        form = parse_qs(body.decode("utf-8"))
        payload_values = form.get("payload") or []
        if not payload_values:
            return {}
        try:
            return json.loads(payload_values[0])
        except json.JSONDecodeError:
            return {}

    def handle_challenge(self, headers: dict[str, str], body: dict) -> dict | None:
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge")}
        return None

    def identify_tenant(
        self, headers: dict[str, str], body: dict, query_params: dict, config: dict
    ) -> str:
        team_id = body.get("team_id") or (
            body.get("team", {}).get("id") if isinstance(body.get("team"), dict) else None
        )
        if not team_id:
            raise TenantResolutionError("Missing team_id in Slack event")

        routing = config.get("routing_dict", {}).get(team_id)
        if not routing:
            raise TenantResolutionError(f"Unknown Slack workspace team_id={team_id}")
        return routing["tenant_id"]

    def direct_forward_target(
        self, headers: dict[str, str], body: dict, tenant_id: str, tenant_name: str | None
    ) -> str | None:
        if body.get("type") not in _INTERACTION_TYPES:
            return None

        # Full-URL escape hatch, for any setup the two branches below don't fit
        # (ngrok'd Pulsar, a port-forward, a one-off tenant host).
        explicit = os.environ.get("PULSAR_INTERACTIONS_URL")
        if explicit:
            return explicit

        if os.environ.get("ENVIRONMENT", "").lower() == "local":
            host = os.environ.get("PULSAR_HOST", "localhost")
            port = os.environ.get("PULSAR_PORT", "8090")
            return f"http://{host}:{port}{_PULSAR_INTERACTIONS_PATH}"

        if not tenant_name:
            return None
        environment = os.environ.get("ENVIRONMENT", "dev")
        return f"https://{tenant_name}.{environment}.aetherion.io{_PULSAR_INTERACTIONS_PATH}"

    def to_canonical(
        self, headers: dict[str, str], body: dict, tenant_id: str, config: dict
    ) -> CanonicalEvent:
        event = body.get("event", {}) if body.get("type") == "event_callback" else {}
        slack_event_type = event.get("type")
        team_id = body.get("team_id")

        event_type = _SLACK_EVENT_TYPE_MAP.get(
            slack_event_type,
            f"{slack_event_type}.received" if slack_event_type else "unknown.received",
        )

        # "channel" is a bare channel-ID string on most events (message, app_mention)
        # but a nested {id, name, ...} object on channel_created/rename/archive, and
        # entirely absent on reaction_added/pin_added/pin_removed (channel lives under
        # "item.channel") and on file_shared/pin_added (its own "channel_id") —
        # normalise so structured_payload.channel_id is always a plain string when the
        # event carries one at all, and never overwrite Slack's own with None.
        item = event.get("item")
        channel_field = event.get("channel")
        if isinstance(channel_field, dict):
            channel_id = channel_field.get("id")
        elif channel_field:
            channel_id = channel_field
        else:
            channel_id = None
        # One fallback chain for every shape, so a channel object without an "id"
        # still consults the event's own key instead of resolving to None.
        if not channel_id:
            channel_id = (item or {}).get("channel") if isinstance(item, dict) else None
        if not channel_id:
            channel_id = event.get("channel_id")

        return CanonicalEvent(
            event_id=str(uuid.uuid4()),
            event_source="slack",
            event_type=event_type,
            tenant_id=tenant_id,
            received_at=datetime.now(tz=UTC).isoformat(),
            source_workspace_id=team_id,
            source_resource_id=channel_id,
            provider_event_id=body.get("event_id"),
            actor=EventActor(
                id=event.get("user"), actor_type="bot" if event.get("bot_id") else "user"
            ),
            structured_payload={
                # Outer envelope first (api_app_id, event_id, event_time, ...),
                # then the inner "event" object on top (more specific — reaction,
                # item, file_id, inviter, ... — wins on any key clash), then
                # normalised/derived fields last so those win over both. Nothing
                # Slack sent is unreachable by filter_conditions.
                **{k: v for k, v in body.items() if k != "token"},
                **event,
                "channel_id": channel_id,
                "team_id": team_id,
                "slack_event_type": slack_event_type,
            },
            raw_payload={k: v for k, v in body.items() if k != "token"},
        )
