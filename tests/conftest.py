import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import UUID as PG_UUID
from sqlalchemy import DateTime, Engine, MetaData, StaticPool, String, create_engine, text
from sqlalchemy.orm import Mapped, Session, declarative_base, mapped_column

from app.core.config import config
from app.db import core, repository, schema
from app.main import app

# ==========================
#  Database & Schema Setup
# ==========================
test_metadata = MetaData()
TestBase = declarative_base(metadata=test_metadata)


class TestDBTable(TestBase):
    __tablename__: str = "test_table"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Creates a database engine for the test session."""
    test_engine = create_engine(
        config.test_db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield test_engine
    test_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def setup_database(engine: Engine) -> None:
    """Creates the database schema."""
    core.Base.metadata.create_all(bind=engine)
    TestBase.metadata.create_all(bind=engine)


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    """
    Provides a transactional database session for testing.
    Uses SAVEPOINTs so app-level commits don't break test isolation.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        db_session = Session(bind=connection, join_transaction_mode="create_savepoint")

        yield db_session

        db_session.close()
        transaction.rollback()


@pytest.fixture()
def api_client(session: Session) -> Iterator[TestClient]:
    """
    Creates a FastAPI test client with the database session dependency overridden.

    Args:
        session: The database session fixture to be injected.

    Yields:
        The configured TestClient instance.
    """
    app.dependency_overrides[core.get_db_session] = lambda: session
    with TestClient(app) as client:
        yield client
        app.dependency_overrides.clear()


# ==========================
#  Test Records
# ==========================


def _create_and_add[DBRecord: core.Base](session: Session, record: DBRecord) -> DBRecord:
    """
    Creates a temporary record for testing.

    Args:
        session: The database session fixture.
        record: The record to add to the database.

    Returns:
        The created record.
    """
    repository.add(session, record)
    assert record.id is not None
    return record


@pytest.fixture()
def test_record(session: Session) -> TestDBTable:
    return _create_and_add(session, TestDBTable(name="Test Record"))


@pytest.fixture()
def test_user(session: Session) -> schema.DBUser:
    return _create_and_add(session, record=schema.DBUser(name="Test User"))


@pytest.fixture()
def test_website(session: Session) -> schema.DBWebsite:
    return _create_and_add(
        session,
        record=schema.DBWebsite(
            url="https://www.test_website.com",
            internal_links=[],
            critical_pages=[],
        ),
    )


@pytest.fixture()
def test_critical_page(session: Session, test_website: schema.DBWebsite) -> schema.DBCriticalPage:
    return _create_and_add(
        session,
        record=schema.DBCriticalPage(
            url=f"{test_website.url}/test_critical_page",
            links=[],
            documents=[],
            text_body="",
            website_id=test_website.id,
        ),
    )
