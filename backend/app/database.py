from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.config import DATABASE_URL


if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is missing or empty. Set it in the root .env file."
    )


engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and close it when the request is finished."""
    database = SessionLocal()
    try:
        yield database
    finally:
        database.close()
