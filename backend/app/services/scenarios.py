"""Fixtures and the six required demo scenarios.

Scenarios drive the *real* engine end-to-end (policy evaluation, payment adapter, webhooks,
recovery, claims). Nothing is inserted directly as a "result".
"""
import secrets
from collections.abc import Callable
from datetime import timedelta

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session_factory, utcnow
from app.domain.policy import MandateConstraints, ProductConstraint
from app.models import Agent, AuditRecord, Customer, Merchant, ProtectionPolicy, Transaction
from app.observability import correlation_id, new_correlation_id
from app.services import events, mandates, signing, transactions
from app.services.transactions import AttemptRequest

DEMO_AGENT_ID = "agt_demo_shopper"
MERCHANTS = [
    # id, name, domain, cancellation, refund behaviour, fulfilment behaviour
    ("mer_stride", "Stride Sports", "stride.example", True, "succeed", "correct"),
    ("mer_quickkicks", "QuickKicks", "quickkicks.example", False, "succeed", "double_quantity"),
    ("mer_finalsale", "FinalSale Outlet", "finalsale.example", False, "decline", "wrong_size"),
    ("mer_pricebump", "PriceBump Store", "pricebump.example", False, "unavailable", "overcharge"),
    ("mer_unlisted", "Unlisted Bazaar", "unlisted.example", False, "decline", "correct"),
]
ALLOWED = ["mer_stride", "mer_quickkicks", "mer_finalsale", "mer_pricebump"]


def ensure_fixtures(s: Session) -> None:
    for mid, name, domain, cancel, refund, fulfil in MERCHANTS:
        if s.get(Merchant, mid) is None:
            s.add(Merchant(id=mid, name=name, domain=domain, supports_cancellation=cancel,
                           refund_behavior=refund, fulfillment_behavior=fulfil))
    if s.get(Agent, DEMO_AGENT_ID) is None:
        s.add(Agent(id=DEMO_AGENT_ID, name="ShopPilot (demo)", operator="Acme AI Agents",
                    api_key_hash=signing.hash_api_key(secrets.token_urlsafe(32))))
    if s.execute(select(ProtectionPolicy)).first() is None:
        s.add(ProtectionPolicy(code="AGP-STD", version=1, name="AgentGuard Standard Protection (simulated)",
                               deductible_cents=1_000, per_claim_limit_cents=50_000, aggregate_limit_cents=200_000,
                               auto_approve_limit_cents=20_000,
                               covered_incident_types=["OVERCHARGE", "QUANTITY_MISMATCH", "PRODUCT_MISMATCH",
                                                       "DUPLICATE_CHARGE"],
                               exclusions=["CUSTOMER_APPROVED_DEVIATION"]))
    s.flush()


class Step(BaseModel):
    action: str
    entity_id: str
    actor: str
    at: str
    data: dict


class ScenarioResult(BaseModel):
    scenario: int
    title: str
    expected: str
    correlation_id: str
    customer_id: str
    mandate_id: str
    transaction_ids: list[str]
    final_states: dict[str, str]
    steps: list[Step]


def _customer(s: Session, name: str | None = None) -> Customer:
    tag = secrets.token_hex(3)
    return mandates.create_customer(s, name or f"Demo Customer {tag}", f"demo+{tag}@agentguard.example")


SHOES = ProductConstraint(category="running shoes", required_attributes={"size": "38"})


def _mandate(s: Session, customer: Customer, *, intent="Buy one pair of running shoes, size 38, for no more than $150.",
             product: ProductConstraint = SHOES, max_amount=15_000, max_qty=1, currency="USD"):
    c = MandateConstraints(max_amount_cents=max_amount, currency=currency, max_quantity=max_qty, max_executions=1,
                           allowed_merchants=ALLOWED, product=product)
    m, _ = mandates.create_mandate(s, customer_id=customer.id, agent_id=DEMO_AGENT_ID, intent_text=intent,
                                   constraints=c, expires_at=utcnow() + timedelta(days=7))
    return m


