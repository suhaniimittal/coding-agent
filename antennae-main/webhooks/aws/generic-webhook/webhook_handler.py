"""
Example Lambda function handler for webhook processing.
This is a sample implementation that can be customized for specific webhook needs.
"""

import json
import logging
import os
from datetime import datetime

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


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
        # Log the incoming event
        logger.info(f"Received event: {json.dumps(event)}")

        # Extract information from the event
        http_method = event.get("httpMethod", "UNKNOWN")
        path = event.get("path", "/")
        headers = event.get("headers", {})
        query_params = event.get("queryStringParameters", {})
        logger.info(f"Query params: {query_params}")
        body = event.get("body", "{}")

        # Get X-Tenant-ID from headers (forwarded by API Gateway)
        tenant_id = headers.get("X-Tenant-ID") or headers.get("x-tenant-id")

        # Parse the request body if it exists
        try:
            body_data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            body_data = {}

        # Process the webhook based on the path or method
        response_data = {
            "message": "Webhook received successfully",
            "timestamp": datetime.utcnow().isoformat(),
            "tenant_id": tenant_id,
            "method": http_method,
            "path": path,
            "received_data": body_data,
            "environment": os.environ.get("ENVIRONMENT", "unknown"),
        }

        # Example: Add custom processing logic here
        # if 'event_type' in body_data:
        #     process_webhook_event(body_data['event_type'], body_data)

        # Return successful response
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,X-Tenant-ID,X-API-Key",
                "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            },
            "body": json.dumps(response_data),
        }

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}", exc_info=True)

        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps({"error": "Internal server error", "message": str(e)}),
        }


def process_webhook_event(event_type, data):
    """
    Process specific webhook event types.

    Args:
        event_type (str): Type of webhook event
        data (dict): Event data
    """
    logger.info(f"Processing webhook event: {event_type}")

    # Add your custom webhook processing logic here
    # Examples:
    # - Store webhook data in DynamoDB
    # - Send notification to SNS/SQS
    # - Call other AWS services
    # - Validate webhook signatures
    # - Process specific business logic

    pass

