"""
Unified Webhook Lambda — single ingestion point for every provider (Slack,
GitHub, and any provider added later) instead of one bespoke Lambda per provider.

Flow (mirrors milkyway/generic-event-driven-architecture.md):
  1. Resolve provider_slug from the URL path -> ProviderPlugin.
  2. Load common (non-tenant) secrets from LAMBDA_WEBHOOK_SIGNING_SECRET_ARN.
  3. ProviderPlugin.verify_signature()
  4. ProviderPlugin.parse_body() -> dict (plain JSON by default).
  5. ProviderPlugin.handle_challenge() -> respond immediately if needed.
  6. ProviderPlugin.identify_tenant()
  7. Load the tenant's secrets from LAMBDA_WEBHOOK_<TENANT_NAME>_SECRETS_ARN
     (the same per-tenant secret gmail/jira/voice/graph-mail-reply webhooks use)
     and merge over the common ones (tenant wins on key clashes).
  8. ProviderPlugin.direct_forward_target() -> if set, synchronously proxy the raw
     request there and stop (see _forward_synchronously docstring for why); else:
  9. ProviderPlugin.to_canonical(tenant_id) -> CanonicalEvent
  10. Publish -> the tenant's own SQS queue (unified-events-{env}-{tenant_id})

Configuration (same config.py strategy as gmail-webhook — env vars when
ENVIRONMENT="local", AWS Secrets Manager with TTL caching otherwise):
  - LAMBDA_WEBHOOK_SIGNING_SECRET_ARN -> secret holding settings shared across tenants
    (ROUTING_DICT, SLACK_SIGNING_SECRET, GITHUB_WEBHOOK_SECRET, ...).
  - LAMBDA_WEBHOOK_<TENANT_NAME>_SECRETS_ARN -> per-tenant overrides/additions,
    resolved after identify_tenant(). Tenant name comes from the tenant's
    ROUTING_DICT entry (Slack) or a ?tenant= query param on the webhook URL
    (GitHub/Jira, same convention as gmail-webhook), uppercased with
    non-alphanumerics replaced by underscores (Lambda env-var keys only allow
    [A-Za-z0-9_]). This tenant secret is shared by all of a tenant's webhooks,
    so keys this handler owns are UNIFIED_-prefixed (UNIFIED_SQS_QUEUE_NAME_PATTERN)
    to avoid clashing with e.g. gmail's queue pattern.
  - Any key missing from the secrets falls back to a same-named plain env var,
    so existing deployments keep working until their secrets are provisioned.

Slack, GitHub, and Jira are wired in PLUGIN_REGISTRY today; adding another
provider means writing one providers/<slug>.py implementing ProviderPlugin,
registering it below, and adding its config keys to _provider_config() — no
other changes to this handler.
"""

import base64
import json
import logging
import os
from typing import Any

import requests

from config import get_secret
from providers.base import ProviderPlugin, TenantResolutionError
from providers.github import GitHubPlugin
from providers.jira import JiraPlugin
from providers.slack import SlackPlugin
from providers.twilio import TwilioPlugin
from sqs_client import publish_canonical_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)

PLUGIN_REGISTRY: dict[str, ProviderPlugin] = {
    "slack": SlackPlugin(),
    "github": GitHubPlugin(),
    "jira": JiraPlugin(),
    "twilio": TwilioPlugin(),
}


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


COMMON_SECRET_ENV_VAR = "LAMBDA_WEBHOOK_SIGNING_SECRET_ARN"


def _tenant_secret_env_var(tenant_name: str) -> str:
    """LAMBDA_WEBHOOK_<TENANT_NAME>_SECRETS_ARN — the same per-tenant env var
    every other webhook Lambda uses. Tenant name uppercased, with any
    non-alphanumeric characters replaced by underscores (Lambda env-var keys
    only allow [A-Za-z0-9_])."""
    sanitized = "".join(c if c.isalnum() else "_" for c in tenant_name.upper())
    return f"LAMBDA_WEBHOOK_{sanitized}_SECRETS_ARN"


def _load_secret_dict(env_var_name: str) -> dict:
    """Resolve the secret whose ARN lives in `env_var_name` to a dict.
    Missing env var or non-dict secret payload -> {} (never raises)."""
    secret_identifier = os.environ.get(env_var_name)
    if not secret_identifier:
        logger.info(f"{env_var_name} not set — skipping")
        return {}
    try:
        value = get_secret(secret_identifier, default={})
    except Exception as e:
        logger.error(f"Failed to load secret referenced by {env_var_name}: {e}", exc_info=True)
        return {}
    if not isinstance(value, dict):
        logger.error(f"Secret referenced by {env_var_name} is not a JSON object — ignoring")
        return {}
    return value


