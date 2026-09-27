# Deploying to Render

This is the real Memento (ADR 0024): the one that holds real memories. The Mac
setup in [`local.md`](local.md) is the development environment.

Everything is described by [`render.yaml`](../render.yaml), so the service and its
database can be recreated from the repository. This is milestone M1 of the
[build plan](build-plan.md).

## What the blueprint creates

| Resource | Plan | Notes |
|---|---|---|
| `memento` web service | `0.5c-512mb`, Frankfurt | Auto-deploys from `main` |
| `memento-db` Postgres | `0.1c-256mb`, Frankfurt, major version 16 | No external access |
| `memento-distiller` cron | `starter`, Frankfurt | Every 15 minutes (M7, 0019) |

All three sit in the same region, so the service reaches the database over
Render's private network rather than the public internet.

## First run

1. In Render: **New > Blueprint**, pick this repository, apply. Render reads
   `render.yaml` and creates all three resources. The cron job will fail until
   step 5 gives it an environment; that is expected.
2. Wait for the web service's first deploy. It is green once `/healthz` answers
   `ok`. If it does not, read the pre-deploy log first: a failed migration fails
   the deploy by design.
3. Create your login. Open a shell on the service (**Shell** tab) and run:

   ```bash
   uv run python manage.py createsuperuser
   ```

4. Visit `https://memento.onrender.com/admin/`, log in, and add a **Profile**
   with your time zone (for example `Europe/London`). Every tool result carries
   `now` in that zone (0017), so this is not optional.
