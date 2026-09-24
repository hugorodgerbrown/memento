# Deploying to Render

Staging is described entirely by [`render.yaml`](../render.yaml), so the service and
its database can be recreated from the repository. This is milestone M1 of the
[build plan](build-plan.md).

## What the blueprint creates

| Resource | Plan | Notes |
|---|---|---|
| `memento-staging` web service | `0.5c-512mb`, Frankfurt | Auto-deploys from `main` |
| `memento-staging-db` Postgres | `0.1c-256mb`, Frankfurt, major version 16 | No external access |

Both sit in the same region, so the service reaches the database over Render's
private network rather than the public internet.

## First run

1. In Render: **New > Blueprint**, pick this repository, apply. Render reads
   `render.yaml` and creates both resources.
2. Wait for the first deploy. It will be green once `/healthz` answers `ok`.
3. Create your login. Open a shell on the service (**Shell** tab) and run:

   ```bash
   uv run python manage.py createsuperuser
   ```

4. Visit `https://memento-staging.onrender.com/admin/` and log in.

`POCKET_WEBHOOK_SECRET` is deliberately left empty (`sync: false` in the
blueprint, so it is never in git). Until it is set in the dashboard at M4, every
Pocket delivery is rejected with a 401 — which is the correct answer for an
unsigned request.

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

## Why ASGI now

M2 mounts the MCP server's streamable HTTP endpoint at `/mcp`, which needs ASGI.
Switching the process model at M1 means M2 changes the application, not the
deploy. Gunicorn supervises the workers and handles graceful restarts; uvicorn
workers do the serving.

To reproduce the production stack locally:

```bash
uv run python manage.py collectstatic --no-input && make serve
```

`CompressedManifestStaticFilesStorage` is only used when `DJANGO_DEBUG` is off,
and it refuses to serve a file that is not in the manifest, so `collectstatic`
has to have run first. That is why it is a build step and not a deploy step.

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

`ipAllowList: []` means the database accepts no external connections. For a
psql session, open a shell on the web service and run:

```bash
uv run python manage.py dbshell
```

Widening the allow list is a deliberate act. The brief is explicit that the
operator can read the data; that is not a reason to let anyone else try.

## The distiller (M7)

`memento-distiller` is a Render cron job, every 15 minutes, that turns the inbox into entries (ADR 0019). It is a separate client: it has its own environment (`distiller/`), its own token, and the model provider's key, none of which the web service has. After the web service is up:

1. On the web service's **Shell** tab, create the distiller's token. The default scopes are read and write; it never gets forget.

   ```bash
   uv run python manage.py create_client <your username> distiller
   ```

2. In the cron job's **Environment**, set `MEMENTO_URL` to `https://memento-staging.onrender.com/mcp`, and set `MEMENTO_TOKEN` and `ANTHROPIC_API_KEY`. `DISTILLER_MODEL` is `claude-sonnet-5` in the blueprint.
3. Trigger a run from the dashboard and read its log. There is one line per note (processed, dismissed or left, and why), with token counts.

After an outage, run it once by hand over the missed period: `uv run python distil.py --since 2026-09-24T06:00:00+01:00`.

## Known log noise

WhiteNoise serves static files through a synchronous iterator, so Django logs
`StreamingHttpResponse must consume synchronous iterators in order to serve them
asynchronously` on each static file under ASGI. It is harmless — the files are
served correctly — and only affects the admin, which is the only thing serving
static files at all.

## Not yet done

- A custom domain. Until then the service is on its `onrender.com` subdomain,
  and `DJANGO_CSRF_TRUSTED_ORIGINS` stays empty.
- Production. This is staging, and it is the only environment until the MCP
  server (M2) and OAuth (M8) exist.
- Backups beyond Render's own. Worth revisiting before real capture starts at M5,
  since by then the data is genuinely unrecoverable if lost.
