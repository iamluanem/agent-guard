"""Background worker: outbox relay -> Redis Streams, recovery retries, deferred-event sweeper."""
import json
import logging
import signal
import time

from sqlalchemy import select

from app.config import get_settings
from app.db import session_factory, utcnow
from app.models import DomainEvent
from app.observability import log, setup_logging
from app.services import events, recovery

logger = logging.getLogger("agentguard.worker")
STREAM = "agentguard.events"
_running = True


def relay_outbox(redis_client) -> int:
    with session_factory()() as s:
        batch = s.execute(select(DomainEvent).where(DomainEvent.published_at.is_(None))
                          .order_by(DomainEvent.id).limit(200).with_for_update(skip_locked=True)).scalars().all()
        for e in batch:
            if redis_client is not None:
                redis_client.xadd(STREAM, {"id": e.id, "type": e.type, "entity_id": e.entity_id,
                                           "correlation_id": e.correlation_id,
                                           "payload": json.dumps(e.payload, default=str)}, maxlen=100_000)
            e.published_at = utcnow()
        s.commit()
        return len(batch)


def tick(redis_client) -> dict:
    with session_factory()() as s:
        evts = recovery.run_due(s)
        s.commit()
    events.deliver(evts)
    with session_factory()() as s:
        swept = events.sweep_deferred(s)
        s.commit()
    return {"published": relay_outbox(redis_client), "recovery_events": len(evts), "deferred_swept": swept}


def main() -> None:
    setup_logging()
    st = get_settings()
    client = None
    if st.redis_url:
        import redis
        client = redis.Redis.from_url(st.redis_url)

    def stop(*_):
        global _running
        _running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log(logger, "worker started", redis=bool(client))
    while _running:
        try:
            stats = tick(client)
            if any(stats.values()):
                log(logger, "worker tick", **stats)
        except Exception:
            logger.exception("worker tick failed")
        time.sleep(2)


if __name__ == "__main__":
    main()
