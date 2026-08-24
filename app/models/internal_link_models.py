import uuid

from pydantic import BaseModel, ConfigDict

from app.utils.field_types import URLString


class InternalLinkCreate(BaseModel):
    url: URLString
    website_id: uuid.UUID


class InternalLinkCreateNoWebsite(BaseModel):
    url: URLString


class InternalLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    url: URLString
    website_id: uuid.UUID


class InternalLinkUpdate(BaseModel):
    url: URLString | None = None
