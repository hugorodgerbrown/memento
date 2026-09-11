import json

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import pocket


@csrf_exempt
@require_POST
def pocket_webhook(request):
    # Verify against the exact bytes received, before parsing anything.
    if not pocket.verify_signature(
        settings.POCKET_WEBHOOK_SECRET,
        request.headers.get("X-HeyPocket-Timestamp", ""),
        request.body,
        request.headers.get("X-HeyPocket-Signature", ""),
    ):
        return HttpResponse(status=401)
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest("invalid JSON")
    decision = pocket.ingest(payload)
    # 200 for every verified delivery, including skips, so Pocket doesn't retry them.
    return HttpResponse(decision, status=200)


def healthz(request):
    """For the host's health check: the app is up and the database answers."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")
