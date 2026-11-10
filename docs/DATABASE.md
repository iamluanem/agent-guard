# Database schema

PostgreSQL 16, managed by Alembic (`backend/migrations`). Money is always `BIGINT` minor units (cents). All timestamps are `timestamptz` in UTC.

```mermaid
erDiagram
    customers ||--o{ mandates : delegates
    agents ||--o{ mandates : "is delegate of"
    mandates ||--|{ mandate_versions : "immutable versions"
    mandates ||--o{ transactions : authorizes
    agents ||--o{ transactions : attempts
    transactions ||--o{ transaction_state_changes : history
    transactions ||--o{ provider_events : webhooks
    transactions ||--o| incidents : "may raise"
    incidents ||--o{ recovery_actions : "recovered by"
    incidents ||--o| claims : "escalates to"
    protection_policies ||--o{ claims : governs
    incidents ||--o{ ledger_entries : "sandbox money"

    mandates { string id PK; string customer_id FK; string agent_id FK; int current_version; string status; int executions_used }
    mandate_versions { string id PK; string mandate_id FK; int version; json constraints; timestamptz expires_at; string content_hash; string signature }
    transactions { string id PK; string idempotency_key UK; string request_hash; string mandate_id FK; int execution_slot; string state; string decision; json violations; bigint amount_cents; bigint captured_amount_cents; json fulfilled; json event_cursors }
    provider_events { int id PK; string event_id UK; string source; string type; int sequence; string status }
    incidents { string id PK; string transaction_id UK; string type; string status; bigint disputed_amount_cents; json evidence; text summary }
    recovery_actions { string id PK; string incident_id FK; string kind; string status; int attempts; timestamptz next_attempt_at }
    claims { string id PK; string incident_id UK; string status; bigint approved_amount_cents; json checks }
    protection_policies { string id PK; string code; int version; bigint deductible_cents; bigint per_claim_limit_cents; bigint aggregate_limit_cents; bigint auto_approve_limit_cents }
    ledger_entries { int id PK; string journal_id; string account; string direction; bigint amount_cents; string kind; bool sandbox }
    audit_records { int seq PK; string correlation_id; string action; string entity_id; json data; string prev_hash; string hash UK }
```

## Integrity constraints that carry business rules

| Constraint | Rule enforced |
|---|---|
| `transactions.idempotency_key UNIQUE` | One transaction per idempotency key |
| `transactions (mandate_id, execution_slot) UNIQUE` | A mandate allowing N executions can never execute N+1 times |
| `mandate_versions (mandate_id, version) UNIQUE` | Versions are append-only |
| `provider_events.event_id UNIQUE` | Webhook re-delivery is idempotent |
| `incidents.transaction_id UNIQUE`, `claims.incident_id UNIQUE` | One incident per transaction, one claim per incident |
| `audit_records.hash UNIQUE` + trigger `audit_records_no_update_delete` | Append-only, tamper-evident log |

## Other tables

`customers`, `agents` (stores `api_key_hash` only), `merchants` (simulation behaviour flags), `notifications`, `audit_head` (single-row lock that serializes hash-chain appends), `domain_events` (transactional outbox, `published_at` set by the worker).
