from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from src.db.errors import NotFoundError
from src.db.schema import DBWebsite
from src.models.website import WebsiteCreate, WebsiteRead, WebsiteUpdate
from src.services.website_service import WebsiteService


def test_get_all_websites(session: Session, test_website: DBWebsite) -> None:
    """
    Tests retrieving a list of all websites.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    fetched_websites: Sequence[WebsiteRead] = WebsiteService(session).get_all()
    assert len(fetched_websites) >= 1
    assert any(c.id == test_website.id for c in fetched_websites)
    assert any(c.url == test_website.url for c in fetched_websites)


def test_get_website(session: Session, test_website: DBWebsite) -> None:
    """
    Tests retrieving an existing website by ID.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    fetched_website: WebsiteRead = WebsiteService(session).get(id=test_website.id)

    assert fetched_website is not None
    assert fetched_website.id == test_website.id
    assert fetched_website.url == test_website.url


def test_create_website(session: Session) -> None:
    """
    Tests creating a new website with basic details.

    Args:
        session: The database session fixture.
    """
    website_details = WebsiteCreate(url="Website", critical_pages=[], internal_links=[])

    created_website: WebsiteRead = WebsiteService(session).create(website_details)
    assert created_website.id is not None
    assert created_website.url == website_details.url

    fetched_website: WebsiteRead = WebsiteService(session).get(id=created_website.id)
    assert fetched_website.url == website_details.url


def test_update_website(session: Session, test_website: DBWebsite) -> None:
    """
    Tests updating an existing website's details.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    model_update = WebsiteUpdate(url="Updated Website")

    updated_website: WebsiteRead = WebsiteService(session).update(id=test_website.id, model_update=model_update)
    assert updated_website.id == test_website.id
    assert updated_website.url == model_update.url

    fetched_website: WebsiteRead = WebsiteService(session).get(id=test_website.id)
    assert fetched_website.url == model_update.url


def test_delete_website(session: Session, test_website: DBWebsite) -> None:
    """
    Tests deleting an existing website.

    Args:
        session: The database session fixture.
        test_website: The test website record.
    """
    WebsiteService(session).delete(id=test_website.id)

    # Assert it can no longer be retrieved
    with pytest.raises(NotFoundError):
        WebsiteService(session).get(id=test_website.id)
