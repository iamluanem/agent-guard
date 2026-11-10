from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import IncidentType, MandateStatus, Outcome
from app.domain.policy import (
    AttemptFacts,
    MandateConstraints,
    MandateContext,
    ProductConstraint,
    check_execution,
    evaluate,
)

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
C = MandateConstraints(max_amount_cents=15_000, currency="USD", max_quantity=1, allowed_merchants=["mer_a"],
                       product=ProductConstraint(category="running shoes", required_attributes={"size": "38"}))


def ctx(**kw):
    base = dict(exists=True, status=MandateStatus.ACTIVE, agent_id="agt_1", constraints=C,
                expires_at=NOW + timedelta(days=1), signature_valid=True, now=NOW)
    return MandateContext(**{**base, **kw})


def attempt(**kw):
    base = dict(agent_id="agt_1", merchant_id="mer_a", sku="S", product_category="Running  Shoes",
                attributes={"size": "38"}, quantity=1, unit_amount_cents=12_990, amount_cents=12_990, currency="usd")
    return AttemptFacts(**{**base, **kw})


def codes(d):
    return {v.code for v in d.violations}


def test_valid_purchase_allowed():
    d = evaluate(attempt(), ctx())
    assert d.outcome == Outcome.ALLOW and d.violations == []


@pytest.mark.parametrize("facts,context,code", [
    ({}, {"exists": False, "constraints": None}, "MANDATE_MISSING"),
    ({}, {"status": MandateStatus.REVOKED}, "MANDATE_REVOKED"),
    ({}, {"expires_at": NOW - timedelta(seconds=1)}, "MANDATE_EXPIRED"),
    ({}, {"signature_valid": False}, "SIGNATURE_INVALID"),
    ({"agent_id": "agt_other"}, {}, "AGENT_NOT_DELEGATED"),
    ({}, {"executions_used": 1}, "DUPLICATE_EXECUTION"),
    ({}, {"recent_attempts": 5}, "REPEATED_ATTEMPTS"),
    ({"merchant_id": "mer_x"}, {}, "MERCHANT_NOT_AUTHORIZED"),
    ({"product_category": "hiking boots"}, {}, "PRODUCT_MISMATCH"),
    ({"attributes": {"size": "40"}}, {}, "PRODUCT_MISMATCH"),
    ({"quantity": 2, "unit_amount_cents": 6_000, "amount_cents": 12_000}, {}, "QUANTITY_EXCEEDED"),
    ({"amount_cents": 1}, {}, "AMOUNT_INCONSISTENT"),
    ({"currency": "BRL"}, {}, "CURRENCY_MISMATCH"),
    ({"unit_amount_cents": 30_000, "amount_cents": 30_000}, {}, "BUDGET_HARD_LIMIT"),
])
def test_blocking_rules(facts, context, code):
    d = evaluate(attempt(**facts), ctx(**context))
    assert d.outcome == Outcome.BLOCK
    assert code in codes(d)


def test_over_budget_within_ceiling_requires_approval():
    d = evaluate(attempt(unit_amount_cents=17_900, amount_cents=17_900), ctx())
    assert d.outcome == Outcome.REQUIRE_APPROVAL and codes(d) == {"BUDGET_EXCEEDED"}


def test_over_budget_action_block():
    strict = C.model_copy(update={"over_budget_action": "BLOCK"})
    d = evaluate(attempt(unit_amount_cents=15_001, amount_cents=15_001), ctx(constraints=strict))
    assert d.outcome == Outcome.BLOCK


def test_block_dominates_approval():
    d = evaluate(attempt(unit_amount_cents=17_900, amount_cents=17_900, merchant_id="mer_x"), ctx())
    assert d.outcome == Outcome.BLOCK


def test_engine_is_deterministic():
    a, c = attempt(quantity=3, unit_amount_cents=1, amount_cents=3), ctx()
    assert evaluate(a, c) == evaluate(a, c)


def test_execution_check_quantity_overcharge():
    r = check_execution(C, 12_990, 1, 25_980, {"sku": "S", "product_category": "running shoes",
                                               "attributes": {"size": "38"}, "quantity": 2})
    assert r.mismatch and r.incident_type == IncidentType.QUANTITY_MISMATCH and r.disputed_amount_cents == 12_990


def test_execution_check_wrong_product_disputes_full_amount():
    r = check_execution(C, 13_900, 1, 13_900, {"sku": "S", "product_category": "running shoes",
                                               "attributes": {"size": "42"}, "quantity": 1})
    assert r.incident_type == IncidentType.PRODUCT_MISMATCH and r.disputed_amount_cents == 13_900


def test_execution_check_clean():
    r = check_execution(C, 12_990, 1, 12_990, {"sku": "S", "product_category": "running shoes",
                                               "attributes": {"size": "38"}, "quantity": 1})
    assert not r.mismatch
