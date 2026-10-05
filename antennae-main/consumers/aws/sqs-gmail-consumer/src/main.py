# src/main.py - MODIFIED TO FOCUS ON EXTRACTION AND S3 UPLOAD
from __future__ import annotations

import asyncio
import json
import os

import boto3
from common_lib.storage.storage_client import storage
from common_lib.utils.logger import setup_logger
from dotenv import load_dotenv
from google.auth.exceptions import RefreshError

from src.client import run_agent
from src.db_utils import decrypt_token
from src.email_filter import email_filter, get_label_config
from src.gmail_client import GmailAPIClient
from src.s3_utils import save_attachment_to_s3

load_dotenv()

logger = setup_logger(__name__)

# --- Environment Configuration ---
environment = os.environ.get("ENVIRONMENT", "dev")
tenant_id = os.environ.get("TENANT_ID")
queue_name_pattern = os.environ.get("QUEUE_NAME_PATTERN", "gmail-events-{environment}-{tenant_id}")
region = os.environ.get("AWS_REGION", "us-east-1")

# Default listener email address (needed for Gmail API calls)
LISTENER_EMAIL = os.environ.get("LISTENER_EMAIL", "aetherion.incoming@gmail.com")


def get_sqs_client():
    """
    Initializes and returns the SQS client using IRSA (no explicit credentials).
    Relies on default AWS credential chain (IRSA in EKS, or default providers).
    """
    try:
        # Use IRSA or default credential chain (no explicit credentials)
        # In EKS, this will use the service account's IRSA role
        return boto3.client("sqs", region_name=region)
    except Exception as e:
        logger.error(f"Error creating SQS client: {e}")
        raise


def get_queue_url(sqs_client, queue_name_pattern: str, environment: str, tenant_id: str) -> str:
    """
    Constructs queue name from pattern and retrieves queue URL.
    Pattern format: "gmail-events-{environment}-{tenant_id}"
    """
    queue_name = queue_name_pattern.format(environment=environment, tenant_id=tenant_id)
    logger.info(f"Resolving queue URL for queue name: {queue_name}")

    try:
        response = sqs_client.get_queue_url(QueueName=queue_name)
        queue_url = response["QueueUrl"]
        logger.info(f"Resolved queue URL: {queue_url}")
        return queue_url
    except Exception as e:
        logger.error(f"Could not get SQS queue URL for {queue_name}: {e}")
        raise


async def poll_queue_and_process_messages():
    """
    Polls the SQS queue for messages and processes each one.
    Uses SQS visibility timeout for automatic retry on failure.
    """
    if not tenant_id:
        logger.error("TENANT_ID environment variable is required")
        raise ValueError("TENANT_ID environment variable is required")

    try:
        sqs_client = get_sqs_client()
        queue_url = get_queue_url(sqs_client, queue_name_pattern, environment, tenant_id)
    except Exception as e:
        logger.error(f"Failed to initialize SQS client or get queue URL: {e}")
        raise

    logger.info(f"Polling SQS queue: {queue_url}")
    logger.info("-" * 40)

    while True:
        try:
            messages = sqs_client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
            )

            if "Messages" in messages:
                for m in messages["Messages"]:
                    message_id = m["MessageId"]
                    receipt_handle = m["ReceiptHandle"]
                    logger.info(f"\n**Message Received** (ID: {message_id})")

                    try:
                        # Parse the SQS Message Body
                        body_json = json.loads(m["Body"])
                        history_id = body_json.get("historyId")

                        if not history_id:
                            logger.error("Message is missing 'historyId'. Skipping.")
                            #  Bad data format - delete to avoid infinite retries
                            sqs_client.delete_message(
                                QueueUrl=queue_url, ReceiptHandle=receipt_handle
                            )
                            continue

                        logger.info(f"Extracted History ID: {history_id}")

                        # Process the message asynchronously
                        s3_keys = await process_gmail_event(history_id, tenant_id)
                        logger.info(f"FINAL S3 KEYS for attachments: {s3_keys}")

                        #  SUCCESS: Delete only on successful processing
                        logger.info(
                            f"Message {message_id} processed successfully. Deleting from queue."
                        )
                        sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                        logger.info(f"Message {message_id} deleted from queue.")

                    except json.JSONDecodeError as json_e:
                        logger.error(f"Invalid JSON in message {message_id}: {json_e}")
                        #  Bad JSON format - delete to avoid infinite retries
                        sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                        logger.warning(f"Deleted malformed message {message_id}")

                    except RefreshError as auth_e:
                        # NON-RETRYABLE: OAuth tokens are invalid/revoked.
                        # Retrying will never succeed — delete message and alert.
                        logger.error(
                            f"OAuth token refresh failed for tenant {tenant_id} "
                            f"(message {message_id}): {auth_e}. "
                            f"The refresh token is likely expired or revoked. "
                            f"Re-authorize the Gmail integration for this tenant.",
                            exc_info=True,
                        )
                        sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                        logger.warning(
                            f"Deleted message {message_id} — OAuth failure is non-retryable."
                        )

                    except Exception as process_e:
                        #  TRANSIENT ERROR: Let SQS handle retry via visibility timeout
                        logger.error(
                            f"Failed to process message {message_id}: {process_e}", exc_info=True
                        )
                        logger.info(
                            f"Message {message_id} will be retried after visibility timeout. "
                            f"Not deleting from queue."
                        )
                        #  Message remains in queue and will be retried automatically

            else:
                logger.info("Queue is empty. Waiting...")
                await asyncio.sleep(5)

        except KeyboardInterrupt:
            logger.info("\nPolling stopped by user.")
            break
        except Exception as e:
            logger.error(f"An unexpected error occurred during polling: {e}", exc_info=True)
            await asyncio.sleep(30)


