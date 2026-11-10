"""Automated Recovery Engine: cancel -> refund -> retry with backoff -> escalate to claim.

A refund *request* is never treated as money recovered. Funds count as recovered only when the
merchant confirms with a `refund.succeeded` event.
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.payments import RecoveryResult, WebhookEvent, merchant_api
from app.config import get_settings
from app.db import utcnow
from app.domain.enums import IncidentStatus, RecoveryKind, RecoveryStatus
from app.models import Incident, Merchant, RecoveryAction, Transaction
from app.services import audit, claims
from app.services.notify import notify


def _merchant(s: Session, tx: Transaction) -> Merchant | None:
    return s.get(Merchant, tx.merchant_id)


def start(s: Session, inc: Incident, tx: Transaction) -> list[WebhookEvent]:
    m = _merchant(s, tx)
    shipped = (tx.fulfilled or {}).get("status") == "shipped"
    kind = RecoveryKind.CANCELLATION if (m and m.supports_cancellation and not shipped) else RecoveryKind.REFUND
    inc.status = IncidentStatus.RECOVERING
    notify(s, tx.customer_id, "We're recovering a problematic purchase",
           inc.evidence.get("customer_message") or f"Incident {inc.id} opened.", inc.id)
    return attempt(s, _new_action(s, inc, kind))


def _new_action(s: Session, inc: Incident, kind: RecoveryKind) -> RecoveryAction:
    a = RecoveryAction(incident_id=inc.id, kind=kind, status=RecoveryStatus.PENDING,
                       amount_cents=inc.disputed_amount_cents, max_attempts=get_settings().recovery_max_attempts)
    s.add(a)
    s.flush()
    return a


def attempt(s: Session, a: RecoveryAction) -> list[WebhookEvent]:
    inc = s.get(Incident, a.incident_id)
    tx = s.get(Transaction, inc.transaction_id)
    m = _merchant(s, tx)
    a.attempts += 1
    next_seq = int(tx.event_cursors.get("merchant", 0)) + 1
    api = merchant_api()
    res: RecoveryResult
    if a.kind == RecoveryKind.CANCELLATION:
        res = api.cancel_order(tx.id, tx.payment_ref or "", bool(m and m.supports_cancellation), next_seq)
    else:
        res = api.request_refund(tx.id, tx.payment_ref or "", a.amount_cents,
                                 m.refund_behavior if m else "succeed", next_seq)
    audit.record(s, actor="recovery-engine", action=f"recovery.{a.kind.lower()}_requested",
                 entity_type="incident", entity_id=inc.id,
                 data={"action_id": a.id, "attempt": a.attempts, "outcome": res.outcome, "reason": res.reason,
                       "amount_cents": a.amount_cents, "simulated": True})

    if res.outcome == "ACCEPTED":
        a.status, a.merchant_ref = RecoveryStatus.REQUESTED, res.merchant_ref
        return res.events
    if res.outcome == "UNSUPPORTED":
        a.status, a.last_error = RecoveryStatus.FAILED, res.reason
        return attempt(s, _new_action(s, inc, RecoveryKind.REFUND))
    # UNAVAILABLE / transient: retry with exponential backoff, then escalate.
    a.last_error = res.reason
    if a.attempts < a.max_attempts:
        a.status = RecoveryStatus.RETRY_SCHEDULED
        a.next_attempt_at = utcnow() + timedelta(seconds=get_settings().recovery_backoff_s * 2 ** (a.attempts - 1))
        return []
    a.status = RecoveryStatus.FAILED
    escalate(s, inc, f"merchant unreachable after {a.attempts} attempts")
    return []


def on_refund_failed(s: Session, tx: Transaction, payload: dict) -> None:
    inc = s.execute(select(Incident).where(Incident.transaction_id == tx.id)).scalar_one_or_none()
    if inc is None:
        return
    a = s.execute(select(RecoveryAction).where(RecoveryAction.incident_id == inc.id,
                                               RecoveryAction.status == RecoveryStatus.REQUESTED)).scalars().first()
    if a:
        a.status, a.last_error = RecoveryStatus.FAILED, payload.get("reason", "declined")
    escalate(s, inc, payload.get("reason", "merchant declined refund"))


def escalate(s: Session, inc: Incident, reason: str) -> None:
    if inc.status == IncidentStatus.ESCALATED:
        return
    inc.status = IncidentStatus.ESCALATED
    audit.record(s, actor="recovery-engine", action="incident.escalated", entity_type="incident",
                 entity_id=inc.id, data={"reason": reason})
    claims.open_claim(s, inc)


def run_due(s: Session) -> list[WebhookEvent]:
    due = s.execute(select(RecoveryAction).where(RecoveryAction.status == RecoveryStatus.RETRY_SCHEDULED,
                                                 RecoveryAction.next_attempt_at <= utcnow())
                    .with_for_update(skip_locked=True)).scalars().all()
    events: list[WebhookEvent] = []
    for a in due:
        events.extend(attempt(s, a))
    return events
