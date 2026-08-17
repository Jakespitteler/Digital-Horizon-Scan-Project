import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.core import get_db_session
from app.utils.interfaces import CRUDFunctions, ServiceModels

SessionDep = Annotated[Session, Depends(get_db_session)]


def create_crud_router[READ: BaseModel, CREATE: BaseModel, UPDATE: BaseModel](
    prefix: str,
    service: type[CRUDFunctions[READ, CREATE, UPDATE]],
    service_models: ServiceModels[READ, CREATE, UPDATE],
) -> APIRouter:
    """Generates a standardised CRUD router."""
    router = APIRouter(prefix=prefix, tags=[prefix.split("/")[-1].capitalize()])

    @router.get("/", response_model=Sequence[READ], status_code=status.HTTP_200_OK)
    def get_items(session: SessionDep, skip: int = 0, limit: int = 100) -> Sequence[READ]:
        return service(session).get_all(skip, limit)

    @router.get("/{id}", response_model=READ, status_code=status.HTTP_200_OK)
    def get_item(session: SessionDep, id: uuid.UUID) -> READ:
        return service(session).get(id)

    @router.post("/", response_model=READ, status_code=status.HTTP_201_CREATED)
    def create_item(session: SessionDep, model_create: service_models.model_create) -> READ:  # type: ignore
        return service(session).create(model_create)  # type: ignore

    @router.patch("/{id}", response_model=READ, status_code=status.HTTP_200_OK)
    def update_item(session: SessionDep, id: uuid.UUID, model_update: service_models.model_update) -> READ:  # type: ignore
        return service(session).update(id, model_update)  # type: ignore

    @router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_item(session: SessionDep, id: uuid.UUID) -> None:
        service(session).delete(id)

    return router
