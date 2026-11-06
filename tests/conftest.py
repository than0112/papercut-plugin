"""Load the plugin directory as a package, the way Hermes's namespaced import does.

The repository root *is* the plugin (``plugin.yaml`` + ``__init__.py``), and
its directory name is not a valid Python identifier, so tests import it under
the alias ``papercraft_hermes``.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
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


@dataclass
class Usage:
    input_tokens: int = 1200
    output_tokens: int = 800
    total_tokens: int = 2000


@dataclass
class StructuredResult:  # mirrors agent.plugin_llm.PluginLlmStructuredResult
    text: str
    provider: str
    model: str
    agent_id: str = "default"
    usage: Usage = field(default_factory=Usage)
    parsed: object = None
    content_type: str = "text"
    audit: dict = field(default_factory=dict)


class FakeLlm:
    """Scripted replies; parsing follows Hermes's json_mode behaviour (fence strip + json.loads)."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def complete_structured(self, *, instructions, input, json_schema=None, json_mode=False, schema_name=None,
                            system_prompt=None, provider=None, model=None, temperature=None, max_tokens=None,
                            timeout=None, agent_id=None, profile=None, purpose=None, task=None):
        self.calls.append(dict(instructions=instructions, input=input, json_schema=json_schema,
                               json_mode=json_mode, provider=provider, model=model, timeout=timeout,
                               purpose=purpose))
        reply = self.replies.pop(0)
        if callable(reply):  # computed from the call, e.g. an oracle labeller
            reply = reply(self.calls[-1])
        if isinstance(reply, BaseException):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        match = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
        try:
            parsed, ctype = json.loads(match.group(1) if match else text), "json"
        except ValueError:
            parsed, ctype = None, "text"
        return StructuredResult(text=text, provider="openrouter", model="vision-test", parsed=parsed,
                                content_type=ctype)


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
