"""ProviderPlugin ABC — every provider (Slack, GitHub, Jira, ...) implements this."""

import json
from abc import ABC, abstractmethod

from canonical_event import CanonicalEvent


class TenantResolutionError(Exception):
    """Raised when identify_tenant() cannot resolve a tenant — handler returns 400."""


class ProviderPlugin(ABC):
    @abstractmethod
    def verify_signature(self, headers: dict[str, str], body: bytes, config: dict) -> bool:
        """Return False to reject the request with 401."""

    def parse_body(self, headers: dict[str, str], body: bytes) -> dict:
        """Parse the raw request body into the dict handle_challenge/identify_tenant/
        to_canonical/direct_forward_target all operate on.
        Default: plain JSON — every provider's normal payload shape. Override only
        when a provider sends something else for some requests (e.g. Slack's
        Interactivity API, which is form-encoded with the real JSON packed into a
        single field)."""
        return json.loads(body) if body else {}

    @abstractmethod
    def handle_challenge(self, headers: dict[str, str], body: dict) -> dict | None:
        """Return a response dict to short-circuit (e.g. Slack url_verification).
        Return None to proceed with normal processing."""

    @abstractmethod
    def identify_tenant(
        self, headers: dict[str, str], body: dict, query_params: dict, config: dict
    ) -> str:
        """Resolve tenant_id from the incoming request.
        `config` carries provider-level settings the handler loaded from env
        (e.g. Slack's team_id -> tenant routing dict).
        Raise TenantResolutionError if tenant cannot be resolved — handler returns 400."""

    def direct_forward_target(
        self, headers: dict[str, str], body: dict, tenant_id: str, tenant_name: str | None
    ) -> str | None:
        """Return a URL to synchronously forward the raw request to instead of the
        normal to_canonical() -> SQS publish path, or None to take that normal path.
        Default: None for every provider. Override only for a payload shape with a
        hard synchronous-response requirement the async queue can't meet — e.g.
        Slack's Interactivity API, whose trigger_id (needed to open a modal) Slack
        invalidates a few seconds after issuing it, well inside SQS enqueue-then-poll
        latency."""
        return None

    @abstractmethod
    def to_canonical(
        self, headers: dict[str, str], body: dict, tenant_id: str, config: dict
    ) -> CanonicalEvent:
        """Map the provider-specific payload (+ any headers it needs, e.g. GitHub's
        X-GitHub-Event/X-GitHub-Delivery) to a CanonicalEvent."""
