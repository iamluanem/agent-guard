import asyncio
import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.adapters import llm
from app.adapters.payments import WebhookEvent
from app.api.deps import SessionDep, require_admin, row
from app.config import get_settings
from app.db import session_factory
from app.domain.enums import ClaimStatus, IncidentStatus, TxState
from app.domain.policy import MandateConstraints
from app.domain.state_machine import TRANSITIONS
from app.models import (
    Agent,
    AuditRecord,
    Claim,
    Customer,
    DomainEvent,
    Incident,
    LedgerEntry,
    Mandate,
    MandateVersion,
    Merchant,
    Notification,
    ProtectionPolicy,
    ProviderEvent,
    RecoveryAction,
    Transaction,
    TransactionStateChange,
)
from app.services import audit, claims, events, ledger, mandates, recovery, scenarios, signing, transactions
from app.services.errors import NotFound, Unauthorized
from app.services.transactions import AttemptRequest, AttemptResult

public = APIRouter()
agent_api = APIRouter(prefix="/v1/agent", tags=["agent"])
webhooks = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])
ops = APIRouter(prefix="/v1", dependencies=[Depends(require_admin)])


@public.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------- identity & authorization
class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AgentIn(BaseModel):
    name: str
    operator: str


class MandateIn(BaseModel):
    customer_id: str
    agent_id: str
    intent_text: str
    constraints: MandateConstraints
    expires_at: datetime | None = None


class AmendIn(BaseModel):
    intent_text: str
    constraints: MandateConstraints
    expires_at: datetime | None = None


class RevokeIn(BaseModel):
    reason: str = "revoked by customer"


@ops.post("/customers", tags=["identity"], status_code=201)
def create_customer(body: CustomerIn, s: SessionDep) -> dict:
    c = mandates.create_customer(s, body.name, body.email)
    s.commit()
    return row(c)


@ops.get("/customers", tags=["identity"])
def list_customers(s: SessionDep) -> list[dict]:
    return [row(c) for c in s.execute(select(Customer).order_by(Customer.created_at.desc())).scalars()]


@ops.post("/agents", tags=["identity"], status_code=201)
def create_agent(body: AgentIn, s: SessionDep) -> dict:
    a, raw = mandates.register_agent(s, body.name, body.operator)
    s.commit()
    return {**row(a, "api_key_hash"), "api_key": raw, "note": "store this key now; it is not retrievable"}


@ops.get("/agents", tags=["identity"])
def list_agents(s: SessionDep) -> list[dict]:
    return [row(a, "api_key_hash") for a in s.execute(select(Agent)).scalars()]


@ops.get("/merchants", tags=["identity"])
def list_merchants(s: SessionDep) -> list[dict]:
    return [row(m) for m in s.execute(select(Merchant)).scalars()]


class IntentIn(BaseModel):
    text: str = Field(min_length=3, max_length=2000)


@ops.post("/intents/extract", tags=["ai"])
def extract_intent(body: IntentIn) -> dict:
    """LLM-assisted proposal of mandate constraints. ADVISORY: the customer must confirm it via POST /v1/mandates."""
    result, provider = llm.extract_intent(body.text)
    return {"proposal": result.model_dump(), "provider": provider, "advisory": True,
            "note": "Not an authorization. Submit confirmed constraints to POST /v1/mandates."}


def _mandate_view(s, m: Mandate) -> dict:
    mv = mandates.current_version(s, m)
    cust = s.get(Customer, m.customer_id)
    return {**row(m), "customer_name": cust.name if cust else None, "intent_text": mv.intent_text,
            "constraints": mv.constraints, "expires_at": mv.expires_at, "content_hash": mv.content_hash,
            "signature": mv.signature, "signature_valid": mandates.verify_version(m, mv)}


@ops.post("/mandates", tags=["authorization"], status_code=201)
def create_mandate(body: MandateIn, s: SessionDep) -> dict:
    m, _ = mandates.create_mandate(s, customer_id=body.customer_id, agent_id=body.agent_id,
                                   intent_text=body.intent_text, constraints=body.constraints,
                                   expires_at=body.expires_at)
    s.commit()
    return _mandate_view(s, m)


@ops.get("/mandates", tags=["authorization"])
def list_mandates(s: SessionDep, limit: int = Query(100, le=500)) -> list[dict]:
    ms = s.execute(select(Mandate).order_by(Mandate.created_at.desc()).limit(limit)).scalars().all()
    return [_mandate_view(s, m) for m in ms]


