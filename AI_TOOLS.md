# A note on AI tools

Required by the brief, and worth writing carefully: how someone uses AI to build an
AI system says more than the system does.

## What I used

**Claude Code (Opus)** as the primary development environment for the whole build —
ingest, retrieval, the agent loop, the API, the interface, the tests. Not
autocomplete; an agent with repository access, running commands and editing files
while I directed the work.

**A separate adversarial review pass**, run as a second agent on a different model
with a brief to find defects rather than to agree. Deliberately a different model
from the one that wrote the code: an author reviewing its own work re-reads its own
assumptions.

**These three documents** were drafted with Claude Code as well, from the actual
repository and from measurements taken against it, then edited. Saying so matters
here: a note about AI use that was itself AI-drafted and did not mention it would be
undermining its own point. One claim in `ARCHITECTURE.md` did not survive being
checked — a retrieval score quoted from an earlier session that could not be
reproduced against the current ranker — and was replaced with a measurement taken
fresh. That is the failure mode this whole document is about, showing up in the
document about it.

**Google Gemini** is what the shipped product calls at runtime — a product
dependency, not a development tool. It is behind an OpenAI-compatible client
abstraction, and the reason it is Gemini rather than OpenAI is billing, not
capability.

## How the work was actually divided

The design decisions in `ARCHITECTURE.md` were mine, and most of them were made
*against* a first draft.

Concretely: excluding superseded documents rather than down-weighting them; putting
scope enforcement in the SQL predicate and before ranking rather than in the prompt;
making `confirm_action` unreachable from the model instead of instructing the model
not to call it; treating business-hours SLA targets as uncomputable rather than
assuming an eight-hour day; keeping policy figures out of the calculator. Each of
those replaced something simpler and more obvious that would have passed a casual
read.

What the AI was genuinely better at: writing the code once a decision was made,
holding a large surface in view during a refactor, and generating the test cases I
would not have thought to write. Several of the properties in the test suite — that
targets never originate from v2, that business-hours durations refuse to become
deadlines, that no tool payload on the wire ever carries another account's id —
came out of that.

## Where it was wrong, and how that was caught

This is the part worth reading.

**Retrieval that looked correct and was not.** The first ranker used an
off-the-shelf `BM25Plus`, which adds a constant to every document's score — so
nothing ever scores zero and "did this passage match at all?" stops being
answerable. Measured: *every* chunk was a candidate for *every* question. Reading
the code did not reveal this; printing the candidate set did. Replaced with a
hand-rolled BM25 using Lucene's IDF; candidates dropped to a mean of 11.

**A hallucinated number was indistinguishable from a correct one.** The calculator
takes policy figures as arguments — correct, so that nothing hard-codes a threshold
— but nothing verified the model had read those figures anywhere. The arithmetic ran
faithfully on invented inputs and returned a precise, well-cited, wrong answer. This
was found by adversarial review, not by testing, because every test passed. The fix
binds every figure to a passage the caller actually cited.

**A path traversal in static file serving.** `root / user_path`, straight out of a
URL. Found by the review pass, verified, fixed with resolve-and-contain.

**A turn that could run for the better part of an hour.** Per-request timeouts
multiply — models × retries × timeout × steps — and each value looked reasonable on
its own. Also from the review pass.

**Tests that passed while the app would not start.** The API tests were written when
`web/dist` did not exist, so the SPA fallback route was never exercised, and a bad
return annotation crashed startup. Green tests, dead app.

**A serialiser that silently dropped a field.** A fix for duplicated tool cards was
inert for a day because `call_id` was never forwarded to the browser. Found by
asking the running system a real question, not by any test.

The pattern is consistent enough to state plainly: **AI-assisted code fails in ways
that read well.** The defects above were not sloppy — they were plausible, internally
consistent, and mostly test-covered. Every one was caught by measuring behaviour,
running the real thing, or pointing a hostile reader at it. None was caught by
reviewing the diff.

## Where the tooling cost me

Three times, an untargeted `git add -A` swallowed unrelated in-progress work into a
commit, against my own commit discipline. Once, a tracked file was overwritten
without checking `git status` first, destroying a better committed implementation —
recovered with `git checkout`. Both are supervision failures rather than model
failures, and both argue for the same thing: an agent with write access to a
repository needs the same review a junior engineer's PR gets, applied at the same
frequency, not at the end.

## What I would keep

Using a different model to review than to write. It found the two most serious
defects in the codebase and half a dozen smaller ones, and it had no stake in the
explanations that made them look reasonable.

Making the system runnable without a key. `--scripted` exists because a provider
went down mid-build; it turned out to also be what makes the full stack testable in
CI and demonstrable when a budget is spent.

And measuring rather than reasoning about behaviour. Every significant fix in this
project started with printing something out.
