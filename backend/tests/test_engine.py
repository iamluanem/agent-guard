"""Idempotency, webhooks, authorization lifecycle, claims review, tamper evidence."""
import json
import secrets
from datetime import timedelta

from sqlalchemy import select, update

from app.adapters.payments import WebhookEvent
from app.db import session_factory, utcnow
from app.models import AuditRecord, MandateVersion, Merchant, RecoveryAction, Transaction
from app.services import events, signing
from app.services.scenarios import _shoe


def agent_key(client):
    return client.post("/v1/agents", json={"name": "T", "operator": "Op"}).json()


def mandate_for(client, world, agent_id, **constraints):
    body = {"customer_id": world["customer"].id, "agent_id": agent_id, "intent_text": "shoes",
            "constraints": {"max_amount_cents": 15_000, "currency": "USD", "max_quantity": 1,
                            "product": {"category": "running shoes", "required_attributes": {"size": "38"}},
                            **constraints}}
    r = client.post("/v1/mandates", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def submit(client, key, mandate_id, idem=None, **kw):
    body = {"idempotency_key": idem or f"idem_{secrets.token_hex(6)}", "mandate_id": mandate_id, **_shoe(), **kw}
    return client.post("/v1/agent/transactions", json=body, headers={"X-Agent-Key": key})


def test_agent_auth_required(client, world):
    r = client.post("/v1/agent/transactions", json={})
    assert r.status_code in (401, 422)
    body = {"idempotency_key": "idem_12345678", **_shoe()}
    assert client.post("/v1/agent/transactions", json=body, headers={"X-Agent-Key": "nope"}).status_code == 401


def test_idempotent_replay_and_key_conflict(client, world):
    a = agent_key(client)
    m = mandate_for(client, world, a["id"])
    r1 = submit(client, a["api_key"], m["id"], idem="idem_fixed_001")
    r2 = submit(client, a["api_key"], m["id"], idem="idem_fixed_001")
    assert r1.status_code == 201 and r2.status_code == 200
    assert r2.json()["replayed"] and r1.json()["transaction_id"] == r2.json()["transaction_id"]
    r3 = submit(client, a["api_key"], m["id"], idem="idem_fixed_001", quantity=1, unit_amount_cents=1,
                amount_cents=1)
    assert r3.status_code == 409


def test_no_mandate_blocked(client, world):
    a = agent_key(client)
    r = submit(client, a["api_key"], None)
    assert r.json()["state"] == "BLOCKED" and r.json()["violations"][0]["code"] == "MANDATE_MISSING"


def test_revoked_mandate_blocks(client, world):
    a = agent_key(client)
    m = mandate_for(client, world, a["id"])
    client.post(f"/v1/mandates/{m['id']}/revoke", json={"reason": "changed my mind"})
    r = submit(client, a["api_key"], m["id"])
    assert "MANDATE_REVOKED" in {v["code"] for v in r.json()["violations"]}


def test_amend_creates_new_signed_version(client, world):
    a = agent_key(client)
    m = mandate_for(client, world, a["id"])
    body = {"intent_text": "shoes, bigger budget",
            "constraints": {**m["constraints"], "max_amount_cents": 20_000}}
    r = client.post(f"/v1/mandates/{m['id']}/amend", json=body).json()
    assert r["current_version"] == 2 and len(r["versions"]) == 2 and r["signature_valid"]
    assert r["versions"][0]["constraints"]["max_amount_cents"] == 15_000  # v1 untouched


def test_tampered_mandate_rejected(client, world, session):
    a = agent_key(client)
    m = mandate_for(client, world, a["id"])
    session.execute(update(MandateVersion).where(MandateVersion.mandate_id == m["id"]).values(
        constraints={**m["constraints"], "max_amount_cents": 999_999}))
    session.commit()
    r = submit(client, a["api_key"], m["id"])
    assert "SIGNATURE_INVALID" in {v["code"] for v in r.json()["violations"]}


def _evt(tx_id, source, type_, seq, **payload):
    return WebhookEvent(event_id=f"evt_{secrets.token_hex(6)}", source=source, type=type_, transaction_id=tx_id,
                        sequence=seq, payload=payload)


def _authorized_tx(client, world):
    a = agent_key(client)
    m = mandate_for(client, world, a["id"])
    r = submit(client, a["api_key"], m["id"], auto_execute=False)
    return r.json()["transaction_id"]


def test_out_of_order_and_duplicate_events(client, world, session):
    from app.services.transactions import execute
    tx_id = _authorized_tx(client, world)
    tx = session.get(Transaction, tx_id)
    execute(session, tx)  # discard simulated events; we deliver our own
    session.commit()
    auth = _evt(tx_id, "payment", "payment.authorized", 1, payment_ref="pay_x")
    cap = _evt(tx_id, "payment", "payment.captured", 2, amount_cents=12_990)
    ful = _evt(tx_id, "merchant", "order.fulfilled", 1, order={"sku": "RUN-PEG-38", "quantity": 1,
               "product_category": "running shoes", "attributes": {"size": "38"}})
    statuses = []
    for e in (cap, ful, cap, auth):  # captured before authorized + duplicate re-delivery
        with session_factory()() as s:
            st, follow = events.ingest(s, e)
            s.commit()
        statuses.append(st)
    assert statuses == ["DEFERRED", "APPLIED", "DUPLICATE", "APPLIED"]
    session.expire_all()
    tx = session.get(Transaction, tx_id)
    assert tx.state == "COMPLETED" and tx.event_cursors == {"payment": 2, "merchant": 1}
    stale = _evt(tx_id, "payment", "payment.authorized", 1)
    with session_factory()() as s:
        assert events.ingest(s, stale)[0] == "STALE"


def test_webhook_signature_enforced(client, world):
    tx_id = _authorized_tx(client, world)
    body = json.dumps({"event_id": "evt_sig_1", "type": "payment.authorized", "transaction_id": tx_id,
                       "sequence": 1, "payload": {}}).encode()
    assert client.post("/v1/webhooks/payment", content=body, headers={"X-Signature": "bad"}).status_code == 401
    ok = client.post("/v1/webhooks/payment", content=body, headers={"X-Signature": signing.sign_webhook(body)})
    assert ok.status_code == 200 and ok.json()["status"] == "APPLIED"
    again = client.post("/v1/webhooks/payment", content=body, headers={"X-Signature": signing.sign_webhook(body)})
    assert again.json()["status"] == "DUPLICATE"


def test_transient_merchant_failure_retries_then_claim_in_review(client, world, session):
    """PriceBump overcharges 20% and its refund API is down: retries -> escalation -> claim."""
    a = agent_key(client)
    m = mandate_for(client, world, a["id"])
    session.execute(update(Merchant).where(Merchant.id == "mer_pricebump").values(fulfillment_behavior="overcharge"))
    session.commit()
    r = submit(client, a["api_key"], m["id"], merchant_id="mer_pricebump")
    tx = client.get(f"/v1/transactions/{r.json()['transaction_id']}").json()
    inc = client.get(f"/v1/incidents/{tx['incident_id']}").json()
    assert inc["type"] == "OVERCHARGE" and inc["disputed_amount_cents"] == 12_990 // 5
    assert inc["recovery_actions"][0]["status"] == "RETRY_SCHEDULED"
    for _ in range(3):  # fast-forward the backoff clock instead of sleeping
        session.execute(update(RecoveryAction).values(next_attempt_at=utcnow() - timedelta(seconds=1)))
        session.commit()
        client.post("/v1/recovery/run-due")
    inc = client.get(f"/v1/incidents/{tx['incident_id']}").json()
    assert inc["status"] == "CLOSED"
    assert inc["recovery_actions"][0]["attempts"] == 3
    assert inc["claim"]["status"] == "PAID"  # 2598 - 1000 deductible = 1598, below auto-approve limit


def test_large_claim_goes_to_human_review(client, world, session):
    from app.models import ProtectionPolicy
    session.execute(update(ProtectionPolicy).values(auto_approve_limit_cents=5_000))
    session.commit()
    res = client.post("/v1/demo/scenarios/6").json()
    claim = client.get("/v1/claims").json()[0]
    assert claim["status"] == "IN_REVIEW" and claim["approved_amount_cents"] == 12_900
    # Reviewer cannot exceed the deterministic cap
    r = client.post(f"/v1/claims/{claim['id']}/review",
                    json={"approve": True, "reviewer": "ops@agentguard", "amount_cents": 99_999})
    assert r.json()["status"] == "PAID" and r.json()["approved_amount_cents"] == 12_900
    assert res["customer_id"]


def test_audit_tamper_detected(client, world, session):
    client.post("/v1/demo/scenarios/1")
    assert client.get("/v1/audit/verify").json()["valid"]
    rec = session.execute(select(AuditRecord).order_by(AuditRecord.seq).limit(1).offset(3)).scalar_one()
    session.execute(update(AuditRecord).where(AuditRecord.seq == rec.seq).values(data={"forged": True}))
    session.commit()
    v = client.get("/v1/audit/verify").json()
    assert not v["valid"] and v["broken_at_seq"] == rec.seq


def test_intent_extraction_is_advisory(client):
    text = "Buy one pair of running shoes, size 38, for no more than $150."
    r = client.post("/v1/intents/extract", json={"text": text})
    body = r.json()
    assert body["advisory"] is True and body["proposal"]["max_amount_cents"] == 15_000


def test_blank_optional_settings_are_unset():
    from app.config import Settings
    s = Settings(signing_secret="a", webhook_secret="b", admin_api_key="", redis_url=" ", openai_api_key="")
    assert s.admin_api_key is None and s.redis_url is None and s.openai_api_key is None


def test_admin_key_enforced_when_set(client, monkeypatch):
    from pydantic import SecretStr

    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "admin_api_key", SecretStr("k1"))
    assert client.get("/v1/metrics/overview").status_code == 401
    assert client.get("/v1/metrics/overview", headers={"X-Admin-Key": "k1"}).status_code == 200
