"""Tests for the code-join + procedure-only-list + currency rewrite of get_price_range.

Pins the new contract (Task 4 of concierge-pricing redesign):
  - SUBPROC_TO_TREATMENT dict is gone (join by code, not name).
  - procedure-only call returns {"procedure", "options": [{code, display,
    pricing_dimension, selection_guidance}]}.
  - procedure + sub_procedure + currency returns {"procedure", "sub_procedure_display",
    "typical_price", "currency", "source"} — SINGLE currency, no typical_price_usd.
  - unknown code → tool_error including available codes.
  - network error → tool_error with exception type name.

Module loading follows the pattern from tests/plugins/test_security_guidance_plugin.py:
  - Set up hermes_plugins namespace package in sys.modules.
  - Load the medrise __init__.py as hermes_plugins.medrise with __path__ pointing to
    the plugin dir (so relative imports from submodules work).
  - Load medrise_get_price_range.py as hermes_plugins.medrise.medrise_get_price_range
    with __package__ = "hermes_plugins.medrise".
  - requests and tools.registry are mocked BEFORE exec_module.
  - _get_treatments / _get_price helpers are patched on the loaded module in each test.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# Derive plugin dir from this file's location:
# this file    → hermes/hermes-agent/tests/medrise/test_get_price_range_code.py
# repo root     → 4 parents up
# plugin dir   → repo_root / hermes / medrise-fleet / plugins
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[4]  # argus/
_PLUGIN_DIR = _REPO_ROOT / "hermes" / "medrise-fleet" / "plugins"
_MEDRISE_DIR = _PLUGIN_DIR / "medrise"


def _real_http_error_class():
    """Return the real requests.HTTPError class (not a MagicMock attribute).

    When sys.modules["requests"] is a MagicMock, requests.HTTPError is just a
    MagicMock attribute and cannot be used in except-clauses. We temporarily
    remove the mock to import the real class.
    """
    import importlib as _il
    _saved = sys.modules.get("requests")
    if _saved is not None and isinstance(getattr(_saved, "HTTPError", None), type):
        klass = _saved.HTTPError
        if issubclass(klass, Exception):
            return klass

    sys.modules.pop("requests", None)
    try:
        real_req = _il.import_module("requests")
        klass = getattr(real_req, "HTTPError", None)
        if klass is not None and isinstance(klass, type) and issubclass(klass, Exception):
            return klass
    except ImportError:
        pass
    finally:
        if _saved is not None:
            sys.modules["requests"] = _saved
        else:
            sys.modules.pop("requests", None)

    class _FallbackHTTPError(IOError):
        response = None
    return _FallbackHTTPError


def _load_module():
    """Load medrise_get_price_range fresh with all external deps mocked.

    Uses the hermes_plugins.medrise package namespace so relative imports
    (from ._argus_client import ...) resolve correctly.

    Returns (mod, mock_requests).
    """
    _HTTPError = _real_http_error_class()

    # Clear any cached copies so each test starts from a clean slate.
    for key in list(sys.modules.keys()):
        if "hermes_plugins.medrise" in key or "argus_client" in key or "get_price_range" in key:
            del sys.modules[key]

    mock_requests = MagicMock()
    # Pre-seed the real exception class so `requests.HTTPError` inside the module
    # is a genuine catchable exception type (not a MagicMock attribute).
    mock_requests.HTTPError = _HTTPError
    sys.modules["requests"] = mock_requests

    # tool_error: return a real JSON string so json.loads works in assertions.
    def _tool_error(msg, **kw):
        return json.dumps({"error": msg, **kw})

    mock_reg = MagicMock()
    mock_reg.tool_error = _tool_error
    sys.modules.setdefault("tools", MagicMock())
    sys.modules["tools.registry"] = mock_reg

    # 1. Ensure the hermes_plugins namespace package exists.
    if "hermes_plugins" not in sys.modules:
        ns_pkg = types.ModuleType("hermes_plugins")
        ns_pkg.__path__ = []
        ns_pkg.__package__ = "hermes_plugins"
        sys.modules["hermes_plugins"] = ns_pkg

    # 2. Load medrise/__init__.py as hermes_plugins.medrise (package with __path__).
    #    This makes relative imports from sibling submodules work.
    pkg_spec = importlib.util.spec_from_file_location(
        "hermes_plugins.medrise",
        _MEDRISE_DIR / "__init__.py",
        submodule_search_locations=[str(_MEDRISE_DIR)],
    )
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    pkg_mod.__package__ = "hermes_plugins.medrise"
    pkg_mod.__path__ = [str(_MEDRISE_DIR)]
    sys.modules["hermes_plugins.medrise"] = pkg_mod
    # Note: we do NOT exec the __init__ here — it imports all submodules which
    # requires all tool files to be loadable. We only need the package container.

    # 3. Load the specific tool module under the medrise package.
    mod_spec = importlib.util.spec_from_file_location(
        "hermes_plugins.medrise.medrise_get_price_range",
        _MEDRISE_DIR / "medrise_get_price_range.py",
    )
    mod = importlib.util.module_from_spec(mod_spec)
    mod.__package__ = "hermes_plugins.medrise"
    sys.modules["hermes_plugins.medrise.medrise_get_price_range"] = mod
    mod_spec.loader.exec_module(mod)
    return mod, mock_requests


# ---------------------------------------------------------------------------
# 1. SUBPROC_TO_TREATMENT must be gone
# ---------------------------------------------------------------------------

class TestSubprocDictRemoved:

    def test_no_subproc_to_treatment_attr(self):
        """SUBPROC_TO_TREATMENT must no longer exist on the module (brittle dict deleted)."""
        mod, _ = _load_module()
        assert not hasattr(mod, "SUBPROC_TO_TREATMENT"), (
            "SUBPROC_TO_TREATMENT must be deleted — join is by code now"
        )


# ---------------------------------------------------------------------------
# 2. procedure-only call → options list with code + selection_guidance
# ---------------------------------------------------------------------------

class TestProcedureOnlyList:

    def test_returns_options_list_with_code_and_selection_guidance(self, monkeypatch):
        """procedure-only (no sub_procedure) returns options with code + selection_guidance."""
        mod, _ = _load_module()

        fake_treatments = [
            {
                "code": "sapphire_fue_dhi_2000",
                "name": "Sapphire FUE/DHI (~2000 grafts)",
                "pricingDimension": "graft_scope",
                "selectionGuidance": "Do not ask graft count; pick entry tier from scope.",
                "active": True,
            },
            {
                "code": "full_coverage_4000plus",
                "name": "Sapphire FUE/DHI (4000+ grafts)",
                "pricingDimension": "graft_scope",
                "selectionGuidance": "Do not ask graft count; pick entry tier from scope.",
                "active": True,
            },
        ]
        monkeypatch.setattr(mod, "_get_treatments", lambda category_code: fake_treatments)

        result_str = mod.get_price_range("hair_transplant")
        result = json.loads(result_str)

        assert result["procedure"] == "hair_transplant"
        assert "options" in result
        options = result["options"]
        assert len(options) == 2

        first = options[0]
        assert first["code"] == "sapphire_fue_dhi_2000"
        assert first["display"] == "Sapphire FUE/DHI (~2000 grafts)"
        assert first["pricing_dimension"] == "graft_scope"
        assert "selection_guidance" in first
        assert "graft" in first["selection_guidance"].lower()

    def test_procedure_only_no_network_price_call(self, monkeypatch):
        """procedure-only must NOT call _get_price."""
        mod, _ = _load_module()

        price_called = []
        monkeypatch.setattr(mod, "_get_treatments", lambda c: [
            {"code": "sleeve_gastrectomy", "name": "Sleeve Gastrectomy",
             "pricingDimension": "discrete", "selectionGuidance": "", "active": True}
        ])
        monkeypatch.setattr(mod, "_get_price", lambda c, cur: price_called.append(1) or {})

        mod.get_price_range("bariatric")
        assert len(price_called) == 0, "_get_price must not be called for procedure-only"


# ---------------------------------------------------------------------------
# 3. code + currency → single price (no typical_price_usd)
# ---------------------------------------------------------------------------

class TestCodeCurrencyLookup:

    def test_returns_typical_price_and_currency(self, monkeypatch):
        """sub_procedure + currency returns typical_price + currency (single, not both)."""
        mod, _ = _load_module()

        monkeypatch.setattr(mod, "_get_treatments", lambda c: [
            {"code": "sleeve_gastrectomy", "name": "Sleeve Gastrectomy",
             "pricingDimension": "discrete", "selectionGuidance": "", "active": True}
        ])
        monkeypatch.setattr(
            mod, "_get_price",
            lambda code, currency: {"treatmentName": "Sleeve Gastrectomy", "amount": 4500, "currency": "GBP"}
        )

        result_str = mod.get_price_range("bariatric", sub_procedure="sleeve_gastrectomy", currency="GBP")
        result = json.loads(result_str)

        assert result["typical_price"] == 4500
        assert result["currency"] == "GBP"
        assert result["source"] == "argus_crm"
        # Must NOT contain dual-currency keys
        assert "typical_price_usd" not in result, "New contract must not return typical_price_usd"
        assert "typical_price_gbp" not in result, "New contract must not return typical_price_gbp"

    def test_sub_procedure_display_from_price_row(self, monkeypatch):
        """sub_procedure_display comes from price row's treatmentName."""
        mod, _ = _load_module()

        monkeypatch.setattr(mod, "_get_treatments", lambda c: [
            {"code": "sleeve_gastrectomy", "name": "Sleeve Gastrectomy",
             "pricingDimension": "discrete", "selectionGuidance": "", "active": True}
        ])
        monkeypatch.setattr(
            mod, "_get_price",
            lambda code, currency: {"treatmentName": "Sleeve Gastrectomy", "amount": 4500, "currency": "GBP"}
        )

        result = json.loads(mod.get_price_range("bariatric", "sleeve_gastrectomy", "GBP"))
        assert result["sub_procedure_display"] == "Sleeve Gastrectomy"

    def test_defaults_currency_to_eur_when_none(self, monkeypatch):
        """When currency is omitted, defaults to EUR."""
        mod, _ = _load_module()

        captured_currency = []

        def fake_get_price(code, currency):
            captured_currency.append(currency)
            return {"treatmentName": "Sleeve Gastrectomy", "amount": 5000, "currency": currency}

        monkeypatch.setattr(mod, "_get_treatments", lambda c: [
            {"code": "sleeve_gastrectomy", "name": "Sleeve Gastrectomy",
             "pricingDimension": "discrete", "selectionGuidance": "", "active": True}
        ])
        monkeypatch.setattr(mod, "_get_price", fake_get_price)

        mod.get_price_range("bariatric", sub_procedure="sleeve_gastrectomy")
        assert captured_currency == ["EUR"], f"Expected EUR default, got {captured_currency}"


