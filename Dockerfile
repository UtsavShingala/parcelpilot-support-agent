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

COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir .

COPY data/synthetic/ data/synthetic/
COPY --from=web /web/dist static/

ENV PORT=8000
CMD ["sh", "-c", "uvicorn parcelpilot.api.main:app --host 0.0.0.0 --port ${PORT}"]
