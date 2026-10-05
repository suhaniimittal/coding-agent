"""
Slack Webhook Handler for AWS Lambda

Flow:
  1. Handle url_verification challenge immediately (no team_id needed).
  2. Load the Slack signing secret + team_id→tenant routing_dict from the single secret
     bundle common to all tenants (LAMBDA_WEBHOOK_SIGNING_SECRET_ARN), falling back to
     the SLACK_SIGNING_SECRET / ROUTING_DICT env vars locally.
  3. Verify the Slack HMAC signature (the shared app's signing secret is common — NOT
     per-tenant), then resolve team_id → {tenant_id, tenant_name} via routing_dict.
  4. Forward raw event body to the tenant's Pulsar pod,
     preserving original Slack signature headers so Pulsar can re-verify.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

import requests

from config import get_secret

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_IS_LOCAL = os.environ.get("ENVIRONMENT", "").lower() == "local"

# ------------------------------------------------------------------
# Config: signing secret + routing dict from the common secret bundle
# ------------------------------------------------------------------


def _coerce_routing(raw: Any) -> dict:
    """ROUTING_DICT may arrive as a dict (Secrets Manager JSON secret) or a JSON string
    (plain env var). Normalize to a dict; invalid JSON → empty routing."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.error("ROUTING_DICT is not valid JSON — falling back to empty routing")
    return {}


def _load_config() -> dict:
    """Load and return the whole secret bundle common to all tenants from the single
    ``LAMBDA_WEBHOOK_SIGNING_SECRET_ARN`` env var (a Secrets Manager ARN in AWS, an
    inline JSON env var locally) — all variables, fetched in one call. The caller picks
    out the keys it needs (signing secret + routing dict). Returns an empty dict when
    the ARN is unset (local / tests), so callers fall back to plain env vars.
    """
    secret_identifier = os.environ.get("LAMBDA_WEBHOOK_SIGNING_SECRET_ARN")
    if not secret_identifier:
        return {}
    fetched = get_secret(secret_identifier, default={})
    return fetched if isinstance(fetched, dict) else {}


def _require_config(config: dict, key: str) -> Any:
    """Take ``key`` from the loaded bundle. Deployed environments MUST provide it in the
    bundle (``LAMBDA_WEBHOOK_SIGNING_SECRET_ARN``) — a missing key raises. Only local dev
    (ENVIRONMENT=local) falls back to the plain env var."""
    if key in config:
        return config[key]
    if _IS_LOCAL:
        return os.environ.get(key, "")
    raise RuntimeError(
        f"{key} missing from the Slack secret bundle; set it in "
        f"LAMBDA_WEBHOOK_SIGNING_SECRET_ARN (required in deployed environments)"
    )


# ------------------------------------------------------------------
# Slack HMAC verification
# ------------------------------------------------------------------


def verify_slack_signature(event: dict[str, Any], signing_secret: str) -> bool:
    try:
        headers = event.get("headers", {})
        body = event.get("body", "")

        if event.get("isBase64Encoded", False):
            body = base64.b64decode(body).decode("utf-8")

        slack_signature = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")
        slack_timestamp = headers.get("x-slack-request-timestamp") or headers.get(
            "X-Slack-Request-Timestamp"
        )

        if not slack_signature or not slack_timestamp:
            logger.warning("Missing Slack signature or timestamp headers")
            return False

        if abs(int(time.time()) - int(slack_timestamp)) > 300:
            logger.warning("Slack request timestamp is too old (>5 min)")
            return False

        sig_basestring = f"v0:{slack_timestamp}:{body}"
        computed = (
            "v0="
            + hmac.new(signing_secret.encode(), sig_basestring.encode(), hashlib.sha256).hexdigest()
        )
        return hmac.compare_digest(computed, slack_signature)

    except Exception as e:
        logger.error(f"Error verifying Slack signature: {e}", exc_info=True)
        return False


def _build_pulsar_url(tenant_name: str) -> str:
    """Build the Pulsar base URL from tenant_name and the ENVIRONMENT env var."""
    if _IS_LOCAL:
        return "http://localhost:8020"
    environment = os.environ.get("ENVIRONMENT", "dev")
    return f"https://{tenant_name}.{environment}.aetherion.io"


# ------------------------------------------------------------------
# Forward to Pulsar
# ------------------------------------------------------------------


