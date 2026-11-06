SHELL := /bin/sh
COMPOSE := docker compose
PROD_COMPOSE := docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml
MONITOR_COMPOSE := $(PROD_COMPOSE) -f docker-compose.monitoring.yml

.PHONY: help setup up observability down build logs ps migrate seed test test-api test-web lint format smoke readiness simulate-update backup clean production-check production-up release-audit load-smoke monitoring-up prepare-server

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
	@echo "  make production-check Validate server configuration without starting services"
	@echo "  make production-up    Validate, build and start the HTTPS server stack"
	@echo "  make prepare-server   Create private server configuration without overwriting"
	@echo "  make release-audit    Audit configured RELEASE_URL and published decision data"
	@echo "  make load-smoke       Run bounded read-only checks against RELEASE_URL"
	@echo "  make monitoring-up    Validate private alert receiver and start monitoring"

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
	$(COMPOSE) logs -f --tail=200 api worker dispatcher beat web

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
	docker build --target builder -t heatshift-web-tests -f infra/docker/web.Dockerfile .
	docker run --rm heatshift-web-tests npm test

lint:
	$(COMPOSE) run --rm api python -m compileall -q app tests
	docker build --target builder -t heatshift-web-tests -f infra/docker/web.Dockerfile .
	docker run --rm heatshift-web-tests npx tsc --noEmit

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

production-check:
	$(PROD_COMPOSE) config --format json | python3 scripts/check_compose_config.py

production-up: production-check
	$(PROD_COMPOSE) up --build -d --wait

release-audit:
	python3 scripts/verify_release.py --url $(RELEASE_URL) --output ./data/exports/release-audit.json

load-smoke:
	python3 scripts/load_smoke.py --url $(RELEASE_URL) --output ./data/exports/load-smoke.json

monitoring-up:
	python3 scripts/check_alerting_config.py
	$(MONITOR_COMPOSE) run --rm --no-deps --entrypoint /bin/amtool alertmanager check-config /run/secrets/alertmanager_config
	$(MONITOR_COMPOSE) up -d prometheus alertmanager

prepare-server:
	python3 scripts/init_production_env.py

clean:
	$(COMPOSE) down --remove-orphans
