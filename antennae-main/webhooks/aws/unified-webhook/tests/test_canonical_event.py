"""Unit tests for canonical_event.py."""

import sys
from pathlib import Path

_unified_dir = Path(__file__).resolve().parent.parent
if str(_unified_dir) not in sys.path:
    sys.path.insert(0, str(_unified_dir))

from canonical_event import CanonicalEvent, EventActor


class TestEventActor:
    def test_defaults(self):
        actor = EventActor()
        assert actor.id is None
        assert actor.email is None
        assert actor.display_name is None
        assert actor.actor_type == "user"

    def test_explicit_values(self):
        actor = EventActor(id="U1", email="a@b.com", display_name="A B", actor_type="bot")
        assert actor.id == "U1"
        assert actor.actor_type == "bot"


class TestCanonicalEvent:
    def test_to_dict_includes_all_fields(self):
        event = CanonicalEvent(
            event_id="e1",
            event_source="slack",
            event_type="app_mention.received",
            tenant_id="t1",
            received_at="2026-01-01T00:00:00+00:00",
            source_workspace_id="T1",
            source_resource_id="C1",
            provider_event_id="ev1",
            actor=EventActor(id="U1"),
            structured_payload={"text": "hi"},
            raw_payload={"raw": True},
        )
        d = event.to_dict()
        assert d["event_id"] == "e1"
        assert d["event_source"] == "slack"
        assert d["schema_version"] == "1.0"
        assert d["actor"]["id"] == "U1"
        assert d["structured_payload"] == {"text": "hi"}
        assert d["raw_payload"] == {"raw": True}

    def test_defaults_are_serialisable(self):
        event = CanonicalEvent(
            event_id="e1",
            event_source="github",
            event_type="push.received",
            tenant_id="t1",
            received_at="2026-01-01T00:00:00+00:00",
        )
        d = event.to_dict()
        assert d["source_workspace_id"] is None
        assert d["structured_payload"] == {}
        assert d["raw_payload"] == {}
        assert d["actor"]["actor_type"] == "user"
