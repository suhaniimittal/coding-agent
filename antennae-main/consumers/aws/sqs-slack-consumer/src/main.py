import asyncio
import json
import os
import re

import boto3
from common_lib.utils.logger import setup_logger
from dotenv import load_dotenv

from src.client import run_agent

load_dotenv()




logger = setup_logger(__name__)

# Environment configuration
environment = os.environ.get("ENVIRONMENT", "dev")
tenant_id = os.environ.get("TENANT_ID")
queue_name_pattern = os.environ.get("QUEUE_NAME_PATTERN", "slack-events-{environment}-{tenant_id}")
region = os.environ.get("AWS_REGION", "us-east-1")


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
    Pattern format: "slack-events-{environment}-{tenant_id}"
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


def poll_queue_for_messages():
    """
    Continuously polls the SQS queue for messages and runs the agent for each.
    """
    if not tenant_id:
        logger.error("TENANT_ID environment variable is required")
        raise ValueError("TENANT_ID environment variable is required")
    
    try:
        sqs_client = get_sqs_client()
        queue_url = get_queue_url(sqs_client, queue_name_pattern, environment, tenant_id)
    except Exception as e:
        logger.error(f"Failed to initialize SQS client or get queue URL: {e}")
        return

    logger.info(f"Polling queue: {queue_url} (Press CTRL+C to stop)")
    logger.info("-------------------------------------------------")

    while True:
        try:
            messages = sqs_client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20, # This will make the call block for up to 20 seconds
                MessageAttributeNames=["All"],
            )

            if "Messages" in messages:
                for m in messages["Messages"]:
                    logger.info("\n**Message Received**")
                    message_id = m["MessageId"]
                    receipt_handle = m["ReceiptHandle"]
                    logger.info(f"MessageID: {message_id}")

                    # --- Parse the JSON body ---
                    body_str = m["Body"]
                    try:
                        body_json = json.loads(body_str)
                        slack_text = body_json.get("text", None)
                        slack_text = re.sub(r"<@[^>]+>", "", slack_text).strip()
                        logger.info(f"Tenant: {body_json.get('tenant_id')}")
                        logger.info(f"Channel: {body_json.get('channel')}")
                        logger.info(f"User: {body_json.get('user')}")
                        logger.info(f"Text: {slack_text}")
                        logger.info(f"Full Event Type: {body_json.get('event_type')}")
                        logger.info(
                            f"Raw Event ID: {body_json.get('raw_event', {}).get('event_id')}"
                        )

                        # --- Prepare agent parameters ---
                        agent_params = {"ticket_key": slack_text}

                        # --- **START AGENT LOGIC HERE** ---
                        logger.info("Starting agent...")
                        asyncio.run(
                            run_agent(
                                agent_name="AssistIQ Resolution",
                                agent_params=json.dumps(agent_params),
                                run_in_sync=True,
                            )
                        )
                        logger.info("Agent run complete.")
                        
                        # --- Delete the message after successful processing ---
                        # If you decide to delete the message after successful processing, 
                        # uncomment the next two lines:
                        sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
                        logger.info(f"Message {message_id} Deleted from Queue.")

                    except json.JSONDecodeError:
                        logger.error("Error: Could not decode message body as JSON.")
                        logger.info(f"Raw Body: {body_str}")
                    except Exception as e:
                        logger.error(
                            f"Error processing message or running agent: {e}", exc_info=True
                        )


            else:
                # The poll naturally times out after 20 seconds, logging this is optional
                logger.info("Queue is currently empty. Waiting for the next poll.")
                # REMOVED: `break` statement

        except KeyboardInterrupt:
            logger.info("\nPolling stopped by user.")
            break
        except Exception as e:
            logger.error(f"An error occurred in the polling loop: {e}")
            break # Break on unrecoverable error


if __name__ == "__main__":
    logger.info("Starting SQS Slack Consumer...")
    logger.info(f"Environment: {environment}, Tenant: {tenant_id}")
    
    try:
        poll_queue_for_messages()
    except KeyboardInterrupt:
        logger.info("Consumer stopped by user.")
    except Exception as e:
        logger.critical(f"SQS Consumer encountered a critical failure: {e}", exc_info=True)
        raise
