# Grace. Two stages: build the wheel, then run it as a non-root user.
FROM python:3.12-slim AS build

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip build && python -m build --wheel

FROM python:3.12-slim

# Nothing in this image needs to write to the filesystem except the SQLite ledger.
RUN useradd --create-home --uid 10001 grace
WORKDIR /app

COPY --from=build /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl "uvicorn>=0.30" && rm -rf /tmp/*.whl

COPY web ./web
RUN mkdir -p /data && chown grace:grace /data
USER grace

# The ledger lives on a mounted disk in production and in memory otherwise, so a restart is clean.
ENV GRACE_DB=/data/grace.db \
    GRACE_PAYPAL_MODE=auto \
    PYTHONUNBUFFERED=1

EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8020/api/health', timeout=4).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "grace.api:app", "--host", "0.0.0.0", "--port", "8020"]
