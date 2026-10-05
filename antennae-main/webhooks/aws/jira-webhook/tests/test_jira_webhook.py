"""
Unit tests for jira-webhook webhook_handler.
Loads the module via importlib so it does not conflict with other
webhook_handler entries in sys.modules.
"""

import base64
import hashlib
import hmac
import importlib.util
import json
import sys
import urllib.error
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

_jira_webhook_dir = Path(__file__).resolve().parent.parent
_handler_path = _jira_webhook_dir / "webhook_handler.py"
_spec = importlib.util.spec_from_file_location("jira_webhook_handler", _handler_path)
wh = importlib.util.module_from_spec(_spec)
sys.modules["jira_webhook_handler"] = wh
_spec.loader.exec_module(wh)


# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

_SECRET = "test-webhook-secret"
_AGENT_RUN_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_APPROVED_STATUS = "Approved"
_RULE_WORK_ITEM_CREATE = "Work item create call back"
_RULE_COMMENT_TRIGGERER = "Comment trigger"
_AGENT_ID = "67f41239-8c6f-4c82-9687-44e20d713b53"
_AGENT_NAME = "Aether"
_DEFAULT_RULE_MAP = {
    _RULE_WORK_ITEM_CREATE: {"agent_id": _AGENT_ID, "agent_name": _AGENT_NAME},
    _RULE_COMMENT_TRIGGERER: {"agent_id": _AGENT_ID, "agent_name": _AGENT_NAME},
}


