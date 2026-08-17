from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.db.errors import NotFoundError
from app.db.schema import DBUser
from app.models.user import UserCreate, UserRead, UserUpdate
from app.services.user_service import UserService


def test_get_all_users(session: Session, test_user: DBUser) -> None:
    """
    Tests retrieving a list of all users.

    Args:
        session: The database session fixture.
        test_user: The test user record.
    """
    fetched_users: Sequence[UserRead] = UserService(session).get_all()
    assert len(fetched_users) >= 1
    assert any(c.id == test_user.id for c in fetched_users)
    assert any(c.name == test_user.name for c in fetched_users)


def test_get_user(session: Session, test_user: DBUser) -> None:
    """
    Tests retrieving an existing user by ID.

    Args:
        session: The database session fixture.
        test_user: The test user record.
    """
    fetched_user: UserRead = UserService(session).get(id=test_user.id)

    assert fetched_user is not None
    assert fetched_user.id == test_user.id
    assert fetched_user.name == test_user.name


def test_create_user(session: Session) -> None:
    """
    Tests creating a new user with basic details.

    Args:
        session: The database session fixture.
    """
    user_details = UserCreate(name="User")

    created_user: UserRead = UserService(session).create(user_details)
    assert created_user.id is not None
    assert created_user.name == user_details.name

    fetched_user: UserRead = UserService(session).get(id=created_user.id)
    assert fetched_user.name == user_details.name


def test_update_user(session: Session, test_user: DBUser) -> None:
    """
    Tests updating an existing user's details.

    Args:
        session: The database session fixture.
        test_user: The test user record.
    """
    model_update = UserUpdate(name="Updated User")

    updated_user: UserRead = UserService(session).update(id=test_user.id, model_update=model_update)
    assert updated_user.id == test_user.id
    assert updated_user.name == model_update.name

    fetched_user: UserRead = UserService(session).get(id=test_user.id)
    assert fetched_user.name == model_update.name


def test_delete_user(session: Session, test_user: DBUser) -> None:
    """
    Tests deleting an existing user.

    Args:
        session: The database session fixture.
        test_user: The test user record.
    """
    UserService(session).delete(id=test_user.id)

    # Assert it can no longer be retrieved
    with pytest.raises(NotFoundError):
        UserService(session).get(id=test_user.id)
