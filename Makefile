# RetailMind AI
#
# Default profile needs nothing but Python: SQLite, in-process retrieval, a
# deterministic mock LLM. `make setup && make dev` and you have a running
# platform. Docker and Ollama are opt-in.

PY  ?= .venv/bin/python
PIP ?= .venv/bin/pip
PORT ?= 8000

.DEFAULT_GOAL := help

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "};{printf "  \033[1m%-18s\033[0m %s\n",$$1,$$2}'

.venv:
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip

setup: .venv ## Create the venv and install everything
	$(PIP) install -q -e ".[test]"
	@test -f .env || cp .env.example .env
	@echo "installed. next: make bootstrap"

bootstrap: ## Create tables, seed business data, ingest the knowledge base
	$(PY) scripts/bootstrap.py

dev: ## Run the API + React shop on http://localhost:$(PORT)
	.venv/bin/uvicorn app.main:app --app-dir backend --reload --port $(PORT)

frontend-dev: ## Vite React shop (proxies /api → :8000)
	cd frontend && npm run dev

frontend-build: ## Build React shop into backend/static/shop
	cd frontend && npm run build

reseed: ## Wipe DB and reseed (30+ products per electronics category)
	rm -f var/retailmind.db
	$(PY) scripts/bootstrap.py

# ---------------------------------------------------------------- tests
test: ## Full suite
	$(PY) -m pytest

test-fast: ## Everything that must pass on every commit (no live models)
	$(PY) -m pytest -m "unit or api or integration or conformance or agents or rag"

test-security: ## Authorization, isolation, and the red-team corpus
	$(PY) -m pytest -m "security or adversarial" -v

test-eval: ## Tests of the evaluation framework itself
	$(PY) -m pytest -m evaluation -v

test-perf: ## Latency and concurrency
	$(PY) -m pytest -m performance -v

cov: ## Coverage report
	$(PY) -m pytest --cov=backend/app --cov-report=term-missing

# ------------------------------------------------------------ evaluation
eval-rag: ## Retrieval + grounding quality
	$(PY) scripts/run_eval.py rag/policy_qa --metric retrieval_recall@5

eval-agents: ## Trajectories, completion, cost
	$(PY) scripts/run_eval.py agents/task_trajectories --suite agents

eval-safety: ## Attack corpus, against a deliberately compromised model
	$(PY) scripts/run_eval.py adversarial/attack_corpus --suite safety --provider mock-naive

eval-benign: ## False-positive rate on ordinary customer messages
	$(PY) scripts/run_eval.py safety/benign_corpus --suite benign

eval-golden: ## End-to-end regression set
	$(PY) scripts/run_eval.py golden/regression --suite golden

eval-all: eval-rag eval-agents eval-safety eval-benign eval-golden ## Every dataset

compare-prompts: ## v2 vs v1 on the golden set, paired, with a confidence interval
	$(PY) scripts/run_eval.py rag/policy_qa --prompt-version v2 --compare v1 \
	  --metric lexical_groundedness

redteam: ## Full red-team report with severities and reproductions
	$(PY) scripts/redteam.py

gates: ## The CI gate: fails the build when a merge gate is breached
	$(PY) scripts/run_eval.py golden/regression --suite golden
	$(PY) scripts/run_eval.py adversarial/attack_corpus --suite safety --provider mock-naive
	$(PY) scripts/run_eval.py safety/benign_corpus --suite benign

# --------------------------------------------------------------- deploy
docker-build: ## Build the deployable image
	docker build -t retailmind:local .

docker-run: ## Run the image exactly as a cloud host would
	docker run --rm -p 8000:8000 -e PORT=8000 retailmind:local

docker-smoke: ## Build, run, and assert the container self-seeds and answers
	docker build -q -t retailmind:smoke .
	docker rm -f retailmind-smoke 2>/dev/null || true
	docker run -d --name retailmind-smoke -p 8899:8899 -e PORT=8899 retailmind:smoke
	@for i in $$(seq 1 30); do curl -sf localhost:8899/health >/dev/null && break || sleep 2; done
	@curl -sf localhost:8899/ready | grep -q '"ready":true' && echo "ready: OK" || (echo "ready: FAIL"; exit 1)
	@curl -sf -X POST localhost:8899/api/chat -H 'Content-Type: application/json' \
	   -H 'Authorization: Bearer customer:CU-1001' \
	   -d '{"message":"What is the return window for electronics?"}' \
	   | grep -q '10 days' && echo "chat: OK" || (echo "chat: FAIL"; exit 1)
	docker rm -f retailmind-smoke

tunnel: ## Share your local server on a temporary public URL (needs cloudflared)
	@echo "Start 'make dev' in another terminal first."
	cloudflared tunnel --url http://localhost:$(PORT)

# --------------------------------------------------------------- docker
up: ## Full infrastructure profile (Postgres, Redis, OpenSearch, Grafana)
	docker compose -f infrastructure/docker-compose.yml --profile core up -d

up-obs: ## Add the observability stack
	docker compose -f infrastructure/docker-compose.yml --profile core --profile obs up -d

down:
	docker compose -f infrastructure/docker-compose.yml down -v

clean: ## Remove the local database and caches
	rm -rf var .pytest_cache .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

.PHONY: help setup bootstrap dev test test-fast test-security test-eval test-perf cov \
        eval-rag eval-agents eval-safety eval-benign eval-golden eval-all \
        compare-prompts redteam gates docker-build docker-run docker-smoke tunnel \
        up up-obs down clean
