"""Unit tests for providers/base.py — the ProviderPlugin contract."""

import sys
from pathlib import Path

import pytest

_unified_dir = Path(__file__).resolve().parent.parent
if str(_unified_dir) not in sys.path:
    sys.path.insert(0, str(_unified_dir))

from providers.base import ProviderPlugin, TenantResolutionError


class TestProviderPluginIsAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            ProviderPlugin()

    def test_incomplete_subclass_cannot_instantiate(self):
        class Incomplete(ProviderPlugin):
            def verify_signature(self, headers, body, config):
                return True

            def handle_challenge(self, headers, body):
                return None

            # identify_tenant and to_canonical intentionally missing

        with pytest.raises(TypeError):
            Incomplete()

    def test_full_subclass_can_instantiate(self):
        class Complete(ProviderPlugin):
            def verify_signature(self, headers, body, config):
                return True

            def handle_challenge(self, headers, body):
                return None

            def identify_tenant(self, headers, body, query_params, config):
                return "tenant-1"

            def to_canonical(self, headers, body, tenant_id, config):
                return None

        plugin = Complete()
        assert plugin.identify_tenant({}, {}, {}, {}) == "tenant-1"


class _MinimalPlugin(ProviderPlugin):
    def verify_signature(self, headers, body, config):
        return True

    def handle_challenge(self, headers, body):
        return None

    def identify_tenant(self, headers, body, query_params, config):
        return "tenant-1"

    def to_canonical(self, headers, body, tenant_id, config):
        return None


class TestParseBodyDefault:
    def test_parses_plain_json(self):
        plugin = _MinimalPlugin()
        assert plugin.parse_body({}, b'{"a": 1}') == {"a": 1}

    def test_empty_body_returns_empty_dict(self):
        plugin = _MinimalPlugin()
        assert plugin.parse_body({}, b"") == {}


class TestDirectForwardTargetDefault:
    def test_returns_none_for_every_provider_by_default(self):
        plugin = _MinimalPlugin()
        assert plugin.direct_forward_target({}, {"type": "anything"}, "tenant-1", "acme") is None


class TestTenantResolutionError:
    def test_is_an_exception(self):
        assert issubclass(TenantResolutionError, Exception)

    def test_carries_message(self):
        try:
            raise TenantResolutionError("missing tenant")
        except TenantResolutionError as e:
            assert str(e) == "missing tenant"
