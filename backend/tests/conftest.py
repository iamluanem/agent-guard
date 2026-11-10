"""Tests run against PostgreSQL when AG_TEST_DATABASE_URL is set (CI), otherwise SQLite."""
import os

os.environ.setdefault("AG_SIGNING_SECRET", "test-signing-secret-not-for-prod")
os.environ.setdefault("AG_WEBHOOK_SECRET", "test-webhook-secret-not-for-prod")
os.environ.setdefault("AG_ENV", "test")
os.environ.pop("AG_ADMIN_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import (
    db,  # noqa: E402
    models,  # noqa: E402,F401
)

TEST_URL = os.environ.get("AG_TEST_DATABASE_URL", "sqlite:///./test.db")
IS_PG = TEST_URL.startswith("postgresql")


@pytest.fixture(autouse=True)
def database():
    if TEST_URL.startswith("sqlite") and os.path.exists("test.db"):
        os.remove("test.db")
    db.configure(TEST_URL)
    db.Base.metadata.drop_all(db.engine())
    db.Base.metadata.create_all(db.engine())
    yield
    db.engine().dispose()


@pytest.fixture
def session():
    with db.session_factory()() as s:
        yield s


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture
def world(session):
    """Customer + agent + merchants + policy + a single-use running-shoe mandate."""
    from app.services import scenarios
    scenarios.ensure_fixtures(session)
    cust = scenarios._customer(session, "Test Customer")
    m = scenarios._mandate(session, cust)
    session.commit()
    return {"customer": cust, "mandate": m, "agent_id": scenarios.DEMO_AGENT_ID}
