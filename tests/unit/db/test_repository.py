import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy.orm import Session

from app.db import repository
from app.db.errors import IntegrityError, NotFoundError
from tests.conftest import TestDBTable


def test_get(session: Session, test_record: TestDBTable) -> None:
    """
    Tests retrieving a record.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    fetched_record: TestDBTable = repository.get(session, table=TestDBTable, id=test_record.id)
    assert fetched_record.id == test_record.id
    assert fetched_record.name == test_record.name


def test_get_raises_not_found_error(session: Session) -> None:
    """
    Tests that retrieving a non-existent ID raises NotFoundError.

    Args:
        session: The database session fixture.
    """
    invalid_id: uuid.UUID = uuid.uuid4()
    with pytest.raises(NotFoundError) as e:
        repository.get(session, table=TestDBTable, id=invalid_id)
    assert str(invalid_id) in str(e.value)


def test_get_list(session: Session) -> None:
    """
    Tests retrieving list records.

    Args:
        session: The database session fixture.
    """
    record1 = TestDBTable(name="Record A")
    record2 = TestDBTable(name="Record B")
    record3 = TestDBTable(name="Record C")
    for record in [record1, record2, record3]:
        repository.add(session, record)

    records: Sequence[TestDBTable] = repository.get_list(session, table=TestDBTable)
    assert len(records) == 3


def test_get_list_with_limit(session: Session) -> None:
    """
    Tests retrieving a list records with limit and skip boundaries.

    Args:
        session: The database session fixture.
    """
    record1 = TestDBTable(name="Record A")
    record2 = TestDBTable(name="Record B")
    record3 = TestDBTable(name="Record C")
    for record in [record1, record2, record3]:
        repository.add(session, record)

    # Retrieving with limit
    records = repository.get_list(session, table=TestDBTable, skip=0, limit=2)  # TODO: Test invalid limits
    assert len(records) == 2


def test_get_list_attributes(session: Session, test_record: TestDBTable) -> None:
    """
    Tests retrieving a list of records by their attributes.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    records: Sequence[TestDBTable] = repository.get_list(
        session,
        table=TestDBTable,
        attributes={TestDBTable.name.key: test_record.name},
    )
    assert len(records) == 1
    assert records[0].id == test_record.id


def test_add(session: Session) -> None:
    """
    Tests adding a new record.

    Args:
        session: The database session fixture.
    """
    record = TestDBTable(name="Create Record")

    repository.add(session, record)
    assert record.id is not None

    fetched_record: TestDBTable = repository.get(session, table=TestDBTable, id=record.id)
    assert fetched_record.name == record.name


def test_add_raises_integrity_error(session: Session, test_record: TestDBTable) -> None:
    """
    Tests that add raises IntegrityError if unique constraints are violated.

    Args:
        session: The database session fixture.
    """
    invalid_record = TestDBTable(name=test_record.name)
    with pytest.raises(IntegrityError) as e:
        repository.add(session, invalid_record)
    assert "unique constraint" in str(e.value)


def test_batch_add(session: Session) -> None:
    """
    Tests adding multiple records in batch.

    Args:
        session: The database session fixture.
    """
    records = [
        TestDBTable(name="Batch Record 1"),
        TestDBTable(name="Batch Record 2"),
        TestDBTable(name="Batch Record 3"),
    ]

    repository.batch_add(session, records)

    for record in records:
        assert record.id is not None
        fetched_record: TestDBTable = repository.get(session, table=TestDBTable, id=record.id)
        assert fetched_record.name == record.name


def test_batch_add_raises_integrity_error(session: Session, test_record: TestDBTable) -> None:
    """
    Tests that batch_add raises IntegrityError if unique constraints are violated.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    records = [
        TestDBTable(name="Valid Batch Record"),
        TestDBTable(name=test_record.name),  # Duplicate name
    ]

    with pytest.raises(IntegrityError) as e:
        repository.batch_add(session, records)
    assert "unique constraint" in str(e.value)


def test_update(session: Session, test_record: TestDBTable) -> None:
    """
    Tests updating a record.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    updates: dict[str, str] = {TestDBTable.name.key: "Updated Record"}
    updated_record: TestDBTable = repository.update(session, test_record, updates)
    assert updated_record.name == updates[TestDBTable.name.key]

    # Confirm persistence
    session.expire(updated_record)
    fetched_updated_record: TestDBTable = repository.get(session, table=TestDBTable, id=test_record.id)
    assert fetched_updated_record.name == updates[TestDBTable.name.key]


def test_update_raises_integrity_error(session: Session, test_record: TestDBTable) -> None:
    """
    Tests that update raises IntegrityError if unique constraints are violated.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    record = TestDBTable(name="Dummy Record")
    repository.add(session, record)

    invalid_updates: dict[str, str] = {TestDBTable.name.key: test_record.name}
    with pytest.raises(IntegrityError) as e:
        repository.update(session, record, invalid_updates)
    assert "unique constraint" in str(e.value)


def test_delete(session: Session, test_record: TestDBTable) -> None:
    """
    Tests deleting a record.

    Args:
        session: The database session fixture.
        test_record: The test record.
    """
    repository.delete(session, table=TestDBTable, id=test_record.id)

    # Confirm it's gone
    with pytest.raises(NotFoundError):
        repository.get(session, table=TestDBTable, id=test_record.id)
