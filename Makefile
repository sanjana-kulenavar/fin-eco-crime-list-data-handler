.PHONY: install lint test demo all dash help

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest --cov=slo --cov-report=term --cov-fail-under=80

demo:
	uv run slo offline-demo

all:
	uv run slo all

dash:
	uv run streamlit run app/dashboard.py

help:
	@echo "install  uv sync"
	@echo "lint     ruff check + format check"
	@echo "test     pytest with coverage gate (80%)"
	@echo "demo     whole pipeline on fixtures, offline"
	@echo "all      live pipeline (downloads the lists)"
	@echo "dash     Streamlit dashboard"
