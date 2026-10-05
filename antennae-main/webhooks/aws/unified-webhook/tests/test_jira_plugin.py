"""Unit tests for providers/jira.py."""

import json
import sys
from pathlib import Path

import pytest

_unified_dir = Path(__file__).resolve().parent.parent
if str(_unified_dir) not in sys.path:
    sys.path.insert(0, str(_unified_dir))

from providers.base import TenantResolutionError
from providers.jira import JiraPlugin


class TestVerifySignature:
    def test_valid_shared_secret_passes(self):
        plugin = JiraPlugin()
        headers = {"x-jira-webhook-secret": "s3cr3t"}
        assert plugin.verify_signature(headers, b"{}", {"webhook_secret": "s3cr3t"}) is True

    def test_wrong_secret_fails(self):
        plugin = JiraPlugin()
        headers = {"x-jira-webhook-secret": "wrong"}
        assert plugin.verify_signature(headers, b"{}", {"webhook_secret": "s3cr3t"}) is False

    def test_missing_configured_secret_fails(self):
        plugin = JiraPlugin()
        assert plugin.verify_signature({"x-jira-webhook-secret": "x"}, b"{}", {}) is False

    def test_missing_header_fails(self):
        plugin = JiraPlugin()
        assert plugin.verify_signature({}, b"{}", {"webhook_secret": "s3cr3t"}) is False


class TestHandleChallenge:
    def test_always_none(self):
        assert JiraPlugin().handle_challenge({}, {}) is None


class TestIdentifyTenant:
    def test_resolves_via_query_param(self):
        plugin = JiraPlugin()
        assert plugin.identify_tenant({}, {}, {"tenant_id": "tenant-1"}, {}) == "tenant-1"

    def test_missing_query_param_raises(self):
        plugin = JiraPlugin()
        with pytest.raises(TenantResolutionError, match="tenant_id"):
            plugin.identify_tenant({}, {}, {}, {})


class TestToCanonical:
    def test_issue_created(self):
        plugin = JiraPlugin()
        body = {
            "webhookEvent": "jira:issue_created",
            "issue": {"key": "PLAT-1", "fields": {"project": {"key": "PLAT"}, "summary": "Test"}},
            "user": {"accountId": "U1", "emailAddress": "a@b.com", "displayName": "A B"},
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "issue.created"
        assert event.event_source == "jira"
        assert event.source_workspace_id == "PLAT"
        assert event.source_resource_id == "PLAT-1"
        assert event.actor.email == "a@b.com"
        assert event.structured_payload["issue_key"] == "PLAT-1"

    def test_issue_updated_transitioned_via_subtype(self):
        plugin = JiraPlugin()
        body = {
            "webhookEvent": "jira:issue_updated",
            "issue_event_type_name": "issue_workflow",
            "issue": {"key": "PLAT-2", "fields": {}},
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "issue.transitioned"

    def test_issue_updated_assigned_via_subtype(self):
        plugin = JiraPlugin()
        body = {
            "webhookEvent": "jira:issue_updated",
            "issue_event_type_name": "issue_assigned",
            "issue": {"key": "PLAT-3", "fields": {"assignee": {"emailAddress": "x@y.com"}}},
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "issue.assigned"
        assert event.structured_payload["assignee"] == "x@y.com"

    def test_issue_updated_unknown_subtype_falls_back_to_generic_updated(self):
        plugin = JiraPlugin()
        body = {
            "webhookEvent": "jira:issue_updated",
            "issue_event_type_name": "some_new_subtype",
            "issue": {"key": "PLAT-4", "fields": {}},
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "issue.updated"

    def test_comment_created(self):
        plugin = JiraPlugin()
        body = {"webhookEvent": "comment_created", "issue": {"key": "PLAT-5", "fields": {}}}
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "issue.commented"

    def test_unmapped_webhook_event_falls_back_gracefully(self):
        plugin = JiraPlugin()
        body = {"webhookEvent": "jira:worklog_updated", "issue": {"key": "PLAT-6", "fields": {}}}
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "jira:worklog_updated.received"

    def test_explicit_event_type_overrides_native_inference(self):
        """Automation-for-Jira custom payloads can declare event_type directly."""
        plugin = JiraPlugin()
        body = {"event_type": "issue.sla_breached", "severity": "P1", "my_custom_key": "abc"}
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.event_type == "issue.sla_breached"
        assert event.structured_payload["severity"] == "P1"
        assert event.structured_payload["my_custom_key"] == "abc"

    def test_custom_payload_without_issue_object_does_not_crash(self):
        plugin = JiraPlugin()
        body = {"event_type": "custom.rule_fired"}
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.source_resource_id is None
        assert event.structured_payload["issue_key"] is None

    def test_structured_payload_preserves_full_body_including_custom_fields(self):
        """No data loss: nested/custom Jira fields must be reachable, not just curated ones."""
        plugin = JiraPlugin()
        body = {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": "PLAT-1",
                "fields": {"project": {"key": "PLAT"}, "customfield_10010": "my-custom-value"},
            },
        }
        event = plugin.to_canonical({}, body, "tenant-1", {})
        assert event.structured_payload["issue"]["fields"]["customfield_10010"] == "my-custom-value"
        json.dumps(event.to_dict())
