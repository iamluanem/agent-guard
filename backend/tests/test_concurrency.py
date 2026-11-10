"""Concurrency-safe duplicate detection. Meaningful on PostgreSQL (row locks); also runs on SQLite."""
import secrets
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.db import session_factory
from app.models import Agent, Mandate, Transaction
from app.services import transactions
from app.services.scenarios import DEMO_AGENT_ID, _shoe
from app.services.transactions import AttemptRequest

N = 12


def _submit(mandate_id: str, key: str):
    with session_factory()() as s:
        agent = s.get(Agent, DEMO_AGENT_ID)
        res, _ = transactions.submit_attempt(s, agent, AttemptRequest(
            idempotency_key=key, mandate_id=mandate_id, auto_execute=False, **_shoe()))
        s.commit()
        return res


def test_parallel_attempts_execute_exactly_once(world):
    mid = world["mandate"].id
    with ThreadPoolExecutor(N) as pool:
        results = list(pool.map(lambda _: _submit(mid, f"idem_{secrets.token_hex(8)}"), range(N)))
    states = sorted(r.state for r in results)
    assert states.count("AUTHORIZED") == 1 and states.count("BLOCKED") == N - 1
    with session_factory()() as s:
        assert s.get(Mandate, mid).executions_used == 1


def test_parallel_same_idempotency_key_creates_one_transaction(world):
    mid, key = world["mandate"].id, f"idem_{secrets.token_hex(8)}"
    with ThreadPoolExecutor(N) as pool:
        results = list(pool.map(lambda _: _submit(mid, key), range(N)))
    assert len({r.transaction_id for r in results}) == 1
    assert sum(not r.replayed for r in results) == 1
    with session_factory()() as s:
        assert s.execute(select(func.count()).select_from(Transaction)).scalar_one() == 1
