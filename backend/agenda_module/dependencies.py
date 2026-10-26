from collections.abc import Generator

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session


def get_db() -> Generator[Session, None, None]:
    from main import engine

    with Session(engine) as db:
        yield db


def _agenda_user(authorization: str | None):
    from main import current_user, has_agenda_access

    user = current_user(authorization)
    if not has_agenda_access(user):
        raise HTTPException(
            status_code=403,
            detail="Le module Agenda n'est pas activé pour votre compte. Contactez le SuperAdmin.",
        )
    return user


def get_current_user_id(
    authorization: str | None = Header(default=None),
) -> int:
    user_id = _agenda_user(authorization).id
    if user_id is None:
        raise HTTPException(status_code=401, detail="Compte utilisateur invalide.")
    return user_id


def require_admin(
    authorization: str | None = Header(default=None),
) -> int:
    user = _agenda_user(authorization)
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Droits administrateur requis.")
    if user.id is None:
        raise HTTPException(status_code=401, detail="Compte utilisateur invalide.")
    return user.id


def is_admin_user(
    authorization: str | None = Header(default=None),
) -> bool:
    return _agenda_user(authorization).role in ("admin", "superadmin")