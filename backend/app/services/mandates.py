"""Agent identity and delegated authorization (mandates)."""
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.enums import MandateStatus
from app.domain.policy import MandateConstraints
from app.models import Agent, Customer, Mandate, MandateVersion, new_id
from app.services import audit, signing
from app.services.errors import Conflict, NotFound, Unauthorized


def create_customer(s: Session, name: str, email: str) -> Customer:
    if s.execute(select(Customer).where(Customer.email == email)).scalar_one_or_none():
        raise Conflict("customer email already registered")
    c = Customer(name=name, email=email)
    s.add(c)
    s.flush()
    audit.record(s, actor="system", action="customer.registered", entity_type="customer", entity_id=c.id,
                 data={"name": name})
    return c


def register_agent(s: Session, name: str, operator: str) -> tuple[Agent, str]:
    """Returns the agent and its raw API key (shown once, only the SHA-256 is stored)."""
    raw = f"agk_{secrets.token_urlsafe(24)}"
    a = Agent(name=name, operator=operator, api_key_hash=signing.hash_api_key(raw))
    s.add(a)
    s.flush()
    audit.record(s, actor="system", action="agent.registered", entity_type="agent", entity_id=a.id,
                 data={"name": name, "operator": operator})
    return a, raw


def authenticate_agent(s: Session, raw_key: str | None) -> Agent:
    if not raw_key:
        raise Unauthorized("missing X-Agent-Key")
    a = s.execute(select(Agent).where(Agent.api_key_hash == signing.hash_api_key(raw_key))).scalar_one_or_none()
    if a is None or a.status != "ACTIVE":
        raise Unauthorized("invalid or suspended agent key")
    return a


def _new_version(s: Session, m: Mandate, version: int, intent: str, c: MandateConstraints,
                 expires_at: datetime | None) -> MandateVersion:
    cdict = c.model_dump(mode="json")
    payload = signing.mandate_payload(mandate_id=m.id, version=version, customer_id=m.customer_id,
                                      agent_id=m.agent_id, intent_text=intent, constraints=cdict,
                                      expires_at=expires_at)
    content_hash, sig = signing.sign(payload)
    mv = MandateVersion(mandate_id=m.id, version=version, intent_text=intent, constraints=cdict,
                        expires_at=expires_at, content_hash=content_hash, signature=sig)
    s.add(mv)
    return mv


def create_mandate(s: Session, *, customer_id: str, agent_id: str, intent_text: str,
                   constraints: MandateConstraints, expires_at: datetime | None) -> tuple[Mandate, MandateVersion]:
    if s.get(Customer, customer_id) is None:
        raise NotFound("customer not found")
    if s.get(Agent, agent_id) is None:
        raise NotFound("agent not found")
    m = Mandate(id=new_id("man"), customer_id=customer_id, agent_id=agent_id, current_version=1,
                status=MandateStatus.ACTIVE, executions_used=0)
    s.add(m)
    s.flush()
    mv = _new_version(s, m, 1, intent_text, constraints, expires_at)
    s.flush()
    audit.record(s, actor=f"customer:{customer_id}", action="mandate.created", entity_type="mandate",
                 entity_id=m.id, data={"version": 1, "content_hash": mv.content_hash,
                                       "constraints": mv.constraints})
    return m, mv


def amend_mandate(s: Session, mandate_id: str, intent_text: str, constraints: MandateConstraints,
                  expires_at: datetime | None) -> MandateVersion:
    """Amendments never overwrite: they append version N+1 and move the head pointer."""
    m = s.execute(select(Mandate).where(Mandate.id == mandate_id).with_for_update()).scalar_one_or_none()
    if m is None:
        raise NotFound("mandate not found")
    if m.status != MandateStatus.ACTIVE:
        raise Conflict("cannot amend a revoked mandate")
    mv = _new_version(s, m, m.current_version + 1, intent_text, constraints, expires_at)
    m.current_version += 1
    s.flush()
    audit.record(s, actor=f"customer:{m.customer_id}", action="mandate.amended", entity_type="mandate",
                 entity_id=m.id, data={"version": mv.version, "content_hash": mv.content_hash})
    return mv


def revoke_mandate(s: Session, mandate_id: str, reason: str) -> Mandate:
    m = s.execute(select(Mandate).where(Mandate.id == mandate_id).with_for_update()).scalar_one_or_none()
    if m is None:
        raise NotFound("mandate not found")
    if m.status == MandateStatus.REVOKED:
        return m
    m.status, m.revoked_at, m.revoked_reason = MandateStatus.REVOKED, utcnow(), reason
    audit.record(s, actor=f"customer:{m.customer_id}", action="mandate.revoked", entity_type="mandate",
                 entity_id=m.id, data={"reason": reason})
    return m


def current_version(s: Session, m: Mandate) -> MandateVersion:
    return s.execute(select(MandateVersion).where(MandateVersion.mandate_id == m.id,
                                                  MandateVersion.version == m.current_version)).scalar_one()


def verify_version(m: Mandate, mv: MandateVersion) -> bool:
    payload = signing.mandate_payload(mandate_id=m.id, version=mv.version, customer_id=m.customer_id,
                                      agent_id=m.agent_id, intent_text=mv.intent_text,
                                      constraints=mv.constraints, expires_at=mv.expires_at)
    return signing.verify(payload, mv.content_hash, mv.signature)
