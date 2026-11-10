# API contracts

Full machine-readable contract: [`openapi.json`](./openapi.json) (also served live at `http://localhost:8000/docs`).

## Authentication

| Surface | Header | Notes |
|---|---|---|
| Agent API `/v1/agent/*` | `X-Agent-Key: agk_…` | Issued once by `POST /v1/agents`; only SHA-256 stored |
| Webhooks `/v1/webhooks/{payment\|merchant}` | `X-Signature: hex(HMAC-SHA256(raw body, AG_WEBHOOK_SECRET))` | |
| Operator API (everything else under `/v1`) | `X-Admin-Key` | Enforced only when `AG_ADMIN_API_KEY` is set |

Every response carries `X-Correlation-ID` (send your own to correlate). Errors: `{"error": "<code>", "message": "…", "details": {…}}` with 401 / 404 / 409 / 422.

## Agent: submit a purchase attempt

`POST /v1/agent/transactions` → `201 Created` (new) or `200 OK` (idempotent replay)

```json
{
  "idempotency_key": "order-7f3a-attempt-1",
  "mandate_id": "man_…",
  "merchant_id": "mer_stride",
  "sku": "RUN-PEG-38",
  "product_name": "Pegasus Trail Running Shoe",
  "product_category": "running shoes",
  "attributes": {"size": "38"},
  "quantity": 1,
  "unit_amount_cents": 12990,
  "amount_cents": 12990,
  "currency": "USD",
  "auto_execute": true
}
```

```json
{
  "transaction_id": "txn_…",
  "state": "BLOCKED",
  "decision": "BLOCK",
  "violations": [
    {"code": "QUANTITY_EXCEEDED", "severity": "BLOCK", "message": "Quantity exceeds mandate", "expected": "1", "actual": "2"}
  ],
  "replayed": false
}
```

Violation codes: `MANDATE_MISSING`, `SIGNATURE_INVALID`, `MANDATE_REVOKED`, `MANDATE_EXPIRED`, `AGENT_NOT_DELEGATED`, `DUPLICATE_EXECUTION`, `REPEATED_ATTEMPTS`, `MERCHANT_NOT_AUTHORIZED`, `PRODUCT_MISMATCH`, `QUANTITY_EXCEEDED`, `AMOUNT_INCONSISTENT`, `CURRENCY_MISMATCH`, `BUDGET_HARD_LIMIT` (block), `BUDGET_EXCEEDED` (approval).

Same `idempotency_key` with a different payload → `409 conflict`.

## Mandates

`POST /v1/mandates`

```json
{
  "customer_id": "cus_…",
  "agent_id": "agt_…",
  "intent_text": "Buy one pair of running shoes, size 38, for no more than $150.",
  "constraints": {
    "max_amount_cents": 15000, "currency": "USD", "max_quantity": 1, "max_executions": 1,
    "allowed_merchants": ["mer_stride"],
    "product": {"category": "running shoes", "required_attributes": {"size": "38"}, "sku_allowlist": null},
    "over_budget_action": "REQUIRE_APPROVAL", "hard_ceiling_pct": 50
  },
  "expires_at": "2026-12-01T00:00:00Z"
}
```

Response includes `current_version`, `content_hash`, `signature`, `signature_valid`. `POST /v1/mandates/{id}/amend` appends a version; `POST /v1/mandates/{id}/revoke` `{"reason": "…"}`.

`POST /v1/intents/extract` `{"text": "…"}` → `{"proposal": {…}, "provider": "rules|anthropic|openai", "advisory": true}` — a proposal only; it authorizes nothing.

## Customer decisions

`POST /v1/transactions/{id}/approve` · `POST /v1/transactions/{id}/reject` — body `{"customer_id": "cus_…"}`. Approval re-runs every rule except the budget-approval rule.

## Webhooks

`POST /v1/webhooks/payment` · `POST /v1/webhooks/merchant`

```json
{"event_id": "evt_…", "type": "payment.captured", "transaction_id": "txn_…", "sequence": 2, "payload": {"amount_cents": 12990}}
```

Types: `payment.authorized`, `payment.captured`, `payment.failed`, `order.fulfilled` (`payload.order` = sku, product_category, attributes, quantity, status), `refund.succeeded`, `refund.failed`.
Response `{"status": "APPLIED" | "DUPLICATE" | "DEFERRED" | "STALE" | "REJECTED"}` — always `200` once the signature is valid, so providers stop retrying.

## Operations

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/metrics/overview` | prevented loss, recovered, simulated payouts, counts |
| GET | `/v1/transactions?state=` · `/v1/transactions/{id}` | list / detail with state history and provider events |
| GET | `/v1/state-machine` | allowed transitions |
| GET | `/v1/incidents` · `/v1/incidents/{id}` | evidence, narrative, recovery actions, claim |
| GET | `/v1/recovery` · POST `/v1/recovery/run-due` | recovery queue; trigger due retries |
| GET | `/v1/claims` · POST `/v1/claims/{id}/review` | `{"approve": true, "reviewer": "ops@…", "note": "…", "amount_cents": 1000}` (capped by policy) |
| GET | `/v1/policies/protection` · `/v1/ledger` | policy config; balanced sandbox ledger |
| GET | `/v1/audit?entity_id=&correlation_id=&action=` · `/v1/audit/verify` | audit trail; hash-chain verification |
| GET | `/v1/stream` | Server-Sent Events from the outbox |
| POST | `/v1/demo/scenarios/{1..6}` | run a demo scenario end-to-end (disable with `AG_ENABLE_DEMO_ENDPOINTS=false`) |

## MCP

`python -m app.mcp_server` (stdio) exposes `guarded_purchase` and `transaction_status`, authenticated with `AGENTGUARD_AGENT_KEY`.
