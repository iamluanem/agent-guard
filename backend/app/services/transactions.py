"""Transaction Protection Engine: validate -> authorize -> execute, idempotently and concurrency-safely.

Duplicate-execution safety has three layers:
  1. Idempotency key (unique): a retried request returns the original transaction, never a new one.
  2. Mandate row lock (SELECT ... FOR UPDATE): attempts against one mandate are serialized.
  3. UNIQUE(mandate_id, execution_slot): even if (2) were bypassed, two executions cannot
     claim the same slot of a single-use mandate — the database rejects the second.
"""
import hashlib
from datetime import timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.payments import ChargeRequest, WebhookEvent, payment_provider
from app.config import get_settings
from app.db import utcnow
from app.domain.enums import MandateStatus, Outcome, TxState
from app.domain.policy import AttemptFacts, MandateConstraints, MandateContext, PolicyDecision, evaluate
from app.domain.state_machine import assert_transition
from app.models import Agent, Mandate, Merchant, Transaction, TransactionStateChange
from app.observability import current_correlation_id
from app.services import audit
from app.services.errors import Conflict, InvalidState, NotFound
from app.services.mandates import current_version, verify_version


class AttemptRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    mandate_id: str | None = None
    merchant_id: str
    sku: str
    product_name: str
    product_category: str
    attributes: dict[str, str] = {}
    quantity: int = Field(ge=1)
    unit_amount_cents: int = Field(ge=0)
    amount_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    auto_execute: bool = True


class AttemptResult(BaseModel):
    transaction_id: str
    state: TxState
    decision: Outcome
    violations: list[dict]
    replayed: bool = False


def transition(s: Session, tx: Transaction, target: TxState, reason: str, actor: str = "engine") -> None:
    assert_transition(TxState(tx.state), target)
    s.add(TransactionStateChange(transaction_id=tx.id, from_state=tx.state, to_state=target, reason=reason))
    prev, tx.state = tx.state, target
    audit.record(s, actor=actor, action=f"transaction.{target.lower()}", entity_type="transaction",
                 entity_id=tx.id, data={"from": prev, "to": target, "reason": reason,
                                        "amount_cents": tx.amount_cents, "merchant_id": tx.merchant_id})


def _request_hash(req: AttemptRequest, agent_id: str) -> str:
    body = audit.canonical({**req.model_dump(exclude={"auto_execute"}), "agent_id": agent_id})
    return hashlib.sha256(body.encode()).hexdigest()


def _replay(s: Session, key: str, req_hash: str) -> AttemptResult | None:
    tx = s.execute(select(Transaction).where(Transaction.idempotency_key == key)).scalar_one_or_none()
    if tx is None:
        return None
    if tx.request_hash != req_hash:
        raise Conflict("idempotency key reused with a different payload", transaction_id=tx.id)
    return AttemptResult(transaction_id=tx.id, state=TxState(tx.state), decision=Outcome(tx.decision),
                         violations=tx.violations, replayed=True)


def _context(s: Session, m: Mandate | None, *, exclude_tx: str | None = None) -> MandateContext:
    now = utcnow()
    st = get_settings()
    if m is None:
        return MandateContext(exists=False, now=now)
    mv = current_version(s, m)
    since = now - timedelta(seconds=st.repeated_attempt_window_s)
    q = select(func.count()).select_from(Transaction).where(Transaction.mandate_id == m.id,
                                                            Transaction.created_at >= since)
    if exclude_tx:
        q = q.where(Transaction.id != exclude_tx)
    return MandateContext(
        exists=True, status=MandateStatus(m.status), agent_id=m.agent_id,
        constraints=MandateConstraints.model_validate(mv.constraints), expires_at=mv.expires_at,
        signature_valid=verify_version(m, mv), executions_used=m.executions_used,
        recent_attempts=s.execute(q).scalar_one(), repeated_attempt_limit=st.repeated_attempt_limit, now=now)


