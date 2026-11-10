"""Persistence model. Money is always integer minor units (cents)."""
import secrets
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime, utcnow


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(10)}"


def _id(prefix: str):
    return mapped_column(String(40), primary_key=True, default=lambda: new_id(prefix))


def _ts():
    return mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = _id("cus")
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = _ts()


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = _id("agt")
    name: Mapped[str] = mapped_column(String(200))
    operator: Mapped[str] = mapped_column(String(200))
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = _ts()


class Merchant(Base):
    """Simulated merchant. Behaviour flags drive the sandbox merchant API."""

    __tablename__ = "merchants"
    id: Mapped[str] = _id("mer")
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(200))
    supports_cancellation: Mapped[bool] = mapped_column(Boolean, default=False)
    refund_behavior: Mapped[str] = mapped_column(String(20), default="succeed")  # succeed|decline|unavailable
    # correct | double_quantity | wrong_size | overcharge
    fulfillment_behavior: Mapped[str] = mapped_column(String(30), default="correct")
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


class Mandate(Base):
    """Head record of a delegated authorization. Content lives in immutable MandateVersion rows."""

    __tablename__ = "mandates"
    id: Mapped[str] = _id("man")
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    executions_used: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = _ts()


class MandateVersion(Base):
    __tablename__ = "mandate_versions"
    __table_args__ = (UniqueConstraint("mandate_id", "version"),)
    id: Mapped[str] = _id("mav")
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    intent_text: Mapped[str] = mapped_column(Text)
    constraints: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(String(128))
    signature_alg: Mapped[str] = mapped_column(String(20), default="HMAC-SHA256")
    created_at: Mapped[datetime] = _ts()


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("mandate_id", "execution_slot", name="uq_mandate_execution_slot"),
        Index("ix_tx_mandate_created", "mandate_id", "created_at"),
    )
    id: Mapped[str] = _id("txn")
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    mandate_id: Mapped[str | None] = mapped_column(ForeignKey("mandates.id"), nullable=True)
    mandate_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    merchant_id: Mapped[str] = mapped_column(String(40), index=True)
    sku: Mapped[str] = mapped_column(String(100))
    product_name: Mapped[str] = mapped_column(String(300))
    product_category: Mapped[str] = mapped_column(String(200))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    quantity: Mapped[int] = mapped_column(Integer)
    unit_amount_cents: Mapped[int] = mapped_column(BigInteger)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    state: Mapped[str] = mapped_column(String(30), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    violations: Mapped[list] = mapped_column(JSON, default=list)
    execution_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    payment_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    captured_amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fulfilled: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    event_cursors: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class TransactionStateChange(Base):
    __tablename__ = "transaction_state_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_state: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(String(300))
    at: Mapped[datetime] = _ts()


class ProviderEvent(Base):
    """Inbound webhook log. Unique event_id makes ingestion idempotent."""

    __tablename__ = "provider_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(100), unique=True)
    source: Mapped[str] = mapped_column(String(20))
    type: Mapped[str] = mapped_column(String(50))
    transaction_id: Mapped[str] = mapped_column(String(40), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20))
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    received_at: Mapped[datetime] = _ts()
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = _id("inc")
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), unique=True)
    customer_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), index=True)
    disputed_amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    evidence: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(String(300), nullable=True)
    summary_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detected_at: Mapped[datetime] = _ts()
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"
    id: Mapped[str] = _id("rec")
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    merchant_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ProtectionPolicy(Base):
    """Configurable reimbursement rules. Simulation only — not an insurance product."""

    __tablename__ = "protection_policies"
    __table_args__ = (UniqueConstraint("code", "version"),)
    id: Mapped[str] = _id("pol")
    code: Mapped[str] = mapped_column(String(50))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    deductible_cents: Mapped[int] = mapped_column(BigInteger)
    per_claim_limit_cents: Mapped[int] = mapped_column(BigInteger)
    aggregate_limit_cents: Mapped[int] = mapped_column(BigInteger)
    auto_approve_limit_cents: Mapped[int] = mapped_column(BigInteger)
    covered_incident_types: Mapped[list] = mapped_column(JSON)
    exclusions: Mapped[list] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[str] = _id("clm")
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), unique=True)
    transaction_id: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("protection_policies.id"))
    status: Mapped[str] = mapped_column(String(20), index=True)
    requested_amount_cents: Mapped[int] = mapped_column(BigInteger)
    approved_amount_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    deductible_applied_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3))
    checks: Mapped[list] = mapped_column(JSON, default=list)
    decision_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class LedgerEntry(Base):
    """Double-entry SANDBOX ledger. No real money moves through AgentGuard's MVP."""

    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    journal_id: Mapped[str] = mapped_column(String(40), index=True)
    account: Mapped[str] = mapped_column(String(100), index=True)
    direction: Mapped[str] = mapped_column(String(6))  # DEBIT|CREDIT
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    kind: Mapped[str] = mapped_column(String(40), index=True)  # MERCHANT_REFUND|PROTECTION_PAYOUT
    ref_type: Mapped[str] = mapped_column(String(30))
    ref_id: Mapped[str] = mapped_column(String(40), index=True)
    sandbox: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="in_app")
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    ref_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = _ts()


class AuditRecord(Base):
    """Append-only, hash-chained audit log (tamper-evident)."""

    __tablename__ = "audit_records"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = _ts()
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class AuditHead(Base):
    """Single-row lock target that serializes appends to the hash chain."""

    __tablename__ = "audit_head"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_hash: Mapped[str] = mapped_column(String(64))
    last_seq: Mapped[int] = mapped_column(Integer, default=0)


class DomainEvent(Base):
    """Transactional outbox. Written in the same DB transaction as the state change."""

    __tablename__ = "domain_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(60), index=True)
    entity_id: Mapped[str] = mapped_column(String(40))
    correlation_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
