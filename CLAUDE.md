# ParcelPilot Support Agent

## Commit discipline — read this before doing anything else

**Commit continuously, not at the end.** The commit history is part of what gets reviewed.
Someone should be able to read `git log` alone and understand how this system was built,
in what order, and why — without opening a single file.

Rules:

* **One concern per commit.** Authority metadata and the retrieval ranker are separate
  commits even when written in the same sitting.
* **A single instruction may warrant several commits.** If one task produces three
  separable changes, that is three commits — do not batch them because they arrived
  together. Split by what changed conceptually, not by when you typed it.
* **Every commit leaves the repo working.** Never commit a half-applied refactor or a
  module that breaks an import. If a change must land in pieces, order the pieces so each
  one is coherent on its own.
* **Commit before anything risky** — a large refactor, a dependency change, a rewrite of
  something that already works. There must always be a clean point to return to.
* **Write messages for someone reading cold.** State what changed and why it changed.
  `wip`, `updates`, `fixes` and `asked changes` are not acceptable.
* **Conventional prefixes:** `feat:` `fix:` `refactor:` `test:` `docs:` `chore:`

Good:

```
feat(ingest): derive authority tier from filename and header text
feat(retrieval): rank by authority before similarity, drop deprecated chunks
test(access): cover cross-account agreement retrieval
refactor(tools): move account scoping into the query layer
```

Bad:

```
added stuff
day 1 done
fixed the thing
```

**Push after every commit.** Do not batch pushes to the end of a session. Pushing as you
go leaves a history spread naturally across the days the work actually happened, instead of
one dump that reads as though the whole thing was assembled in an hour.


## What this is

A take-home assessment for an AI Engineer role. Build an agentic RAG support system
for a fictional B2B logistics platform (ParcelPilot) over an **intentionally messy**
document corpus.

Deliverables: public repo + hosted app + ~5 min demo video + `ARCHITECTURE.md` +
`PRODUCT.md` + a note on AI tools used.

Repo: https://github.com/UtsavShingala/parcelpilot-support-agent (public — required)

## Hard requirements

1. Natural-language chatbot. Answers **only** from the supplied pack. Escalates to a
   human when it cannot answer confidently or the request needs judgment.
2. Access control enforced in the **data/tool layer**, not by prompt instruction.
   Customers must never see another account's data.
3. At least three distinct tools: document search, structured-data lookup/calculation,
   and a state-changing action (escalation / ticket update / follow-up task).
4. **Explicit user confirmation before any state-changing action.** Prepare, ask, then execute.
5. Multi-step requests: order → account → that account's contract → applicable policy/SOP
   → calculation → escalation decision.
6. Chat UI that shows which tool is currently running. Hosted link strongly preferred.
7. ~5 minute demo video: architecture, live demo, key decisions and why.

**Evaluators will test with different record IDs and questions from the same pack.**
Never hard-code IDs or answers — always load and reason over the data.

## The corpus and its deliberate traps

Lives in `data/raw/` (gitignored). Download:
https://drive.google.com/drive/folders/1iPwLSAOjh1qBzVj6ywWP5iBhTpLDR3C-

| File | Role |
|---|---|
| `01_Support_Policy_v3_CURRENT.pdf` | Authoritative general policy |
| `02_Support_Policy_v2_DEPRECATED.pdf` | **Trap** — superseded, must never win an answer |
| `03_Cancellation_and_Service_Credit_SOP_v4.pdf` | Cancellation fees, credit eligibility |
| `04_Product_Operations_Guide_and_Known_Issues.pdf` | Product behaviour, known bugs |
| `05_Northstar_Logistics_Enterprise_Agreement.pdf` | **Overrides** general policy for Northstar |
| `06_LumenWorks_Service_Agreement.pdf` | **Overrides** general policy for LumenWorks |
| `ParcelPilot_Assessment_Data.xlsx` | Accounts, orders, tickets + README sheet with snapshot time |

## Source authority hierarchy — the core design rule

1. **Customer-specific agreement** (scoped to that account only) — highest authority
2. **Current policy / current SOP**
3. **Product operations guide**
4. **Historical ticket resolutions** — context only, never authoritative, known to contain
   incorrect guidance