async def process_gmail_event(history_id: int, tenant_id: str) -> list[str]:
    """
    Orchestrates extraction and upload. Returns a list of S3 keys for all attachments.
    Only processes attachments for emails that pass the filter.
    """

    # 0. Initialization
    try:
        storage.init_client()
    except Exception as e:
        logger.critical(f"CRITICAL: Failed to initialize storage client: {e}")
        raise RuntimeError("Storage client initialization failed") from e

    # 1. Get Decrypted Token
    logger.info(f"Decrypting token for mail : {LISTENER_EMAIL}")
    gmail_token = await decrypt_token(tenant_id, LISTENER_EMAIL)

    # 2. Initialize Gmail Client
    gmail_client = GmailAPIClient(token=gmail_token, email_address=LISTENER_EMAIL)

    # 3. Find the New Message ID
    message_id = gmail_client.get_new_message_id(history_id)
    if not message_id:
        logger.warning(f"Could not find new message for history ID {history_id}. Ending.")
        return []

    # 4. Fetch the message
    message = await gmail_client._fetch_message(msg_id=message_id)
    logger.info(f"fetched message successfully for message id: {message_id}")

    # 5. Extract ONLY basic content and sender info (lightweight operation)
    message_body, sender_email = await gmail_client.extract_basic_info(message=message)

    logger.info(f"Extracted Message Body (Snippet): {message_body[:50]}...")
    logger.info(f"Sender Email: {sender_email}")

    # 6. Apply Email Sender Filtering FIRST (before heavy attachment processing)
    if not email_filter.should_process_email(sender_email):
        logger.info(f"Email from {sender_email} filtered out - stopping processing")
        logger.info(f"Message processing stopped for history ID {history_id}")
        return []  # Don't waste resources on filtered emails

    logger.info(f"Email from {sender_email} passed filter - proceeding with extraction")

    # 7. Build body-only .eml and upload to S3
    eml_bytes = await gmail_client.build_body_eml(message=message)
    eml_filename = f"email_{message_id}.eml"
    eml_s3_key = await save_attachment_to_s3(
        tenant_id=tenant_id,
        file_name=eml_filename,
        file_content=eml_bytes,
    )
    logger.info(f"Email body saved as .eml to S3: {eml_s3_key}")

    # 8. Extract and upload all attachments (only for allowed emails)
    attachments = await gmail_client.extract_attachments(message=message, msg_id=message_id)

    uploaded_s3_keys = [eml_s3_key]
    if attachments:
        logger.info(f"Processing {len(attachments)} attachment(s)")
        for i, (attachment_filename, attachment_content) in enumerate(attachments):
            try:
                s3_key = await save_attachment_to_s3(
                    tenant_id=tenant_id,
                    file_name=attachment_filename,
                    file_content=attachment_content,
                )
                uploaded_s3_keys.append(s3_key)
                logger.info(f"Attachment {i + 1}/{len(attachments)} uploaded to S3: {s3_key}")
            except Exception as e:
                logger.error(f"Failed to upload attachment '{attachment_filename}': {e}")
                continue
    else:
        logger.info("No attachments found in email")

    logger.info(f"History ID: {history_id}")
    logger.info(
        f"Total files uploaded: {len(uploaded_s3_keys)} (1 .eml + "
        f"{len(uploaded_s3_keys) - 1} attachments)"
    )

    # 9. Apply Gmail Label and Mark as Read/Processed
    try:
        # Get centralized label configuration
        label_config = get_label_config()

        # Ensure label exists and get its ID
        label_id = await gmail_client.ensure_label_exists()

        # Apply label, mark as read, and optionally move to label folder
        await gmail_client.apply_label_and_mark_read(
            message_id=message_id,
            label_id=label_id,
            mark_as_read=label_config.mark_as_read,
            remove_from_inbox=label_config.remove_from_inbox,
        )

        logger.info(
            f"Successfully labeled email as '{label_config.name}' and processed Gmail actions"
        )

    except Exception as e:
        logger.error(f"Failed to apply Gmail labeling for message {message_id}: {e}")
        # Don't fail the entire process if labeling fails
        pass

    # 10. Trigger Agent
    logger.info("Starting agent trigger...")
    agent_input = {
        "history_id": history_id,
        "sender_email": sender_email,
        "uploaded_files": uploaded_s3_keys,
    }
    logger.info(f"Agent input: {agent_input}")
    await run_agent(
        agent_name="Receipt Creator",
        agent_params=json.dumps(agent_input),
        run_in_sync=True,
    )

    return uploaded_s3_keys


if __name__ == "__main__":
    if not tenant_id:
        logger.error("TENANT_ID environment variable is required to run the consumer.")
        exit(1)

    logger.info("Starting Gmail SQS Consumer...")
    logger.info(f"Environment: {environment}, Tenant: {tenant_id}")
    try:
        # Run the polling loop
        asyncio.run(poll_queue_and_process_messages())
    except KeyboardInterrupt:
        logger.info("Consumer stopped by user.")
    except Exception as e:
        logger.critical(f"SQS Consumer encountered a critical failure: {e}", exc_info=True)
        raise
