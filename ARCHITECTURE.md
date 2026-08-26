# Architecture

A support assistant is easy to build and hard to trust. The hard part of this
system is not the chat loop — it is that the corpus it answers from disagrees with
itself, and that being wrong is worse than saying nothing.

Four properties drove every structural decision:

1. **The documents contradict each other, deliberately.** A superseded policy still
   reads as current. Two customer agreements override the general terms for their
   own account only. The ticket log contains answers that are now wrong.
2. **A customer must never see another customer's data** — not as a matter of the
   model behaving well, but as a matter of the query being incapable of returning it.
3. **The model may propose actions. It may not perform them.**
4. **"Now" is a timestamp in a spreadsheet, not the system clock.**

Everything below follows from those.

---

## The shape

```
                     browser (React + Vite)
                            │  SSE
┌───────────────────────────┼────────────────────────────────┐
│  FastAPI (one process, one origin, no CORS)                │
│                           │                                │
│  api/        session · chat · actions · insights · static  │
│                           │                                │
│  agent/      loop ── registry ── tools ── model client     │
│                │                    │                      │
│  retrieval/    ├─ store ── scope ── lexical (BM25)         │
│  data/         └─ queries ── SQLite (account-scoped SQL)   │
│  auth/       CallerContext → AccountScope                  │
│  insights/   proactive detection (no model calls)          │
└────────────────────────────────────────────────────────────┘
                            ▲
                  ingest/   │  offline, run once
             PDFs ──────────┘  → 25 chunks + authority metadata
             XLSX ──────────┘  → SQLite tables + snapshot clock
```

One FastAPI process serves the API and the built frontend from the same origin.
No CORS, one URL, one container. The frontend is a build artifact, not a service.

Ingest is offline and idempotent. Nothing at request time parses a PDF.

---

## A request, end to end

*"Can I cancel ORD-1001 without a cancellation fee?"*, asked as Northstar.

1. **Session.** The browser sends an opaque cookie. The server looks up a
   `CallerContext(role=CUSTOMER, account_id="ACCT-001")`. No role or account id is
   ever sent to the browser, so none is ever read back from it.
2. **Registry.** The tools this role may call are assembled — the mutating ones
   filtered out by role, `confirm_action` absent entirely.
3. **`lookup_orders`.** The SQL carries `WHERE account_id = ?`. The order exists
   and belongs to this account, so a row comes back. Asked as LumenWorks, the same
   query returns nothing — not an error, nothing.
4. **`search_documents`.** `AccountScope.permits()` filters chunks *before* BM25
   scores anything. Northstar's agreement is in the candidate set; LumenWorks's is
   not, so it cannot be ranked, quoted or counted. The deprecated v2 policy is
   excluded by a hard filter. Results come back with the agreement leading and the
   SOP behind it.
5. **Conflict detection.** Both the agreement and the SOP matched the same query
   terms and say different things about the same fee. That is reported as an
   explicit override — the agreement says in so many words that it replaces the
   SOP's default — so the answer must name both.
6. **`calculate`.** The free-cancellation window and the fee amount are passed *in*,
   as figures the model read out of the retrieved clauses. Before the arithmetic
   runs, each figure is checked against the passages actually cited. Timings are
   measured from the snapshot, never the clock.
7. **Answer.** Streamed as typed SSE events — `tool_start`, `tool_result`,
   `text_delta`, `completed` — so the interface shows which tool is running while
   it runs.

If step 6 had come back flagged as needing human confirmation, or if nothing above
the relevance gate had been retrieved, the turn would have prepared an escalation
instead — and even then, prepared it, not sent it.

---

## Ingest

The pack is six PDFs and a workbook. The PDFs are Google Docs exports, which is
why this stage is more than a text dump.

**Outline reconstruction.** The export destroys line structure — no reliable
newlines, no heading tags. What survives is font size. `pdf_text.py` extracts
`TextRun(text, size, page)` through a pypdf visitor; `sections.py` ranks the
above-body sizes and rebuilds the outline from them. One bug worth recording: the
title's size is also above body, so ranking naively nested every top-level section
under its predecessor. Depths are remapped to consecutive levels to fix it.

