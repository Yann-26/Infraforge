# ============================================================================
# InfraForge — AI agents & code quality targets
#
# Include from the main Makefile:   include agents.mk
# Or run directly:                  make -f agents.mk agents-test
# ============================================================================

PYTHON      ?= python3
AGENT_PATHS := agents quality tests/agents tests/agent_eval
LINE_LENGTH := 100

.PHONY: agents-install agents-lint agents-format agents-test agents-eval agents-eval-llm \
        agents-investigate-local agents-review-local quality agents-up

agents-install: ## Install agent + quality-check dependencies
	$(PYTHON) -m pip install -r agents/requirements-dev.txt

agents-lint: ## Lint agent code (ruff, black, isort)
	$(PYTHON) -m ruff check --line-length $(LINE_LENGTH) $(AGENT_PATHS)
	$(PYTHON) -m black --line-length $(LINE_LENGTH) --check $(AGENT_PATHS)
	$(PYTHON) -m isort --profile black --line-length $(LINE_LENGTH) --check-only $(AGENT_PATHS)

agents-format: ## Auto-format agent code
	$(PYTHON) -m isort --profile black --line-length $(LINE_LENGTH) $(AGENT_PATHS)
	$(PYTHON) -m black --line-length $(LINE_LENGTH) $(AGENT_PATHS)

agents-test: ## Run agent unit tests (redaction, validation, fallback, ...)
	$(PYTHON) -m pytest tests/agents -q

agents-eval: ## Run the evaluation set offline (heuristic baseline, no API cost)
	$(PYTHON) -m tests.agent_eval.run_eval --mode heuristic

agents-eval-llm: ## Run the evaluation set against Claude (needs ANTHROPIC_API_KEY, costs money)
	$(PYTHON) -m tests.agent_eval.run_eval --mode llm

agents-investigate-local: ## Investigate a local log: make agents-investigate-local LOG=job.log DIFF=mr.diff
	$(PYTHON) -m agents investigate --log-file $(LOG) $(if $(DIFF),--diff-file $(DIFF)) --dry-run

agents-review-local: ## Review a local diff: make agents-review-local DIFF=mr.diff
	$(PYTHON) -m agents review --diff-file $(DIFF) --dry-run

quality: ## Run code quality checks (complexity, size, cycles, secrets) — see quality/quality.yml
	$(PYTHON) quality/run_quality.py

agents-up: ## Start Pushgateway (+ Prometheus/Grafana) for agent metrics
	docker compose -f docker-compose.yml -f docker-compose.agents.yml up -d pushgateway prometheus grafana
	@echo "   Pushgateway: http://localhost:9091"
