SHELL := /bin/sh
COMPOSE := docker compose

.PHONY: help setup up observability down build logs ps migrate seed test test-api test-web lint format smoke readiness simulate-update backup clean

help:
	@echo "HEAT//SHIFT commands"
	@echo "  make setup            Copy .env.example to .env (never overwrites)"
	@echo "  make up               Build and start the complete platform"
	@echo "  make test             Run API and web tests"
	@echo "  make observability    Start the stack with Prometheus on port 9090"
	@echo "  make backup           Create a checksum-protected local Postgres backup"
	@echo "  make simulate-update  Run the authorized local fixture ingestion"
	@echo "  make smoke            Check API, datasets and web endpoints"
	@echo "  make readiness        Audit production configuration and live dependencies"

setup:
	@test -f .env || cp .env.example .env
	@echo "Edit .env and replace CHANGE_ME values before non-local use."

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up --build -d

observability:
	$(COMPOSE) --profile observability up --build -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200 api worker beat web

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) run --rm migrate

seed:
	$(COMPOSE) run --rm -e DEMO_MODE=true api python -c "from app.db.session import SessionLocal; from app.services.bootstrap import ensure_sources, seed_demo_data; db=SessionLocal(); ensure_sources(db); seed_demo_data(db); db.close()"

test: test-api test-web

test-api:
	$(COMPOSE) run --rm -e TEST_DATABASE_URL=sqlite+pysqlite:///:memory: api pytest -q

test-web:
	$(COMPOSE) run --rm web npm test

lint:
	$(COMPOSE) run --rm api python -m compileall -q app tests
	$(COMPOSE) run --rm web npx tsc --noEmit

format:
	@echo "No automatic formatter is configured; use project-local tooling before enabling this target."

smoke:
	python3 scripts/smoke_test.py

readiness:
	python3 scripts/check_readiness.py

simulate-update:
	ALLOW_FIXTURE_INGESTION=true python3 scripts/simulate_provider_update.py

backup:
	./scripts/backup_postgres.sh

clean:
	$(COMPOSE) down --remove-orphans
