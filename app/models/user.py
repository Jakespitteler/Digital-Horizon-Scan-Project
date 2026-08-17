import uuid

from pydantic import BaseModel, ConfigDict

from app.utils.interfaces import ServiceModels


class UserCreate(BaseModel):
    name: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str


class UserUpdate(BaseModel):
    name: str | None = None


UserServiceModels = ServiceModels(model_read=UserRead, model_create=UserCreate, model_update=UserUpdate)
