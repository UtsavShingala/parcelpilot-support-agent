# Product

## The problem this actually solves

ParcelPilot's support answers already exist. They are in a policy document, an SOP,
a product guide, and — for two customers — a signed agreement that quietly
contradicts all of the above.

The problem is not that the answers are missing. It is that **no one can hold them
in their head at once.** A support agent handling a Northstar cancellation has to
remember that Northstar's contract waives a fee the SOP charges, that the policy
they half-remember was superseded in a version bump nobody announced, and that the
last time this question came up, the answer given was wrong.

That is where the money leaks. Not in slow replies — in confident wrong ones. A
support agent who says "there's no fee" when there is has cost a margin. One who
says "there's a fee" when the contract waives it has cost a renewal conversation.
Both feel like good service at the time.

**So the product bet here is inverted from most support bots: the primary feature is
knowing when not to answer.** A deflected question is worth something. A wrong
answer is worth considerably less than nothing, because it is discovered later, by
the customer, with a contract in hand.

Everything visible in the product follows from that bet.

---

## Who it serves

One system, three jobs.

**The customer** wants a specific answer about their own account, and correctness
matters more than speed — they will act on it. They get an assistant confined to
their account by construction, that cites what it relied on, and that says "let me
get a person" rather than guessing.

**The support agent** wants the same answers across every account, plus the ability
to act — escalate, update a ticket, schedule a follow-up. They get the full corpus
and the action tools, with a confirmation step in front of every write.

**The ops manager** wants to know what is going wrong *before* a customer writes in.
They get the operations view.

The persona picker is the seam between them. It is doing three jobs at once — mock
login, access-control demonstration, and abuse gate on a public URL — which is more
than a login screen usually earns.

---

## What shipped, and why each piece is a product decision

### It cites, always

Every answer names the documents behind it, and the interface shows the clause text
next to the answer. Not decoration: a support agent forwarding an answer to a
customer needs to know what it rests on, and a customer challenging one needs
something to challenge.

### It names conflicts instead of hiding them

When a contract overrides general policy, the answer says so and cites both. The
system could pick the winner silently — ranking already knows which one governs —
but "your agreement waives the fee the standard SOP would charge" is a *better*
answer than either source alone, and it is what a good support agent would say.
Hiding the conflict would also hide the single most valuable thing the system knows.

### Escalation is an outcome, not a failure

The assistant hands over when the answer is not in the documents, when sources
conflict in a way the hierarchy cannot settle, when the request needs judgment no
document supports, or when a calculation is flagged as needing human confirmation.
Every handover carries a note of what was already established, so the person picking
it up does not restart from zero.

This is presented as a successful outcome in the interface, deliberately. A system
that treats escalation as failure teaches its operators to tune it toward answering
anyway.

### Nothing is done without being asked

The model can prepare an escalation, a ticket update or a follow-up. It cannot
perform one. The user sees exactly what would happen and clicks to allow it.

The product reason, beyond safety: **the first time an assistant does something a
user did not expect, the user stops trusting all of it** — including the answers
that were fine. The confirmation step is cheap and it buys the benefit of the doubt.

### Operations view — the bonus problem

**Chosen: Problem 1, proactive issue detection.**

Chosen over the alternatives because it reuses the entire scoped data layer that
already existed, so the marginal cost was small and the marginal capability is
large: the difference between a system that waits to be asked and one that tells you
what is wrong.

It surfaces:

- **Response targets missed or about to be**, measured against the dataset snapshot,
  each naming the target it missed and citing the clause that set it — including
  where a customer's contract sets a tighter target than their plan.
- **One fault, several tickets**, flagged as an incident rather than a support
  question when more than one customer is affected.
- **Past answers the current documents contradict** — the workbook says outright
  that some recorded resolutions are wrong, so those are surfaced rather than left
  to be quoted back at a customer.

Two constraints kept it useful. It makes **no model calls** — a dashboard that costs
money to open does not get opened, and a finding that changes between refreshes
cannot be trusted. And it is **tuned for precision over recall**: a cluster that is
not really a cluster reads as "two customers are affected", which is worse than a
missed one, because it gets ignored after the second false alarm.

**Problem 2, trust,** is addressed structurally rather than as a feature — the
authority hierarchy, the exclusion of superseded material, conflict surfacing, the
grounding check on every figure, and escalation as a first-class outcome. It did not
need its own screen; it needed to be true everywhere.

---

## Built beyond the brief

None of the following was asked for. Each is here because the product is worse
without it, and each cost little enough that not building it would have been the
odd choice.

**It runs with no API key at all.** `--scripted` swaps the model for a deterministic
client while every other layer — tools, scoping, authority ranking, the confirmation
gate — executes for real. Built because a provider went down mid-project and the
whole system became undemonstrable in an instant. A demo that dies when someone
else's service does is not a demo, and a support product that cannot be exercised
without spending money cannot be tested by the people who need to trust it. The
interface labels it, so an assembled answer is never mistaken for a reasoned one.

**A model that is down does not take the product down with it.** Overload, quota
exhaustion and timeout are per-model conditions, so the client tries the next name
on a list. Over one afternoon three different models each went unavailable while the
others answered fine. Support is the function customers reach for when something has
already gone wrong; being unavailable then is worse than being unavailable generally.

