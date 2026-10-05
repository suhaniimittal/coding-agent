"""
Gmail Webhook Handler for AWS Lambda
Receives Gmail Pub/Sub pushes (or direct payloads) and enqueues events to
email-specific SQS queues.
"""

import base64
import json
import logging
import os
import time
from typing import Any

import boto3
import jwt
from botocore.exceptions import ClientError
from jwt import PyJWKClient

from config import get_secret

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
sqs_client = boto3.client("sqs")
secrets_client = boto3.client("secretsmanager")

jwks_client = None

try:
    jwks_client = PyJWKClient("https://www.googleapis.com/oauth2/v3/certs")
except Exception as e:
    logger.error(f"Error creating JWKS client: {str(e)}", exc_info=True)
    jwks_client = None
    raise RuntimeError(f"Error creating JWKS client: {str(e)}")


def _sanitize_email_for_queue(email: str) -> str:
    """
    Convert an email into a safe SQS queue name segment.
    Replaces unsupported characters and trims to a safe length.
    """
    safe = email.lower()
    # Common replacements
    for ch in ["@", ".", "+", " "]:
        safe = safe.replace(ch, "-")
    # Replace any remaining unsupported characters
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in safe)
    # SQS queue name limit is 80 chars (excluding .fifo). Keep conservative.
    return safe[:64]


def get_sqs_queue_url_by_name(queue_name: str) -> str | None:
    try:
        logger.info(f"Looking up SQS queue: {queue_name}")
        response = sqs_client.get_queue_url(QueueName=queue_name)
        return response["QueueUrl"]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "AWS.SimpleQueueService.NonExistentQueue":
            logger.error(f"SQS queue not found: {queue_name}")
        else:
            logger.error(f"Error getting SQS queue URL: {str(e)}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting SQS queue URL: {str(e)}", exc_info=True)
        return None


def send_to_sqs(
    queue_url: str, message_body: dict[str, Any], email_address: str, tenant: str
) -> bool:
    try:
        message_attributes = {
            "Email": {"StringValue": email_address, "DataType": "String"},
            "Source": {"StringValue": "gmail", "DataType": "String"},
            "Timestamp": {"StringValue": str(int(time.time())), "DataType": "Number"},
            "Tenant": {"StringValue": tenant, "DataType": "String"},
        }
        response = sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message_body),
            MessageAttributes=message_attributes,
        )
        logger.info(f"Message sent to SQS. MessageId: {response['MessageId']}")
        return True
    except ClientError as e:
        logger.error(f"Error sending message to SQS: {str(e)}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending to SQS: {str(e)}", exc_info=True)
        return False


def _decode_base64_json(data_b64: str) -> dict[str, Any]:
    decoded_bytes = base64.b64decode(data_b64)
    try:
        return json.loads(decoded_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        logger.error("Pub/Sub message data is not valid JSON")
        return {"raw": decoded_bytes.decode("utf-8", errors="ignore")}


def parse_pubsub_body(event: dict[str, Any]) -> dict[str, Any] | None:
    """Parse Pub/Sub push payload from Gmail."""
    try:
        message = event.get("message", {})
        if not message:
            logger.warning("No message in Pub/Sub payload")
            return None

        data_b64 = message.get("data")
        if not data_b64:
            logger.warning("Missing message data")
            return None

        decoded = _decode_base64_json(data_b64)
        return decoded
    except Exception as e:
        logger.error(f"Error parsing Pub/Sub body: {e}", exc_info=True)
        return None


def build_processed_event(
    email_address: str, raw_body: dict[str, Any], parsed_payload: dict[str, Any]
) -> dict[str, Any]:
    processed: dict[str, Any] = {
        "source": "gmail",
        "timestamp": int(time.time()),
        "event_type": "gmail_notification",
        "emailAddress": email_address,
        "raw": raw_body,
    }
    if isinstance(parsed_payload, dict):
        # Attach parsed payload (e.g., { emailAddress, historyId })
        processed["payload"] = parsed_payload
        if parsed_payload.get("historyId"):
            processed["historyId"] = parsed_payload.get("historyId")
    return processed


def build_queue_name(environment: str, pattern: str, email_address: str) -> str:
    safe_email = _sanitize_email_for_queue(email_address)
    name = pattern.format(environment=environment, email=safe_email)
    # Ensure length constraints
    return name[:80]


def get_google_jwt_audience_issuer(
    tenant_id: str, tenant: str, environment: str
) -> tuple[str, str]:
    # Default to calfus if tenant is None or empty
    if not tenant:
        logger.warning("Missing tenant parameter, defaulting to 'calfus'")
        tenant = "calfus"

    LAMBDA_WEBHOOK_TENANT_SECRETS_ARN = f"LAMBDA_WEBHOOK_{tenant.upper()}_SECRETS_ARN"
    secret_identifier = os.environ.get(LAMBDA_WEBHOOK_TENANT_SECRETS_ARN)

    audience = get_secret(
        secret_identifier,
        key="PUBSUB_PUSH_ENDPOINT",
        default=f"https://wh.{environment}.aetherion.io/webhooks/gmail?tenant_id={tenant_id}&tenant={tenant}",
    )
    issuer = get_secret(
        secret_identifier,
        key="GMAIL_ISSUER",
        default=f"{tenant}-pubsub-push@aetherion-{environment}.iam.gserviceaccount.com",
    )
    return audience, issuer


def verify_google_jwt(
    auth_header: str, tenant_id: str, tenant: str, environment: str
) -> str | None:
    """Verify the JWT from Google's Pub/Sub push."""
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("Missing or malformed Authorization header")
        return None

    token = auth_header.replace("Bearer ", "")
    if jwks_client is None:
        raise RuntimeError("JWKS client not initialized")

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        expected_audience, expected_issuer = get_google_jwt_audience_issuer(
            tenant_id, tenant, environment
        )
        logger.info(f"Expected audience: {expected_audience}")
        logger.info(f"Expected issuer: {expected_issuer}")

        decoded_jwt = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=expected_audience,
        )

        email = decoded_jwt.get("email", "")
        if email != expected_issuer:
            logger.warning(f"Invalid JWT issuer: {email}")
            return None

        logger.info(f"✅ JWT verified successfully from {email}")
        return email

    except jwt.ExpiredSignatureError:
        logger.error("JWT expired")
    except jwt.InvalidTokenError as e:
        logger.error(f"Invalid JWT: {e}")
    except Exception as e:
        logger.error(f"JWT verification error: {e}", exc_info=True)

    return None


