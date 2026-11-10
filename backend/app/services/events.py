"""Transaction Monitoring: webhook ingestion with dedupe, ordering and post-execution checks.

- Duplicate delivery: UNIQUE(event_id) -> recorded once, re-deliveries return DUPLICATE.
- Out-of-order: each source has a per-transaction sequence. Gaps are DEFERRED and applied
  as soon as the missing event arrives (or force-applied by the sweeper after a timeout).
- Stale events (sequence already applied) are recorded as STALE and ignored.
"""
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters import llm
from app.adapters.payments import WebhookEvent
from app.config import get_settings
from app.db import session_factory, utcnow
from app.domain.enums import EventStatus, IncidentStatus, RecoveryStatus, TxState
from app.domain.policy import MandateConstraints, check_execution
from app.domain.state_machine import can_transition
from app.models import Incident, MandateVersion, Merchant, ProviderEvent, RecoveryAction, Transaction
from app.observability import log
from app.services import audit, claims, ledger, recovery
from app.services.notify import notify
from app.services.transactions import transition

logger = logging.getLogger(__name__)


def ingest(s: Session, e: WebhookEvent) -> tuple[EventStatus, list[WebhookEvent]]:
    """Ingest one provider event. Caller commits. Returns follow-up events to deliver."""
    pe = ProviderEvent(event_id=e.event_id, source=e.source, type=e.type, transaction_id=e.transaction_id,
                       sequence=e.sequence, payload=e.payload, status=EventStatus.DEFERRED)
    try:
        with s.begin_nested():
            s.add(pe)
            s.flush()
    except IntegrityError:
        return EventStatus.DUPLICATE, []

    tx = s.execute(select(Transaction).where(Transaction.id == e.transaction_id).with_for_update()).scalar_one_or_none()
    if tx is None:
        pe.status, pe.detail = EventStatus.REJECTED, "unknown transaction"
        return pe.status, []

    cursor = int(tx.event_cursors.get(e.source, 0))
    if e.sequence <= cursor:
        pe.status, pe.detail = EventStatus.STALE, f"cursor already at {cursor}"
        return pe.status, []
    if e.sequence > cursor + 1:
        pe.detail = f"waiting for sequence {cursor + 1}"
        audit.record(s, actor=f"webhook:{e.source}", action="event.deferred", entity_type="transaction",
                     entity_id=tx.id, data={"event_id": e.event_id, "type": e.type, "sequence": e.sequence})
        return EventStatus.DEFERRED, []

    follow = _apply(s, tx, pe)
    follow += _drain(s, tx, e.source)
    return EventStatus(pe.status), follow


def _drain(s: Session, tx: Transaction, source: str) -> list[WebhookEvent]:
    follow: list[WebhookEvent] = []
    while True:
        nxt = int(tx.event_cursors.get(source, 0)) + 1
        pe = s.execute(select(ProviderEvent).where(
            ProviderEvent.transaction_id == tx.id, ProviderEvent.source == source,
            ProviderEvent.sequence == nxt, ProviderEvent.status == EventStatus.DEFERRED)).scalars().first()
        if pe is None:
            return follow
        follow += _apply(s, tx, pe)


def _advance(tx: Transaction, source: str, seq: int) -> None:
    tx.event_cursors = {**tx.event_cursors, source: seq}  # reassign so the JSON column is flagged dirty


def _apply(s: Session, tx: Transaction, pe: ProviderEvent) -> list[WebhookEvent]:
    _advance(tx, pe.source, pe.sequence)
    pe.status, pe.processed_at = EventStatus.APPLIED, utcnow()
    p = pe.payload
    follow: list[WebhookEvent] = []
    audit.record(s, actor=f"webhook:{pe.source}", action=f"event.{pe.type}", entity_type="transaction",
                 entity_id=tx.id, data={"event_id": pe.event_id, "sequence": pe.sequence, **p})

    if pe.type == "payment.authorized":
        tx.payment_ref = tx.payment_ref or p.get("payment_ref")
    elif pe.type == "payment.captured":
        tx.captured_amount_cents = int(p["amount_cents"])
        if can_transition(TxState(tx.state), TxState.COMPLETED):
            transition(s, tx, TxState.COMPLETED, "payment captured", actor="webhook:payment")
    elif pe.type == "payment.failed":
        if can_transition(TxState(tx.state), TxState.FAILED):
            transition(s, tx, TxState.FAILED, p.get("reason", "payment failed"), actor="webhook:payment")
    elif pe.type == "order.fulfilled":
        tx.fulfilled = p.get("order", {})
    elif pe.type == "refund.succeeded":
        follow += _refund_succeeded(s, tx, p)
    elif pe.type == "refund.failed":
        recovery.on_refund_failed(s, tx, p)
    else:
        pe.status, pe.detail = EventStatus.REJECTED, "unsupported event type"

    if pe.type in ("payment.captured", "order.fulfilled"):
        follow += _verify_execution(s, tx)
    return follow


