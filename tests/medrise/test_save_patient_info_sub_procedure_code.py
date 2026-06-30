"""Test that sub_procedure_code is in SCHEMA and maps to subProcedureCode in the CRM body.

Uses the same module-loader pattern as test_get_price_range_code.py.
Does NOT make real HTTP calls — requests.post is mocked.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[4]  # argus/
_PLUGIN_DIR = _REPO_ROOT / "hermes" / "medrise-fleet" / "plugins"
_MEDRISE_DIR = _PLUGIN_DIR / "medrise"


def _load_module():
    """Load medrise_save_patient_info fresh with all external deps mocked."""
    for key in list(sys.modules.keys()):
        if "hermes_plugins.medrise" in key or "argus_client" in key or "save_patient_info" in key:
            del sys.modules[key]

    mock_requests = MagicMock()
    sys.modules["requests"] = mock_requests

    sys.modules.setdefault("tools", MagicMock())
    sys.modules.setdefault("tools.registry", MagicMock())

    if "hermes_plugins" not in sys.modules:
        ns_pkg = types.ModuleType("hermes_plugins")
        ns_pkg.__path__ = []
        ns_pkg.__package__ = "hermes_plugins"
        sys.modules["hermes_plugins"] = ns_pkg

    pkg_spec = importlib.util.spec_from_file_location(
        "hermes_plugins.medrise",
        _MEDRISE_DIR / "__init__.py",
        submodule_search_locations=[str(_MEDRISE_DIR)],
    )
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    pkg_mod.__package__ = "hermes_plugins.medrise"
    pkg_mod.__path__ = [str(_MEDRISE_DIR)]
    sys.modules["hermes_plugins.medrise"] = pkg_mod

    mod_spec = importlib.util.spec_from_file_location(
        "hermes_plugins.medrise.medrise_save_patient_info",
        _MEDRISE_DIR / "medrise_save_patient_info.py",
    )
    mod = importlib.util.module_from_spec(mod_spec)
    mod.__package__ = "hermes_plugins.medrise"
    sys.modules["hermes_plugins.medrise.medrise_save_patient_info"] = mod
    mod_spec.loader.exec_module(mod)
    return mod, mock_requests


class TestSubProcedureCodeFieldMap:

    def test_field_map_contains_sub_procedure_code(self):
        """_FIELD_MAP must map 'sub_procedure_code' → 'subProcedureCode'."""
        mod, _ = _load_module()
        assert "sub_procedure_code" in mod._FIELD_MAP, (
            "_FIELD_MAP must contain 'sub_procedure_code'"
        )
        assert mod._FIELD_MAP["sub_procedure_code"] == "subProcedureCode", (
            "sub_procedure_code must map to 'subProcedureCode' (camelCase CRM field)"
        )

    def test_schema_has_sub_procedure_code_property(self):
        """SCHEMA parameters.properties must include 'sub_procedure_code'."""
        mod, _ = _load_module()
        props = mod.SCHEMA["parameters"]["properties"]
        assert "sub_procedure_code" in props, (
            "SCHEMA must declare sub_procedure_code parameter"
        )
        prop = props["sub_procedure_code"]
        assert prop["type"] == "string"

    def test_sub_procedure_code_not_required(self):
        """sub_procedure_code must NOT be in SCHEMA required list (optional field)."""
        mod, _ = _load_module()
        required = mod.SCHEMA["parameters"].get("required", [])
        assert "sub_procedure_code" not in required

    def test_sub_procedure_code_sent_in_body(self):
        """When sub_procedure_code is passed, it must appear as 'subProcedureCode' in the POST body."""
        mod, _ = _load_module()

        captured_body = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured_body.update(json or {})
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            resp.json.return_value = {"data": {"enriched": True, "activeCaseId": 42}}
            return resp

        # Mock argus_base and mint_token on the _argus_client sub-module
        argus_client_mod = sys.modules.get("hermes_plugins.medrise._argus_client")
        if argus_client_mod is None:
            argus_client_mod = MagicMock()
            sys.modules["hermes_plugins.medrise._argus_client"] = argus_client_mod
        argus_client_mod.argus_base = lambda: "http://localhost:8080"
        argus_client_mod.mint_token = lambda: "test-token"
        mod.argus_base = lambda: "http://localhost:8080"
        mod.mint_token = lambda: "test-token"

        mock_requests_module = sys.modules["requests"]
        mock_requests_module.post = fake_post

        result_str = mod.save_patient_info(
            session_key="+905551234567",
            sub_procedure_code="sapphire_fue_dhi_2000",
        )
        result = json.loads(result_str)

        assert captured_body.get("subProcedureCode") == "sapphire_fue_dhi_2000", (
            f"Expected 'subProcedureCode' in POST body, got: {captured_body}"
        )

    def test_sub_procedure_code_absent_not_in_body(self):
        """When sub_procedure_code is not passed, 'subProcedureCode' must NOT appear in body."""
        mod, _ = _load_module()

        captured_body = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured_body.update(json or {})
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            resp.json.return_value = {"data": {"enriched": True, "activeCaseId": 42}}
            return resp

        mod.argus_base = lambda: "http://localhost:8080"
        mod.mint_token = lambda: "test-token"

        mock_requests_module = sys.modules["requests"]
        mock_requests_module.post = fake_post

        mod.save_patient_info(
            session_key="+905551234567",
            treatment_category="hair",
        )

        assert "subProcedureCode" not in captured_body, (
            f"subProcedureCode must be absent when not passed; body was: {captured_body}"
        )
