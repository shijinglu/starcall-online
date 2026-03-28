.PHONY: install run test test-unit test-component test-integration lint clean

VENV := .venv
UV := uv

## install — create venv and install all dependencies (inc. dev)
install:
	$(UV) venv $(VENV)
	$(UV) pip install -e ".[dev]"

## run — start the FastAPI server on port 8000
run:
	$(UV) run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

## test — run all tests (unit + component + integration)
test:
	$(UV) run pytest tests/ -v

## test-unit — run unit tests only
test-unit:
	$(UV) run pytest tests/unit/ -v

## test-component — run L3 component tests (requires API keys in .env)
test-component:
	$(UV) run pytest tests/component/ -v -x

## test-integration — run L2 integration tests (requires running backend)
test-integration:
	RUN_INTEGRATION_TESTS=1 $(UV) run pytest tests/integration/ -v -x

## lint — run ruff linter (if installed)
lint:
	$(UV) run ruff check app/ tests/

## clean — remove build artifacts and caches
clean:
	rm -rf $(VENV) .pytest_cache __pycache__ app/__pycache__ app/**/__pycache__ tests/**/__pycache__ .ruff_cache *.egg-info
