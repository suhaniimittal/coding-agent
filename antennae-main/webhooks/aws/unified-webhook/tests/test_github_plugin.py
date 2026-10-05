"""Unit tests for providers/github.py."""

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

_unified_dir = Path(__file__).resolve().parent.parent
if str(_unified_dir) not in sys.path:
    sys.path.insert(0, str(_unified_dir))

from providers.base import TenantResolutionError
from providers.github import GitHubPlugin


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_valid_signature_passes(self):
        plugin = GitHubPlugin()
        body = b'{"action": "opened"}'
        sig = _sign("s3cr3t", body)
        assert (
            plugin.verify_signature(
                {"x-hub-signature-256": sig}, body, {"webhook_secret": "s3cr3t"}
            )
            is True
        )

    def test_wrong_secret_fails(self):
        plugin = GitHubPlugin()
        body = b"{}"
        sig = _sign("actual", body)
        assert (
            plugin.verify_signature({"x-hub-signature-256": sig}, body, {"webhook_secret": "wrong"})
            is False
        )

    def test_missing_secret_fails(self):
        plugin = GitHubPlugin()
        assert plugin.verify_signature({}, b"{}", {}) is False

    def test_missing_prefix_fails(self):
        plugin = GitHubPlugin()
        headers = {"x-hub-signature-256": "not-prefixed"}
        assert plugin.verify_signature(headers, b"{}", {"webhook_secret": "s"}) is False


class TestHandleChallenge:
    def test_always_none(self):
        assert GitHubPlugin().handle_challenge({}, {}) is None


class TestIdentifyTenant:
    def test_resolves_via_header(self):
        plugin = GitHubPlugin()
        assert plugin.identify_tenant({"x-tenant-id": "tenant-1"}, {}, {}, {}) == "tenant-1"

    def test_missing_header_raises(self):
        plugin = GitHubPlugin()
        with pytest.raises(TenantResolutionError, match="Missing X-Tenant-ID"):
            plugin.identify_tenant({}, {}, {}, {})


class TestToCanonical:
    def test_pull_request_opened(self):
        plugin = GitHubPlugin()
        headers = {"x-github-event": "pull_request", "x-github-delivery": "d1"}
        body = {
            "action": "opened",
            "pull_request": {"number": 42, "merged": False},
            "repository": {"full_name": "acme/repo"},
            "sender": {"id": 7, "login": "octocat", "type": "User"},
        }
        event = plugin.to_canonical(headers, body, "tenant-1", {})
        assert event.event_type == "pull_request.opened"
        assert event.source_workspace_id == "acme/repo"
        assert event.source_resource_id == "42"
        assert event.provider_event_id == "d1"
        assert event.actor.display_name == "octocat"
        assert event.actor.actor_type == "user"

    def test_pull_request_merged_requires_merged_flag(self):
        plugin = GitHubPlugin()
        headers = {"x-github-event": "pull_request"}
        body = {
            "action": "closed",
            "pull_request": {"number": 1, "merged": True},
            "repository": {},
            "sender": {},
        }
        event = plugin.to_canonical(headers, body, "tenant-1", {})
        assert event.event_type == "pull_request.merged"

    def test_pull_request_closed_without_merge_falls_back(self):
        plugin = GitHubPlugin()
        headers = {"x-github-event": "pull_request"}
        body = {
            "action": "closed",
            "pull_request": {"number": 1, "merged": False},
            "repository": {},
            "sender": {},
        }
        event = plugin.to_canonical(headers, body, "tenant-1", {})
        assert event.event_type == "pull_request.closed"

    def test_bot_sender_detected(self):
        plugin = GitHubPlugin()
        headers = {"x-github-event": "push"}
        body = {"repository": {}, "sender": {"id": 1, "login": "bot", "type": "Bot"}}
        event = plugin.to_canonical(headers, body, "tenant-1", {})
        assert event.actor.actor_type == "bot"

    def test_unmapped_event_falls_back_to_action_pattern(self):
        plugin = GitHubPlugin()
        headers = {"x-github-event": "star"}
        body = {"action": "created", "repository": {}, "sender": {}}
        event = plugin.to_canonical(headers, body, "tenant-1", {})
        assert event.event_type == "star.created"

    def test_structured_payload_preserves_full_body(self):
        """No data loss: every field GitHub sent must be reachable, not just the 3 curated ones."""
        plugin = GitHubPlugin()
        headers = {"x-github-event": "pull_request"}
        body = {
            "action": "opened",
            "pull_request": {
                "number": 1,
                "merged": False,
                "draft": True,
                "labels": [{"name": "bug"}],
            },
            "repository": {"full_name": "acme/repo"},
            "sender": {"login": "octocat"},
        }
        event = plugin.to_canonical(headers, body, "tenant-1", {})
        assert event.structured_payload["pull_request"]["draft"] is True
        assert event.structured_payload["pull_request"]["labels"] == [{"name": "bug"}]
        # sanity: still JSON-serialisable end to end
        json.dumps(event.to_dict())
