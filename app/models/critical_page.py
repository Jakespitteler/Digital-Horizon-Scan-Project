import uuid

from pydantic import BaseModel, ConfigDict

from app.utils.field_types import URLString
from app.utils.interfaces import ServiceModels


class CriticalPageCreate(BaseModel):
    url: URLString
    links: list[URLString]
    documents: list[URLString]
    text_body: str
    website_id: uuid.UUID


class CriticalPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: URLString
    links: list[URLString]
    documents: list[URLString]
    text_body: str
    website_id: uuid.UUID


class CriticalPageUpdate(BaseModel):
    url: URLString | None = None
    links: list[URLString] | None = None
    documents: list[URLString] | None = None
    text_body: str | None = None


CriticalPageServiceModels = ServiceModels(
    model_read=CriticalPageRead,
    model_create=CriticalPageCreate,
    model_update=CriticalPageUpdate,
)
