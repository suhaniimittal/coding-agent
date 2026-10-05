"""
Tests for the Twilio provider plugin (inbound SMS / WhatsApp / RCS).

Twilio differs from the other providers in two ways that are easy to get wrong:
it posts form-encoded rather than JSON, and its signature covers the exact public
URL — so both are asserted against the vendor's own validator.
"""

import pytest
from twilio.request_validator import RequestValidator

from providers.base import TenantResolutionError
from providers.twilio import TwilioPlugin

_TOKEN = "test-auth-token"
_URL = "https://api.example.com/webhooks/v2/twilio"
_TENANT_MAP = {"+15559876543": {"tenant_id": "tenant-1", "tenant_name": "acme"}}


@pytest.fixture
def plugin():
    return TwilioPlugin()


@pytest.fixture
def config():
    return {"auth_token": _TOKEN, "webhook_url": _URL, "number_tenant_map": _TENANT_MAP}


def _form(**fields) -> bytes:
    from urllib.parse import urlencode

    base = {
        "From": "+15551234567",
        "To": "+15559876543",
        "Body": "what is the status?",
        "MessageSid": "SM123",
        "NumMedia": "0",
    }
    base.update(fields)
    return urlencode({k: v for k, v in base.items() if v is not None}).encode()


def _sign(body: bytes, url: str = _URL) -> str:
    from urllib.parse import parse_qsl

    params = dict(parse_qsl(body.decode(), keep_blank_values=True))
    return RequestValidator(_TOKEN).compute_signature(url, params)


class TestParseBody:
    def test_form_encoded_body_is_decoded(self, plugin):
        parsed = plugin.parse_body({}, _form())
        assert parsed["From"] == "+15551234567"
        assert parsed["Body"] == "what is the status?"

    def test_blank_optional_fields_are_kept(self, plugin):
        # Dropping them would hide them from subscription filter_conditions.
        assert plugin.parse_body({}, _form(Body=""))["Body"] == ""

    def test_empty_body(self, plugin):
        assert plugin.parse_body({}, b"") == {}


class TestVerifySignature:
    def test_valid_signature_passes(self, plugin, config):
        body = _form()
        assert plugin.verify_signature({"x-twilio-signature": _sign(body)}, body, config)

    def test_tampered_body_fails(self, plugin, config):
        signature = _sign(_form())
        assert not plugin.verify_signature(
            {"x-twilio-signature": signature}, _form(Body="transfer all the money"), config
        )

    def test_signature_for_a_different_url_fails(self, plugin, config):
        body = _form()
        rogue = _sign(body, url="https://evil.example.com/webhooks/v2/twilio")
        assert not plugin.verify_signature({"x-twilio-signature": rogue}, body, config)

    def test_missing_signature_header_fails(self, plugin, config):
        assert not plugin.verify_signature({}, _form(), config)

    @pytest.mark.parametrize("missing", ["auth_token", "webhook_url"])
    def test_fails_closed_when_not_configured(self, plugin, config, missing):
        # An unverifiable request must never be treated as genuine.
        body = _form()
        config[missing] = ""
        assert not plugin.verify_signature({"x-twilio-signature": _sign(body)}, body, config)


class TestIdentifyTenant:
    def test_to_number_selects_the_tenant(self, plugin, config):
        parsed = plugin.parse_body({}, _form())
        assert plugin.identify_tenant({}, parsed, {}, config) == "tenant-1"

    def test_whatsapp_prefix_is_stripped_before_lookup(self, plugin, config):
        # One map entry per number must serve SMS, WhatsApp and RCS.
        parsed = plugin.parse_body({}, _form(To="whatsapp:+15559876543"))
        assert plugin.identify_tenant({}, parsed, {}, config) == "tenant-1"

    def test_plain_string_mapping_is_accepted(self, plugin, config):
        config["number_tenant_map"] = {"+15559876543": "tenant-1"}
        parsed = plugin.parse_body({}, _form())
        assert plugin.identify_tenant({}, parsed, {}, config) == "tenant-1"

    def test_unknown_number_is_rejected(self, plugin, config):
        parsed = plugin.parse_body({}, _form(To="+15550000000"))
        with pytest.raises(TenantResolutionError):
            plugin.identify_tenant({}, parsed, {}, config)

    def test_missing_to_is_rejected(self, plugin, config):
        with pytest.raises(TenantResolutionError):
            plugin.identify_tenant({}, {}, {}, config)


class TestToCanonical:
    @pytest.mark.parametrize(
        "from_address,expected_channel",
        [
            ("+15551234567", "sms"),
            ("whatsapp:+15551234567", "whatsapp"),
            ("rcs:+15551234567", "rcs"),
        ],
    )
    def test_channel_comes_from_the_address_prefix(
        self, plugin, config, from_address, expected_channel
    ):
        # Separate event types let a tenant subscribe an agent to SMS only.
        parsed = plugin.parse_body({}, _form(From=from_address))
        event = plugin.to_canonical({}, parsed, "tenant-1", config)
        assert event.event_type == f"{expected_channel}.received"
        assert event.structured_payload["channel"] == expected_channel
        assert event.structured_payload["from_number"] == "+15551234567"

    def test_message_sid_is_the_dedup_key(self, plugin, config):
        parsed = plugin.parse_body({}, _form())
        assert plugin.to_canonical({}, parsed, "tenant-1", config).provider_event_id == "SM123"

    def test_media_urls_are_collected(self, plugin, config):
        parsed = plugin.parse_body(
            {}, _form(NumMedia="2", MediaUrl0="https://m/0", MediaUrl1="https://m/1")
        )
        event = plugin.to_canonical({}, parsed, "tenant-1", config)
        assert event.structured_payload["media_urls"] == ["https://m/0", "https://m/1"]

    def test_raw_fields_stay_reachable_for_filter_conditions(self, plugin, config):
        parsed = plugin.parse_body({}, _form(FromCity="LONDON"))
        event = plugin.to_canonical({}, parsed, "tenant-1", config)
        assert event.structured_payload["FromCity"] == "LONDON"
        assert event.structured_payload["text"] == "what is the status?"
