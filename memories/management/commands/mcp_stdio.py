"""
Memento's nine tools over stdio, for Claude Desktop on this Mac (0023). Claude
Desktop starts this itself, so there is no network and no token: whoever can
run it already has the database. Stdout carries the protocol; nothing else
may print there.
"""

import asyncio

from django.core.management.base import BaseCommand, CommandError

from memories import mcp_server, services
from memories.models import Client, Scope

NAME = "claude-desktop"
ALL = [Scope.READ, Scope.WRITE, Scope.FORGET]


class Command(BaseCommand):
    help = "Run Memento's MCP tools over stdio, as Claude Desktop on this Mac starts them."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Whose memory. Default: the first admin.")

    def handle(self, *args, user=None, **options):
        client = local_client(owner(user))
        mcp_server.current_client.set(client)
        asyncio.run(mcp_server.mcp.run_stdio_async())


def owner(username=None):
    from django.contrib.auth import get_user_model

    users = get_user_model().objects
    found = (
        users.filter(username=username).first()
        if username
        else users.filter(is_superuser=True).order_by("pk").first()
    )
    if found is None:
        raise CommandError("No Memento user yet. Create one first with: make superuser")
    return found


def local_client(owner) -> Client:
    """
    The client every entry made from Claude Desktop is recorded against. It can
    forget, so "don't log that" works; forgetting still previews and waits for a yes.
    """
    client = Client.objects.filter(owner=owner, name=NAME).first()
    if client is None:
        client, _token = services.create_client(owner, NAME, scopes=ALL)
        return client
    if client.revoked_at or set(client.scopes) != set(ALL):
        client.revoked_at, client.scopes = None, ALL
        client.save(update_fields=["revoked_at", "scopes"])
    return client
