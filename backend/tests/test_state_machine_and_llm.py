import pytest

from app.adapters.llm import IntentExtraction, RulesProvider
from app.domain.enums import TxState as S
from app.domain.state_machine import TERMINAL, InvalidTransition, assert_transition, is_reachable


def test_happy_path_transitions():
    path = [S.CREATED, S.AUTHORIZED, S.EXECUTING, S.COMPLETED, S.DISPUTED, S.RECOVERY_PENDING, S.REFUNDED]
    for a, b in zip(path, path[1:], strict=False):
        assert_transition(a, b)


@pytest.mark.parametrize("a,b", [(S.CREATED, S.COMPLETED), (S.BLOCKED, S.AUTHORIZED),
                                 (S.REFUNDED, S.COMPLETED), (S.COMPLETED, S.EXECUTING)])
def test_invalid_transitions(a, b):
    with pytest.raises(InvalidTransition):
        assert_transition(a, b)


def test_terminal_states():
    assert TERMINAL == {S.BLOCKED, S.FAILED, S.REFUNDED}
    assert is_reachable(S.AUTHORIZED, S.REFUNDED) and not is_reachable(S.COMPLETED, S.EXECUTING)


def test_rules_intent_extraction_canonical_example():
    r = RulesProvider().structured("", "Buy one pair of running shoes, size 38, for no more than $150.",
                                   IntentExtraction)
    assert (r.product_category, r.quantity, r.attributes, r.max_amount_cents, r.currency) == \
           ("running shoes", 1, {"size": "38"}, 15_000, "USD")
    assert r.ambiguities == []


def test_rules_intent_flags_ambiguity():
    r = RulesProvider().structured("", "get me some headphones", IntentExtraction)
    assert r.max_amount_cents is None and "no budget stated" in r.ambiguities and r.confidence < 0.9
