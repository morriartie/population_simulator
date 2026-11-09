# popsim: container shortcuts (see docker-compose.yml)
#   make build   build the images (creates uv.lock in a container if it is missing)
#   make start   (re)build if needed, then start the market API and the dashboard
#   make stop    stop and remove the containers (API snapshots are kept)
#   make clean   stop, then remove images, the API data volume and Python caches
#                (runs/ and scenarios/ are never touched)

COMPOSE ?= docker compose
DASHBOARD_PORT ?= $(or $(POPSIM_DASHBOARD_HOST_PORT),8000)
API_PORT ?= $(or $(POPSIM_HOST_PORT),8080)

.DEFAULT_GOAL := help
.PHONY: help build start stop clean

help:
	@echo "make build   build the images"
	@echo "make start   start the market API and dashboard (http://localhost:$(DASHBOARD_PORT))"
	@echo "make stop    stop the containers"
	@echo "make clean   remove containers, images, the API data volume and caches"

# uv.lock is committed; this only runs if it is missing, and uses a container (no host uv).
uv.lock:
	docker run --rm -v "$(CURDIR)":/work -w /work ghcr.io/astral-sh/uv:python3.12-bookworm-slim uv lock

build: uv.lock
	$(COMPOSE) build

start:
	@$(COMPOSE) up -d --build --wait api dashboard || \
		{ echo ""; echo "Startup failed. Last dashboard log lines:"; \
		  $(COMPOSE) logs --tail=30 dashboard; exit 1; }
	@echo ""
	@echo "Dashboard:  http://localhost:$(DASHBOARD_PORT)"
	@echo "Market API: http://localhost:$(API_PORT)"

stop:
	$(COMPOSE) down --remove-orphans

clean:
	$(COMPOSE) --profile batch --profile test down --volumes --rmi all --remove-orphans
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache
