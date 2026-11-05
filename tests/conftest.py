"""Load the plugin directory as a package, the way Hermes's namespaced import does.

The repository root *is* the plugin (``plugin.yaml`` + ``__init__.py``), and
its directory name is not a valid Python identifier, so tests import it under
the alias ``papercraft_hermes``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "papercraft_hermes"


def _load_plugin_package():
    if PKG in sys.modules:
        return sys.modules[PKG]
    spec = importlib.util.spec_from_file_location(
        PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)
    return module


plugin = _load_plugin_package()
sys.path.insert(0, str(ROOT / "examples"))


class FakeContext:
    """Records registrations using the real PluginContext.register_tool signature (hermes_cli/plugins.py)."""

    def __init__(self, config: dict | None = None, llm=None):
        self.tools: dict[str, dict] = {}
        self.hooks: list[str] = []
        self._config = config or {}
        self.llm = llm

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None,
                      is_async=False, description="", emoji="", override=False):
        assert name not in self.tools, f"duplicate tool {name}"
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler,
                            "description": description, "override": override}

    def register_hook(self, name, callback):
        self.hooks.append(name)

    def get_config(self, key, default=None):
        return self._config.get(key, default)


@pytest.fixture
def fake_ctx(tmp_path):
    return FakeContext(config={"workspace_dir": str(tmp_path / "workspace")})


@pytest.fixture
def registered(fake_ctx):
    plugin.register(fake_ctx)
    return fake_ctx


def call(ctx: FakeContext, tool: str, args: dict) -> dict:
    """Invoke a handler like Hermes does and parse its JSON string result."""
    handler = ctx.tools[tool]["handler"]
    raw = handler(args, task_id="test-task", session_id="test-session")
    assert isinstance(raw, str), "handlers must return a JSON string"
    return json.loads(raw)


@pytest.fixture
def fixture_dir(tmp_path_factory):
    from make_synthetic_fixture import generate

    out = tmp_path_factory.mktemp("synthetic")
    generate(out, 480, 320)
    return out
