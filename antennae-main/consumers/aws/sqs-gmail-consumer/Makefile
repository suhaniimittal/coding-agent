# Makefile for SQS Gmail Consumer

.PHONY: help install test test-watch test-cov clean lint format type-check

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies
	uv sync

test: ## Run all tests
	uv run pytest

test-watch: ## Run tests in watch mode
	uv run pytest-watch --runner "uv run pytest"

test-cov: ## Run tests with coverage
	uv run pytest --cov=src --cov-report=html --cov-report=term

test-unit: ## Run only unit tests
	uv run pytest -m unit

test-integration: ## Run only integration tests
	uv run pytest -m integration

test-fast: ## Run tests excluding slow ones
	uv run pytest -m "not slow"

test-verbose: ## Run tests with verbose output
	uv run pytest -v -s

clean: ## Clean up generated files
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	rm -rf __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

lint: ## Run linting
	uv run ruff check src tests

lint-fix: ## Run linting with auto-fix
	uv run ruff check --fix src tests

format: ## Format code
	uv run black src tests

format-check: ## Check code formatting
	uv run black --check src tests

type-check: ## Run type checking (if mypy is added)
	@echo "Type checking not configured yet"

ci: clean lint format-check test-cov ## Run all CI checks

dev-setup: install ## Set up development environment
	@echo "Development environment ready!"
	@echo "Run 'make test' to run tests"
	@echo "Run 'make test-cov' to run tests with coverage"

# Docker commands
docker-build: ## Build Docker image
	docker build --secret id=codeartifact_token,env=CODEARTIFACT_AUTH_TOKEN -t sqs-gmail-consumer:latest .

docker-test: docker-build ## Run tests in Docker
	docker run --rm sqs-gmail-consumer:latest uv run pytest
