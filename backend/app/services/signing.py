"""Mandate signing. Every mandate version is canonicalized, hashed and HMAC-signed.

The signature is re-verified on every policy evaluation, so a row edited directly in the
database (bypassing the API) is rejected as SIGNATURE_INVALID.
"""
import hashlib
import hmac
from datetime import datetime

from app.config import get_settings
from app.services.audit import canonical


def mandate_payload(*, mandate_id: str, version: int, customer_id: str, agent_id: str,
                    intent_text: str, constraints: dict, expires_at: datetime | None) -> str:
    return canonical({
        "mandate_id": mandate_id, "version": version, "customer_id": customer_id, "agent_id": agent_id,
        "intent": intent_text, "constraints": constraints,
        "expires_at": expires_at.isoformat() if expires_at else None,
    })


def sign(payload: str) -> tuple[str, str]:
    key = get_settings().signing_secret.get_secret_value().encode()
    content_hash = hashlib.sha256(payload.encode()).hexdigest()
    signature = hmac.new(key, content_hash.encode(), hashlib.sha256).hexdigest()
    return content_hash, signature


def verify(payload: str, content_hash: str, signature: str) -> bool:
    expected_hash, expected_sig = sign(payload)
    return hmac.compare_digest(expected_hash, content_hash) and hmac.compare_digest(expected_sig, signature)


def verify_webhook(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    key = get_settings().webhook_secret.get_secret_value().encode()
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def sign_webhook(body: bytes) -> str:
    key = get_settings().webhook_secret.get_secret_value().encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
