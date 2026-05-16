PYTHON ?= python3
PROJECT_ROOT := $(CURDIR)
SOURCE_ROOT := $(PROJECT_ROOT)/src
export PYTHONPATH := $(SOURCE_ROOT):$(PYTHONPATH)

.PHONY: install-dev compile test lint typecheck format check

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

compile:
	$(PYTHON) -m compileall src

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

format:
	$(PYTHON) -m ruff format .

check: compile test lint typecheck