def _verify_execution(s: Session, tx: Transaction) -> list[WebhookEvent]:
    """Once both payment capture and order fulfilment are known, compare them to the mandate."""
    if tx.state != TxState.COMPLETED or tx.captured_amount_cents is None or tx.fulfilled is None:
        return []
    mv = s.execute(select(MandateVersion).where(MandateVersion.mandate_id == tx.mandate_id,
                                                MandateVersion.version == tx.mandate_version)).scalar_one()
    result = check_execution(MandateConstraints.model_validate(mv.constraints), tx.amount_cents, tx.quantity,
                             tx.captured_amount_cents, tx.fulfilled)
    audit.record(s, actor="monitor", action="execution.verified", entity_type="transaction", entity_id=tx.id,
                 data=result.model_dump(mode="json"))
    if not result.mismatch:
        return []

    merchant = s.get(Merchant, tx.merchant_id)
    evidence = {
        "transaction_id": tx.id, "incident_type": result.incident_type,
        "authorized": {"amount_cents": tx.amount_cents, "quantity": tx.quantity, "sku": tx.sku,
                       "attributes": tx.attributes, "mandate_id": tx.mandate_id, "mandate_version": tx.mandate_version},
        "executed": {"captured_amount_cents": tx.captured_amount_cents, "fulfilled": tx.fulfilled,
                     "payment_ref": tx.payment_ref},
        "findings": [f.model_dump() for f in result.findings],
        "disputed_amount_cents": result.disputed_amount_cents, "currency": tx.currency,
        "merchant_supports_cancellation": bool(merchant and merchant.supports_cancellation),
    }
    narrative, source = llm.explain_incident(evidence)
    evidence["customer_message"] = narrative.customer_message
    inc = Incident(transaction_id=tx.id, customer_id=tx.customer_id, type=result.incident_type,
                   severity="HIGH" if result.disputed_amount_cents >= 10_000 else "MEDIUM",
                   status=IncidentStatus.OPEN, disputed_amount_cents=result.disputed_amount_cents,
                   currency=tx.currency, evidence=evidence, summary=narrative.summary,
                   recommended_action=narrative.recommended_action, summary_source=source)
    s.add(inc)
    s.flush()
    audit.record(s, actor="monitor", action="incident.detected", entity_type="incident", entity_id=inc.id,
                 data={"transaction_id": tx.id, "type": inc.type, "disputed_amount_cents": inc.disputed_amount_cents,
                       "narrative_source": source})
    transition(s, tx, TxState.DISPUTED, f"{result.incident_type} detected after execution", actor="monitor")
    transition(s, tx, TxState.RECOVERY_PENDING, "recovery workflow started", actor="recovery-engine")
    log(logger, "incident detected", incident_id=inc.id, transaction_id=tx.id, type=inc.type)
    return recovery.start(s, inc, tx)


def _refund_succeeded(s: Session, tx: Transaction, p: dict) -> list[WebhookEvent]:
    inc = s.execute(select(Incident).where(Incident.transaction_id == tx.id)).scalar_one_or_none()
    if inc is None:
        return []
    a = s.execute(select(RecoveryAction).where(RecoveryAction.incident_id == inc.id,
                                               RecoveryAction.status == RecoveryStatus.REQUESTED)).scalars().first()
    if a:
        a.status = RecoveryStatus.SUCCEEDED
    amount = int(p.get("amount_cents", inc.disputed_amount_cents))
    ledger.post(s, kind=claims.REFUND, debit=f"merchant:{tx.merchant_id}", credit=f"customer:{tx.customer_id}",
                amount_cents=amount, currency=tx.currency, ref_type="incident", ref_id=inc.id)
    inc.status, inc.resolved_at = IncidentStatus.RECOVERED, utcnow()
    if can_transition(TxState(tx.state), TxState.REFUNDED):
        transition(s, tx, TxState.REFUNDED, f"merchant refunded {amount} cents", actor="webhook:merchant")
    audit.record(s, actor="recovery-engine", action="incident.recovered", entity_type="incident", entity_id=inc.id,
                 data={"amount_cents": amount, "merchant_ref": p.get("merchant_ref")})
    notify(s, tx.customer_id, "Funds recovered",
           f"The merchant refunded {amount / 100:.2f} {tx.currency} for incident {inc.id}.", inc.id)
    return []


def sweep_deferred(s: Session) -> int:
    """Force-apply events stuck behind a gap that never filled (lost upstream event)."""
    cutoff = utcnow() - timedelta(seconds=get_settings().deferred_event_timeout_s)
    stuck = s.execute(select(ProviderEvent).where(ProviderEvent.status == EventStatus.DEFERRED,
                                                  ProviderEvent.received_at <= cutoff)
                      .order_by(ProviderEvent.transaction_id, ProviderEvent.source, ProviderEvent.sequence)
                      ).scalars().all()
    n = 0
    for pe in stuck:
        tx = s.execute(select(Transaction).where(Transaction.id == pe.transaction_id).with_for_update()).scalar_one()
        if pe.status != EventStatus.DEFERRED:
            continue
        audit.record(s, actor="monitor", action="event.gap_skipped", entity_type="transaction", entity_id=tx.id,
                     data={"source": pe.source, "from": tx.event_cursors.get(pe.source, 0), "to": pe.sequence})
        deliver(_apply(s, tx, pe) + _drain(s, tx, pe.source), session=s)
        n += 1
    return n


def deliver(events: list[WebhookEvent], session: Session | None = None) -> None:
    """Deliver simulated provider webhooks. Each event commits in its own DB transaction,
    exactly like independent HTTP deliveries would."""
    queue = list(events)
    if session is not None:
        while queue:
            _, follow = ingest(session, queue.pop(0))
            queue.extend(follow)
        return
    while queue:
        e = queue.pop(0)
        with session_factory()() as s:
            _, follow = ingest(s, e)
            s.commit()
        queue.extend(follow)
