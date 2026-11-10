"""Protection Claims Engine — deterministic, configurable, SIMULATED.

Payouts are sandbox ledger entries. This is not an insurance product and nothing here
represents real coverage.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.enums import ClaimStatus, IncidentStatus, Outcome, RecoveryStatus
from app.models import Claim, Incident, ProtectionPolicy, RecoveryAction, Transaction
from app.services import audit, ledger
from app.services.errors import InvalidState, NotFound
from app.services.notify import notify

PAYOUT = "PROTECTION_PAYOUT_SIMULATED"
REFUND = "MERCHANT_REFUND"


def active_policy(s: Session) -> ProtectionPolicy:
    p = s.execute(select(ProtectionPolicy).where(ProtectionPolicy.active.is_(True))
                  .order_by(ProtectionPolicy.version.desc())).scalars().first()
    if p is None:
        raise NotFound("no active protection policy")
    return p


def _check(code: str, passed: bool, detail: str, hard: bool = True) -> dict:
    return {"code": code, "passed": passed, "hard": hard, "detail": detail}


def evaluate(s: Session, inc: Incident, tx: Transaction, p: ProtectionPolicy) -> tuple[list[dict], int, int]:
    """Returns (checks, payable_cents, deductible_applied_cents). Pure reads, no writes."""
    actions = s.execute(select(RecoveryAction).where(RecoveryAction.incident_id == inc.id)).scalars().all()
    authorized = tx.decision in (Outcome.ALLOW, Outcome.REQUIRE_APPROVAL) and tx.mandate_version is not None \
        and tx.execution_slot is not None
    already = ledger.credited_for(s, [inc.id])  # refunds + previous payouts on this incident
    paid_before = int(s.execute(select(func.coalesce(func.sum(Claim.approved_amount_cents), 0)).where(
        Claim.customer_id == tx.customer_id, Claim.status == ClaimStatus.PAID)).scalar_one())
    exclusions = set(p.exclusions)

    checks = [
        _check("VALID_AUTHORIZATION", authorized,
               f"executed under signed mandate {tx.mandate_id} v{tx.mandate_version}" if authorized
               else "no valid authorization on record"),
        _check("AGENT_OR_EXECUTION_ERROR_EVIDENCE", bool(inc.evidence.get("findings")),
               f"{len(inc.evidence.get('findings', []))} deterministic finding(s)"),
        _check("PAYMENT_RECORDS", bool(tx.payment_ref and tx.captured_amount_cents is not None),
               f"payment_ref={tx.payment_ref} captured={tx.captured_amount_cents}"),
        _check("MERCHANT_RECOVERY_ATTEMPTED",
               any(a.status == RecoveryStatus.FAILED for a in actions)
               and not any(a.status == RecoveryStatus.SUCCEEDED for a in actions),
               f"{len(actions)} recovery action(s): " + ", ".join(f"{a.kind}:{a.status}" for a in actions)),
        _check("COVERED_INCIDENT_TYPE", inc.type in p.covered_incident_types, f"type={inc.type}"),
        _check("EXCLUSION_CUSTOMER_APPROVED_DEVIATION",
               not ("CUSTOMER_APPROVED_DEVIATION" in exclusions and tx.approved_by_customer
                    and inc.type == "OVERCHARGE"),
               "customer explicitly approved an over-budget amount" if tx.approved_by_customer else "n/a"),
        _check("NO_DUPLICATE_COMPENSATION", already < inc.disputed_amount_cents,
               f"already compensated {already} cents; only the remainder is claimable"),
    ]
    gross = max(0, inc.disputed_amount_cents - already)
    deductible = min(p.deductible_cents, gross)
    remaining_aggregate = max(0, p.aggregate_limit_cents - paid_before)
    payable = min(gross - deductible, p.per_claim_limit_cents, remaining_aggregate)
    checks.append(_check("LIMITS_AND_DEDUCTIBLE", payable > 0,
                         f"gross={gross} deductible={deductible} per_claim_limit={p.per_claim_limit_cents} "
                         f"aggregate_remaining={remaining_aggregate} payable={payable}"))
    checks.append(_check("WITHIN_AUTO_APPROVAL_LIMIT", payable <= p.auto_approve_limit_cents,
                         f"auto-approve limit {p.auto_approve_limit_cents}", hard=False))
    return checks, payable, deductible


def open_claim(s: Session, inc: Incident) -> Claim:
    existing = s.execute(select(Claim).where(Claim.incident_id == inc.id)).scalar_one_or_none()
    if existing:
        return existing
    tx = s.get(Transaction, inc.transaction_id)
    assert tx is not None
    p = active_policy(s)
    checks, payable, deductible = evaluate(s, inc, tx, p)
    c = Claim(incident_id=inc.id, transaction_id=tx.id, customer_id=tx.customer_id, policy_id=p.id,
              status=ClaimStatus.SUBMITTED, requested_amount_cents=inc.disputed_amount_cents,
              currency=inc.currency, checks=checks, deductible_applied_cents=deductible)
    s.add(c)
    s.flush()
    audit.record(s, actor="claims-engine", action="claim.submitted", entity_type="claim", entity_id=c.id,
                 data={"incident_id": inc.id, "policy": f"{p.code}@v{p.version}",
                       "requested_amount_cents": c.requested_amount_cents})

    hard_fail = [ch for ch in checks if ch["hard"] and not ch["passed"]]
    if hard_fail:
        _decide(s, c, ClaimStatus.REJECTED, 0, "rules", "failed: " + ", ".join(ch["code"] for ch in hard_fail))
    elif not all(ch["passed"] for ch in checks):
        c.status = ClaimStatus.IN_REVIEW
        c.approved_amount_cents = payable
        audit.record(s, actor="claims-engine", action="claim.escalated_to_review", entity_type="claim",
                     entity_id=c.id, data={"proposed_amount_cents": payable})
        notify(s, tx.customer_id, "Your protection claim is under review",
               f"Claim {c.id} needs a human reviewer. "
               f"Proposed simulated payout: {payable / 100:.2f} {c.currency}.", c.id)
    else:
        _decide(s, c, ClaimStatus.APPROVED, payable, "rules", "all eligibility checks passed")
        pay(s, c)
    return c


def _decide(s: Session, c: Claim, status: ClaimStatus, amount: int, by: str, reason: str, note: str | None = None):
    c.status, c.approved_amount_cents, c.decided_by, c.decision_reason = status, amount, by, reason
    c.reviewer_note, c.decided_at = note, utcnow()
    audit.record(s, actor=by if by != "rules" else "claims-engine", action=f"claim.{status.lower()}",
                 entity_type="claim", entity_id=c.id, data={"amount_cents": amount, "reason": reason})
    if status == ClaimStatus.REJECTED:
        notify(s, c.customer_id, "Protection claim not approved", f"Claim {c.id}: {reason}", c.id)


def pay(s: Session, c: Claim) -> None:
    if c.status != ClaimStatus.APPROVED:
        raise InvalidState("only approved claims can be paid")
    inc = s.get(Incident, c.incident_id)
    assert inc is not None
    ledger.post(s, kind=PAYOUT, debit=ledger.PROTECTION_POOL, credit=f"customer:{c.customer_id}",
                amount_cents=c.approved_amount_cents, currency=c.currency, ref_type="incident", ref_id=inc.id)
    c.status, c.paid_at = ClaimStatus.PAID, utcnow()
    inc.status, inc.resolved_at = IncidentStatus.CLOSED, utcnow()
    audit.record(s, actor="claims-engine", action="claim.paid", entity_type="claim", entity_id=c.id,
                 data={"amount_cents": c.approved_amount_cents, "sandbox": True})
    notify(s, c.customer_id, "Simulated protection payout issued",
           f"{c.approved_amount_cents / 100:.2f} {c.currency} credited in the AgentGuard sandbox ledger "
           f"(simulation — not real insurance).", c.id)


def review(s: Session, claim_id: str, *, approve: bool, reviewer: str, note: str,
           amount_cents: int | None = None) -> Claim:
    c = s.execute(select(Claim).where(Claim.id == claim_id).with_for_update()).scalar_one_or_none()
    if c is None:
        raise NotFound("claim not found")
    if c.status != ClaimStatus.IN_REVIEW:
        raise InvalidState(f"claim is {c.status}, not IN_REVIEW")
    if not approve:
        _decide(s, c, ClaimStatus.REJECTED, 0, f"reviewer:{reviewer}", "rejected by reviewer", note)
        return c
    # Humans can lower but never exceed the deterministic policy cap.
    cap = c.approved_amount_cents
    amount = cap if amount_cents is None else min(amount_cents, cap)
    _decide(s, c, ClaimStatus.APPROVED, amount, f"reviewer:{reviewer}", "approved by reviewer", note)
    pay(s, c)
    return c