def _shoe(merchant="mer_stride", qty=1, unit=12_990, size="38", sku="RUN-PEG-38", name="Pegasus Trail Running Shoe"):
    return dict(merchant_id=merchant, sku=sku, product_name=name, product_category="running shoes",
                attributes={"size": size}, quantity=qty, unit_amount_cents=unit, amount_cents=unit * qty,
                currency="USD")


def _attempt(mandate_id: str, key: str | None = None, **kw):
    """Submit one attempt in its own DB transaction, then deliver provider webhooks."""
    with session_factory()() as s:
        agent = s.get(Agent, DEMO_AGENT_ID)
        req = AttemptRequest(idempotency_key=key or f"idem_{secrets.token_hex(8)}", mandate_id=mandate_id, **kw)
        res, evts = transactions.submit_attempt(s, agent, req)
        s.commit()
    events.deliver(evts)
    return res


TITLES = {
    1: ("Correct purchase", "Transaction approved, executed and verified"),
    2: ("Duplicate purchase", "Second execution blocked (DUPLICATE_EXECUTION); idempotent replay returns original"),
    3: ("Wrong quantity", "Transaction rejected (QUANTITY_EXCEEDED)"),
    4: ("Excessive amount", "Held for explicit customer approval (BUDGET_EXCEEDED)"),
    5: ("Incorrect completed purchase", "Mismatch detected after execution; merchant refund recovers funds"),
    6: ("Merchant refund fails", "Refund declined; protection claim evaluated and simulated payout issued"),
}


def run(n: int, customer_name: str | None = None) -> ScenarioResult:
    if n not in TITLES:
        raise ValueError("scenario must be 1..6")
    cid = new_correlation_id()
    token = correlation_id.set(cid)
    try:
        with session_factory()() as s:
            ensure_fixtures(s)
            cust = _customer(s, customer_name)
            m = _mandate(s, cust)
            s.commit()
        tx_ids: list[str] = []
        if n == 1:
            tx_ids.append(_attempt(m.id, **_shoe()).transaction_id)
        elif n == 2:
            key = f"idem_{secrets.token_hex(8)}"
            first = _attempt(m.id, key=key, **_shoe())
            replay = _attempt(m.id, key=key, **_shoe())
            assert replay.replayed and replay.transaction_id == first.transaction_id
            tx_ids += [first.transaction_id, _attempt(m.id, **_shoe()).transaction_id]
        elif n == 3:
            tx_ids.append(_attempt(m.id, **_shoe(qty=2, unit=6_000)).transaction_id)
        elif n == 4:
            tx_ids.append(_attempt(m.id, **_shoe(unit=17_900)).transaction_id)
        elif n == 5:
            tx_ids.append(_attempt(m.id, **_shoe(merchant="mer_quickkicks")).transaction_id)
        elif n == 6:
            tx_ids.append(_attempt(m.id, **_shoe(merchant="mer_finalsale", unit=13_900)).transaction_id)
        with session_factory()() as s:
            states = {t: s.get(Transaction, t).state for t in tx_ids}
            recs = s.execute(select(AuditRecord).where(AuditRecord.correlation_id == cid)
                             .order_by(AuditRecord.seq)).scalars().all()
            steps = [Step(action=r.action, entity_id=r.entity_id, actor=r.actor, at=r.at.isoformat(), data=r.data)
                     for r in recs]
        title, expected = TITLES[n]
        return ScenarioResult(scenario=n, title=title, expected=expected, correlation_id=cid, customer_id=cust.id,
                              mandate_id=m.id, transaction_ids=tx_ids, final_states=states, steps=steps)
    finally:
        correlation_id.reset(token)


# ---- background seed data: varied customers / products, all driven through the real engine ----

SEED_PEOPLE = ["Ana Souza", "Marcus Lee", "Priya Nair", "Diego Alvarez", "Sofia Rossi", "Kenji Tanaka",
               "Fatima Diallo", "Lucas Martins", "Emma Schmidt", "Omar Haddad"]
