"""Register an MCP client for a user and print its bearer token, once."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from memories import services
from memories.models import ClientMode, Scope


class Command(BaseCommand):
    help = "Create an MCP client and print its bearer token. The token is shown only once."

    def add_arguments(self, parser):
        parser.add_argument("username", help="The Memento user whose memory it can reach.")
        parser.add_argument("name", help="Client name, recorded as client_name on every entry.")
        parser.add_argument(
            "--scopes",
            default=f"{Scope.READ},{Scope.WRITE}",
            help=f"Comma-separated: {', '.join(Scope.values)}. Default: read and write.",
        )
        parser.add_argument("--mode", default=ClientMode.DIRECT, choices=ClientMode.values)

    def handle(self, *args, username, name, scopes, mode, **options):
        owner = get_user_model().objects.filter(username=username).first()
        if owner is None:
            raise CommandError(f"No user {username!r}.")
        try:
            client, token = services.create_client(
                owner, name, scopes=[s.strip() for s in scopes.split(",") if s.strip()], mode=mode
            )
        except ValidationError as e:
            raise CommandError(" ".join(e.messages)) from e
        self.stdout.write(f"Client {client.name!r} for {owner}: {', '.join(client.scopes)}")
        self.stdout.write(f"Token (shown once, store it now): {token}")
