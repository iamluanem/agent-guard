# Architecture

## System context

```mermaid
flowchart LR
    Customer((Customer)) -- intent / approve / revoke --> Dash[Dashboard<br/>Next.js]
    Agent[AI shopping agent] -- MCP tool: guarded_purchase --> MCP[AgentGuard MCP server]
    Agent -- REST + X-Agent-Key --> API
    MCP --> API
    Dash -- REST + SSE --> API

    subgraph AgentGuard
      API[FastAPI API] --> Engine[Deterministic<br/>policy engine]
      API --> LLM[LLM adapter<br/>advisory only]
      API --> DB[(PostgreSQL)]
      Worker[Worker] --> DB
      Worker -- outbox relay --> Redis[(Redis Streams)]
    end

    API -- charge(idempotency key) --> PSP[Payment provider adapter<br/>mock / real]
    API -- cancel / refund --> Merchant[Merchant API adapter<br/>simulated / real]
    PSP -- signed webhooks --> API
    Merchant -- signed webhooks --> API
    API -. OTLP .-> Jaeger[(Tracing)]
```

## Boundaries

| Layer | Responsibility | May move money? |
|---|---|---|
| `adapters/llm.py` | Interpret intent, explain incidents | **No** — output is schema-validated and advisory |
| `domain/policy.py` | Pure, deterministic rules (no I/O, no clock) | Decides, never executes |
| `domain/state_machine.py` | Single source of valid transitions | — |
| `services/transactions.py` | Idempotency, locking, slot claiming, execution | Via payment adapter |
| `services/events.py` | Webhook ingestion, ordering, post-execution verification | — |
| `services/recovery.py` | Cancellation / refund / retry / escalation | Via merchant adapter |
| `services/claims.py` | Eligibility + limits, sandbox payout | Sandbox ledger only |
| `services/audit.py` | Hash-chained audit + outbox | — |

## Purchase flow

```mermaid
sequenceDiagram
    participant A as Agent
    participant G as AgentGuard
    participant P as PSP (mock)
    participant M as Merchant (sim)
    A->>G: POST /v1/agent/transactions (idempotency_key)
    G->>G: replay check → lock mandate → verify signature → evaluate rules
    alt BLOCK
        G-->>A: BLOCKED + violations
    else REQUIRE_APPROVAL
        G-->>A: AWAITING_APPROVAL (customer decides in dashboard)
    else ALLOW
        G->>G: claim execution slot (UNIQUE mandate_id, slot)
        G->>P: charge(idempotency_key = transaction id)
        P-->>G: webhook payment.authorized / payment.captured
        M-->>G: webhook order.fulfilled
        G->>G: compare captured + fulfilled vs mandate
        alt mismatch
            G->>G: incident → DISPUTED → RECOVERY_PENDING
            G->>M: cancel or refund
            M-->>G: refund.succeeded → REFUNDED (ledger: merchant → customer)
            M-->>G: refund.failed → escalate → claim → sandbox payout
        end
    end
```

## Duplicate-execution safety (defence in depth)

1. **Idempotency key** — `UNIQUE(transactions.idempotency_key)`; same key + same payload hash ⇒ original result; same key + different payload ⇒ `409`.
2. **Mandate row lock** — `SELECT … FOR UPDATE` serializes attempts per mandate, with a double-checked idempotency lookup after acquiring the lock.
3. **Execution slots** — `UNIQUE(mandate_id, execution_slot)`. If the lock were bypassed, the database still rejects a second execution; the engine converts that into `BLOCKED: DUPLICATE_EXECUTION`.
4. **PSP idempotency** — the payment adapter is called with the transaction id as its idempotency key.

Verified by `tests/test_concurrency.py` (12 parallel threads ⇒ exactly one `AUTHORIZED`).

## Webhook processing

- HMAC-SHA256 signature over the raw body (`X-Signature`).
- `UNIQUE(provider_events.event_id)` ⇒ at-least-once delivery is safe (`DUPLICATE`).
- Per-transaction, per-source sequence cursor: `seq ≤ cursor` ⇒ `STALE`; `seq > cursor + 1` ⇒ `DEFERRED`, drained automatically when the gap fills; the worker force-applies deferred events after `AG_DEFERRED_EVENT_TIMEOUT_S`.

## Tamper evidence

Each audit record stores `sha256(prev_hash + canonical(record))`. `GET /v1/audit/verify` recomputes the chain and reports the first broken sequence. On PostgreSQL a trigger rejects `UPDATE`/`DELETE` on `audit_records`. Mandate versions are HMAC-signed and re-verified at every evaluation, so editing a mandate row directly yields `SIGNATURE_INVALID`.

## Observability

- `X-Correlation-ID` accepted or generated per request, propagated into audit records, outbox events and JSON logs.
- OpenTelemetry FastAPI instrumentation when `AG_OTEL_EXPORTER_ENDPOINT` is set (`docker compose --profile observability up` starts Jaeger).
- Transactional outbox (`domain_events`) → Redis Stream `agentguard.events`; the dashboard consumes SSE from `/v1/stream`.

## AWS deployment shape

| Component | AWS |
|---|---|
| API, worker | ECS Fargate services (same image), ALB in front of the API |
| PostgreSQL | RDS for PostgreSQL (Multi-AZ) |
| Redis | ElastiCache for Redis |
| Secrets (`AG_SIGNING_SECRET`, `AG_WEBHOOK_SECRET`, LLM keys) | Secrets Manager → ECS task secrets; signing could move to KMS (asymmetric keys) |
| Dashboard | Amplify Hosting or ECS + CloudFront |
| Tracing / logs | ADOT collector → X-Ray; CloudWatch Logs (JSON) |
| Webhooks ingress | ALB + WAF; optional SQS buffer in front of ingestion |

## Simulated vs real

Simulated components are isolated behind ports (`PaymentProvider`, `MerchantApi`) and flagged `simulated = True`. Every ledger row is `sandbox = true`; the API and dashboard label all payouts as simulated.