**Authority from content, not filenames.** `02_..._DEPRECATED.pdf` announces itself
in its name, and a corpus-agnostic pipeline cannot rely on that. `authority.py`
reads the header block — the part of the document that states its own status,
version, effective date and the account it applies to — and derives the tier from
that. Filenames are metadata about a file; header blocks are the document
speaking.

**Sections are chunks.** A section is already the unit a person cites — "the SOP,
section 2" — so it is kept whole rather than cut to a token window. The header
block becomes a chunk too, because it is what answers "which policy is in force?"
and "what plan am I on?".

The result:

| Document | Tier | Scope | Chunks |
|---|---|---|---|
| Support Policy v3 | `CURRENT_POLICY` | global | 5 |
| Support Policy v2 | `DEPRECATED` | global | 2 |
| Cancellation & Service Credit SOP v4 | `CURRENT_POLICY` | global | 4 |
| Product Operations Guide | `PRODUCT_DOC` | global | 5 |
| Northstar Enterprise Agreement | `AGREEMENT` | `ACCT-001` | 5 |
| LumenWorks Service Agreement | `AGREEMENT` | `ACCT-002` | 4 |

**25 chunks, ~5,250 characters.** That number governs several decisions below.

The workbook becomes SQLite — `accounts` (4), `orders` (6), `tickets` (7) — plus a
`corpus_meta` table holding the snapshot timestamp parsed from the README sheet.

Adding a corpus is a directory and a `CORPUS` env var. No code change.

---

## Retrieval

### Why BM25 and no embeddings

The whole corpus fits in a single prompt. Retrieval is not here to manage a context
window — it is here to enforce access control, and lexical search over policy text
full of exact terms ("service credit", "cancellation fee", order ids) ranked
correctly on every case that mattered. A vector store would add a dependency, a
second API key and a failure mode while changing no answer.

The scores are normalised 0–1 so a semantic score can be blended in later without
retuning anything above it. Revisit when a real paraphrase query is observed to miss.

### Why a hand-rolled BM25

Both obvious off-the-shelf variants fail at 25 documents:

- **BM25-Okapi** uses `log(N−n+0.5) − log(n+0.5)`, which hits zero for a term in
  half the corpus and goes negative past that. Over 25 chunks that threshold lands
  on ordinary topic words — "cancellation" gets scored as noise.
- **BM25+** fixes the negative IDF by adding a constant to every term's
  contribution, *including for documents that do not contain the term*. Every
  passage then scores above zero for any query, so "did this match at all?" stops
  being answerable. Measured directly: 23 of 25 chunks were candidates for every
  question, silently disabling the relevance gate. After the switch, 11.

Lucene's IDF — `ln(1 + (N−n+0.5)/(n+0.5))` — is positive for every term while a
passage sharing no terms with the query scores exactly zero. Both properties at
once. `k1=1.5`, `b=0.75`, untuned; the corpus is far too small to justify fitting
them to it.

### Authority multiplies, it does not substitute

| Tier | Multiplier |
|---|---|
| `AGREEMENT` | 1.30 |
| `CURRENT_POLICY` | 1.20 |
| `PRODUCT_DOC` | 1.05 |
| `HISTORICAL` | 0.80 |
| `DEPRECATED` | 0.50 |

A multiplier, not an additive bonus or a sort key. A passage that barely matches
cannot be promoted into an answer by the seniority of the document it came from.
Among passages that *do* match, the more authoritative one wins.

Historical sits below 1.0 because the workbook says outright that past resolutions
may be wrong.

### Deprecated is excluded, not demoted

This is the trap most likely to be failed quietly, and down-weighting does not
solve it. Measured: the v2 policy scores **0.836** lexically on a support-target
question, because v2 is a near-copy of v3 with different numbers — often worded
*closer* to the question than its replacement. Any multiplier gentle enough to be
defensible leaves it able to win.

So it is filtered out before scoring. Callers that genuinely need it — to explain
what changed — ask for it explicitly.

### Conflicts are surfaced, not resolved

