.PHONY: install demo serve test lint clean

install:
	python -m pip install -e ".[server,dev]"

demo:
	python -m grace.demo --all

serve:
	GRACE_DB=grace.db python -m grace.cli serve --port 8020

test:
	pytest

lint:
	ruff check .
	ruff format --check .

clean:
	rm -f grace.db && find . -name __pycache__ -type d -exec rm -rf {} +
