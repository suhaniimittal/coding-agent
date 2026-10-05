"""
Jira Webhook Handler for AWS Lambda

Jira fires this endpoint when a ticket transitions to the configured "Approved" status.
The webhook payload must include `aetherion_agent_run_id=<uuid>` in the issue description.

POST /webhooks/jira — Jira calls this on issue transition.
Calls milkyway POST /patch/runs/{agent_run_id}/approve-all via internal API key.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from json import JSONDecodeError
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

JIRA_WEBHOOK_SECRET = os.environ.get("JIRA_WEBHOOK_SECRET", "")
JIRA_APPROVED_STATUS = os.environ.get("JIRA_APPROVED_STATUS", "")
MILKYWAY_BASE_URL = os.environ.get("MILKYWAY_BASE_URL", "")
AGENT_RUN_API_BASE_URL = os.environ.get("AGENT_RUN_API_BASE_URL", "http://localhost:8010")
JIRA_RULE_AGENT_CONFIG_MAP = os.environ.get("JIRA_RULE_AGENT_CONFIG_MAP", "")
AUTH_TOKEN_URL = os.environ.get("AUTH_TOKEN_URL", "")
AUTH_CLIENT_ID = os.environ.get("AUTH_CLIENT_ID", "")
AUTH_CLIENT_SECRET = os.environ.get("AUTH_CLIENT_SECRET", "")

_secrets_cache: dict[str, dict[str, Any]] = {}


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _verify_jira_signature(body: bytes, signature: str | None, signing_secret: str) -> bool:
    if not signature:
        logger.warning("Missing X-Hub-Signature header")
        return False
    expected = "sha256=" + hmac.new(signing_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _adf_to_text(node: Any) -> str:
    """Recursively extract plain text from a Jira ADF document node."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    text = ""
    for child in node.get("content", []):
        text += _adf_to_text(child)
    return text


def _get_transition_status(data: dict[str, Any]) -> str:
    """Extract the destination status name from any Jira webhook payload shape."""
    # Jira Automation "Send web request" custom body
    transition = data.get("transition") or {}
    if transition.get("to_status"):
        return transition["to_status"]

    # Native Jira webhook — status in changelog
    for item in (data.get("changelog") or {}).get("items", []):
        if item.get("field") == "status" and item.get("toString"):
            return item["toString"]

    # Native Jira webhook — current status on issue fields
    issue = data.get("issue") or {}
    status = (issue.get("fields") or {}).get("status") or {}
    return status.get("name", "")


def _extract_agent_run_id(description: Any) -> str | None:
    """Extract agent_run_id embedded by create_itsm_ticket_activity."""
    # Normalise to plain text
    if isinstance(description, dict):
        description = _adf_to_text(description)
    description = description or ""
    match = re.search(r"aetherion_agent_run_id=([a-f0-9-]{36})", description)
    return match.group(1) if match else None


def _extract_transition_status_from_raw(raw_body: str) -> str:
    """Best-effort fallback for malformed payloads."""
    transition_match = re.search(r'"to_status"\s*:\s*"([^"]+)"', raw_body)
    if transition_match:
        return transition_match.group(1)
    status_match = re.search(r'"status"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"', raw_body)
    if status_match:
        return status_match.group(1)
    return ""


def _extract_agent_run_id_from_raw(raw_body: str) -> str | None:
    """Best-effort fallback for malformed payloads."""
    match = re.search(r"aetherion_agent_run_id=([a-f0-9-]{36})", raw_body)
    return match.group(1) if match else None


def _get_tenant_id(headers: dict[str, Any]) -> str | None:
    tenant_id = headers.get("X-Tenant-ID") or headers.get("x-tenant-id")
    if tenant_id:
        return str(tenant_id).strip()
    return None


