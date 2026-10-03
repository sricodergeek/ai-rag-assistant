from collections.abc import Mapping

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import User


def _find_google_user(db: Session, provider_user_id: str) -> User | None:
    return (
        db.query(User)
        .filter_by(auth_provider="google", provider_user_id=provider_user_id)
        .first()
    )


def get_or_create_google_user(db: Session, identity: Mapping[str, str]) -> User:
    provider_user_id = identity["sub"]
    existing_user = _find_google_user(db, provider_user_id)
    if existing_user is not None:
        return existing_user

    user = User(
        email=identity["email"],
        name=identity.get("name"),
        avatar_url=identity.get("picture"),
        auth_provider="google",
        provider_user_id=provider_user_id,
    )
    db.add(user)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing_user = _find_google_user(db, provider_user_id)
        if existing_user is not None:
            return existing_user
        raise

    db.refresh(user)
    return user
