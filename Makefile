SHELL := /bin/sh
COMPOSE := docker compose
.PHONY: help install up down restart logs ps migrate seed test test-health shell-db swagger clean
help:
	@printf '%s\n' 'up: start services' 'down: stop services' 'restart: rebuild API' 'logs: follow logs' 'ps: show status' 'migrate: apply migrations' 'seed: populate demo data' 'test: run full suite' 'shell-db: open psql' 'swagger: print Swagger URL' 'clean: remove containers and database volume'
install:
	@test -f .env || cp .env.example .env
	@printf '%s\n' 'Configured .env; review it before starting.'
up:
	$(COMPOSE) up -d --build
down:
	$(COMPOSE) down
restart:
	$(COMPOSE) up -d --build --force-recreate api
logs:
	$(COMPOSE) logs -f api migrate
ps:
	$(COMPOSE) ps -a
migrate:
	$(COMPOSE) run --rm --build migrate
seed:
	$(COMPOSE) run --rm --build api python scripts/seed_demo.py
test:
	$(COMPOSE) --profile test run --rm --build test
test-health:
	$(COMPOSE) --profile test run --rm --build test python -m pytest -q tests/test_health.py
shell-db:
	$(COMPOSE) exec db psql -U solar_erp -d solar_erp
swagger:
	@printf '%s\n' 'Swagger UI: http://localhost:8000/docs' 'OpenAPI JSON: http://localhost:8000/openapi.json'
clean:
	$(COMPOSE) down -v
