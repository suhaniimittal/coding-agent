"""
Graph Mail Reply Webhook Handler for AWS Lambda

Receives Microsoft Graph API change notifications when a new email lands in the
agent's inbox, validates the notification, fetches the full message from Graph,
then triggers a new Arbiter agent run via the agent/run API so the agent can
read the reply, analyse it, and respond back to the supplier.

Tenant identification:
  Graph does not forward custom headers, so tenant context is embedded in the
  subscription's `clientState` (JSON) at subscription-creation time by the
  `subscribe_reply_notifications` tool.  The webhook extracts `tenant_id` from
  `clientState` and then follows the same config-resolution pattern used by
  the Jira webhook: LAMBDA_WEBHOOK_<TENANT>_SECRETS_ARN → Secrets Manager.

Graph subscription flow:
  1. subscribe_reply_notifications registers a subscription and sets clientState
     to JSON containing: secret, tenant_id, conversation_id, supplier_name,
     supplier_email.
  2. Graph immediately POSTs ?validationToken=<token> — handler echoes it back
     as text/plain (required within 10 s).
  3. On each new inbox message Graph POSTs a notification batch.  Handler
     validates, fetches the message, and calls POST /api/v1/agent/run.

Required env vars (or Secrets Manager keys per tenant):
  AGENT_EMAIL          – sender mailbox UPN
  AZURE_TENANT_ID      – Azure AD tenant ID
  AZURE_CLIENT_ID      – App registration client ID
  AZURE_CLIENT_SECRET  – App registration client secret
  GRAPH_WEBHOOK_SECRET – Must match the secret embedded in clientState
  AGENT_RUN_API_BASE_URL – Base URL of the Aetherion agent/run API
  AGENT_ID             – Arbiter agent UUID
  AGENT_NAME           – "Arbiter"
  AUTH_TOKEN_URL       – OAuth2 token endpoint for agent/run API access
  AUTH_CLIENT_ID       – Client ID for agent/run API auth
  AUTH_CLIENT_SECRET   – Client secret for agent/run API auth
"""

import base64
import hashlib
import json
import logging
import os
import time
from typing import Any

import boto3
import msal
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
GRAPH_MESSAGE_URL = (
    "https://graph.microsoft.com/v1.0/users/{sender}/messages/{message_id}"
    "?$select=id,conversationId,subject,from,receivedDateTime,body,bodyPreview"
)

_secrets_cache: dict[str, dict[str, Any]] = {}

# Deduplication: multiple subscriptions on the same inbox all fire for every
# incoming message.  We track processed message IDs for a short TTL window so
# only the first notification for a given message triggers an agent run.
_processed_messages: dict[str, float] = {}
_DEDUP_TTL_SECONDS = 300  # 5 minutes is more than enough for duplicate bursts


def _is_duplicate(message_id: str) -> bool:
    """Return True if this message_id was already processed within the TTL window."""
    now = time.time()
    # Evict stale entries to keep the dict from growing unbounded
    stale = [k for k, ts in _processed_messages.items() if now - ts > _DEDUP_TTL_SECONDS]
    for k in stale:
        del _processed_messages[k]
    if message_id in _processed_messages:
        return True
    _processed_messages[message_id] = now
    return False


def _email_hash(address: str) -> str:
    """16-char hex SHA-256 of a normalised email — mirrors subscribe_reply_notifications."""
    return hashlib.sha256(address.lower().encode()).hexdigest()[:16]

# ---------------------------------------------------------------------------
# Helpers shared with Jira-webhook pattern
# ---------------------------------------------------------------------------

def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_secret_dict(secret_identifier: str) -> dict[str, Any]:
    if secret_identifier in _secrets_cache:
        return _secrets_cache[secret_identifier]
    if not secret_identifier:
        return {}
    if secret_identifier.strip().startswith("{"):
        try:
            parsed = json.loads(secret_identifier)
            if isinstance(parsed, dict):
                _secrets_cache[secret_identifier] = parsed
                return parsed
        except json.JSONDecodeError:
            return {}
    if secret_identifier.startswith("arn:aws:secretsmanager:"):
        try:
            sm = boto3.client("secretsmanager")
            resp = sm.get_secret_value(SecretId=secret_identifier)
            raw = resp.get("SecretString", "{}")
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict):
                _secrets_cache[secret_identifier] = parsed
                return parsed
        except Exception as exc:
            logger.error("Failed to fetch secret %s: %s", secret_identifier, exc, exc_info=True)
    return {}