def _sign(body: str, secret: str = _SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def _event(body: dict | str, extra_headers: dict | None = None, base64_encoded: bool = False):
    raw = json.dumps(body) if isinstance(body, dict) else body
    encoded = base64.b64encode(raw.encode()).decode() if base64_encoded else raw
    headers = {
        "x-tenant-id": "tenant1",
        "x-hub-signature": _sign(raw),
    }
    if extra_headers:
        headers.update(extra_headers)
    return {
        "body": encoded,
        "headers": headers,
        "isBase64Encoded": base64_encoded,
        "requestContext": {},
    }


def _approval_body(agent_run_id: str = _AGENT_RUN_ID) -> dict:
    return {
        "transition": {"to_status": _APPROVED_STATUS},
        "issue": {
            "fields": {
                "description": f"Some text aetherion_agent_run_id={agent_run_id} more text",
                "status": {"name": _APPROVED_STATUS},
            }
        },
    }


def _work_item_create_body() -> dict:
    return {
        "event": _RULE_WORK_ITEM_CREATE,
        "issue_key": "AG-50",
        "issue_summary": "Test ticket 3",
        "author": "Manik Anand",
        "comment": "",
    }


def _comment_trigger_body() -> dict:
    return {
        "event": _RULE_COMMENT_TRIGGERER,
        "issue_key": "AG-51",
        "issue_status": "In Progress",
        "issue_summary": "Another ticket",
        "issue_description": "Description here",
        "original_reporter": "Jane Doe",
        "current_comment_id": "12345",
        "current_comment_body": "Please do X",
        "current_comment_author": "John Smith",
        "current_comment_author_email": "john@example.com",
    }


@contextmanager
def _patch_wh_cfg(**overrides):
    """
    Patch the module-level constants that _tenant_config uses as fallbacks.
    patch.dict("os.environ") cannot change these because they are captured at
    module import time via os.environ.get().
    """
    defaults = {
        "JIRA_WEBHOOK_SECRET": _SECRET,
        "JIRA_APPROVED_STATUS": _APPROVED_STATUS,
        "MILKYWAY_BASE_URL": "https://milkyway.test",
        "AGENT_RUN_API_BASE_URL": "https://agent.test",
        "JIRA_RULE_AGENT_CONFIG_MAP": _DEFAULT_RULE_MAP,
        "AUTH_TOKEN_URL": "https://auth.test/token",
        "AUTH_CLIENT_ID": "client-id",
        "AUTH_CLIENT_SECRET": "client-secret",
        **overrides,
    }
    patches = [patch.object(wh, k, v) for k, v in defaults.items()]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# _verify_jira_signature
# ---------------------------------------------------------------------------


class TestVerifyJiraSignature:
    def test_valid_signature(self):
        body = b'{"test": "payload"}'
        sig = "sha256=" + hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert wh._verify_jira_signature(body, sig, _SECRET) is True

    def test_invalid_signature(self):
        body = b'{"test": "payload"}'
        assert wh._verify_jira_signature(body, "sha256=wrong", _SECRET) is False

    def test_missing_signature_returns_false(self):
        assert wh._verify_jira_signature(b"{}", None, _SECRET) is False

    def test_empty_signature_returns_false(self):
        assert wh._verify_jira_signature(b"{}", "", _SECRET) is False


# ---------------------------------------------------------------------------
# _adf_to_text
# ---------------------------------------------------------------------------


class TestAdfToText:
    def test_plain_string(self):
        assert wh._adf_to_text("hello") == "hello"

    def test_text_node(self):
        assert wh._adf_to_text({"type": "text", "text": "hello"}) == "hello"

    def test_nested_content(self):
        node = {
            "type": "doc",
            "content": [
                {"type": "text", "text": "foo"},
                {"type": "text", "text": "bar"},
            ],
        }
        assert wh._adf_to_text(node) == "foobar"

    def test_non_dict_non_str_returns_empty(self):
        assert wh._adf_to_text(42) == ""
        assert wh._adf_to_text(None) == ""


# ---------------------------------------------------------------------------
# _get_transition_status
# ---------------------------------------------------------------------------


class TestGetTransitionStatus:
    def test_from_transition_to_status(self):
        data = {"transition": {"to_status": "Approved"}}
        assert wh._get_transition_status(data) == "Approved"

    def test_from_changelog_items(self):
        data = {"changelog": {"items": [{"field": "status", "toString": "In Review"}]}}
        assert wh._get_transition_status(data) == "In Review"

    def test_from_issue_fields_status(self):
        data = {"issue": {"fields": {"status": {"name": "Done"}}}}
        assert wh._get_transition_status(data) == "Done"

    def test_empty_data_returns_empty_string(self):
        assert wh._get_transition_status({}) == ""

    def test_transition_takes_priority_over_changelog(self):
        data = {
            "transition": {"to_status": "Approved"},
            "changelog": {"items": [{"field": "status", "toString": "Other"}]},
        }
        assert wh._get_transition_status(data) == "Approved"


# ---------------------------------------------------------------------------
# _extract_agent_run_id
# ---------------------------------------------------------------------------


class TestExtractAgentRunId:
    def test_found_in_plain_string(self):
        desc = f"Some text aetherion_agent_run_id={_AGENT_RUN_ID} more"
        assert wh._extract_agent_run_id(desc) == _AGENT_RUN_ID

    def test_found_in_adf_node(self):
        node = {"type": "text", "text": f"aetherion_agent_run_id={_AGENT_RUN_ID}"}
        assert wh._extract_agent_run_id(node) == _AGENT_RUN_ID

    def test_not_found_returns_none(self):
        assert wh._extract_agent_run_id("no id here") is None

    def test_none_returns_none(self):
        assert wh._extract_agent_run_id(None) is None

    def test_empty_string_returns_none(self):
        assert wh._extract_agent_run_id("") is None


# ---------------------------------------------------------------------------
# _build_multipart
# ---------------------------------------------------------------------------


class TestBuildMultipart:
    def test_contains_all_fields(self):
        fields = {"agent_name": "Aether", "id": "123", "run_in_sync": "false"}
        body, content_type = wh._build_multipart(fields)
        decoded = body.decode("utf-8")
        assert "agent_name" in decoded
        assert "Aether" in decoded
        assert "run_in_sync" in decoded
        assert "false" in decoded
        assert "multipart/form-data" in content_type

    def test_boundary_in_content_type_matches_body(self):
        fields = {"key": "value"}
        body, content_type = wh._build_multipart(fields)
        boundary = content_type.split("boundary=")[1]
        assert boundary in body.decode("utf-8")

    def test_agent_params_json_encoded(self):
        params = {"issue_key": "AG-1", "event": "Work item create call back"}
        fields = {
            "agent_name": "Aether",
            "id": "uuid",
            "run_in_sync": "false",
            "agent_params": json.dumps(params),
        }
        body, _ = wh._build_multipart(fields)
        decoded = body.decode("utf-8")
        assert '"issue_key"' in decoded
        assert "AG-1" in decoded


# ---------------------------------------------------------------------------
# lambda_handler — authentication & routing
# ---------------------------------------------------------------------------


class TestLambdaHandlerAuth:
    def test_missing_tenant_id_non_local_returns_400(self):
        event = {"body": "{}", "headers": {}, "isBase64Encoded": False}
        with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
            resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 400
        assert "X-Tenant-ID" in resp["body"]

    def test_local_env_skips_tenant_id_check(self):
        body = _approval_body()
        raw = json.dumps(body)
        event = {
            "body": raw,
            "headers": {"x-hub-signature": _sign(raw)},
            "isBase64Encoded": False,
        }
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with patch.object(wh, "_approve_all_for_run", return_value={"approved": True}):
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200

    def test_invalid_signature_returns_401(self):
        body = _approval_body()
        raw = json.dumps(body)
        event = {
            "body": raw,
            "headers": {"x-tenant-id": "tenant1", "x-hub-signature": "sha256=wrong"},
            "isBase64Encoded": False,
        }
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 401

    def test_no_secret_configured_skips_signature_check(self):
        body = _approval_body()
        raw = json.dumps(body)
        event = {
            "body": raw,
            "headers": {"x-tenant-id": "tenant1"},
            "isBase64Encoded": False,
        }
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg(JIRA_WEBHOOK_SECRET=""):
                with patch.object(wh, "_approve_all_for_run", return_value={"approved": True}):
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200

    def test_base64_encoded_body_is_decoded(self):
        body = _approval_body()
        event = _event(body, base64_encoded=True)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with patch.object(wh, "_approve_all_for_run", return_value={}):
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
# lambda_handler — approval flow (existing behaviour)
# ---------------------------------------------------------------------------


class TestLambdaHandlerApproval:
    def test_non_approval_transition_returns_200_ignored(self):
        body = {"transition": {"to_status": "In Progress"}}
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["status"] == "ignored"

    def test_missing_agent_run_id_returns_422(self):
        body = {
            "transition": {"to_status": _APPROVED_STATUS},
            "issue": {"fields": {"description": "no id here"}},
        }
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 422
        assert "aetherion_agent_run_id" in resp["body"]

    def test_milkyway_not_configured_returns_500(self):
        body = _approval_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg(MILKYWAY_BASE_URL=""):
                resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 500
        assert "not configured" in resp["body"]

    def test_successful_approval_calls_approve_all_and_returns_200(self):
        body = _approval_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with patch.object(
                    wh, "_approve_all_for_run", return_value={"approved": True}
                ) as mock_approve:
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["status"] == "approved"
        mock_approve.assert_called_once_with(
            agent_run_id=_AGENT_RUN_ID,
            milkyway_base_url="https://milkyway.test",
            auth_token_url="https://auth.test/token",
            auth_client_id="client-id",
            auth_client_secret="client-secret",
        )

    def test_agent_run_id_from_adf_description(self):
        body = {
            "transition": {"to_status": _APPROVED_STATUS},
            "issue": {
                "fields": {
                    "description": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "text",
                                "text": f"aetherion_agent_run_id={_AGENT_RUN_ID}",
                            }
                        ],
                    }
                }
            },
        }
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with patch.object(wh, "_approve_all_for_run", return_value={}) as mock_approve:
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200
        mock_approve.assert_called_once()
        assert mock_approve.call_args.kwargs["agent_run_id"] == _AGENT_RUN_ID

    def test_fallback_raw_body_parsing_on_invalid_json(self):
        # Not valid JSON → falls into JSONDecodeError branch, uses regex fallbacks
        bad_raw = (
            f'"to_status": "{_APPROVED_STATUS}", '
            f'aetherion_agent_run_id={_AGENT_RUN_ID}'
        )
        event = {
            "body": bad_raw,
            "headers": {"x-tenant-id": "tenant1"},
            "isBase64Encoded": False,
        }
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg(JIRA_WEBHOOK_SECRET=""):
                with patch.object(wh, "_approve_all_for_run", return_value={}) as mock_approve:
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200
        mock_approve.assert_called_once()

    def test_approve_all_raises_returns_500(self):
        body = _approval_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with patch.object(
                    wh,
                    "_approve_all_for_run",
                    side_effect=RuntimeError("milkyway returned 503: err"),
                ):
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# lambda_handler — Jira Automation callback events (new flow)
# ---------------------------------------------------------------------------


