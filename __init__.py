"""PaperCraft 3D for Hermes — plugin registration.

Hermes discovers this directory through ``plugin.yaml`` and calls
``register(ctx)`` once. Every tool registered here is listed under
``provides_tools`` in the manifest (``hermes plugins validate`` checks this).
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .core.pipeline import Runtime
from .tools import ALL_TOOLS

logger = logging.getLogger(__name__)

TOOLSET = "papercraft"
__version__ = "0.3.1"


def _bind(module: Any, runtime: Runtime) -> Callable[..., str]:
    def handler(args: dict, **kwargs: Any) -> str:
        return module.handle(args or {}, runtime, **kwargs)

    handler.__name__ = module.NAME
    handler.__doc__ = module.SCHEMA["description"]
    return handler


def register(ctx: Any) -> None:
    runtime = Runtime(ctx=ctx)
    for module in ALL_TOOLS:
        ctx.register_tool(
            name=module.NAME,
            toolset=TOOLSET,
            schema=module.SCHEMA,
            handler=_bind(module, runtime),
            description=module.SCHEMA["description"].split(". ")[0],
            emoji="📜",
        )
    logger.debug("papercraft-3d registered %d tools", len(ALL_TOOLS))
