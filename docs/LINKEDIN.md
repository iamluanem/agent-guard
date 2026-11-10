# AgentGuard: what happens when an AI agent buys the wrong thing?

*Technical write-up for an engineering portfolio.*

AI agents are starting to buy things for people. Agentic-commerce protocols (AP2 and its mandates, agent-initiated card credentials) answer "is this agent allowed to pay?". They don't fully answer the next question: **what happens when the agent, or the merchant behind it, gets the purchase wrong?**

I built AgentGuard, an MVP of a B2B trust layer that sits between shopping agents and payment execution, to explore that question end to end.

## The problem in one instruction

> "Buy one pair of running shoes, size 38, for no more than $150."

Things that can go wrong: the agent retries after a timeout and buys twice. It picks size 42. It pays $179. It chooses a merchant the customer never approved. Or everything is right on the agent side, and the merchant ships two pairs and charges for both, then refuses the refund.

Each failure needs a different response: **prevent**, **detect**, **recover**, **reimburse**. AgentGuard models all four and keeps the money for each in a separate bucket.

## Design decisions I'd defend in a review

**1. The LLM never decides anything about money.**
The LLM turns "no more than $150, size 38" into *proposed* structured constraints, and it writes incident narratives. That's all. Authorization, refunds and payouts come from a pure, deterministic policy engine with no I/O and no clock reads. Given the same facts, it returns the same decision, so any decision can be replayed from the audit log. A rules-based fallback means the system runs without any model at all.

**2. Mandates are signed and immutable.**
Each mandate version is canonicalized, hashed and HMAC-signed, then re-verified on every evaluation. Amendments append a version and revocation is audited. If someone edits the constraints directly in the database, the next attempt fails with `SIGNATURE_INVALID`.

**3. Exactly-once execution, in layers.**
- An idempotency key plus a payload hash: a retry returns the original result, and reusing the key with a different payload returns `409`.
- A `SELECT … FOR UPDATE` lock on the mandate row, with an idempotency check repeated after the lock is acquired.
- `UNIQUE(mandate_id, execution_slot)` as the backstop. A single-use mandate physically cannot execute twice, and the engine turns the constraint violation into a clean `DUPLICATE_EXECUTION` block.

A test fires 12 concurrent threads at one mandate. Exactly one purchase is authorized, on both PostgreSQL and SQLite.

**4. Webhooks are hostile input.**
Signatures are HMAC-checked, and a unique `event_id` makes redelivery harmless. A per-source sequence cursor defers out-of-order events until the gap fills (a capture arriving before its authorization) and ignores stale ones. A sweeper force-applies events stuck behind a gap that never fills.

**5. A refund request is not money back.**
Recovery tries cancellation, then a refund, then retries with exponential backoff, then escalates. Funds only count as *recovered* when the merchant confirms. If recovery fails, a configurable claims engine checks authorization validity, evidence, recovery attempts, exclusions, duplicate compensation, the deductible and the limits. Claims above a threshold go to a human, who can lower the payout but never exceed the policy cap.

**6. Simulated money is labeled as simulated, everywhere.**
Payouts go into a balanced double-entry *sandbox* ledger. Every row is flagged, and the dashboard draws simulated money with a hatched pattern. It's a demo of reimbursement mechanics, not insurance.

**7. Evidence you can't quietly rewrite.**
Each audit record hashes the previous one, and a PostgreSQL trigger rejects `UPDATE`/`DELETE` on the table. One endpoint recomputes the chain and reports the first broken record. Correlation IDs flow from the HTTP request into audit records, outbox events and JSON logs, and OpenTelemetry is optional.

## Stack

FastAPI · Pydantic v2 · SQLAlchemy 2 · PostgreSQL · Alembic · Redis Streams (transactional outbox) · MCP server (agents get a `guarded_purchase` tool, never raw payment access) · Next.js + React + TypeScript + Tailwind · Docker Compose · GitHub Actions (lint, migration drift check, tests on two databases, image builds).

## What the demo shows

Six scenarios run through the real engine: a correct purchase, a duplicate blocked, wrong quantity rejected, an over-budget purchase waiting for approval, a merchant mis-shipment refunded, and a declined refund paid out by a simulated claim. The overview separates **prevented loss**, **recovered funds** and **simulated payouts**, because they mean different things to a risk team.

## What I'd do next

Customer-side signatures (passkeys or verifiable credentials instead of a platform HMAC), KMS-backed keys, real PSP and AP2 credential-provider adapters, chargeback integration, and per-merchant risk scoring fed by incident history.

**Core principle: AI agents will make mistakes. Customers need *verifiable* protection when they do.**

Repo: https://github.com/iamluanem/agent-guard
