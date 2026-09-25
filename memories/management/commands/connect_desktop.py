"""
Connect Claude Desktop to Memento on this Mac in one step (0020): a token with the
forget scope, kept in a file only you can read, and Memento added to Claude
Desktop's settings. Safe to run again: the new token replaces the old one.
"""

import json
import shutil
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from memories import services
from memories.models import Client, Scope

NAME = "claude-desktop"
CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
TOKEN_FILE = Path.home() / ".memento-claude-desktop"
URL = "http://127.0.0.1:8000/mcp"


class Command(BaseCommand):
    help = "Connect Claude Desktop to Memento: token, token file and Claude Desktop settings."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Whose memory. Default: the only (or first) admin.")
        parser.add_argument("--config", type=Path, default=CONFIG)
        parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)

    def handle(self, *args, user=None, config, token_file, **options):
        owner = _owner(user)
        settings = _read(config)  # before anything changes, so a bad file changes nothing

        # Forget is included: without it "don't log that" and "forget ..." are refused.
        # Forgetting still previews what will go and waits for a yes.
        old = Client.objects.filter(owner=owner, name__startswith=NAME, revoked_at__isnull=True)
        name = (
            NAME
            if not Client.objects.filter(owner=owner, name=NAME).exists()
            else (f"{NAME}-{timezone.now():%Y%m%d-%H%M%S}")
        )
        client, token = services.create_client(
            owner, name, scopes=[Scope.READ, Scope.WRITE, Scope.FORGET]
        )
        for stale in old.exclude(pk=client.pk):
            services.revoke_client(stale)

        # A file only you can read, so the token never appears in a process list.
        token_file.write_text(f"Authorization: Bearer {token}\n")
        token_file.chmod(0o600)

        if config.exists():
            shutil.copy(config, config.with_name(config.name + ".before-memento"))
        settings.setdefault("mcpServers", {})["memento"] = {
            "command": shutil.which("npx") or "/opt/homebrew/bin/npx",
            "args": [
                "-y", "mcp-remote@latest", URL,
                "--transport", "http-only",
                "--header-file", str(token_file),
            ],
        }  # fmt: skip
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps(settings, indent=2) + "\n")

        self.stdout.write(
            f"Connected Claude Desktop to {owner.username}'s Memento.\n"
            "Now quit Claude Desktop (Cmd-Q), open it again, and ask: "
            "What's in my Memento inbox?"
        )


def _owner(username):
    users = get_user_model().objects
    if username:
        owner = users.filter(username=username).first()
        if owner is None:
            raise CommandError(f"No user {username!r}. Create one with: make superuser")
        return owner
    owner = users.filter(is_superuser=True).order_by("pk").first()
    if owner is None:
        raise CommandError("No Memento user yet. Create one first with: make superuser")
    return owner


def _read(config: Path) -> dict:
    if not config.exists() or not config.read_text().strip():
        return {}
    try:
        settings = json.loads(config.read_text())
    except json.JSONDecodeError as e:
        raise CommandError(
            f"{config} isn't valid JSON ({e}). Fix or move it, then run this again. "
            "Nothing was changed."
        ) from e
    if not isinstance(settings, dict):
        raise CommandError(f"{config} should hold a JSON object. Nothing was changed.")
    return settings
