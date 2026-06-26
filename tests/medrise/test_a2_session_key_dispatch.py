"""Tests that handle_function_call threads session_key (phone) into registry.dispatch.

Task A2: session_key seam — the phone number from the X-Hermes-Session-Key HTTP
header (stored in gateway.session_context.HERMES_SESSION_KEY ContextVar) must
reach tool handlers so they can identify which lead they are acting on.

Test approach: mirrors tests/test_dispatch_session_id.py — patch registry and
get_session_env at the model_tools level, call handle_function_call with
skip_pre_tool_call_hook=True (same as the session_id test), and assert that
session_key is forwarded to registry.dispatch via **kwargs.
"""

import json
from unittest.mock import MagicMock, patch


def _make_registry(captured: dict):
    """Return a mock registry whose dispatch records the kwargs it receives."""
    reg = MagicMock()

    def _dispatch(name, args, **kwargs):
        captured.update(kwargs)
        return json.dumps({"result": "ok"})

    reg.dispatch.side_effect = _dispatch
    return reg


class TestSessionKeyDispatch:

    def test_dispatch_passes_session_key_from_contextvars(self):
        """registry.dispatch receives session_key on the normal tool path."""
        captured = {}
        with patch("model_tools.registry", _make_registry(captured)), \
             patch("gateway.session_context.get_session_env", return_value="+905551112233"):
            from model_tools import handle_function_call
            handle_function_call(
                "save_patient_info",
                {"name": "Ali"},
                task_id="t1",
                session_id="api-abc",
                skip_pre_tool_call_hook=True,
            )
        assert captured.get("session_key") == "+905551112233"

    def test_execute_code_path_also_passes_session_key(self):
        """registry.dispatch receives session_key on the execute_code path too."""
        captured = {}
        with patch("model_tools.registry", _make_registry(captured)), \
             patch("gateway.session_context.get_session_env", return_value="+905559998877"):
            from model_tools import handle_function_call
            handle_function_call(
                "execute_code",
                {"code": "print(1)"},
                task_id="t1",
                session_id="api-xyz",
                skip_pre_tool_call_hook=True,
            )
        assert captured.get("session_key") == "+905559998877"

    def test_empty_session_key_still_forwarded(self):
        """When no session key is set, dispatch still receives session_key=''."""
        captured = {}
        with patch("model_tools.registry", _make_registry(captured)), \
             patch("gateway.session_context.get_session_env", return_value=""):
            from model_tools import handle_function_call
            handle_function_call(
                "web_search",
                {"query": "test"},
                task_id="t1",
                session_id="api-nophone",
                skip_pre_tool_call_hook=True,
            )
        assert "session_key" in captured
        assert captured["session_key"] == ""
