# --- Stage 1: build the frontend ---
FROM node:20-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Stage 2: the service, with the frontend baked in ---
FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir .

# The corpus. data/raw is absent from the repository by design, so a build from a
# clone carries only the placeholder; a build from a local checkout bakes in the
# real pack. Either way the index is built at startup, not committed.
COPY data/ data/
COPY --from=web /web/dist static/

# Paths are absolute so nothing depends on where the package ended up. The package
# is installed rather than laid out in src/, so there is no repository root above it
# to anchor a relative path against.
ENV DATA_DIR=/app/data \
    INDEX_DIR=/app/data/index \
    PORT=8000

# Build the index, then serve. Building here rather than at image-build time keeps
# the artifacts matched to whatever corpus is actually mounted or copied, and fails
# loudly at startup if there is no corpus at all rather than serving an empty one.
CMD ["sh", "-c", "python -m parcelpilot.ingest.build_index && exec uvicorn parcelpilot.api.main:app --host 0.0.0.0 --port ${PORT}"]