@ops.get("/mandates/{mandate_id}", tags=["authorization"])
def get_mandate(mandate_id: str, s: SessionDep) -> dict:
    m = s.get(Mandate, mandate_id)
    if m is None:
        raise NotFound("mandate not found")
    versions = s.execute(select(MandateVersion).where(MandateVersion.mandate_id == m.id)
                         .order_by(MandateVersion.version)).scalars()
    return {**_mandate_view(s, m), "versions": [row(v) for v in versions]}


@ops.post("/mandates/{mandate_id}/amend", tags=["authorization"])
def amend(mandate_id: str, body: AmendIn, s: SessionDep) -> dict:
    mandates.amend_mandate(s, mandate_id, body.intent_text, body.constraints, body.expires_at)
    s.commit()
    return get_mandate(mandate_id, s)


@ops.post("/mandates/{mandate_id}/revoke", tags=["authorization"])
def revoke(mandate_id: str, body: RevokeIn, s: SessionDep) -> dict:
    m = mandates.revoke_mandate(s, mandate_id, body.reason)
    s.commit()
    return _mandate_view(s, m)


# ---------------------------------------------------------------- agent-facing protection API
@agent_api.post("/transactions", response_model=AttemptResult)
def submit_transaction(body: AttemptRequest, s: SessionDep, response: Response,
                       x_agent_key: Annotated[str | None, Header()] = None) -> AttemptResult:
    """Validate (and optionally execute) a purchase attempt. Retrying with the same idempotency_key is safe."""
    agent = mandates.authenticate_agent(s, x_agent_key)
    res, evts = transactions.submit_attempt(s, agent, body)
    s.commit()
    events.deliver(evts)
    response.status_code = 200 if res.replayed else 201
    with session_factory()() as s2:  # reflect state after simulated webhooks
        res.state = TxState(s2.get(Transaction, res.transaction_id).state)
    return res


@agent_api.get("/transactions/{tx_id}")
def agent_get_transaction(tx_id: str, s: SessionDep, x_agent_key: Annotated[str | None, Header()] = None) -> dict:
    """An agent can read only its own transactions."""
    agent = mandates.authenticate_agent(s, x_agent_key)
    t = s.get(Transaction, tx_id)
    if t is None or t.agent_id != agent.id:
        raise NotFound("transaction not found")
    return {k: v for k, v in row(t).items() if k in {"id", "state", "decision", "violations", "amount_cents",
                                                       "currency", "payment_ref", "created_at", "updated_at"}}


# ---------------------------------------------------------------- monitoring
@ops.get("/transactions", tags=["transactions"])
def list_transactions(s: SessionDep, state: TxState | None = None, limit: int = Query(200, le=1000)) -> list[dict]:
    q = select(Transaction).order_by(Transaction.created_at.desc()).limit(limit)
    if state:
        q = q.where(Transaction.state == state)
    return [row(t, "request_hash") for t in s.execute(q).scalars()]


@ops.get("/transactions/{tx_id}", tags=["transactions"])
def get_transaction(tx_id: str, s: SessionDep) -> dict:
    t = s.get(Transaction, tx_id)
    if t is None:
        raise NotFound("transaction not found")
    hist = s.execute(select(TransactionStateChange).where(TransactionStateChange.transaction_id == tx_id)
                     .order_by(TransactionStateChange.id)).scalars()
    evs = s.execute(select(ProviderEvent).where(ProviderEvent.transaction_id == tx_id)
                    .order_by(ProviderEvent.id)).scalars()
    inc = s.execute(select(Incident).where(Incident.transaction_id == tx_id)).scalar_one_or_none()
    return {**row(t, "request_hash"), "history": [row(h) for h in hist], "provider_events": [row(e) for e in evs],
            "incident_id": inc.id if inc else None}


@ops.get("/state-machine", tags=["transactions"])
def state_machine() -> dict:
    return {k.value: sorted(v.value for v in vs) for k, vs in TRANSITIONS.items()}


class DecisionIn(BaseModel):
    customer_id: str


@ops.post("/transactions/{tx_id}/approve", tags=["transactions"], response_model=AttemptResult)
def approve(tx_id: str, body: DecisionIn, s: SessionDep) -> AttemptResult:
    res, evts = transactions.approve(s, tx_id, body.customer_id)
    s.commit()
    events.deliver(evts)
    with session_factory()() as s2:
        res.state = TxState(s2.get(Transaction, tx_id).state)
    return res