SEED_PRODUCTS = [
    ("wireless headphones", {"color": "black"}, "AUD-NC700-BLK", "NC700 Noise-Cancelling Headphones", 24_900),
    ("coffee beans", {"roast": "medium"}, "COF-ETH-1KG", "Ethiopia Yirgacheffe 1kg", 3_800),
    ("running shoes", {"size": "41"}, "RUN-PEG-41", "Pegasus Trail Running Shoe", 13_490),
    ("desk lamp", {"color": "white"}, "LMP-ARC-WHT", "Arc LED Desk Lamp", 7_900),
    ("yoga mat", {"color": "green"}, "YGA-PRO-GRN", "ProGrip Yoga Mat", 5_400),
]


def seed(rounds: int = 2) -> dict:
    import random
    rng = random.Random(42)
    with session_factory()() as s:
        ensure_fixtures(s)
        s.commit()
    created = 0
    plan: list[tuple[str, Callable]] = []
    for r in range(rounds):
        for i, person in enumerate(SEED_PEOPLE):
            cat, attrs, sku, name, price = SEED_PRODUCTS[(i + r) % len(SEED_PRODUCTS)]
            variant = rng.choice(["ok", "ok", "ok", "dup", "qty", "over", "unlisted", "qk", "fs", "pb"])
            plan.append((person, lambda c=cat, a=attrs, k=sku, n=name, p=price, v=variant, who=person:
                         _seed_one(who, c, a, k, n, p, v)))
    for _, fn in plan:
        fn()
        created += 1
    for n in range(1, 7):
        run(n, customer_name=f"Scenario {n} Customer")
    _seed_extras()
    return {"seeded_flows": created + 3, "scenarios": 6}


def _seed_extras() -> None:
    """Items that need an operator: a claim above the auto-approval limit and pending approvals."""
    token = correlation_id.set(new_correlation_id())
    try:
        with session_factory()() as s:
            cust = _customer(s, "Helena Costa")
            m = _mandate(s, cust, intent="Buy one pair of premium running shoes, size 38, up to $450.",
                         max_amount=45_000)
            pending = [_mandate(s, _customer(s, who), intent=f"Buy one {cat} for no more than ${b / 100:.0f}.",
                                product=ProductConstraint(category=cat, required_attributes=a), max_amount=b)
                       for who, cat, a, b in (("Rafael Lima", "wireless headphones", {"color": "black"}, 22_000),
                                              ("Chloé Martin", "desk lamp", {"color": "white"}, 6_500))]
            s.commit()
        _attempt(m.id, **_shoe(merchant="mer_finalsale", unit=39_900, sku="RUN-ELITE-38", name="Elite Carbon Racer"))
        _attempt(pending[0].id, merchant_id="mer_stride", sku="AUD-NC700-BLK", product_name="NC700 Headphones",
                 product_category="wireless headphones", attributes={"color": "black"}, quantity=1,
                 unit_amount_cents=24_900, amount_cents=24_900, currency="USD")
        _attempt(pending[1].id, merchant_id="mer_stride", sku="LMP-ARC-WHT", product_name="Arc LED Desk Lamp",
                 product_category="desk lamp", attributes={"color": "white"}, quantity=1,
                 unit_amount_cents=7_900, amount_cents=7_900, currency="USD")
    finally:
        correlation_id.reset(token)


def _seed_one(person, cat, attrs, sku, name, price, variant):
    token = correlation_id.set(new_correlation_id())
    try:
        with session_factory()() as s:
            cust = _customer(s, person)
            budget = price + 2_000
            m = _mandate(s, cust, intent=f"Buy one {cat} for no more than ${budget / 100:.0f}.",
                         product=ProductConstraint(category=cat, required_attributes=attrs), max_amount=budget)
            s.commit()
        base = dict(sku=sku, product_name=name, product_category=cat, attributes=attrs, quantity=1,
                    unit_amount_cents=price, amount_cents=price, currency="USD")
        merchant = {"qk": "mer_quickkicks", "fs": "mer_finalsale", "pb": "mer_pricebump",
                    "unlisted": "mer_unlisted"}.get(variant, "mer_stride")
        if variant == "qty":
            base.update(quantity=2, amount_cents=price * 2)
        if variant == "over":
            base.update(unit_amount_cents=budget + 1_500, amount_cents=budget + 1_500)
        _attempt(m.id, merchant_id=merchant, **base)
        if variant == "dup":
            _attempt(m.id, merchant_id=merchant, **base)
    finally:
        correlation_id.reset(token)