def _parse_rule_agent_map(value: Any) -> dict[str, Any]:
    """Parse JIRA_RULE_AGENT_CONFIG_MAP from either a dict (from secret payload) or a JSON string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Failed to parse JIRA_RULE_AGENT_CONFIG_MAP as JSON")
            return {}
    return {}


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
            import boto3  # noqa: PLC0415

            sm = boto3.client("secretsmanager")
            resp = sm.get_secret_value(SecretId=secret_identifier)
            raw = resp.get("SecretString", "{}")
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict):
                _secrets_cache[secret_identifier] = parsed
                return parsed
        except Exception as e:
            logger.error(f"Failed to fetch tenant secret {secret_identifier}: {e}", exc_info=True)
            return {}

    return {}


def _tenant_config(tenant_id: str | None) -> dict[str, str]:
    """
    Resolve tenant-scoped webhook config.

    Priority:
    1) Tenant secret `LAMBDA_WEBHOOK_<TENANT>_SECRETS_ARN` (JSON with AUTH_* and optional
       MILKYWAY_BASE_URL, JIRA_WEBHOOK_SECRET, JIRA_APPROVED_STATUS)
    2) Global env fallbacks (for local/dev convenience)
    """
    secret_payload: dict[str, Any] = {}
    if tenant_id:
        env_key = f"LAMBDA_WEBHOOK_{tenant_id.upper()}_SECRETS_ARN"
        secret_identifier = os.environ.get(env_key, "")
        if secret_identifier:
            secret_payload = _get_secret_dict(secret_identifier)

    return {
        "milkyway_base_url": str(secret_payload.get("MILKYWAY_BASE_URL") or MILKYWAY_BASE_URL),
        "agent_run_api_base_url": str(
            secret_payload.get("AGENT_RUN_API_BASE_URL") or AGENT_RUN_API_BASE_URL
        ),
        "jira_rule_agent_config_map": _parse_rule_agent_map(
            secret_payload.get("JIRA_RULE_AGENT_CONFIG_MAP") or JIRA_RULE_AGENT_CONFIG_MAP
        ),
        "auth_token_url": str(secret_payload.get("AUTH_TOKEN_URL") or AUTH_TOKEN_URL),
        "auth_client_id": str(secret_payload.get("AUTH_CLIENT_ID") or AUTH_CLIENT_ID),
        "auth_client_secret": str(secret_payload.get("AUTH_CLIENT_SECRET") or AUTH_CLIENT_SECRET),
        "jira_webhook_secret": str(
            secret_payload.get("JIRA_WEBHOOK_SECRET") or JIRA_WEBHOOK_SECRET
        ),
        "jira_approved_status": str(
            secret_payload.get("JIRA_APPROVED_STATUS") or JIRA_APPROVED_STATUS
        ),
    }


def _get_access_token(auth_token_url: str, auth_client_id: str, auth_client_secret: str) -> str:
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": auth_client_id,
            "client_secret": auth_client_secret,
        }
    ).encode()
    req = urllib.request.Request(auth_token_url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())["access_token"]


def _approve_all_for_run(
    agent_run_id: str,
    milkyway_base_url: str,
    auth_token_url: str,
    auth_client_id: str,
    auth_client_secret: str,
) -> dict[str, Any]:
    token = _get_access_token(auth_token_url, auth_client_id, auth_client_secret)
    url = f"{milkyway_base_url}/api/v1/patch/runs/{agent_run_id}/approve-all"
    req = urllib.request.Request(
        url,
        method="POST",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"milkyway approve-all returned {e.code}: {body}") from e


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
        "agent_name": agent_name,
        "id": agent_id,
        "run_in_sync": "false",
        "agent_params": json.dumps(agent_params),
    }
    body, content_type = _build_multipart(fields)
    print(f"Creating agent run with params: {agent_params}")
    req = urllib.request.Request(
        url,
        method="POST",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        raise RuntimeError(f"agent_run_api returned {e.code}: {err_body}") from e


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        print("Received event:", json.dumps(event))
        headers = event.get("headers") or {}
        tenant_id = _get_tenant_id(headers)
        is_local = os.environ.get("ENVIRONMENT", "").lower() == "local"
        if not tenant_id and not is_local:
            logger.error("Missing X-Tenant-ID header")
            return _response(400, {"error": "Missing X-Tenant-ID header"})

        cfg = _tenant_config(tenant_id)

        raw_body = event.get("body", "") or ""
        if event.get("isBase64Encoded", False):
            import base64

            raw_body = base64.b64decode(raw_body).decode("utf-8")

        body_bytes = raw_body.encode("utf-8")

        if cfg["jira_webhook_secret"]:
            signature = headers.get("X-Hub-Signature") or headers.get("x-hub-signature")
            if not _verify_jira_signature(body_bytes, signature, cfg["jira_webhook_secret"]):
                return _response(401, {"error": "Invalid webhook signature"})

        data: dict[str, Any] = {}
        transition_status = ""
        agent_run_id: str | None = None
        description: Any = ""

        try:
            data = json.loads(raw_body) if raw_body else {}
            transition_status = _get_transition_status(data)
            issue = data.get("issue", {})
            fields = issue.get("fields", {})
            description = fields.get("description", "")
            agent_run_id = _extract_agent_run_id(description)
        except JSONDecodeError as json_err:
            logger.warning(
                "Invalid JSON from Jira Automation; falling back to raw-body parsing: %s",
                json_err,
            )
            transition_status = _extract_transition_status_from_raw(raw_body)
            agent_run_id = _extract_agent_run_id_from_raw(raw_body)

        rule_name = data.get("event")
        rule_map = cfg["jira_rule_agent_config_map"]
        rule = rule_map.get(rule_name) if rule_name else None
        if rule:
            if not cfg["agent_run_api_base_url"]:
                logger.error(
                    "AGENT_RUN_API_BASE_URL not configured for tenant_id=%s", tenant_id or "local"
                )
                return _response(500, {"error": "agent_run_api integration not configured"})
            agent_id = str(rule.get("agent_id", ""))
            agent_name = str(rule.get("agent_name", ""))
            logger.info(
                "Jira rule '%s' matched: triggering agent %s (%s) for issue %s",
                rule_name,
                agent_name,
                agent_id,
                data.get("issue_key"),
            )
            result = _create_agent_run(
                agent_params=data,
                agent_run_api_base_url=cfg["agent_run_api_base_url"],
                agent_id=agent_id,
                agent_name=agent_name,
                auth_token_url=cfg["auth_token_url"],
                auth_client_id=cfg["auth_client_id"],
                auth_client_secret=cfg["auth_client_secret"],
            )
            return _response(200, {"status": "created", **result})

        if transition_status.lower() != cfg["jira_approved_status"].lower():
            logger.info(f"Ignoring transition to '{transition_status}'")
            return _response(
                200,
                {
                    "status": "ignored",
                    "reason": f"transition to '{transition_status}' is not approval",
                },
            )

        if not agent_run_id:
            logger.error("Cannot resolve aetherion_agent_run_id from webhook payload")
            return _response(422, {"error": "Cannot resolve aetherion_agent_run_id from webhook payload"})

        if (
            not cfg["milkyway_base_url"]
            or not cfg["auth_token_url"]
            or not cfg["auth_client_id"]
            or not cfg["auth_client_secret"]
            or not cfg["jira_approved_status"]
        ):
            logger.error(
                "Tenant config missing MILKYWAY_BASE_URL, AUTH_* or JIRA_APPROVED_STATUS (tenant_id=%s)",
                tenant_id or "local",
            )
            return _response(500, {"error": "Milkyway integration not configured"})

        logger.info(
            "Jira approval webhook: approving all pending work items for run %s (tenant_id=%s)",
            agent_run_id,
            tenant_id or "local",
        )
        result = _approve_all_for_run(
            agent_run_id=agent_run_id,
            milkyway_base_url=cfg["milkyway_base_url"],
            auth_token_url=cfg["auth_token_url"],
            auth_client_id=cfg["auth_client_id"],
            auth_client_secret=cfg["auth_client_secret"],
        )
        return _response(200, {"status": "approved", **result})

    except Exception as e:
        logger.error(f"Error processing Jira webhook: {e}", exc_info=True)
        return _response(500, {"error": "Internal server error", "message": str(e)})
