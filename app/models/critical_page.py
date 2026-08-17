import uuid

from pydantic import BaseModel, ConfigDict

from app.models.interfaces import ServiceModels


class CriticalPageCreate(BaseModel):
    url: str
    links: list[str]
    documents: list[str]
    text_body: str
    website_id: uuid.UUID


class CriticalPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: str
    links: list[str]
    documents: list[str]
    text_body: str
    website_id: uuid.UUID


class CriticalPageUpdate(BaseModel):
    url: str | None = None
    links: list[str] | None = None
    documents: list[str] | None = None
    text_body: str | None = None


CriticalPageServiceModels = ServiceModels(
    model_read=CriticalPageRead,
    model_create=CriticalPageCreate,
    model_update=CriticalPageUpdate,
)