@ops.post("/transactions/{tx_id}/reject", tags=["transactions"], response_model=AttemptResult)
def reject(tx_id: str, body: DecisionIn, s: SessionDep) -> AttemptResult:
    res = transactions.reject(s, tx_id, body.customer_id)
    s.commit()
    return res


class WebhookIn(BaseModel):
    event_id: str
    type: str
    transaction_id: str
    sequence: int = Field(ge=1)
    payload: dict = {}


@webhooks.post("/{source}")
async def receive_webhook(source: Literal["payment", "merchant"], request: Request,
                          x_signature: Annotated[str | None, Header()] = None) -> dict:
    """Provider webhooks (HMAC-SHA256 of the raw body in X-Signature). At-least-once delivery is safe."""
    raw = await request.body()
    if not signing.verify_webhook(raw, x_signature):
        raise Unauthorized("invalid webhook signature")
    body = WebhookIn.model_validate_json(raw)
    evt = WebhookEvent(event_id=body.event_id, source=source, type=body.type, transaction_id=body.transaction_id,
                       sequence=body.sequence, payload=body.payload)

    def _run() -> str:
        with session_factory()() as s:
            status, follow = events.ingest(s, evt)
            s.commit()
        events.deliver(follow)
        return status

    return {"status": await asyncio.to_thread(_run)}


# ---------------------------------------------------------------- incidents, recovery, claims
@ops.get("/incidents", tags=["incidents"])
def list_incidents(s: SessionDep, status: IncidentStatus | None = None) -> list[dict]:
    q = select(Incident).order_by(Incident.detected_at.desc())
    if status:
        q = q.where(Incident.status == status)
    return [row(i) for i in s.execute(q).scalars()]


@ops.get("/incidents/{inc_id}", tags=["incidents"])
def get_incident(inc_id: str, s: SessionDep) -> dict:
    i = s.get(Incident, inc_id)
    if i is None:
        raise NotFound("incident not found")
    acts = s.execute(select(RecoveryAction).where(RecoveryAction.incident_id == inc_id)
                     .order_by(RecoveryAction.created_at)).scalars()
    c = s.execute(select(Claim).where(Claim.incident_id == inc_id)).scalar_one_or_none()
    return {**row(i), "recovery_actions": [row(a) for a in acts], "claim": row(c) if c else None}


@ops.get("/recovery", tags=["recovery"])
def list_recovery(s: SessionDep) -> list[dict]:
    rows = s.execute(select(RecoveryAction, Incident).join(Incident, Incident.id == RecoveryAction.incident_id)
                     .order_by(RecoveryAction.created_at.desc())).all()
    return [{**row(a), "transaction_id": i.transaction_id, "incident_type": i.type,
             "incident_status": i.status, "currency": i.currency} for a, i in rows]


@ops.post("/recovery/run-due", tags=["recovery"])
def run_due(s: SessionDep) -> dict:
    evts = recovery.run_due(s)
    s.commit()
    events.deliver(evts)
    return {"delivered_events": len(evts)}


@ops.get("/claims", tags=["claims"])
def list_claims(s: SessionDep, status: ClaimStatus | None = None) -> list[dict]:
    q = select(Claim).order_by(Claim.created_at.desc())
    if status:
        q = q.where(Claim.status == status)
    return [row(c) for c in s.execute(q).scalars()]


@ops.get("/claims/{claim_id}", tags=["claims"])
def get_claim(claim_id: str, s: SessionDep) -> dict:
    c = s.get(Claim, claim_id)
    if c is None:
        raise NotFound("claim not found")
    return row(c)


class ReviewIn(BaseModel):
    approve: bool
    reviewer: str = Field(min_length=1)
    note: str = ""
    amount_cents: int | None = Field(default=None, ge=0)


@ops.post("/claims/{claim_id}/review", tags=["claims"])
def review_claim(claim_id: str, body: ReviewIn, s: SessionDep) -> dict:
    c = claims.review(s, claim_id, approve=body.approve, reviewer=body.reviewer, note=body.note,
                      amount_cents=body.amount_cents)
    s.commit()
    return row(c)


@ops.get("/policies/protection", tags=["claims"])
def protection_policies(s: SessionDep) -> list[dict]:
    return [row(p) for p in s.execute(select(ProtectionPolicy)).scalars()]


