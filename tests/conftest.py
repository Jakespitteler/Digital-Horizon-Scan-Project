import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import UUID as PG_UUID
from sqlalchemy import DateTime, Engine, MetaData, StaticPool, String, create_engine, text
from sqlalchemy.orm import Mapped, Session, declarative_base, mapped_column

from app.api import routers
from app.core.config import config
from app.db import repository
from app.db.core import Base, get_db_session
from app.db.schema import DBCriticalPage, DBUser, DBWebsite
from app.main import app
from app.models.critical_page import CriticalPageCreate, CriticalPageUpdate
from app.models.user import UserCreate, UserUpdate
from app.models.website import WebsiteCreate, WebsiteUpdate

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
    Base.metadata.create_all(bind=engine)
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
    app.dependency_overrides[get_db_session] = lambda: session
    with TestClient(app) as client:
        yield client
        app.dependency_overrides.clear()


# ==========================
#  Test Records
# ==========================


def _create_and_add[DBRecord: Base](session: Session, record: DBRecord) -> DBRecord:
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
def test_user(session: Session) -> DBUser:
    return _create_and_add(session, record=DBUser(name="Test User"))


@pytest.fixture()
def test_website(session: Session) -> DBWebsite:
    return _create_and_add(session, record=DBWebsite(url="Test Website", internal_links=[], critical_pages=[]))


@pytest.fixture()
def test_critical_page(session: Session, test_website: DBWebsite) -> DBCriticalPage:
    return _create_and_add(
        session,
        record=DBCriticalPage(
            url="Test Critical Page",
            links=[],
            documents=[],
            text_body="",
            website_id=test_website.id,
        ),
    )


# ==========================
#  API Tests Configuration
# ==========================


class RouterTestConfig(BaseModel):
    """Defines the configuration and payloads for CRUD endpoint testing."""

    prefix: str
    model_create: BaseModel
    model_update: BaseModel
    test_fixture_name: str


ROUTER_TEST_CONFIGS: list[RouterTestConfig] = [
    RouterTestConfig(
        prefix=routers.USER_ROUTER.prefix,
        model_create=UserCreate(name="Test User"),
        model_update=UserUpdate(name="Updated User"),
        test_fixture_name="test_user",
    ),
    RouterTestConfig(
        prefix=routers.WEBSITE_ROUTER.prefix,
        model_create=WebsiteCreate(url="Test Website", critical_pages=[], internal_links=[]),
        model_update=WebsiteUpdate(url="Updated Website"),
        test_fixture_name="test_website",
    ),
    RouterTestConfig(
        prefix=routers.CRITICAL_PAGE_ROUTER.prefix,
        model_create=CriticalPageCreate(
            url="Test Critical Page",
            links=[],
            documents=[],
            text_body="",
            website_id=uuid.uuid4(),
        ),
        model_update=CriticalPageUpdate(url="Updated Critical Page"),
        test_fixture_name="test_critical_page",
    ),
]


def _get_route_name(config: RouterTestConfig) -> str:
    """Extracts the name of the router from the configuration.

    Args:
        config: The router configuration

    Returns:
        str: The name of the router
    """
    return config.prefix.strip("/")


@pytest.fixture(params=ROUTER_TEST_CONFIGS, ids=_get_route_name)
def router_test_config(request: pytest.FixtureRequest) -> RouterTestConfig:
    """Yields the current router configuration for testing."""
    return request.param


@pytest.fixture
def test_api_record(request: pytest.FixtureRequest, router_test_config: RouterTestConfig) -> Base:
    """Dynamically fetches the database record fixture specified in the config."""
    return request.getfixturevalue(router_test_config.test_fixture_name)
