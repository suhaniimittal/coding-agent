"""
boto3 SQS publisher for the unified webhook. Publishes into each tenant's own
queue (SQS_QUEUE_NAME_PATTERN, e.g. "unified-events-{environment}-{tenant_id}")
— the same per-tenant queue nebula already provisions for every tenant, not a
separate shared queue — so pulsar's per-tenant pod only ever sees its own
tenant's events. Mirrors the sqs:GetQueueUrl + sqs:SendMessage IAM pattern
already granted to slack-webhook/gmail-webhook (queue resolved by name at
runtime, not a hardcoded URL, since the URL/account-id isn't known in .tfvars).
"""

import json
import logging
import os

import boto3

logger = logging.getLogger()

# SQS_ENDPOINT points at localstack in local dev, matching pulsar's consumer side
# (provider_event_sqs_consumer). Gated on ENVIRONMENT=local so a stray value can
# never redirect a deployed Lambda away from real SQS.
_endpoint = (
    os.environ.get("SQS_ENDPOINT") or None
    if os.environ.get("ENVIRONMENT", "").lower() == "local"
    else None
)
_sqs = boto3.client(
    "sqs",
    region_name=os.environ.get("AWS_REGION", "us-east-1"),
    endpoint_url=_endpoint,
)
_queue_url_cache: dict[str, str] = {}


def _resolve_queue_url(tenant_id: str, queue_name_pattern: str | None = None) -> str:
    if tenant_id not in _queue_url_cache:
        # Pattern normally arrives from the webhook secrets (common/tenant merged);
        # the env var is the pre-migration fallback.
        pattern = queue_name_pattern or os.environ["SQS_QUEUE_NAME_PATTERN"]
        environment = os.environ["ENVIRONMENT"]
        queue_name = pattern.format(environment=environment, tenant_id=tenant_id)
        _queue_url_cache[tenant_id] = _sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
    return _queue_url_cache[tenant_id]


def publish_canonical_event(canonical_event: dict, queue_name_pattern: str | None = None) -> None:
    tenant_id = canonical_event["tenant_id"]
    _sqs.send_message(
        QueueUrl=_resolve_queue_url(tenant_id, queue_name_pattern),
        MessageBody=json.dumps(canonical_event),
        MessageAttributes={
            "provider_slug": {
                "DataType": "String",
                "StringValue": canonical_event["event_source"],
            }
        },
    )