def lambda_handler(event, context):
    """
    Main Lambda handler function.

    Args:
        event (dict): API Gateway event containing request details
        context (object): Lambda context object

    Returns:
        dict: Response object with statusCode, headers, and body
    """
    try:
        logger.info("Received Gmail webhook event")
        environment = os.getenv("ENVIRONMENT", "dev")

        # Step 0: Extract tenant_id from query parameters
        query_params = event.get("queryStringParameters") or {}
        tenant_id = query_params.get("tenant_id")
        tenant = query_params.get("tenant")
        if not tenant_id or not tenant:
            logger.error("Missing tenant_id or tenant")
            return {"statusCode": 400, "body": json.dumps({"error": "Missing tenant_id or tenant"})}

        # Step 1: Verify Google JWT
        auth_header = event.get("headers", {}).get("Authorization", "")
        verified_issuer = verify_google_jwt(auth_header, tenant_id, tenant, environment)
        if not verified_issuer:
            return {"statusCode": 401, "body": json.dumps({"error": "Unauthorized"})}

        # Step 2: Parse Pub/Sub payload
        decoded_bytes = base64.b64decode(event.get("body", ""))
        decoded_str = decoded_bytes.decode("utf-8")
        raw_body = json.loads(decoded_str)
        parsed = parse_pubsub_body(raw_body)
        if not parsed:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Invalid Pub/Sub message"}),
            }

        # Step 3: Extract Gmail emailAddress
        email_address = parsed.get("emailAddress") or parsed.get("email")
        if not email_address:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing emailAddress"}),
            }

        # Step 4: Build queue name + URL
        LAMBDA_WEBHOOK_TENANT_SECRETS_ARN = f"LAMBDA_WEBHOOK_{tenant.upper()}_SECRETS_ARN"
        secret_identifier = os.environ.get(LAMBDA_WEBHOOK_TENANT_SECRETS_ARN)

        # GMAIL_SQS_QUEUE_NAME_PATTERN is the webhook-scoped key (the tenant secret
        # is shared by every webhook, and unified-webhook needs its own pattern);
        # the legacy unprefixed key is honored until tenant secrets are migrated.
        queue_name_pattern = get_secret(
            secret_identifier,
            key="GMAIL_SQS_QUEUE_NAME_PATTERN",
            default=None,
        ) or get_secret(
            secret_identifier,
            key="SQS_QUEUE_NAME_PATTERN",
            default="gmail-events-{environment}-{tenant_id}",
        )
        queue_name = queue_name_pattern.format(environment=environment, tenant_id=tenant_id)
        queue_url = get_sqs_queue_url_by_name(queue_name)

        if not queue_url:
            return {"statusCode": 500, "body": json.dumps({"error": "Queue not found"})}

        # Step 5: Build processed event and enqueue
        processed_event = build_processed_event(email_address, raw_body, parsed)
        success = send_to_sqs(queue_url, processed_event, email_address, tenant)

        if not success:
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Failed to enqueue"}),
            }

        logger.info(f"✅ Successfully processed Gmail notification for {email_address}")

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "message": "Event processed successfully",
                    "email": email_address,
                    "tenant": tenant,
                }
            ),
        }

    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error", "message": str(e)}),
        }
