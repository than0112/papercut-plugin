"""Phase 1: manifest, registration and handler contract."""

from __future__ import annotations

import json
import re

import pytest
from jsonschema import Draft202012Validator

from conftest import ROOT, call, plugin

EXPECTED_TOOLS = {
    "papercraft_analyze",
    "papercraft_segment",
    "papercraft_build_scene",
    "papercraft_generate_plan",
    "papercraft_update_scene",
    "papercraft_export_project",
}


def _manifest() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))


def test_manifest_required_fields():
    m = _manifest()
    for field in ("name", "version", "description"):
        assert m.get(field), field
    assert isinstance(m["version"], str)
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", m["name"])
    assert re.fullmatch(r">=\d+\.\d+(\.\d+)?", m["requires_hermes"])  # SemVer floor, not CalVer


def test_manifest_version_matches_package():
    assert _manifest()["version"] == plugin.__version__


def test_declared_tools_match_registrations(registered):
    """Mirrors the 'declared tools' check of `hermes plugins validate`."""
    assert set(_manifest()["provides_tools"]) == set(registered.tools) == EXPECTED_TOOLS
    assert set(_manifest().get("provides_hooks") or []) == set(registered.hooks)


def test_config_schema_types_are_known():
    allowed = {"str", "int", "float", "bool", "list", "dict", "secret"}
    for key, spec in _manifest()["config_schema"].items():
        assert spec["type"] in allowed, key


def test_tool_schemas_are_valid_json_schema(registered):
    for name, entry in registered.tools.items():
        schema = entry["schema"]
        assert schema["name"] == name
        assert len(schema["description"]) > 80, f"{name}: description should tell the model when to use it"
        Draft202012Validator.check_schema(schema["parameters"])
        assert entry["toolset"] == "papercraft"
        assert entry["override"] is False


@pytest.mark.parametrize("tool", sorted(EXPECTED_TOOLS))
def test_handlers_never_raise_on_garbage(registered, tool):
    for args in ({}, {"project_id": "../../etc"}, {"image_path": 42}, {"project_id": None}):
        result = call(registered, tool, args)
        assert result["ok"] is False
        assert result["error"]["code"] in {"invalid_argument", "project_not_found", "path_forbidden"}


@pytest.mark.parametrize("tool", sorted(EXPECTED_TOOLS - {"papercraft_analyze"}))
def test_project_tools_report_missing_project(registered, tool):
    args = {"project_id": "does-not-exist", "instruction": "x", "patch": [{"op": "remove", "path": "/x"}]}
    result = call(registered, tool, args)
    assert result == {
        "ok": False,
        "tool": tool,
        "error": result["error"],
    }
    assert result["error"]["code"] == "project_not_found"


def test_analyze_validates_image_then_reports_not_implemented(registered, fixture_dir):
    result = call(registered, "papercraft_analyze", {"image_path": str(fixture_dir / "source.png")})
    assert result["ok"] is False, "an unimplemented stage must never claim success"
    assert result["error"]["code"] == "not_implemented"
    info = result["error"]["details"]["validated_image"]
    assert (info["format"], info["width"], info["height"]) == ("PNG", 480, 320)


def test_analyze_rejects_non_image(registered, tmp_path):
    fake = tmp_path / "evil.png"
    fake.write_text("not really a png")
    result = call(registered, "papercraft_analyze", {"image_path": str(fake)})
    assert result["error"]["code"] == "image_format"


def test_results_are_json_serialisable_strings(registered):
    raw = registered.tools["papercraft_segment"]["handler"]({})
    assert json.loads(raw)["tool"] == "papercraft_segment"
