"""Unit tests for sqs_client.py."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_unified_dir = Path(__file__).resolve().parent.parent
if str(_unified_dir) not in sys.path:
    sys.path.insert(0, str(_unified_dir))

import sqs_client


class TestPublishCanonicalEvent:
    def setup_method(self):
        # Module-level cache must not leak between tests.
        sqs_client._queue_url_cache = {}

    def test_resolves_queue_url_once_per_tenant_and_caches(self):
        fake_sqs = MagicMock()
        fake_sqs.get_queue_url.return_value = {"QueueUrl": "https://sqs.local/q1"}

        with (
            patch.object(sqs_client, "_sqs", fake_sqs),
            patch.dict(
                "os.environ",
                {
                    "SQS_QUEUE_NAME_PATTERN": "unified-events-{environment}-{tenant_id}",
                    "ENVIRONMENT": "dev",
                },
            ),
        ):
            sqs_client.publish_canonical_event(
                {"event_source": "slack", "event_id": "e1", "tenant_id": "tenant-a"}
            )
            sqs_client.publish_canonical_event(
                {"event_source": "slack", "event_id": "e2", "tenant_id": "tenant-a"}
            )

        fake_sqs.get_queue_url.assert_called_once_with(QueueName="unified-events-dev-tenant-a")
        assert fake_sqs.send_message.call_count == 2

    def test_resolves_a_different_queue_per_tenant(self):
        fake_sqs = MagicMock()
        fake_sqs.get_queue_url.return_value = {"QueueUrl": "https://sqs.local/q1"}

        with (
            patch.object(sqs_client, "_sqs", fake_sqs),
            patch.dict(
                "os.environ",
                {
                    "SQS_QUEUE_NAME_PATTERN": "unified-events-{environment}-{tenant_id}",
                    "ENVIRONMENT": "dev",
                },
            ),
        ):
            sqs_client.publish_canonical_event(
                {"event_source": "slack", "event_id": "e1", "tenant_id": "tenant-a"}
            )
            sqs_client.publish_canonical_event(
                {"event_source": "slack", "event_id": "e2", "tenant_id": "tenant-b"}
            )

        assert fake_sqs.get_queue_url.call_count == 2
        fake_sqs.get_queue_url.assert_any_call(QueueName="unified-events-dev-tenant-a")
        fake_sqs.get_queue_url.assert_any_call(QueueName="unified-events-dev-tenant-b")

    def test_send_message_includes_provider_slug_attribute(self):
        fake_sqs = MagicMock()
        fake_sqs.get_queue_url.return_value = {"QueueUrl": "https://sqs.local/q1"}

        with (
            patch.object(sqs_client, "_sqs", fake_sqs),
            patch.dict(
                "os.environ",
                {
                    "SQS_QUEUE_NAME_PATTERN": "unified-events-{environment}-{tenant_id}",
                    "ENVIRONMENT": "dev",
                },
            ),
        ):
            sqs_client.publish_canonical_event(
                {"event_source": "github", "event_id": "e1", "tenant_id": "tenant-a"}
            )

        call_kwargs = fake_sqs.send_message.call_args.kwargs
        assert call_kwargs["QueueUrl"] == "https://sqs.local/q1"
        assert call_kwargs["MessageAttributes"]["provider_slug"]["StringValue"] == "github"
        assert '"event_id": "e1"' in call_kwargs["MessageBody"]
