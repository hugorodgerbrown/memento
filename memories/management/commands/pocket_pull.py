"""
Pull recent Pocket recordings into Memento (0021). launchd runs it every 15 minutes
while Memento lives on one Mac (0020); the webhook does the same job once deployed.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from memories import pocket
from memories.models import PocketLink

WINDOW = timedelta(hours=36)  # a night's sleep and then some; repeats are silent


class Command(BaseCommand):
    help = "Pull recent Pocket recordings, by the same rules as the webhook."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Whose Pocket to pull. Default: the only linked one.")
        parser.add_argument(
            "--since", help="Look back to this time (ISO 8601) instead of 36 hours."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Say what would happen; store nothing."
        )

    def handle(self, *args, user=None, since=None, dry_run=False, **options):
        if not settings.POCKET_API_KEY:
            raise CommandError(
                "Set POCKET_API_KEY in .env: create one in Pocket > Settings > Developer > "
                "API Keys."
            )
        links = PocketLink.objects.select_related("owner")
        if user:
            links = links.filter(owner__username=user)
        if links.count() != 1:
            raise CommandError(
                "Link your Pocket account first (admin > Pocket links), or say which with --user."
                if not links.exists()
                else "More than one Pocket link: say which with --user."
            )
        start = _when(since) if since else timezone.now() - WINDOW
        if start is None:
            raise CommandError(f"--since isn't a time I can read: {since!r}. Use ISO 8601.")
        if timezone.is_naive(start):
            start = timezone.make_aware(start)
        api = pocket.PocketAPI(settings.POCKET_API_KEY, settings.POCKET_API_URL)
        try:
            results = pocket.pull(links.get(), api, since=start, dry_run=dry_run)
        except pocket.PocketAPIError as e:
            raise CommandError(str(e)) from e
        verb = "Would have" if dry_run else "Pulled"
        for rec_id, decision in results:
            self.stdout.write(f"{rec_id}: {decision}")
        self.stdout.write(f"{verb} {len(results)} change(s) since {start:%Y-%m-%d %H:%M %Z}.")


def _when(value: str) -> datetime | None:
    """A time, or a bare date meaning its first moment."""
    if parsed := parse_datetime(value):
        return parsed
    day = parse_date(value)
    return datetime.combine(day, datetime.min.time()) if day else None
