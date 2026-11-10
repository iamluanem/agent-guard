"""Tamper-evident audit log: each record hashes its content plus the previous record's hash."""
import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AuditHead, AuditRecord, DomainEvent
from app.observability import current_correlation_id

GENESIS = "0" * 64


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prev_hash: str, at: datetime, cid: str, actor: str, action: str, etype: str, eid: str, data: dict) -> str:
    body = canonical({"prev": prev_hash, "at": at.isoformat(), "cid": cid, "actor": actor,
                      "action": action, "etype": etype, "eid": eid, "data": data})
    return hashlib.sha256(body.encode()).hexdigest()


def record(session: Session, *, actor: str, action: str, entity_type: str, entity_id: str,
           data: dict | None = None, emit: bool = True) -> AuditRecord:
    """Append to the chain (serialized by row lock on audit_head) and write an outbox event."""
    data = json.loads(canonical(data or {}))
    head = session.execute(select(AuditHead).where(AuditHead.id == 1).with_for_update()).scalar_one_or_none()
    if head is None:
        head = AuditHead(id=1, last_hash=GENESIS, last_seq=0)
        session.add(head)
        session.flush()
    at = utcnow()
    cid = current_correlation_id()
    h = _digest(head.last_hash, at, cid, actor, action, entity_type, entity_id, data)
    rec = AuditRecord(at=at, correlation_id=cid, actor=actor, action=action, entity_type=entity_type,
                      entity_id=entity_id, data=data, prev_hash=head.last_hash, hash=h)
    session.add(rec)
    session.flush()
    head.last_hash, head.last_seq = h, rec.seq
    if emit:
        session.add(DomainEvent(type=action, entity_id=entity_id, correlation_id=cid,
                                payload={"entity_type": entity_type, **data}))
    return rec


def verify_chain(session: Session) -> dict:
    prev = GENESIS
    count = 0
    for rec in session.execute(select(AuditRecord).order_by(AuditRecord.seq)).scalars():
        expected = _digest(prev, rec.at, rec.correlation_id, rec.actor, rec.action,
                           rec.entity_type, rec.entity_id, rec.data)
        if rec.prev_hash != prev or rec.hash != expected:
            return {"valid": False, "records_checked": count, "broken_at_seq": rec.seq}
        prev, count = rec.hash, count + 1
    return {"valid": True, "records_checked": count, "head_hash": prev}
