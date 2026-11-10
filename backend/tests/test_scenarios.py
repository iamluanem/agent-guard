"""The six required demo scenarios, end-to-end through the HTTP API."""
from app.domain.enums import TxState


def run(client, n):
    r = client.post(f"/v1/demo/scenarios/{n}")
    assert r.status_code == 200, r.text
    return r.json()


def test_scenario_1_correct_purchase(client):
    res = run(client, 1)
    assert list(res["final_states"].values()) == [TxState.COMPLETED]
    assert "execution.verified" in [s["action"] for s in res["steps"]]
    assert client.get("/v1/incidents").json() == []


def test_scenario_2_duplicate_blocked(client):
    res = run(client, 2)
    first, second = res["transaction_ids"]
    assert res["final_states"] == {first: TxState.COMPLETED, second: TxState.BLOCKED}
    tx = client.get(f"/v1/transactions/{second}").json()
    assert [v["code"] for v in tx["violations"]] == ["DUPLICATE_EXECUTION"]


def test_scenario_3_wrong_quantity(client):
    tx_id = run(client, 3)["transaction_ids"][0]
    tx = client.get(f"/v1/transactions/{tx_id}").json()
    assert tx["state"] == TxState.BLOCKED and {v["code"] for v in tx["violations"]} == {"QUANTITY_EXCEEDED"}
    assert tx["payment_ref"] is None  # never reached the payment provider


def test_scenario_4_requires_approval_then_approve(client):
    res = run(client, 4)
    tx_id = res["transaction_ids"][0]
    assert res["final_states"][tx_id] == TxState.AWAITING_APPROVAL
    r = client.post(f"/v1/transactions/{tx_id}/approve", json={"customer_id": res["customer_id"]})
    assert r.status_code == 200 and r.json()["state"] == TxState.COMPLETED
    assert client.get(f"/v1/transactions/{tx_id}").json()["approved_by_customer"] is True


def test_scenario_4_reject(client):
    res = run(client, 4)
    tx_id = res["transaction_ids"][0]
    r = client.post(f"/v1/transactions/{tx_id}/reject", json={"customer_id": res["customer_id"]})
    assert r.json()["state"] == TxState.BLOCKED
    assert client.post(f"/v1/transactions/{tx_id}/approve",
                       json={"customer_id": res["customer_id"]}).status_code == 409


def test_scenario_5_incident_and_refund(client):
    tx_id = run(client, 5)["transaction_ids"][0]
    assert client.get(f"/v1/transactions/{tx_id}").json()["state"] == TxState.REFUNDED
    inc = client.get("/v1/incidents").json()[0]
    assert inc["type"] == "QUANTITY_MISMATCH" and inc["status"] == "RECOVERED"
    assert inc["disputed_amount_cents"] == 12_990 and inc["summary"]
    m = client.get("/v1/metrics/overview").json()
    assert m["recovered_cents"] == 12_990 and m["simulated_payouts_cents"] == 0


def test_scenario_6_refund_fails_claim_paid(client):
    tx_id = run(client, 6)["transaction_ids"][0]
    tx = client.get(f"/v1/transactions/{tx_id}").json()
    assert tx["state"] == TxState.RECOVERY_PENDING  # money not recovered from merchant
    inc = client.get(f"/v1/incidents/{tx['incident_id']}").json()
    assert inc["type"] == "PRODUCT_MISMATCH"
    assert [a["status"] for a in inc["recovery_actions"]] == ["FAILED"]
    claim = inc["claim"]
    assert claim["status"] == "PAID"
    assert claim["approved_amount_cents"] == 13_900 - 1_000  # disputed minus deductible
    assert all(c["passed"] for c in claim["checks"])
    led = client.get("/v1/ledger").json()
    assert led["sandbox"] and led["balanced"] and all(e["sandbox"] for e in led["entries"])
    m = client.get("/v1/metrics/overview").json()
    assert m["simulated_payouts_cents"] == 12_900 and m["recovered_cents"] == 0


def test_metrics_separate_prevented_recovered_paid(client):
    for n in (3, 5, 6):
        run(client, n)
    m = client.get("/v1/metrics/overview").json()
    assert (m["prevented_loss_cents"], m["recovered_cents"], m["simulated_payouts_cents"]) == (12_000, 12_990, 12_900)


def test_audit_chain_valid_after_all_scenarios(client):
    for n in range(1, 7):
        run(client, n)
    v = client.get("/v1/audit/verify").json()
    assert v["valid"] and v["records_checked"] > 50
