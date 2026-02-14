PYTHON ?= python3

.PHONY: install run init-db test

install:
	$(PYTHON) -m pip install -e .[dev]

run:
	uvicorn control_plane.main:app --host 0.0.0.0 --port 8000 --reload

init-db:
	$(PYTHON) -m control_plane.scripts.init_db

test:
	pytest -q
