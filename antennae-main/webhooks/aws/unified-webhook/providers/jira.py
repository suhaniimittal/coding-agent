"""
Jira provider plugin.

Auth: native Jira webhooks (Project/System Settings -> WebHooks) cannot sign
requests or add custom headers at all — there's no HMAC scheme to verify here
the way Slack/GitHub have one. The practical way to secure a Jira webhook is
an "Automation for Jira" rule (or Jira Cloud's "Send web request" action) that
lets you attach a custom header, so verify_signature checks a shared-secret
header instead of an HMAC. If you're wiring this up via native System
WebHooks (no custom headers possible), set VERIFY_SIGNATURE=false for this
provider and rely on the URL being unguessable + your reverse proxy/IP
allowlist instead.

Tenant resolution: native Jira webhook config only lets you set a URL (with
query string) — no custom headers — so unlike Slack (team_id -> routing
lookup) or GitHub (X-Tenant-ID header), Jira tenant comes from a query param
baked into the webhook URL at setup time: /webhooks/v2/jira?tenant_id=<uuid>.

Custom payloads: an Automation for Jira rule's "Send web request" action can
send *any* JSON body you define in the rule — not necessarily the native
issue-webhook shape (webhookEvent/issue/fields/...) this plugin otherwise
expects. To support that:
  - structured_payload always spreads the ENTIRE raw body first, so every
    field a custom rule sends is reachable by filter_conditions, before any
    normalised/derived fields are layered on top (see to_canonical).
  - A custom payload can set a top-level "event_type" key to bypass Jira's
    native webhookEvent/issue_event_type_name inference entirely and declare
    its own canonical event_type directly — use this for rules that don't
    represent a native issue event at all (e.g. a custom SLA-breach rule).
raw_payload is always the untouched original body regardless, so nothing is
ever unrecoverable even if you only reach a field through raw_payload.
"""

import hmac
import uuid
from datetime import UTC, datetime

from canonical_event import CanonicalEvent, EventActor
from providers.base import ProviderPlugin, TenantResolutionError

# Jira's top-level "webhookEvent" field -> our canonical event_type, for events
# where that field alone is enough to determine the type.
_JIRA_WEBHOOK_EVENT_MAP = {
    "jira:issue_created": "issue.created",
    "jira:issue_deleted": "issue.deleted",
    "comment_created": "issue.commented",
    "comment_updated": "issue.commented",
    "comment_deleted": "issue.commented",
}

# For "jira:issue_updated" specifically, Jira's "issue_event_type_name" field
# carries the finer-grained sub-type (assignment change, transition, comment,
# generic field edit, ...).
_JIRA_ISSUE_UPDATE_SUBTYPE_MAP = {
    "issue_assigned": "issue.assigned",
    "issue_workflow": "issue.transitioned",
    "issue_generic": "issue.updated",
    "issue_commented": "issue.commented",
    "issue_comment_edited": "issue.commented",
}


class JiraPlugin(ProviderPlugin):
    def verify_signature(self, headers: dict[str, str], body: bytes, config: dict) -> bool:
        secret = config.get("webhook_secret")
        if not secret:
            return False
        provided = headers.get("x-jira-webhook-secret") or headers.get("X-Jira-Webhook-Secret")
        if not provided:
            return False
        return hmac.compare_digest(provided, secret)

    def handle_challenge(self, headers: dict[str, str], body: dict) -> dict | None:
        return None  # Jira has no challenge/handshake step

    def identify_tenant(
        self, headers: dict[str, str], body: dict, query_params: dict, config: dict
    ) -> str:
        tenant_id = query_params.get("tenant_id")
        if not tenant_id:
            raise TenantResolutionError("Missing ?tenant_id= query param on the Jira webhook URL")
        return tenant_id

    def to_canonical(
        self, headers: dict[str, str], body: dict, tenant_id: str, config: dict
    ) -> CanonicalEvent:
        # A custom Automation-for-Jira payload can declare its own canonical
        # event_type directly, bypassing native-webhook inference entirely.
        explicit_event_type = body.get("event_type")
        webhook_event = body.get("webhookEvent", "")
        issue_event_type_name = body.get("issue_event_type_name")

        if explicit_event_type:
            event_type = explicit_event_type
        elif webhook_event == "jira:issue_updated":
            event_type = _JIRA_ISSUE_UPDATE_SUBTYPE_MAP.get(issue_event_type_name, "issue.updated")
        else:
            event_type = _JIRA_WEBHOOK_EVENT_MAP.get(
                webhook_event, f"{webhook_event}.received" if webhook_event else "unknown.received"
            )

        # Native issue-webhook shape — absent entirely for a fully custom
        # Automation payload that isn't about a specific issue at all.
        issue = body.get("issue") or {}
        fields = issue.get("fields") or {}
        project = fields.get("project") or {}
        status = fields.get("status") or {}
        priority = fields.get("priority") or {}
        assignee = fields.get("assignee") or {}
        reporter = fields.get("reporter") or {}
        actor_user = body.get("user") or {}

        return CanonicalEvent(
            event_id=str(uuid.uuid4()),
            event_source="jira",
            event_type=event_type,
            tenant_id=tenant_id,
            received_at=datetime.now(tz=UTC).isoformat(),
            source_workspace_id=project.get("key"),
            source_resource_id=issue.get("key"),
            provider_event_id=None,  # Jira webhooks carry no dedup ID
            actor=EventActor(
                id=actor_user.get("accountId"),
                email=actor_user.get("emailAddress"),
                display_name=actor_user.get("displayName"),
                actor_type="user",
            ),
            structured_payload={
                # Full raw body first — every field a custom Automation rule
                # sends is reachable by filter_conditions, not just what we
                # thought to extract below.
                **body,
                # Normalised convenience fields on top so they win on key
                # clashes (e.g. a custom payload that happens to also use
                # "project" as a key) — empty/absent when there's no native
                # `issue` object to derive them from.
                "issue_key": issue.get("key"),
                "project": project.get("key"),
                "summary": fields.get("summary"),
                "status": status.get("name"),
                "priority": priority.get("name"),
                "assignee": assignee.get("emailAddress") or assignee.get("displayName"),
                "reporter": reporter.get("emailAddress") or reporter.get("displayName"),
                "webhook_event": webhook_event,
                "issue_event_type_name": issue_event_type_name,
            },
            raw_payload=body,
        )
