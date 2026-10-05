"""
CanonicalEvent — the normalised shape every ProviderPlugin maps its provider's
webhook payload into before it's published to SQS. Mirrors the schema in
milkyway/generic-event-driven-architecture.md.
"""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EventActor:
    id: str | None = None
    email: str | None = None
    display_name: str | None = None
    actor_type: str = "user"  # "user" | "bot" | "system"


@dataclass
class CanonicalEvent:
    event_id: str  # uuid4 — idempotency key
    event_source: str  # provider slug: "slack" | "jira" | "github" | "teams" | "outlook"
    event_type: str  # "issue.created" | "push.received" | "app_mention.received" ...
    tenant_id: str
    received_at: str  # ISO8601 UTC
    schema_version: str = "1.0"

    source_workspace_id: str | None = (
        None  # Jira: project_key, GitHub: repo full_name, Slack: team_id
    )
    source_resource_id: str | None = None  # Jira: issue_key, Slack: channel_id
    provider_event_id: str | None = None  # provider's own dedup ID

    actor: EventActor = field(default_factory=EventActor)

    structured_payload: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
