"""
Twilio Voice Webhook Handler for AWS Lambda
Validates Twilio signature and returns TwiML response pointing to WebSocket endpoint in milkyway.
This version includes logic to fetch an authentication token and embed it in the TwiML Stream URL.
"""

import base64
import json
import logging
import os
import time
from typing import Any
from urllib.parse import parse_qs

import requests
from twilio.request_validator import RequestValidator

from config import get_secret

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Cache for access tokens
_token_cache: dict[str, dict[str, Any]] = {}
TOKEN_CACHE_EXPIRY = 300  # 5 minutes default expiry

LAMBDA_WEBHOOK_SIGNING_SECRET_ARN = "LAMBDA_WEBHOOK_SIGNING_SECRET_ARN"
TWILIO_AUTH_TOKEN = "TWILIO_AUTH_TOKEN"
AUTH_TOKEN_URL = "AUTH_TOKEN_URL"
AUTH_CLIENT_ID = "AUTH_CLIENT_ID"
AUTH_CLIENT_SECRET = "AUTH_CLIENT_SECRET"

fallback_twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>
    Thank you for calling. We're experiencing technical difficulties. 
    Please try again later.
    </Say>
</Response>
""".strip()


def get_twilio_auth_token() -> str | None:
    """
    Retrieves the Twilio Auth Token from environment or Secrets Manager.
    """
    secret_identifier = os.environ.get(LAMBDA_WEBHOOK_SIGNING_SECRET_ARN)
    if not secret_identifier:
        logger.error(f"{LAMBDA_WEBHOOK_SIGNING_SECRET_ARN} not configured")
        raise Exception(f"{LAMBDA_WEBHOOK_SIGNING_SECRET_ARN} not configured")

    return get_secret(secret_identifier, key=TWILIO_AUTH_TOKEN)


def get_milkyway_auth_token(tenant_id: str) -> str | None:
    """
    Fetches a client credentials access token for the backend media stream service.
    Caches the token based on tenant_id and its expiry time.
    """

    if tenant_id in _token_cache and time.time() < _token_cache[tenant_id]["expiry"]:
        logger.info(f"Using cached token for tenant: {tenant_id}")
        return _token_cache[tenant_id]["token"]

    logger.info(f"Fetching new token for tenant: {tenant_id}")
    LAMBDA_WEBHOOK_TENANT_SECRETS_ARN = f"LAMBDA_WEBHOOK_{tenant_id.upper()}_SECRETS_ARN"
    secret_identifier = os.environ.get(LAMBDA_WEBHOOK_TENANT_SECRETS_ARN)

    logger.info(f"Fetching secrets for tenant: {tenant_id}")
    logger.info(f"Secret identifier: {secret_identifier}")

    auth_url = get_secret(secret_identifier, key=AUTH_TOKEN_URL)
    client_id = get_secret(secret_identifier, key=AUTH_CLIENT_ID)
    client_secret = get_secret(secret_identifier, key=AUTH_CLIENT_SECRET)

    if not all([auth_url, client_id, client_secret]):
        logger.error("Missing required AUTH_ environment variables.")
        return None

    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    try:
        response = requests.post(auth_url, data=data, timeout=5)

        if response.status_code != 200:
            logger.error(
                f"Auth token request failed with "
                f"status: {response.status_code}, body: {response.text}"
            )
            return None

        response_data = response.json()

        access_token = response_data.get("access_token")
        expires_in = response_data.get("expires_in", TOKEN_CACHE_EXPIRY)

        if not access_token:
            logger.error("Auth token response missing 'access_token'.")
            return None

        expiry_time = time.time() + expires_in - 5
        _token_cache[tenant_id] = {
            "token": access_token,
            "expiry": expiry_time,
        }
        logger.info(f"Successfully fetched and cached new token for tenant: {tenant_id}")
        return access_token

    except requests.RequestException as e:
        logger.error(f"Error fetching access token: {str(e)}", exc_info=True)
        return None


def parse_form_data(body: str) -> dict[str, str]:
    """
    Parse application/x-www-form-urlencoded body into dict.

    Matches FastAPI's request.form() behavior to ensure compatibility
    with Twilio signature validation.

    Args:
        body: URL-encoded form data string

    Returns:
        Dict[str, str]: Parsed form parameters with URL-decoded values
    """
    if not body:
        return {}

    # Use keep_blank_values=True to match FastAPI behavior
    # parse_qs automatically URL-decodes the values
    parsed = parse_qs(body, keep_blank_values=True)

    # Convert lists to single values (Twilio sends single values per param)
    return {k: v[0] if isinstance(v, list) and len(v) > 0 else "" for k, v in parsed.items()}


def generate_url_variants(event: dict[str, Any]) -> list[str]:
    """
    Generate possible URL variants for signature validation.

    Since REST API v1 doesn't provide rawQueryString, we need to try different
    query parameter orderings. This function generates all possible orderings
    to handle the case where the original order is unknown.

    Args:
        event: API Gateway event

    Returns:
        List of possible URLs to try for signature validation
    """
    from itertools import permutations

    headers = event.get("headers", {})
    path = event.get("path") or event.get("rawPath", "/voice")

    request_context = event.get("requestContext", {})
    domain_name = request_context.get("domainName") or headers.get("Host")
    protocol = headers.get("X-Forwarded-Proto") or headers.get("x-forwarded-proto") or "https"

    base_url = f"{protocol}://{domain_name}{path}"

    # Check for rawQueryString first (HTTP API v2)
    raw_query_string = event.get("rawQueryString")
    if raw_query_string:
        return [f"{base_url}?{raw_query_string}"]

    # REST API v1 - need to try different orderings
    query_params = event.get("queryStringParameters", {}) or {}

    if not query_params:
        return [base_url]

    # Generate all possible orderings of query parameters
    urls = []
    param_items = list(query_params.items())

    for perm in permutations(param_items):
        query_str = "&".join(f"{k}={v}" for k, v in perm)
        urls.append(f"{base_url}?{query_str}")

    return urls


def verify_twilio_signature(
    event: dict[str, Any],
    auth_token: str,
    url: str,
    form_params: dict[str, str],
) -> bool:
    """
    Verify that the request came from Twilio using the signature.

    If the initial URL doesn't validate, tries alternative query parameter
    orderings since REST API v1 doesn't preserve the original order.
    """
    try:
        headers = event.get("headers", {})

        # Get Twilio signature from headers (case-insensitive)
        twilio_signature = headers.get("X-Twilio-Signature") or headers.get("x-twilio-signature")

        if not twilio_signature:
            logger.warning("Missing Twilio signature in headers")
            return False

        validator = RequestValidator(auth_token)

        # First try with the provided URL
        if validator.validate(url, form_params, twilio_signature):
            logger.info("Twilio signature validated successfully")
            return True

        # If validation failed and rawQueryString wasn't available,
        # try alternative query parameter orderings
        raw_query_string = event.get("rawQueryString")
        if not raw_query_string:
            logger.info("[Signature Debug] Trying alternative query parameter orderings...")
            url_variants = generate_url_variants(event)

            for variant_url in url_variants:
                if variant_url == url:
                    continue  # Already tried this one

                if validator.validate(variant_url, form_params, twilio_signature):
                    logger.info(f"Twilio signature validated with alternative URL: {variant_url}")
                    return True

        logger.warning("Twilio signature validation failed")
        return False

    except Exception as e:
        logger.error(f"Error verifying Twilio signature: {str(e)}", exc_info=True)
        return False


def generate_twiml_response(stream_url: str, auth_token: str) -> str:
    """
    Generate TwiML response pointing to WebSocket endpoint in milkyway.

    Args:
        stream_url: URL for the media stream

    Returns:
        str: TwiML XML string
    """

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url}">
            <Parameter name="auth_token" value="{auth_token}"/>
        </Stream>
    </Connect>
</Response>""".strip()


