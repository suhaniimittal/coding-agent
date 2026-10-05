"""Twilio provider plugin — inbound SMS, WhatsApp and RCS over one webhook.

Unlike the other providers: Twilio posts form-encoded, not JSON (hence parse_body),
and its signature covers the exact public URL, which a Lambda behind API Gateway
cannot reconstruct — so TWILIO_WEBHOOK_URL supplies it rather than guessing, which
is what forces the voice-webhook to brute-force query-parameter orderings.
"""

import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from twilio.request_validator import RequestValidator

from canonical_event import CanonicalEvent, EventActor
from providers.base import ProviderPlugin, TenantResolutionError

# Address prefix -> our channel name. Bare E.164 (no prefix) is SMS/MMS. Only
# channels pulsar can actually reply on: an unknown one would open a thread with
# nowhere to send the answer.
_CHANNEL_PREFIXES = {
    "whatsapp:": "whatsapp",
    "rcs:": "rcs",
}
_DEFAULT_CHANNEL = "sms"


def _channel_of(address: str) -> str:
    for prefix, channel in _CHANNEL_PREFIXES.items():
        if address.startswith(prefix):
            return channel
    return _DEFAULT_CHANNEL


def _strip_channel_prefix(address: str) -> str:
    for prefix in _CHANNEL_PREFIXES:
        if address.startswith(prefix):
            return address[len(prefix) :]
    return address


class TwilioPlugin(ProviderPlugin):
    def parse_body(self, headers: dict[str, str], body: bytes) -> dict:
        # keep_blank_values: Twilio sends empty strings for absent optional fields
        # (e.g. MediaUrl0), and dropping them would hide them from filter_conditions.
        return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True)) if body else {}

    def verify_signature(self, headers: dict[str, str], body: bytes, config: dict) -> bool:
        auth_token = config.get("auth_token")
        webhook_url = config.get("webhook_url")
        if not auth_token or not webhook_url:
            # Fail closed: an unverifiable request must not be treated as genuine.
            return False

        signature = headers.get("x-twilio-signature") or headers.get("X-Twilio-Signature")
        if not signature:
            return False

        form_params = self.parse_body(headers, body)
        validator = RequestValidator(auth_token)
        return validator.validate(webhook_url, form_params, signature)

    def handle_challenge(self, headers: dict[str, str], body: dict) -> dict | None:
        return None

    def identify_tenant(
        self, headers: dict[str, str], body: dict, query_params: dict, config: dict
    ) -> str:
        # `To` is our own Twilio number, so it identifies the tenant the same way
        # Slack's team_id does. Keyed on the bare E.164 so one number serves
        # SMS/WhatsApp/RCS without three map entries.
        to_address = _strip_channel_prefix(body.get("To") or "")
        if not to_address:
            raise TenantResolutionError("Missing To in Twilio webhook")

        routing = config.get("number_tenant_map", {}).get(to_address)
        if not routing:
            raise TenantResolutionError(f"Unknown Twilio number To={to_address}")
        if isinstance(routing, str):
            return routing
        tenant_id = routing.get("tenant_id")
        if not tenant_id:
            raise TenantResolutionError(f"No tenant_id configured for Twilio number {to_address}")
        return tenant_id

    def to_canonical(
        self, headers: dict[str, str], body: dict, tenant_id: str, config: dict
    ) -> CanonicalEvent:
        from_address = body.get("From") or ""
        to_address = body.get("To") or ""
        channel = _channel_of(from_address)
        sender = _strip_channel_prefix(from_address)

        media_count = int(body.get("NumMedia") or 0)
        media_urls = [body[f"MediaUrl{i}"] for i in range(media_count) if body.get(f"MediaUrl{i}")]

        return CanonicalEvent(
            event_id=str(uuid.uuid4()),
            event_source="twilio",
            # Distinct per channel so a tenant can subscribe an agent to SMS without
            # also getting WhatsApp. channel_relay keys off structured_payload.channel.
            event_type=f"{channel}.received",
            tenant_id=tenant_id,
            received_at=datetime.now(tz=UTC).isoformat(),
            source_workspace_id=_strip_channel_prefix(to_address),
            source_resource_id=sender,
            provider_event_id=body.get("MessageSid"),
            actor=EventActor(id=sender, display_name=body.get("ProfileName")),
            structured_payload={
                # Everything Twilio sent stays reachable by filter_conditions, with
                # normalised fields layered on top.
                **body,
                "channel": channel,
                "from_number": sender,
                "to_number": _strip_channel_prefix(to_address),
                "text": body.get("Body") or "",
                "media_urls": media_urls,
                "message_sid": body.get("MessageSid"),
            },
            raw_payload=dict(body),
        )