def _facts(tx_or_req, agent_id: str) -> AttemptFacts:
    r = tx_or_req
    return AttemptFacts(agent_id=agent_id, merchant_id=r.merchant_id, sku=r.sku,
                        product_category=r.product_category, attributes=r.attributes, quantity=r.quantity,
                        unit_amount_cents=r.unit_amount_cents, amount_cents=r.amount_cents, currency=r.currency)


def submit_attempt(s: Session, agent: Agent, req: AttemptRequest) -> tuple[AttemptResult, list[WebhookEvent]]:
    req_hash = _request_hash(req, agent.id)
    if (prev := _replay(s, req.idempotency_key, req_hash)) is not None:
        return prev, []

    m: Mandate | None = None
    if req.mandate_id:
        m = s.execute(select(Mandate).where(Mandate.id == req.mandate_id).with_for_update()).scalar_one_or_none()
        # Double-checked idempotency: a concurrent request with the same key may have committed
        # while we waited for the mandate lock.
        if (prev := _replay(s, req.idempotency_key, req_hash)) is not None:
            return prev, []

    ctx = _context(s, m)
    decision: PolicyDecision = evaluate(_facts(req, agent.id), ctx)
    tx = Transaction(
        idempotency_key=req.idempotency_key, request_hash=req_hash, correlation_id=current_correlation_id(),
        mandate_id=m.id if m else None, mandate_version=m.current_version if m else None, agent_id=agent.id,
        customer_id=m.customer_id if m else None, merchant_id=req.merchant_id, sku=req.sku,
        product_name=req.product_name, product_category=req.product_category, attributes=req.attributes,
        quantity=req.quantity, unit_amount_cents=req.unit_amount_cents, amount_cents=req.amount_cents,
        currency=req.currency.upper(), state=TxState.CREATED, decision=decision.outcome,
        violations=[v.model_dump() for v in decision.violations], event_cursors={})
    s.add(tx)
    try:
        s.flush()
    except IntegrityError:
        s.rollback()
        prev = _replay(s, req.idempotency_key, req_hash)
        if prev is None:
            raise
        return prev, []

    s.add(TransactionStateChange(transaction_id=tx.id, from_state=None, to_state=TxState.CREATED,
                                 reason="attempt received"))
    audit.record(s, actor=f"agent:{agent.id}", action="transaction.attempted", entity_type="transaction",
                 entity_id=tx.id, data={"mandate_id": tx.mandate_id, "mandate_version": tx.mandate_version,
                                        "request_hash": req_hash, "amount_cents": tx.amount_cents})
    audit.record(s, actor="policy-engine", action="policy.evaluated", entity_type="transaction", entity_id=tx.id,
                 data=decision.model_dump(mode="json"))

    events: list[WebhookEvent] = []
    if decision.outcome == Outcome.BLOCK:
        transition(s, tx, TxState.BLOCKED, ", ".join(sorted({v.code for v in decision.violations})))
    elif decision.outcome == Outcome.REQUIRE_APPROVAL:
        transition(s, tx, TxState.AWAITING_APPROVAL, "customer approval required")
    else:
        assert m is not None
        if not _claim_slot(s, m, tx):
            return _result(tx), []
        transition(s, tx, TxState.AUTHORIZED, "all policy rules passed")
        if req.auto_execute:
            events = execute(s, tx)
    return _result(tx), events


def _claim_slot(s: Session, m: Mandate, tx: Transaction) -> bool:
    """Claim the next execution slot. The UNIQUE constraint is the last line of defence:
    if a concurrent writer got the slot first, this attempt is blocked as a duplicate."""
    slot = m.executions_used + 1
    try:
        with s.begin_nested():
            tx.execution_slot = slot
            m.executions_used = slot
            s.flush()
        return True
    except IntegrityError:
        s.refresh(tx)
        tx.decision = Outcome.BLOCK
        tx.violations = [*tx.violations, {"code": "DUPLICATE_EXECUTION", "severity": "BLOCK", "expected": None,
                                          "actual": str(slot), "message": "Execution slot claimed concurrently"}]
        transition(s, tx, TxState.BLOCKED, "DUPLICATE_EXECUTION (unique slot constraint)")
        return False


