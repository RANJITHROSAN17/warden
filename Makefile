.PHONY: install demo serve dashboard test lint clean postman spec docker

install:
	python -m pip install -e ".[server,dev]"

demo:
	python -m grace.demo --all

serve:
	GRACE_DB=grace.db python -m uvicorn grace.api:app --host 0.0.0.0 --port 8020

dashboard: serve    ## alias: the README used to say "make dashboard", which did not exist
	@true

test:
	pytest

lint:
	ruff check .
	ruff format --check .

clean:
	rm -f grace.db && find . -name __pycache__ -type d -exec rm -rf {} +

# ── artefacts for the submission ────────────────────────────────────────────────
postman:            ## print the collection path and the run order
	@echo "postman/Grace.postman_collection.json  (8 requests: OAuth -> list -> assess -> file)"
	@echo "set paypal_client_id / paypal_client_secret as collection variables"

spec:               ## re-fetch PayPal's published Disputes spec
	curl -sSfL https://raw.githubusercontent.com/paypal/paypal-rest-api-specifications/main/openapi/customer_disputes_v1.json -o openapi/customer_disputes_v1.json
	@python3 -c "import json;d=json.load(open('openapi/customer_disputes_v1.json'));print('spec',d['info']['version'],'-',len(d['paths']),'paths')"

docker:             ## build the image Render uses
	docker build -t grace:local .
