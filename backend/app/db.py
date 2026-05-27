"""Database engine, session factory, and the FastAPI request-scoped session dependency.

Sync SQLAlchemy 2.0 + psycopg3. One engine per process. Each request gets its own
Session (and therefore its own pooled connection); because path operations are declared
`def` (sync), FastAPI runs them in a threadpool, so concurrent requests genuinely run on
separate connections and `SELECT ... FOR UPDATE` row locks block as expected.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=10,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