class TestLambdaHandlerAutomationCallbacks:
    def _mock_create(self):
        return patch.object(wh, "_create_agent_run", return_value={"run_id": "new-run-123"})

    def test_work_item_create_calls_create_agent_run(self):
        body = _work_item_create_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with self._mock_create() as mock_create:
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["status"] == "created"
        mock_create.assert_called_once()

    def test_comment_trigger_calls_create_agent_run(self):
        body = _comment_trigger_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with self._mock_create() as mock_create:
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200
        mock_create.assert_called_once()

    def test_create_agent_run_receives_full_body_as_agent_params(self):
        body = _work_item_create_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with self._mock_create() as mock_create:
                    wh.lambda_handler(event, None)
        assert mock_create.call_args.kwargs["agent_params"] == body

    def test_create_agent_run_receives_correct_config(self):
        body = _work_item_create_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with self._mock_create() as mock_create:
                    wh.lambda_handler(event, None)
        kw = mock_create.call_args.kwargs
        assert kw["agent_run_api_base_url"] == "https://agent.test"
        assert kw["agent_id"] == _AGENT_ID
        assert kw["agent_name"] == _AGENT_NAME
        assert kw["auth_token_url"] == "https://auth.test/token"

    def test_agent_run_api_not_configured_returns_500(self):
        body = _work_item_create_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg(AGENT_RUN_API_BASE_URL=""):
                resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 500
        assert "agent_run_api" in resp["body"]

    def test_automation_event_bypasses_transition_check(self):
        """Automation callbacks must NOT be filtered by JIRA_APPROVED_STATUS."""
        body = _work_item_create_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg(JIRA_APPROVED_STATUS="some-other-status"):
                with self._mock_create() as mock_create:
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200
        mock_create.assert_called_once()

    def test_automation_event_bypasses_agent_run_id_check(self):
        """Automation callbacks must NOT 422 because there is no agent_run_id."""
        body = _work_item_create_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with self._mock_create():
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] != 422

    def test_create_agent_run_raises_returns_500(self):
        body = _work_item_create_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with patch.object(
                    wh,
                    "_create_agent_run",
                    side_effect=RuntimeError("agent_run_api returned 503: err"),
                ):
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 500

    def test_unknown_event_name_falls_through_to_transition_logic(self):
        """A payload with an unrecognised event value uses the approval path."""
        body = {
            "event": "Some unknown rule",
            "transition": {"to_status": "Irrelevant"},
            "issue": {"fields": {"description": "no id"}},
        }
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                resp = wh.lambda_handler(event, None)
        # Transition doesn't match approved status → ignored
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["status"] == "ignored"

    def test_rule_uses_per_rule_agent_id_and_name(self):
        """Each rule's agent_id/agent_name from the map is passed to _create_agent_run."""
        custom_map = {
            _RULE_WORK_ITEM_CREATE: {"agent_id": "aaaaaaaa-0000-0000-0000-000000000001", "agent_name": "AgentAlpha"},
        }
        body = _work_item_create_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg(JIRA_RULE_AGENT_CONFIG_MAP=custom_map):
                with self._mock_create() as mock_create:
                    resp = wh.lambda_handler(event, None)
        assert resp["statusCode"] == 200
        kw = mock_create.call_args.kwargs
        assert kw["agent_id"] == "aaaaaaaa-0000-0000-0000-000000000001"
        assert kw["agent_name"] == "AgentAlpha"

    def test_event_not_in_rule_map_does_not_trigger_agent_run(self):
        """Event absent from the map must not call _create_agent_run."""
        body = {
            "event": "Unregistered rule",
            "issue_key": "AG-99",
        }
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg():
                with self._mock_create() as mock_create:
                    resp = wh.lambda_handler(event, None)
        mock_create.assert_not_called()

    def test_empty_rule_map_does_not_trigger_agent_run(self):
        """An empty map means no rule matches — fall through to transition logic."""
        body = _work_item_create_body()
        event = _event(body)
        with patch.dict("os.environ", {"ENVIRONMENT": "local"}):
            with _patch_wh_cfg(JIRA_RULE_AGENT_CONFIG_MAP={}):
                with self._mock_create() as mock_create:
                    resp = wh.lambda_handler(event, None)
        mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_rule_agent_map