def _result(tx: Transaction) -> AttemptResult:
    return AttemptResult(transaction_id=tx.id, state=TxState(tx.state), decision=Outcome(tx.decision),
                         violations=tx.violations)


def execute(s: Session, tx: Transaction) -> list[WebhookEvent]:
    transition(s, tx, TxState.EXECUTING, "submitting to payment provider")
    merchant = s.get(Merchant, tx.merchant_id)
    behavior = merchant.fulfillment_behavior if merchant else "correct"
    res = payment_provider().charge(ChargeRequest(
        transaction_id=tx.id, idempotency_key=tx.id, merchant_id=tx.merchant_id, amount_cents=tx.amount_cents,
        currency=tx.currency, sku=tx.sku, product_category=tx.product_category, attributes=tx.attributes,
        quantity=tx.quantity, unit_amount_cents=tx.unit_amount_cents), behavior)
    if not res.accepted:
        transition(s, tx, TxState.FAILED, f"provider rejected: {res.error}")
        return []
    tx.payment_ref = res.payment_ref
    audit.record(s, actor="payment-provider", action="payment.submitted", entity_type="transaction",
                 entity_id=tx.id, data={"payment_ref": res.payment_ref, "simulated": True})
    return res.events


def get_locked(s: Session, tx_id: str) -> Transaction:
    tx = s.execute(select(Transaction).where(Transaction.id == tx_id).with_for_update()).scalar_one_or_none()
    if tx is None:
        raise NotFound("transaction not found")
    return tx


def approve(s: Session, tx_id: str, customer_id: str) -> tuple[AttemptResult, list[WebhookEvent]]:
    tx = get_locked(s, tx_id)
    if tx.customer_id != customer_id:
        raise NotFound("transaction not found")
    if tx.state != TxState.AWAITING_APPROVAL:
        raise InvalidState(f"transaction is {tx.state}, not AWAITING_APPROVAL")
    m = s.execute(select(Mandate).where(Mandate.id == tx.mandate_id).with_for_update()).scalar_one()
    # Re-evaluate at approval time: the mandate may have been revoked or consumed meanwhile.
    decision = evaluate(_facts(tx, tx.agent_id), _context(s, m, exclude_tx=tx.id))
    hard = [v for v in decision.violations if v.severity == "BLOCK"]
    audit.record(s, actor=f"customer:{customer_id}", action="transaction.approval_decided",
                 entity_type="transaction", entity_id=tx.id,
                 data={"approved": not hard, "revalidation": decision.model_dump(mode="json")})
    if hard:
        tx.violations = [v.model_dump() for v in decision.violations]
        transition(s, tx, TxState.BLOCKED, "re-validation failed at approval time", actor=f"customer:{customer_id}")
        return _result(tx), []
    tx.approved_by_customer = True
    if not _claim_slot(s, m, tx):
        return _result(tx), []
    transition(s, tx, TxState.AUTHORIZED, "explicit customer approval", actor=f"customer:{customer_id}")
    return _result(tx), execute(s, tx)


def reject(s: Session, tx_id: str, customer_id: str) -> AttemptResult:
    tx = get_locked(s, tx_id)
    if tx.customer_id != customer_id:
        raise NotFound("transaction not found")
    if tx.state != TxState.AWAITING_APPROVAL:
        raise InvalidState(f"transaction is {tx.state}, not AWAITING_APPROVAL")
    transition(s, tx, TxState.BLOCKED, "customer rejected the purchase", actor=f"customer:{customer_id}")
    return _result(tx)
