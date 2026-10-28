.PHONY: install demo serve test lint clean

install:
	python -m pip install -e ".[server,dev]"

demo:
	python -m warden.demo

serve:
	WARDEN_DB=warden.db python -m warden.cli serve --port 8000

test:
	pytest

lint:
	ruff check .
	ruff format --check .

clean:
	rm -f warden.db && find . -name __pycache__ -type d -exec rm -rf {} +