def forward_to_pulsar(
    pulsar_url: str,
    raw_body: str,
    original_headers: dict,
    tenant_id: str,
    internal_api_key: str = "",
) -> bool:
    """
    POST the raw Slack event body to the tenant's Pulsar /api/slack/events endpoint.

    Secured with X-Internal-Api-Key so the endpoint is not publicly accessible.
    Forwards the original Slack signature headers so Pulsar can re-verify if needed.
    tenant_id is not forwarded — each Pulsar pod knows its own tenant via TENANT_ID env.
    """
    target = pulsar_url.rstrip("/") + "/api/v1/pulsar/slack/events"
    forward_headers = {"Content-Type": "application/json", "X-Internal-Api-Key": internal_api_key}
    # Forward original Slack headers for Pulsar re-verification
    for key in (
        "x-slack-signature",
        "x-slack-request-timestamp",
        "X-Slack-Signature",
        "X-Slack-Request-Timestamp",
    ):
        if key in original_headers:
            forward_headers[key] = original_headers[key]

    try:
        resp = requests.post(target, data=raw_body, headers=forward_headers, timeout=5)
        if resp.status_code == 200:
            logger.info(f"Forwarded to Pulsar tenant={tenant_id} status=200")
            return True
        logger.warning(
            f"""
            Pulsar returned non-200 
            tenant={tenant_id} 
            status={resp.status_code} 
            target={target} 
            response={resp.text} 
            headers={forward_headers}
        """
        )
        return False
    except requests.exceptions.Timeout:
        logger.error(f"Timeout forwarding to Pulsar tenant={tenant_id} url={target}")
        return False
    except Exception as e:
        logger.error(f"Error forwarding to Pulsar tenant={tenant_id}: {e}", exc_info=True)
        return False


# ------------------------------------------------------------------
# Lambda handler
# ------------------------------------------------------------------


def lambda_handler(event, context):
    try:
        logger.info("Received Slack webhook event")

        # Decode body
        raw_body = event.get("body", "{}")
        if event.get("isBase64Encoded", False):
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        try:
            body_data = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            logger.error("Invalid JSON in request body")
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Invalid JSON"}),
            }

        # Handle Slack URL verification challenge — must respond before routing.
        # This handshake carries no team_id, so no tenant/signing secret can be resolved.
        if body_data.get("type") == "url_verification":
            logger.info("Handling Slack URL verification challenge")
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"challenge": body_data.get("challenge")}),
            }

        # Load the whole secret bundle common to all tenants, then take just the signing
        # secret and routing dict out of it. In deployed environments these MUST come
        # from the bundle — a missing key raises. Only local dev falls back to env vars.
        # The Slack app is shared, so the signing secret is NOT per-tenant — the tenant
        # comes from routing_dict[team_id].
        config = _load_config()
        signing_secret = _require_config(config, "SLACK_SIGNING_SECRET")
        routing_dict = _coerce_routing(_require_config(config, "ROUTING_DICT"))
        internal_api_key = _require_config(config, "INTERNAL_API_KEY")

        # Verify the HMAC signature before trusting the payload — common signing secret,
        # so no tenant is needed yet.
        verify_signature = os.environ.get("VERIFY_SLACK_SIGNATURE", "true").lower() == "true"
        if verify_signature:
            # No hardcoded fallback secret: if verification is on, a missing
            # signing secret must reject rather than silently accept.
            if not signing_secret or not verify_slack_signature(event, signing_secret):
                logger.warning("Invalid Slack signature — rejecting request")
                return {
                    "statusCode": 401,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"error": "Invalid signature"}),
                }

        # Resolve team_id → tenant via the routing dict from the bundle.
        team_id = body_data.get("team_id") or (
            body_data.get("team", {}).get("id") if isinstance(body_data.get("team"), dict) else None
        )
        if not team_id:
            logger.error("No team_id in Slack event — cannot route")
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Missing team_id"}),
            }

        routing = routing_dict.get(team_id)
        if not routing and _IS_LOCAL:
            # Local mock: route any unregistered workspace to the demo tenant.
            routing = {"tenant_id": os.environ.get("TENANT_ID", ""), "tenant_name": "demo"}
            logger.info(f"[local mock] defaulting team_id={team_id} -> tenant_name=demo")

        if not routing:
            logger.warning(f"Unknown Slack workspace team_id={team_id} — not registered")
            # Return 200 so Slack doesn't retry-bomb an unregistered workspace
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"ok": True}),
            }

        tenant_id = routing["tenant_id"]
        tenant_name = routing["tenant_name"]
        pulsar_url = _build_pulsar_url(tenant_name)
        logger.info(
            f"Routing team_id={team_id} → tenant_id={tenant_id} "
            f"tenant_name={tenant_name} pulsar_url={pulsar_url}"
        )

        # Forward to tenant's Pulsar pod
        forward_to_pulsar(
            pulsar_url=pulsar_url,
            raw_body=raw_body,
            original_headers=event.get("headers", {}),
            tenant_id=tenant_id,
            internal_api_key=internal_api_key,
        )

        # Always return 200 to Slack — forwarding failures are logged and retried internally
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": True}),
        }

    except Exception as e:
        logger.error(f"Unhandled error in lambda_handler: {e}", exc_info=True)
        # Still return 200 to Slack to avoid retry storms
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": True}),
        }
