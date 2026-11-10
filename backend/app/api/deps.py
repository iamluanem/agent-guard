import hmac
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.services.errors import Unauthorized

SessionDep = Annotated[Session, Depends(get_session)]


def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    key = get_settings().admin_api_key
    if key is None:
        return
    if not x_admin_key or not hmac.compare_digest(x_admin_key, key.get_secret_value()):
        raise Unauthorized("missing or invalid X-Admin-Key")


def row(obj, *exclude: str) -> dict:
    return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs if c.key not in exclude}
