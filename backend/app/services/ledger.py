"""Double-entry SANDBOX ledger. Every journal balances; every row is sandbox=True."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LedgerEntry, new_id

PROTECTION_POOL = "sandbox:protection_pool"


def post(s: Session, *, kind: str, debit: str, credit: str, amount_cents: int, currency: str,
         ref_type: str, ref_id: str) -> str:
    if amount_cents <= 0:
        raise ValueError("ledger amounts must be positive")
    jid = new_id("jnl")
    for account, direction in ((debit, "DEBIT"), (credit, "CREDIT")):
        s.add(LedgerEntry(journal_id=jid, account=account, direction=direction, amount_cents=amount_cents,
                          currency=currency, kind=kind, ref_type=ref_type, ref_id=ref_id, sandbox=True))
    s.flush()
    return jid


def total(s: Session, kind: str) -> int:
    q = select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
        LedgerEntry.kind == kind, LedgerEntry.direction == "CREDIT")
    return int(s.execute(q).scalar_one())


def credited_for(s: Session, ref_ids: list[str]) -> int:
    q = select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
        LedgerEntry.ref_id.in_(ref_ids), LedgerEntry.direction == "CREDIT")
    return int(s.execute(q).scalar_one())


def is_balanced(s: Session) -> bool:
    rows = s.execute(select(LedgerEntry.direction, func.sum(LedgerEntry.amount_cents))
                     .group_by(LedgerEntry.direction)).all()
    sums = {d: int(v) for d, v in rows}
    return sums.get("DEBIT", 0) == sums.get("CREDIT", 0)
