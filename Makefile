.PHONY: install test lint run clean docker-build docker-run help

PYTHON := python3
PIP := pip

help:
	@echo "Proxy Guard - Developer Commands"
	@echo "--------------------------------"
	@echo "make install      Install dependencies (prod + dev)"
	@echo "make test         Run unit tests"
	@echo "make lint         Run linters (black, isort, mypy)"
	@echo "make run          Run the proxy server locally"
	@echo "make docker-build Build the docker image"
	@echo "make docker-run   Run via docker-compose"
	@echo "make clean        Remove build artifacts"

install:
	$(PIP) install -e .[dev]

test:
	pytest -v

lint:
	black src tests
	isort src tests
	mypy src
	flake8 src tests

run:
	$(PYTHON) main.py

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache
	find . -name "__pycache__" -exec rm -rf {} +
	find . -name "*.pyc" -exec rm -f {} +

docker-build:
	docker build -t proxy-guard:latest .

docker-run:
	docker compose up --build
