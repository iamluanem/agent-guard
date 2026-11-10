"""Payment + merchant provider ports, with sandbox implementations.

Real providers (card acquirer, AP2 credential provider, merchant order APIs) plug in by
implementing these Protocols. Simulated adapters are explicitly flagged `simulated = True`
and every ledger movement they cause is written with sandbox=True.
"""
import secrets
from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class WebhookEvent:
    """What a provider would POST to /v1/webhooks/{source}."""

    event_id: str
    source: Literal["payment", "merchant"]
    type: str
    transaction_id: str
    sequence: int
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ChargeRequest:
    transaction_id: str
    idempotency_key: str
    merchant_id: str
    amount_cents: int
    currency: str
    sku: str
    product_category: str
    attributes: dict
    quantity: int
    unit_amount_cents: int


@dataclass(frozen=True)
class ChargeResult:
    accepted: bool
    payment_ref: str | None
    events: list[WebhookEvent]
    error: str | None = None


RecoveryOutcome = Literal["ACCEPTED", "DECLINED", "UNAVAILABLE", "UNSUPPORTED"]


@dataclass(frozen=True)
class RecoveryResult:
    outcome: RecoveryOutcome
    merchant_ref: str | None
    events: list[WebhookEvent]
    reason: str | None = None


class PaymentProvider(Protocol):
    simulated: bool

    def charge(self, req: ChargeRequest, merchant_behavior: str) -> ChargeResult: ...


class MerchantApi(Protocol):
    simulated: bool

    def cancel_order(self, tx_id: str, payment_ref: str, supports_cancellation: bool,
                     next_seq: int) -> RecoveryResult: ...

    def request_refund(self, tx_id: str, payment_ref: str, amount_cents: int, behavior: str,
                       next_seq: int) -> RecoveryResult: ...


def _evt(source, type_, tx, seq, **payload) -> WebhookEvent:
    return WebhookEvent(event_id=f"evt_{secrets.token_hex(8)}", source=source, type=type_,
                        transaction_id=tx, sequence=seq, payload=payload)


class MockPaymentProvider:
    """Idempotent sandbox PSP. Same idempotency key -> same payment_ref, no double charge."""

    simulated = True

    def __init__(self) -> None:
        self._by_key: dict[str, ChargeResult] = {}

    def charge(self, req: ChargeRequest, merchant_behavior: str) -> ChargeResult:
        if req.idempotency_key in self._by_key:
            prev = self._by_key[req.idempotency_key]
            return ChargeResult(prev.accepted, prev.payment_ref, events=[], error=prev.error)
        ref = f"pay_sbx_{secrets.token_hex(8)}"
        captured, fulfilled = req.amount_cents, {
            "sku": req.sku, "product_category": req.product_category,
            "attributes": dict(req.attributes), "quantity": req.quantity, "status": "processing"}
        # Merchant-side execution errors the agent did not request (scenario 5/6).
        if merchant_behavior == "double_quantity":
            fulfilled["quantity"] = req.quantity * 2
            captured = req.unit_amount_cents * req.quantity * 2
        elif merchant_behavior == "wrong_size":
            fulfilled["attributes"] = {**req.attributes, "size": "42"}
        elif merchant_behavior == "overcharge":
            captured = req.amount_cents + req.amount_cents // 5
        events = [
            _evt("payment", "payment.authorized", req.transaction_id, 1, payment_ref=ref),
            _evt("payment", "payment.captured", req.transaction_id, 2, payment_ref=ref,
                 amount_cents=captured, currency=req.currency),
            _evt("merchant", "order.fulfilled", req.transaction_id, 1, order=fulfilled),
        ]
        result = ChargeResult(True, ref, events)
        self._by_key[req.idempotency_key] = result
        return result


class SimulatedMerchantApi:
    simulated = True

    def cancel_order(self, tx_id, payment_ref, supports_cancellation, next_seq) -> RecoveryResult:
        if not supports_cancellation:
            return RecoveryResult("UNSUPPORTED", None, [], "merchant does not support cancellation")
        ref = f"can_sbx_{secrets.token_hex(6)}"
        return RecoveryResult("ACCEPTED", ref, [
            _evt("merchant", "refund.succeeded", tx_id, next_seq, merchant_ref=ref, via="cancellation")])

    def request_refund(self, tx_id, payment_ref, amount_cents, behavior, next_seq) -> RecoveryResult:
        ref = f"ref_sbx_{secrets.token_hex(6)}"
        if behavior == "unavailable":
            return RecoveryResult("UNAVAILABLE", None, [], "merchant API timeout (simulated)")
        if behavior == "decline":
            return RecoveryResult("ACCEPTED", ref, [
                _evt("merchant", "refund.failed", tx_id, next_seq, merchant_ref=ref,
                     reason="merchant_declined: item marked final sale")])
        return RecoveryResult("ACCEPTED", ref, [
            _evt("merchant", "refund.succeeded", tx_id, next_seq, merchant_ref=ref, amount_cents=amount_cents)])


_payment = MockPaymentProvider()
_merchant = SimulatedMerchantApi()


def payment_provider() -> PaymentProvider:
    return _payment


def merchant_api() -> MerchantApi:
    return _merchant