5. **Deprecated policy versions** — excluded from answers; may be cited only to explain
   that something changed

Every chunk carries metadata: `source_file`, `doc_type`, `version`,
`status` (current|deprecated), `scope` (global|<account_id>), `effective_date`.
Ranking is **metadata-aware**, not pure embedding similarity.

When a contract overrides general policy, the answer must **say so explicitly** and cite both.

## Time

Use the snapshot timestamp from the xlsx **README sheet** as "now" for all time-based
logic (SLA breaches, ticket age). **Never use the real system clock.**

## Architecture

Single FastAPI service serving both the API and the built frontend from one origin —
no CORS, one URL, one deploy.

```
src/parcelpilot/
  ingest/     PDF → chunks + authority metadata; xlsx → SQLite tables
  retrieval/  hybrid store, authority-aware ranking, conflict detection
  auth/       CallerContext + persona definitions
  data/       SQLite, account-scoped queries
  agent/      tool definitions, agent loop, prompts
  insights/   proactive issue detection
  api/        /api/chat (SSE), /api/session (persona login)
web/          chat UI showing live tool calls
```

Standard Python src-layout: `src/` is the backend package root, `web/` the frontend.
Two separate toolchains, one deployed container — the Dockerfile builds `web/` and
serves the result as static files from the FastAPI process.

**Model: OpenAI via the `openai` SDK.** Chosen because that is where the available API
credit is, not for any capability reason — nothing in this design depends on a particular
provider. Keep the tool-execution logic in `agent/loop.py` separate from the API call
itself, so the provider stays a small, isolated edit rather than a rewrite.

**Retrieval: BM25 only, no embeddings.** The whole corpus is 25 chunks and roughly 1,500
tokens. Lexical search over policy text — full of exact terms like "service credit",
"cancellation fee", order ids — was measured to rank correctly on every case that matters,
so embeddings and a vector store would add a dependency, a second API key and a failure
mode while changing no answer. Revisit only if a real paraphrase query is observed to miss.

Retrieval earns its place here for **access control**, not context-window pressure: the
corpus would fit in a single prompt, but `scope.permits()` runs before scoring, so another
account's material is never ranked, quoted or counted.

## Access control

The persona picker is simultaneously the mock login, the access-control demo, and the
abuse gate on a public URL.

Personas: Northstar customer · LumenWorks customer · ParcelPilot support agent · ops manager.

Every tool takes `caller_context` (role + account_id) as its first argument and filters at
the query layer. No persona selected → `/api/chat` refuses, enforced server-side.

## Bonus problem chosen

**Problem 1 — Proactive Issue Detection.** An internal ops view surfacing recurring
complaint clusters, multiple tickets on one product issue, high-severity tickets near or
past SLA, and multi-customer incidents. Mostly aggregation over the ticket sheet: high
payoff for low effort.

**Problem 2 — Trust** is addressed structurally by the authority hierarchy, explicit
conflict surfacing, and the escalation path.

## Deployment

- Host: Railway or Fly.io. **Not** Render's free tier — ~50s cold starts hurt a reviewer demo.
- Domain: `parcelpilot.utsavshingala.com` (user owns `utsavshingala.com`)
- `ANTHROPIC_API_KEY` server-side only, never in the frontend bundle
- Spend cap set in the Anthropic console; per-session message rate limit

## Portfolio consideration

The ingest pipeline must be **corpus-agnostic** (the brief demands this anyway). Plan: a
second synthetic corpus for a different fictional company with the same structural traps
(a superseded policy version, two overriding contracts, a ticket log with wrong
resolutions), selectable via a `CORPUS` env var. This proves generality — the thing the
brief says will be tested — and lets the public portfolio demo run on owned data.

`data/raw/` is gitignored. `data/synthetic/` is committed so the repo clones and runs standalone.

## Working agreements

- **Commit freely and often, and push after each commit** — see the commit discipline
  section at the top of this file.
- Discuss and wait for a go-ahead before editing code when the user is asking a question
  or weighing options.
- Always reply in English, even when the user writes in Hindi/Hinglish.
