# Common tasks. Everything runs through uv so the locked environment is always used.
include $(wildcard .env)
export

.PHONY: setup db migrate run serve test lint fmt check superuser

setup:        ## Install dependencies and git hooks
	uv sync
	uv run pre-commit install

db:           ## Start local Postgres and wait until it's ready
	docker compose up -d --wait db

migrate:      ## Apply migrations
	uv run python manage.py migrate

run:          ## Development server on http://localhost:8000
	uv run python manage.py runserver

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

superuser:    ## Create an admin user
	uv run python manage.py createsuperuser
