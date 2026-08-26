# ParcelPilot Support Agent

An agentic RAG support system over a deliberately imperfect document corpus: a
superseded policy that still reads as current, customer agreements that override
general terms, and a ticket log containing wrong answers.

The interesting part is not the chat. It is what sits under it — retrieval that
ranks by **authority** before similarity, access control enforced in the **query
layer** rather than the prompt, and state-changing actions that a model can prepare
but only a person can perform.

## Run it without an API key

Everything except the model's own judgement runs with no credentials at all.

```bash
uv sync --extra dev
python -m parcelpilot.ingest.build_index          # needs data/raw, see data/README.md

python -m parcelpilot.cli personas
python -m parcelpilot.cli ask "Can I cancel ORD-1001 without a cancellation fee?" \
    -p acct-001 --scripted
```

`--scripted` (or `SCRIPTED=1`) runs a deterministic client instead of calling a
provider. It is a real mode, not test scaffolding: the tools, the scoping, the
authority ranking and the confirmation split are all exercised for real, and only
the final wording is assembled rather than reasoned. The interface labels it, so an
assembled answer is never mistaken for a model's.

To see the whole point of the system in one command:

```bash
python -m parcelpilot.cli compare "Can I cancel ORD-1001 without a cancellation fee?" \
    -p acct-001 -p acct-002 --scripted
```

Two customers, one question, two correctly different answers — because the tool
layer handed them different material, not because the model was told to pretend.

## Run it with a key

```bash
cp .env.example .env        # then paste MODEL_API_KEY
python -m parcelpilot.cli models     # lists what the key can actually reach
```

Defaults to **Gemini** through its OpenAI-compatible endpoint, which has a free
tier. Any provider speaking the same protocol works -- OpenAI, OpenRouter, Together
-- by changing `MODEL_BASE_URL` and `MODEL_NAME`; for OpenAI itself, leave the base
URL empty.

This is not a hypothetical: the project moved from OpenAI to Gemini mid-build when
the credit ran out, and it cost two settings and one provider quirk. Nothing above
[`agent/model.py`](src/parcelpilot/agent/model.py) changed.

Set `MODEL_NAME` to the cheapest model in that list that supports tool calling.
Every loop step resends the conversation, so model choice dominates cost far more
than provider choice does.

## The web interface

```bash
# terminal 1 — the API
uv run uvicorn parcelpilot.api.main:app --reload

# terminal 2 — the interface
cd web && npm install && npm run dev      # http://localhost:5173
```

In development Vite proxies `/api` so the browser still sees one origin. In
production FastAPI serves the built bundle itself, so there is no CORS anywhere.

```bash
cd web && npm run build     # then the API alone serves everything on :8000
```

## Docker

```bash
docker compose up --build   # http://localhost:8000
```

`docker-compose.yml` mounts `data/raw` read-only rather than baking it in, so the
corpus stays out of the image during local development.

> **Deployment note.** `data/raw` is not in the repository — the document pack
> belongs to the assessment provider. A `docker build` from a local checkout picks
> it up from the build context; a build from a fresh clone will not have it, and the
> container will fail at startup rather than serve an empty corpus. Hosting therefore
> needs the corpus supplied deliberately: baked in from a local build, mounted as a
> volume, or replaced by an owned corpus via `CORPUS`.

## Tests

```bash
uv run pytest              # tests needing the document pack skip without it
uv run ruff check src tests
cd web && npm run typecheck
```

## Layout

```
src/parcelpilot/
  ingest/     PDF -> chunks + authority metadata; xlsx -> SQLite
  retrieval/  BM25, authority-aware ranking, account scoping, conflict detection
  auth/       CallerContext and the persona roster, read from the data
  data/       account-scoped queries, scoping in the SQL predicate
  agent/      tools, registry, loop, prompts, scripted mode
  api/        sessions, SSE chat, action confirmation
web/          the chat interface
```

Design decisions and their reasoning are in [`ARCHITECTURE.md`](ARCHITECTURE.md);
product choices in [`PRODUCT.md`](PRODUCT.md).