5. Give the distiller its environment, as [below](#the-distiller-m7).
6. Connect your clients, as [below](#clients).

`POCKET_WEBHOOK_SECRET` is deliberately unset. Pocket is not connected to the
real Memento yet (0024): its first dry run against the real account has not
happened. Until the secret is set, every delivery to `/ingest/pocket/` is
rejected with a 401, which is the correct answer for an unsigned request.

## How a deploy runs

```
build      pip install uv && uv sync --locked && collectstatic
pre-deploy migrate            ← separate instance, old version still serving
start      gunicorn config.asgi:application -k uvicorn_worker.UvicornWorker
```

Migrations run as their own step rather than inside the start command, so a
failed migration fails the deploy and the previous version keeps serving.
`preDeployCommand` is why the service is on a paid plan: it is not available on
the free plan, which would force `migrate` into the start command, where it
would run once per worker boot and a failure would take the site down.

## Why ASGI

M2 mounts the MCP server's streamable HTTP endpoint at `/mcp`, which needs ASGI.
Gunicorn supervises the workers and handles graceful restarts; uvicorn workers do
the serving. To reproduce this stack locally:

```bash
uv run python manage.py collectstatic --no-input && make serve
```

`CompressedManifestStaticFilesStorage` is only used when `DJANGO_DEBUG` is off,
and it refuses to serve a file that is not in the manifest, so `collectstatic`
has to have run first. That is why it is a build step and not a deploy step.

## Clients

Each client gets its own bearer token, so provenance records which one wrote an
entry and `forget` can be withheld by scope (0005, 0016). On the service's
**Shell** tab:

```bash
uv run python manage.py create_client <your username> claude-desktop
uv run python manage.py create_client <your username> claude-code
```

Each prints its token once. Point the client at `https://memento.onrender.com/mcp`
with `Authorization: Bearer <token>`.

Claude Desktop's own **Custom connectors** setting needs OAuth, not a bearer
token, so until M8 it connects through `mcp-remote` with the header. Note that
`make connect-desktop` configures the *local* stdio server (0023) and is not what
you want here.

claude.ai and ChatGPT cannot use a bearer token at all. They need OAuth 2.1,
which is M8. **Decide the hostname before starting M8:** an OAuth issuer is a URL
that registered clients remember, so moving to a custom domain afterwards means
re-registering them.

## Settings that exist because of the host

Three things in `config/settings.py` are there for this deployment specifically,
and are covered by `DeploySettingsTests` in `memories/tests/test_ops.py`:

- **`RENDER_EXTERNAL_HOSTNAME`** is appended to `ALLOWED_HOSTS` and, as an
  origin, to `CSRF_TRUSTED_ORIGINS`. The hostname does not exist until the
  service does, so it cannot be written into the blueprint. Render addresses
  health checks to this hostname too, so without it they would 400.
- **`SECURE_PROXY_SSL_HEADER`** trusts `X-Forwarded-Proto`, because TLS
  terminates at Render's load balancer.
- **`SECURE_REDIRECT_EXEMPT = [r"^healthz$"]`**. Render counts any 3xx as
  healthy, so without the exemption `SECURE_SSL_REDIRECT` would answer the
  health check with a 301 and the service would report healthy with a dead
  database. The point of `/healthz` is that it runs `SELECT 1`.

## Database access

`ipAllowList: []` means the database accepts no external connections. For a psql
session, open a shell on the web service and run:

```bash
uv run python manage.py dbshell
```

Widening the allow list is a deliberate act. The brief is explicit that the
operator can read the data; that is not a reason to let anyone else try.

## Backups

Render's automatic backups of `memento-db` are the baseline, and they are the
reason this is no longer the single point of failure the Mac was. Keep one copy
that is not Render's as well.

Because the database takes no external connections, an off-Render dump means
opening the allow list for as long as it takes:

1. In the database's **Connections** settings, add your current IP address.
2. Take the dump, using the external connection string Render shows. The Postgres
   16 client in the development container does the work, so nothing needs
   installing:

   ```bash
   make backup REMOTE_URL='<the external connection string>'
   ```

3. Remove your IP address again.

`make restore FILE=... CONFIRM=yes` still restores into the *development*
database, which is where you would want to test a dump. Restoring into the real
Memento is deliberately not a `make` target.

**Backups are the one place forgetting does not reach.** `forget` hard-deletes
from the live database (Principle 5), but not from a dump taken earlier and not
from Render's automatic backups. Restoring any backup brings back everything
forgotten since it was taken, and drops the tombstones that recorded it.

## The distiller (M7)

`memento-distiller` is a Render cron job, every 15 minutes, that turns the inbox
into entries (0019). It is a separate client: its own environment, its own token,
and the model provider's key, none of which the web service has — the web service
refuses to start if it finds a model key at all (Principle 2, 0013).

1. On the web service's **Shell** tab, create the distiller's token. The default
   scopes are read and write; it never gets forget.

   ```bash
   uv run python manage.py create_client <your username> distiller
   ```

2. In the cron job's **Environment**, set `MEMENTO_URL` to
   `https://memento.onrender.com/mcp`, and set `MEMENTO_TOKEN` and
   `ANTHROPIC_API_KEY`. `DISTILLER_MODEL` is `claude-sonnet-5` in the blueprint.
3. Trigger a run from the dashboard and read its log. There is one line per note
   (processed, dismissed or left, and why), with token counts.

After an outage, run it once by hand over the missed period, from a shell on the
cron job: `uv run python distil.py --since 2026-09-24T06:00:00+01:00`.

## Known log noise

WhiteNoise serves static files through a synchronous iterator, so Django logs
`StreamingHttpResponse must consume synchronous iterators in order to serve them
asynchronously` on each static file under ASGI. It is harmless — the files are
served correctly — and only affects the admin, which is the only thing serving
static files at all.

## Verify on first run

In the repository's habit of not trusting what has not been seen:

- **The dump path.** `make backup REMOTE_URL=...` over a briefly opened allow
  list has not been run against Render. Confirm it before relying on it as the
  off-Render copy.
- **Render's backup retention** on the `0.1c-256mb` plan, and whether it is
  enough. Check what the dashboard actually offers.
- **Two workers in 512MB**, once `/mcp` is serving real traffic.

## Not yet done

- A custom domain. Until then the service is on its `onrender.com` subdomain and
  `DJANGO_CSRF_TRUSTED_ORIGINS` stays empty. This blocks nothing except M8, where
  it should be settled first.
- Pocket (0024). Either point its webhook at `/ingest/pocket/` and set
  `POCKET_WEBHOOK_SECRET`, or add the pull as a second cron job with
  `POCKET_API_KEY`. Until then, Pocket recordings reach nothing.
- OAuth (M8), and with it claude.ai and ChatGPT.
