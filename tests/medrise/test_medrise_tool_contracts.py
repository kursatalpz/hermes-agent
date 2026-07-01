"""Yeni tool sözleşmeleri (2026-07-01 — mükemmele-yakın turu):

1. save_patient_info: sub_procedure_code alanı YOK (auto-persist get_price_range'e taşındı);
   treatment_category enum'u get_price_range procedure enum'uyla BİREBİR (tek sözlük).
2. get_price_range: tek fiyat döndüğünde kodu enrich-lead'e SUNUCU-taraflı yazar (session_key
   telefonuyla); persist düşse bile fiyat cevabı döner.
3. escalate_to_human: argus insan-penceresine gider (wppconnect yan-kanalı yok); kimlik
   session_key'den (lead_id parametresi YOK); hata dönüşü lead-facing kural taşır.

Loader deseni test_get_price_range_code.py ile aynı; HTTP mock'lu.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[4]  # argus/
_MEDRISE_DIR = _REPO_ROOT / "hermes" / "medrise-fleet" / "plugins" / "medrise"

_CATEGORY_ENUM = ["bariatric", "orthopedic", "ivf", "hair_transplant", "dental", "facial_aesthetics"]


def _load(module_name: str):
    """Plugin modülünü dış bağımlılıkları mock'layarak taze yükle."""
    for key in list(sys.modules.keys()):
        if "hermes_plugins" in key:
            del sys.modules[key]

    mock_requests = MagicMock()
    # `from requests import HTTPError` import ANINDA bağlanır — exec'ten önce gerçek exception
    # sınıfı koyulmalı (MagicMock attribute'u except'te TypeError üretir).
    mock_requests.HTTPError = type("HTTPError", (Exception,), {"response": None})
    sys.modules["requests"] = mock_requests

    tools_registry = MagicMock()
    tools_registry.tool_error = lambda msg, **kw: json.dumps({"error": msg, **kw})
    sys.modules["tools"] = MagicMock()
    sys.modules["tools.registry"] = tools_registry

    ns_pkg = types.ModuleType("hermes_plugins")
    ns_pkg.__path__ = []
    sys.modules["hermes_plugins"] = ns_pkg

    pkg_spec = importlib.util.spec_from_file_location(
        "hermes_plugins.medrise", _MEDRISE_DIR / "__init__.py",
        submodule_search_locations=[str(_MEDRISE_DIR)])
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    pkg_mod.__path__ = [str(_MEDRISE_DIR)]
    sys.modules["hermes_plugins.medrise"] = pkg_mod

    spec = importlib.util.spec_from_file_location(
        f"hermes_plugins.medrise.{module_name}", _MEDRISE_DIR / f"{module_name}.py")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "hermes_plugins.medrise"
    sys.modules[f"hermes_plugins.medrise.{module_name}"] = mod
    spec.loader.exec_module(mod)
    return mod, mock_requests


def _ok_response(payload):
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json.return_value = payload
    return resp


class TestSavePatientInfoContract:

    def test_no_sub_procedure_code_anywhere(self):
        mod, _ = _load("medrise_save_patient_info")
        assert "sub_procedure_code" not in mod.SCHEMA["parameters"]["properties"]
        assert "sub_procedure_code" not in mod._FIELD_MAP

    def test_treatment_category_enum_matches_procedure_enum(self):
        mod, _ = _load("medrise_save_patient_info")
        prop = mod.SCHEMA["parameters"]["properties"]["treatment_category"]
        assert prop.get("enum") == _CATEGORY_ENUM


class TestGetPriceRangeAutoPersist:

    def _run(self, mod, mock_requests, post_fails=False):
        treatments = [{"code": "sapphire_fue_dhi_2000", "name": "Sapphire FUE/DHI (~2000 grafts)",
                       "pricingDimension": "graft_scope", "selectionGuidance": "x", "active": True}]
        price = [{"amount": 3000.0, "currency": "GBP", "treatmentName": "Sapphire FUE/DHI (~2000 grafts)"}]

        def fake_get(url, params=None, headers=None, timeout=None):
            if url.endswith("/treatments"):
                return _ok_response({"data": {"content": treatments}})
            return _ok_response({"data": {"content": price}})

        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["body"] = json
            if post_fails:
                raise RuntimeError("argus down")
            return _ok_response({"data": {"enriched": True, "activeCaseId": 7}})

        mock_requests.get = fake_get
        mock_requests.post = fake_post
        mod.mint_token = lambda: "tok"
        mod.argus_base = lambda: "http://argus"

        out = json.loads(mod.get_price_range(
            procedure="hair_transplant", sub_procedure="sapphire_fue_dhi_2000",
            currency="GBP", session_key="+447700900001"))
        return out, captured

    def test_single_price_persists_quoted_code(self):
        mod, mock_requests = _load("medrise_get_price_range")
        out, captured = self._run(mod, mock_requests)
        assert out["typical_price"] == 3000.0
        assert out["_code_saved"] is True
        assert captured["url"].endswith("/api/v1/conversations/enrich-lead")
        assert captured["body"] == {"phone": "+447700900001",
                                    "subProcedureCode": "sapphire_fue_dhi_2000"}

    def test_persist_failure_does_not_break_price(self):
        mod, mock_requests = _load("medrise_get_price_range")
        out, _ = self._run(mod, mock_requests, post_fails=True)
        assert out["typical_price"] == 3000.0
        assert out["_code_saved"] is False


class TestEscalateToHumanArgus:

    def test_schema_has_no_model_supplied_identity(self):
        mod, _ = _load("medrise_escalate_to_human")
        props = mod.SCHEMA["parameters"]["properties"]
        for phantom in ("lead_id", "persona", "country", "procedure", "stage"):
            assert phantom not in props, f"{phantom} modelden gelmemeli"
        assert set(mod.SCHEMA["parameters"]["required"]) == {"priority", "reason"}

    def test_posts_to_argus_with_session_key_phone(self):
        mod, mock_requests = _load("medrise_escalate_to_human")
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["body"] = json
            return _ok_response({"data": {"escalated": True, "notifiedRecipients": 2}})

        mock_requests.post = fake_post
        mod.mint_token = lambda: "tok"
        mod.argus_base = lambda: "http://argus"

        out = json.loads(mod.escalate_to_human(
            session_key="+447700900002", priority="standard", reason="payment_pending",
            question="can I pay a deposit?", summary="hair lead, October"))
        assert out["status"] == "escalated"
        assert out["notified_recipients"] == 2
        assert captured["url"].endswith("/api/v1/conversations/escalations")
        assert captured["body"]["phone"] == "+447700900002"
        assert captured["body"]["priority"] == "standard"

    def test_error_return_carries_lead_facing_rule(self):
        mod, mock_requests = _load("medrise_escalate_to_human")

        def fake_post(url, json=None, headers=None, timeout=None):
            raise RuntimeError("argus down")

        mock_requests.post = fake_post
        mod.mint_token = lambda: "tok"
        mod.argus_base = lambda: "http://argus"

        out = json.loads(mod.escalate_to_human(
            session_key="+447700900003", priority="standard", reason="unknown_fact"))
        assert out["status"] == "error"
        assert "NEVER mention any system issue" in out["rule"]

    def test_no_session_key_is_graceful(self):
        mod, _ = _load("medrise_escalate_to_human")
        out = json.loads(mod.escalate_to_human(priority="standard", reason="unknown_fact"))
        assert out["status"] == "error"
        assert "rule" in out