Ranking decides which passage leads. It does not get to decide the other one
stopped existing. When an agreement and the SOP both speak to the same fee, the
honest answer names both and says which governs. `conflicts.py` treats two
passages as addressing the same question when they matched the same *query* terms
— a deliberately shallow test, and the right depth, since retrieval already
established both are relevant. What remains is whether they are relevant to the
same part of the question.

Where an agreement states its own precedence ("This clause replaces the default
failed-pickup credit amount and timing threshold in the SOP"), that is reported as
an explicit `OVERRIDE` rather than as bare `PRECEDENCE`.

---

## Access control

The rule: **a customer sees one account; internal staff see all of them.** It is
implemented once.

`CallerContext` is frozen — access cannot widen mid-request — and holds no
permission logic of its own. It *derives* an `AccountScope`, which is the single
place the rule lives. Two implementations would eventually disagree, and the one
that disagreed silently would be the leak.

That scope is applied in two places, both below the model:

- **SQL predicates.** `data/queries.py` builds `WHERE account_id = ?` into the
  query. There is no unscoped query to accidentally call.
- **Pre-ranking filter.** `scope.permits()` runs before BM25, so out-of-scope
  chunks are never candidates.

Every tool takes `CallerContext` as its **first positional argument**. Not an
option, not ambient state — first argument, always, so a tool that forgets to be
scoped cannot be written. Resources (store, database) are bound when the registry
is built, so that promise costs nothing at the call site.

Tools also declare which roles may call them, and the registry checks twice: once
to decide what the model is *told* exists, and again to refuse the call. Only the
second is enforcement. The first is a hint, and a hint is not a control.

`/api/chat` refuses without a session, server-side. The persona picker is
simultaneously the mock login, the access-control demo, and the abuse gate on a
public URL.

---

## Actions: prepare, ask, confirm

`prepare_escalation`, `prepare_ticket_update`, `prepare_follow_up` are pure
functions. They validate the request, resolve it against data the caller may see,
and return a draft. They write nothing — which matters, because a model exploring a
question will sometimes prepare an action it then decides against.

`ActionLedger.confirm` is the only function in this system that writes, and **it is
deliberately absent from the tool registry.** The model cannot reach it. A
confirmation has to arrive from the person, through `POST /api/actions/confirm`,
naming a draft the server already holds.

That distinction is the whole design. "Ask before acting" as a prompt instruction
is a request the model may forget under pressure. As an unreachable function it is
a property of the system.

Confirmation re-derives authorisation from scratch rather than trusting the draft.
A draft is just data, and by the time it comes back it has been outside the process.

Two timestamps are recorded: `effective_at` (the snapshot, keeping the ledger on
the same timeline as everything else the system reasons about) and `recorded_at`
(real wall-clock, because when a row was written is an audit fact, not something to
reason about). The ledger is a separate file from the corpus database — rebuilding
the corpus must not erase the record of something the system actually did.

---

## Time

Every date reaches the system through `data.snapshot_at`, read from the workbook's
README sheet: **2026-08-16 11:00:00+05:30**. SLA elapsed time, ticket age,
cancellation windows — all measured from there. The wall clock appears exactly
once, in the audit ledger, where it is the correct answer.

**Business-hours targets are deliberately not computed.** The policy states several
targets in business hours and the pack records no working calendar or holiday list.
Rather than quietly assuming an eight-hour day — inventing a calendar the corpus
never states — those calculations return the wall-clock figure marked as needing
human confirmation. That is a genuine escalation trigger, not a gap.

---

## The agent loop

A turn is: ask the model, run the tools it asked for, feed results back, repeat.
Two properties are load-bearing.

**Dispatch never touches the provider.** The loop talks to a `ModelClient` and a
`ToolRegistry`; neither knows about the other. Tool execution, scoping, role checks
and error handling are all provider-independent — which is what makes the client
swappable, and what lets the entire loop be tested with no key and no network.

**The ceiling escalates rather than retries.** A question still calling tools after
a dozen round-trips is not one call away from an answer; it is going in circles,
and the honest response is to hand it to a person with a note explaining what was
already established. Retrying spends more money to produce a worse version of the
same failure.

Four bounds, each added because something specific went wrong:

| Bound | Value | Why |
|---|---|---|
| `max_agent_steps` | 12 | The circling case above. |
| `MAX_CALLS_PER_TOOL` | 3 | A model rephrasing the same search repeatedly. Six distinct searches for one question is not thoroughness. |
| Repeat guard | identical calls | An identical call is answered from the transcript. Recorded on failure too — an erroring call is the shape most likely to be retried verbatim. |
| `max_turn_seconds` | 150 | Per-request timeouts multiply: models × retries × timeout × steps reached the better part of an hour with defaults that each looked reasonable alone. |

---

## Grounding

The calculator takes policy figures as *arguments* rather than holding a table of
thresholds — a calculator that knew the cancellation fee was INR 250 would be
hard-coding an answer the brief says will be tested with different records, and
would keep returning 250 after the SOP changed.

That is the right call, and it left a hole: nothing checked the model had actually
read those numbers anywhere. **A hallucinated figure was indistinguishable from a
correct one.** The arithmetic ran faithfully on it, the answer came back precise
and well-cited, and the citation was real even when the number was not — the exact
failure this system exists to prevent, produced by the part of it meant to prevent
guessing.

So every figure now names the passage it came from, and that passage is fetched
through the caller's own scope and searched for the value. A figure cannot be
grounded in a document the caller may not see, in a superseded one, or in one that
does not contain the number.

What this does *not* catch: a right-number-wrong-clause misread. The SOP's window
is genuinely in the SOP, so quoting it at a customer whose agreement overrides it
still grounds. Conflict detection is what surfaces that, and the answer must name
both.

---

## Proactive detection (bonus problem 1)

`insights/` reads the same scoped data the assistant reads and surfaces what a
support manager would want on a Monday morning:

- **Response targets already missed or approaching**, measured against the snapshot,
  each naming the target it missed and citing the clause that set it.
- **One product fault generating several tickets**, and specifically whether more
  than one customer is affected — which makes it an incident rather than a support
  question.
- **Past answers the current documents contradict.** The workbook warns outright
  that some recorded resolutions are wrong.

Three deliberate constraints:

**It makes no model calls.** A dashboard that costs money to open does not get
opened, and a finding that changes between refreshes cannot be trusted or tested.
Severities are suggested by matching the policy's own definitions — v3 section 2 —
and each suggestion shows the phrase that produced it so a reader can overrule it.

**It measures no business-hours targets**, for the reason above. Those are handed
to a person explicitly.

**It never widens what a caller can see.** The data arrives through the same scoped
queries as everything else, so `/api/insights` shows a support agent every account
and would show a customer only their own.

Two precision bugs are worth recording, because both were this system committing
its own failure in miniature:

- Tickets were clustered on known-issue *body* text. A known issue's body is a
  paragraph of ordinary support vocabulary — shipment, customer, upload, status —
  and any ticket collides with two of those by accident. A total outage was being
  filed under "Bulk Upload failures". Matching on the heading costs recall and buys
  precision, which is the right way round when a false positive reads as "two
  customers are affected".
- A ticket about CSV row limits was being flagged as possibly overridden by
  LumenWorks's agreement — on the strength of the account having *any* contract.
  The agreement covers support targets, cancellation and pickup credits and says
  nothing about uploads. Claiming otherwise asserted a relationship the corpus does
  not contain, which is precisely what the authority hierarchy exists to prevent.

Bonus problem 2, **trust**, is addressed structurally rather than as a feature: the
authority hierarchy, hard exclusion of superseded material, explicit conflict
surfacing, the grounding gate, and escalation as a first-class outcome.

---

## Transport and interface

`POST /api/chat` streams typed SSE events. The event name is on the wire as well as
in the payload, so a client can attach per-type listeners instead of switching on a
field it must parse first.

Payloads are trimmed to what a citation card renders. The chunk text is kept — a
reviewer should be able to read the clause an answer rests on without opening the
PDF — but relevance internals and matched-term lists are not sent, since nothing
displays them. Structured rows are capped at 25 per result so one broad lookup
cannot push a megabyte into the browser.

Each tool call carries a `call_id`. One model reply can contain several calls
sharing a step number, and matching on step alone gave both cards the first result.

The interface is two columns: the conversation on the left, the working — tool
calls, sources, conflicts — on the right, so a live tool trace does not push the
answer off screen.

**Public-URL hygiene:** httpOnly opaque session cookie; per-session message
allowance, where a failed turn still costs one (otherwise the demo is drainable by
anyone able to provoke failures); oversized and empty questions rejected before any
work; static file serving confined to the bundle directory by resolve-and-contain,
after a path traversal was found in review.

Conversation history is capped at six messages — three exchanges. Enough for "and
that one?" to resolve, short enough that the prompt does not grow without bound.

---

## Model provider

The client is named for the *protocol*, not the vendor: `CompatibleModelClient`
speaks the OpenAI chat-completions API, which OpenAI, Gemini's compatibility
endpoint, OpenRouter and Together all implement. Pointing at a different provider
is a base URL and a model name.

This was not theoretical. The build started on OpenAI, whose billing would not
accept the available payment method, and moved to Gemini mid-project. The change
was configuration plus one real incompatibility: Gemini returns a
`thought_signature` that must be replayed with the tool call it belongs to, or step
2 of any multi-tool turn returns a 400. `ToolCall.provider_extra` carries it
opaquely — the loop never inspects it.

**Fallback list.** 429, 5xx and timeout are per-model conditions, so a second model
name is the cheapest available recovery. Over one afternoon three different Gemini
models each went unavailable while the others answered fine. The timeout is 45s,
tuned against measurement rather than intuition: healthy Flash models answered in
5–12 seconds while a degraded one sat past 25 on a trivial prompt. A long timeout
does not rescue that — it makes every visitor wait out a model that is not going to
answer, multiplied by every name in the list.

**Scripted mode** (`--scripted` / `SCRIPTED=1`) runs a deterministic client instead
of a provider. It is a real runtime mode, not test scaffolding: the tools, scoping,
authority ranking and confirmation split all execute for real, and only the final
wording is assembled rather than reasoned. It means the system can be demonstrated
and deployed with no credentials, and stays alive when a budget is spent or a
provider is down. The interface labels it, so an assembled answer is never mistaken
for a model's.

---

## Testing

**325 tests**, and the ones that matter are properties rather than fixtures:

- The real corpus never answers from the superseded policy.
- A customer's turn never renders another account's identifier — checked across
  *every* tool payload on the wire, not just the final answer.
- The same question asked as two personas produces different answers.
- Targets never originate from v2.
- Business-hours durations refuse to become deadlines.
- Breaches are measured against the snapshot, not today.

Because scripted mode exists, the full stack — HTTP, session, registry, scoping,
ranking, confirmation — is exercised in CI with no key and no network.

---

## Trade-offs, honestly

**No embeddings.** Correct at 25 chunks; would be wrong at 2,500. The seam is
prepared — normalised scores, a single ranking function — but the work is not done.

**Severity is regex over policy wording.** It is transparent, free, testable and
shows its reasoning, which a model call would not. It is also brittle in ways a
larger ticket corpus would expose.

**Sections are chunks.** Right for a pack whose longest section is a few hundred
characters. A corpus with multi-page sections needs real chunking beneath the
section boundary.

**The model still over-searches.** Turns run 6–10 steps on questions that need
four. The guards bound the cost; they do not make it elegant.

**One process, SQLite, in-memory sessions.** Correct for a demo of this size and
explicitly not a horizontal-scaling story. Sessions would move to Redis and the
ledger to Postgres before a second instance existed.

## What I would build next

1. **An eval set** over the four traps — the deprecated policy, contract override,
   wrong historical resolutions, cross-account attempts — run as one command
   printing a pass table. The component tests prove the parts work; an eval proves
   the *answers* are right, and that is what a support system is graded on.
2. **A retrieval seam for embeddings**, activated by corpus size rather than by
   preference.
3. **Fewer steps per turn** — most turns retrieve enough by step three and keep
   going anyway.
