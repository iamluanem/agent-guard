from sqlalchemy.orm import Session

from app.models import Notification


def notify(s: Session, customer_id: str | None, subject: str, body: str, ref_id: str) -> None:
    """In-app notification. Email/push adapters would subscribe to the same outbox events."""
    s.add(Notification(customer_id=customer_id, subject=subject, body=body, ref_id=ref_id))
