# Shortcuts. `make <name>` runs the command under it.
# Mac/Linux/WSL have `make` already. Windows (not WSL): copy the command and run it directly.

COMPOSE = docker compose -f docker-compose.dev.yml
LOCUST  = $(COMPOSE) run --rm --service-ports locust
RESULTS = /results

.PHONY: up down logs ps normal spike failure sick mttr results compare ai-normal ai-eval ai-watch locust-ui test lint dashboard

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

sick:          ## scenario 4: app-3 fails 30% of requests from 60s to 120s but says "ready"
	$(COMPOSE) run --rm -e CHAOS_TARGET=http://app-3:8000 -e CHAOS_SERVER_ID=app-3 locust \
		-f scenario_sick.py --headless --host http://gateway:8000 --csv $(RESULTS)/sick --only-summary

mttr:          ## Week 4: MTTR report from the last failure run (also saves a CSV)
	python experiments/mttr.py experiments/results/raw/events.jsonl \
		--csv experiments/results/raw/mttr.csv

# ---- Everything at once (Week 5 + 7 + 8–9). About 25 min; SCENARIO_SCALE=0.3 for a quick try ----
results:       ## Mode A vs B baseline, train + score the AI, save every result file
	bash experiments/run_all.sh

compare:       ## Week 5: rebuild the Mode A vs Mode B table from runs already saved
	python experiments/compare_modes.py experiments/results/raw --out experiments/results/mode-baseline.md

# ---- Week 8–9: AI early warning (needs `make up`; Prometheus at localhost:9090) ----
RAW = experiments/results/raw

ai-normal:     ## run the normal scenario, then teach the AI what "normal" looks like
	$(MAKE) normal
	python -m ai.anomaly collect --minutes 3.5 --out $(RAW)/normal_metrics.csv
	python -m ai.anomaly train $(RAW)/normal_metrics.csv

ai-eval:       ## run the sick scenario, score both detectors, build the HTML report
	$(MAKE) sick
	python -m ai.anomaly collect --minutes 3.5 --out $(RAW)/sick_metrics.csv
	python -m ai.anomaly evaluate $(RAW)/sick_metrics.csv $(RAW)/events.jsonl \
		--json $(RAW)/sick_eval.json
	python -m ai.report $(RAW)/ai-report-sick.html sick=$(RAW)/sick_eval.json

ai-watch:      ## live early warning: writes anomaly_detected to events.jsonl
	EVENTS_FILE=$(RAW)/events.jsonl python -m ai.anomaly watch

locust-ui:     ## open the Locust web UI at http://localhost:8089 (pick users yourself)
	$(LOCUST) -f scenario_normal.py --host http://gateway:8000

test:          ## run unit tests
	pytest -q

lint:          ## check + format code
	ruff check . --fix && ruff format .

dashboard:     ## regenerate the Grafana dashboard JSON after editing build_dashboard.py
	python monitoring/grafana/build_dashboard.py