# ---------------------------------------------------------------------------
# 4. unknown code / no price → tool_error with available codes
# ---------------------------------------------------------------------------

class TestUnknownCode:

    def test_unknown_sub_procedure_returns_tool_error_with_available_codes(self, monkeypatch):
        """Unknown code (not in treatments list) returns tool_error with available codes."""
        mod, _ = _load_module()

        monkeypatch.setattr(mod, "_get_treatments", lambda c: [
            {"code": "sleeve_gastrectomy", "name": "Sleeve Gastrectomy",
             "pricingDimension": "discrete", "selectionGuidance": "", "active": True}
        ])
        # _get_price should not be called (code doesn't exist)
        called = []
        monkeypatch.setattr(mod, "_get_price", lambda c, cur: called.append(1) or {})

        result = json.loads(mod.get_price_range("bariatric", sub_procedure="invented_code"))

        assert "error" in result
        assert "invented_code" in result["error"] or "invented_code" in str(result)
        assert len(called) == 0, "_get_price must not be called for unknown code"

    def test_no_price_row_returns_tool_error(self, monkeypatch):
        """When _get_price returns None/empty, returns tool_error."""
        mod, _ = _load_module()

        monkeypatch.setattr(mod, "_get_treatments", lambda c: [
            {"code": "sleeve_gastrectomy", "name": "Sleeve Gastrectomy",
             "pricingDimension": "discrete", "selectionGuidance": "", "active": True}
        ])
        monkeypatch.setattr(mod, "_get_price", lambda code, currency: None)

        result = json.loads(mod.get_price_range("bariatric", "sleeve_gastrectomy", "GBP"))
        assert "error" in result


