# Common tasks. Everything runs through uv so the locked environment is always used.
include $(wildcard .env)
export

.PHONY: setup db migrate run serve test lint fmt check superuser client eval distil distil-eval \
	local-server backup restore launchd-install launchd-uninstall launchd-status

setup:        ## Install dependencies and git hooks
	uv sync
	uv run pre-commit install

db:           ## Start local Postgres and wait until it's ready
	docker compose up -d --wait db

migrate:      ## Apply migrations
	uv run python manage.py migrate

run:          ## Development server on http://localhost:8000, with /mcp (ASGI, reloads on change)
	uv run uvicorn config.asgi:application --reload --port 8000 --timeout-graceful-shutdown 3

serve:        ## ASGI, exactly as Render runs it. Needs collectstatic first.
	uv run gunicorn config.asgi:application \
	  --worker-class uvicorn_worker.UvicornWorker \
	  --workers 2 --bind 127.0.0.1:8000 --access-logfile -

test:         ## Full test suite against Postgres
	uv run python manage.py test

lint:         ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

fmt:          ## Fix lint issues and format
	uv run ruff check --fix .
	uv run ruff format .

check: lint   ## Everything CI runs
	uv run python manage.py makemigrations --check --dry-run
	uv run python manage.py test
	cd distiller && uv sync --locked -q && uv run python -m unittest -q

distil:       ## Run the distiller once over the last 35 minutes of the inbox. Reads distiller/.env (MEMENTO_URL, MEMENTO_TOKEN, ANTHROPIC_API_KEY)
	@test -f distiller/.env || { echo "Create distiller/.env first: see docs/local.md."; exit 2; }
	cd distiller && set -a && . ./.env && set +a && uv run python distil.py $(if $(SINCE),--since $(SINCE))

distil-eval:  ## Capture-policy cases as inbox notes, through the distiller: make distil-eval [RUNS=3] [CASE=id]
	cd distiller && uv run python ../evals/distilling.py --runs $(or $(RUNS),3) $(if $(CASE),--case $(CASE))

superuser:    ## Create an admin user
	uv run python manage.py createsuperuser

eval:         ## Capture evals against Claude Code, on a separate database: make eval [RUNS=3] [CASE=id] [SKILL=1] [MODEL=id]
	uv run python evals/capture.py --runs $(or $(RUNS),3) $(if $(CASE),--case $(CASE)) \
		$(if $(MODEL),--model $(MODEL)) \
		$(if $(SKILL),--skill --label claude-code-skill)

client:       ## Create an MCP client: make client USER=hugo NAME=claude-code [SCOPES=...]
	uv run python manage.py create_client $(USER) $(NAME) $(if $(SCOPES),--scopes $(SCOPES))

# --- One Mac (0020) ---------------------------------------------------------------

BACKUP_DIR ?= $(HOME)/Memento backups
PGCMD ?= docker compose exec -T db
LAUNCHD_JOBS ?= server
LAUNCH_AGENTS := $(HOME)/Library/LaunchAgents
LOG_DIR := $(HOME)/Library/Logs/Memento

local-server: ## Memento as launchd runs it: Postgres up, migrations applied, /mcp on 127.0.0.1:8000
	docker compose up -d --wait db
	uv run python manage.py migrate --no-input
	uv run uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5

# A backup is written to a temporary file, checked, and only then given its name, so a
# failed or interrupted dump never leaves a file that looks usable.
backup:       ## Dump everything to "$(BACKUP_DIR)" (make backup BACKUP_DIR=...)
	@mkdir -p "$(BACKUP_DIR)"
	@out="$(BACKUP_DIR)/memento-$$(date +%Y-%m-%d-%H%M%S).dump"; tmp="$$out.partial"; \
	if $(PGCMD) pg_dump -U memento -Fc memento > "$$tmp" && $(PGCMD) pg_restore --list < "$$tmp" > /dev/null; \
	then mv "$$tmp" "$$out" && echo "Backed up to $$out"; \
	else rm -f "$$tmp"; echo "Backup failed; nothing was written."; exit 1; fi

# One transaction: if anything in the archive fails, nothing in the live database changes.
# Without it, a damaged archive can drop tables (and the immutability triggers) and stop.
restore:      ## Replace the database with a backup: make restore FILE=... CONFIRM=yes
	@test -f "$(FILE)" || { echo "Say which backup: make restore FILE=path/to/memento-....dump CONFIRM=yes"; exit 2; }
	@test "$(CONFIRM)" = yes || { echo "This replaces everything in Memento with $(FILE), including anything forgotten since it was taken. Add CONFIRM=yes to go ahead."; exit 2; }
	@$(PGCMD) pg_restore --list < "$(FILE)" > /dev/null || { echo "$(FILE) isn't a readable backup. Nothing was changed."; exit 1; }
	@$(PGCMD) pg_restore -U memento -d memento --clean --if-exists --no-owner --single-transaction --exit-on-error < "$(FILE)" \
	  || { echo "The restore failed and was rolled back. Memento is as it was."; exit 1; }
	@echo "Restored from $(FILE)"

launchd-install: ## Keep Memento running on this Mac: make launchd-install [LAUNCHD_JOBS="server distiller"]
	@mkdir -p "$(LAUNCH_AGENTS)" "$(LOG_DIR)"
	@for job in $(LAUNCHD_JOBS); do \
	  plist="$(LAUNCH_AGENTS)/com.memento.$$job.plist"; \
	  sed -e "s|@REPO@|$(CURDIR)|g" -e "s|@LOGS@|$(LOG_DIR)|g" -e "s|@PATH@|$$(dirname $$(command -v uv)):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin|g" \
	    ops/launchd/com.memento.$$job.plist > "$$plist"; \
	  launchctl bootout gui/$$(id -u) "$$plist" 2>/dev/null; \
	  launchctl bootstrap gui/$$(id -u) "$$plist" && echo "Started com.memento.$$job (logs in $(LOG_DIR))"; \
	done

launchd-uninstall: ## Stop and remove the launchd jobs: make launchd-uninstall [LAUNCHD_JOBS="server distiller"]
	@for job in $(LAUNCHD_JOBS); do \
	  plist="$(LAUNCH_AGENTS)/com.memento.$$job.plist"; \
	  launchctl bootout gui/$$(id -u) "$$plist" 2>/dev/null; rm -f "$$plist" && echo "Removed com.memento.$$job"; \
	done

launchd-status: ## Which Memento jobs launchd is running, and their last exit codes
	@launchctl list | grep -E "PID|com\.memento" || echo "No Memento jobs loaded."
