# AgentGuard — Product Requirements Document

> Status: MVP · Owner: Luane da Silva · All payments, merchants and reimbursements in the MVP are **simulated**.

## 1. Problem

AI agents now execute purchases on behalf of customers (agentic commerce, AP2-style mandates). New failure modes appear that card networks and merchants were never designed for:

| Failure | Example |
|---|---|
| Duplicate execution | Agent retries after a timeout and buys twice |
| Wrong quantity / product | "size 38" becomes size 42, 1 pair becomes 2 |
| Budget overrun | Instruction said "≤ $150", agent pays $179 |
| Unauthorized merchant | Agent picks a merchant the customer never approved |
| Missing / expired consent | Mandate revoked or expired but agent keeps buying |
| Execution error after the fact | Merchant charges more or ships the wrong item |
| Failed recovery | Merchant declines the refund |

**Core principle:** AI agents make mistakes. Customers need *verifiable* protection when they do.

## 2. Users

- **Agent platforms** (B2B): integrate the guarded purchase API / MCP tool so every purchase is checked.
- **Customers**: delegate purchases with explicit, signed, revocable mandates.
- **Operations / risk analysts**: monitor incidents, drive recovery, review claims.

## 3. Goals & non-goals

**Goals**
1. Prevent invalid transactions *before* execution when possible.
2. Detect execution mismatches *after* completion.
3. Recover funds from merchants automatically.
4. Evaluate reimbursement claims under a configurable policy when recovery fails.
5. Make every decision reproducible and tamper-evident.

**Non-goals (MVP)**: real money movement, real insurance coverage, card network chargebacks, KYC.

## 4. Key product decisions

| Decision | Rationale |
|---|---|
| Deterministic policy engine is the only authority over money | Financial decisions must be reproducible and auditable. LLMs are advisory (intent parsing, narratives). |
| Mandates are signed and versioned, never edited | An amendment is a new version; old evidence remains verifiable. |
| Over-budget within a ceiling → customer approval, beyond → block | Small overruns (price changed) are common; large ones are errors. Ceiling is configurable per mandate. |
| A refund *request* is never counted as recovered | Only a merchant `refund.succeeded` event moves money in the ledger. |
| Three separate money metrics | **Prevented loss** (blocked before execution) ≠ **recovered funds** (merchant refunds) ≠ **simulated protection payouts**. |
| Humans may lower but never exceed the deterministic claim cap | Human review handles judgment, policy still bounds exposure. |

## 5. Functional requirements

### A. Identity & authorization
- Register customers and agents (agents authenticate with an API key; only its SHA-256 is stored).
- Mandates carry budget, currency, merchant allowlist, product (category, required attributes, SKU allowlist), quantity, execution count and expiry constraints.
- Every version is canonicalized, hashed and HMAC-signed; the signature is re-verified at every evaluation.
- Customers can amend (new version) and revoke.

### B. Transaction protection engine
Rules: mandate present · signature valid · active · not expired · agent is the delegate · execution limit · repeated attempts · merchant allowed · product match · quantity · amount consistency · currency · budget.
Outcomes: `ALLOW`, `REQUIRE_APPROVAL`, `BLOCK` (any blocking violation dominates).

### C. AI risk analysis (advisory)
- Intent extraction → *proposed* constraints with confidence and ambiguities; the customer confirms them.
- Incident narratives and recommended recovery action.
- Provider abstraction (Anthropic / OpenAI / deterministic rules fallback), schema-validated output.

### D. Monitoring
State machine `CREATED → AUTHORIZED → EXECUTING → COMPLETED` with `AWAITING_APPROVAL, BLOCKED, FAILED, DISPUTED, RECOVERY_PENDING, REFUNDED`. Webhooks are signed, idempotent, ordered by per-source sequence (gaps deferred, stale ignored).

### E. Recovery
Incident → disputed amount → cancellation if supported and not shipped → refund otherwise → retries with exponential backoff → escalation to claim → customer notifications.

### F. Claims
Checks: valid authorization · error evidence · payment records · recovery attempted and failed · covered incident type · exclusions · no duplicate compensation · deductible / per-claim / aggregate limits · auto-approval limit (else human review).

### G. Audit
Hash-chained, append-only audit log (DB trigger blocks UPDATE/DELETE), correlation IDs on every request, JSON logs, OpenTelemetry tracing, transactional outbox → Redis Streams.

## 6. Demo scenarios (acceptance criteria)

| # | Scenario | Expected |
|---|---|---|
| 1 | Correct purchase | `COMPLETED`, execution verified, no incident |
| 2 | Duplicate purchase | 2nd attempt `BLOCKED: DUPLICATE_EXECUTION`; retry with same idempotency key returns original |
| 3 | Wrong quantity | `BLOCKED: QUANTITY_EXCEEDED`, never reaches PSP |
| 4 | Excessive amount | `AWAITING_APPROVAL: BUDGET_EXCEEDED`; approve → executes, reject → blocked |
| 5 | Incorrect completed purchase | Merchant ships 2 → `QUANTITY_MISMATCH` incident → refund → `REFUNDED`, recovered funds |
| 6 | Merchant refund fails | Wrong size → refund declined → claim `PAID` (simulated), disputed − deductible |

## 7. Success metrics
Prevented loss, recovered amount, simulated payouts, incident MTTR, % claims auto-decided, false-block rate.

## 8. Future work
Real PSP / AP2 credential-provider adapters, customer-side signatures (passkeys / verifiable credentials), per-merchant risk scoring, chargeback integration, multi-currency FX, licensed insurance partner.
