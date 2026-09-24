"""
ASGI entry point, as served by gunicorn's uvicorn workers.

/mcp goes to the MCP server's streamable HTTP app; everything else goes to
Django. Lifespan events go to the MCP app too, because its session manager
needs a task group that lives as long as the worker. Django has no lifespan.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_app = get_asgi_application()

from memories.mcp_server import http_app  # noqa: E402  (needs Django set up first)

mcp_app = http_app()


def is_mcp(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


async def application(scope, receive, send):
    if scope["type"] == "lifespan" or is_mcp(scope.get("path", "")):
        await mcp_app(scope, receive, send)
    else:
        await django_app(scope, receive, send)