**Every figure in an answer is bound to the passage it came from.** Not required, and
the single most valuable thing here. Without it a hallucinated threshold produced a
precise, well-cited, wrong answer — the exact failure the product exists to prevent,
wearing the costume of a good one.

**The cost of an answer is bounded.** A repeat guard, a per-tool budget and a
wall-clock ceiling on the turn. This is a product constraint before it is an
engineering one: a support assistant whose cost per answer is unpredictable cannot be
priced, and one that leaves a customer watching a spinner for three minutes has
already failed regardless of what it eventually says.

**The public URL assumes strangers.** Opaque server-side sessions, a per-visitor
message allowance where failed turns still count, and oversized input rejected before
any work happens. A demo link that a stranger can drain is a demo link that stops
working before the person you sent it to opens it.

**Nothing about the customer roster is written in code.** Personas are read out of
the account table, and the severity rules are read out of the policy's own wording.
The brief says evaluators will test with different records; a hard-coded roster would
be a second copy of the data that goes stale the moment the workbook changes.

**The audit ledger is a separate file from the corpus.** Rebuilding the index must
never erase the record of something the system actually did. The corpus is
disposable; what was done on someone's account is not.

---

## The one metric

**Correct containment: the share of customer questions the assistant closed without
a human, that a support agent sampling them agrees were right.**

Measured as a single number, sampled weekly from a random draw of contained
conversations.

Why this one and not the obvious alternatives:

**Deflection rate alone is actively dangerous here.** It rises when the assistant
answers more questions, including the ones it should have escalated. Optimising it
optimises for confidence, which is precisely the failure this product exists to
prevent. A support bot with 95% deflection and a 4% wrong-answer rate is worse than
no bot, and deflection rate reports it as a triumph.

**CSAT is too slow and too noisy** at this volume, and customers rate wrong answers
highly right up until they act on them.

**Escalation rate is deflection wearing a disguise** — same gaming, opposite sign.

Correct containment can only be moved two ways: answer more questions, or be right
more often. Answering more while being wrong pushes it down. Escalating everything
pushes it down. Both failure modes are visible in the same number, which is what a
single metric has to do.

It carries one hard guardrail, not a second metric but a stop condition:
**confidently wrong answers are treated as incidents**, investigated individually,
not averaged into a rate.

The leading indicator to watch alongside it: **what fraction of escalations the
human agreed needed escalating.** If that drops, the assistant is handing over work
it should have handled and containment will fall next.

---

## What I deliberately left out

**A real escalation destination.** Confirmed actions land in an audit ledger, and
the interface shows them. They do not reach a helpdesk queue, an email, or a Slack
channel. The integration is real work with no design risk in it, and the assessment
is about the decision to escalate, not the plumbing behind it.

**Real authentication.** The persona picker is a mock login. Actual SSO would
demonstrate nothing the access-control layer does not already demonstrate — every
tool derives its scope from a `CallerContext`, and where that context came from is
the least interesting part.

**A feedback loop.** No thumbs, no correction capture. It is the highest-value thing
missing, and it is missing because it is worth building properly or not at all: a
feedback signal nobody routes anywhere is theatre.

**Vector search.** 25 chunks. Lexical ranking was measured to be correct on every
case that matters. Adding embeddings would have meant a second API key, another
dependency and another failure mode in exchange for no better answer.

**A second corpus.** The ingest pipeline is corpus-agnostic — authority is derived
from document headers rather than filenames, precisely so it generalises — but a
second synthetic corpus was scoped out to keep the submission focused on the pack
that will actually be tested.

**Confidence shown to the user.** Considered and rejected: a percentage next to an
answer invites people to read 80% as "probably fine" and act on it. The system makes
a binary decision — answer or hand over — and stands behind it.

---

## What I would build next, in order

1. **An eval set over the four traps.** A fixed list of questions with expected
   outcomes — the superseded policy must never win, the contract must override, the
   wrong historical resolutions must not be repeated, cross-account attempts must
   return nothing — run as one command printing a pass table. The existing tests
   prove the *parts* work. This proves the *answers* are right, which is the thing
   being graded and the thing that regresses silently. It is first because
   everything after it is safer once it exists.

2. **Close the loop between insights and the documents.** The ops view already
   detects past answers that current documents contradict. That is one step from a
   documentation backlog: recurring questions the corpus answers badly are a
   content problem, and the system is already sitting on the evidence.

3. **The escalation queue as a real destination**, with the handover note attached —
   the note is already written and currently nobody receives it.

4. **Feedback capture routed somewhere**, feeding the eval set. A correction from a
   support agent is the highest-quality training signal available and it is
   currently discarded.

5. **Embeddings, gated on corpus size rather than preference.** The seam exists —
   normalised scores, one ranking function. It should activate when a real
   paraphrase query is observed to miss, not before.

---

## The honest assessment

What works well: the access control is structural rather than instructed, and I have
not been able to make it leak. The authority hierarchy handles the corpus traps the
pack was built around. Escalation is a genuine path rather than an error message.

What is weaker: the assistant over-searches — turns run six to ten steps on
questions that need four. Guards bound the cost but do not make it elegant. And
there is no eval set yet, which means the strongest claim I can make about answer
correctness is that the tests cover the components and the traps I thought to check.
That is the gap I would close first, and it is the reason it sits at the top of the
list above.
