"""Pytest fixtures. Tests run against a dedicated `fleet_test` database (created if
missing) so they never touch the dev data. Each test starts from a clean, seeded state.

Concurrency tests need real Postgres row locks, so the test engine uses a large pool and
threads open their own sessions/connections.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  -- register models on Base.metadata
from app.config import settings
from app.db import Base
from app.seed import seed


def _test_url() -> str:
    base, _, _name = settings.database_url.rpartition("/")
    return f"{base}/fleet_test"


TEST_URL = _test_url()


@pytest.fixture(scope="session")
def engine():
    # Ensure the fleet_test database exists (CREATE DATABASE needs autocommit).
    admin = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'fleet_test'")
        ).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE fleet_test"))
    admin.dispose()

    eng = create_engine(TEST_URL, pool_size=50, max_overflow=10, pool_pre_ping=True, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_db(engine):
    """Reset to a clean, seeded baseline before each test."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE anomalies, telemetry_events, maintenance_records, missions "
                "RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(text("UPDATE zones SET entry_count = 0"))
        conn.execute(
            text(
                "UPDATE vehicles SET status='idle', battery_pct=100, speed_mps=0, lat=NULL, "
                "lon=NULL, current_zone=NULL, last_event_ts=NULL, last_event_id=NULL, "
                "last_anomaly_id=NULL, last_seen_at=NULL"
            )
        )
    seed(engine)
    yield


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def client(engine):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # No `with` block: skip the app lifespan (schema + seed are managed by fixtures here).
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()
