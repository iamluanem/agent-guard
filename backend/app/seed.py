"""python -m app.seed  — creates fixtures and realistic history by driving the real engine."""
import json

from sqlalchemy import func, select

from app.db import session_factory
from app.models import Transaction
from app.observability import setup_logging
from app.services import scenarios


def main() -> None:
    setup_logging("WARNING")
    with session_factory()() as s:
        if s.execute(select(func.count()).select_from(Transaction)).scalar_one() > 0:
            print(json.dumps({"skipped": "database already has transactions"}))
            return
    print(json.dumps(scenarios.seed()))


if __name__ == "__main__":
    main()