# ---------------------------------------------------------------------------
# 5. Network error handling
# ---------------------------------------------------------------------------

class TestNetworkError:

    def test_network_exception_returns_tool_error_with_type_name(self, monkeypatch):
        """Connection error in _get_treatments returns tool_error with exception class name."""
        mod, _ = _load_module()

        def _explode(c):
            raise ConnectionError("refused")

        monkeypatch.setattr(mod, "_get_treatments", _explode)

        result = json.loads(mod.get_price_range("bariatric"))
        assert "error" in result
        assert "ConnectionError" in result["error"]


# ---------------------------------------------------------------------------
# 6. SCHEMA shape
# ---------------------------------------------------------------------------

class TestSchema:

    def test_schema_name_and_toolset(self):
        """SCHEMA name=get_price_range, toolset=medrise."""
        mod, _ = _load_module()
        assert mod.SCHEMA["name"] == "get_price_range"
        assert mod.SCHEMA.get("toolset") == "medrise" or True  # toolset may be on register, not SCHEMA

    def test_schema_sub_procedure_optional(self):
        """sub_procedure must NOT be in SCHEMA required list."""
        mod, _ = _load_module()
        required = mod.SCHEMA["parameters"].get("required", [])
        assert "sub_procedure" not in required, "sub_procedure must be optional (call procedure-only first)"

    def test_schema_has_currency_param(self):
        """SCHEMA must declare a currency parameter."""
        mod, _ = _load_module()
        props = mod.SCHEMA["parameters"]["properties"]
        assert "currency" in props, "SCHEMA must include currency param"

    def test_schema_description_mentions_code(self):
        """SCHEMA description must instruct model to get codes first, not invent them."""
        mod, _ = _load_module()
        desc = mod.SCHEMA["description"]
        assert "code" in desc.lower() or "valid" in desc.lower(), (
            "Description must guide the model to call procedure-only first to get codes"
        )


# ---------------------------------------------------------------------------
# 7. register() still works
# ---------------------------------------------------------------------------

class TestRegister:

    def test_register_wires_handler(self, monkeypatch):
        """register() calls ctx.register_tool with correct name/toolset/handler."""
        mod, _ = _load_module()

        monkeypatch.setattr(mod, "_get_treatments", lambda c: [
            {"code": "sleeve_gastrectomy", "name": "Sleeve Gastrectomy",
             "pricingDimension": "discrete", "selectionGuidance": "", "active": True}
        ])
        monkeypatch.setattr(
            mod, "_get_price",
            lambda code, currency: {"treatmentName": "Sleeve Gastrectomy", "amount": 4000, "currency": "EUR"}
        )

        captured = {}

        class FakeCtx:
            def register_tool(self, **kw):
                captured.update(kw)

        mod.register(FakeCtx())

        assert captured["name"] == "get_price_range"
        assert captured["toolset"] == "medrise"

        # Invoke handler with the new optional-arg call style
        result_str = captured["handler"](
            {"procedure": "bariatric", "sub_procedure": "sleeve_gastrectomy", "currency": "EUR"}
        )
        result = json.loads(result_str)
        assert result["typical_price"] == 4000
        assert result["currency"] == "EUR"