def _tenant_config(tenant_id: str | None) -> dict[str, str]:
    secret_payload: dict[str, Any] = {}
    if tenant_id:
        env_key = f"LAMBDA_WEBHOOK_{tenant_id.upper()}_SECRETS_ARN"
        secret_identifier = os.environ.get(env_key, "")
        if secret_identifier:
            secret_payload = _get_secret_dict(secret_identifier)

    def _v(key: str) -> str:
        return str(secret_payload.get(key) or os.environ.get(key, ""))

    return {
        "agent_email":           _v("AGENT_EMAIL"),
        "azure_tenant_id":       _v("AZURE_TENANT_ID"),
        "azure_client_id":       _v("AZURE_CLIENT_ID"),
        "azure_client_secret":   _v("AZURE_CLIENT_SECRET"),
        "graph_webhook_secret":  _v("GRAPH_WEBHOOK_SECRET"),
        "agent_run_api_base_url": _v("AGENT_RUN_API_BASE_URL"),
        "agent_id":              _v("ARBITER_AGENT_ID"),
        "agent_name":            _v("ARBITER_AGENT_NAME"),
        "auth_token_url":        _v("AUTH_TOKEN_URL"),
        "auth_client_id":        _v("AUTH_CLIENT_ID"),
        "auth_client_secret":    _v("AUTH_CLIENT_SECRET"),
    }


# ---------------------------------------------------------------------------
# OAuth2 token for agent/run API
# ---------------------------------------------------------------------------

_SSL_VERIFY = os.environ.get("ENVIRONMENT", "").lower() != "local"