def _load_common_secrets() -> dict:
    """Settings shared across all tenants (ROUTING_DICT, signing secrets, ...)."""
    return _load_secret_dict(COMMON_SECRET_ENV_VAR)


def _merge_tenant_secrets(common_secrets: dict, tenant_name: str | None) -> dict:
    """Overlay the tenant's own secret on top of the common one — tenant wins."""
    if not tenant_name:
        logger.info("No tenant_name resolved — using common secrets only")
        return dict(common_secrets)
    tenant_secrets = _load_secret_dict(_tenant_secret_env_var(tenant_name))
    return {**common_secrets, **tenant_secrets}


def _resolve_tenant_name(tenant_id: str, secrets: dict, query_params: dict) -> str | None:
    """tenant_id -> tenant_name, for the LAMBDA_WEBHOOK_<TENANT_NAME>_ARN lookup.
    Slack: the ROUTING_DICT entry that routed this tenant carries tenant_name.
    GitHub/Jira: no routing entry — a ?tenant= query param baked into the
    webhook URL (same convention as gmail-webhook) supplies it instead."""
    for entry in _parse_routing_dict(secrets).values():
        if isinstance(entry, dict) and entry.get("tenant_id") == tenant_id:
            tenant_name = entry.get("tenant_name")
            if tenant_name:
                return tenant_name
    return query_params.get("tenant") or None


def _secret_value(secrets: dict, key: str, default: Any = "") -> Any:
    """Read `key` from the merged secrets, falling back to a same-named plain
    env var so pre-migration deployments (config still in Lambda env) work."""
    value = secrets.get(key)
    if value is None:
        value = os.environ.get(key, default)
    return value


def _parse_json_secret(secrets: dict, key: str) -> dict:
    """Secrets Manager JSON gives a dict already; the env-var fallback gives a string."""
    value = _secret_value(secrets, key, default={})
    if isinstance(value, str):
        try:
            value = json.loads(value) if value else {}
        except json.JSONDecodeError:
            logger.error(f"{key} is not valid JSON — falling back to empty mapping")
            value = {}
    return value if isinstance(value, dict) else {}


def _parse_routing_dict(secrets: dict) -> dict:
    return _parse_json_secret(secrets, "ROUTING_DICT")


def _provider_config(provider_slug: str, secrets: dict) -> dict:
    """Provider-level settings drawn from the (common or common+tenant merged)
    secrets — the equivalent of what each legacy per-provider Lambda read
    directly from its own env vars."""
    if provider_slug == "slack":
        return {
            "signing_secret": _secret_value(secrets, "SLACK_SIGNING_SECRET"),
            "routing_dict": _parse_routing_dict(secrets),
        }
    if provider_slug == "github":
        return {"webhook_secret": _secret_value(secrets, "GITHUB_WEBHOOK_SECRET")}
    if provider_slug == "jira":
        return {"webhook_secret": _secret_value(secrets, "JIRA_WEBHOOK_SECRET")}
    if provider_slug == "twilio":
        return {
            "auth_token": _secret_value(secrets, "TWILIO_AUTH_TOKEN"),
            # The exact public URL configured in the Twilio console. Signature
            # validation is over the URL, so a guessed one silently fails.
            "webhook_url": _secret_value(secrets, "TWILIO_WEBHOOK_URL"),
            "number_tenant_map": _parse_json_secret(secrets, "TWILIO_NUMBER_TENANT_MAP"),
        }
    return {}


def _forward_synchronously(
    target: str, raw_body: bytes, headers: dict[str, str], internal_api_key: str
) -> dict:
    """Synchronous proxy for a payload that can't wait on the async SQS pipeline —
    see ProviderPlugin.direct_forward_target's docstring for why (Slack
    Interactivity's trigger_id). Forwards the exact raw bytes Slack sent plus its
    signature headers, so the receiving service can re-verify the HMAC itself,
    mirroring how the legacy slack-webhook Lambda forwarded events.

    The downstream response is relayed back VERBATIM, because for these payloads
    the body is semantically meaningful to the provider, not just an ack: Slack
    reads a view_submission response as either an empty body (close the modal) or
    a {"response_action": ...} object (show field errors / clear). Substituting
    our own {"ok": true} here makes Slack report "We had some trouble
    connecting" and leave the modal open, even though the work succeeded.

    A failed forward degrades to an empty 200 — nothing useful can be relayed,
    and a non-2xx would make Slack retry-bomb; the ask stays PENDING and is
    still resolvable from the run UI."""
    forward_headers = {"X-Internal-Api-Key": internal_api_key}
    for key in ("content-type", "x-slack-signature", "x-slack-request-timestamp"):
        if key in headers:
            forward_headers[key] = headers[key]
    try:
        resp = requests.post(target, data=raw_body, headers=forward_headers, timeout=5)
        if resp.status_code != 200:
            logger.warning(f"Direct forward to {target} returned {resp.status_code}: {resp.text}")
        relayed: dict[str, Any] = {"statusCode": resp.status_code, "body": resp.text or ""}
        content_type = resp.headers.get("Content-Type")
        if content_type:
            relayed["headers"] = {"Content-Type": content_type}
        return relayed
    except Exception as e:
        logger.error(f"Direct forward to {target} failed: {e}", exc_info=True)
        return {"statusCode": 200, "body": ""}


