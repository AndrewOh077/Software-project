# Shortcuts. `make <name>` runs the command under it.
# Mac/Linux/WSL have `make` already. Windows (not WSL): copy the command and run it directly.

COMPOSE = docker compose -f docker-compose.dev.yml
LOCUST  = $(COMPOSE) run --rm --service-ports locust
RESULTS = /results

.PHONY: up down logs ps normal spike failure locust-ui test lint dashboard

up:            ## start fake apps, stub gateway, Prometheus, Grafana
	$(COMPOSE) up -d --build
	@echo ""
	@echo "Grafana    -> http://localhost:3000"
	@echo "Prometheus -> http://localhost:9090"
	@echo "Gateway    -> http://localhost:8000/work"

down:          ## stop everything
	$(COMPOSE) --profile loadtest down

logs:          ## follow logs of all services
	$(COMPOSE) logs -f

ps:            ## what is running?
	$(COMPOSE) ps

normal:        ## scenario 1: steady 20 users for 3 min
	$(LOCUST) -f scenario_normal.py --headless --host http://gateway:8000 \
		--csv $(RESULTS)/normal --only-summary

spike:         ## scenario 2: 10 -> 150 -> 10 users
	$(LOCUST) -f scenario_spike.py --headless --host http://gateway:8000 \
		--csv $(RESULTS)/spike --only-summary

failure:       ## scenario 3: kill app-2 at 60s, bring it back at 120s
	$(LOCUST) -f scenario_failure.py --headless --host http://gateway:8000 \
		--csv $(RESULTS)/failure --only-summary

locust-ui:     ## open the Locust web UI at http://localhost:8089 (pick users yourself)
	$(LOCUST) -f scenario_normal.py --host http://gateway:8000

test:          ## run unit tests
	pytest -q

lint:          ## check + format code
	ruff check . --fix && ruff format .

dashboard:     ## regenerate the Grafana dashboard JSON after editing build_dashboard.py
	python monitoring/grafana/build_dashboard.py