def build_full_url(event: dict[str, Any]) -> str:
    """
    Build the full URL from API Gateway event for signature validation.

    IMPORTANT: The URL must exactly match what Twilio used to compute the signature,
    including the exact order of query parameters. We use rawQueryString when available
    (HTTP API v2) to preserve the original parameter order.

    Args:
        event: API Gateway event

    Returns:
        str: Full URL
    """
    environment = os.environ.get("ENVIRONMENT")
    if environment == "local":
        url = os.environ.get("VOICE_WEBHOOK_URL")
        logger.info(f"[URL Debug] Final constructed URL: {url}")
        return url

    headers = event.get("headers", {})
    path = event.get("path") or event.get("rawPath", "/voice")

    request_context = event.get("requestContext", {})
    domain_name = request_context.get("domainName") or headers.get("Host")
    protocol = headers.get("X-Forwarded-Proto") or headers.get("x-forwarded-proto") or "https"

    # Use rawQueryString if available (HTTP API v2) to preserve exact parameter order
    # This is critical for Twilio signature validation
    raw_query_string = event.get("rawQueryString")

    if raw_query_string:
        # HTTP API v2 format - use raw query string directly
        query_str = raw_query_string
    else:
        # REST API v1 format - reconstruct from parsed params (order may vary)
        query_params = event.get("queryStringParameters", {}) or {}
        query_parts = [f"{k}={v}" for k, v in query_params.items()]
        query_str = "&".join(query_parts)

    if query_str:
        url = f"{protocol}://{domain_name}{path}?{query_str}"
    else:
        url = f"{protocol}://{domain_name}{path}"

    url = url.replace(" ", "%20")
    logger.info(f"[URL Debug] Final constructed URL: {url}")
    return url


def lambda_handler(event, context):
    """
    Main Lambda handler function for Twilio voice webhooks.

    Args:
        event (dict): API Gateway event containing request details
        context (object): Lambda context object

    Returns:
        dict: Response object with statusCode, headers, and body
    """
    request_id = context.aws_request_id if context else None
    try:
        logger.info(f"Twilio voice webhook handler invoked. Request ID: {request_id}")

        twilio_auth_token = get_twilio_auth_token()
        if not twilio_auth_token:
            logger.error("Failed to retrieve Twilio Auth Token")
            raise Exception("Failed to retrieve Twilio Auth Token")

        # Build full URL for signature validation
        url = build_full_url(event)

        # Parse form data
        body = event.get("body", "")
        if event.get("isBase64Encoded", False):
            body = base64.b64decode(body).decode("utf-8")

        form_params = parse_form_data(body)

        if not verify_twilio_signature(event, twilio_auth_token, url, form_params):
            logger.warning("Twilio signature validation failed")
            return {
                "statusCode": 403,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Invalid signature"}),
            }
        logger.info("Twilio signature validated successfully")

        # Extract call information
        call_sid = form_params.get("CallSid")
        from_number = form_params.get("From")
        to_number = form_params.get("To")

        # Get agent_id from query parameters
        query_string = event.get("queryStringParameters", {}) or {}
        tenant_id = query_string.get("tenant_id", "")
        agent_id = query_string.get("agent_id", "")

        if not call_sid or not tenant_id or not agent_id:
            logger.error("Missing CallSid, tenant_id, or agent_id in request")
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Missing CallSid, tenant_id, or agent_id"}),
            }

        logger.info(
            f"Processing call: CallSid={call_sid}, From={from_number}, "
            f"To={to_number}, tenant_id={tenant_id or 'N/A'}, agent_id={agent_id or 'N/A'}"
        )

        # Generate TwiML response
        try:
            # Get environment variables
            websocket_base_url = os.environ.get(
                "VOICE_WEBSOCKET_BASE_URL",
                f"wss://{tenant_id}.{os.environ.get('ENVIRONMENT')}.aetherion.io",
            )
            websocket_clean_url = websocket_base_url.rstrip("/")

            access_token = get_milkyway_auth_token(tenant_id)

            if not access_token:
                logger.error("Aborting. Could not fetch access token for media stream.")
                # Fallback TwiML response
                return {
                    "statusCode": 200,
                    "headers": {"Content-Type": "application/xml"},
                    "body": fallback_twiml,
                }

            # check if it's inbound callinvoke voice_agent from here with right parameters/payload
            agent_name = query_string.get("agent_name", None)
            launch_agent = query_string.get("launch_agent", "False")

            if launch_agent.lower() in ("true", "1", "yes", "t", "y") and agent_name:
                api_base_url = websocket_clean_url.replace("wss://", "https://")
                agent_run_url = f"{api_base_url}/api/v1/agent/run"
                agent_run_params = {
                    "call_sid": call_sid,
                    "caller_number": from_number,
                }
                agent_run_form = {
                    "agent_name": agent_name,
                    "run_in_sync": "false",
                    "agent_params": json.dumps(agent_run_params),
                }
                logger.info(
                    f"Invoking agent run: url={agent_run_url}, form={json.dumps(agent_run_form)}"
                )
                agent_run_response = requests.post(
                    agent_run_url,
                    data=agent_run_form,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if agent_run_response.status_code not in (200, 201, 202):
                    logger.error(
                        f"Failed to invoke agent run for CallSid={call_sid}: "
                        f"status={agent_run_response.status_code}, "
                        f"body={agent_run_response.text}"
                    )
                    return {
                        "statusCode": 200,
                        "headers": {"Content-Type": "application/xml"},
                        "body": fallback_twiml,
                    }
                else:
                    logger.info(
                        f"Successfully launched agent for CallSid: {call_sid}, "
                        f"Response: {agent_run_response}"
                    )
                    time.sleep(3)

            stream_url_base = f"{websocket_clean_url}/api/v1/media-stream/{call_sid}/{agent_id}"

            twiml = generate_twiml_response(stream_url_base, access_token)
            logger.info(f"Successfully processed webhook for CallSid={call_sid}")
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/xml"},
                "body": twiml,
            }

        except Exception as e:
            logger.exception(f"Error generating TwiML for CallSid={call_sid}: {e}")
            logger.warning(f"Returning fallback TwiML for CallSid={call_sid}")
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/xml"},
                "body": fallback_twiml,
            }

    except Exception as e:
        logger.error(f"Error processing Twilio webhook: {str(e)}", exc_info=True)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/xml"},
            "body": fallback_twiml,
        }


