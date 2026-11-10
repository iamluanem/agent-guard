# AgentGuard

**Financial protection for autonomous AI transactions.** AgentGuard sits between AI shopping agents and payment execution. It validates every purchase against a signed customer mandate, detects execution errors after the fact, recovers funds from merchants, and evaluates protection claims when recovery fails.

> AI agents can make mistakes. Customers need verifiable protection when they do.

⚠️ **Sandbox MVP.** Payments, merchants and reimbursements are simulated. Every ledger entry is flagged `sandbox`. Nothing here is real insurance coverage.

## What it does

| Stage | Capability |
|---|---|
| **Authorize** | Signed, versioned, revocable mandates (budget, merchants, product, quantity, executions, expiry) |
| **Prevent** | Deterministic policy engine: `ALLOW` / `REQUIRE_APPROVAL` / `BLOCK`, with idempotency keys and concurrency-safe duplicate detection |
| **Detect** | Signed webhooks (dedupe, out-of-order, stale), state machine, post-execution comparison of captured payment + fulfilled order vs mandate |
| **Recover** | Incident → cancellation or refund → retries with backoff → escalation |
| **Protect** | Configurable claims engine (deductible, limits, exclusions, duplicate compensation, human review) paying out from a balanced sandbox ledger |
| **Prove** | Hash-chained, append-only audit log, correlation IDs, JSON logs, OpenTelemetry |

The LLM is **advisory only**: it proposes mandate constraints from natural language and writes incident narratives. It never authorizes payments, refunds or payouts.

## Quick start (Docker)

```bash
cp .env.example .env
# replace each "change-me" with: python -c "import secrets; print(secrets.token_urlsafe(32))"
docker compose up --build
```

- API + OpenAPI docs: http://localhost:8000/docs
- Migrations and seed data run automatically (`migrate` service).
- Tracing: `docker compose --profile observability up` and set `AG_OTEL_EXPORTER_ENDPOINT=http://jaeger:4318` → Jaeger at http://localhost:16686

## Run the six demo scenarios

```bash
for n in 1 2 3 4 5 6; do
  curl -s -X POST localhost:8000/v1/demo/scenarios/$n | python -m json.tool | head -20
done
curl -s localhost:8000/v1/metrics/overview | python -m json.tool
curl -s localhost:8000/v1/audit/verify
```

| # | Scenario | Result |
|---|---|---|
| 1 | Correct purchase | `COMPLETED`, execution verified |
| 2 | Duplicate purchase | second attempt `BLOCKED: DUPLICATE_EXECUTION`; same idempotency key replays the original |
| 3 | Wrong quantity | `BLOCKED: QUANTITY_EXCEEDED` (never reaches the PSP) |
| 4 | Excessive amount | `AWAITING_APPROVAL`; approve via `POST /v1/transactions/{id}/approve` |
| 5 | Incorrect completed purchase | merchant shipped 2 → incident → refund → `REFUNDED` (**recovered funds**) |
| 6 | Merchant refund fails | wrong size, refund declined → claim → **simulated payout** (disputed − deductible) |

Overview metrics keep **prevented loss**, **recovered funds** and **simulated protection payouts** separate.

## Integrate an agent

```bash
# 1. register an agent (the key is shown once)
curl -s -X POST localhost:8000/v1/agents -H 'content-type: application/json' \
  -d '{"name":"ShopPilot","operator":"Acme"}'
# 2. propose constraints from the customer's words (advisory)
curl -s -X POST localhost:8000/v1/intents/extract -H 'content-type: application/json' \
  -d '{"text":"Buy one pair of running shoes, size 38, for no more than $150."}'
# 3. customer confirms → POST /v1/mandates ; 4. agent calls POST /v1/agent/transactions with X-Agent-Key
```

Or give the agent the MCP server (tools `guarded_purchase`, `transaction_status`):

```json
{"mcpServers": {"agentguard": {"command": "python", "args": ["-m", "app.mcp_server"],
  "cwd": "backend", "env": {"AGENTGUARD_URL": "http://localhost:8000", "AGENTGUARD_AGENT_KEY": "agk_..."}}}}
```

## Local development (without Docker)

Requires Python 3.12+, PostgreSQL 16 and (optionally) Redis.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
export AG_SIGNING_SECRET=dev-sign AG_WEBHOOK_SECRET=dev-hook \
       AG_DATABASE_URL=postgresql+psycopg://agentguard:agentguard@localhost:5432/agentguard
alembic upgrade head && python -m app.seed
uvicorn app.main:app --reload          # API
python -m app.worker                   # outbox relay, recovery retries, deferred-event sweeper
```

## Tests

```bash
cd backend
pytest                                                    # SQLite
AG_TEST_DATABASE_URL=postgresql+psycopg://…/agentguard_test pytest   # PostgreSQL, incl. concurrency
ruff check .
```

53 tests: policy rules, state machine, intent extraction, all six scenarios through HTTP, idempotency, webhook signature / dedupe / ordering, mandate tampering, retries → escalation, human claim review, audit-chain tampering, and 12-thread races proving exactly-once execution. CI runs them on PostgreSQL and SQLite, checks that migrations match the models, and builds the image.

## Repository layout

```
backend/
  app/domain/        pure policy engine + state machine (no I/O)
  app/services/      mandates, transactions, events, recovery, claims, ledger, audit, scenarios
  app/adapters/      payment / merchant ports + sandbox implementations, LLM providers
  app/api/           FastAPI routes
  app/mcp_server.py  MCP tools for agents
  app/worker.py      outbox → Redis Streams, retries, sweeper
  migrations/        Alembic
  tests/
docs/                PRD, architecture, database schema, API contracts, OpenAPI
```

## Documentation

- [Product Requirements](docs/PRD.md)
- [Architecture](docs/ARCHITECTURE.md) — diagrams, boundaries, exactly-once design, AWS mapping
- [Database schema](docs/DATABASE.md)
- [API contracts](docs/API.md) · [OpenAPI](docs/openapi.json)

## Configuration

All settings use the `AG_` prefix (see `backend/app/config.py` and `.env.example`). No secret has a default value: the app refuses to start without `AG_SIGNING_SECRET` and `AG_WEBHOOK_SECRET`.
