import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db import repository
from app.db.schema import DBInternalLink
from app.models.internal_link_models import InternalLinkCreate, InternalLinkRead, InternalLinkUpdate
from app.utils.interfaces import CRUDService

logger: logging.Logger = logging.getLogger(__name__)


class InternalLinkService(CRUDService[InternalLinkRead, InternalLinkCreate, InternalLinkUpdate]):
    def __init__(self, session: Session):
        """_summary_

        Args:
            session (Session): The database session.
        """
        self._db = session

    def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[InternalLinkRead]:
        """
        Retrieves internal_link records.

        Args:
            skip: The number of records to skip.
            limit: The maximum number of records to return.

        Returns:
            The retrieved internal_links.
        """
        internal_link_records: Sequence[DBInternalLink] = repository.get_list(
            self._db, table=DBInternalLink, skip=skip, limit=limit
        )
        return [InternalLinkRead.model_validate(internal_link_record) for internal_link_record in internal_link_records]

    def get(self, id: uuid.UUID) -> InternalLinkRead:
        """
        Retrieves a single internal_link by its primary key.

        Args:
            id: The id of the internal_link to retrieve.

        Raises:
            NotFoundError: If no internal_link exists with the provided ID.

        Returns:
            The retrieved internal_link.
        """
        internal_link_record: DBInternalLink = repository.get(self._db, table=DBInternalLink, id=id)
        return InternalLinkRead.model_validate(internal_link_record)

    def create(self, model_create: InternalLinkCreate) -> InternalLinkRead:
        """
        Creates a new internal_link record.

        Args:
            model_create: The internal_link details to create.

        Raises:
            IntegrityError: If the internal_link already exists in db.

        Returns:
            The internal_link record.
        """
        internal_link_record: DBInternalLink = DBInternalLink(**model_create.model_dump())
        repository.add(self._db, record=internal_link_record)
        return InternalLinkRead.model_validate(internal_link_record)

    def update(self, id: uuid.UUID, model_update: InternalLinkUpdate) -> InternalLinkRead:
        """
        Updates an existing internal_link record.

        Args:
            id: The id of the internal_link to update.
            model_update: The new data to apply to the internal_link.

        Raises:
            NotFoundError: If the internal_link with id does not exist.
            IntegrityError: If the internal_link updated details already exists in db.

        Returns:
            The updated internal_link.
        """
        internal_link_record: DBInternalLink = repository.get(self._db, table=DBInternalLink, id=id)
        internal_link_record = repository.update(
            self._db, record=internal_link_record, updates=model_update.model_dump(exclude_unset=True)
        )
        return InternalLinkRead.model_validate(internal_link_record)

    def delete(self, id: uuid.UUID) -> None:
        """
        Deletes a internal_link by its primary key.

        Args:
            id: The id of the internal_link to delete.

        Raises:
            NotFoundError: If no internal_link exists with the provided ID.
        """
        repository.get(self._db, table=DBInternalLink, id=id)
        repository.delete(self._db, table=DBInternalLink, id=id)