# ---------------------------------------------------------------------------


class TestParseRuleAgentMap:
    def test_dict_passthrough(self):
        m = {"Work item create call back": {"agent_id": "uuid-1", "agent_name": "Bot"}}
        assert wh._parse_rule_agent_map(m) == m

    def test_json_string_parsed(self):
        raw = '{"My Rule": {"agent_id": "uuid-2", "agent_name": "R2"}}'
        result = wh._parse_rule_agent_map(raw)
        assert result == {"My Rule": {"agent_id": "uuid-2", "agent_name": "R2"}}

    def test_empty_string_returns_empty(self):
        assert wh._parse_rule_agent_map("") == {}

    def test_none_returns_empty(self):
        assert wh._parse_rule_agent_map(None) == {}

    def test_malformed_json_returns_empty(self):
        assert wh._parse_rule_agent_map("{not valid json") == {}

    def test_json_array_returns_empty(self):
        assert wh._parse_rule_agent_map("[1, 2, 3]") == {}


# ---------------------------------------------------------------------------
# _create_agent_run (unit)
# ---------------------------------------------------------------------------


class TestCreateAgentRun:
    def _mock_urlopen(self, response_data: dict):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return patch("urllib.request.urlopen", return_value=mock_resp)

    def _call(self, agent_params=None, **kwargs):
        defaults = {
            "agent_params": agent_params or {"event": "Work item create call back"},
            "agent_run_api_base_url": "https://agent.test",
            "agent_id": "agent-uuid",
            "agent_name": "Aether",
            "auth_token_url": "https://auth/token",
            "auth_client_id": "cid",
            "auth_client_secret": "csecret",
        }
        defaults.update(kwargs)
        return wh._create_agent_run(**defaults)

    def test_posts_to_correct_endpoint(self):
        with patch.object(wh, "_get_access_token", return_value="tok"):
            with self._mock_urlopen({"run_id": "r1"}) as mock_urlopen:
                self._call()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://agent.test/api/v1/agent/run"
        assert req.method == "POST"

    def test_uses_bearer_token(self):
        with patch.object(wh, "_get_access_token", return_value="my-token"):
            with self._mock_urlopen({}) as mock_urlopen:
                self._call()
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer my-token"

    def test_multipart_content_type_header(self):
        with patch.object(wh, "_get_access_token", return_value="tok"):
            with self._mock_urlopen({}) as mock_urlopen:
                self._call()
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Content-type").startswith("multipart/form-data")

    def test_agent_params_in_body(self):
        params = {"issue_key": "AG-99", "event": "Work item create call back"}
        with patch.object(wh, "_get_access_token", return_value="tok"):
            with self._mock_urlopen({}) as mock_urlopen:
                self._call(agent_params=params)
        body_str = mock_urlopen.call_args[0][0].data.decode("utf-8")
        assert "agent_params" in body_str
        assert "AG-99" in body_str

    def test_run_in_sync_false_in_body(self):
        with patch.object(wh, "_get_access_token", return_value="tok"):
            with self._mock_urlopen({}) as mock_urlopen:
                self._call()
        body_str = mock_urlopen.call_args[0][0].data.decode("utf-8")
        assert "run_in_sync" in body_str
        assert "false" in body_str

    def test_http_error_raises_runtime_error(self):
        err = urllib.error.HTTPError(
            url="https://agent.test/api/v1/agent/run",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=BytesIO(b"downstream error"),
        )
        with patch.object(wh, "_get_access_token", return_value="tok"):
            with patch("urllib.request.urlopen", side_effect=err):
                try:
                    self._call()
                    assert False, "expected RuntimeError"
                except RuntimeError as exc:
                    assert "503" in str(exc)