@ops.get("/ledger", tags=["claims"])
def ledger_entries(s: SessionDep, limit: int = Query(200, le=1000)) -> dict:
    rows = s.execute(select(LedgerEntry).order_by(LedgerEntry.id.desc()).limit(limit)).scalars()
    return {"sandbox": True, "balanced": ledger.is_balanced(s), "entries": [row(e) for e in rows]}


# ---------------------------------------------------------------- audit, metrics, stream
@ops.get("/audit", tags=["audit"])
def audit_trail(s: SessionDep, entity_id: str | None = None, correlation_id: str | None = None,
                action: str | None = None, limit: int = Query(300, le=2000)) -> list[dict]:
    q = select(AuditRecord).order_by(AuditRecord.seq.desc()).limit(limit)
    if entity_id:
        q = q.where(AuditRecord.entity_id == entity_id)
    if correlation_id:
        q = q.where(AuditRecord.correlation_id == correlation_id)
    if action:
        q = q.where(AuditRecord.action.like(f"{action}%"))
    return [row(r) for r in s.execute(q).scalars()]


@ops.get("/audit/verify", tags=["audit"])
def audit_verify(s: SessionDep) -> dict:
    return audit.verify_chain(s)


@ops.get("/metrics/overview", tags=["metrics"])
def overview(s: SessionDep) -> dict:
    def count(q):
        return int(s.execute(q).scalar_one())

    def amount(state):
        return count(select(func.coalesce(func.sum(Transaction.amount_cents), 0)).where(Transaction.state == state))

    by_state = dict(s.execute(select(Transaction.state, func.count()).group_by(Transaction.state)).all())
    return {
        "transactions_monitored": count(select(func.count()).select_from(Transaction)),
        "by_state": by_state,
        "blocked_attempts": by_state.get(TxState.BLOCKED, 0),
        "awaiting_approval": by_state.get(TxState.AWAITING_APPROVAL, 0),
        "incidents_detected": count(select(func.count()).select_from(Incident)),
        "incidents_open": count(select(func.count()).select_from(Incident).where(
            Incident.status.in_([IncidentStatus.OPEN, IncidentStatus.RECOVERING, IncidentStatus.ESCALATED]))),
        "prevented_loss_cents": amount(TxState.BLOCKED),
        "recovered_cents": ledger.total(s, claims.REFUND),
        "simulated_payouts_cents": ledger.total(s, claims.PAYOUT),
        "claims_in_review": count(select(func.count()).select_from(Claim).where(Claim.status == ClaimStatus.IN_REVIEW)),
        "volume_authorized_cents": count(select(func.coalesce(func.sum(Transaction.amount_cents), 0)).where(
            Transaction.execution_slot.is_not(None))),
        "currency": "USD",
    }


@ops.get("/notifications", tags=["audit"])
def notifications(s: SessionDep, customer_id: str | None = None) -> list[dict]:
    q = select(Notification).order_by(Notification.id.desc()).limit(200)
    if customer_id:
        q = q.where(Notification.customer_id == customer_id)
    return [row(n) for n in s.execute(q).scalars()]


@ops.get("/stream", tags=["metrics"])
async def stream(request: Request, after: int = 0) -> StreamingResponse:
    """Server-Sent Events from the transactional outbox (domain_events)."""

    def fetch(cursor: int) -> list[DomainEvent]:
        with session_factory()() as s:
            return list(s.execute(select(DomainEvent).where(DomainEvent.id > cursor)
                                  .order_by(DomainEvent.id).limit(100)).scalars())

    async def gen():
        cursor = after
        if cursor == 0:
            with session_factory()() as s:
                cursor = s.execute(select(func.coalesce(func.max(DomainEvent.id), 0))).scalar_one()
        while not await request.is_disconnected():
            for e in await asyncio.to_thread(fetch, cursor):
                cursor = e.id
                yield f"id: {e.id}\ndata: {json.dumps(row(e), default=str)}\n\n"
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------- demo
@ops.post("/demo/scenarios/{n}", tags=["demo"], response_model=scenarios.ScenarioResult)
def run_scenario(n: int) -> scenarios.ScenarioResult:
    if not get_settings().enable_demo_endpoints:
        raise HTTPException(404)
    if n not in range(1, 7):
        raise HTTPException(422, "scenario must be 1..6")
    return scenarios.run(n)
