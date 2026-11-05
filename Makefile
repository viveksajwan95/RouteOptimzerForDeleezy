.PHONY: install dev run test lint format demo demo-zones

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

run:
	uvicorn deleezy_optimizer.main:app --reload

test:
	pytest

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .
	ruff check --fix .

demo:
	python -m deleezy_optimizer.demo routes

demo-zones:
	python -m deleezy_optimizer.demo zones