def _get_access_token(auth_token_url: str, auth_client_id: str, auth_client_secret: str) -> str:
    resp = requests.post(
        auth_token_url,
        data={
            "grant_type":    "client_credentials",
            "client_id":     auth_client_id,
            "client_secret": auth_client_secret,
        },
        timeout=10,
        verify=_SSL_VERIFY,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Microsoft Graph token  (MSAL client-credentials)
# ---------------------------------------------------------------------------

def _get_graph_token(azure_tenant_id: str, azure_client_id: str, azure_client_secret: str) -> str:
    authority = f"https://login.microsoftonline.com/{azure_tenant_id}"
    app = msal.ConfidentialClientApplication(
        azure_client_id, authority=authority, client_credential=azure_client_secret
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"Graph token error: {result.get('error_description') or result}")
    return result["access_token"]


# ---------------------------------------------------------------------------
# Graph message fetch
# ---------------------------------------------------------------------------

def _fetch_message(sender: str, message_id: str, graph_token: str) -> dict[str, Any]:
    url = GRAPH_MESSAGE_URL.format(sender=sender, message_id=message_id)
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {graph_token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Graph getMessage {resp.status_code}: {resp.text[:200]}")
    return resp.json()


# ---------------------------------------------------------------------------
# Agent/run  (identical multipart pattern to Jira-webhook)
# ---------------------------------------------------------------------------

def _build_multipart(fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = "----FormBoundary" + hashlib.sha256(
        json.dumps(fields, sort_keys=True).encode()
    ).hexdigest()[:16]
    lines: list[str] = []
    for name, value in fields.items():
        lines.append(f"--{boundary}")
        lines.append(f'Content-Disposition: form-data; name="{name}"')
        lines.append("")
        lines.append(value)
    lines.append(f"--{boundary}--")
    return "\r\n".join(lines).encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def _create_agent_run(
    agent_params: dict[str, Any],
    agent_run_api_base_url: str,
    agent_id: str,
    agent_name: str,
    auth_token_url: str,
    auth_client_id: str,
    auth_client_secret: str,
) -> dict[str, Any]:
    token = _get_access_token(auth_token_url, auth_client_id, auth_client_secret)
    url = f"{agent_run_api_base_url}/api/v1/agent/run"
    fields = {
        "agent_name":   agent_name,
        "id":           agent_id,
        "run_in_sync":  "false",
        "agent_params": json.dumps(agent_params),
    }
    body, content_type = _build_multipart(fields)
    resp = requests.post(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  content_type,
        },
        timeout=10,
        verify=_SSL_VERIFY,
    )
    if not resp.ok:
        raise RuntimeError(f"agent/run returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        query_params = event.get("queryStringParameters") or {}

        # ── Graph validation handshake ───────────────────────────────────────
        # Graph POSTs ?validationToken=<token> immediately after subscription
        # creation. Must echo it back as text/plain within 10 seconds.
        validation_token = query_params.get("validationToken")
        if validation_token:
            logger.info("Graph subscription validation handshake")
            return {"statusCode": 200, "headers": {"Content-Type": "text/plain"}, "body": validation_token}

        # ── Parse notification batch ─────────────────────────────────────────
        raw_body = event.get("body") or ""
        if event.get("isBase64Encoded", False):
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            logger.error("Non-JSON body received | raw=%s", raw_body[:200])
            return _response(400, {"error": "Invalid JSON body"})

        notifications = body.get("value") or []
        if not notifications:
            return _response(200, {"processed": 0})

        processed = 0
        skipped = 0

        for notif in notifications:
            client_state_raw = notif.get("clientState") or ""
            try:
                client_state = json.loads(client_state_raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Skipping — invalid clientState JSON")
                skipped += 1
                continue

            # ── Resolve tenant + config (short key "t") ─────────────────────
            tenant_id = (client_state.get("t") or "").strip()
            cfg = _tenant_config(tenant_id or None)

            # ── Validate secret (short key "s" in clientState) ───────────────
            stored_secret = (client_state.get("s") or "")
            expected_secret = cfg["graph_webhook_secret"]
            # Compare only the capped prefix (subscribe_reply_notifications caps at 20 chars)
            if expected_secret and stored_secret != expected_secret[:20]:
                logger.warning("Skipping — clientState secret mismatch (tenant=%s)", tenant_id)
                skipped += 1
                continue

            supplier_name = client_state.get("n", "")
            expected_email_hash = client_state.get("eh", "")

            resource_data = notif.get("resourceData") or {}
            message_id = resource_data.get("id")
            if not message_id:
                logger.warning("Notification missing resourceData.id")
                skipped += 1
                continue

            # ── Deduplicate ──────────────────────────────────────────────────
            # Multiple subscriptions on the same inbox all fire for the same
            # incoming message.  Only the first notification for a given
            # message_id within the TTL window triggers an agent run.
            if _is_duplicate(message_id):
                logger.info("Duplicate notification for message %s — skipping", message_id)
                skipped += 1
                continue

            # ── Fetch full message from Graph ────────────────────────────────
            try:
                graph_token = _get_graph_token(
                    cfg["azure_tenant_id"], cfg["azure_client_id"], cfg["azure_client_secret"]
                )
                message = _fetch_message(cfg["agent_email"], message_id, graph_token)
            except Exception as exc:
                logger.error("Failed to fetch message %s: %s", message_id, exc)
                skipped += 1
                continue

            # ── Skip messages sent by the agent itself ───────────────────────
            from_address = (
                (message.get("from") or {}).get("emailAddress", {}).get("address", "").lower()
            )
            if from_address == cfg["agent_email"].lower():
                skipped += 1
                continue

            # ── Match reply by hashed from-address ──────────────────────────
            # subscribe_reply_notifications stores SHA-256[:16] of the supplier
            # email as "eh" in clientState (raw email doesn't fit in 128 chars).
            if expected_email_hash and _email_hash(from_address) != expected_email_hash:
                logger.info(
                    "from_address hash mismatch for subscription %s, skipping",
                    notif.get("subscriptionId"),
                )
                skipped += 1
                continue

            # ── Trigger agent run ────────────────────────────────────────────
            agent_params = {
                "mode":             "process_reply",
                "tenant_id":        tenant_id,
                "conversation_id":  message.get("conversationId", ""),
                "message_id":       message_id,
                "supplier_name":    supplier_name,
                "supplier_email":   from_address,   # use actual from_address (full, untruncated)
                "from_address":     from_address,
                "subject":          message.get("subject", ""),
                "received_at":      message.get("receivedDateTime", ""),
                "body_preview":     message.get("bodyPreview", ""),
                "body":             (message.get("body") or {}).get("content", ""),
                "body_content_type": (message.get("body") or {}).get("contentType", "Text"),
            }

            try:
                result = _create_agent_run(
                    agent_params=agent_params,
                    agent_run_api_base_url=cfg["agent_run_api_base_url"],
                    agent_id=cfg["agent_id"],
                    agent_name=cfg["agent_name"],
                    auth_token_url=cfg["auth_token_url"],
                    auth_client_id=cfg["auth_client_id"],
                    auth_client_secret=cfg["auth_client_secret"],
                )
                processed += 1
                logger.info(
                    "Agent run triggered | supplier=%s tenant=%s run=%s",
                    client_state.get("supplier_name"), tenant_id,
                    result.get("id") or result.get("run_id"),
                )
            except Exception as exc:
                logger.error("agent/run call failed: %s", exc, exc_info=True)
                skipped += 1

        logger.info("Batch complete | processed=%d skipped=%d", processed, skipped)
        return _response(202, {"processed": processed, "skipped": skipped})

    except Exception as exc:
        logger.error("Unhandled error: %s", exc, exc_info=True)
        return _response(500, {"error": "Internal server error", "message": str(exc)})
