"""LLM provider abstraction. ADVISORY ONLY.

The LLM interprets intent and writes explanations. Its output is schema-validated and is never
an input to an authorization, refund or reimbursement decision — those come from the
deterministic policy engine. A deterministic rules provider is the default and the fallback.
"""
import json
import logging
import re
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class IntentExtraction(BaseModel):
    product_category: str
    quantity: int = Field(ge=1)
    attributes: dict[str, str] = {}
    max_amount_cents: int | None = Field(default=None, gt=0)
    currency: str = "USD"
    confidence: float = Field(ge=0, le=1)
    ambiguities: list[str] = []


class IncidentNarrative(BaseModel):
    summary: str
    recommended_action: str
    customer_message: str


class LLMProvider(Protocol):
    name: str

    def structured(self, system: str, prompt: str, schema: type[T]) -> T: ...


_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9, "ten": 10, "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3}
_CURRENCY = [("R$", "BRL"), ("€", "EUR"), ("£", "GBP"), ("$", "USD")]


class RulesProvider:
    """Deterministic fallback: regex intent parsing and templated narratives. No network."""

    name = "rules"

    def structured(self, system: str, prompt: str, schema: type[T]) -> T:
        if schema is IntentExtraction:
            return self._intent(prompt)  # type: ignore[return-value]
        if schema is IncidentNarrative:
            return self._narrative(json.loads(prompt))  # type: ignore[return-value]
        raise NotImplementedError(schema.__name__)

    @staticmethod
    def _intent(text: str) -> IntentExtraction:
        t = text.strip()
        low = t.lower()
        amb: list[str] = []
        qty = 1
        m = re.search(r"\b(?:buy|purchase|order|get|compre|comprar)\s+(\d+|[a-zà-ú]+)\b", low)
        if m:
            tok = m.group(1)
            qty = int(tok) if tok.isdigit() else _WORDS.get(tok, 1)
        else:
            amb.append("quantity not stated; assumed 1")
        cat = re.search(r"\b(?:buy|purchase|order|get|compre|comprar)\s+(?:\d+|[a-zà-ú]+)\s+"
                        r"(?:pairs? of\s+|par(?:es)? de\s+)?([a-zà-ú][a-zà-ú\s-]*?)"
                        r"(?=,|\s+for\b|\s+size\b|\s+in\b|\s+por\b|\.|$)", low)
        category = cat.group(1).strip() if cat else ""
        if not category:
            amb.append("product category unclear")
            category = "unspecified"
        attrs: dict[str, str] = {}
        for key, pat in (("size", r"\b(?:size|tamanho)\s+([\w.]+)"), ("color", r"\b(?:color|colour|cor)\s+(\w+)")):
            if (am := re.search(pat, low)):
                attrs[key] = am.group(1).rstrip(".")
        currency, amount = "USD", None
        am2 = re.search(r"(r\$|€|£|\$)\s*(\d+(?:[.,]\d{1,2})?)", low)
        if am2:
            sym = am2.group(1).upper()
            currency = next(code for s, code in _CURRENCY if s.upper() == sym)
            amount = round(float(am2.group(2).replace(",", ".")) * 100)
        else:
            amb.append("no budget stated")
        if not re.search(r"no more than|up to|under|max|at most|até|no máximo|budget", low) and amount:
            amb.append("amount found but not explicitly stated as a ceiling")
        conf = max(0.3, 0.95 - 0.2 * len(amb))
        return IntentExtraction(product_category=category, quantity=qty, attributes=attrs,
                                max_amount_cents=amount, currency=currency, confidence=round(conf, 2),
                                ambiguities=amb)

    @staticmethod
    def _narrative(ev: dict) -> IncidentNarrative:
        amt = ev.get("disputed_amount_cents", 0) / 100
        cur = ev.get("currency", "USD")
        def val(code: str, v):
            return f"{int(v) / 100:.2f} {cur}" if code == "OVERCHARGE" and str(v).isdigit() else v

        findings = "; ".join(f"{f['code'].lower().replace('_', ' ')} (expected {val(f['code'], f.get('expected'))}, "
                             f"got {val(f['code'], f.get('actual'))})" for f in ev.get("findings", []))
        action = ("Request merchant cancellation" if ev.get("merchant_supports_cancellation")
                  else "Request a refund from the merchant")
        return IncidentNarrative(
            summary=f"{ev.get('incident_type', 'Mismatch').replace('_', ' ').capitalize()} on transaction "
                    f"{ev.get('transaction_id')}: {findings}. Disputed amount {amt:.2f} {cur}.",
            recommended_action=f"{action} for {amt:.2f} {cur}; escalate to a protection claim if it fails.",
            customer_message=f"We detected a problem with a purchase your agent made and started recovering "
                             f"{amt:.2f} {cur}. We'll keep you updated.")


class _HttpJsonProvider:
    name = "http"

    def __init__(self, fallback: RulesProvider) -> None:
        self.fallback = fallback

    def _call(self, system: str, prompt: str, schema: dict) -> str:
        raise NotImplementedError

    def structured(self, system: str, prompt: str, schema: type[T]) -> T:
        try:
            raw = self._call(system, prompt, schema.model_json_schema())
            return schema.model_validate_json(raw)
        except (httpx.HTTPError, ValidationError, KeyError, ValueError) as exc:
            log.warning("llm call failed, using deterministic fallback", extra={"fields": {"error": str(exc)}})
            return self.fallback.structured(system, prompt, schema)


class AnthropicProvider(_HttpJsonProvider):
    name = "anthropic"

    def _call(self, system, prompt, schema):
        st = get_settings()
        assert st.anthropic_api_key
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=20, headers={
            "x-api-key": st.anthropic_api_key.get_secret_value(), "anthropic-version": "2023-06-01"},
            json={"model": st.llm_model, "max_tokens": 1024, "system": system,
                  "tools": [{"name": "emit", "description": "Return the result", "input_schema": schema}],
                  "tool_choice": {"type": "tool", "name": "emit"},
                  "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        block = next(b for b in r.json()["content"] if b["type"] == "tool_use")
        return json.dumps(block["input"])


class OpenAIProvider(_HttpJsonProvider):
    name = "openai"

    def _call(self, system, prompt, schema):
        st = get_settings()
        assert st.openai_api_key
        r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=20,
                       headers={"Authorization": f"Bearer {st.openai_api_key.get_secret_value()}"},
                       json={"model": st.llm_model, "messages": [{"role": "system", "content": system},
                                                                 {"role": "user", "content": prompt}],
                             "response_format": {"type": "json_object"}})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def get_provider() -> LLMProvider:
    st = get_settings()
    rules = RulesProvider()
    if st.llm_provider == "anthropic" and st.anthropic_api_key:
        return AnthropicProvider(rules)
    if st.llm_provider == "openai" and st.openai_api_key:
        return OpenAIProvider(rules)
    return rules


INTENT_SYSTEM = ("Extract purchase constraints from a customer's instruction to a shopping agent. "
                 "Amounts in minor units. List anything ambiguous. Do not invent constraints.")
NARRATIVE_SYSTEM = ("Explain a transaction anomaly for an operations analyst and the customer. Be factual, "
                    "cite the findings, never promise reimbursement.")


def extract_intent(text: str) -> tuple[IntentExtraction, str]:
    p = get_provider()
    return p.structured(INTENT_SYSTEM, text, IntentExtraction), p.name


def explain_incident(evidence: dict) -> tuple[IncidentNarrative, str]:
    p = get_provider()
    return p.structured(NARRATIVE_SYSTEM, json.dumps(evidence, default=str), IncidentNarrative), p.name
