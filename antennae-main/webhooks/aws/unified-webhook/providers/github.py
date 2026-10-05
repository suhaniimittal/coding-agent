"""
GitHub provider plugin. Tenant resolution: GitHub has no concept of "workspace ->
tenant" the way Slack does, so — same as Jira per the RFC's provider-quirks table —
tenant is carried explicitly via an X-Tenant-ID header set on the webhook URL/secret
configured per tenant in GitHub's repo/org webhook settings.
"""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime

from canonical_event import CanonicalEvent, EventActor
from providers.base import ProviderPlugin, TenantResolutionError

_EVENT_TYPE_MAP = {
    ("pull_request", "opened"): "pull_request.opened",
    # No ("pull_request", "closed") entry here on purpose: "closed" alone is
    # ambiguous (merged vs abandoned/rejected) — to_canonical's explicit
    # merged-flag check handles "pull_request.merged", and a plain "closed"
    # PR correctly falls through to the f"{event}.{action}" default below
    # ("pull_request.closed"), instead of this map silently mislabeling every
    # closed-without-merge PR as "merged".
    ("push", None): "push.received",
    ("issues", "opened"): "issue.opened",
    ("issues", "closed"): "issue.closed",
}


class GitHubPlugin(ProviderPlugin):
    def verify_signature(self, headers: dict[str, str], body: bytes, config: dict) -> bool:
        secret = config.get("webhook_secret")
        if not secret:
            return False

        signature_header = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
        if not signature_header or not signature_header.startswith("sha256="):
            return False

        computed = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, signature_header)

    def handle_challenge(self, headers: dict[str, str], body: dict) -> dict | None:
        return None  # GitHub has no challenge/handshake step

    def identify_tenant(
        self, headers: dict[str, str], body: dict, query_params: dict, config: dict
    ) -> str:
        tenant_id = headers.get("x-tenant-id") or headers.get("X-Tenant-ID")
        if not tenant_id:
            raise TenantResolutionError("Missing X-Tenant-ID header")
        return tenant_id

    def to_canonical(
        self, headers: dict[str, str], body: dict, tenant_id: str, config: dict
    ) -> CanonicalEvent:
        github_event = headers.get("x-github-event") or headers.get("X-GitHub-Event") or ""
        delivery_id = headers.get("x-github-delivery") or headers.get("X-GitHub-Delivery")
        action = body.get("action")

        if (
            github_event == "pull_request"
            and action == "closed"
            and body.get("pull_request", {}).get("merged")
        ):
            event_type = "pull_request.merged"
        else:
            event_type = (
                _EVENT_TYPE_MAP.get((github_event, action))
                or _EVENT_TYPE_MAP.get((github_event, None))
                or f"{github_event}.{action or 'received'}"
            )

        repo = body.get("repository", {})
        sender = body.get("sender", {})

        return CanonicalEvent(
            event_id=str(uuid.uuid4()),
            event_source="github",
            event_type=event_type,
            tenant_id=tenant_id,
            received_at=datetime.now(tz=UTC).isoformat(),
            source_workspace_id=repo.get("full_name"),
            source_resource_id=str(
                body.get("pull_request", {}).get("number")
                or body.get("issue", {}).get("number")
                or ""
            )
            or None,
            provider_event_id=delivery_id,
            actor=EventActor(
                id=str(sender.get("id")) if sender.get("id") else None,
                display_name=sender.get("login"),
                actor_type="bot" if sender.get("type") == "Bot" else "user",
            ),
            structured_payload={
                # Full raw body first so every field GitHub sent (pull_request,
                # issue, labels, requested_reviewers, ...) is reachable by
                # filter_conditions, not just the three fields below.
                **body,
                "action": action,
                "repository": repo.get("full_name"),
                "sender": sender.get("login"),
            },
            raw_payload=body,
        )
