"""
Connect Claude Desktop to Memento on this Mac in one step (0023): Claude Desktop
starts Memento itself and talks to it directly. No network, no token. Safe to
run again.
"""

import json
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from .mcp_stdio import local_client, owner

CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"


class Command(BaseCommand):
    help = "Connect Claude Desktop to Memento on this Mac."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Whose memory. Default: the first admin.")
        parser.add_argument("--config", type=Path, default=CONFIG)

    def handle(self, *args, user=None, config, **options):
        person = owner(user)
        current = _read(config)  # before anything changes, so a bad file changes nothing
        local_client(person)

        repo = Path(settings.BASE_DIR)
        uv = shutil.which("uv")
        if uv is None:
            raise CommandError(
                "Can't find uv. Install it (https://docs.astral.sh/uv/), then rerun."
            )
        if config.exists():
            shutil.copy(config, config.with_name(config.name + ".before-memento"))
        current.setdefault("mcpServers", {})["memento"] = {
            "command": uv,
            "args": [
                "--directory", str(repo), "run", "--quiet",
                "--env-file", str(repo / ".env"),
                "python", "manage.py", "mcp_stdio", "--user", person.username,
            ],
        }  # fmt: skip
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps(current, indent=2) + "\n")

        self.stdout.write(
            f"Connected Claude Desktop to {person.username}'s Memento.\n"
            "Now quit Claude Desktop (Cmd-Q), open it again, and ask: "
            "What's in my Memento inbox?"
        )


def _read(config: Path) -> dict:
    if not config.exists() or not config.read_text().strip():
        return {}
    try:
        current = json.loads(config.read_text())
    except json.JSONDecodeError as e:
        raise CommandError(
            f"{config} isn't valid JSON ({e}). Fix or move it, then run this again. "
            "Nothing was changed."
        ) from e
    if not isinstance(current, dict):
        raise CommandError(f"{config} should hold a JSON object. Nothing was changed.")
    return current
