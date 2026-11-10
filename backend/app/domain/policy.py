"""Deterministic transaction policy engine.

Pure functions only: no I/O, no clock reads, no LLM. Given the same facts it always returns
the same decision, which makes every financial decision reproducible from the audit trail.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import IncidentType, MandateStatus, Outcome


def _norm(value: str) -> str:
    return " ".join(value.lower().split())


class ProductConstraint(BaseModel):
    category: str = Field(min_length=1)
    required_attributes: dict[str, str] = {}
    sku_allowlist: list[str] | None = None


class MandateConstraints(BaseModel):
    """What a customer delegated to an agent. Signed and versioned; never mutated in place."""

    max_amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    max_quantity: int = Field(ge=1)
    max_executions: int = Field(default=1, ge=1)
    allowed_merchants: list[str] | None = None  # None = any merchant
    product: ProductConstraint
    over_budget_action: Literal["REQUIRE_APPROVAL", "BLOCK"] = "REQUIRE_APPROVAL"
    hard_ceiling_pct: int = Field(default=50, ge=0, le=1000)


class AttemptFacts(BaseModel):
    agent_id: str
    merchant_id: str
    sku: str
    product_category: str
    attributes: dict[str, str] = {}
    quantity: int = Field(ge=1)
    unit_amount_cents: int = Field(ge=0)
    amount_cents: int = Field(ge=0)
    currency: str


class MandateContext(BaseModel):
    exists: bool
    status: MandateStatus | None = None
    agent_id: str | None = None
    constraints: MandateConstraints | None = None
    expires_at: datetime | None = None
    signature_valid: bool = False
    executions_used: int = 0
    recent_attempts: int = 0
    repeated_attempt_limit: int = 5
    now: datetime

    @model_validator(mode="after")
    def _consistent(self) -> "MandateContext":
        if self.exists and self.constraints is None:
            raise ValueError("existing mandate requires constraints")
        return self


Severity = Literal["BLOCK", "APPROVAL"]


class Violation(BaseModel):
    code: str
    severity: Severity
    message: str
    expected: str | None = None
    actual: str | None = None


class PolicyDecision(BaseModel):
    outcome: Outcome
    violations: list[Violation]
    rules_evaluated: list[str]


def _block(code: str, msg: str, expected=None, actual=None) -> Violation:
    return Violation(code=code, severity="BLOCK", message=msg,
                     expected=None if expected is None else str(expected),
                     actual=None if actual is None else str(actual))


def product_violations(c: MandateConstraints, sku: str, category: str, attrs: dict[str, str]) -> list[Violation]:
    out: list[Violation] = []
    p = c.product
    if _norm(category) != _norm(p.category):
        out.append(_block("PRODUCT_MISMATCH", "Product category differs from mandate", p.category, category))
    if p.sku_allowlist is not None and sku not in p.sku_allowlist:
        out.append(_block("PRODUCT_MISMATCH", "SKU not in mandate allowlist", ",".join(p.sku_allowlist), sku))
    for key, want in p.required_attributes.items():
        got = attrs.get(key)
        if got is None or _norm(got) != _norm(want):
            out.append(_block("PRODUCT_MISMATCH", f"Attribute '{key}' differs from mandate", want, got))
    return out


def evaluate(a: AttemptFacts, ctx: MandateContext) -> PolicyDecision:
    rules: list[str] = []
    v: list[Violation] = []

    def rule(name: str) -> None:
        rules.append(name)

    rule("mandate_present")
    if not ctx.exists or ctx.constraints is None:
        v.append(_block("MANDATE_MISSING", "No mandate authorizes this purchase"))
        return PolicyDecision(outcome=Outcome.BLOCK, violations=v, rules_evaluated=rules)
    c = ctx.constraints

    rule("signature_valid")
    if not ctx.signature_valid:
        v.append(_block("SIGNATURE_INVALID", "Mandate signature verification failed"))
    rule("mandate_active")
    if ctx.status != MandateStatus.ACTIVE:
        v.append(_block("MANDATE_REVOKED", "Mandate was revoked by the customer"))
    rule("mandate_not_expired")
    if ctx.expires_at is not None and ctx.now >= ctx.expires_at:
        v.append(_block("MANDATE_EXPIRED", "Mandate expired", ctx.expires_at.isoformat(), ctx.now.isoformat()))
    rule("agent_delegated")
    if ctx.agent_id != a.agent_id:
        v.append(_block("AGENT_NOT_DELEGATED", "Agent is not the delegate of this mandate", ctx.agent_id, a.agent_id))
    rule("execution_limit")
    if ctx.executions_used >= c.max_executions:
        v.append(_block("DUPLICATE_EXECUTION", "Mandate execution limit already consumed",
                        c.max_executions, ctx.executions_used + 1))
    rule("repeated_attempts")
    if ctx.recent_attempts >= ctx.repeated_attempt_limit:
        v.append(_block("REPEATED_ATTEMPTS", "Too many execution attempts in a short window",
                        ctx.repeated_attempt_limit, ctx.recent_attempts + 1))
    rule("merchant_allowed")
    if c.allowed_merchants is not None and a.merchant_id not in c.allowed_merchants:
        v.append(_block("MERCHANT_NOT_AUTHORIZED", "Merchant not authorized by customer",
                        ",".join(c.allowed_merchants), a.merchant_id))
    rule("product_match")
    v.extend(product_violations(c, a.sku, a.product_category, a.attributes))
    rule("quantity_limit")
    if a.quantity > c.max_quantity:
        v.append(_block("QUANTITY_EXCEEDED", "Quantity exceeds mandate", c.max_quantity, a.quantity))
    rule("amount_consistent")
    if a.unit_amount_cents * a.quantity != a.amount_cents:
        v.append(_block("AMOUNT_INCONSISTENT", "Total does not equal unit price x quantity",
                        a.unit_amount_cents * a.quantity, a.amount_cents))
    rule("currency_match")
    if a.currency.upper() != c.currency.upper():
        v.append(_block("CURRENCY_MISMATCH", "Currency differs from mandate", c.currency, a.currency))
    rule("budget")
    if a.amount_cents > c.max_amount_cents:
        ceiling = c.max_amount_cents * (100 + c.hard_ceiling_pct) // 100
        if c.over_budget_action == "BLOCK" or a.amount_cents > ceiling:
            v.append(_block("BUDGET_HARD_LIMIT", "Amount exceeds hard budget ceiling",
                            c.max_amount_cents, a.amount_cents))
        else:
            v.append(Violation(code="BUDGET_EXCEEDED", severity="APPROVAL",
                               message="Amount exceeds budget; explicit customer approval required",
                               expected=str(c.max_amount_cents), actual=str(a.amount_cents)))

    if any(x.severity == "BLOCK" for x in v):
        outcome = Outcome.BLOCK
    elif v:
        outcome = Outcome.REQUIRE_APPROVAL
    else:
        outcome = Outcome.ALLOW
    return PolicyDecision(outcome=outcome, violations=v, rules_evaluated=rules)


class ExecutionCheck(BaseModel):
    """Post-execution comparison between what was authorized and what actually happened."""

    mismatch: bool
    incident_type: IncidentType | None = None
    disputed_amount_cents: int = 0
    findings: list[Violation] = []


def check_execution(
    c: MandateConstraints,
    authorized_amount_cents: int,
    authorized_quantity: int,
    captured_amount_cents: int | None,
    fulfilled: dict | None,
) -> ExecutionCheck:
    """Compare captured payment + fulfilled order against the authorization."""
    findings: list[Violation] = []
    itype: IncidentType | None = None
    captured = captured_amount_cents if captured_amount_cents is not None else authorized_amount_cents

    if fulfilled:
        prod = product_violations(c, fulfilled.get("sku", ""), fulfilled.get("product_category", ""),
                                  fulfilled.get("attributes", {}))
        if prod:
            findings.extend(prod)
            itype = IncidentType.PRODUCT_MISMATCH
        qty = int(fulfilled.get("quantity", authorized_quantity))
        if qty != authorized_quantity:
            findings.append(_block("QUANTITY_MISMATCH", "Fulfilled quantity differs from authorized",
                                   authorized_quantity, qty))
            itype = itype or IncidentType.QUANTITY_MISMATCH
    if captured > authorized_amount_cents:
        findings.append(_block("OVERCHARGE", "Captured amount exceeds authorized amount",
                               authorized_amount_cents, captured))
        itype = itype or IncidentType.OVERCHARGE

    if not findings:
        return ExecutionCheck(mismatch=False)
    # Wrong product: the whole charge is disputed. Otherwise only the excess over the authorization.
    disputed = captured if itype == IncidentType.PRODUCT_MISMATCH else max(0, captured - authorized_amount_cents)
    return ExecutionCheck(mismatch=True, incident_type=itype, disputed_amount_cents=disputed, findings=findings)