def lambda_handler(event: dict, context: Any) -> dict:
    try:
        provider_slug = (event.get("pathParameters") or {}).get("provider_slug")
        plugin = PLUGIN_REGISTRY.get(provider_slug or "")
        if not plugin:
            logger.warning(f"Unknown provider_slug={provider_slug!r}")
            return _response(404, {"error": f"Unknown provider: {provider_slug}"})

        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        query_params = event.get("queryStringParameters") or {}

        raw_body = event.get("body", "") or ""
        body_bytes = (
            base64.b64decode(raw_body) if event.get("isBase64Encoded") else raw_body.encode("utf-8")
        )

        # Common secrets only at this point — the tenant isn't known until
        # identify_tenant(), and signature verification/routing are tenant-agnostic.
        common_secrets = _load_common_secrets()
        config = _provider_config(provider_slug, common_secrets)
        logger.info(
            f"Config loaded for provider={provider_slug} keys={sorted(common_secrets.keys())}"
        )

        if not plugin.verify_signature(headers, body_bytes, config):
            logger.warning(f"Invalid signature for provider={provider_slug}")
            return _response(401, {"error": "Invalid signature"})

        try:
            body_data = plugin.parse_body(headers, body_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _response(400, {"error": "Invalid JSON"})

        challenge_response = plugin.handle_challenge(headers, body_data)
        if challenge_response is not None:
            return _response(200, challenge_response)

        try:
            tenant_id = plugin.identify_tenant(headers, body_data, query_params, config)
        except TenantResolutionError as e:
            logger.warning(f"Tenant resolution failed for provider={provider_slug}: {e}")
            # Slack retry-bombs unregistered workspaces if we 400 — match legacy
            # behavior of returning 200 for that specific provider.
            if provider_slug == "slack":
                return _response(200, {"ok": True})
            return _response(400, {"error": str(e)})

        # Tenant is known now — overlay the tenant's own secrets and rebuild the
        # provider config so everything downstream sees the merged view.
        tenant_name = _resolve_tenant_name(tenant_id, common_secrets, query_params)
        secrets = _merge_tenant_secrets(common_secrets, tenant_name)
        config = _provider_config(provider_slug, secrets)

        forward_target = plugin.direct_forward_target(headers, body_data, tenant_id, tenant_name)
        if forward_target:
            relayed = _forward_synchronously(
                forward_target, body_bytes, headers, _secret_value(secrets, "INTERNAL_API_KEY")
            )
            logger.info(
                f"Direct-forwarded provider={provider_slug} tenant={tenant_id} "
                f"target={forward_target} status={relayed['statusCode']}"
            )
            return relayed

        # UNIFIED_-prefixed on purpose: the tenant secret is shared across webhooks
        # and the bare SQS_QUEUE_NAME_PATTERN key there belongs to gmail-webhook.
        # When unset, sqs_client falls back to the SQS_QUEUE_NAME_PATTERN env var.
        canonical_event = plugin.to_canonical(headers, body_data, tenant_id, config)
        publish_canonical_event(
            canonical_event.to_dict(),
            queue_name_pattern=_secret_value(
                secrets, "UNIFIED_SQS_QUEUE_NAME_PATTERN", default=None
            ),
        )

        logger.info(
            f"Published event provider={provider_slug} tenant={tenant_id} "
            f"event_type={canonical_event.event_type}"
        )
        # Twilio parses a messaging webhook's response as TwiML — a JSON body earns
        # error 12300 on every inbound, burying real webhook failures in the console.
        if provider_slug == "twilio":
            return {"statusCode": 204, "body": ""}
        return _response(200, {"ok": True})

    except Exception as e:
        logger.error(f"Unhandled error in unified webhook handler: {e}", exc_info=True)
        # Return 200 for provider webhooks that retry-bomb on non-2xx (Slack); the
        # error is logged above and the event is simply dropped for this delivery.
        return _response(200, {"ok": True})
