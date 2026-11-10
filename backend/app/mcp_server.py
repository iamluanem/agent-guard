"""MCP server exposing AgentGuard as authorized tools for shopping agents.

The agent never talks to a payment provider directly: its only purchase tool routes through
AgentGuard's deterministic policy engine with the agent's own API key.

    AGENTGUARD_URL=http://localhost:8000 AGENTGUARD_AGENT_KEY=agk_... python -m app.mcp_server
"""
import os
import uuid

import httpx
from mcp.server.mcpserver import MCPServer

BASE = os.environ.get("AGENTGUARD_URL", "http://localhost:8000")

server = MCPServer(
    name="agentguard",
    instructions="Use guarded_purchase for every purchase. A BLOCKED or AWAITING_APPROVAL result is final for "
                 "you: report it to the customer, never retry with altered values to get past a rule.",
)


def _headers() -> dict:
    key = os.environ.get("AGENTGUARD_AGENT_KEY")
    if not key:
        raise RuntimeError("AGENTGUARD_AGENT_KEY is not set")
    return {"X-Agent-Key": key}


@server.tool()
def guarded_purchase(mandate_id: str, merchant_id: str, sku: str, product_name: str, product_category: str,
                     quantity: int, unit_amount_cents: int, currency: str, attributes: dict[str, str] | None = None,
                     idempotency_key: str | None = None) -> dict:
    """Validate and execute a purchase under a customer mandate. Safe to retry with the same idempotency_key."""
    body = {"idempotency_key": idempotency_key or f"mcp_{uuid.uuid4().hex}", "mandate_id": mandate_id,
            "merchant_id": merchant_id, "sku": sku, "product_name": product_name,
            "product_category": product_category, "attributes": attributes or {}, "quantity": quantity,
            "unit_amount_cents": unit_amount_cents, "amount_cents": unit_amount_cents * quantity,
            "currency": currency}
    r = httpx.post(f"{BASE}/v1/agent/transactions", json=body, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


@server.tool()
def transaction_status(transaction_id: str) -> dict:
    """Current state of one of this agent's transactions."""
    r = httpx.get(f"{BASE}/v1/agent/transactions/{transaction_id}", headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    server.run("stdio")
