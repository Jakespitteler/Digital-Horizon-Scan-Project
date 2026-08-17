import uuid
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session


class CRUDFunctions[READ: BaseModel, CREATE: BaseModel, UPDATE: BaseModel](Protocol):
    def __init__(self, session: Session) -> None: ...
    def get_all(self, skip: int, limit: int) -> Sequence[READ]: ...
    def get(self, id: uuid.UUID) -> READ: ...
    def create(self, model_create: CREATE) -> READ: ...
    def update(self, id: uuid.UUID, model_update: UPDATE) -> READ: ...
    def delete(self, id: uuid.UUID) -> None: ...


class ServiceModels[READ: BaseModel, CREATE: BaseModel, UPDATE: BaseModel](BaseModel):
    model_read: type[READ]
    model_create: type[CREATE]
    model_update: type[UPDATE]
